"""
kaoruko/execution/handlers/network_control.py
WiFi and Bluetooth toggle via Windows netsh / PowerShell.
"""
from __future__ import annotations
import subprocess
from kaoruko.infrastructure.logging.logger import get_logger
log = get_logger("execution.network")

class NetworkControlHandler:
    def __init__(self, config) -> None:
        self.config = config

    def set_wifi(self, enable: bool = True, **kwargs) -> str:
        action = "enable" if enable else "disable"
        try:
            subprocess.run(
                ["netsh", "interface", "set", "interface", "Wi-Fi", action],
                capture_output=True, timeout=5
            )
            log.info("wifi_toggled", enable=enable)
            return f"WiFi {'enabled' if enable else 'disabled'}~"
        except Exception as e:
            log.error("wifi_error", error=str(e))
            return "I had trouble changing WiFi settings~"

    def set_bluetooth(self, enable: bool = True, **kwargs) -> str:
        state = "1" if enable else "0"
        try:
            script = (
                f"$bt = [Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime];"
                f"$radios = [Windows.Devices.Radios.Radio]::GetRadiosAsync().GetResults();"
                f"$bt_radio = $radios | Where-Object {{ $_.Kind -eq 'Bluetooth' }};"
                f"if ($bt_radio) {{ $bt_radio.SetStateAsync({'1' if enable else '0'}).GetResults() }}"
            )
            subprocess.run(
                ["powershell", "-command", script],
                capture_output=True, timeout=10
            )
            return f"Bluetooth {'enabled' if enable else 'disabled'}~"
        except Exception as e:
            log.error("bluetooth_error", error=str(e))
            return "I had trouble changing Bluetooth settings~"


"""
kaoruko/execution/handlers/window_manager.py
Win32 window management: minimize, maximize, move, resize.
"""
from kaoruko.infrastructure.logging.logger import get_logger as _log
_wlog = _log("execution.window_manager")

try:
    import win32gui, win32con
    _W32 = True
except ImportError:
    _W32 = False

class WindowManagerHandler:
    def __init__(self, config) -> None:
        self.config = config

    def minimize_all(self, **kwargs) -> str:
        if _W32:
            import win32api
            win32api.keybd_event(0x5B, 0, 0, 0)   # Win key
            win32api.keybd_event(0x4D, 0, 0, 0)   # M key
        return "Minimized all windows~"

    def show_desktop(self, **kwargs) -> str:
        import subprocess
        subprocess.Popen(["powershell", "-command",
                         "(New-Object -com Shell.Application).minimizeall()"])
        return "Showing desktop~"

    def close_active_window(self, **kwargs) -> str:
        if _W32:
            hwnd = win32gui.GetForegroundWindow()
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            return "Closed active window~"
        return "Window close unavailable~"


"""
kaoruko/execution/handlers/notification.py
Windows toast notifications via win10toast-reborn.
"""
from kaoruko.infrastructure.logging.logger import get_logger as _nlog
_nnotify = _nlog("execution.notification")

try:
    from win10toast import ToastNotifier
    _TOAST = True
except ImportError:
    _TOAST = False

class NotificationHandler:
    def __init__(self, config) -> None:
        self.config = config
        self._toaster = ToastNotifier() if _TOAST else None

    def show_notification(
        self,
        title: str = "Kaoruko",
        message: str = "",
        duration: int = 5,
        **kwargs
    ) -> str:
        if self._toaster:
            try:
                self._toaster.show_toast(
                    title, message,
                    duration=duration,
                    threaded=True,
                )
                return ""   # Silent — notification is the response
            except Exception as e:
                _nnotify.error("toast_error", error=str(e))
        _nnotify.info("notification_fallback", title=title, message=message)
        return ""
