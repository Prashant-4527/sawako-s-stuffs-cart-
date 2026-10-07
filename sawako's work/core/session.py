"""
kaoruko/core/session.py

Conversation session management.
Tracks multi-turn context, intent history, and entity memory within a session.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

from kaoruko.infrastructure.logging.logger import get_logger

log = get_logger("core.session")


@dataclass
class Turn:
    """A single conversation turn (user OR assistant)."""
    turn_id: str = field(default_factory=lambda: str(uuid4())[:8])
    role: str = ""           # "user" | "assistant"
    text: str = ""
    intent: Optional[str] = None
    entities: dict[str, Any] = field(default_factory=dict)
    language: str = "en"
    confidence: float = 1.0
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def age_seconds(self) -> float:
        return time.time() - self.timestamp


@dataclass
class Session:
    """
    A voice session — from wake word to silence.
    Tracks context across multiple follow-up commands.
    """
    session_id: str = field(default_factory=lambda: str(uuid4()))
    started_at: float = field(default_factory=time.time)
    turns: list[Turn] = field(default_factory=list)
    active_intent: Optional[str] = None
    active_entities: dict[str, Any] = field(default_factory=dict)
    last_app_mentioned: Optional[str] = None
    last_url_mentioned: Optional[str] = None
    language: str = "en"

    def add_turn(self, turn: Turn) -> None:
        self.turns.append(turn)
        # Update active context from latest user turn
        if turn.role == "user" and turn.intent:
            self.active_intent = turn.intent
            # Merge entities (newer values override older)
            self.active_entities.update(turn.entities)
            # Track contextual shortcuts
            if "app_name" in turn.entities:
                self.last_app_mentioned = turn.entities["app_name"]
            if "url" in turn.entities:
                self.last_url_mentioned = turn.entities["url"]

    def get_recent_turns(self, n: int = 5) -> list[Turn]:
        """Get the N most recent turns for context injection."""
        return self.turns[-n:]

    def resolve_context(self, text: str, entities: dict[str, Any]) -> dict[str, Any]:
        """
        Resolve contextual references like "it", "that app", "there".

        Example:
            Turn 1: "Open Chrome"
            Turn 2: "Search for AI jobs"  → implicitly in Chrome
        """
        resolved = dict(entities)

        # If user said something like "search for X" without specifying a browser,
        # inject the last known app context if it's a browser
        BROWSER_APPS = {"chrome", "firefox", "edge", "safari", "brave"}
        if self.last_app_mentioned in BROWSER_APPS and "app_name" not in resolved:
            if any(word in text.lower() for word in ("search", "open", "go to", "navigate")):
                resolved["_context_app"] = self.last_app_mentioned

        return resolved

    def duration_seconds(self) -> float:
        return time.time() - self.started_at

    def to_history_messages(self) -> list[dict[str, str]]:
        """Convert turns to Claude/OpenAI message format."""
        return [
            {"role": t.role, "content": t.text}
            for t in self.turns
            if t.text.strip()
        ]

    def __repr__(self) -> str:
        return (
            f"Session(id={self.session_id[:8]}, "
            f"turns={len(self.turns)}, "
            f"duration={self.duration_seconds():.1f}s)"
        )


class SessionManager:
    """
    Manages the lifecycle of conversation sessions.
    Creates new sessions on wake, archives completed sessions.
    """

    SESSION_TIMEOUT = 120.0  # seconds of inactivity before session ends

    def __init__(self) -> None:
        self._current: Optional[Session] = None
        self._archived: list[Session] = []
        self._max_archived = 100

    def start_session(self) -> Session:
        """Start a new session (on wake word detection)."""
        if self._current:
            self._archive_current()

        self._current = Session()
        log.info("session_started", session_id=self._current.session_id)
        return self._current

    def get_or_create(self) -> Session:
        """Get current session, or create one if none exists."""
        if self._current is None or self._is_expired():
            return self.start_session()
        return self._current

    def end_session(self) -> Optional[Session]:
        """Explicitly end the current session."""
        if not self._current:
            return None
        archived = self._current
        self._archive_current()
        log.info(
            "session_ended",
            session_id=archived.session_id,
            turns=len(archived.turns),
            duration=archived.duration_seconds(),
        )
        return archived

    def current(self) -> Optional[Session]:
        return self._current

    def _is_expired(self) -> bool:
        if not self._current or not self._current.turns:
            return False
        last_turn = self._current.turns[-1]
        return last_turn.age_seconds() > self.SESSION_TIMEOUT

    def _archive_current(self) -> None:
        if self._current:
            self._archived.append(self._current)
            if len(self._archived) > self._max_archived:
                self._archived.pop(0)
            self._current = None
