"""
kaoruko/execution/handlers/reminder_scheduler.py

Background reminder scheduler.
Polls the reminders table every 30 seconds and fires due reminders
via the event bus → TTS speaks them.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from kaoruko.infrastructure.logging.logger import get_logger

if TYPE_CHECKING:
    from kaoruko.core.event_bus import EventBus
    from kaoruko.memory.long_term import DatabaseManager

log = get_logger("execution.reminder_scheduler")


class ReminderScheduler:
    """
    Async background task that checks for due reminders every 30s.
    Fires them via event bus → assistant speaks the reminder aloud.
    """

    CHECK_INTERVAL = 30  # seconds

    def __init__(self, db: "DatabaseManager", bus: "EventBus") -> None:
        self.db = db
        self.bus = bus
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        log.info("reminder_scheduler_started")

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._check_reminders()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("reminder_poll_error", error=str(e))
            await asyncio.sleep(self.CHECK_INTERVAL)

    async def _check_reminders(self) -> None:
        from sqlalchemy import select, update
        from kaoruko.memory.long_term import ReminderModel
        from kaoruko.core.event_bus import KaorukoEvent

        now = datetime.now(timezone.utc)
        async with self.db.session() as sess:
            result = await sess.execute(
                select(ReminderModel)
                .where(ReminderModel.due_at <= now)
                .where(ReminderModel.notified == False)  # noqa: E712
            )
            due = result.scalars().all()

        for reminder in due:
            log.info("reminder_firing", content=reminder.content[:60])

            # Speak the reminder
            await self.bus.publish(
                KaorukoEvent.UI_SHOW_NOTIFICATION,
                data={
                    "title": "Kaoruko Reminder",
                    "message": reminder.content,
                },
                source="reminder_scheduler",
            )
            # Also trigger TTS
            await self.bus.publish(
                KaorukoEvent.EXEC_ACTION_SUCCESS,
                data={
                    "response_text": f"Reminder~ {reminder.content}",
                },
                source="reminder_scheduler",
            )

            # Mark as notified
            async with self.db.session() as sess:
                await sess.execute(
                    update(ReminderModel)
                    .where(ReminderModel.id == reminder.id)
                    .values(notified=True)
                )
                await sess.commit()
