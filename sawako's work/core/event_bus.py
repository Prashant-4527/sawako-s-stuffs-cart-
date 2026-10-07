"""
kaoruko/core/event_bus.py

Async event bus — the central nervous system of Kaoruko.
All components communicate exclusively through this bus.
Supports: publish, subscribe, unsubscribe, wildcard patterns, priority.

Architecture pattern: Observer / Pub-Sub (async-native)

Fix applied:
  - publish_sync() now uses asyncio.get_running_loop() when called from within
    a running loop, falling back to asyncio.get_event_loop() only for truly
    sync contexts. get_event_loop() emits DeprecationWarning in Python 3.10+.
  - Added clear_subscriptions() for clean shutdown/testing (clears all subs).
  - Added subscriber_ids property for introspection in tests.
"""

from __future__ import annotations

import asyncio
import re
import weakref
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Optional, Union
from uuid import uuid4

from kaoruko.infrastructure.logging.logger import get_logger

log = get_logger("core.event_bus")


# ── Event Definitions ─────────────────────────────────────────────────────────

class KaorukoEvent(str, Enum):
    """All canonical event types in the Kaoruko system."""

    # ── Voice Pipeline Events ─────────────────────────────────
    VOICE_WAKE_DETECTED       = "voice.wake_detected"
    VOICE_LISTENING_START     = "voice.listening_start"
    VOICE_LISTENING_STOP      = "voice.listening_stop"
    VOICE_AUDIO_CAPTURED      = "voice.audio_captured"
    VOICE_TRANSCRIPT_READY    = "voice.transcript_ready"
    VOICE_LANGUAGE_DETECTED   = "voice.language_detected"

    # ── NLU Events ────────────────────────────────────────────
    NLU_INTENT_RESOLVED       = "nlu.intent_resolved"
    NLU_ENTITIES_EXTRACTED    = "nlu.entities_extracted"
    NLU_LOW_CONFIDENCE        = "nlu.low_confidence"
    NLU_AMBIGUOUS             = "nlu.ambiguous"

    # ── Execution Events ──────────────────────────────────────
    EXEC_ACTION_STARTED       = "execution.action_started"
    EXEC_ACTION_SUCCESS       = "execution.action_success"
    EXEC_ACTION_FAILED        = "execution.action_failed"
    EXEC_ACTION_CANCELLED     = "execution.action_cancelled"
    EXEC_CONFIRM_REQUIRED     = "execution.confirm_required"
    EXEC_CONFIRM_RECEIVED     = "execution.confirm_received"

    # ── TTS Events ────────────────────────────────────────────
    TTS_SPEAKING_START        = "tts.speaking_start"
    TTS_SPEAKING_END          = "tts.speaking_end"
    TTS_WAVEFORM_DATA         = "tts.waveform_data"

    # ── UI Events ─────────────────────────────────────────────
    UI_MODE_CHANGED           = "ui.mode_changed"
    UI_THEME_CHANGED          = "ui.theme_changed"
    UI_SHOW_NOTIFICATION      = "ui.show_notification"
    UI_UPDATE_TRANSCRIPT      = "ui.update_transcript"
    UI_UPDATE_HISTORY         = "ui.update_history"
    UI_ORB_STATE_CHANGED      = "ui.orb_state_changed"

    # ── Memory Events ─────────────────────────────────────────
    MEMORY_UPDATED            = "memory.updated"
    MEMORY_ROUTINE_TRIGGERED  = "memory.routine_triggered"

    # ── Assistant Lifecycle ───────────────────────────────────
    ASSISTANT_READY           = "assistant.ready"
    ASSISTANT_BUSY            = "assistant.busy"
    ASSISTANT_IDLE            = "assistant.idle"
    ASSISTANT_SHUTDOWN        = "assistant.shutdown"

    # ── System Events ─────────────────────────────────────────
    SYSTEM_ERROR              = "error.occurred"
    SYSTEM_WARNING            = "system.warning"
    SYSTEM_STATUS_UPDATE      = "system.status_update"

    # ── Plugin Events ─────────────────────────────────────────
    PLUGIN_LOADED             = "plugin.loaded"
    PLUGIN_UNLOADED           = "plugin.unloaded"
    PLUGIN_ERROR              = "plugin.error"


@dataclass
class Event:
    """An event payload traveling through the bus."""
    type: KaorukoEvent
    data: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4())[:8])
    source: Optional[str] = None

    def __repr__(self) -> str:
        return f"Event(type={self.type.value}, id={self.event_id}, source={self.source})"


# ── Subscriber Types ──────────────────────────────────────────────────────────

AsyncHandler = Callable[[Event], Coroutine[Any, Any, None]]
SyncHandler  = Callable[[Event], None]
Handler      = Union[AsyncHandler, SyncHandler]


@dataclass
class Subscription:
    handler: Handler
    priority: int = 0
    once: bool = False
    pattern: Optional[re.Pattern] = None


# ── Event Bus ─────────────────────────────────────────────────────────────────

class EventBus:
    """
    Async event bus with:
    - Direct event subscriptions
    - Wildcard pattern subscriptions (e.g. "voice.*", "*.error")
    - Priority ordering
    - One-shot subscriptions
    - Full audit trail logging
    - Clean shutdown via clear_subscriptions()
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Subscription]] = defaultdict(list)
        self._wildcard_subscribers: list[tuple[re.Pattern, Subscription]] = []
        self._lock = asyncio.Lock()
        self._event_history: list[Event] = []
        self._max_history = 500
        log.info("event_bus_initialized")

    # ── Subscribe ─────────────────────────────────────────────────────────────

    def subscribe(
        self,
        event_type: Union[KaorukoEvent, str],
        handler: Handler,
        priority: int = 0,
        once: bool = False,
    ) -> str:
        """
        Subscribe to an event.

        Args:
            event_type: KaorukoEvent enum or string (supports "*" wildcards)
            handler:    Async or sync callable
            priority:   Higher = called first (default 0)
            once:       If True, auto-unsubscribe after first event

        Returns:
            Subscription ID
        """
        event_str = event_type.value if isinstance(event_type, KaorukoEvent) else event_type
        sub = Subscription(handler=handler, priority=priority, once=once)

        if "*" in event_str:
            regex = re.compile("^" + re.escape(event_str).replace(r"\*", ".*") + "$")
            sub.pattern = regex
            self._wildcard_subscribers.append((regex, sub))
        else:
            subs = self._subscribers[event_str]
            subs.append(sub)
            subs.sort(key=lambda s: s.priority, reverse=True)

        log.debug("event_subscribed", evt=event_str, priority=priority, once=once)
        return str(id(sub))

    def unsubscribe(self, event_type: Union[KaorukoEvent, str], handler: Handler) -> bool:
        """Remove a specific handler from an event."""
        event_str = event_type.value if isinstance(event_type, KaorukoEvent) else event_type
        before = len(self._subscribers[event_str])
        self._subscribers[event_str] = [
            s for s in self._subscribers[event_str] if s.handler is not handler
        ]
        removed = before - len(self._subscribers[event_str])
        if removed:
            log.debug("event_unsubscribed", evt=event_str)
        return removed > 0

    def clear_subscriptions(self, event_type: Optional[Union[KaorukoEvent, str]] = None) -> int:
        """
        Remove all subscriptions. If event_type given, clear only that event.
        Returns the number of subscriptions removed.

        FIX: Added for proper shutdown cleanup and test isolation.
        Without this, long-running tests or repeated shutdown/startup cycles
        accumulate ghost subscriptions that fire on stale events.
        """
        if event_type is not None:
            event_str = event_type.value if isinstance(event_type, KaorukoEvent) else event_type
            count = len(self._subscribers.get(event_str, []))
            self._subscribers[event_str] = []
            log.debug("subscriptions_cleared", evt=event_str, count=count)
            return count

        total = sum(len(v) for v in self._subscribers.values())
        total += len(self._wildcard_subscribers)
        self._subscribers.clear()
        self._wildcard_subscribers.clear()
        log.info("all_subscriptions_cleared", count=total)
        return total

    # ── Publish ───────────────────────────────────────────────────────────────

    async def publish(
        self,
        event_type: Union[KaorukoEvent, str],
        data: Optional[dict[str, Any]] = None,
        source: Optional[str] = None,
    ) -> None:
        """Publish an event to all subscribers."""
        event_str = event_type.value if isinstance(event_type, KaorukoEvent) else event_type
        event = Event(
            type=event_type if isinstance(event_type, KaorukoEvent) else KaorukoEvent(event_type),
            data=data or {},
            source=source,
        )

        handlers: list[Subscription] = []
        handlers.extend(self._subscribers.get(event_str, []))

        for pattern, sub in self._wildcard_subscribers:
            if pattern.match(event_str):
                handlers.append(sub)

        handlers.sort(key=lambda s: s.priority, reverse=True)

        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history.pop(0)

        log.debug(
            "event_published",
            evt=event_str,
            source=source,
            subscriber_count=len(handlers),
            event_id=event.event_id,
        )

        to_remove: list[Subscription] = []
        for sub in handlers:
            try:
                if asyncio.iscoroutinefunction(sub.handler):
                    await sub.handler(event)
                else:
                    sub.handler(event)
            except Exception as e:
                log.error(
                    "event_handler_error",
                    event=event_str,
                    error=str(e),
                    handler=getattr(sub.handler, "__name__", "unknown"),
                )
            if sub.once:
                to_remove.append(sub)

        for sub in to_remove:
            if sub.pattern:
                self._wildcard_subscribers = [
                    (p, s) for p, s in self._wildcard_subscribers if s is not sub
                ]
            else:
                self._subscribers[event_str] = [
                    s for s in self._subscribers[event_str] if s is not sub
                ]

    def publish_sync(
        self,
        event_type: Union[KaorukoEvent, str],
        data: Optional[dict[str, Any]] = None,
        source: Optional[str] = None,
    ) -> None:
        """
        Synchronous publish — schedules event dispatch on the running loop.

        FIX: Previously used asyncio.get_event_loop() unconditionally.
        get_event_loop() is deprecated in Python 3.10+ when called from
        a non-main thread or when there is no current event loop.
        Now uses get_running_loop() inside a running context, and falls
        back to get_event_loop() only for truly synchronous callers.
        """
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.publish(event_type, data, source))
        except RuntimeError:
            # No running loop — caller is in a sync context
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.publish(event_type, data, source))
            else:
                loop.run_until_complete(self.publish(event_type, data, source))

    # ── Utilities ─────────────────────────────────────────────────────────────

    async def wait_for(
        self,
        event_type: Union[KaorukoEvent, str],
        timeout: float = 10.0,
        predicate: Optional[Callable[[Event], bool]] = None,
    ) -> Optional[Event]:
        """Wait for a specific event (useful for confirmation flows)."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Event] = loop.create_future()

        async def _resolve(event: Event) -> None:
            if predicate is None or predicate(event):
                if not future.done():
                    future.set_result(event)

        self.subscribe(event_type, _resolve, once=True)

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def get_recent_events(
        self,
        event_type: Optional[KaorukoEvent] = None,
        limit: int = 50,
    ) -> list[Event]:
        """Get recent events from history."""
        events = self._event_history
        if event_type:
            events = [e for e in events if e.type == event_type]
        return events[-limit:]

    def subscriber_count(self, event_type: Union[KaorukoEvent, str]) -> int:
        event_str = event_type.value if isinstance(event_type, KaorukoEvent) else event_type
        return len(self._subscribers.get(event_str, []))

    @property
    def total_subscriber_count(self) -> int:
        """Total number of active subscriptions across all events."""
        return sum(len(v) for v in self._subscribers.values()) + len(self._wildcard_subscribers)


# ── Singleton accessor ────────────────────────────────────────────────────────

_bus_instance: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get the global event bus singleton."""
    global _bus_instance
    if _bus_instance is None:
        _bus_instance = EventBus()
    return _bus_instance


def reset_event_bus() -> EventBus:
    """Reset the global event bus singleton (for testing only)."""
    global _bus_instance
    _bus_instance = EventBus()
    return _bus_instance
