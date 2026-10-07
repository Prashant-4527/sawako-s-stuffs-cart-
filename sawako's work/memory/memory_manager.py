"""
kaoruko/memory/memory_manager.py

Unified memory interface.
Combines short-term (in-memory) and long-term (SQLite) memory.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Optional, TYPE_CHECKING

from kaoruko.infrastructure.logging.logger import get_logger

if TYPE_CHECKING:
    from kaoruko.infrastructure.config.schema import KaorukoConfig
    from kaoruko.memory.long_term import DatabaseManager

log = get_logger("memory.manager")


class ShortTermMemory:
    """
    In-memory circular buffer for recent conversation context.
    Cleared when the assistant restarts.
    """

    def __init__(self, max_turns: int = 20) -> None:
        self._buffer: deque[dict] = deque(maxlen=max_turns)

    def add(self, role: str, content: str, metadata: Optional[dict] = None) -> None:
        self._buffer.append({"role": role, "content": content, **(metadata or {})})

    def get_recent(self, n: int = 5) -> list[dict]:
        items = list(self._buffer)
        return items[-n:] if len(items) > n else items

    def clear(self) -> None:
        self._buffer.clear()

    def to_messages(self) -> list[dict[str, str]]:
        """Format for Claude/OpenAI API messages."""
        return [{"role": m["role"], "content": m["content"]} for m in self._buffer]


class MemoryManager:
    """
    Unified memory interface.

    Short-term: fast, in-process, no persistence
    Long-term:  SQLite, persists across restarts

    Usage:
        await memory.remember("user_name", "Haruki")
        name = await memory.recall("user_name")
        await memory.add_context(role="user", content="Open Chrome")
    """

    def __init__(self, db: "DatabaseManager", config: "KaorukoConfig") -> None:
        self.db = db
        self.config = config
        self.short_term = ShortTermMemory(max_turns=20)
        self._user_profile: dict[str, Any] = {}
        log.info("memory_manager_created")

    async def initialize(self) -> None:
        """Load user profile and preferences from DB."""
        try:
            profile = await self.db.get_preference("user_profile")
            if profile:
                self._user_profile = profile
            log.info("memory_initialized", profile_keys=list(self._user_profile.keys()))
        except Exception as e:
            log.warning("memory_init_warning", error=str(e))

    async def remember(
        self,
        key: str,
        value: Any,
        memory_type: str = "fact",
        tags: Optional[list[str]] = None,
    ) -> None:
        """Store a long-term memory."""
        await self.db.save_memory(
            type=memory_type,
            subject=key,
            content=str(value),
            tags=tags or [],
        )
        log.debug("memory_stored", key=key, type=memory_type)

    async def recall(self, key: str) -> Optional[str]:
        """Retrieve a long-term memory by key."""
        from sqlalchemy import select
        from kaoruko.memory.long_term import MemoryModel
        async with self.db.session() as sess:
            result = await sess.execute(
                select(MemoryModel)
                .where(MemoryModel.subject == key)
                .order_by(MemoryModel.created_at.desc())
                .limit(1)
            )
            row = result.scalar_one_or_none()
            return row.content if row else None

    def add_context(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a turn to short-term memory."""
        self.short_term.add(role=role, content=content, metadata=kwargs)

    def get_context(self, n: int = 5) -> list[dict]:
        """Get recent conversation context for AI prompt injection."""
        return self.short_term.get_recent(n)

    def get_user_profile(self) -> dict[str, Any]:
        return dict(self._user_profile)

    async def update_profile(self, key: str, value: Any) -> None:
        self._user_profile[key] = value
        await self.db.set_preference("user_profile", self._user_profile, category="behavior")

    async def flush(self) -> None:
        """Persist any pending in-memory state before shutdown."""
        await self.db.set_preference("user_profile", self._user_profile, category="behavior")
        log.info("memory_flushed")
