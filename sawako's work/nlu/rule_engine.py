"""
kaoruko/nlu/rule_engine.py

Layer 1 intent classification: regex + keyword rules.
Handles ~80% of common commands in <5ms with zero AI cost.

Supports English, Hinglish, and Japanese patterns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from kaoruko.infrastructure.logging.logger import get_logger

log = get_logger("nlu.rule_engine")


@dataclass
class RuleResult:
    intent: str
    entities: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.95
    action_plan: Optional[dict] = None
    response_text: Optional[str] = None


# ── Pattern Definitions ───────────────────────────────────────────────────────
# Format: (compiled_pattern, intent, entity_extractor_fn, confidence)

def _app_name(m: re.Match) -> dict:
    """Extract and normalize app name from regex match."""
    raw = m.group("app").strip().lower()
    return {"app_name": _normalize_app(raw)}

def _normalize_app(name: str) -> str:
    """Normalize spoken app names to canonical form."""
    aliases = {
        "chrome": ["chrome", "google chrome", "browser", "web browser", "google"],
        "code": ["vscode", "vs code", "visual studio code", "code editor"],
        "discord": ["discord"],
        "steam": ["steam"],
        "spotify": ["spotify"],
        "notepad": ["notepad", "text editor"],
        "explorer": ["explorer", "file explorer", "files"],
        "terminal": ["terminal", "cmd", "command prompt", "powershell"],
        "youtube": ["youtube"],
        "gmail": ["gmail", "email", "mail"],
    }
    for canonical, variants in aliases.items():
        if any(v in name for v in variants):
            return canonical
    return name.replace(" ", "_")

def _search_query(m: re.Match) -> dict:
    return {"query": m.group("query").strip()}

def _volume_level(m: re.Match) -> dict:
    level = int(m.group("level")) if m.group("level") else None
    direction = m.group("dir") if "dir" in m.groupdict() else None
    return {"level": level, "direction": direction}

def _file_path(m: re.Match) -> dict:
    return {"path": m.group("path").strip()}

def _url(m: re.Match) -> dict:
    return {"url": m.group("url").strip()}


# ── Rule Table ────────────────────────────────────────────────────────────────
# Rules are tested in order; first match wins.
# Pattern groups named: app, query, path, level, dir, url

_RULES: list[tuple[re.Pattern, str, Any, float]] = [

    # ═══════════════════════════════════════════════════════════
    # APPLICATION OPEN — English + Hinglish + Japanese
    # ═══════════════════════════════════════════════════════════
    # ═══════════════════════════════════════════════════════════
    # HIGH-PRIORITY SPECIFIC PATTERNS (before generic APP_OPEN)
    # ═══════════════════════════════════════════════════════════

    # Settings (must come before APP_OPEN to prevent "settings" being treated as app)
    (re.compile(r"^(?:open\s+)?settings$", re.IGNORECASE),
     "META_SETTINGS", lambda m: {}, 0.97),

    # Known folder opens (before generic app open)
    (re.compile(
        r"^open\s+(?P<path>downloads|desktop|documents|pictures|music|videos|home)(?:\s+folder)?$",
        re.IGNORECASE,
    ), "FILE_OPEN", lambda m: {"path": m.group("path").lower()}, 0.97),

    # Workflow/mode starts (before generic app open)
    (re.compile(
        r"^(?:start|begin|activate|launch)\s+(?P<app>.+?)\s+mode$",
        re.IGNORECASE,
    ), "WORKFLOW_START", _app_name, 0.95),

    # Help (before any open pattern)
    (re.compile(r"^(?:help|what\s+can\s+you\s+do|commands)$", re.IGNORECASE),
     "META_HELP", lambda m: {}, 0.97),

    (re.compile(
        r"^(?:open|launch|start|run|start up)\s+(?P<app>.+)$",
        re.IGNORECASE,
    ), "APP_OPEN", _app_name, 0.97),

    # Hinglish: "chrome kholo", "VS code open karo", "discord chalu karo"
    (re.compile(
        r"^(?P<app>.+?)\s+(?:kholo|open\s+karo|chalu\s+karo|start\s+karo|chalao)$",
        re.IGNORECASE,
    ), "APP_OPEN", _app_name, 0.97),

    # Japanese: "クロームを開いて" = "open chrome"
    (re.compile(r"^(?P<app>.+?)を開いて$"), "APP_OPEN", _app_name, 0.95),

    # ═══════════════════════════════════════════════════════════
    # APPLICATION CLOSE
    # ═══════════════════════════════════════════════════════════
    (re.compile(
        r"^(?:close|quit|exit|kill|stop)\s+(?P<app>.+)$",
        re.IGNORECASE,
    ), "APP_CLOSE", _app_name, 0.97),

    (re.compile(
        r"^(?P<app>.+?)\s+(?:band\s+karo|close\s+karo|bandh\s+karo)$",
        re.IGNORECASE,
    ), "APP_CLOSE", _app_name, 0.95),

    # ═══════════════════════════════════════════════════════════
    # BROWSER SEARCH
    # ═══════════════════════════════════════════════════════════
    (re.compile(
        r"^(?:search|google|look up|find)\s+(?:for\s+)?(?P<query>.+)$",
        re.IGNORECASE,
    ), "BROWSER_SEARCH", _search_query, 0.95),

    # "Google me X search karo"
    (re.compile(
        r"^(?:google|bing|youtube)\s+(?:me|mein|pe)\s+(?P<query>.+?)\s+search\s+karo$",
        re.IGNORECASE,
    ), "BROWSER_SEARCH", _search_query, 0.95),

    # ═══════════════════════════════════════════════════════════
    # BROWSER OPEN URL
    # ═══════════════════════════════════════════════════════════
    (re.compile(
        r"^(?:open|go to|navigate to|visit)\s+(?P<url>(?:https?://|www\.)\S+)$",
        re.IGNORECASE,
    ), "BROWSER_OPEN", _url, 0.97),

    (re.compile(
        r"^(?:open|go to)\s+(?P<url>[a-zA-Z0-9-]+\.(?:com|org|net|io|co)\S*)$",
        re.IGNORECASE,
    ), "BROWSER_OPEN", _url, 0.95),

    # ═══════════════════════════════════════════════════════════
    # SYSTEM — Volume
    # ═══════════════════════════════════════════════════════════
    (re.compile(
        r"^(?:set\s+)?volume\s+(?:to\s+)?(?P<level>\d{1,3})\s*(?:%|percent)?$",
        re.IGNORECASE,
    ), "SYS_VOLUME", _volume_level, 0.97),

    (re.compile(
        r"^(?P<dir>turn\s+up|increase|louder|raise)\s+(?:the\s+)?volume$",
        re.IGNORECASE,
    ), "SYS_VOLUME", _volume_level, 0.95),

    (re.compile(
        r"^(?P<dir>turn\s+down|decrease|quieter|lower)\s+(?:the\s+)?volume$",
        re.IGNORECASE,
    ), "SYS_VOLUME", _volume_level, 0.95),

    (re.compile(
        r"^(?:mute|silence|shut\s+up)(?:\s+sound)?$",
        re.IGNORECASE,
    ), "SYS_MUTE", lambda m: {"mute": True}, 0.97),

    (re.compile(
        r"^unmute(?:\s+sound)?$",
        re.IGNORECASE,
    ), "SYS_MUTE", lambda m: {"mute": False}, 0.97),

    # ═══════════════════════════════════════════════════════════
    # SYSTEM — Power
    # ═══════════════════════════════════════════════════════════
    (re.compile(
        r"^(?:shutdown|shut\s+down|power\s+off|turn\s+off\s+(?:the\s+)?(?:pc|computer|laptop))$",
        re.IGNORECASE,
    ), "SYS_SHUTDOWN", lambda m: {}, 0.98),

    (re.compile(
        r"^(?:restart|reboot|restart\s+(?:the\s+)?(?:pc|computer))$",
        re.IGNORECASE,
    ), "SYS_RESTART", lambda m: {}, 0.98),

    (re.compile(
        r"^(?:sleep|sleep\s+mode|hibernate)$",
        re.IGNORECASE,
    ), "SYS_SLEEP", lambda m: {}, 0.98),

    (re.compile(
        r"^(?:lock|lock\s+(?:screen|pc|computer))$",
        re.IGNORECASE,
    ), "SYS_LOCK", lambda m: {}, 0.98),

    # ═══════════════════════════════════════════════════════════
    # SYSTEM — Network
    # ═══════════════════════════════════════════════════════════
    (re.compile(
        r"^(?:turn\s+(?:on|off)|enable|disable|toggle)\s+(?:wi-?fi|wifi|wireless)$",
        re.IGNORECASE,
    ), "SYS_WIFI", lambda m: {
        "enable": "on" in m.group(0).lower() or "enable" in m.group(0).lower()
    }, 0.95),

    (re.compile(
        r"^(?:turn\s+(?:on|off)|enable|disable|toggle)\s+bluetooth$",
        re.IGNORECASE,
    ), "SYS_BLUETOOTH", lambda m: {
        "enable": "on" in m.group(0).lower() or "enable" in m.group(0).lower()
    }, 0.95),

    # ═══════════════════════════════════════════════════════════
    # FILE OPERATIONS
    # ═══════════════════════════════════════════════════════════
    (re.compile(
        r"^open\s+(?P<path>.+?)\s+folder$",
        re.IGNORECASE,
    ), "FILE_OPEN", _file_path, 0.92),

    (re.compile(
        r"^(?:create|make|new)\s+(?:a\s+)?folder(?:\s+(?:called|named)\s+)?(?P<path>.+)?$",
        re.IGNORECASE,
    ), "FILE_CREATE", _file_path, 0.92),

    (re.compile(
        r"^(?:find|search\s+for)\s+(?P<path>.+?)(?:\s+file)?$",
        re.IGNORECASE,
    ), "FILE_SEARCH", _file_path, 0.88),

    # ═══════════════════════════════════════════════════════════
    # MEDIA
    # ═══════════════════════════════════════════════════════════
    (re.compile(r"^(?:pause|stop)\s+(?:music|song|playback)?$", re.IGNORECASE),
     "MEDIA_PAUSE", lambda m: {}, 0.97),

    (re.compile(r"^(?:next|skip)\s+(?:song|track)?$", re.IGNORECASE),
     "MEDIA_NEXT", lambda m: {}, 0.97),

    (re.compile(r"^(?:previous|prev|back)\s+(?:song|track)?$", re.IGNORECASE),
     "MEDIA_PREV", lambda m: {}, 0.97),

    # ═══════════════════════════════════════════════════════════
    # WORKFLOW
    # ═══════════════════════════════════════════════════════════
    (re.compile(
        r"^(?:start|begin|activate|launch)\s+(?P<app>.+?)\s+mode$",
        re.IGNORECASE,
    ), "WORKFLOW_START", _app_name, 0.90),

    # ═══════════════════════════════════════════════════════════
    # KAORUKO META
    # ═══════════════════════════════════════════════════════════
    (re.compile(r"^(?:stop|pause|sleep|go\s+to\s+sleep)(?:\s+listening)?$", re.IGNORECASE),
     "META_STOP", lambda m: {}, 0.97),

    (re.compile(r"^(?:help|what\s+can\s+you\s+do|commands)$", re.IGNORECASE),
     "META_HELP", lambda m: {}, 0.97),

    (re.compile(r"^(?:status|system\s+status|how\s+are\s+you)$", re.IGNORECASE),
     "META_STATUS", lambda m: {}, 0.95),

    (re.compile(r"^(?:open\s+)?settings$", re.IGNORECASE),
     "META_SETTINGS", lambda m: {}, 0.97),
]


class RuleEngine:
    """
    Fast regex-based intent matcher.
    Loaded once at startup; pure in-memory matching, zero I/O.
    """

    def __init__(self) -> None:
        self._rules = _RULES
        self._loaded = False

    def load(self) -> None:
        """Pre-compile all patterns (already compiled at module load)."""
        count = len(self._rules)
        self._loaded = True
        log.info("rule_engine_loaded", pattern_count=count)

    def match(self, text: str, language: str = "en") -> Optional[RuleResult]:
        """
        Try to match text against all rules.
        Returns the first match above confidence threshold, or None.
        """
        text = text.strip()
        if not text:
            return None

        for pattern, intent, entity_fn, confidence in self._rules:
            m = pattern.match(text)
            if m:
                try:
                    entities = entity_fn(m) if callable(entity_fn) else {}
                except Exception:
                    entities = {}

                # Build simple action plan for direct intents
                action_plan = self._build_action_plan(intent, entities)

                return RuleResult(
                    intent=intent,
                    entities=entities,
                    confidence=confidence,
                    action_plan=action_plan,
                )

        return None

    def _build_action_plan(self, intent: str, entities: dict) -> Optional[dict]:
        """Build a simple action plan for common rule-matched intents."""
        SIMPLE_INTENTS = {
            "APP_OPEN", "APP_CLOSE", "BROWSER_SEARCH", "BROWSER_OPEN",
            "SYS_VOLUME", "SYS_MUTE", "SYS_SHUTDOWN", "SYS_RESTART",
            "SYS_SLEEP", "SYS_LOCK", "SYS_WIFI", "SYS_BLUETOOTH",
            "FILE_OPEN", "FILE_CREATE", "FILE_SEARCH",
            "MEDIA_PAUSE", "MEDIA_NEXT", "MEDIA_PREV",
            "META_STOP", "META_HELP", "META_STATUS", "META_SETTINGS",
        }
        if intent in SIMPLE_INTENTS:
            return {
                "actions": [{
                    "handler": _INTENT_TO_HANDLER.get(intent, "unknown"),
                    "method": _INTENT_TO_METHOD.get(intent, "handle"),
                    "params": entities,
                    "requires_confirmation": intent in _CONFIRM_REQUIRED,
                }],
                "execution_mode": "sequential",
                "response_strategy": "immediate",
            }
        return None


# ── Intent → Handler mapping ──────────────────────────────────────────────────

_INTENT_TO_HANDLER: dict[str, str] = {
    "APP_OPEN":      "app_control",
    "APP_CLOSE":     "app_control",
    "BROWSER_SEARCH": "browser_control",
    "BROWSER_OPEN":  "browser_control",
    "SYS_VOLUME":    "audio_control",
    "SYS_MUTE":      "audio_control",
    "SYS_SHUTDOWN":  "system_control",
    "SYS_RESTART":   "system_control",
    "SYS_SLEEP":     "system_control",
    "SYS_LOCK":      "system_control",
    "SYS_WIFI":      "network_control",
    "SYS_BLUETOOTH": "network_control",
    "FILE_OPEN":     "file_manager",
    "FILE_CREATE":   "file_manager",
    "FILE_SEARCH":   "file_manager",
    "MEDIA_PAUSE":   "keyboard_control",
    "MEDIA_NEXT":    "keyboard_control",
    "MEDIA_PREV":    "keyboard_control",
    "META_STOP":     "assistant",
    "META_HELP":     "assistant",
    "META_STATUS":   "assistant",
    "META_SETTINGS": "assistant",
}

_INTENT_TO_METHOD: dict[str, str] = {
    "APP_OPEN":      "open_application",
    "APP_CLOSE":     "close_application",
    "BROWSER_SEARCH": "search",
    "BROWSER_OPEN":  "open_url",
    "SYS_VOLUME":    "set_volume",
    "SYS_MUTE":      "set_mute",
    "SYS_SHUTDOWN":  "shutdown",
    "SYS_RESTART":   "restart",
    "SYS_SLEEP":     "sleep",
    "SYS_LOCK":      "lock_screen",
    "SYS_WIFI":      "set_wifi",
    "SYS_BLUETOOTH": "set_bluetooth",
    "FILE_OPEN":     "open_folder",
    "FILE_CREATE":   "create_folder",
    "FILE_SEARCH":   "search_file",
    "MEDIA_PAUSE":   "media_pause",
    "MEDIA_NEXT":    "media_next",
    "MEDIA_PREV":    "media_prev",
    "META_STOP":     "stop_listening",
    "META_HELP":     "show_help",
    "META_STATUS":   "show_status",
    "META_SETTINGS": "open_settings",
}

_CONFIRM_REQUIRED = {
    "SYS_SHUTDOWN",
    "SYS_RESTART",
    "FILE_DELETE",
}
