"""
kaoruko/ui/widgets/status_bar.py

Status bar widget: shows assistant state, model, uptime, and mic indicator.
"""
from __future__ import annotations

import time
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget


class StatusDot(QLabel):
    """Animated blinking dot for microphone active state."""
    def __init__(self, color: str = "#4DFFB4", parent=None):
        super().__init__("●", parent)
        self._color = color
        self._visible_state = True
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._blink)
        self.setStyleSheet(f"color: {color}; font-size: 9px;")

    def start_blink(self) -> None:
        self._timer.start(600)

    def stop_blink(self) -> None:
        self._timer.stop()
        self._visible_state = True
        self.setStyleSheet(f"color: {self._color}; font-size: 9px;")

    def set_color(self, color: str) -> None:
        self._color = color
        self.setStyleSheet(f"color: {color}; font-size: 9px;")

    def _blink(self) -> None:
        self._visible_state = not self._visible_state
        alpha = "ff" if self._visible_state else "40"
        self.setStyleSheet(f"color: {self._color}{alpha}; font-size: 9px;")


class StatusBar(QWidget):
    """Bottom status bar showing assistant state and system info."""

    def __init__(self, palette: object, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._start_time = time.time()
        self._setup_ui()

        # Uptime update timer
        self._uptime_timer = QTimer(self)
        self._uptime_timer.timeout.connect(self._update_uptime)
        self._uptime_timer.start(10_000)   # update every 10s

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(12)

        p = self._palette
        muted = getattr(p, "text_muted", "#6B6280")
        accent = getattr(p, "accent_primary", "#9B5CFF")

        # Mic dot
        self._mic_dot = StatusDot(color=getattr(p, "success", "#4DFFB4"))
        layout.addWidget(self._mic_dot)

        # State label
        self._state_label = QLabel("Idle")
        self._state_label.setStyleSheet(f"color: {accent}; font-size: 11px; font-weight: 600;")
        layout.addWidget(self._state_label)

        layout.addStretch()

        # AI model label
        self._model_label = QLabel("claude-haiku")
        self._model_label.setStyleSheet(f"color: {muted}; font-size: 10px;")
        layout.addWidget(self._model_label)

        # Separator
        sep = QLabel("·")
        sep.setStyleSheet(f"color: {muted}; font-size: 10px;")
        layout.addWidget(sep)

        # Uptime
        self._uptime_label = QLabel("0m")
        self._uptime_label.setStyleSheet(f"color: {muted}; font-size: 10px;")
        layout.addWidget(self._uptime_label)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_state(self, state: str) -> None:
        p = self._palette
        colors = {
            "idle":       getattr(p, "text_muted",       "#6B6280"),
            "listening":  getattr(p, "orb_listening",    "#5CE0FF"),
            "processing": getattr(p, "orb_processing",   "#FFD166"),
            "speaking":   getattr(p, "orb_speaking",     "#4DFFB4"),
            "error":      getattr(p, "error",            "#FF5C8D"),
        }
        color = colors.get(state.lower(), getattr(p, "text_secondary", "#B8AEDD"))
        label = state.title()
        self._state_label.setText(label)
        self._state_label.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 600;")

        # Mic dot behavior
        if state == "listening":
            self._mic_dot.set_color(getattr(p, "orb_listening", "#5CE0FF"))
            self._mic_dot.start_blink()
        else:
            self._mic_dot.stop_blink()
            self._mic_dot.set_color(
                getattr(p, "success", "#4DFFB4") if state == "idle"
                else getattr(p, "orb_processing", "#FFD166")
            )

    def set_model(self, model_name: str) -> None:
        self._model_label.setText(model_name[:20])

    def _update_uptime(self) -> None:
        elapsed = int(time.time() - self._start_time)
        if elapsed < 3600:
            self._uptime_label.setText(f"{elapsed // 60}m")
        else:
            self._uptime_label.setText(f"{elapsed // 3600}h {(elapsed % 3600) // 60}m")
