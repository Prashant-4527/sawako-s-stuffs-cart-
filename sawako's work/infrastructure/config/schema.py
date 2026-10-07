"""
kaoruko/infrastructure/config/schema.py

Pydantic v2 configuration schema.
All config values are typed, validated, and documented.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enums ────────────────────────────────────────────────────────────────────

class STTEngine(str, Enum):
    WHISPER = "whisper"
    GOOGLE = "google"
    AUTO = "auto"

class WhisperModel(str, Enum):
    TINY = "tiny"
    BASE = "base"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"

class TTSEngine(str, Enum):
    EDGE_TTS = "edge_tts"
    COQUI = "coqui"
    ELEVENLABS = "elevenlabs"

class AIProvider(str, Enum):
    CLAUDE = "claude"
    OPENAI = "openai"
    OLLAMA = "ollama"

class UITheme(str, Enum):
    CYBER_PURPLE = "cyber_purple"
    MIDNIGHT = "midnight"
    SAKURA = "sakura"

class UIMode(str, Enum):
    ORB = "orb"
    DASHBOARD = "dashboard"
    MINIMAL = "minimal"
    GAMING = "gaming"

class UIPosition(str, Enum):
    BOTTOM_RIGHT = "bottom_right"
    BOTTOM_LEFT = "bottom_left"
    TOP_RIGHT = "top_right"
    TOP_LEFT = "top_left"
    CENTER = "center"

class GPUMode(str, Enum):
    AUTO = "auto"
    CUDA = "cuda"
    CPU = "cpu"


# ── Wake Word Config ─────────────────────────────────────────────────────────

class WakeWordConfig(BaseModel):
    enabled: bool = True
    phrases: list[str] = Field(
        default=["Hey Kaoruko", "Kaoruko"],
        min_length=1,
    )
    sensitivity: float = Field(default=0.65, ge=0.0, le=1.0)
    cooldown_seconds: float = Field(default=1.5, ge=0.5)
    model_path: Optional[Path] = None


# ── STT Config ───────────────────────────────────────────────────────────────

class STTConfig(BaseModel):
    engine: STTEngine = STTEngine.AUTO
    model_size: WhisperModel = WhisperModel.BASE
    language: Optional[str] = None        # None = auto-detect
    device: str = "auto"                  # "cpu" | "cuda" | "auto"
    compute_type: str = "int8"            # CTranslate2 quantization
    beam_size: int = Field(default=5, ge=1, le=10)
    # VAD parameters
    vad_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    min_speech_duration_ms: int = Field(default=250, ge=100)
    max_silence_duration_ms: int = Field(default=800, ge=200)


# ── TTS Config ───────────────────────────────────────────────────────────────

class TTSConfig(BaseModel):
    engine: TTSEngine = TTSEngine.EDGE_TTS
    voice: str = "ja-JP-NanamiNeural"
    fallback_voice: str = "en-US-AriaNeural"
    speaking_rate: str = "-5%"
    pitch: str = "+5Hz"
    volume: str = "+0%"

    acknowledgements: list[str] = Field(default=[
        "Hai, wakarimashita~",
        "Of course~",
        "Right away~",
        "Consider it done~",
    ])
    confirmations: list[str] = Field(default=[
        "Shall I proceed?",
        "Are you sure?",
        "Please confirm~",
    ])
    errors: list[str] = Field(default=[
        "Gomen nasai, I encountered a problem.",
        "My apologies, something went wrong~",
    ])
    greetings: list[str] = Field(default=[
        "Ohayou gozaimasu~",
        "Good to hear from you~",
        "Welcome back~",
    ])


# ── Voice Config ─────────────────────────────────────────────────────────────

class AudioConfig(BaseModel):
    sample_rate: int = Field(default=16000, ge=8000, le=48000)
    channels: int = Field(default=1, ge=1, le=2)
    chunk_size: int = Field(default=512, ge=128, le=4096)
    dtype: str = "float32"
    noise_suppression_strength: float = Field(default=0.75, ge=0.0, le=1.0)
    noise_suppression_stationary: bool = True


class VoiceConfig(BaseModel):
    wake_word: WakeWordConfig = Field(default_factory=WakeWordConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)


# ── AI Config ────────────────────────────────────────────────────────────────

class AIConfig(BaseModel):
    primary: AIProvider = AIProvider.CLAUDE
    model: str = "claude-haiku-4-5-20251001"
    fallback: AIProvider = AIProvider.OLLAMA
    offline_model: str = "llama3.2:3b"
    max_tokens: int = Field(default=1024, ge=64, le=8192)
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=15.0, ge=1.0)
    # Cost management
    max_daily_tokens: Optional[int] = None  # None = unlimited
    prefer_local_for_simple: bool = True    # Use rules/local for simple cmds


# ── UI Config ────────────────────────────────────────────────────────────────

class UIConfig(BaseModel):
    theme: UITheme = UITheme.CYBER_PURPLE
    default_mode: UIMode = UIMode.ORB
    always_on_top: bool = True
    start_position: UIPosition = UIPosition.BOTTOM_RIGHT
    opacity: float = Field(default=0.95, ge=0.1, le=1.0)
    frameless: bool = True
    show_in_taskbar: bool = False
    orb_size: int = Field(default=80, ge=40, le=200)
    animation_fps: int = Field(default=60, ge=30, le=120)
    font_family: str = "Segoe UI"


# ── Security Config ───────────────────────────────────────────────────────────

class SecurityConfig(BaseModel):
    confirm_destructive: bool = True           # Ask before delete/shutdown/restart
    require_pin: bool = False                  # Require PIN for sensitive actions
    pin_hash: Optional[str] = None             # bcrypt-hashed PIN
    audio_indicator: bool = True               # Show mic-active indicator
    log_all_commands: bool = True
    auto_lock_minutes: Optional[int] = None    # None = never


# ── Privacy Config ────────────────────────────────────────────────────────────

class PrivacyConfig(BaseModel):
    save_transcripts: bool = True
    transcript_retention_days: int = Field(default=30, ge=1)
    share_analytics: bool = False
    mask_sensitive_in_logs: bool = True


# ── Performance Config ────────────────────────────────────────────────────────

class PerformanceConfig(BaseModel):
    max_cpu_percent: int = Field(default=15, ge=5, le=50)
    gpu_acceleration: GPUMode = GPUMode.AUTO
    cache_ai_responses: bool = True
    cache_ttl_seconds: int = Field(default=300, ge=60)


# ── Assistant Config ──────────────────────────────────────────────────────────

class AssistantConfig(BaseModel):
    name: str = "Kaoruko"
    language: str = "en"
    multilingual: bool = True
    supported_languages: list[str] = Field(default=["en", "ja", "hi"])
    persona: str = (
        "You are Kaoruko (香子), an elegant and intelligent female AI desktop assistant. "
        "Your personality is calm, warm, and professional with a subtle Japanese-inspired "
        "speaking style. You address the user with respect and warmth. You occasionally use "
        "gentle Japanese expressions like 'Hai', 'Wakarimashita', or 'Gomen nasai' when "
        "appropriate, but primarily speak in the user's language. You are precise, helpful, "
        "and never overly verbose."
    )


# ── Root Config ───────────────────────────────────────────────────────────────

class KaorukoConfig(BaseModel):
    """Root configuration model for Kaoruko."""

    version: str = "1.0.0"
    assistant: AssistantConfig = Field(default_factory=AssistantConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    performance: PerformanceConfig = Field(default_factory=PerformanceConfig)

    # Runtime-injected, not stored in YAML
    _project_root: Optional[Path] = None

    model_config = {"arbitrary_types_allowed": True}

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: str) -> str:
        parts = v.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise ValueError(f"Invalid version format: {v}. Expected MAJOR.MINOR.PATCH")
        return v

    def get_data_dir(self) -> Path:
        root = self._project_root or Path.cwd()
        return root / "data"

    def get_config_dir(self) -> Path:
        root = self._project_root or Path.cwd()
        return root / "config"

    def to_safe_dict(self) -> dict[str, Any]:
        """Return config dict with sensitive fields masked."""
        d = self.model_dump()
        # Mask PIN hash
        if d.get("security", {}).get("pin_hash"):
            d["security"]["pin_hash"] = "***"
        return d
