"""
kaoruko/execution/handlers/assistant_handler.py

Handles Kaoruko's own meta-commands:
  META_HELP     — list capabilities
  META_STATUS   — show system status
  META_SETTINGS — open settings panel
  META_STOP     — stop listening
  MEM_REMIND    — set a reminder
  MEM_NOTE      — save a note
  MEM_RECALL    — recall a memory
"""
from __future__ import annotations

import asyncio
import platform
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from kaoruko.infrastructure.logging.logger import get_logger

if TYPE_CHECKING:
    from kaoruko.infrastructure.config.schema import KaorukoConfig

log = get_logger("execution.assistant_handler")


class AssistantHandler:
    """Handles all META_* and MEM_* intent actions."""

    def __init__(self, config: "KaorukoConfig", bus=None, memory=None) -> None:
        self.config = config
        self.bus = bus
        self.memory = memory

    # ── META handlers ─────────────────────────────────────────────────────────

    def show_help(self, **kwargs) -> str:
        return (
            "I can open and close apps, search the web, control volume and brightness, "
            "manage files, control system power, and have intelligent conversations. "
            "Just speak naturally — say 'Open Chrome', 'Search for Python tutorials', "
            "'Set volume to 60', or ask me anything~"
        )

    def show_status(self, **kwargs) -> str:
        import psutil
        try:
            cpu = psutil.cpu_percent(interval=0.3)
            ram = psutil.virtual_memory()
            ram_used = ram.used // (1024 ** 2)
            ram_total = ram.total // (1024 ** 2)
            status = (
                f"All systems operational~ "
                f"CPU at {cpu:.0f}%, "
                f"RAM {ram_used}MB of {ram_total}MB used. "
                f"Running on {platform.node()}."
            )
        except ImportError:
            status = "All systems operational~ I am ready to assist you."
        log.info("status_shown")
        return status

    def open_settings(self, **kwargs) -> str:
        if self.bus:
            from kaoruko.core.event_bus import KaorukoEvent
            asyncio.create_task(
                self.bus.publish(
                    KaorukoEvent.UI_MODE_CHANGED,
                    data={"action": "open_settings"},
                    source="assistant_handler",
                )
            )
        return "Opening settings~"

    def stop_listening(self, **kwargs) -> str:
        log.info("stop_listening_requested")
        return "Understood. I will stay quiet~ Say my name when you need me."

    # ── Memory / Reminder handlers ────────────────────────────────────────────

    async def set_reminder(
        self,
        content: str = "",
        time_str: str = "",
        **kwargs,
    ) -> str:
        if not content:
            return "What would you like me to remind you about?"

        if self.memory:
            await self.memory.remember(
                key=f"reminder_{datetime.now().timestamp()}",
                value={"content": content, "time": time_str},
                memory_type="reminder",
                tags=["reminder"],
            )

        time_note = f" at {time_str}" if time_str else ""
        return f"I'll remind you{time_note}: {content}~"

    async def save_note(self, content: str = "", **kwargs) -> str:
        if not content:
            return "What would you like me to note?"
        if self.memory:
            await self.memory.remember(
                key=f"note_{datetime.now().timestamp()}",
                value=content,
                memory_type="note",
                tags=["note"],
            )
        return f"Noted~ I'll remember: {content}"

    async def recall_memory(self, subject: str = "", **kwargs) -> str:
        if not subject:
            return "What would you like me to recall?"
        if self.memory:
            value = await self.memory.recall(subject)
            if value:
                return f"I recall: {value}"
        return f"I don't have anything stored about '{subject}'~"
