"""
kaoruko/execution/registry.py

Handler registry — maps handler names to handler instances.
All execution handlers register here at startup.
"""
from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

from kaoruko.infrastructure.logging.logger import get_logger

if TYPE_CHECKING:
    from kaoruko.infrastructure.config.schema import KaorukoConfig
    from kaoruko.core.event_bus import EventBus

log = get_logger("execution.registry")


class HandlerRegistry:
    def __init__(self, config: "KaorukoConfig", bus: "EventBus") -> None:
        self.config = config
        self.bus = bus
        self.handlers: dict[str, Any] = {}

    def register(self, name: str, handler: Any) -> None:
        self.handlers[name] = handler
        log.debug("handler_registered", name=name)

    def get(self, name: str) -> Optional[Any]:
        return self.handlers.get(name)

    def register_all(self) -> None:
        """Instantiate and register all built-in handlers."""
        from kaoruko.execution.handlers.app_control       import AppControlHandler
        from kaoruko.execution.handlers.system_control    import SystemControlHandler
        from kaoruko.execution.handlers.audio_control     import AudioControlHandler
        from kaoruko.execution.handlers.browser_control   import BrowserControlHandler
        from kaoruko.execution.handlers.file_manager      import FileManagerHandler
        from kaoruko.execution.handlers.keyboard_control  import KeyboardControlHandler
        from kaoruko.execution.handlers.network_control   import NetworkControlHandler
        from kaoruko.execution.handlers.window_manager    import WindowManagerHandler
        from kaoruko.execution.handlers.notification      import NotificationHandler
        from kaoruko.execution.handlers.assistant_handler import AssistantHandler
        from kaoruko.execution.handlers.workflow_engine   import WorkflowEngine

        self.register("app_control",      AppControlHandler(self.config))
        self.register("system_control",   SystemControlHandler(self.config, self.bus))
        self.register("audio_control",    AudioControlHandler(self.config))
        self.register("browser_control",  BrowserControlHandler(self.config))
        self.register("file_manager",     FileManagerHandler(self.config))
        self.register("keyboard_control", KeyboardControlHandler(self.config))
        self.register("network_control",  NetworkControlHandler(self.config))
        self.register("window_manager",   WindowManagerHandler(self.config))
        self.register("notification",     NotificationHandler(self.config))
        self.register("assistant",        AssistantHandler(self.config, self.bus))

        # Workflow engine needs reference to registry for sub-dispatching
        wf_engine = WorkflowEngine(self.config)
        wf_engine.set_registry(self)
        self.register("workflow",         wf_engine)

        log.info("all_handlers_registered", count=len(self.handlers))
