"""
kaoruko/plugins/built_in/system_info/__init__.py

Built-in plugin: System Information
Handles: GET_SYSTEM_INFO, GET_BATTERY, GET_DISK_SPACE, GET_IP

Example phrases:
  "What's my battery level?"
  "How much disk space do I have?"
  "What's my IP address?"
  "Show me system info"
"""
from __future__ import annotations

from typing import Any, Optional
from kaoruko.plugins.plugin_base import KaorukoPlugin


class Plugin(KaorukoPlugin):
    name        = "system_info"
    version     = "1.0.0"
    author      = "Kaoruko Team"
    description = "Provides system information: battery, disk, RAM, IP, CPU"
    intents     = [
        "GET_SYSTEM_INFO",
        "GET_BATTERY",
        "GET_DISK_SPACE",
        "GET_IP",
        "GET_CPU",
        "GET_RAM",
    ]

    def on_load(self) -> None:
        try:
            import psutil
            self._psutil = psutil
        except ImportError:
            self._psutil = None

    def handle_intent(
        self,
        intent: str,
        entities: dict[str, Any],
        session: Optional[Any] = None,
    ) -> Optional[str]:
        if intent == "GET_BATTERY":
            return self._battery()
        if intent == "GET_DISK_SPACE":
            return self._disk()
        if intent == "GET_IP":
            return self._ip()
        if intent == "GET_CPU":
            return self._cpu()
        if intent == "GET_RAM":
            return self._ram()
        if intent == "GET_SYSTEM_INFO":
            parts = [self._cpu(), self._ram(), self._disk()]
            return " ".join(p for p in parts if p)
        return None

    def get_example_phrases(self) -> list[str]:
        return [
            "What's my battery level?",
            "How much disk space do I have?",
            "What's my IP address?",
            "Show me CPU usage",
            "How much RAM am I using?",
        ]

    # ── Implementations ───────────────────────────────────────────────────────

    def _battery(self) -> str:
        if not self._psutil:
            return "psutil is required for battery info~"
        try:
            bat = self._psutil.sensors_battery()
            if bat is None:
                return "No battery detected — you must be on a desktop~"
            status = "charging" if bat.power_plugged else "on battery"
            return f"Battery is at {bat.percent:.0f}%, {status}~"
        except Exception:
            return "I couldn't read battery info~"

    def _disk(self) -> str:
        if not self._psutil:
            return "psutil is required for disk info~"
        try:
            disk = self._psutil.disk_usage("/")
            free_gb  = disk.free  / (1024**3)
            total_gb = disk.total / (1024**3)
            return f"Disk space: {free_gb:.1f} GB free of {total_gb:.0f} GB total~"
        except Exception:
            return "I couldn't read disk info~"

    def _ip(self) -> str:
        try:
            import socket
            hostname = socket.gethostname()
            ip = socket.gethostbyname(hostname)
            return f"Your local IP address is {ip}~"
        except Exception:
            return "I couldn't determine your IP address~"

    def _cpu(self) -> str:
        if not self._psutil:
            return "psutil is required for CPU info~"
        try:
            usage = self._psutil.cpu_percent(interval=0.5)
            count = self._psutil.cpu_count()
            return f"CPU usage is {usage:.0f}% across {count} cores~"
        except Exception:
            return "I couldn't read CPU info~"

    def _ram(self) -> str:
        if not self._psutil:
            return "psutil is required for RAM info~"
        try:
            mem = self._psutil.virtual_memory()
            used_gb  = mem.used  / (1024**3)
            total_gb = mem.total / (1024**3)
            return f"RAM: {used_gb:.1f} GB used of {total_gb:.0f} GB — {mem.percent:.0f}% full~"
        except Exception:
            return "I couldn't read RAM info~"
