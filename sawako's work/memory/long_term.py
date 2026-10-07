"""
kaoruko/memory/long_term.py

SQLite database manager via SQLAlchemy 2.0 (async).
Owns all schema creation, migration, and CRUD operations.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer,
    JSON, String, Text, ForeignKey, Index, event,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession, AsyncEngine,
    async_sessionmaker, create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.pool import StaticPool

from kaoruko.infrastructure.logging.logger import get_logger

log = get_logger("memory.database")


# ── ORM Base ──────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ── Models ────────────────────────────────────────────────────────────────────

class ConversationModel(Base):
    __tablename__ = "conversations"

    id            = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id    = Column(String(36), nullable=False, index=True)
    role          = Column(String(16), nullable=False)   # "user" | "assistant"
    content       = Column(Text, nullable=False)
    language      = Column(String(8), default="en")
    intent        = Column(String(64), nullable=True)
    confidence    = Column(Float, nullable=True)
    timestamp     = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    meta          = Column("metadata", JSON, nullable=True)

    actions = relationship("ActionModel", back_populates="conversation")


class ActionModel(Base):
    __tablename__ = "actions"

    id              = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=True)
    action_type     = Column(String(64), nullable=False)
    handler         = Column(String(64), nullable=False)
    parameters      = Column(JSON, nullable=False, default=dict)
    status          = Column(String(16), nullable=False)  # success|failed|cancelled
    duration_ms     = Column(Integer, nullable=True)
    error_message   = Column(Text, nullable=True)
    timestamp       = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    conversation = relationship("ConversationModel", back_populates="actions")


class PreferenceModel(Base):
    __tablename__ = "preferences"

    key        = Column(String(128), primary_key=True)
    value      = Column(JSON, nullable=False)
    category   = Column(String(32), nullable=True)   # voice|ui|behavior|privacy
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class MemoryModel(Base):
    __tablename__ = "memories"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    type         = Column(String(32), nullable=False)     # fact|preference|routine|note
    subject      = Column(String(256), nullable=False)
    content      = Column(Text, nullable=False)
    confidence   = Column(Float, default=1.0)
    source       = Column(String(36), nullable=True)      # conversation_id
    tags         = Column(JSON, nullable=True)
    last_accessed = Column(DateTime, nullable=True)
    created_at   = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at   = Column(DateTime, nullable=True)


class RoutineModel(Base):
    __tablename__ = "routines"

    id              = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name            = Column(String(128), nullable=False)
    trigger_pattern = Column(Text, nullable=True)
    actions         = Column(JSON, nullable=False, default=list)
    frequency       = Column(Integer, default=0)
    last_run        = Column(DateTime, nullable=True)
    enabled         = Column(Boolean, default=True)
    created_at      = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AppEventModel(Base):
    __tablename__ = "app_events"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    app_name    = Column(String(128), nullable=False, index=True)
    event_type  = Column(String(32), nullable=False)  # open|close|focus
    timestamp   = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    day_of_week = Column(Integer, nullable=True)
    hour_of_day = Column(Integer, nullable=True)


class AuditLogModel(Base):
    __tablename__ = "audit_log"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_type  = Column(String(64), nullable=False)
    description = Column(Text, nullable=False)
    severity    = Column(String(16), default="INFO")
    action_id   = Column(String(36), nullable=True)
    timestamp   = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ReminderModel(Base):
    __tablename__ = "reminders"

    id         = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    content    = Column(Text, nullable=False)
    due_at     = Column(DateTime, nullable=False, index=True)
    recurrence = Column(String(64), nullable=True)  # cron expression
    notified   = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── Indexes ───────────────────────────────────────────────────────────────────

Index("idx_conversations_session", ConversationModel.session_id, ConversationModel.timestamp)
Index("idx_actions_timestamp", ActionModel.timestamp)
Index("idx_memories_type", MemoryModel.type, MemoryModel.confidence)
Index("idx_reminders_due", ReminderModel.due_at, ReminderModel.notified)


# ── Database Manager ──────────────────────────────────────────────────────────

class DatabaseManager:
    """
    Async SQLite database manager.

    Usage:
        db = DatabaseManager(Path("data/kaoruko.db"))
        await db.initialize()

        async with db.session() as sess:
            sess.add(ConversationModel(...))
            await sess.commit()
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._engine: Optional[AsyncEngine] = None
        self._session_factory: Optional[async_sessionmaker] = None

    async def initialize(self) -> None:
        """Create database, run schema, set up connection pool."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        db_url = f"sqlite+aiosqlite:///{self._db_path}"

        self._engine = create_async_engine(
            db_url,
            echo=False,
            connect_args={"check_same_thread": False},
            # Use StaticPool for SQLite (single connection, safe for single process)
            poolclass=StaticPool,
        )

        # Enable WAL mode for better concurrent read performance
        @event.listens_for(self._engine.sync_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.close()

        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        # Create all tables
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        log.info("database_initialized", path=str(self._db_path))

    def session(self) -> AsyncSession:
        """Return a new async session (use as context manager)."""
        if not self._session_factory:
            raise RuntimeError("DatabaseManager not initialized. Call initialize() first.")
        return self._session_factory()

    async def close(self) -> None:
        """Dispose of the connection pool."""
        if self._engine:
            await self._engine.dispose()
            log.info("database_closed")

    # ── Convenience write methods ─────────────────────────────────────────────

    async def save_conversation_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        language: str = "en",
        intent: Optional[str] = None,
        confidence: Optional[float] = None,
        metadata: Optional[dict] = None,
    ) -> str:
        """Save a conversation turn, return its ID."""
        record = ConversationModel(
            session_id=session_id,
            role=role,
            content=content,
            language=language,
            intent=intent,
            confidence=confidence,
            meta=metadata,
        )
        async with self.session() as sess:
            sess.add(record)
            await sess.commit()
        return record.id

    async def save_action(
        self,
        action_type: str,
        handler: str,
        parameters: dict,
        status: str,
        duration_ms: Optional[int] = None,
        error_message: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> str:
        """Log an executed action, return its ID."""
        record = ActionModel(
            action_type=action_type,
            handler=handler,
            parameters=parameters,
            status=status,
            duration_ms=duration_ms,
            error_message=error_message,
            conversation_id=conversation_id,
        )
        async with self.session() as sess:
            sess.add(record)
            await sess.commit()
        return record.id

    async def save_memory(
        self,
        type: str,
        subject: str,
        content: str,
        tags: Optional[list[str]] = None,
        confidence: float = 1.0,
        expires_at: Optional[datetime] = None,
    ) -> str:
        """Store a long-term memory fact."""
        record = MemoryModel(
            type=type,
            subject=subject,
            content=content,
            tags=tags,
            confidence=confidence,
            expires_at=expires_at,
        )
        async with self.session() as sess:
            sess.add(record)
            await sess.commit()
        return record.id

    async def get_preference(self, key: str) -> Optional[Any]:
        """Retrieve a user preference value."""
        from sqlalchemy import select
        async with self.session() as sess:
            result = await sess.execute(
                select(PreferenceModel).where(PreferenceModel.key == key)
            )
            row = result.scalar_one_or_none()
            return row.value if row else None

    async def set_preference(self, key: str, value: Any, category: str = "behavior") -> None:
        """Set or update a user preference."""
        from sqlalchemy.dialects.sqlite import insert
        stmt = insert(PreferenceModel).values(
            key=key, value=value, category=category
        ).on_conflict_do_update(
            index_elements=["key"],
            set_={"value": value, "updated_at": datetime.now(timezone.utc)},
        )
        async with self.session() as sess:
            await sess.execute(stmt)
            await sess.commit()

    async def audit(
        self,
        event_type: str,
        description: str,
        severity: str = "INFO",
        action_id: Optional[str] = None,
    ) -> None:
        """Write a security audit log entry."""
        record = AuditLogModel(
            event_type=event_type,
            description=description,
            severity=severity,
            action_id=action_id,
        )
        async with self.session() as sess:
            sess.add(record)
            await sess.commit()
