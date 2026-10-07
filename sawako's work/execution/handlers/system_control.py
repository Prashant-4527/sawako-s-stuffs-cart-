"""
kaoruko/execution/handlers/system_control.py
System-level control: power, lock, brightness.
"""
from __future__ import annotations
import asyncio, os, subprocess, ctypes, platform
from typing import Optional, TYPE_CHECKING
from kaoruko.infrastructure.logging.logger import get_logger
if TYPE_CHECKING:
    from kaoruko.infrastructure.config.schema import KaorukoConfig
    from kaoruko.core.event_bus import EventBus

log = get_logger("execution.system_control")

class SystemControlHandler:
    def __init__(self, config: "KaorukoConfig", bus: "EventBus") -> None:
        self.config = config
        self.bus = bus

    def shutdown(self, delay: int = 5, **kwargs) -> str:
        log.info("system_shutdown_initiated", delay=delay)
        if platform.system() == "Windows":
            subprocess.run(["shutdown", "/s", "/t", str(delay)])
        return f"Shutting down in {delay} seconds. Sayonara~"

    def restart(self, delay: int = 5, **kwargs) -> str:
        log.info("system_restart_initiated", delay=delay)
        if platform.system() == "Windows":
            subprocess.run(["shutdown", "/r", "/t", str(delay)])
        return f"Restarting in {delay} seconds~"

    def sleep(self, **kwargs) -> str:
        log.info("system_sleep")
        if platform.system() == "Windows":
            subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
        return "Oyasumi nasai~ Going to sleep~"

    def lock_screen(self, **kwargs) -> str:
        log.info("system_lock")
        if platform.system() == "Windows":
            ctypes.windll.user32.LockWorkStation()
        return "Screen locked~ Stay safe~"

    def cancel_shutdown(self, **kwargs) -> str:
        if platform.system() == "Windows":
            subprocess.run(["shutdown", "/a"])
        return "Shutdown cancelled~"

    def set_brightness(self, level: int, **kwargs) -> str:
        level = max(0, min(100, int(level)))
        try:
            import screen_brightness_control as sbc
            sbc.set_brightness(level)
            log.info("brightness_set", level=level)
            return f"Brightness set to {level}%~"
        except ImportError:
            try:
                subprocess.run(
                    ["powershell", "-command",
                     f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{level})"],
                    capture_output=True
                )
                return f"Brightness set to {level}%~"
            except Exception as e:
                log.error("brightness_error", error=str(e))
                return "I had trouble adjusting brightness~"
