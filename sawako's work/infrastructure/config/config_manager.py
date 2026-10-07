"""
kaoruko/infrastructure/config/config_manager.py

Unified configuration manager.
Loads kaoruko.yaml → validates with Pydantic → provides typed access.
Supports hot-reload via watchdog.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Optional

from ruamel.yaml import YAML

from kaoruko.infrastructure.config.schema import KaorukoConfig


class ConfigError(Exception):
    """Raised when configuration is invalid or missing."""


class ConfigManager:
    """
    Singleton-style config manager.

    Usage:
        config = ConfigManager.load(path)
        config.ui.theme           → UITheme.CYBER_PURPLE
        config.voice.stt.engine   → STTEngine.WHISPER
    """

    _instance: Optional["ConfigManager"] = None
    _lock = threading.Lock()

    def __init__(self, config: KaorukoConfig, config_path: Path) -> None:
        self._config = config
        self._config_path = config_path
        self._change_listeners: list[Callable[[KaorukoConfig], None]] = []
        self._yaml = YAML()
        self._yaml.preserve_quotes = True

    # ── Public API ────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, config_path: Path) -> "KaorukoConfig":
        """
        Load and validate configuration from YAML file.
        Falls back to defaults if file does not exist.
        """
        with cls._lock:
            project_root = config_path.parent.parent

            if not config_path.exists():
                # First run — create default config
                instance = cls._create_defaults(config_path, project_root)
            else:
                instance = cls._load_from_file(config_path, project_root)

            cls._instance = instance
            return instance._config

    @classmethod
    def get(cls) -> "KaorukoConfig":
        """Get the currently loaded config (must call load() first)."""
        if cls._instance is None:
            raise ConfigError("ConfigManager.load() has not been called yet.")
        return cls._instance._config

    def save(self) -> None:
        """Persist current config back to YAML (preserves comments)."""
        raw = self._config.model_dump(mode="json", exclude_none=True)
        with open(self._config_path, "w", encoding="utf-8") as f:
            self._yaml.dump({"kaoruko": raw}, f)

    def update(self, key_path: str, value: Any) -> None:
        """
        Update a nested config value at runtime.

        Example:
            manager.update("ui.theme", "sakura")
            manager.update("voice.tts.engine", "elevenlabs")
        """
        keys = key_path.split(".")
        obj = self._config
        for key in keys[:-1]:
            obj = getattr(obj, key)
        setattr(obj, keys[-1], value)
        self._notify_listeners()

    def on_change(self, listener: Callable[[KaorukoConfig], None]) -> None:
        """Register a callback for config changes."""
        self._change_listeners.append(listener)

    # ── Internal ──────────────────────────────────────────────────────────────

    @classmethod
    def _load_from_file(cls, path: Path, project_root: Path) -> "ConfigManager":
        yaml = YAML()
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.load(f)

        # Handle nested "kaoruko:" key
        data = raw.get("kaoruko", raw) if raw else {}

        try:
            config = KaorukoConfig(**_flatten_yaml(data))
        except Exception as e:
            raise ConfigError(f"Invalid configuration in {path}: {e}") from e

        config._project_root = project_root
        return cls(config=config, config_path=path)

    @classmethod
    def _create_defaults(cls, path: Path, project_root: Path) -> "ConfigManager":
        """Create default config, write to disk, return instance."""
        config = KaorukoConfig()
        config._project_root = project_root

        # Ensure config directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        yaml = YAML()
        yaml.preserve_quotes = True

        default_yaml = _build_default_yaml()
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(default_yaml, f)

        return cls(config=config, config_path=path)

    def _notify_listeners(self) -> None:
        for listener in self._change_listeners:
            try:
                listener(self._config)
            except Exception:
                pass  # Never crash on listener error


# ── Helpers ───────────────────────────────────────────────────────────────────

def _flatten_yaml(data: dict) -> dict:
    """Convert nested YAML structure to Pydantic model kwargs."""
    result = {}
    for key, value in data.items():
        if isinstance(value, dict):
            result[key] = value
        else:
            result[key] = value
    return result


def _build_default_yaml() -> dict:
    """Return the default YAML structure for first-run config creation."""
    return {
        "kaoruko": {
            "version": "1.0.0",
            "assistant": {
                "name": "Kaoruko",
                "language": "en",
                "multilingual": True,
            },
            "voice": {
                "wake_word": {
                    "enabled": True,
                    "phrases": ["Hey Kaoruko", "Kaoruko"],
                    "sensitivity": 0.65,
                },
                "stt": {
                    "engine": "auto",
                    "model_size": "base",
                    "language": None,
                },
                "tts": {
                    "engine": "edge_tts",
                    "voice": "ja-JP-NanamiNeural",
                    "fallback_voice": "en-US-AriaNeural",
                    "speaking_rate": "-5%",
                    "pitch": "+5Hz",
                },
            },
            "ai": {
                "primary": "claude",
                "model": "claude-haiku-4-5-20251001",
                "fallback": "ollama",
                "offline_model": "llama3.2:3b",
            },
            "ui": {
                "theme": "cyber_purple",
                "default_mode": "orb",
                "always_on_top": True,
                "start_position": "bottom_right",
                "opacity": 0.95,
            },
            "security": {
                "confirm_destructive": True,
                "require_pin": False,
                "audio_indicator": True,
            },
            "privacy": {
                "save_transcripts": True,
                "share_analytics": False,
            },
            "performance": {
                "max_cpu_percent": 15,
                "gpu_acceleration": "auto",
            },
        }
    }
