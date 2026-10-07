"""
kaoruko/execution/handlers/workflow_engine.py

Built-in workflow engine.
Executes named multi-step automation sequences ("modes").

Built-in workflows:
  study_mode   — VS Code + browser + focus playlist + ChatGPT
  gaming_mode  — Steam + Discord + Spotify + mute notifications
  work_mode    — Slack + Chrome + VS Code + calendar
  meeting_mode — Zoom/Teams + mute + camera check
  movie_mode   — VLC + dim lights + mute notifications

Users can define custom workflows via config.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TYPE_CHECKING

from kaoruko.infrastructure.logging.logger import get_logger

if TYPE_CHECKING:
    from kaoruko.infrastructure.config.schema import KaorukoConfig

log = get_logger("execution.workflow_engine")


@dataclass
class WorkflowStep:
    handler: str
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    delay_ms: int = 500          # Pause between steps
    optional: bool = False       # If True, failure doesn't stop workflow


@dataclass
class Workflow:
    name: str
    display_name: str
    description: str
    steps: list[WorkflowStep]
    response: str = ""


# ── Built-in workflow definitions ─────────────────────────────────────────────

_BUILT_IN_WORKFLOWS: dict[str, Workflow] = {

    "study": Workflow(
        name="study",
        display_name="Study Mode",
        description="Open development tools and study resources",
        steps=[
            WorkflowStep("app_control",     "open_application", {"app_name": "code"}),
            WorkflowStep("browser_control", "open_url",         {"url": "https://chatgpt.com"}, delay_ms=800),
            WorkflowStep("browser_control", "open_url",         {"url": "https://youtube.com"}, delay_ms=400),
            WorkflowStep("audio_control",   "set_volume",       {"level": 40}),
        ],
        response="Study mode activated~ VS Code, ChatGPT, and YouTube are ready. Good luck~",
    ),

    "gaming": Workflow(
        name="gaming",
        display_name="Gaming Mode",
        description="Launch gaming apps and optimize for performance",
        steps=[
            WorkflowStep("app_control",   "open_application", {"app_name": "steam"}),
            WorkflowStep("app_control",   "open_application", {"app_name": "discord"}, delay_ms=1000),
            WorkflowStep("app_control",   "open_application", {"app_name": "spotify"}, delay_ms=800),
            WorkflowStep("audio_control", "set_volume",       {"level": 70}),
        ],
        response="Gaming mode on~ Steam, Discord, and Spotify are launching. Have fun~",
    ),

    "work": Workflow(
        name="work",
        display_name="Work Mode",
        description="Open productivity apps for a work session",
        steps=[
            WorkflowStep("app_control",     "open_application", {"app_name": "code"}),
            WorkflowStep("app_control",     "open_application", {"app_name": "slack"}, delay_ms=600),
            WorkflowStep("browser_control", "open_url",         {"url": "https://gmail.com"}, delay_ms=600),
            WorkflowStep("audio_control",   "set_volume",       {"level": 30}),
        ],
        response="Work mode ready~ VS Code, Slack, and Gmail are opening. Let's be productive~",
    ),

    "movie": Workflow(
        name="movie",
        display_name="Movie Mode",
        description="Set up for watching a movie",
        steps=[
            WorkflowStep("app_control",   "open_application", {"app_name": "vlc"}, optional=True),
            WorkflowStep("audio_control", "set_volume",       {"level": 80}),
            WorkflowStep("system_control","set_brightness",   {"level": 20}),
        ],
        response="Movie mode set~ VLC is opening, brightness lowered, volume up. Enjoy~",
    ),

    "focus": Workflow(
        name="focus",
        display_name="Focus Mode",
        description="Minimize distractions for deep work",
        steps=[
            WorkflowStep("app_control",    "close_application", {"app_name": "discord"}, optional=True),
            WorkflowStep("app_control",    "close_application", {"app_name": "spotify"}, optional=True),
            WorkflowStep("audio_control",  "set_mute",          {"mute": True}),
            WorkflowStep("system_control", "set_brightness",    {"level": 70}),
        ],
        response="Focus mode activated~ Distractions cleared. You've got this~",
    ),

    "morning": Workflow(
        name="morning",
        display_name="Morning Routine",
        description="Start your morning with news and productivity",
        steps=[
            WorkflowStep("browser_control", "open_url", {"url": "https://news.ycombinator.com"}),
            WorkflowStep("browser_control", "open_url", {"url": "https://gmail.com"}, delay_ms=600),
            WorkflowStep("audio_control",   "set_volume", {"level": 35}),
            WorkflowStep("system_control",  "set_brightness", {"level": 85}),
        ],
        response="Ohayou gozaimasu~ Your morning setup is ready. Have a wonderful day~",
    ),
}

# Aliases for natural language matching
_WORKFLOW_ALIASES: dict[str, str] = {
    "study mode":   "study",
    "study":        "study",
    "gaming mode":  "gaming",
    "gaming":       "gaming",
    "game mode":    "gaming",
    "work mode":    "work",
    "work":         "work",
    "movie mode":   "movie",
    "movie":        "movie",
    "focus mode":   "focus",
    "focus":        "focus",
    "morning":      "morning",
    "morning routine": "morning",
    "good morning": "morning",
}


class WorkflowEngine:
    """
    Executes named multi-step workflows.
    Registered as the 'workflow' handler in the registry.
    """

    def __init__(self, config: "KaorukoConfig", registry=None) -> None:
        self.config = config
        self._registry = registry
        self._custom_workflows: dict[str, Workflow] = {}

    def set_registry(self, registry) -> None:
        self._registry = registry

    async def start_workflow(self, app_name: str = "", **kwargs) -> str:
        """
        Launch a named workflow.
        app_name is reused as workflow_name for rule_engine compatibility.
        """
        name = app_name.lower().strip()
        workflow_key = _WORKFLOW_ALIASES.get(name) or name

        workflow = (
            _BUILT_IN_WORKFLOWS.get(workflow_key)
            or self._custom_workflows.get(workflow_key)
        )

        if not workflow:
            return (
                f"I don't have a workflow called '{app_name}'~ "
                f"Available modes: study, gaming, work, movie, focus, morning."
            )

        log.info("workflow_starting", name=workflow.name,
                 steps=len(workflow.steps))

        await self._execute_workflow(workflow)
        return workflow.response or f"{workflow.display_name} activated~"

    async def stop_workflow(self, app_name: str = "", **kwargs) -> str:
        """Stop / undo a workflow (best effort)."""
        return f"Deactivating {app_name} mode~"

    async def _execute_workflow(self, workflow: Workflow) -> None:
        """Run all steps in a workflow sequentially with delays."""
        for i, step in enumerate(workflow.steps):
            try:
                if self._registry:
                    handler = self._registry.get(step.handler)
                    if handler:
                        method = getattr(handler, step.method, None)
                        if method:
                            if asyncio.iscoroutinefunction(method):
                                await method(**step.params)
                            else:
                                await asyncio.get_event_loop().run_in_executor(
                                    None, lambda m=method, p=step.params: m(**p)
                                )
                            log.debug("workflow_step_done",
                                      workflow=workflow.name,
                                      step=i,
                                      handler=step.handler,
                                      method=step.method)
            except Exception as e:
                log.error("workflow_step_error",
                          workflow=workflow.name,
                          step=i,
                          error=str(e))
                if not step.optional:
                    break

            if step.delay_ms > 0 and i < len(workflow.steps) - 1:
                await asyncio.sleep(step.delay_ms / 1000)

    def register_custom(self, workflow: Workflow) -> None:
        """Register a user-defined custom workflow."""
        self._custom_workflows[workflow.name] = workflow
        log.info("custom_workflow_registered", name=workflow.name)

    def list_workflows(self) -> list[dict]:
        """Return all available workflows for the UI."""
        result = []
        for key, wf in _BUILT_IN_WORKFLOWS.items():
            result.append({
                "name": wf.name,
                "display_name": wf.display_name,
                "description": wf.description,
                "step_count": len(wf.steps),
                "custom": False,
            })
        for key, wf in self._custom_workflows.items():
            result.append({
                "name": wf.name,
                "display_name": wf.display_name,
                "description": wf.description,
                "step_count": len(wf.steps),
                "custom": True,
            })
        return result
