"""
kaoruko/execution/handlers/audio_control.py
Windows volume and audio device control via pycaw (Core Audio API).
"""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING
from kaoruko.infrastructure.logging.logger import get_logger
if TYPE_CHECKING:
    from kaoruko.infrastructure.config.schema import KaorukoConfig

log = get_logger("execution.audio_control")

try:
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    _PYCAW_AVAILABLE = True
except ImportError:
    _PYCAW_AVAILABLE = False
    log.warning("pycaw_unavailable", message="Volume control will use fallback")


def _get_volume_interface():
    if not _PYCAW_AVAILABLE:
        return None
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


class AudioControlHandler:
    def __init__(self, config: "KaorukoConfig") -> None:
        self.config = config

    def set_volume(self, level: Optional[int] = None, direction: Optional[str] = None, **kwargs) -> str:
        if level is not None:
            return self._set_absolute_volume(int(level))
        if direction:
            return self._adjust_relative(direction)
        return "Please specify a volume level~"

    def set_mute(self, mute: bool = True, **kwargs) -> str:
        if _PYCAW_AVAILABLE:
            try:
                vol = _get_volume_interface()
                vol.SetMute(1 if mute else 0, None)
                log.info("mute_set", mute=mute)
                return "Muted~" if mute else "Unmuted~"
            except Exception as e:
                log.error("mute_error", error=str(e))
        # Fallback: nircmd
        import subprocess
        cmd = "nircmd mutesysvolume 1" if mute else "nircmd mutesysvolume 0"
        subprocess.run(cmd, shell=True, capture_output=True)
        return "Muted~" if mute else "Unmuted~"

    def get_volume(self, **kwargs) -> int:
        if _PYCAW_AVAILABLE:
            try:
                vol = _get_volume_interface()
                return int(vol.GetMasterVolumeLevelScalar() * 100)
            except Exception:
                pass
        return -1

    def _set_absolute_volume(self, level: int) -> str:
        level = max(0, min(100, level))
        if _PYCAW_AVAILABLE:
            try:
                vol = _get_volume_interface()
                vol.SetMasterVolumeLevelScalar(level / 100.0, None)
                log.info("volume_set", level=level)
                return f"Volume set to {level}%~"
            except Exception as e:
                log.error("volume_set_error", error=str(e))
        # Fallback: nircmd or powershell
        import subprocess
        subprocess.run(
            ["powershell", "-command",
             f"$obj = New-Object -ComObject WScript.Shell; $obj.SendKeys([char]173)"],
            capture_output=True
        )
        return f"Volume adjusted~"

    def _adjust_relative(self, direction: str) -> str:
        import subprocess
        direction = direction.lower()
        current = self.get_volume()
        if current < 0:
            current = 50
        if any(w in direction for w in ("up", "increase", "louder", "raise")):
            new = min(100, current + 10)
        else:
            new = max(0, current - 10)
        return self._set_absolute_volume(new)
