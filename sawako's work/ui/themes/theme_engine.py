"""
kaoruko/ui/themes/theme_engine.py

Theme engine — defines all visual tokens and generates Qt stylesheets.
Supports: CYBER_PURPLE (default), MIDNIGHT, SAKURA.

Design language:
  - Glassmorphism panels with frosted blur
  - Neon glow accents on interactive elements
  - Smooth gradient backgrounds
  - Semi-transparent surfaces with subtle borders
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class ThemeName(str, Enum):
    CYBER_PURPLE = "cyber_purple"
    MIDNIGHT     = "midnight"
    SAKURA       = "sakura"


@dataclass(frozen=True)
class ColorPalette:
    # Core backgrounds
    bg_deep:          str    # deepest background
    bg_base:          str    # main window background
    bg_surface:       str    # card / panel surface
    bg_surface_hover: str    # surface on hover
    bg_overlay:       str    # modal overlay tint

    # Accent colors
    accent_primary:   str    # main accent (orb, buttons)
    accent_secondary: str    # secondary accent
    accent_glow:      str    # glow / bloom effect
    accent_dim:       str    # muted accent

    # Text
    text_primary:     str
    text_secondary:   str
    text_muted:       str
    text_on_accent:   str

    # State colors
    success:          str
    warning:          str
    error:            str
    info:             str

    # Borders & dividers
    border:           str
    border_focus:     str
    divider:          str

    # Waveform / orb
    wave_primary:     str
    wave_secondary:   str
    orb_idle:         str
    orb_listening:    str
    orb_processing:   str
    orb_speaking:     str
    orb_error:        str


# ── Cyber Purple Theme ────────────────────────────────────────────────────────
CYBER_PURPLE = ColorPalette(
    bg_deep          = "#050508",
    bg_base          = "#0A0A14",
    bg_surface       = "rgba(20, 18, 40, 0.85)",
    bg_surface_hover = "rgba(35, 30, 65, 0.90)",
    bg_overlay       = "rgba(5, 5, 12, 0.70)",

    accent_primary   = "#9B5CFF",
    accent_secondary = "#5CE0FF",
    accent_glow      = "rgba(155, 92, 255, 0.45)",
    accent_dim       = "rgba(155, 92, 255, 0.20)",

    text_primary     = "#F0EEFF",
    text_secondary   = "#B8AEDD",
    text_muted       = "#6B6280",
    text_on_accent   = "#FFFFFF",

    success          = "#4DFFB4",
    warning          = "#FFD166",
    error            = "#FF5C8D",
    info             = "#5CE0FF",

    border           = "rgba(155, 92, 255, 0.25)",
    border_focus     = "rgba(155, 92, 255, 0.80)",
    divider          = "rgba(255, 255, 255, 0.06)",

    wave_primary     = "#9B5CFF",
    wave_secondary   = "#5CE0FF",
    orb_idle         = "#9B5CFF",
    orb_listening    = "#5CE0FF",
    orb_processing   = "#FFD166",
    orb_speaking     = "#4DFFB4",
    orb_error        = "#FF5C8D",
)

# ── Midnight Theme ────────────────────────────────────────────────────────────
MIDNIGHT = ColorPalette(
    bg_deep          = "#020204",
    bg_base          = "#080B12",
    bg_surface       = "rgba(12, 16, 28, 0.88)",
    bg_surface_hover = "rgba(20, 26, 44, 0.92)",
    bg_overlay       = "rgba(2, 2, 8, 0.72)",

    accent_primary   = "#3D8EFF",
    accent_secondary = "#00F5C4",
    accent_glow      = "rgba(61, 142, 255, 0.40)",
    accent_dim       = "rgba(61, 142, 255, 0.18)",

    text_primary     = "#E8F0FF",
    text_secondary   = "#8AABDD",
    text_muted       = "#4A5878",
    text_on_accent   = "#FFFFFF",

    success          = "#00F5C4",
    warning          = "#FFB347",
    error            = "#FF4D6D",
    info             = "#3D8EFF",

    border           = "rgba(61, 142, 255, 0.22)",
    border_focus     = "rgba(61, 142, 255, 0.75)",
    divider          = "rgba(255, 255, 255, 0.05)",

    wave_primary     = "#3D8EFF",
    wave_secondary   = "#00F5C4",
    orb_idle         = "#3D8EFF",
    orb_listening    = "#00F5C4",
    orb_processing   = "#FFB347",
    orb_speaking     = "#00F5C4",
    orb_error        = "#FF4D6D",
)

# ── Sakura Theme ──────────────────────────────────────────────────────────────
SAKURA = ColorPalette(
    bg_deep          = "#060408",
    bg_base          = "#100C14",
    bg_surface       = "rgba(28, 18, 34, 0.88)",
    bg_surface_hover = "rgba(45, 28, 52, 0.92)",
    bg_overlay       = "rgba(6, 4, 10, 0.72)",

    accent_primary   = "#FF6B9D",
    accent_secondary = "#FFB5C8",
    accent_glow      = "rgba(255, 107, 157, 0.40)",
    accent_dim       = "rgba(255, 107, 157, 0.18)",

    text_primary     = "#FFF0F5",
    text_secondary   = "#DDAABB",
    text_muted       = "#7A5870",
    text_on_accent   = "#FFFFFF",

    success          = "#88FFCC",
    warning          = "#FFD98C",
    error            = "#FF5C7A",
    info             = "#A5B4FF",

    border           = "rgba(255, 107, 157, 0.22)",
    border_focus     = "rgba(255, 107, 157, 0.75)",
    divider          = "rgba(255, 255, 255, 0.05)",

    wave_primary     = "#FF6B9D",
    wave_secondary   = "#FFB5C8",
    orb_idle         = "#FF6B9D",
    orb_listening    = "#A5B4FF",
    orb_processing   = "#FFD98C",
    orb_speaking     = "#88FFCC",
    orb_error        = "#FF5C7A",
)

_THEMES: dict[ThemeName, ColorPalette] = {
    ThemeName.CYBER_PURPLE: CYBER_PURPLE,
    ThemeName.MIDNIGHT:     MIDNIGHT,
    ThemeName.SAKURA:       SAKURA,
}


def get_palette(theme: ThemeName) -> ColorPalette:
    return _THEMES.get(theme, CYBER_PURPLE)


def build_global_stylesheet(palette: ColorPalette) -> str:
    """Generate a full Qt stylesheet from a color palette."""
    return f"""
/* ═══════════════════════════════════════════════
   KAORUKO  —  Global Qt Stylesheet
   ═══════════════════════════════════════════════ */

QWidget {{
    background: transparent;
    color: {palette.text_primary};
    font-family: "Segoe UI", "Yu Gothic UI", "Meiryo UI", sans-serif;
    font-size: 13px;
    border: none;
    outline: none;
}}

/* ── Main window background ──────────────────── */
#KaorukoMainWindow, #KaorukoDashboard {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 {palette.bg_deep},
        stop:0.5 {palette.bg_base},
        stop:1 {palette.bg_deep}
    );
    border-radius: 16px;
}}

/* ── Glass panels ────────────────────────────── */
#GlassPanel, QFrame#panel {{
    background: {palette.bg_surface};
    border: 1px solid {palette.border};
    border-radius: 12px;
}}

#GlassPanel:hover {{
    background: {palette.bg_surface_hover};
    border: 1px solid {palette.border_focus};
}}

/* ── Transcript area ─────────────────────────── */
#TranscriptWidget {{
    background: {palette.bg_surface};
    border: 1px solid {palette.border};
    border-radius: 10px;
    padding: 8px;
}}

QTextEdit#transcript_view {{
    background: transparent;
    color: {palette.text_primary};
    font-size: 13px;
    line-height: 1.6;
    border: none;
    padding: 4px 8px;
}}

/* ── Command history ─────────────────────────── */
QListWidget#history_list {{
    background: transparent;
    color: {palette.text_secondary};
    font-size: 12px;
    border: none;
    padding: 4px;
}}
QListWidget#history_list::item {{
    padding: 5px 8px;
    border-radius: 6px;
}}
QListWidget#history_list::item:hover {{
    background: {palette.accent_dim};
    color: {palette.text_primary};
}}
QListWidget#history_list::item:selected {{
    background: {palette.accent_dim};
    color: {palette.accent_primary};
}}

/* ── Buttons ─────────────────────────────────── */
QPushButton {{
    background: {palette.accent_dim};
    color: {palette.accent_primary};
    border: 1px solid {palette.border};
    border-radius: 8px;
    padding: 7px 16px;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {palette.accent_glow};
    border: 1px solid {palette.accent_primary};
    color: {palette.text_on_accent};
}}
QPushButton:pressed {{
    background: {palette.accent_primary};
    color: {palette.text_on_accent};
}}
QPushButton:disabled {{
    color: {palette.text_muted};
    border-color: {palette.divider};
    background: transparent;
}}

/* ── Accent / primary buttons ────────────────── */
QPushButton#btn_primary {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {palette.accent_primary}, stop:1 {palette.accent_secondary});
    color: {palette.text_on_accent};
    border: none;
    font-weight: 700;
}}

/* ── Sliders ─────────────────────────────────── */
QSlider::groove:horizontal {{
    height: 4px;
    background: {palette.divider};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 14px;
    height: 14px;
    background: {palette.accent_primary};
    border-radius: 7px;
    margin: -5px 0;
}}
QSlider::handle:horizontal:hover {{
    background: {palette.accent_secondary};
}}
QSlider::sub-page:horizontal {{
    background: {palette.accent_primary};
    border-radius: 2px;
}}

/* ── ScrollBars ──────────────────────────────── */
QScrollBar:vertical {{
    width: 6px;
    background: transparent;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {palette.border};
    border-radius: 3px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{
    background: {palette.accent_primary};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

/* ── Labels ──────────────────────────────────── */
QLabel#label_accent {{
    color: {palette.accent_primary};
    font-weight: 600;
}}
QLabel#label_muted {{
    color: {palette.text_muted};
    font-size: 11px;
}}
QLabel#status_dot {{
    color: {palette.success};
    font-size: 10px;
}}

/* ── Tooltips ────────────────────────────────── */
QToolTip {{
    background: {palette.bg_surface};
    color: {palette.text_primary};
    border: 1px solid {palette.border};
    border-radius: 6px;
    padding: 5px 9px;
    font-size: 12px;
}}

/* ── ComboBox ────────────────────────────────── */
QComboBox {{
    background: {palette.bg_surface};
    color: {palette.text_primary};
    border: 1px solid {palette.border};
    border-radius: 7px;
    padding: 5px 10px;
    font-size: 12px;
}}
QComboBox:hover {{ border: 1px solid {palette.border_focus}; }}
QComboBox QAbstractItemView {{
    background: {palette.bg_base};
    color: {palette.text_primary};
    selection-background-color: {palette.accent_dim};
    border: 1px solid {palette.border};
    border-radius: 6px;
}}

/* ── Line edits / inputs ─────────────────────── */
QLineEdit {{
    background: {palette.bg_surface};
    color: {palette.text_primary};
    border: 1px solid {palette.border};
    border-radius: 8px;
    padding: 7px 12px;
    font-size: 13px;
}}
QLineEdit:focus {{
    border: 1px solid {palette.border_focus};
    background: {palette.bg_surface_hover};
}}
QLineEdit::placeholder {{
    color: {palette.text_muted};
}}

/* ── Dividers ────────────────────────────────── */
QFrame[frameShape="4"], QFrame[frameShape="5"] {{
    color: {palette.divider};
    height: 1px;
}}
"""
