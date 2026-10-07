"""
kaoruko/execution/handlers/window_manager.py
"""
from __future__ import annotations
import subprocess
from kaoruko.infrastructure.logging.logger import get_logger
log = get_logger("execution.window_manager")

try:
    import win32gui, win32con, win32api
    _W32 = True
except ImportError:
    _W32 = False

class WindowManagerHandler:
    def __init__(self, config) -> None:
        self.config = config

    def minimize_all(self, **kwargs) -> str:
        subprocess.Popen(
            ["powershell", "-command",
             "(New-Object -com Shell.Application).minimizeall()"]
        )
        return "Minimized all windows~"

    def show_desktop(self, **kwargs) -> str:
        return self.minimize_all()

    def close_active_window(self, **kwargs) -> str:
        if _W32:
            hwnd = win32gui.GetForegroundWindow()
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            log.info("active_window_closed", hwnd=hwnd)
            return "Closed active window~"
        return "Window close requires pywin32~"

    def maximize_active_window(self, **kwargs) -> str:
        if _W32:
            hwnd = win32gui.GetForegroundWindow()
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            return "Maximized~"
        return ""

    def minimize_active_window(self, **kwargs) -> str:
        if _W32:
            hwnd = win32gui.GetForegroundWindow()
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            return "Minimized~"
        return ""
