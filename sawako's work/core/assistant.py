"""
kaoruko/core/assistant.py

KaorukoAssistant — the central orchestrator.
Owns the assistant lifecycle, wires all subsystems together,
and coordinates the voice → NLU → execution → TTS pipeline.

Fixes applied:
  - Accepts project_root parameter; no more Path.cwd() guessing
  - Removed _check_plugins() keyword-map that bypassed the NLU pipeline.
    Plugin intents are now resolved through the NLU pipeline and dispatched
    in _on_intent_resolved(), keeping intent resolution in one place.
  - All event subscriptions tracked by handler reference for proper
    cleanup on shutdown — prevents ghost callbacks if shutdown is called
    before the event loop closes.
  - AIRouter and PluginManager both receive project_root.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from kaoruko.core.event_bus import EventBus, KaorukoEvent, Event, get_event_bus
from kaoruko.core.state_machine import AssistantState, StateMachine
from kaoruko.core.session import SessionManager, Turn
from kaoruko.infrastructure.logging.logger import get_logger, bind_request_context

if TYPE_CHECKING:
    from kaoruko.infrastructure.config.schema import KaorukoConfig
    from kaoruko.memory.long_term import DatabaseManager
    from kaoruko.infrastructure.telemetry.metrics import MetricsCollector

log = get_logger("core.assistant")


class KaorukoAssistant:
    """
    Top-level orchestrator for the Kaoruko AI assistant.

    Responsibilities:
    - Bootstrap all subsystems in correct dependency order
    - Route voice events through the full processing pipeline
    - Manage the assistant state machine
    - Handle graceful startup and shutdown
    """

    def __init__(
        self,
        config: "KaorukoConfig",
        db: "DatabaseManager",
        metrics: "MetricsCollector",
        project_root: Optional[Path] = None,  # FIX: explicit root, not Path.cwd()
    ) -> None:
        self.config = config
        self.db = db
        self.metrics = metrics
        # FIX: Accept explicit project_root. Falls back to cwd only as last resort.
        self._project_root = project_root or Path.cwd()

        self.bus: EventBus = get_event_bus()
        self.state_machine = StateMachine()
        self.session_manager = SessionManager()

        self._voice_pipeline = None
        self._nlu_engine = None
        self._execution_engine = None
        self._memory_manager = None
        self._ai_router = None
        self._plugin_manager = None
        self._reminder_scheduler = None

        # FIX: Track subscribed handlers so we can unsubscribe cleanly in shutdown()
        self._subscribed_handlers: list[tuple] = []  # [(event_type, handler), ...]

        self._running = False
        self._startup_complete = asyncio.Event()

        log.info("assistant_created", name=config.assistant.name)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def startup(self) -> None:
        """Full bootstrap sequence."""
        log.info("assistant_startup_begin")
        await self.state_machine.transition(AssistantState.INITIALIZING)

        # 1. Memory system
        from kaoruko.memory.memory_manager import MemoryManager
        self._memory_manager = MemoryManager(db=self.db, config=self.config)
        await self._memory_manager.initialize()
        log.info("memory_ready")

        # 2. AI router — FIX: pass project_root
        from kaoruko.intelligence.ai_router import AIRouter
        self._ai_router = AIRouter(config=self.config, project_root=self._project_root)
        log.info("ai_router_ready", primary=self.config.ai.primary)

        # 3. NLU engine
        from kaoruko.nlu.intent_classifier import IntentClassifier
        self._nlu_engine = IntentClassifier(
            config=self.config,
            ai_router=self._ai_router,
        )
        await self._nlu_engine.initialize()
        log.info("nlu_ready")

        # 4. Execution engine
        from kaoruko.execution.executor import Executor
        self._execution_engine = Executor(
            config=self.config,
            bus=self.bus,
            memory=self._memory_manager,
        )
        assistant_handler = self._execution_engine._registry.get("assistant")
        if assistant_handler:
            assistant_handler.memory = self._memory_manager
        log.info("executor_ready")

        # 5. Plugin manager — FIX: use project_root, not Path.cwd()
        from kaoruko.plugins.plugin_base import PluginManager
        plugins_dir = self._project_root / "kaoruko" / "plugins" / "built_in"
        self._plugin_manager = PluginManager(
            plugins_dir=plugins_dir,
            config=self.config,
            bus=self.bus,
        )
        loaded = await self._plugin_manager.discover_and_load()
        log.info("plugins_ready", loaded=loaded)

        # FIX: Inject plugin_manager into NLU so it can check plugin intents
        # as Layer 0 before the rule engine (fast path, zero AI cost)
        self._nlu_engine.set_plugin_manager(self._plugin_manager)

        # 6. Reminder scheduler
        from kaoruko.execution.handlers.reminder_scheduler import ReminderScheduler
        self._reminder_scheduler = ReminderScheduler(db=self.db, bus=self.bus)
        self._reminder_scheduler.start()
        log.info("reminder_scheduler_ready")

        # 7. Voice pipeline
        from kaoruko.voice.pipeline import VoicePipeline
        self._voice_pipeline = VoicePipeline(config=self.config, bus=self.bus)

        # 8. Wire events
        self._wire_events()

        # 9. Start voice pipeline
        await self._voice_pipeline.start()
        log.info("voice_pipeline_running")

        # 10. Mark ready
        await self.state_machine.transition(AssistantState.IDLE)
        self._running = True
        self._startup_complete.set()

        await self.bus.publish(
            KaorukoEvent.ASSISTANT_READY,
            data={"version": "1.0.0", "name": self.config.assistant.name},
            source="assistant",
        )
        log.info("assistant_ready", message="Hajimemashite~ Kaoruko is ready.")

    async def shutdown(self) -> None:
        """Graceful shutdown — stop all subsystems cleanly."""
        log.info("assistant_shutdown_begin")
        self._running = False

        await self.bus.publish(KaorukoEvent.ASSISTANT_SHUTDOWN, source="assistant")

        # FIX: Unsubscribe all event handlers to prevent ghost callbacks
        # if shutdown() is called while the event loop is still running.
        for event_type, handler in self._subscribed_handlers:
            self.bus.unsubscribe(event_type, handler)
        self._subscribed_handlers.clear()
        log.info("event_subscriptions_cleared")

        if self._reminder_scheduler:
            self._reminder_scheduler.stop()

        if self._voice_pipeline:
            await self._voice_pipeline.stop()

        if self._memory_manager:
            await self._memory_manager.flush()

        if self.db:
            await self.db.close()

        await self.state_machine.transition(AssistantState.SHUTDOWN)
        log.info("assistant_shutdown_complete")

    async def wait_until_ready(self) -> None:
        await self._startup_complete.wait()

    # ── Event Wiring ──────────────────────────────────────────────────────────

    def _wire_events(self) -> None:
        """
        Connect all inter-component event subscriptions.

        FIX: Store (event_type, handler) tuples in _subscribed_handlers so
        shutdown() can cleanly unsubscribe them and prevent ghost callbacks.
        """
        subs = [
            (KaorukoEvent.VOICE_WAKE_DETECTED,   self._on_wake_detected,   10),
            (KaorukoEvent.VOICE_TRANSCRIPT_READY, self._on_transcript_ready, 10),
            (KaorukoEvent.NLU_INTENT_RESOLVED,   self._on_intent_resolved,  10),
            (KaorukoEvent.EXEC_ACTION_SUCCESS,    self._on_action_complete,  0),
            (KaorukoEvent.EXEC_ACTION_FAILED,     self._on_action_failed,    0),
            (KaorukoEvent.TTS_SPEAKING_END,       self._on_speaking_ended,   0),
        ]
        for event_type, handler, priority in subs:
            self.bus.subscribe(event_type, handler, priority=priority)
            self._subscribed_handlers.append((event_type, handler))

        log.info("events_wired", count=len(subs))

    # ── Pipeline Handlers ─────────────────────────────────────────────────────

    async def _on_wake_detected(self, event: Event) -> None:
        if not self.state_machine.can_wake():
            return
        session = self.session_manager.start_session()
        bind_request_context(session_id=session.session_id)
        await self.state_machine.transition(AssistantState.LISTENING)
        await self.bus.publish(
            KaorukoEvent.UI_ORB_STATE_CHANGED,
            data={"state": "listening"}, source="assistant",
        )
        self.metrics.increment("wake_detections")

    async def _on_transcript_ready(self, event: Event) -> None:
        text: str = event.data.get("text", "").strip()
        language: str = event.data.get("language", "en")
        if not text:
            await self._return_to_idle()
            return

        session = self.session_manager.get_or_create()
        turn = Turn(role="user", text=text, language=language)
        session.add_turn(turn)
        session.language = language

        await self.state_machine.transition(AssistantState.PROCESSING)
        await self.bus.publish(
            KaorukoEvent.UI_UPDATE_TRANSCRIPT,
            data={"role": "user", "text": text}, source="assistant",
        )

        # FIX: Removed _check_plugins() keyword-map bypass.
        # All intent classification now flows through the NLU pipeline.
        # Plugin Layer 0 is handled inside IntentClassifier.classify()
        # via the injected PluginManager. This keeps the single-responsibility
        # principle intact and ensures metrics/logging cover plugin hits too.

        try:
            result = await self._nlu_engine.classify(
                text=text, language=language, context=session,
            )
            turn.intent = result.intent
            turn.entities = result.entities
            turn.confidence = result.confidence

            await self.bus.publish(
                KaorukoEvent.NLU_INTENT_RESOLVED,
                data={
                    "intent": result.intent,
                    "entities": result.entities,
                    "confidence": result.confidence,
                    "action_plan": result.action_plan,
                    "response_text": result.response_text,
                    "plugin_response": result.plugin_response,
                    "session_id": session.session_id,
                },
                source="nlu",
            )
        except Exception as e:
            log.error("nlu_error", error=str(e), text=text)
            await self._handle_error("I encountered a processing error. Gomen nasai~")

    async def _on_intent_resolved(self, event: Event) -> None:
        action_plan = event.data.get("action_plan")
        response_text = event.data.get("response_text", "")
        plugin_response = event.data.get("plugin_response")  # FIX: plugin result from NLU Layer 0
        session = self.session_manager.current()

        # FIX: Plugin response comes through the NLU pipeline now (not a bypass).
        if plugin_response is not None:
            await self._speak(plugin_response)
        elif action_plan:
            await self._execution_engine.execute(
                action_plan=action_plan,
                session=session,
            )
        elif response_text:
            await self._speak(response_text)
        else:
            await self._return_to_idle()

    async def _on_action_complete(self, event: Event) -> None:
        response = event.data.get("response_text")
        session = self.session_manager.current()
        if response:
            if session:
                session.add_turn(Turn(role="assistant", text=response))
            await self._speak(response)
        else:
            await self._return_to_idle()
        await self.bus.publish(
            KaorukoEvent.UI_UPDATE_HISTORY,
            data=event.data, source="assistant",
        )
        self.metrics.increment("actions_executed")

    async def _on_action_failed(self, event: Event) -> None:
        error_msg = event.data.get("error_message", "Something went wrong.")
        await self._handle_error(f"Gomen nasai~ {error_msg}")

    async def _on_speaking_ended(self, event: Event) -> None:
        await self._return_to_idle()

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _speak(self, text: str) -> None:
        await self.state_machine.transition(AssistantState.SPEAKING)
        await self.bus.publish(
            KaorukoEvent.UI_UPDATE_TRANSCRIPT,
            data={"role": "assistant", "text": text}, source="assistant",
        )
        if self._voice_pipeline:
            await self._voice_pipeline.speak(text)
        else:
            await self._return_to_idle()

    async def _handle_error(self, message: str) -> None:
        await self.state_machine.transition(AssistantState.ERROR)
        await self.bus.publish(
            KaorukoEvent.UI_ORB_STATE_CHANGED,
            data={"state": "error"}, source="assistant",
        )
        await self._speak(message)
        await self.state_machine.transition(AssistantState.IDLE)

    async def _return_to_idle(self) -> None:
        await self.state_machine.transition(AssistantState.IDLE)
        await self.bus.publish(
            KaorukoEvent.UI_ORB_STATE_CHANGED,
            data={"state": "idle"}, source="assistant",
        )

    @property
    def is_ready(self) -> bool:
        return self._startup_complete.is_set()

    @property
    def current_state(self) -> AssistantState:
        return self.state_machine.state
