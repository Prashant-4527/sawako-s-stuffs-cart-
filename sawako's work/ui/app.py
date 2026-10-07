"""
kaoruko/ui/app.py

KaorukoApp — master Qt application shell.

Manages:
  - Orb mode: floating 80×80px draggable orb (default)
  - Dashboard mode: full 480×640px glass panel with transcript + history
  - Minimal mode: ultra-compact bar
  - Mode transitions with smooth animations
  - System tray icon for taskbar-free operation
  - Event bus subscriptions to update UI state
"""
from __future__ import annotations

import asyncio
import sys
from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import (
    Qt, QPoint, QTimer, QThread, pyqtSignal, pyqtSlot, QRect,
)
from PyQt6.QtGui import (
    QAction, QColor, QCursor, QFont, QIcon,
    QPainter, QPainterPath, QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMenu, QPushButton, QSizePolicy,
    QSystemTrayIcon, QVBoxLayout, QWidget,
)

from kaoruko.core.event_bus import KaorukoEvent
from kaoruko.infrastructure.logging.logger import get_logger
from kaoruko.ui.animations.animation_engine import (
    OrbWidget, OrbState, WaveformWidget,
    make_glow_effect, fade_in,
)
from kaoruko.ui.themes.theme_engine import (
    ThemeName, get_palette, build_global_stylesheet,
)
from kaoruko.ui.widgets.transcript_widget import TranscriptWidget
from kaoruko.ui.widgets.history_widget import HistoryWidget
from kaoruko.ui.widgets.status_bar import StatusBar

if TYPE_CHECKING:
    from kaoruko.core.assistant import KaorukoAssistant
    from kaoruko.infrastructure.config.schema import KaorukoConfig

log = get_logger("ui.app")


# ── Async bridge thread ───────────────────────────────────────────────────────

class AsyncBridgeThread(QThread):
    """
    Runs the asyncio event loop in a dedicated QThread.
    Allows coroutines (assistant startup, event bus) to run
    alongside the Qt event loop without blocking the UI.
    """

    def __init__(self, assistant: "KaorukoAssistant") -> None:
        super().__init__()
        self._assistant = assistant
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def run(self) -> None:
        import platform
        if platform.system() == "Windows":
            self._loop = asyncio.ProactorEventLoop()
        else:
            self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._assistant.startup())
        self._loop.run_forever()

    def get_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        return self._loop

    def schedule(self, coro) -> None:
        if self._loop:
            asyncio.run_coroutine_threadsafe(coro, self._loop)


# ── Orb window (always-on-top floating widget) ────────────────────────────────

class OrbWindow(QWidget):
    """
    The floating orb widget.
    Always-on-top, frameless, draggable, translucent.
    Double-click → expand to dashboard.
    Single click → toggle listen.
    """

    expand_requested = pyqtSignal()

    def __init__(self, palette: object, config: "KaorukoConfig") -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(110, 130)

        self._palette = palette
        self._config = config
        self._drag_start: Optional[QPoint] = None
        self._setup_ui()
        self._position_on_screen()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 8)
        layout.setSpacing(4)

        # Orb
        self._orb = OrbWidget(size=80)
        self._orb.clicked.connect(self._on_orb_click)
        layout.addWidget(self._orb, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Name label
        self._name_lbl = QLabel("香子")
        self._name_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        font = QFont("Yu Gothic UI", 10)
        self._name_lbl.setFont(font)
        p = self._palette
        self._name_lbl.setStyleSheet(
            f"color: {getattr(p,'text_muted','#6B6280')}; background: transparent;"
        )
        layout.addWidget(self._name_lbl)

        # Waveform
        self._waveform = WaveformWidget()
        self._waveform.setFixedHeight(24)
        self._waveform.start_idle()
        layout.addWidget(self._waveform)

    def set_state(self, state: str) -> None:
        self._orb.set_state(state)
        p = self._palette
        state_colors = {
            "idle":       getattr(p, "text_muted",     "#6B6280"),
            "listening":  getattr(p, "orb_listening",  "#5CE0FF"),
            "processing": getattr(p, "orb_processing", "#FFD166"),
            "speaking":   getattr(p, "orb_speaking",   "#4DFFB4"),
            "error":      getattr(p, "error",          "#FF5C8D"),
        }
        color = state_colors.get(state, getattr(p, "text_muted", "#6B6280"))
        self._name_lbl.setStyleSheet(f"color: {color}; background: transparent;")

        if state == "listening":
            self._waveform.set_active(True)
        elif state == "speaking":
            self._waveform.set_active(True)
        else:
            self._waveform.set_active(False)
            self._waveform.start_idle()

    def _on_orb_click(self) -> None:
        # Single click → request expand
        self.expand_requested.emit()

    def _position_on_screen(self) -> None:
        """Position orb at configured screen location."""
        screen = QApplication.primaryScreen().geometry()
        pos_name = getattr(self._config.ui, "start_position", None)
        pos_str = pos_name.value if pos_name else "bottom_right"
        w, h = self.width(), self.height()
        margin = 24

        positions = {
            "bottom_right": QPoint(screen.width() - w - margin, screen.height() - h - margin - 48),
            "bottom_left":  QPoint(margin, screen.height() - h - margin - 48),
            "top_right":    QPoint(screen.width() - w - margin, margin + 40),
            "top_left":     QPoint(margin, margin + 40),
            "center":       QPoint((screen.width() - w) // 2, (screen.height() - h) // 2),
        }
        self.move(positions.get(pos_str, positions["bottom_right"]))

    # Drag support
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_start and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_start)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_start = None

    def mouseDoubleClickEvent(self, event) -> None:
        self.expand_requested.emit()


# ── Dashboard window ──────────────────────────────────────────────────────────

class DashboardWindow(QWidget):
    """
    Full dashboard panel: transcript + history + controls.
    Glass morphism aesthetic, 480×600px.
    """

    collapsed = pyqtSignal()

    def __init__(self, palette: object, config: "KaorukoConfig") -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(460, 580)
        self.resize(460, 580)

        self._palette = palette
        self._config = config
        self._drag_start: Optional[QPoint] = None
        self._setup_ui()
        self._apply_style()

    def _setup_ui(self) -> None:
        p = self._palette

        # Root container (glass panel)
        self._root = QWidget(self)
        self._root.setObjectName("KaorukoDashboard")
        self._root.setGeometry(0, 0, self.width(), self.height())

        root_layout = QVBoxLayout(self._root)
        root_layout.setContentsMargins(16, 12, 16, 12)
        root_layout.setSpacing(10)

        # ── Title bar ────────────────────────────────────────────────────────
        title_bar = QHBoxLayout()

        orb_mini = OrbWidget(size=32)
        title_bar.addWidget(orb_mini)
        self._mini_orb = orb_mini

        title = QLabel("香子  Kaoruko")
        title.setFont(QFont("Yu Gothic UI", 13, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {getattr(p,'text_primary','#F0EEFF')}; background: transparent;")
        title_bar.addWidget(title)

        title_bar.addStretch()

        # Collapse button
        btn_collapse = QPushButton("⌃")
        btn_collapse.setFixedSize(28, 28)
        btn_collapse.setToolTip("Collapse to orb")
        btn_collapse.clicked.connect(self.collapsed.emit)
        btn_collapse.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {getattr(p,'text_muted','#6B6280')};
                border: 1px solid {getattr(p,'border','rgba(155,92,255,0.25)')};
                border-radius: 6px;
                font-size: 12px;
            }}
            QPushButton:hover {{ color: {getattr(p,'text_primary','#F0EEFF')}; }}
        """)
        title_bar.addWidget(btn_collapse)

        # Settings button
        btn_settings = QPushButton("⚙")
        btn_settings.setFixedSize(28, 28)
        btn_settings.setToolTip("Settings")
        btn_settings.setStyleSheet(btn_collapse.styleSheet())
        btn_settings.clicked.connect(self._open_settings)
        title_bar.addWidget(btn_settings)

        root_layout.addLayout(title_bar)

        # Divider
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        root_layout.addWidget(div)

        # ── Waveform ─────────────────────────────────────────────────────────
        self._waveform = WaveformWidget()
        self._waveform.setFixedHeight(36)
        self._waveform.start_idle()
        root_layout.addWidget(self._waveform)

        # ── Transcript ────────────────────────────────────────────────────────
        self._transcript = TranscriptWidget(palette=p)
        self._transcript.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root_layout.addWidget(self._transcript, stretch=3)

        # ── History sidebar ───────────────────────────────────────────────────
        self._history = HistoryWidget(palette=p)
        self._history.setFixedHeight(120)
        root_layout.addWidget(self._history, stretch=1)

        # ── Status bar ────────────────────────────────────────────────────────
        self._status = StatusBar(palette=p)
        self._status.setFixedHeight(28)
        root_layout.addWidget(self._status)

    def set_state(self, state: str) -> None:
        self._mini_orb.set_state(state)
        self._status.set_state(state)
        if state in ("listening", "speaking"):
            self._waveform.set_active(True)
        else:
            self._waveform.set_active(False)
            self._waveform.start_idle()

    def add_transcript(self, role: str, text: str) -> None:
        self._transcript.add_entry(role, text)
        if role == "user":
            self._history.add_item(text)

    def _open_settings(self) -> None:
        from kaoruko.ui.widgets.settings_panel import SettingsPanel
        dlg = SettingsPanel(config=self._config, palette=self._palette, parent=self)
        dlg.exec()

    def _apply_style(self) -> None:
        p = self._palette
        bg   = getattr(p, "bg_base",    "#0A0A14")
        surf = getattr(p, "bg_surface", "rgba(20,18,40,0.85)")
        brd  = getattr(p, "border",     "rgba(155,92,255,0.25)")
        self.setStyleSheet(f"""
            QWidget#KaorukoDashboard {{
                background: {surf};
                border: 1px solid {brd};
                border-radius: 16px;
            }}
            QFrame[frameShape="4"] {{ color: {brd}; }}
        """)

    # Drag support
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_start and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_start)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_start = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._root.setGeometry(0, 0, self.width(), self.height())


# ── KaorukoApp master class ───────────────────────────────────────────────────

class KaorukoApp:
    """
    Master application shell.
    Manages the orb, dashboard, system tray, and event routing.
    """

    def __init__(
        self,
        assistant: "KaorukoAssistant",
        config: "KaorukoConfig",
    ) -> None:
        self._assistant = assistant
        self._config = config

        theme = ThemeName(config.ui.theme.value)
        self._palette = get_palette(theme)

        self._orb: Optional[OrbWindow] = None
        self._dashboard: Optional[DashboardWindow] = None
        self._tray: Optional[QSystemTrayIcon] = None
        self._bridge: Optional[AsyncBridgeThread] = None
        self._dashboard_visible = False

    def launch(self) -> None:
        """Build all UI components, start async bridge, show orb."""
        # Apply global stylesheet
        app = QApplication.instance()
        app.setStyleSheet(build_global_stylesheet(self._palette))

        # Build windows
        self._orb = OrbWindow(palette=self._palette, config=self._config)
        self._orb.expand_requested.connect(self._toggle_dashboard)

        self._dashboard = DashboardWindow(palette=self._palette, config=self._config)
        self._dashboard.collapsed.connect(self._collapse_dashboard)

        # System tray
        self._setup_tray()

        # Show orb
        self._orb.show()
        fade_in(self._orb, 400)

        # Start async bridge (runs assistant startup)
        self._bridge = AsyncBridgeThread(assistant=self._assistant)
        self._bridge.start()

        # Subscribe to events (using QTimer.singleShot to wait for loop)
        QTimer.singleShot(500, self._subscribe_to_events)

        log.info("ui_launched")

    # ── Event subscriptions ───────────────────────────────────────────────────

    def _subscribe_to_events(self) -> None:
        """Wire event bus to UI updates. Must run after async loop starts."""
        if not self._bridge or not self._bridge.get_loop():
            QTimer.singleShot(300, self._subscribe_to_events)
            return

        bus = self._assistant.bus

        # Use thread-safe Qt signal bridge for cross-thread UI updates
        bridge = _EventBridge(self._orb, self._dashboard)

        bus.subscribe(KaorukoEvent.UI_ORB_STATE_CHANGED, bridge.on_orb_state)
        bus.subscribe(KaorukoEvent.UI_UPDATE_TRANSCRIPT,  bridge.on_transcript)
        bus.subscribe(KaorukoEvent.UI_UPDATE_HISTORY,     bridge.on_history)
        bus.subscribe(KaorukoEvent.ASSISTANT_READY,       bridge.on_ready)

        self._bridge_obj = bridge  # Keep reference alive
        log.info("ui_events_subscribed")

    # ── Dashboard toggle ──────────────────────────────────────────────────────

    def _toggle_dashboard(self) -> None:
        if self._dashboard_visible:
            self._collapse_dashboard()
        else:
            self._expand_dashboard()

    def _expand_dashboard(self) -> None:
        if not self._dashboard:
            return
        # Position dashboard near orb
        orb_pos = self._orb.pos()
        screen = QApplication.primaryScreen().geometry()
        dw, dh = self._dashboard.width(), self._dashboard.height()

        # Prefer positioning to the left of the orb if at right edge
        x = orb_pos.x() - dw - 10
        if x < 0:
            x = orb_pos.x() + self._orb.width() + 10
        y = max(10, orb_pos.y() - dh + self._orb.height())

        self._dashboard.move(x, y)
        self._dashboard.show()
        fade_in(self._dashboard, 300)
        self._dashboard_visible = True

    def _collapse_dashboard(self) -> None:
        if not self._dashboard:
            return
        self._dashboard.hide()
        self._dashboard_visible = False

    # ── System tray ───────────────────────────────────────────────────────────

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        # Create a simple colored icon
        pixmap = QPixmap(22, 22)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        p = self._palette
        painter.setBrush(QColor(getattr(p, "accent_primary", "#9B5CFF")))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(2, 2, 18, 18)
        painter.end()

        self._tray = QSystemTrayIcon(QIcon(pixmap))
        self._tray.setToolTip("Kaoruko — AI Assistant")

        menu = QMenu()
        menu.addAction("Show Dashboard", self._expand_dashboard)
        menu.addAction("Settings", self._open_settings)
        menu.addSeparator()
        menu.addAction("Quit Kaoruko", QApplication.quit)
        self._tray.setContextMenu(menu)
        self._tray.show()

    def _open_settings(self) -> None:
        from kaoruko.ui.widgets.settings_panel import SettingsPanel
        dlg = SettingsPanel(config=self._config, palette=self._palette)
        dlg.exec()


# ── Thread-safe event bridge ──────────────────────────────────────────────────

from PyQt6.QtCore import QObject
class _EventBridge(QObject):
    """
    Routes async event bus events to Qt UI updates safely across threads.
    Uses QTimer.singleShot to marshal calls to the Qt main thread.
    """

    def __init__(self, orb: OrbWindow, dashboard: DashboardWindow) -> None:
        try:
            from PyQt6.QtCore import QObject
            super().__init__()
        except Exception:
            pass
        self._orb = orb
        self._dashboard = dashboard

    async def on_orb_state(self, event) -> None:
        state = event.data.get("state", "idle")
        QTimer.singleShot(0, lambda: self._set_state(state))

    async def on_transcript(self, event) -> None:
        role = event.data.get("role", "assistant")
        text = event.data.get("text", "")
        QTimer.singleShot(0, lambda: self._add_transcript(role, text))

    async def on_history(self, event) -> None:
        pass  # History is updated via transcript

    async def on_ready(self, event) -> None:
        QTimer.singleShot(0, lambda: self._set_state("idle"))

    def _set_state(self, state: str) -> None:
        if self._orb:
            self._orb.set_state(state)
        if self._dashboard:
            self._dashboard.set_state(state)

    def _add_transcript(self, role: str, text: str) -> None:
        if self._dashboard:
            self._dashboard.add_transcript(role, text)
