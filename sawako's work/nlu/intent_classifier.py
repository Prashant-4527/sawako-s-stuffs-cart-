"""
kaoruko/nlu/intent_classifier.py

3-layer hybrid intent classification pipeline with Layer 0 for plugins:

Layer 0 — Plugin Check  : Injected PluginManager checks registered intents, <1ms
Layer 1 — Rule Engine   : Regex + keyword matching, handles ~80% of commands, <5ms
Layer 2 — Local NLU     : Sentence embeddings similarity, handles ~15%, <50ms
Layer 3 — AI Planner    : Claude/Ollama for complex/ambiguous, handles ~5%, <800ms

Fix applied:
  - Added Layer 0 that checks plugin-registered intents via PluginManager.
    This replaces the ad-hoc keyword map in assistant._check_plugins() that
    bypassed the NLU pipeline entirely. Now plugin hits are logged, metered,
    and subject to the same confidence/routing logic as everything else.
  - plugin_response field added to ClassificationResult to carry the plugin's
    response text back through the pipeline to the assistant.
  - set_plugin_manager() setter allows late injection (plugin manager is loaded
    after NLU engine in the startup sequence).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

from kaoruko.infrastructure.logging.logger import get_logger
from kaoruko.nlu.rule_engine import RuleEngine, RuleResult
from kaoruko.nlu.language_detector import LanguageDetector

if TYPE_CHECKING:
    from kaoruko.infrastructure.config.schema import KaorukoConfig
    from kaoruko.intelligence.ai_router import AIRouter
    from kaoruko.core.session import Session
    from kaoruko.plugins.plugin_base import PluginManager

log = get_logger("nlu.intent_classifier")


@dataclass
class ClassificationResult:
    intent: str
    entities: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    action_plan: Optional[dict] = None
    response_text: Optional[str] = None
    plugin_response: Optional[str] = None  # FIX: carries plugin handler output
    layer_used: str = "rule"
    processing_ms: float = 0.0


class IntentClassifier:
    """
    Unified intent classification facade.
    Delegates to the appropriate processing layer based on confidence.
    """

    RULE_CONFIDENCE_THRESHOLD  = 0.85
    LOCAL_CONFIDENCE_THRESHOLD = 0.70

    def __init__(
        self,
        config: "KaorukoConfig",
        ai_router: "AIRouter",
    ) -> None:
        self.config = config
        self.ai_router = ai_router
        self._rule_engine = RuleEngine()
        self._lang_detector = LanguageDetector()
        self._local_nlu: Optional[Any] = None
        self._plugin_manager: Optional["PluginManager"] = None  # injected post-init
        self._initialized = False

    def set_plugin_manager(self, plugin_manager: "PluginManager") -> None:
        """
        Inject the PluginManager after it is loaded.

        FIX: PluginManager is initialized after IntentClassifier in the startup
        sequence, so we use a setter rather than requiring it in __init__.
        """
        self._plugin_manager = plugin_manager
        log.info(
            "plugin_manager_injected",
            plugin_count=len(plugin_manager),
            plugin_intents=list(plugin_manager.get_all_intents()),
        )

    async def initialize(self) -> None:
        """Load NLU models (sentence-transformers etc.)."""
        self._rule_engine.load()
        self._lang_detector.initialize()
        self._initialized = True
        log.info("intent_classifier_ready", layers=["plugin", "rule", "local_nlu", "ai"])

    async def classify(
        self,
        text: str,
        language: str = "en",
        context: Optional["Session"] = None,
    ) -> ClassificationResult:
        """
        Classify user text into intent + entities + action plan.

        Layer 0: Plugin quick-check (sub-millisecond, no I/O for matching)
        Layer 1: Rule engine (regex, <5ms)
        Layer 2: Local NLU (embeddings, <50ms)
        Layer 3: AI (Claude/Ollama, <800ms)
        """
        start = time.perf_counter()
        text = text.strip()

        if not text:
            return ClassificationResult(intent="CONV_EMPTY", confidence=1.0)

        # ── Layer 0: Plugin intent check ──────────────────────────────────────
        # FIX: This replaces the keyword-map bypass in assistant._check_plugins().
        # Plugin intent matching is now part of the pipeline, not a side-channel.
        if self._plugin_manager:
            plugin_result = await self._classify_plugin(text, language, context, start)
            if plugin_result is not None:
                return plugin_result

        # ── Layer 1: Rule Engine ──────────────────────────────────────────────
        rule_result = self._rule_engine.match(text, language=language)
        if rule_result and rule_result.confidence >= self.RULE_CONFIDENCE_THRESHOLD:
            elapsed = (time.perf_counter() - start) * 1000
            log.info(
                "intent_resolved",
                layer="rule",
                intent=rule_result.intent,
                confidence=rule_result.confidence,
                ms=round(elapsed, 1),
            )
            return ClassificationResult(
                intent=rule_result.intent,
                entities=rule_result.entities,
                confidence=rule_result.confidence,
                action_plan=rule_result.action_plan,
                response_text=rule_result.response_text,
                layer_used="rule",
                processing_ms=elapsed,
            )

        # ── Layer 2: Local NLU (sentence-transformers) ────────────────────────
        try:
            local_result = await self._classify_local(text, language)
            if local_result and local_result.confidence >= self.LOCAL_CONFIDENCE_THRESHOLD:
                elapsed = (time.perf_counter() - start) * 1000
                log.info(
                    "intent_resolved",
                    layer="local_nlu",
                    intent=local_result.intent,
                    confidence=local_result.confidence,
                    ms=round(elapsed, 1),
                )
                local_result.processing_ms = elapsed
                return local_result
        except Exception as e:
            log.warning("local_nlu_error", error=str(e))

        # ── Layer 3: AI Planner (Claude/Ollama) ───────────────────────────────
        try:
            ai_result = await self._classify_ai(text, language, context)
            elapsed = (time.perf_counter() - start) * 1000
            log.info(
                "intent_resolved",
                layer="ai",
                intent=ai_result.intent,
                confidence=ai_result.confidence,
                ms=round(elapsed, 1),
            )
            ai_result.processing_ms = elapsed
            return ai_result
        except Exception as e:
            log.error("ai_classification_error", error=str(e))

        # ── Fallback ──────────────────────────────────────────────────────────
        elapsed = (time.perf_counter() - start) * 1000
        return ClassificationResult(
            intent="CONV_CHAT",
            entities={"original_text": text},
            confidence=0.5,
            layer_used="fallback",
            processing_ms=elapsed,
        )

    async def _classify_plugin(
        self,
        text: str,
        language: str,
        context: Optional["Session"],
        start: float,
    ) -> Optional[ClassificationResult]:
        """
        Layer 0: Check if any loaded plugin handles an intent matched
        in the text. Uses the PluginManager's registered intent set
        cross-checked against a lightweight keyword scan.

        Returns a ClassificationResult with plugin_response set if a
        plugin handles the intent, or None to continue to Layer 1.
        """
        if not self._plugin_manager:
            return None

        plugin_intents = self._plugin_manager.get_all_intents()
        if not plugin_intents:
            return None

        # Lightweight keyword → intent map (same logic that was in assistant.py,
        # but now it's properly part of the NLU pipeline with metrics and logging)
        text_lower = text.lower()
        KEYWORD_INTENT_MAP = {
            "battery":      "GET_BATTERY",
            "disk space":   "GET_DISK_SPACE",
            "ip address":   "GET_IP",
            "cpu":          "GET_CPU",
            "ram":          "GET_RAM",
            "memory usage": "GET_RAM",
            "weather":      "GET_WEATHER",
            "temperature":  "GET_TEMPERATURE",
            "forecast":     "GET_FORECAST",
        }

        matched_intent: Optional[str] = None
        for keyword, intent in KEYWORD_INTENT_MAP.items():
            if keyword in text_lower and intent in plugin_intents:
                matched_intent = intent
                break

        if not matched_intent:
            return None

        plugin = self._plugin_manager.get_handler_for_intent(matched_intent)
        if not plugin:
            return None

        try:
            response = await self._plugin_manager.call_handle_intent(
                plugin=plugin,
                intent=matched_intent,
                entities={},
                session=context,
            )
            if response is None:
                return None

            elapsed = (time.perf_counter() - start) * 1000
            log.info(
                "intent_resolved",
                layer="plugin",
                intent=matched_intent,
                plugin=plugin.name,
                confidence=0.98,
                ms=round(elapsed, 1),
            )
            return ClassificationResult(
                intent=matched_intent,
                entities={},
                confidence=0.98,
                action_plan=None,
                response_text=None,
                plugin_response=response,  # Carry plugin output back to assistant
                layer_used="plugin",
                processing_ms=elapsed,
            )
        except Exception as e:
            log.warning("plugin_intent_error", intent=matched_intent, error=str(e))
            return None

    async def _classify_local(
        self, text: str, language: str
    ) -> Optional[ClassificationResult]:
        """Use sentence-transformers similarity matching."""
        if self._local_nlu is None:
            self._local_nlu = await self._load_local_nlu()
        if self._local_nlu is None:
            return None
        return await self._local_nlu.classify(text, language)

    async def _load_local_nlu(self):
        """Load the local NLU model (sentence-transformers)."""
        try:
            from kaoruko.nlu.local_nlu import LocalNLU
            nlu = LocalNLU()
            await nlu.initialize()
            log.info("local_nlu_loaded")
            return nlu
        except ImportError:
            log.warning("local_nlu_unavailable", reason="sentence-transformers not installed")
            return None
        except Exception as e:
            log.error("local_nlu_load_error", error=str(e))
            return None

    async def _classify_ai(
        self,
        text: str,
        language: str,
        context: Optional["Session"],
    ) -> ClassificationResult:
        """Use Claude/Ollama for complex intent resolution."""
        history = context.to_history_messages()[-4:] if context else []

        response = await self.ai_router.classify_intent(
            text=text,
            language=language,
            conversation_history=history,
        )
        return ClassificationResult(
            intent=response.get("intent", "CONV_CHAT"),
            entities=response.get("entities", {}),
            confidence=response.get("confidence", 0.7),
            action_plan=response.get("action_plan"),
            response_text=response.get("response_text"),
            layer_used="ai",
        )
