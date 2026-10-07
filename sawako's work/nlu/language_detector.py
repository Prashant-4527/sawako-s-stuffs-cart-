"""
kaoruko/nlu/language_detector.py

Detects language of user input.
Supports English, Hinglish (Hindi-English code-switching), and Japanese.

Strategy:
1. Script detection (fast, zero-cost):
   - Japanese kana/kanji → "ja"
   - Devanagari → "hi"
2. Hinglish keyword detection:
   - Common Hinglish words → "hi-en"
3. langdetect library for everything else
"""

from __future__ import annotations

import re
from typing import Optional

from kaoruko.infrastructure.logging.logger import get_logger

log = get_logger("nlu.language_detector")

try:
    from langdetect import detect, DetectorFactory
    DetectorFactory.seed = 42   # Reproducible results
    _LANGDETECT_AVAILABLE = True
except ImportError:
    _LANGDETECT_AVAILABLE = False
    log.warning("langdetect_unavailable")


# ── Script patterns ───────────────────────────────────────────────────────────
_JP_PATTERN  = re.compile(r'[\u3040-\u30FF\u4E00-\u9FFF]')   # Hiragana/Katakana/Kanji
_DEVA_PATTERN = re.compile(r'[\u0900-\u097F]')                # Devanagari

# Common Hinglish words/suffixes (English words used in Hindi contexts)
_HINGLISH_KEYWORDS = {
    "karo", "kholo", "band", "bandh", "chalu", "daal", "dhundo",
    "bata", "batao", "dikhao", "dikha", "sun", "suno", "chalao",
    "lagao", "nahi", "haan", "ok", "theek", "acha", "zyada",
    "thoda", "abhi", "baad", "pehle", "aur", "ya", "mein", "pe",
    "se", "ka", "ki", "ko", "me", "hai",
}


class LanguageDetector:
    """
    Lightweight language detector for voice commands.

    Returns normalized language codes:
    - "en"    → English
    - "ja"    → Japanese
    - "hi"    → Hindi (pure Devanagari)
    - "hi-en" → Hinglish (code-switched Hindi-English)
    """

    def __init__(self) -> None:
        self._initialized = False

    def initialize(self) -> None:
        self._initialized = True
        log.info(
            "language_detector_ready",
            backend="langdetect" if _LANGDETECT_AVAILABLE else "rule_only",
        )

    def detect(self, text: str) -> str:
        """
        Detect the primary language of a text string.

        Args:
            text: User utterance (possibly multilingual)

        Returns:
            ISO language code: "en", "ja", "hi", "hi-en"
        """
        if not text or not text.strip():
            return "en"

        text = text.strip()

        # ── 1. Script-based detection (O(n), fastest) ────────────────────────
        if _JP_PATTERN.search(text):
            return "ja"

        if _DEVA_PATTERN.search(text):
            return "hi"

        # ── 2. Hinglish keyword detection ────────────────────────────────────
        tokens = set(text.lower().split())
        hinglish_matches = tokens.intersection(_HINGLISH_KEYWORDS)
        if hinglish_matches:
            # If >50% of tokens are Hinglish keywords → label as Hinglish
            ratio = len(hinglish_matches) / max(len(tokens), 1)
            if ratio > 0.15 or len(hinglish_matches) >= 2:
                log.debug("hinglish_detected", matches=list(hinglish_matches)[:5])
                return "hi-en"

        # ── 3. langdetect library ─────────────────────────────────────────────
        if _LANGDETECT_AVAILABLE:
            try:
                lang = detect(text)
                if lang in ("en", "ja", "hi"):
                    return lang
                if lang in ("mr", "ne", "sa"):   # Other Indian languages → treat as hi
                    return "hi"
                return "en"   # Default to English for unrecognized
            except Exception:
                pass

        return "en"

    def is_multilingual(self, text: str) -> bool:
        """Check if the text contains code-switching."""
        has_latin = bool(re.search(r'[a-zA-Z]', text))
        has_jp = bool(_JP_PATTERN.search(text))
        has_deva = bool(_DEVA_PATTERN.search(text))
        return (has_latin and has_jp) or (has_latin and has_deva)

    def normalize_for_stt(self, lang: str) -> Optional[str]:
        """
        Convert our language codes to Whisper language hints.
        Returns None for auto-detect.
        """
        mapping = {
            "en":    "en",
            "ja":    "ja",
            "hi":    "hi",
            "hi-en": None,   # Auto-detect handles Hinglish better
        }
        return mapping.get(lang, None)
