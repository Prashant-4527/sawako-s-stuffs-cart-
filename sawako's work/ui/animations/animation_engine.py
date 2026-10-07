"""
kaoruko/ui/animations/animation_engine.py

Qt animation primitives for Kaoruko's UI.
All animations use QPropertyAnimation + QEasingCurve for GPU-accelerated,
smooth 60fps rendering.

Provides:
  - FadeAnimation       — opacity fade in/out
  - SlideAnimation      — slide in/out from any direction
  - PulseAnimation      — pulsing scale/glow effect for the orb
  - WaveformAnimation   — real-time audio waveform painter
  - GlowEffect          — QGraphicsEffect neon glow
  - OrbAnimator         — full orb state machine with animations
"""
from __future__ import annotations

import math
import random
import time
from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve, QPoint, QPropertyAnimation, QRect,
    QSequentialAnimationGroup, QParallelAnimationGroup,
    QTimer, Qt, pyqtProperty, pyqtSignal, QObject, QSize,
)
from PyQt6.QtGui import (
    QColor, QPainter, QPainterPath, QPen, QRadialGradient,
    QLinearGradient, QBrush, QFont, QFontMetrics,
)
from PyQt6.QtWidgets import QWidget, QGraphicsDropShadowEffect


# ── Glow shadow effect ────────────────────────────────────────────────────────

def make_glow_effect(
    color: str = "#9B5CFF",
    blur_radius: int = 30,
    offset: tuple[int, int] = (0, 0),
) -> QGraphicsDropShadowEffect:
    """Create a neon glow drop-shadow effect."""
    effect = QGraphicsDropShadowEffect()
    effect.setColor(QColor(color))
    effect.setBlurRadius(blur_radius)
    effect.setOffset(*offset)
    return effect


# ── Fade animation ────────────────────────────────────────────────────────────

def fade_in(widget: QWidget, duration: int = 300) -> QPropertyAnimation:
    anim = QPropertyAnimation(widget, b"windowOpacity")
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.start()
    return anim


def fade_out(widget: QWidget, duration: int = 250) -> QPropertyAnimation:
    anim = QPropertyAnimation(widget, b"windowOpacity")
    anim.setDuration(duration)
    anim.setStartValue(widget.windowOpacity())
    anim.setEndValue(0.0)
    anim.setEasingCurve(QEasingCurve.Type.InCubic)
    anim.start()
    return anim


# ── Slide animation ───────────────────────────────────────────────────────────

def slide_in_from_bottom(widget: QWidget, distance: int = 40, duration: int = 350) -> QPropertyAnimation:
    start_pos = widget.pos() + QPoint(0, distance)
    end_pos   = widget.pos()
    anim = QPropertyAnimation(widget, b"pos")
    anim.setDuration(duration)
    anim.setStartValue(start_pos)
    anim.setEndValue(end_pos)
    anim.setEasingCurve(QEasingCurve.Type.OutBack)
    anim.start()
    return anim


# ── Waveform painter ──────────────────────────────────────────────────────────

class WaveformWidget(QWidget):
    """
    Real-time audio waveform visualization.
    Paints a smooth sine-wave style bar graph that reacts to audio levels.
    Updates at 60fps when active.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedHeight(40)

        self._bars = 32
        self._levels: list[float] = [0.0] * self._bars
        self._targets: list[float] = [0.0] * self._bars
        self._phase = 0.0
        self._active = False
        self._color_primary = QColor("#9B5CFF")
        self._color_secondary = QColor("#5CE0FF")

        self._timer = QTimer(self)
        self._timer.setInterval(16)   # 60fps
        self._timer.timeout.connect(self._tick)

    def set_colors(self, primary: str, secondary: str) -> None:
        self._color_primary   = QColor(primary)
        self._color_secondary = QColor(secondary)

    def set_active(self, active: bool) -> None:
        self._active = active
        if active:
            self._timer.start()
        else:
            self._timer.stop()
            self._targets = [0.0] * self._bars
            self.update()

    def push_audio_level(self, rms: float) -> None:
        """Feed real-time audio RMS level (0.0-1.0)."""
        noise = [random.gauss(0, 0.12) for _ in range(self._bars)]
        center_boost = [math.cos((i / self._bars - 0.5) * math.pi) ** 2
                        for i in range(self._bars)]
        self._targets = [
            max(0.0, min(1.0, rms * center_boost[i] + abs(noise[i]) * 0.3))
            for i in range(self._bars)
        ]

    def _tick(self) -> None:
        self._phase += 0.08
        if not self._active:
            # Idle breathing animation
            for i in range(self._bars):
                wave = 0.08 * math.sin(self._phase + i * 0.4)
                self._targets[i] = max(0.04, abs(wave))
        # Smooth toward targets
        for i in range(self._bars):
            diff = self._targets[i] - self._levels[i]
            self._levels[i] += diff * 0.25
        self.update()

    def start_idle(self) -> None:
        self._active = False
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._levels = [0.0] * self._bars
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        bar_w = w / self._bars
        cx = w / 2

        for i, level in enumerate(self._levels):
            bar_h = max(2, level * (h - 4))
            x = i * bar_w + bar_w * 0.15
            bw = bar_w * 0.7
            y = (h - bar_h) / 2

            # Color interpolation: primary → secondary based on position
            t = i / max(self._bars - 1, 1)
            r = int(self._color_primary.red()   * (1-t) + self._color_secondary.red()   * t)
            g = int(self._color_primary.green() * (1-t) + self._color_secondary.green() * t)
            b = int(self._color_primary.blue()  * (1-t) + self._color_secondary.blue()  * t)

            # Alpha based on level
            alpha = int(40 + level * 215)
            color = QColor(r, g, b, alpha)

            painter.setBrush(QBrush(color))
            painter.setPen(Qt.PenStyle.NoPen)

            # Rounded bar
            rect = QRect(int(x), int(y), int(bw), int(bar_h))
            painter.drawRoundedRect(rect, 2, 2)

        painter.end()


# ── Orb animator ─────────────────────────────────────────────────────────────

class OrbState:
    IDLE       = "idle"
    LISTENING  = "listening"
    PROCESSING = "processing"
    SPEAKING   = "speaking"
    ERROR      = "error"


class OrbWidget(QWidget):
    """
    The central floating orb — Kaoruko's visual avatar.
    Paints a layered radial gradient sphere with animated glow rings.
    State-driven: each state has unique color, pulse speed, ring count.
    """

    clicked = pyqtSignal()

    # State → (core_color, ring_color, pulse_speed_ms, ring_count)
    _STATE_CONFIG = {
        OrbState.IDLE:       ("#9B5CFF", "#5CE0FF", 3000, 2),
        OrbState.LISTENING:  ("#5CE0FF", "#FFFFFF", 600,  3),
        OrbState.PROCESSING: ("#FFD166", "#FF9F45", 400,  4),
        OrbState.SPEAKING:   ("#4DFFB4", "#9B5CFF", 250,  3),
        OrbState.ERROR:      ("#FF5C8D", "#FF2255", 200,  2),
    }

    def __init__(self, size: int = 80, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._state = OrbState.IDLE
        self._pulse_phase = 0.0
        self._ring_phase  = 0.0
        self._hover       = False
        self._press       = False

        self._timer = QTimer(self)
        self._timer.setInterval(16)  # 60fps
        self._timer.timeout.connect(self._animate_tick)
        self._timer.start()

        # Apply glow effect
        glow = make_glow_effect(color="#9B5CFF", blur_radius=28)
        self.setGraphicsEffect(glow)
        self._glow_effect = glow

    # ── Public API ────────────────────────────────────────────────────────────

    def set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        cfg = self._STATE_CONFIG.get(state, self._STATE_CONFIG[OrbState.IDLE])
        # Update glow color
        self._glow_effect.setColor(QColor(cfg[0]))
        self.update()

    def set_theme(self, palette: object) -> None:
        """Update orb colors from theme palette."""
        pass  # Colors driven by state config; palette used for ring colors

    # ── Internal ──────────────────────────────────────────────────────────────

    def _animate_tick(self) -> None:
        cfg = self._STATE_CONFIG.get(self._state, self._STATE_CONFIG[OrbState.IDLE])
        pulse_speed = cfg[2]
        self._pulse_phase += (2 * math.pi) / (pulse_speed / 16)
        self._ring_phase  += (2 * math.pi) / 80
        if self._pulse_phase > 2 * math.pi:
            self._pulse_phase -= 2 * math.pi
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cfg = self._STATE_CONFIG.get(self._state, self._STATE_CONFIG[OrbState.IDLE])
        core_color  = QColor(cfg[0])
        ring_color  = QColor(cfg[1])
        ring_count  = cfg[3]

        cx, cy = self._size / 2, self._size / 2
        base_r = self._size * 0.35

        # Pulse scale
        pulse = 1.0 + 0.06 * math.sin(self._pulse_phase)
        if self._hover:
            pulse += 0.04
        if self._press:
            pulse -= 0.08
        r = base_r * pulse

        # ── Outer ring waves ─────────────────────────────────────────────────
        for i in range(ring_count):
            ring_r = r + 8 + i * 9 + 6 * math.sin(self._ring_phase + i * 1.2)
            alpha = int(max(0, 80 - i * 25) * (0.5 + 0.5 * math.sin(self._ring_phase + i)))
            ring_c = QColor(ring_color)
            ring_c.setAlpha(alpha)
            pen = QPen(ring_c, 1.2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(
                int(cx - ring_r), int(cy - ring_r),
                int(ring_r * 2), int(ring_r * 2)
            )

        # ── Core orb body ─────────────────────────────────────────────────────
        grad = QRadialGradient(cx - r * 0.25, cy - r * 0.25, r * 1.4)
        light = core_color.lighter(170)
        light.setAlpha(255)
        grad.setColorAt(0.0,  light)
        grad.setColorAt(0.45, core_color)
        mid = core_color.darker(130)
        mid.setAlpha(230)
        grad.setColorAt(0.85, mid)
        dark = core_color.darker(200)
        dark.setAlpha(200)
        grad.setColorAt(1.0,  dark)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))

        # ── Specular highlight ───────────────────────────────────────────────
        hl_r = r * 0.35
        hl_grad = QRadialGradient(cx - r * 0.3, cy - r * 0.35, hl_r)
        hl_grad.setColorAt(0.0, QColor(255, 255, 255, 160))
        hl_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(QBrush(hl_grad))
        painter.drawEllipse(
            int(cx - r * 0.3 - hl_r), int(cy - r * 0.35 - hl_r),
            int(hl_r * 2), int(hl_r * 2)
        )

        painter.end()

    def mousePressEvent(self, event) -> None:
        self._press = True

    def mouseReleaseEvent(self, event) -> None:
        self._press = False
        self.clicked.emit()

    def enterEvent(self, event) -> None:
        self._hover = True

    def leaveEvent(self, event) -> None:
        self._hover = False
