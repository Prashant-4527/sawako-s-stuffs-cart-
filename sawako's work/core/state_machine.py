"""
kaoruko/core/state_machine.py

Finite state machine for the Kaoruko assistant lifecycle.
Manages valid state transitions and enforces invariants.

States:
  INITIALIZING → IDLE → LISTENING → PROCESSING → SPEAKING → IDLE
                                                           ↘ ERROR → IDLE
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Optional, Callable
import asyncio

from kaoruko.infrastructure.logging.logger import get_logger

log = get_logger("core.state_machine")


class AssistantState(str, Enum):
    INITIALIZING = "initializing"
    IDLE         = "idle"
    LISTENING    = "listening"
    PROCESSING   = "processing"
    SPEAKING     = "speaking"
    CONFIRMING   = "confirming"   # Waiting for user confirmation
    ERROR        = "error"
    SHUTDOWN     = "shutdown"


# Valid transitions: current_state → set of allowed next states
_TRANSITIONS: dict[AssistantState, set[AssistantState]] = {
    AssistantState.INITIALIZING: {AssistantState.IDLE, AssistantState.ERROR},
    AssistantState.IDLE:         {AssistantState.LISTENING, AssistantState.SHUTDOWN},
    AssistantState.LISTENING:    {AssistantState.PROCESSING, AssistantState.IDLE},
    AssistantState.PROCESSING:   {AssistantState.SPEAKING, AssistantState.CONFIRMING,
                                  AssistantState.IDLE, AssistantState.ERROR},
    AssistantState.SPEAKING:     {AssistantState.IDLE, AssistantState.LISTENING},
    AssistantState.CONFIRMING:   {AssistantState.PROCESSING, AssistantState.IDLE},
    AssistantState.ERROR:        {AssistantState.IDLE, AssistantState.SHUTDOWN},
    AssistantState.SHUTDOWN:     set(),  # Terminal state
}

StateChangeCallback = Callable[[AssistantState, AssistantState], None]


class StateMachine:
    """
    Thread-safe assistant state machine.

    Usage:
        sm = StateMachine()
        await sm.transition(AssistantState.LISTENING)
        sm.state  → AssistantState.LISTENING
    """

    def __init__(self) -> None:
        self._state = AssistantState.INITIALIZING
        self._previous: Optional[AssistantState] = None
        self._lock = asyncio.Lock()
        self._callbacks: list[StateChangeCallback] = []
        self._state_history: list[tuple[AssistantState, float]] = []

    @property
    def state(self) -> AssistantState:
        return self._state

    @property
    def previous_state(self) -> Optional[AssistantState]:
        return self._previous

    def is_busy(self) -> bool:
        return self._state in {
            AssistantState.LISTENING,
            AssistantState.PROCESSING,
            AssistantState.SPEAKING,
            AssistantState.CONFIRMING,
        }

    def can_wake(self) -> bool:
        """Can wake word trigger a listening session right now?"""
        return self._state == AssistantState.IDLE

    async def transition(self, new_state: AssistantState) -> bool:
        """
        Attempt a state transition.

        Returns:
            True if transition succeeded, False if invalid.
        """
        async with self._lock:
            allowed = _TRANSITIONS.get(self._state, set())
            if new_state not in allowed:
                log.warning(
                    "invalid_state_transition",
                    from_state=self._state.value,
                    to_state=new_state.value,
                    allowed=[s.value for s in allowed],
                )
                return False

            old_state = self._state
            self._previous = old_state
            self._state = new_state

            import time
            self._state_history.append((new_state, time.time()))
            if len(self._state_history) > 100:
                self._state_history.pop(0)

            log.info(
                "state_transition",
                from_state=old_state.value,
                to_state=new_state.value,
            )

            for callback in self._callbacks:
                try:
                    callback(old_state, new_state)
                except Exception as e:
                    log.error("state_callback_error", error=str(e))

            return True

    def on_transition(self, callback: StateChangeCallback) -> None:
        """Register a callback for state changes."""
        self._callbacks.append(callback)

    def __repr__(self) -> str:
        return f"StateMachine(state={self._state.value})"
