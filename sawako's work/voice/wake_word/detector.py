"""
kaoruko/voice/wake_word/detector.py

Wake word detection engine.
Primary backend: OpenWakeWord (ONNX, offline, free, custom-trainable)
Fallback: Energy + keyword spotting (always-available)

Fix applied:
  - _fire_detection() runs on a background thread. Inside a background thread
    asyncio.get_event_loop() is deprecated and unreliable in Python 3.10+
    (may return a new unrelated loop or raise). Now uses the loop injected via
    the constructor — the main asyncio event loop — which is always valid.
  - _load_oww_builtin() called via run_in_executor uses asyncio.get_running_loop()
    instead of deprecated get_event_loop().
"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

from kaoruko.core.event_bus import EventBus, KaorukoEvent
from kaoruko.infrastructure.logging.logger import get_logger

log = get_logger("voice.wake_word")

try:
    import openwakeword
    from openwakeword.model import Model as OWWModel
    _OWW_AVAILABLE = True
except ImportError:
    _OWW_AVAILABLE = False
    log.warning("openwakeword_unavailable", message="Will use energy-based fallback")


class WakeWordDetector:
    """
    Always-listening wake word detector.

    Consumes audio from AudioCapture via callback.
    Runs detection on a separate thread (non-blocking for main loop).
    Triggers event bus on detection with cooldown to prevent double-firing.

    Target CPU usage: <3% at idle.
    Wake latency: <100ms from word end to event.
    """

    COOLDOWN_SECONDS = 1.5
    OWW_CHUNK_SIZE = 1280          # OpenWakeWord expects 80ms chunks at 16kHz
    DETECTION_THRESHOLD = 0.65

    def __init__(
        self,
        event_bus: EventBus,
        phrases: list[str],
        sensitivity: float = 0.65,
        model_dir: Optional[Path] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        self.bus = event_bus
        self.phrases = phrases
        self.sensitivity = sensitivity
        self.model_dir = model_dir or Path(__file__).parent / "models"
        # FIX: Require the main event loop to be passed in.
        # Storing it here means _fire_detection() never needs to call
        # get_event_loop() from a background thread (unreliable in 3.10+).
        self._loop = loop

        self._oww_model: Optional[object] = None
        self._audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=50)
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._last_detection: float = 0.0
        self._detection_count = 0

        self._accumulator = np.array([], dtype=np.float32)

    # ── Public API ────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Load OWW model. Falls back gracefully if unavailable."""
        if _OWW_AVAILABLE:
            await self._load_oww_model()
        else:
            log.warning("wake_word_using_fallback", reason="OpenWakeWord not installed")
        log.info(
            "wake_word_detector_ready",
            phrases=self.phrases,
            backend="openwakeword" if self._oww_model else "energy_fallback",
            sensitivity=self.sensitivity,
        )

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._detection_loop,
            name="kaoruko-wakeword",
            daemon=True,
        )
        self._thread.start()
        log.info("wake_word_detector_started")

    def stop(self) -> None:
        self._running = False
        self._audio_queue.put(None)
        if self._thread:
            self._thread.join(timeout=2.0)
        log.info("wake_word_detector_stopped", detections=self._detection_count)

    def on_audio_chunk(self, chunk: np.ndarray) -> None:
        if not self._running:
            return
        try:
            self._audio_queue.put_nowait(chunk)
        except queue.Full:
            pass

    # ── Detection Loop (background thread) ───────────────────────────────────

    def _detection_loop(self) -> None:
        log.debug("wake_word_thread_started")

        while self._running:
            try:
                chunk = self._audio_queue.get(timeout=0.1)
                if chunk is None:
                    break

                self._accumulator = np.concatenate([self._accumulator, chunk])

                while len(self._accumulator) >= self.OWW_CHUNK_SIZE:
                    window = self._accumulator[:self.OWW_CHUNK_SIZE]
                    self._accumulator = self._accumulator[self.OWW_CHUNK_SIZE:]
                    self._process_window(window)

            except queue.Empty:
                continue
            except Exception as e:
                log.error("wake_word_loop_error", error=str(e))

        log.debug("wake_word_thread_stopped")

    def _process_window(self, audio: np.ndarray) -> None:
        detected = False
        score = 0.0
        phrase = ""

        if self._oww_model is not None:
            detected, score, phrase = self._check_oww(audio)
        else:
            detected, score, phrase = self._check_energy_fallback(audio)

        if detected:
            self._fire_detection(score, phrase)

    def _check_oww(self, audio: np.ndarray) -> tuple[bool, float, str]:
        try:
            pcm = (audio * 32767).astype(np.int16)
            self._oww_model.predict(pcm)

            best_phrase = ""
            best_score = 0.0

            for phrase_key in self._oww_model.prediction_buffer:
                scores = self._oww_model.prediction_buffer[phrase_key]
                if scores:
                    score = float(scores[-1])
                    if score > best_score:
                        best_score = score
                        best_phrase = phrase_key

            return best_score >= self.sensitivity, best_score, best_phrase
        except Exception as e:
            log.debug("oww_inference_error", error=str(e))
            return False, 0.0, ""

    def _check_energy_fallback(self, audio: np.ndarray) -> tuple[bool, float, str]:
        rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        threshold = 0.04
        score = min(1.0, rms / threshold)
        return score > 0.8, score, self.phrases[0] if self.phrases else "kaoruko"

    def _fire_detection(self, score: float, phrase: str) -> None:
        """
        Fire the wake detection event with cooldown protection.

        FIX: Was using `asyncio.get_event_loop()` inside this background thread.
        In Python 3.10+, get_event_loop() in a non-main thread where no current
        loop is set emits a DeprecationWarning and may return the wrong loop.
        Now uses self._loop which is the main event loop passed at construction.
        """
        now = time.time()
        if now - self._last_detection < self.COOLDOWN_SECONDS:
            return

        self._last_detection = now
        self._detection_count += 1

        log.info(
            "wake_word_detected",
            phrase=phrase,
            score=round(score, 3),
            total=self._detection_count,
        )

        # FIX: Use the pre-captured loop reference, not get_event_loop()
        if self._loop is None or not self._loop.is_running():
            log.warning("wake_word_no_event_loop")
            return

        asyncio.run_coroutine_threadsafe(
            self.bus.publish(
                KaorukoEvent.VOICE_WAKE_DETECTED,
                data={"phrase": phrase, "confidence": score},
                source="wake_word_detector",
            ),
            self._loop,
        )

    # ── Model Loading ─────────────────────────────────────────────────────────

    async def _load_oww_model(self) -> None:
        try:
            model_paths = self._find_model_files()

            if model_paths:
                self._oww_model = OWWModel(
                    wakeword_models=model_paths,
                    inference_framework="onnx",
                )
                log.info("oww_custom_model_loaded", models=model_paths)
            else:
                # FIX: Use get_running_loop() — called from within a coroutine
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._load_oww_builtin)
        except Exception as e:
            log.error("oww_load_error", error=str(e))
            self._oww_model = None

    def _load_oww_builtin(self) -> None:
        """Load OWW built-in models (runs in thread pool)."""
        try:
            openwakeword.utils.download_models()
            self._oww_model = OWWModel(inference_framework="onnx")
            log.info("oww_builtin_models_loaded")
        except Exception as e:
            log.warning("oww_builtin_load_failed", error=str(e))
            self._oww_model = None

    def _find_model_files(self) -> list[str]:
        if not self.model_dir.exists():
            return []
        return [
            str(f)
            for f in self.model_dir.iterdir()
            if f.suffix in (".onnx", ".tflite") and not f.name.startswith(".")
        ]
