"""
kaoruko/execution/executor.py

Action Executor — dispatches action plans to appropriate handlers.
Handles confirmation gates, error recovery, parallel/sequential execution,
and response text generation.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

from kaoruko.core.event_bus import EventBus, KaorukoEvent
from kaoruko.execution.registry import HandlerRegistry
from kaoruko.infrastructure.logging.logger import get_logger
from kaoruko.security.confirmation import ConfirmationGate

if TYPE_CHECKING:
    from kaoruko.infrastructure.config.schema import KaorukoConfig
    from kaoruko.memory.memory_manager import MemoryManager
    from kaoruko.core.session import Session

log = get_logger("execution.executor")


@dataclass
class ActionResult:
    success: bool
    handler: str
    method: str
    params: dict
    response_text: str = ""
    duration_ms: float = 0.0
    error: Optional[str] = None


class Executor:
    """
    Central action dispatcher.

    Receives action plans from the NLU layer and:
    1. Validates actions against the permission system
    2. Applies confirmation gates for destructive actions
    3. Dispatches to registered handlers
    4. Publishes results to the event bus
    5. Logs all actions to the audit trail
    """

    def __init__(
        self,
        config: "KaorukoConfig",
        bus: EventBus,
        memory: "MemoryManager",
    ) -> None:
        self.config = config
        self.bus = bus
        self.memory = memory
        self._registry = HandlerRegistry(config=config, bus=bus)
        self._confirmation = ConfirmationGate(config=config, bus=bus)
        self._registry.register_all()
        log.info("executor_ready", handlers=list(self._registry.handlers.keys()))

    async def execute(
        self,
        action_plan: dict[str, Any],
        session: Optional["Session"] = None,
    ) -> None:
        """
        Execute an action plan.

        Args:
            action_plan: From NLU layer:
                {
                  "actions": [{"handler": ..., "method": ..., "params": ...}, ...],
                  "execution_mode": "sequential" | "parallel",
                  "response_strategy": "immediate" | "after_completion" | "silent"
                }
            session: Current conversation session for context
        """
        actions = action_plan.get("actions", [])
        execution_mode = action_plan.get("execution_mode", "sequential")
        response_strategy = action_plan.get("response_strategy", "after_completion")

        if not actions:
            log.warning("empty_action_plan")
            return

        await self.bus.publish(
            KaorukoEvent.EXEC_ACTION_STARTED,
            data={"action_count": len(actions), "mode": execution_mode},
            source="executor",
        )

        # ── Confirmation check ─────────────────────────────────────────────────
        requires_confirmation = any(a.get("requires_confirmation", False) for a in actions)
        if requires_confirmation and self.config.security.confirm_destructive:
            confirmed = await self._confirmation.ask(
                action_plan=action_plan,
                method="voice_confirm",
            )
            if not confirmed:
                log.info("action_cancelled_by_user")
                await self.bus.publish(
                    KaorukoEvent.EXEC_ACTION_CANCELLED,
                    data={"reason": "user_declined"},
                    source="executor",
                )
                return

        # ── Execute ────────────────────────────────────────────────────────────
        if execution_mode == "parallel":
            results = await self._execute_parallel(actions)
        else:
            results = await self._execute_sequential(actions)

        # ── Results ────────────────────────────────────────────────────────────
        all_success = all(r.success for r in results)
        combined_response = self._build_response(results, all_success)

        if all_success:
            await self.bus.publish(
                KaorukoEvent.EXEC_ACTION_SUCCESS,
                data={
                    "results": [{"handler": r.handler, "method": r.method} for r in results],
                    "response_text": combined_response,
                    "duration_ms": sum(r.duration_ms for r in results),
                },
                source="executor",
            )
        else:
            failed = [r for r in results if not r.success]
            await self.bus.publish(
                KaorukoEvent.EXEC_ACTION_FAILED,
                data={
                    "error_message": failed[0].error if failed else "Unknown error",
                    "response_text": combined_response,
                },
                source="executor",
            )

        # Persist action to memory
        self._log_to_memory(results, session)

    # ── Execution modes ───────────────────────────────────────────────────────

    async def _execute_sequential(
        self, actions: list[dict]
    ) -> list[ActionResult]:
        results = []
        for action in actions:
            result = await self._dispatch_action(action)
            results.append(result)
            if not result.success:
                log.warning(
                    "sequential_action_failed",
                    handler=action.get("handler"),
                    method=action.get("method"),
                    error=result.error,
                )
                break   # Stop on first failure in sequential mode
        return results

    async def _execute_parallel(
        self, actions: list[dict]
    ) -> list[ActionResult]:
        tasks = [self._dispatch_action(a) for a in actions]
        return list(await asyncio.gather(*tasks, return_exceptions=False))

    # ── Single action dispatch ────────────────────────────────────────────────

    async def _dispatch_action(self, action: dict) -> ActionResult:
        handler_name = action.get("handler", "")
        method_name = action.get("method", "")
        params = action.get("params", {})

        start = time.perf_counter()

        handler = self._registry.get(handler_name)
        if not handler:
            log.error("handler_not_found", handler=handler_name)
            return ActionResult(
                success=False,
                handler=handler_name,
                method=method_name,
                params=params,
                error=f"Unknown handler: {handler_name}",
                duration_ms=0,
            )

        try:
            method = getattr(handler, method_name, None)
            if not method:
                raise AttributeError(f"Handler '{handler_name}' has no method '{method_name}'")

            log.info(
                "action_dispatching",
                handler=handler_name,
                method=method_name,
                params={k: v for k, v in params.items() if k != "password"},
            )

            if asyncio.iscoroutinefunction(method):
                response_text = await method(**params)
            else:
                response_text = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: method(**params)
                )

            duration_ms = (time.perf_counter() - start) * 1000
            log.info(
                "action_success",
                handler=handler_name,
                method=method_name,
                ms=round(duration_ms, 1),
            )
            return ActionResult(
                success=True,
                handler=handler_name,
                method=method_name,
                params=params,
                response_text=response_text or "",
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            log.error(
                "action_failed",
                handler=handler_name,
                method=method_name,
                error=str(e),
            )
            return ActionResult(
                success=False,
                handler=handler_name,
                method=method_name,
                params=params,
                error=str(e),
                duration_ms=duration_ms,
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_response(
        self, results: list[ActionResult], all_success: bool
    ) -> str:
        """Aggregate response text from multiple action results."""
        texts = [r.response_text for r in results if r.response_text]
        if texts:
            return " ".join(texts)
        if all_success:
            return "Done~"
        failed = next((r for r in results if not r.success), None)
        return f"Gomen nasai~ {failed.error}" if failed else "Something went wrong~"

    def _log_to_memory(
        self,
        results: list[ActionResult],
        session: Optional["Session"],
    ) -> None:
        """Log action stats to memory for habit learning (fire-and-forget)."""
        for result in results:
            if result.success and result.handler == "app_control":
                app = result.params.get("app_name", "")
                if app:
                    asyncio.create_task(
                        self.memory.remember(
                            key=f"app_usage_{app}",
                            value={"last_opened": time.time()},
                            memory_type="preference",
                            tags=["app_usage"],
                        )
                    )
