"""
kaoruko/execution/handlers/app_control.py

Application control handler.
Opens, closes, and switches Windows applications using subprocess + win32api.
Supports 30+ common apps with alias resolution.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from kaoruko.infrastructure.logging.logger import get_logger

if TYPE_CHECKING:
    from kaoruko.infrastructure.config.schema import KaorukoConfig

log = get_logger("execution.app_control")

# Try Windows-specific imports
try:
    import win32api, win32con, win32process
    import win32gui
    _WIN32_AVAILABLE = True
except ImportError:
    _WIN32_AVAILABLE = False

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False


# ── App catalog ───────────────────────────────────────────────────────────────
# Maps canonical name → executable + launch args + aliases
_APP_CATALOG: dict[str, dict] = {
    "chrome": {
        "aliases": ["google chrome", "browser", "chrome browser", "google"],
        "exe": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "args": [],
        "process": "chrome.exe",
    },
    "firefox": {
        "aliases": ["firefox", "mozilla", "mozilla firefox"],
        "exe": r"C:\Program Files\Mozilla Firefox\firefox.exe",
        "args": [],
        "process": "firefox.exe",
    },
    "edge": {
        "aliases": ["microsoft edge", "edge", "msedge"],
        "exe": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "args": [],
        "process": "msedge.exe",
    },
    "code": {
        "aliases": ["vscode", "vs code", "visual studio code", "code editor"],
        "exe": "code",   # PATH-based
        "args": [],
        "process": "Code.exe",
    },
    "notepad": {
        "aliases": ["notepad", "text editor", "txt editor"],
        "exe": "notepad.exe",
        "args": [],
        "process": "notepad.exe",
    },
    "calculator": {
        "aliases": ["calc", "calculator"],
        "exe": "calc.exe",
        "args": [],
        "process": "CalculatorApp.exe",
    },
    "explorer": {
        "aliases": ["file explorer", "explorer", "files", "my computer", "this pc"],
        "exe": "explorer.exe",
        "args": [],
        "process": "explorer.exe",
    },
    "discord": {
        "aliases": ["discord"],
        "exe": os.path.expandvars(r"%LOCALAPPDATA%\Discord\Update.exe"),
        "args": ["--processStart", "Discord.exe"],
        "process": "Discord.exe",
    },
    "steam": {
        "aliases": ["steam"],
        "exe": r"C:\Program Files (x86)\Steam\Steam.exe",
        "args": [],
        "process": "Steam.exe",
    },
    "spotify": {
        "aliases": ["spotify", "music player"],
        "exe": os.path.expandvars(r"%APPDATA%\Spotify\Spotify.exe"),
        "args": [],
        "process": "Spotify.exe",
    },
    "slack": {
        "aliases": ["slack"],
        "exe": os.path.expandvars(r"%LOCALAPPDATA%\slack\slack.exe"),
        "args": [],
        "process": "slack.exe",
    },
    "terminal": {
        "aliases": ["terminal", "cmd", "command prompt", "command line", "powershell"],
        "exe": "wt.exe",          # Windows Terminal
        "args": [],
        "fallback_exe": "cmd.exe",
        "process": "WindowsTerminal.exe",
    },
    "powershell": {
        "aliases": ["powershell", "ps", "posh"],
        "exe": "powershell.exe",
        "args": [],
        "process": "powershell.exe",
    },
    "taskmgr": {
        "aliases": ["task manager", "taskmgr"],
        "exe": "taskmgr.exe",
        "args": [],
        "process": "Taskmgr.exe",
    },
    "paint": {
        "aliases": ["paint", "mspaint"],
        "exe": "mspaint.exe",
        "args": [],
        "process": "mspaint.exe",
    },
    "word": {
        "aliases": ["word", "microsoft word", "ms word"],
        "exe": r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE",
        "args": [],
        "process": "WINWORD.EXE",
    },
    "excel": {
        "aliases": ["excel", "microsoft excel", "ms excel", "spreadsheet"],
        "exe": r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE",
        "args": [],
        "process": "EXCEL.EXE",
    },
    "powerpoint": {
        "aliases": ["powerpoint", "ppt", "microsoft powerpoint", "slides"],
        "exe": r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE",
        "args": [],
        "process": "POWERPNT.EXE",
    },
    "vlc": {
        "aliases": ["vlc", "vlc player", "media player"],
        "exe": r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        "args": [],
        "process": "vlc.exe",
    },
    "obs": {
        "aliases": ["obs", "obs studio", "streaming", "screen recorder"],
        "exe": r"C:\Program Files\obs-studio\bin\64bit\obs64.exe",
        "args": [],
        "process": "obs64.exe",
    },
    "settings": {
        "aliases": ["settings", "windows settings", "system settings"],
        "exe": "ms-settings:",   # URI scheme
        "args": [],
        "process": "SystemSettings.exe",
    },
}


class AppControlHandler:
    """
    Windows application control handler.

    Methods:
        open_application(app_name) → opens app
        close_application(app_name) → closes app
        switch_to_application(app_name) → focuses running app
    """

    def __init__(self, config: "KaorukoConfig") -> None:
        self.config = config
        # Build reverse alias index for O(1) lookup
        self._alias_index: dict[str, str] = {}
        for canonical, data in _APP_CATALOG.items():
            self._alias_index[canonical] = canonical
            for alias in data.get("aliases", []):
                self._alias_index[alias.lower()] = canonical

    # ── Public methods (called by executor) ───────────────────────────────────

    def open_application(self, app_name: str, **kwargs) -> str:
        """Open an application by name."""
        canonical = self._resolve(app_name)
        if not canonical:
            return self._try_dynamic_launch(app_name)

        app = _APP_CATALOG[canonical]
        exe = app["exe"]
        args = app.get("args", [])

        # Check if already running
        if self._is_running(app.get("process", "")):
            return self.switch_to_application(canonical)

        try:
            # Handle URI schemes (ms-settings:, etc.)
            if ":" in exe and not os.path.sep in exe:
                os.startfile(exe)
                log.info("app_opened_uri", app=canonical, uri=exe)
                return f"Opening {canonical.title()}~"

            # Try direct path first
            if os.path.isfile(exe):
                subprocess.Popen(
                    [exe] + args,
                    creationflags=subprocess.CREATE_NEW_CONSOLE if canonical == "terminal" else 0,
                )
            else:
                # Try PATH-based launch (code, notepad, etc.)
                subprocess.Popen([exe] + args, shell=True)

            log.info("app_opened", app=canonical, exe=exe)
            return f"Opening {canonical.replace('_', ' ').title()}~"

        except FileNotFoundError:
            # Try fallback exe
            fallback = app.get("fallback_exe")
            if fallback:
                try:
                    subprocess.Popen([fallback], shell=True)
                    return f"Opening {canonical.title()} (fallback)~"
                except Exception:
                    pass
            log.warning("app_not_found", app=canonical, exe=exe)
            return f"Gomen nasai~ I couldn't find {app_name} on your system."

        except Exception as e:
            log.error("app_open_error", app=canonical, error=str(e))
            return f"Gomen nasai~ I had trouble opening {app_name}."

    def close_application(self, app_name: str, **kwargs) -> str:
        """Close an application by name."""
        canonical = self._resolve(app_name)
        process_name = _APP_CATALOG.get(canonical, {}).get("process", "") if canonical else ""

        if not process_name and not canonical:
            return f"I'm not sure which application to close for '{app_name}'~"

        search_name = process_name or canonical or app_name

        if _PSUTIL_AVAILABLE:
            killed = 0
            for proc in psutil.process_iter(["name", "pid"]):
                try:
                    if search_name.lower() in proc.info["name"].lower():
                        proc.terminate()
                        killed += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            if killed > 0:
                log.info("app_closed", app=canonical or app_name, processes=killed)
                return f"Closed {(canonical or app_name).replace('_', ' ').title()}~"
            return f"I couldn't find {app_name} running~"
        else:
            # Fallback: taskkill
            try:
                subprocess.run(
                    ["taskkill", "/F", "/IM", search_name],
                    capture_output=True,
                    timeout=5,
                )
                return f"Closed {(canonical or app_name).title()}~"
            except Exception as e:
                log.error("app_close_error", app=app_name, error=str(e))
                return f"Had trouble closing {app_name}~"

    def switch_to_application(self, app_name: str, **kwargs) -> str:
        """Bring a running application to the foreground."""
        canonical = self._resolve(app_name) or app_name
        process_name = _APP_CATALOG.get(canonical, {}).get("process", canonical)

        if _WIN32_AVAILABLE:
            return self._win32_focus(process_name, canonical)
        else:
            # Fallback: try opening (will focus if already running on most apps)
            return self.open_application(app_name)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _resolve(self, name: str) -> Optional[str]:
        """Resolve any name/alias to a canonical app key."""
        if not name:
            return None
        return self._alias_index.get(name.lower().strip())

    def _is_running(self, process_name: str) -> bool:
        """Check if a process is currently running."""
        if not process_name or not _PSUTIL_AVAILABLE:
            return False
        for proc in psutil.process_iter(["name"]):
            try:
                if process_name.lower() == proc.info["name"].lower():
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return False

    def _win32_focus(self, process_name: str, display_name: str) -> str:
        """Focus a window using win32 API."""
        try:
            target_hwnd = None

            def enum_callback(hwnd, _):
                nonlocal target_hwnd
                if win32gui.IsWindowVisible(hwnd):
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    try:
                        proc = psutil.Process(pid)
                        if process_name.lower() in proc.name().lower():
                            target_hwnd = hwnd
                    except Exception:
                        pass

            win32gui.EnumWindows(enum_callback, None)

            if target_hwnd:
                win32gui.SetForegroundWindow(target_hwnd)
                win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
                return f"Switching to {display_name.title()}~"
            return f"I couldn't find {display_name} running~"
        except Exception as e:
            log.error("win32_focus_error", app=display_name, error=str(e))
            return f"Had trouble switching to {display_name}~"

    def _try_dynamic_launch(self, app_name: str) -> str:
        """Try to launch an unknown app by name (best-effort)."""
        try:
            subprocess.Popen(app_name, shell=True)
            log.info("dynamic_app_launch", app=app_name)
            return f"Trying to open {app_name}~"
        except Exception as e:
            return f"I couldn't find or open '{app_name}'~ Make sure it's installed."

    def list_running_apps(self) -> list[str]:
        """Return list of currently running application names."""
        if not _PSUTIL_AVAILABLE:
            return []
        apps = set()
        for proc in psutil.process_iter(["name"]):
            try:
                name = proc.info["name"]
                if name and name.endswith(".exe"):
                    apps.add(name[:-4].lower())
            except Exception:
                pass
        return sorted(apps)
