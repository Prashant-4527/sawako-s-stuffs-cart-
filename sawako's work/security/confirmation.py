"""
kaoruko/security/confirmation.py

Confirmation gate system for destructive / sensitive actions.
Methods: voice_confirm, ui_confirm, pin_confirm.
"""
from __future__ import annotations
import asyncio
from typing import Any, Optional, TYPE_CHECKING
from kaoruko.infrastructure.logging.logger import get_logger
if TYPE_CHECKING:
    from kaoruko.infrastructure.config.schema import KaorukoConfig
    from kaoruko.core.event_bus import EventBus

log = get_logger("security.confirmation")


class ConfirmationGate:
    def __init__(self, config: "KaorukoConfig", bus: "EventBus") -> None:
        self.config = config
        self.bus = bus

    async def ask(
        self,
        action_plan: dict[str, Any],
        method: str = "voice_confirm",
        timeout: float = 10.0,
    ) -> bool:
        """
        Ask for user confirmation.
        Returns True if confirmed, False if denied or timed out.
        """
        from kaoruko.core.event_bus import KaorukoEvent

        action_name = self._describe(action_plan)
        log.info("confirmation_requested", action=action_name, method=method)

        await self.bus.publish(
            KaorukoEvent.EXEC_CONFIRM_REQUIRED,
            data={"action": action_name, "method": method},
            source="confirmation_gate",
        )

        if method == "voice_confirm":
            return await self._voice_confirm(action_name, timeout)
        elif method == "ui_confirm":
            return await self._ui_confirm(action_name, timeout)
        return False

    async def _voice_confirm(self, action_name: str, timeout: float) -> bool:
        from kaoruko.core.event_bus import KaorukoEvent
        event = await self.bus.wait_for(
            KaorukoEvent.EXEC_CONFIRM_RECEIVED,
            timeout=timeout,
        )
        if event:
            confirmed = event.data.get("confirmed", False)
            log.info("confirmation_result", action=action_name, confirmed=confirmed)
            return confirmed
        log.info("confirmation_timeout", action=action_name)
        return False

    async def _ui_confirm(self, action_name: str, timeout: float) -> bool:
        # Wait for UI confirmation event
        return await self._voice_confirm(action_name, timeout)

    def _describe(self, plan: dict) -> str:
        actions = plan.get("actions", [])
        if actions:
            a = actions[0]
            return f"{a.get('handler','?')}.{a.get('method','?')}"
        return "unknown action"
