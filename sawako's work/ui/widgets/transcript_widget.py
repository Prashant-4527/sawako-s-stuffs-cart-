"""
kaoruko/ui/widgets/transcript_widget.py

Live conversation transcript widget.
Renders user and assistant turns with distinct styling,
smooth scroll animation, and fade-in entry effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
    QTextEdit, QVBoxLayout, QWidget,
)


@dataclass
class TranscriptEntry:
    role: str           # "user" | "assistant"
    text: str
    timestamp: str = ""
    language: str = "en"

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().strftime("%H:%M")


class TranscriptWidget(QWidget):
    """
    Scrollable conversation transcript.
    User messages appear right-aligned; assistant messages left-aligned.
    """

    MAX_ENTRIES = 100

    def __init__(self, palette: object, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._entries: list[TranscriptEntry] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header label
        self._header = QLabel("Conversation")
        self._header.setObjectName("label_muted")
        self._header.setFixedHeight(24)
        font = QFont("Segoe UI", 10)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        self._header.setFont(font)
        layout.addWidget(self._header)

        # Scroll area containing all message bubbles
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setFrameShape(self._scroll.Shape.NoFrame)

        self._container = QWidget()
        self._container.setObjectName("TranscriptWidget")
        self._messages_layout = QVBoxLayout(self._container)
        self._messages_layout.setContentsMargins(8, 8, 8, 8)
        self._messages_layout.setSpacing(10)
        self._messages_layout.addStretch()

        self._scroll.setWidget(self._container)
        layout.addWidget(self._scroll)

    @pyqtSlot(str, str)
    def add_entry(self, role: str, text: str, language: str = "en") -> None:
        """Add a new transcript entry."""
        entry = TranscriptEntry(role=role, text=text, language=language)
        self._entries.append(entry)

        if len(self._entries) > self.MAX_ENTRIES:
            self._prune_oldest()

        bubble = self._make_bubble(entry)
        # Insert before the trailing stretch
        self._messages_layout.insertWidget(
            self._messages_layout.count() - 1, bubble
        )

        # Scroll to bottom after render
        QTimer.singleShot(50, self._scroll_to_bottom)

    def clear(self) -> None:
        """Clear all entries."""
        self._entries.clear()
        while self._messages_layout.count() > 1:
            item = self._messages_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def _make_bubble(self, entry: TranscriptEntry) -> QWidget:
        """Build a message bubble widget for an entry."""
        is_user = (entry.role == "user")

        outer = QWidget()
        outer_layout = QHBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        bubble = QLabel()
        bubble.setWordWrap(True)
        bubble.setTextFormat(Qt.TextFormat.RichText)
        bubble.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        bubble.setMaximumWidth(320)

        p = self._palette
        if is_user:
            bg    = getattr(p, "accent_dim", "rgba(155,92,255,0.18)")
            color = getattr(p, "accent_secondary", "#5CE0FF")
            align = "right"
            label = "You"
        else:
            bg    = "rgba(255,255,255,0.05)"
            color = getattr(p, "text_primary", "#F0EEFF")
            align = "left"
            label = "Kaoruko"

        bubble.setStyleSheet(f"""
            QLabel {{
                background: {bg};
                color: {color};
                border-radius: 10px;
                padding: 8px 12px;
                font-size: 13px;
                line-height: 1.5;
            }}
        """)

        # Role + timestamp header
        header_html = (
            f'<span style="font-size:10px;opacity:0.55;">'
            f'{label} · {entry.timestamp}</span><br/>'
        )
        bubble.setText(header_html + entry.text)

        if is_user:
            outer_layout.addStretch()
            outer_layout.addWidget(bubble)
        else:
            outer_layout.addWidget(bubble)
            outer_layout.addStretch()

        return outer

    def _prune_oldest(self) -> None:
        """Remove the oldest message bubble."""
        if self._messages_layout.count() > 1:
            item = self._messages_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        if self._entries:
            self._entries.pop(0)

    def _scroll_to_bottom(self) -> None:
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
