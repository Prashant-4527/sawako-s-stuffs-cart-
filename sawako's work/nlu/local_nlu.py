"""
kaoruko/nlu/local_nlu.py

Layer 2 intent classification using sentence-transformers.
Converts utterances to embeddings, compares with labeled examples
using cosine similarity.

Runs fully offline, no API call needed.
Handles ~15% of commands that slip past rule engine but don't need AI.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

import numpy as np

from kaoruko.infrastructure.logging.logger import get_logger

if TYPE_CHECKING:
    from kaoruko.nlu.intent_classifier import ClassificationResult

log = get_logger("nlu.local_nlu")


# ── Labeled example utterances per intent ─────────────────────────────────────
_EXAMPLES: dict[str, list[str]] = {
    "APP_OPEN": [
        "can you open chrome for me",
        "please start visual studio code",
        "launch the spotify application",
        "I want to use discord",
        "bring up steam please",
        "fire up notepad",
    ],
    "APP_CLOSE": [
        "I'm done with chrome",
        "please close the browser",
        "get rid of discord",
        "I don't need notepad anymore",
        "terminate spotify",
    ],
    "BROWSER_SEARCH": [
        "look up python tutorials",
        "find me information about machine learning",
        "I want to search for jobs in Tokyo",
        "google how to make sushi",
        "search the web for AI news",
    ],
    "SYS_VOLUME": [
        "the music is too loud",
        "I can't hear well, make it louder",
        "turn the sound down a little",
        "adjust audio to fifty percent",
        "make it quieter please",
    ],
    "SYS_SHUTDOWN": [
        "I'm done for the day, shut it all down",
        "please power off the computer",
        "time to turn off the pc",
        "I'm going to bed, shut down",
    ],
    "FILE_OPEN": [
        "show me my downloads",
        "can you open my documents",
        "I need to access my desktop folder",
        "navigate to the pictures directory",
    ],
    "CONV_CHAT": [
        "how are you doing",
        "tell me something interesting",
        "what do you think about AI",
        "have a conversation with me",
        "let's talk",
        "what's your name",
    ],
    "META_HELP": [
        "I don't know what you can do",
        "show me all your features",
        "what kind of commands do you know",
        "I need help understanding your capabilities",
    ],
    "META_STATUS": [
        "how are you performing",
        "show me system information",
        "what's the current status",
        "tell me about yourself",
    ],
    "WORKFLOW_START": [
        "I want to start working",
        "set up my development environment",
        "prepare my gaming setup",
        "I'm going to study now, set everything up",
        "activate focus mode",
    ],
}


class LocalNLU:
    """
    Sentence-transformers based intent classifier.
    Computes cosine similarity between user utterance and labeled examples.
    Returns the closest match above a confidence threshold.
    """

    SIMILARITY_THRESHOLD = 0.70
    MODEL_NAME = "all-MiniLM-L6-v2"   # 80MB, very fast, great for similarity

    def __init__(self) -> None:
        self._model = None
        self._example_embeddings: dict[str, np.ndarray] = {}
        self._initialized = False

    async def initialize(self) -> None:
        """Load the sentence-transformers model and pre-compute embeddings."""
        await asyncio.get_event_loop().run_in_executor(None, self._load_model)

    def _load_model(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.MODEL_NAME)

            # Pre-compute all example embeddings
            log.info("local_nlu_computing_embeddings",
                     intent_count=len(_EXAMPLES))
            for intent, examples in _EXAMPLES.items():
                embeddings = self._model.encode(
                    examples,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                # Store mean embedding per intent
                self._example_embeddings[intent] = np.mean(embeddings, axis=0)

            self._initialized = True
            log.info("local_nlu_ready",
                     model=self.MODEL_NAME,
                     intents=len(self._example_embeddings))
        except ImportError:
            log.warning("sentence_transformers_not_installed")
            self._initialized = False
        except Exception as e:
            log.error("local_nlu_load_error", error=str(e))
            self._initialized = False

    async def classify(
        self,
        text: str,
        language: str = "en",
    ) -> Optional["ClassificationResult"]:
        """
        Classify text using cosine similarity against labeled examples.

        Returns:
            ClassificationResult if similarity > threshold, else None
        """
        if not self._initialized or self._model is None:
            return None

        try:
            from kaoruko.nlu.intent_classifier import ClassificationResult

            # Encode query
            query_embedding = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._model.encode(
                    [text],
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )[0]
            )

            # Find best matching intent
            best_intent = None
            best_score = 0.0

            for intent, mean_embedding in self._example_embeddings.items():
                score = float(np.dot(query_embedding, mean_embedding))
                if score > best_score:
                    best_score = score
                    best_intent = intent

            if best_intent and best_score >= self.SIMILARITY_THRESHOLD:
                from kaoruko.nlu.rule_engine import _INTENT_TO_HANDLER, _INTENT_TO_METHOD
                handler = _INTENT_TO_HANDLER.get(best_intent, "unknown")
                method = _INTENT_TO_METHOD.get(best_intent, "handle")

                return ClassificationResult(
                    intent=best_intent,
                    entities={},
                    confidence=best_score,
                    action_plan={
                        "actions": [{
                            "handler": handler,
                            "method": method,
                            "params": {},
                            "requires_confirmation": False,
                        }],
                        "execution_mode": "sequential",
                        "response_strategy": "after_completion",
                    },
                    layer_used="local_nlu",
                )

            return None

        except Exception as e:
            log.error("local_nlu_classify_error", error=str(e))
            return None
