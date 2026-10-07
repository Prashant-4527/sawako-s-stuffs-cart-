"""
kaoruko/ui/widgets/settings_panel.py

Settings panel — tabbed configuration UI.
Tabs: General · Voice · AI · Appearance · Privacy · About
"""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSlider, QTabWidget,
    QVBoxLayout, QWidget,
)

if TYPE_CHECKING:
    from kaoruko.infrastructure.config.schema import KaorukoConfig


class SettingsPanel(QDialog):
    """
    Full settings dialog.
    Changes emit config_changed signal; caller persists them.
    """

    config_changed = pyqtSignal(str, object)   # (key_path, new_value)

    def __init__(
        self,
        config: "KaorukoConfig",
        palette: object,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Dialog)
        self._config = config
        self._palette = palette
        self.setWindowTitle("Kaoruko — Settings")
        self.setMinimumSize(520, 480)
        self.setModal(True)
        self._setup_ui()
        self._apply_style()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Title
        title = QLabel("⚙  Settings")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        root.addWidget(title)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_general_tab(), "General")
        self._tabs.addTab(self._build_voice_tab(), "Voice")
        self._tabs.addTab(self._build_ai_tab(), "AI")
        self._tabs.addTab(self._build_appearance_tab(), "Appearance")
        self._tabs.addTab(self._build_privacy_tab(), "Privacy")
        self._tabs.addTab(self._build_about_tab(), "About")
        root.addWidget(self._tabs)

        # Buttons
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel |
            QDialogButtonBox.StandardButton.Apply
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        btns.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self._apply)
        root.addWidget(btns)

    # ── Tab builders ──────────────────────────────────────────────────────────

    def _build_general_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.setSpacing(10)

        self._cb_always_on_top = QCheckBox()
        self._cb_always_on_top.setChecked(self._config.ui.always_on_top)
        f.addRow("Always on top:", self._cb_always_on_top)

        self._cb_taskbar = QCheckBox()
        self._cb_taskbar.setChecked(self._config.ui.show_in_taskbar)
        f.addRow("Show in taskbar:", self._cb_taskbar)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(30, 100)
        self._opacity_slider.setValue(int(self._config.ui.opacity * 100))
        f.addRow("Opacity:", self._opacity_slider)

        self._cmb_position = QComboBox()
        positions = ["bottom_right", "bottom_left", "top_right", "top_left", "center"]
        self._cmb_position.addItems(positions)
        self._cmb_position.setCurrentText(self._config.ui.start_position.value)
        f.addRow("Start position:", self._cmb_position)

        return w

    def _build_voice_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # Wake word group
        ww_grp = QGroupBox("Wake Word")
        ww_form = QFormLayout(ww_grp)

        self._cb_wake_enabled = QCheckBox()
        self._cb_wake_enabled.setChecked(self._config.voice.wake_word.enabled)
        ww_form.addRow("Enabled:", self._cb_wake_enabled)

        self._wake_sensitivity = QSlider(Qt.Orientation.Horizontal)
        self._wake_sensitivity.setRange(30, 95)
        self._wake_sensitivity.setValue(int(self._config.voice.wake_word.sensitivity * 100))
        ww_form.addRow("Sensitivity:", self._wake_sensitivity)

        layout.addWidget(ww_grp)

        # STT group
        stt_grp = QGroupBox("Speech Recognition")
        stt_form = QFormLayout(stt_grp)

        self._cmb_stt_model = QComboBox()
        self._cmb_stt_model.addItems(["tiny", "base", "small", "medium"])
        self._cmb_stt_model.setCurrentText(self._config.voice.stt.model_size.value)
        stt_form.addRow("Whisper model:", self._cmb_stt_model)

        layout.addWidget(stt_grp)

        # TTS group
        tts_grp = QGroupBox("Voice Output")
        tts_form = QFormLayout(tts_grp)

        self._cmb_tts_voice = QComboBox()
        voices = [
            "ja-JP-NanamiNeural", "ja-JP-AoiNeural",
            "en-US-AriaNeural",   "en-US-JennyNeural",
            "en-US-SaraNeural",
        ]
        self._cmb_tts_voice.addItems(voices)
        self._cmb_tts_voice.setCurrentText(self._config.voice.tts.voice)
        tts_form.addRow("Voice:", self._cmb_tts_voice)

        layout.addWidget(tts_grp)
        layout.addStretch()
        return w

    def _build_ai_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.setSpacing(10)

        self._cmb_ai_primary = QComboBox()
        self._cmb_ai_primary.addItems(["claude", "openai", "ollama"])
        self._cmb_ai_primary.setCurrentText(self._config.ai.primary.value)
        f.addRow("Primary AI:", self._cmb_ai_primary)

        self._le_api_key = QLineEdit()
        self._le_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._le_api_key.setPlaceholderText("Enter Anthropic API key…")
        f.addRow("API Key:", self._le_api_key)

        save_key_btn = QPushButton("Save Key")
        save_key_btn.setObjectName("btn_primary")
        save_key_btn.clicked.connect(self._save_api_key)
        f.addRow("", save_key_btn)

        self._cmb_offline_model = QComboBox()
        self._cmb_offline_model.addItems(["llama3.2:3b", "llama3.2:1b", "phi3:mini", "mistral:7b"])
        self._cmb_offline_model.setCurrentText(self._config.ai.offline_model)
        f.addRow("Offline model (Ollama):", self._cmb_offline_model)

        return w

    def _build_appearance_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)

        self._cmb_theme = QComboBox()
        self._cmb_theme.addItems(["cyber_purple", "midnight", "sakura"])
        self._cmb_theme.setCurrentText(self._config.ui.theme.value)
        f.addRow("Theme:", self._cmb_theme)

        self._cmb_mode = QComboBox()
        self._cmb_mode.addItems(["orb", "dashboard", "minimal", "gaming"])
        self._cmb_mode.setCurrentText(self._config.ui.default_mode.value)
        f.addRow("Default mode:", self._cmb_mode)

        self._orb_size_slider = QSlider(Qt.Orientation.Horizontal)
        self._orb_size_slider.setRange(40, 150)
        self._orb_size_slider.setValue(self._config.ui.orb_size)
        f.addRow("Orb size:", self._orb_size_slider)

        return w

    def _build_privacy_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)

        self._cb_transcripts = QCheckBox()
        self._cb_transcripts.setChecked(self._config.privacy.save_transcripts)
        f.addRow("Save transcripts:", self._cb_transcripts)

        self._cb_analytics = QCheckBox()
        self._cb_analytics.setChecked(self._config.privacy.share_analytics)
        f.addRow("Share anonymous analytics:", self._cb_analytics)

        self._cb_confirm = QCheckBox()
        self._cb_confirm.setChecked(self._config.security.confirm_destructive)
        f.addRow("Confirm destructive actions:", self._cb_confirm)

        return w

    def _build_about_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        for line, size, bold in [
            ("香子  Kaoruko",      20, True),
            ("AI Desktop Voice Assistant", 12, False),
            ("Version 1.0.0",              11, False),
            ("",                            10, False),
            ("Built with Python + PyQt6",   10, False),
            ("Voice: faster-whisper + Edge TTS", 10, False),
            ("AI: Claude / Ollama",         10, False),
        ]:
            lbl = QLabel(line)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            font = QFont("Segoe UI", size)
            if bold:
                font.setWeight(QFont.Weight.Bold)
            lbl.setFont(font)
            layout.addWidget(lbl)

        layout.addStretch()
        return w

    # ── Actions ───────────────────────────────────────────────────────────────

    def _apply(self) -> None:
        """Emit config changes without closing the dialog."""
        self.config_changed.emit("ui.always_on_top", self._cb_always_on_top.isChecked())
        self.config_changed.emit("ui.opacity", self._opacity_slider.value() / 100.0)
        self.config_changed.emit("ui.theme", self._cmb_theme.currentText())
        self.config_changed.emit("voice.tts.voice", self._cmb_tts_voice.currentText())
        self.config_changed.emit("voice.wake_word.enabled", self._cb_wake_enabled.isChecked())
        self.config_changed.emit("voice.stt.model_size", self._cmb_stt_model.currentText())
        self.config_changed.emit("ai.primary", self._cmb_ai_primary.currentText())
        self.config_changed.emit("privacy.save_transcripts", self._cb_transcripts.isChecked())
        self.config_changed.emit("security.confirm_destructive", self._cb_confirm.isChecked())

    def _save_api_key(self) -> None:
        key = self._le_api_key.text().strip()
        if key:
            from pathlib import Path
            from kaoruko.security.secrets_manager import SecretsManager
            mgr = SecretsManager(Path.cwd())
            mgr.store("anthropic_api_key", key)
            self._le_api_key.clear()
            self._le_api_key.setPlaceholderText("✓ API key saved securely")

    def _apply_style(self) -> None:
        p = self._palette
        bg    = getattr(p, "bg_base",    "#0A0A14")
        surf  = getattr(p, "bg_surface", "rgba(20,18,40,0.85)")
        text  = getattr(p, "text_primary", "#F0EEFF")
        muted = getattr(p, "text_muted",   "#6B6280")
        border = getattr(p, "border",      "rgba(155,92,255,0.25)")
        self.setStyleSheet(f"""
            QDialog {{ background: {bg}; color: {text}; border-radius: 12px; }}
            QTabWidget::pane {{ background: {surf}; border: 1px solid {border}; border-radius: 8px; }}
            QTabBar::tab {{ background: transparent; color: {muted};
                            padding: 7px 18px; font-size: 12px; }}
            QTabBar::tab:selected {{ color: {text}; border-bottom: 2px solid {getattr(p,'accent_primary','#9B5CFF')}; }}
            QGroupBox {{ color: {muted}; border: 1px solid {border};
                         border-radius: 8px; margin-top: 8px; padding: 12px 8px 8px 8px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; color: {text}; font-size:11px; }}
        """)
