"""
kaoruko/voice/stt/recognizer.py

Speech-to-Text engine abstraction + Whisper implementation.

Architecture: Strategy pattern — swap engines via config.
All engines implement the STTEngine protocol.

Engines:
  WhisperSTTEngine  — faster-whisper (local, offline, multilingual)
  GoogleSTTEngine   — Google Speech-to-Text v2 (online, best Hinglish)
  AutoSTTEngine     — selects engine based on network + language context
"""

from __future__ import annotations

import asyncio
import io
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

import numpy as np

from kaoruko.infrastructure.logging.logger import get_logger

log = get_logger("voice.stt")


@dataclass
class TranscriptResult:
    text: str
    language: str = "en"
    confidence: float = 1.0
    duration_ms: float = 0.0
    engine: str = "unknown"
    segments: list[dict] = None    # Word-level segments if available

    def __post_init__(self):
        if self.segments is None:
            self.segments = []

    def is_empty(self) -> bool:
        return not self.text.strip()


@runtime_checkable
class STTEngine(Protocol):
    async def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
    ) -> TranscriptResult: ...
    async def initialize(self) -> None: ...


# ── Whisper STT Engine ────────────────────────────────────────────────────────

class WhisperSTTEngine:
    """
    faster-whisper STT engine (local, offline, multilingual).
    Uses CTranslate2 backend for 2-4x faster inference than OpenAI Whisper.

    Model sizes vs performance:
      tiny  : ~39M params, ~3x realtime, lowest accuracy
      base  : ~74M params, ~7x realtime, good accuracy (DEFAULT)
      small : ~244M params, ~4x realtime, better multilingual
      medium: ~769M params, ~2x realtime, best accuracy offline
    """

    def __init__(
        self,
        model_size: str = "base",
        device: str = "auto",
        compute_type: str = "int8",
        beam_size: int = 5,
        model_dir: Optional[Path] = None,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.model_dir = str(model_dir) if model_dir else None
        self._model = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Load the Whisper model (may download ~140MB for base)."""
        await asyncio.get_event_loop().run_in_executor(
            None, self._load_model
        )

    def _load_model(self) -> None:
        try:
            from faster_whisper import WhisperModel

            # Determine compute device
            device = self.device
            if device == "auto":
                try:
                    import torch
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except ImportError:
                    device = "cpu"

            compute_type = self.compute_type
            if device == "cpu" and compute_type == "float16":
                compute_type = "int8"   # float16 unsupported on CPU

            self._model = WhisperModel(
                self.model_size,
                device=device,
                compute_type=compute_type,
                download_root=self.model_dir,
            )
            log.info(
                "whisper_model_loaded",
                size=self.model_size,
                device=device,
                compute_type=compute_type,
            )
        except ImportError:
            log.error("faster_whisper_not_installed")
            raise
        except Exception as e:
            log.error("whisper_model_load_error", error=str(e), size=self.model_size)
            raise

    async def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
    ) -> TranscriptResult:
        """
        Transcribe audio to text.

        Args:
            audio:       float32 mono numpy array
            sample_rate: Sample rate (must be 16000 for Whisper)
            language:    ISO code hint (None = auto-detect)

        Returns:
            TranscriptResult with text, detected language, and confidence
        """
        if self._model is None:
            raise RuntimeError("WhisperSTTEngine not initialized. Call initialize() first.")

        start = time.perf_counter()

        # Resample if needed (Whisper requires 16kHz)
        if sample_rate != 16000:
            audio = self._resample(audio, sample_rate, 16000)

        # Run inference in thread pool (CPU-bound)
        async with self._lock:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._transcribe_sync(audio, language),
            )

        elapsed_ms = (time.perf_counter() - start) * 1000
        result.duration_ms = elapsed_ms

        log.info(
            "stt_transcript",
            engine="whisper",
            text=result.text[:80],
            language=result.language,
            ms=round(elapsed_ms, 1),
        )
        return result

    def _transcribe_sync(
        self,
        audio: np.ndarray,
        language: Optional[str],
    ) -> TranscriptResult:
        """Synchronous Whisper inference (runs in thread pool)."""
        try:
            segments, info = self._model.transcribe(
                audio,
                language=language,
                beam_size=self.beam_size,
                vad_filter=True,
                vad_parameters={
                    "threshold": 0.5,
                    "min_silence_duration_ms": 300,
                },
                word_timestamps=True,
            )

            text_parts = []
            word_segments = []

            for seg in segments:
                text_parts.append(seg.text.strip())
                if seg.words:
                    for word in seg.words:
                        word_segments.append({
                            "word": word.word,
                            "start": word.start,
                            "end": word.end,
                            "probability": word.probability,
                        })

            full_text = " ".join(text_parts).strip()
            avg_confidence = (
                sum(w["probability"] for w in word_segments) / len(word_segments)
                if word_segments else 0.9
            )

            return TranscriptResult(
                text=full_text,
                language=info.language,
                confidence=avg_confidence,
                engine="whisper",
                segments=word_segments,
            )
        except Exception as e:
            log.error("whisper_transcribe_error", error=str(e))
            return TranscriptResult(text="", engine="whisper", confidence=0.0)

    @staticmethod
    def _resample(audio: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
        try:
            from scipy.signal import resample_poly
            from math import gcd
            g = gcd(from_rate, to_rate)
            return resample_poly(audio, to_rate // g, from_rate // g).astype(np.float32)
        except ImportError:
            log.warning("scipy_unavailable_for_resample")
            return audio


# ── Auto STT Engine (selects best backend) ────────────────────────────────────

class AutoSTTEngine:
    """
    Automatic engine selection:
    - No internet / Japanese / Hinglish → Whisper (small)
    - Internet + English → WhisperBase (fast, good enough)
    - Internet + speed priority → Whisper (tiny)
    """

    def __init__(self, config: object) -> None:
        self.config = config
        self._whisper_engines: dict[str, WhisperSTTEngine] = {}
        self._current: Optional[WhisperSTTEngine] = None

    async def initialize(self) -> None:
        """Pre-load the default engine."""
        self._current = self._get_engine("base")
        await self._current.initialize()

    async def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
    ) -> TranscriptResult:
        engine = self._select_engine(language)
        return await engine.transcribe(audio, sample_rate, language)

    def _select_engine(self, language: Optional[str]) -> WhisperSTTEngine:
        """Select model size based on language needs."""
        if language in ("ja", "hi", None):
            return self._get_engine("small")   # Better multilingual
        return self._get_engine("base")

    def _get_engine(self, size: str) -> WhisperSTTEngine:
        if size not in self._whisper_engines:
            self._whisper_engines[size] = WhisperSTTEngine(model_size=size)
        return self._whisper_engines[size]
