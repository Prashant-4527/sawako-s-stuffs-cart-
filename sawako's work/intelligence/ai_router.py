"""
kaoruko/intelligence/ai_router.py

Routes AI requests to the appropriate provider.
Primary: Claude (Anthropic API) with function calling
Fallback: Ollama (local LLM)

Fixes applied:
  - Internet check now uses a TTL cache (30s) — was permanent after first check
  - SecretsManager no longer uses hardcoded Path.cwd() — project_root injected
  - Claude API calls now retry up to 3 times with exponential backoff
  - _should_use_claude() resets cached state on network errors
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from kaoruko.infrastructure.logging.logger import get_logger

if TYPE_CHECKING:
    from kaoruko.infrastructure.config.schema import KaorukoConfig

log = get_logger("intelligence.ai_router")


# ── Kaoruko System Prompt ─────────────────────────────────────────────────────

_KAORUKO_SYSTEM_PROMPT = """You are Kaoruko (香子), an elegant and intelligent female AI desktop assistant.

PERSONALITY:
- Calm, warm, and professional with a subtle Japanese-inspired speaking style
- Use gentle Japanese expressions occasionally (Hai, Wakarimashita, Gomen nasai)
- Precise and helpful; never verbose
- Address the user with warmth and respect

YOUR CAPABILITIES:
- Open/close/switch desktop applications
- Control system settings (volume, brightness, WiFi, Bluetooth, power)
- File system operations (open, create, move, search files/folders)
- Browser control (open URLs, search the web)
- Keyboard and mouse automation
- Multi-step workflow execution
- Answer questions and have intelligent conversations

RESPONSE FORMAT (when executing actions):
You MUST respond with a JSON object in this exact format when the user wants an action executed:
{
  "intent": "INTENT_NAME",
  "entities": {"key": "value"},
  "confidence": 0.95,
  "action_plan": {
    "actions": [
      {
        "handler": "handler_name",
        "method": "method_name",
        "params": {},
        "requires_confirmation": false
      }
    ],
    "execution_mode": "sequential",
    "response_strategy": "after_completion"
  },
  "response_text": "What you will say to the user~",
  "requires_confirmation": false
}

For conversation (no action needed):
{
  "intent": "CONV_CHAT",
  "entities": {},
  "confidence": 0.9,
  "action_plan": null,
  "response_text": "Your conversational response here~"
}

IMPORTANT:
- ALWAYS respond with valid JSON only
- For destructive actions (delete, shutdown), set requires_confirmation to true
- Keep response_text brief and warm in Kaoruko's voice
- If the user speaks Hinglish or Japanese, respond in the same language mix"""


# ── Function Definitions for Claude Tool Use ──────────────────────────────────

_EXECUTE_ACTION_TOOL = {
    "name": "execute_desktop_action",
    "description": "Execute a desktop control action",
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "description": "The classified intent",
                "enum": [
                    "APP_OPEN", "APP_CLOSE", "APP_SWITCH",
                    "BROWSER_OPEN", "BROWSER_SEARCH", "BROWSER_TAB",
                    "SYS_VOLUME", "SYS_MUTE", "SYS_SHUTDOWN", "SYS_RESTART",
                    "SYS_SLEEP", "SYS_LOCK", "SYS_BRIGHTNESS",
                    "SYS_WIFI", "SYS_BLUETOOTH",
                    "FILE_OPEN", "FILE_CREATE", "FILE_DELETE",
                    "FILE_MOVE", "FILE_RENAME", "FILE_SEARCH",
                    "MEDIA_PLAY", "MEDIA_PAUSE", "MEDIA_NEXT",
                    "WORKFLOW_START", "WORKFLOW_STOP",
                    "MEM_REMIND", "MEM_NOTE", "MEM_RECALL",
                    "CONV_CHAT", "CONV_EXPLAIN", "CONV_CALCULATE",
                    "META_SETTINGS", "META_HELP", "META_STOP", "META_STATUS",
                    # Plugin intents
                    "GET_BATTERY", "GET_DISK_SPACE", "GET_IP",
                    "GET_CPU", "GET_RAM", "GET_WEATHER",
                    "GET_TEMPERATURE", "GET_FORECAST",
                ],
            },
            "entities": {
                "type": "object",
                "description": "Extracted entities from the command",
            },
            "action_plan": {
                "type": "object",
                "description": "Structured execution plan",
                "properties": {
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "handler": {"type": "string"},
                                "method": {"type": "string"},
                                "params": {"type": "object"},
                                "requires_confirmation": {"type": "boolean"},
                            },
                        },
                    },
                    "execution_mode": {
                        "type": "string",
                        "enum": ["sequential", "parallel", "conditional"],
                    },
                },
            },
            "response_text": {
                "type": "string",
                "description": "What Kaoruko should say to the user",
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "requires_confirmation": {"type": "boolean"},
        },
        "required": ["intent", "response_text", "confidence"],
    },
}


# ── Retry helpers ─────────────────────────────────────────────────────────────

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 0.5   # seconds


async def _with_retry(coro_factory, max_retries: int = _MAX_RETRIES):
    """
    Call coro_factory() up to max_retries times with exponential backoff.
    Retries on rate-limit (429) and transient server errors (5xx).
    Raises the last exception if all attempts fail.

    FIX: Was a single call with no retry — transient 429s would fall
    through to Ollama unnecessarily.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except Exception as exc:
            last_exc = exc
            exc_str = str(exc).lower()
            # Retry on rate-limit or server errors only
            if "rate" in exc_str or "429" in exc_str or "500" in exc_str or "503" in exc_str:
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                log.warning(
                    "claude_retry",
                    attempt=attempt + 1,
                    max=max_retries,
                    delay=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)
            else:
                raise  # Non-retriable error — surface immediately
    raise last_exc


class AIRouter:
    """
    Routes AI requests to Claude (primary) or Ollama (fallback).
    Implements function calling for structured action planning.
    """

    # FIX: Internet check TTL — was cached forever after first check.
    # If network drops mid-session, Claude would remain "unavailable" for the
    # entire run. Now re-checks every 30 seconds.
    _INTERNET_CACHE_TTL = 30.0  # seconds

    def __init__(self, config: "KaorukoConfig", project_root: Optional[Path] = None) -> None:
        self.config = config
        # FIX: Accept explicit project_root instead of hardcoding Path.cwd().
        # Path.cwd() is unreliable when launched from a shortcut or different directory.
        self._project_root = project_root or Path.cwd()
        self._claude_client = None
        self._ollama_client = None
        self._internet_available: Optional[bool] = None
        self._internet_last_checked: float = 0.0   # FIX: TTL tracking

    # ── Main Interface ────────────────────────────────────────────────────────

    async def classify_intent(
        self,
        text: str,
        language: str = "en",
        conversation_history: Optional[list[dict]] = None,
    ) -> dict[str, Any]:
        """
        Use AI to classify intent and build action plan.
        Returns structured dict compatible with ClassificationResult.
        """
        if await self._should_use_claude():
            try:
                return await _with_retry(
                    lambda: self._classify_with_claude(text, language, conversation_history)
                )
            except Exception as e:
                log.warning("claude_exhausted_retries", error=str(e), fallback="ollama")
                self._invalidate_internet_cache()  # Force re-check on next call

        return await self._classify_with_ollama(text, language, conversation_history)

    async def chat(
        self,
        message: str,
        system_context: Optional[str] = None,
        history: Optional[list[dict]] = None,
    ) -> str:
        """General conversation response."""
        if await self._should_use_claude():
            try:
                return await _with_retry(
                    lambda: self._chat_with_claude(message, system_context, history)
                )
            except Exception as e:
                log.warning("claude_chat_exhausted_retries", error=str(e))
                self._invalidate_internet_cache()

        return await self._chat_with_ollama(message, history)

    # ── Claude Integration ────────────────────────────────────────────────────

    async def _classify_with_claude(
        self,
        text: str,
        language: str,
        history: Optional[list[dict]],
    ) -> dict[str, Any]:
        client = await self._get_claude_client()
        messages = self._build_messages(text, history)

        response = await client.messages.create(
            model=self.config.ai.model,
            max_tokens=self.config.ai.max_tokens,
            system=_KAORUKO_SYSTEM_PROMPT,
            messages=messages,
            tools=[_EXECUTE_ACTION_TOOL],
            tool_choice={"type": "auto"},
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "execute_desktop_action":
                result = dict(block.input)
                log.info(
                    "claude_classified",
                    intent=result.get("intent"),
                    confidence=result.get("confidence"),
                )
                return result

        text_response = " ".join(
            b.text for b in response.content if hasattr(b, "text")
        )
        return {
            "intent": "CONV_CHAT",
            "entities": {},
            "confidence": 0.85,
            "action_plan": None,
            "response_text": text_response,
        }

    async def _chat_with_claude(
        self,
        message: str,
        system_context: Optional[str],
        history: Optional[list[dict]],
    ) -> str:
        client = await self._get_claude_client()
        messages = self._build_messages(message, history)
        system = (
            f"{_KAORUKO_SYSTEM_PROMPT}\n\n{system_context}"
            if system_context
            else _KAORUKO_SYSTEM_PROMPT
        )

        response = await client.messages.create(
            model=self.config.ai.model,
            max_tokens=512,
            system=system,
            messages=messages,
        )
        return response.content[0].text if response.content else "Gomen nasai~"

    async def _get_claude_client(self):
        if self._claude_client is None:
            import anthropic
            from kaoruko.security.secrets_manager import SecretsManager
            # FIX: Use injected project_root, not Path.cwd()
            secrets = SecretsManager(self._project_root)
            api_key = secrets.get("anthropic_api_key") or ""
            self._claude_client = anthropic.AsyncAnthropic(api_key=api_key)
        return self._claude_client

    # ── Ollama Integration ────────────────────────────────────────────────────

    async def _classify_with_ollama(
        self,
        text: str,
        language: str,
        history: Optional[list[dict]],
    ) -> dict[str, Any]:
        """Use local Ollama for offline intent classification."""
        try:
            import ollama
            prompt = self._build_ollama_prompt(text, history)
            response = await ollama.AsyncClient().chat(
                model=self.config.ai.offline_model,
                messages=[
                    {"role": "system", "content": _KAORUKO_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                format="json",
            )
            content = response["message"]["content"]
            result = json.loads(content)
            log.info("ollama_classified", intent=result.get("intent"))
            return result
        except Exception as e:
            log.error("ollama_failed", error=str(e))
            return {
                "intent": "CONV_CHAT",
                "entities": {},
                "confidence": 0.5,
                "action_plan": None,
                "response_text": "Gomen nasai, I had trouble understanding that~",
            }

    async def _chat_with_ollama(
        self, message: str, history: Optional[list[dict]]
    ) -> str:
        try:
            import ollama
            messages = [{"role": "system", "content": _KAORUKO_SYSTEM_PROMPT}]
            if history:
                messages.extend(history[-4:])
            messages.append({"role": "user", "content": message})

            response = await ollama.AsyncClient().chat(
                model=self.config.ai.offline_model,
                messages=messages,
            )
            return response["message"]["content"]
        except Exception as e:
            log.error("ollama_chat_error", error=str(e))
            return "Gomen nasai~ I am having trouble connecting to my local AI model."

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _should_use_claude(self) -> bool:
        if self.config.ai.primary != "claude":
            return False
        return await self._check_internet()

    async def _check_internet(self) -> bool:
        """
        FIX: Internet availability is now TTL-cached (30s) instead of
        being cached forever after the first check.
        Previously: check once, never re-check → stuck if network drops.
        Now: re-check every 30 seconds, reset on API error.
        """
        now = time.monotonic()
        if (
            self._internet_available is not None
            and (now - self._internet_last_checked) < self._INTERNET_CACHE_TTL
        ):
            return self._internet_available

        try:
            import httpx
            async with httpx.AsyncClient(timeout=2.0) as client:
                await client.head("https://api.anthropic.com")
            self._internet_available = True
        except Exception:
            self._internet_available = False

        self._internet_last_checked = now
        return self._internet_available

    def _invalidate_internet_cache(self) -> None:
        """Force internet re-check on next call (called after API errors)."""
        self._internet_available = None
        self._internet_last_checked = 0.0

    def _build_messages(
        self, text: str, history: Optional[list[dict]]
    ) -> list[dict[str, str]]:
        messages = []
        if history:
            messages.extend(history[-6:])
        messages.append({"role": "user", "content": text})
        return messages

    def _build_ollama_prompt(
        self, text: str, history: Optional[list[dict]]
    ) -> str:
        context = ""
        if history:
            recent = history[-4:]
            context = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in recent)
            context = f"Recent conversation:\n{context}\n\n"
        return f"{context}User command: {text}\n\nRespond with JSON only."
