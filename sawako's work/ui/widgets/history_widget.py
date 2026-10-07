"""
kaoruko/ui/widgets/history_widget.py

Scrollable command history sidebar.
Shows executed commands with intent label, status icon, and timestamp.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QVBoxLayout, QWidget, QSizePolicy,
)


@dataclass
class HistoryItem:
    text: str
    intent: str = ""
    success: bool = True
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))


class HistoryWidget(QWidget):
    """Command history panel with click-to-repeat support."""

    item_clicked = pyqtSignal(str)   # emits command text

    def __init__(self, palette: object, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._items: list[HistoryItem] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Header
        hdr = QLabel("History")
        hdr.setObjectName("label_muted")
        font = QFont("Segoe UI", 10)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        hdr.setFont(font)
        layout.addWidget(hdr)

        # List
        self._list = QListWidget()
        self._list.setObjectName("history_list")
        self._list.setSpacing(2)
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list)

    def add_item(self, text: str, intent: str = "", success: bool = True) -> None:
        item_data = HistoryItem(text=text, intent=intent, success=success)
        self._items.append(item_data)

        p = self._palette
        icon = "✓" if success else "✗"
        color = getattr(p, "success", "#4DFFB4") if success else getattr(p, "error", "#FF5C8D")

        display = f'{icon}  {text[:50]}{"…" if len(text)>50 else ""}'
        list_item = QListWidgetItem(display)
        list_item.setData(Qt.ItemDataRole.UserRole, text)
        list_item.setForeground(QColor(color if not success else getattr(p, "text_secondary", "#B8AEDD")))
        list_item.setToolTip(f"{intent} · {item_data.timestamp}")

        self._list.insertItem(0, list_item)

        # Cap at 200 items
        if self._list.count() > 200:
            self._list.takeItem(self._list.count() - 1)

    def clear(self) -> None:
        self._list.clear()
        self._items.clear()

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        text = item.data(Qt.ItemDataRole.UserRole)
        if text:
            self.item_clicked.emit(text)
