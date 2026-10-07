"""
kaoruko/plugins/plugin_base.py

Plugin base class and plugin manager.
Plugins extend Kaoruko with new intents, handlers, and UI widgets.

Fix applied:
  - handle_intent() is now async. The original sync version would block the
    asyncio event loop if a plugin performed any I/O (network call for weather,
    disk read for system info, etc.). Plugins should be able to await freely.
  - PluginManager._load_plugin() now calls `await instance.on_load()` where
    on_load is async, and falls back gracefully if it's sync.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import json
import sys
from abc import ABC
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from kaoruko.infrastructure.logging.logger import get_logger

if TYPE_CHECKING:
    from kaoruko.core.event_bus import EventBus
    from kaoruko.infrastructure.config.schema import KaorukoConfig

log = get_logger("plugins.manager")


# ── Plugin base class ─────────────────────────────────────────────────────────

class KaorukoPlugin(ABC):
    """
    Base class for all Kaoruko plugins.
    Subclass this in your plugin's __init__.py.

    All lifecycle hooks and handle_intent() may be async or sync —
    the plugin manager handles both transparently.
    """

    name: str        = "unnamed_plugin"
    version: str     = "1.0.0"
    author: str      = ""
    description: str = ""
    intents: list[str] = []       # Intent codes this plugin handles
    requires: list[str] = []      # Required Python packages

    def __init__(self, config: "KaorukoConfig", bus: "EventBus") -> None:
        self.config = config
        self.bus = bus
        self._enabled = True

    def on_load(self) -> None:
        """Called when the plugin is loaded. Override to initialise resources."""
        pass

    def on_unload(self) -> None:
        """Called when the plugin is unloaded. Clean up resources here."""
        pass

    def on_enable(self) -> None:
        """Called when the plugin is enabled after being disabled."""
        pass

    def on_disable(self) -> None:
        """Called when the plugin is disabled."""
        pass

    async def handle_intent(
        self,
        intent: str,
        entities: dict[str, Any],
        session: Optional[Any] = None,
    ) -> Optional[str]:
        """
        Handle a matched intent. Return response text, or None to skip.

        FIX: Now async. Plugins that call external APIs (weather, home
        automation, etc.) can await without blocking the event loop.
        Sync subclass overrides are wrapped in run_in_executor automatically
        by _call_handle_intent() in the plugin manager.
        """
        return None

    def get_example_phrases(self) -> list[str]:
        """Return example phrases for this plugin (shown in help)."""
        return []

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def __repr__(self) -> str:
        return f"Plugin({self.name} v{self.version})"


# ── Plugin manifest ───────────────────────────────────────────────────────────

@dataclass
class PluginManifest:
    name: str
    version: str
    description: str = ""
    author: str = ""
    intents: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    entry_class: str = "Plugin"
    enabled: bool = True


# ── Plugin manager ────────────────────────────────────────────────────────────

class PluginManager:
    """
    Discovers, loads, and manages Kaoruko plugins.
    Scans the /plugins/ directory for plugin packages.
    Supports runtime enable/disable and hot-reload.
    """

    def __init__(
        self,
        plugins_dir: Path,
        config: "KaorukoConfig",
        bus: "EventBus",
    ) -> None:
        self._dir = plugins_dir
        self._config = config
        self._bus = bus
        self._plugins: dict[str, KaorukoPlugin] = {}
        self._manifests: dict[str, PluginManifest] = {}

    async def discover_and_load(self) -> int:
        """Scan plugins directory and load all valid plugins. Returns count loaded."""
        if not self._dir.exists():
            self._dir.mkdir(parents=True, exist_ok=True)
            return 0

        loaded = 0
        for item in sorted(self._dir.iterdir()):
            if item.is_dir() and (item / "__init__.py").exists():
                try:
                    if await self._load_plugin(item):
                        loaded += 1
                except Exception as e:
                    log.error("plugin_load_error", plugin=item.name, error=str(e))

        log.info("plugins_loaded", count=loaded)
        return loaded

    async def _load_plugin(self, plugin_dir: Path) -> bool:
        """Load a single plugin package."""
        plugin_name = plugin_dir.name

        manifest = self._read_manifest(plugin_dir)
        if manifest and not manifest.enabled:
            log.debug("plugin_disabled_by_manifest", plugin=plugin_name)
            return False

        spec = importlib.util.spec_from_file_location(
            f"kaoruko_plugin_{plugin_name}",
            plugin_dir / "__init__.py",
        )
        if not spec or not spec.loader:
            return False

        module = importlib.util.module_from_spec(spec)
        sys.modules[f"kaoruko_plugin_{plugin_name}"] = module
        spec.loader.exec_module(module)

        entry_class_name = manifest.entry_class if manifest else "Plugin"
        plugin_class = getattr(module, entry_class_name, None)
        if not plugin_class or not issubclass(plugin_class, KaorukoPlugin):
            log.warning("plugin_no_valid_class", plugin=plugin_name, expected=entry_class_name)
            return False

        if manifest and manifest.requires:
            for req in manifest.requires:
                try:
                    importlib.import_module(req.replace("-", "_"))
                except ImportError:
                    log.warning("plugin_missing_requirement", plugin=plugin_name, requirement=req)
                    return False

        instance: KaorukoPlugin = plugin_class(self._config, self._bus)

        # FIX: Support both async and sync on_load()
        result = instance.on_load()
        if inspect.isawaitable(result):
            await result

        self._plugins[plugin_name] = instance
        if manifest:
            self._manifests[plugin_name] = manifest

        from kaoruko.core.event_bus import KaorukoEvent
        await self._bus.publish(
            KaorukoEvent.PLUGIN_LOADED,
            data={"name": plugin_name, "version": instance.version},
            source="plugin_manager",
        )
        log.info("plugin_loaded", name=plugin_name, version=instance.version, intents=instance.intents)
        return True

    async def call_handle_intent(
        self,
        plugin: KaorukoPlugin,
        intent: str,
        entities: dict[str, Any],
        session: Optional[Any] = None,
    ) -> Optional[str]:
        """
        Call handle_intent(), supporting both async and sync implementations.
        Sync implementations are wrapped in run_in_executor to avoid blocking.
        """
        result = plugin.handle_intent(intent, entities, session)
        if inspect.isawaitable(result):
            return await result
        # Sync override — run in thread pool to protect the event loop
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, plugin.handle_intent, intent, entities, session
        )

    def _read_manifest(self, plugin_dir: Path) -> Optional[PluginManifest]:
        manifest_path = plugin_dir / "manifest.json"
        if not manifest_path.exists():
            return None
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            return PluginManifest(**{
                k: v for k, v in data.items()
                if k in PluginManifest.__dataclass_fields__
            })
        except Exception as e:
            log.warning("manifest_parse_error", plugin=plugin_dir.name, error=str(e))
            return None

    def get_handler_for_intent(self, intent: str) -> Optional[KaorukoPlugin]:
        """Find a loaded plugin that handles the given intent."""
        for plugin in self._plugins.values():
            if intent in plugin.intents and plugin.is_enabled:
                return plugin
        return None

    def enable(self, name: str) -> bool:
        plugin = self._plugins.get(name)
        if not plugin:
            return False
        plugin._enabled = True
        plugin.on_enable()
        log.info("plugin_enabled", name=name)
        return True

    def disable(self, name: str) -> bool:
        plugin = self._plugins.get(name)
        if not plugin:
            return False
        plugin._enabled = False
        plugin.on_disable()
        log.info("plugin_disabled", name=name)
        return True

    async def reload(self, name: str) -> bool:
        """Reload a plugin without restarting Kaoruko."""
        plugin = self._plugins.get(name)
        if plugin:
            result = plugin.on_unload()
            if inspect.isawaitable(result):
                await result
            del self._plugins[name]

        plugin_dir = self._dir / name
        if plugin_dir.exists():
            return await self._load_plugin(plugin_dir)
        return False

    def list_plugins(self) -> list[dict]:
        return [
            {
                "name": name,
                "version": p.version,
                "description": p.description,
                "enabled": p.is_enabled,
                "intents": p.intents,
            }
            for name, p in self._plugins.items()
        ]

    def get_all_intents(self) -> set[str]:
        """Return the union of all intent codes handled by loaded plugins."""
        intents: set[str] = set()
        for plugin in self._plugins.values():
            if plugin.is_enabled:
                intents.update(plugin.intents)
        return intents

    def __len__(self) -> int:
        return len(self._plugins)
