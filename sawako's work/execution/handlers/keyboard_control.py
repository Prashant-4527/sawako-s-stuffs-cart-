"""
kaoruko/execution/handlers/keyboard_control.py
Keyboard automation: shortcuts, media keys, typing.
"""
from __future__ import annotations
import time
from typing import TYPE_CHECKING
from kaoruko.infrastructure.logging.logger import get_logger
if TYPE_CHECKING:
    from kaoruko.infrastructure.config.schema import KaorukoConfig
log = get_logger("execution.keyboard")

try:
    import pynput.keyboard as pynkb
    _PYNPUT = True
except ImportError:
    _PYNPUT = False

try:
    import pyautogui
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False


class KeyboardControlHandler:
    def __init__(self, config) -> None:
        self.config = config
        self._kb = pynkb.Controller() if _PYNPUT else None

    def media_pause(self, **kwargs) -> str:
        return self._press_key(pynkb.Key.media_play_pause if _PYNPUT else None, "play/pause")

    def media_next(self, **kwargs) -> str:
        return self._press_key(pynkb.Key.media_next if _PYNPUT else None, "next track")

    def media_prev(self, **kwargs) -> str:
        return self._press_key(pynkb.Key.media_previous if _PYNPUT else None, "previous track")

    def type_text(self, text: str, **kwargs) -> str:
        if _PYNPUT and self._kb:
            self._kb.type(text)
            return f"Typed: {text[:30]}~"
        if _PYAUTOGUI:
            pyautogui.typewrite(text, interval=0.05)
            return f"Typed: {text[:30]}~"
        return "Keyboard control unavailable~"

    def press_shortcut(self, keys: str, **kwargs) -> str:
        """e.g. keys='ctrl+c' or 'win+d'"""
        if _PYAUTOGUI:
            parts = [k.strip().lower() for k in keys.split("+")]
            pyautogui.hotkey(*parts)
            return f"Pressed {keys}~"
        return "Shortcut unavailable~"

    def _press_key(self, key, name: str) -> str:
        if _PYNPUT and self._kb and key:
            self._kb.press(key)
            self._kb.release(key)
            log.info("key_pressed", key=name)
            return f"{name.title()}~"
        return f"Keyboard unavailable~"
