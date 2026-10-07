"""
kaoruko/voice/stt/vad.py

Voice Activity Detection using Silero VAD.
Accurately distinguishes speech from silence/noise.

Why Silero over WebRTC:
- Better accuracy on accented speech (Hindi, Japanese)
- ONNX-based, runs locally, no cloud
- Handles code-switching (Hinglish) well
- <3ms per chunk on CPU

VAD is used at two points in the pipeline:
1. After wake word — decide when the user has finished speaking
2. Before STT — trim silence from audio to reduce transcription errors
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

from kaoruko.infrastructure.logging.logger import get_logger

log = get_logger("voice.vad")


@dataclass
class VADConfig:
    sample_rate: int = 16000
    threshold: float = 0.5            # Speech probability threshold (0-1)
    min_speech_duration_ms: int = 250  # Ignore speech segments shorter than this
    max_silence_duration_ms: int = 800 # Stop recording after this much silence
    window_size_samples: int = 512     # Silero window size


@dataclass
class SpeechSegment:
    """A detected speech segment with audio data."""
    audio: np.ndarray
    duration_ms: float
    start_time: float
    end_time: float
    speech_ratio: float   # Fraction of windows classified as speech


class SileroVAD:
    """
    Silero VAD wrapper.
    Buffers audio chunks and yields complete speech segments.

    Usage:
        vad = SileroVAD(VADConfig())
        await vad.initialize()

        segment = await vad.record_until_silence(audio_capture)
        # segment.audio is ready for STT
    """

    def __init__(self, config: VADConfig) -> None:
        self.config = config
        self._model = None
        self._initialized = False
        self._lock = threading.Lock()

    async def initialize(self) -> None:
        """Load the Silero VAD model (ONNX)."""
        try:
            import torch
            model, _ = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                onnx=True,
                verbose=False,
            )
            self._model = model
            self._initialized = True
            log.info("silero_vad_loaded")
        except ImportError:
            log.warning("silero_vad_unavailable", reason="torch not installed, using energy VAD")
            self._initialized = True  # Will use fallback
        except Exception as e:
            log.error("silero_vad_load_error", error=str(e))
            self._initialized = True  # Will use fallback

    def is_speech(self, audio_chunk: np.ndarray) -> float:
        """
        Returns speech probability for an audio chunk (0.0 - 1.0).
        Uses Silero if available, falls back to energy-based VAD.
        """
        if self._model is not None:
            return self._silero_probability(audio_chunk)
        return self._energy_probability(audio_chunk)

    async def record_until_silence(
        self,
        audio_capture: object,
        max_duration_seconds: float = 15.0,
    ) -> Optional[SpeechSegment]:
        """
        Record audio until the user stops speaking.

        Args:
            audio_capture: AudioCapture instance
            max_duration_seconds: Hard stop after this duration

        Returns:
            SpeechSegment with full utterance audio, or None if no speech
        """
        if not self._initialized:
            await self.initialize()

        chunks: list[np.ndarray] = []
        speech_flags: list[bool] = []
        silence_start: Optional[float] = None
        speech_started = False
        start_time = time.time()

        min_speech_chunks = int(
            self.config.min_speech_duration_ms / 1000 *
            self.config.sample_rate / self.config.window_size_samples
        )
        max_silence_chunks = int(
            self.config.max_silence_duration_ms / 1000 *
            self.config.sample_rate / self.config.window_size_samples
        )

        silence_chunk_count = 0

        while True:
            # Timeout guard
            if time.time() - start_time > max_duration_seconds:
                log.warning("vad_max_duration_reached", seconds=max_duration_seconds)
                break

            chunk = audio_capture.read_chunk(timeout=0.05)
            if chunk is None:
                await asyncio.sleep(0.01)
                continue

            prob = self.is_speech(chunk)
            is_speech = prob >= self.config.threshold

            chunks.append(chunk)
            speech_flags.append(is_speech)

            if is_speech:
                speech_started = True
                silence_chunk_count = 0
            elif speech_started:
                silence_chunk_count += 1
                if silence_chunk_count >= max_silence_chunks:
                    break  # User stopped speaking

            # Don't return if we never detected speech at all
            if (not speech_started and
                len(chunks) > 200):   # ~6 seconds of pure silence
                log.debug("vad_no_speech_detected")
                return None

        if not speech_started:
            return None

        # Filter leading/trailing silence
        audio = np.concatenate(chunks)
        speech_ratio = sum(speech_flags) / max(len(speech_flags), 1)

        # Trim silence from start
        audio = self._trim_silence(audio)

        end_time = time.time()
        duration_ms = (end_time - start_time) * 1000

        if len(audio) < self.config.sample_rate * 0.1:  # Less than 100ms
            return None

        return SpeechSegment(
            audio=audio,
            duration_ms=duration_ms,
            start_time=start_time,
            end_time=end_time,
            speech_ratio=speech_ratio,
        )

    # ── VAD Backends ─────────────────────────────────────────────────────────

    def _silero_probability(self, chunk: np.ndarray) -> float:
        """Get speech probability from Silero VAD model."""
        try:
            import torch
            tensor = torch.FloatTensor(chunk)
            if len(tensor.shape) == 1:
                tensor = tensor.unsqueeze(0)
            prob = self._model(tensor, self.config.sample_rate).item()
            return float(prob)
        except Exception:
            return self._energy_probability(chunk)

    def _energy_probability(self, chunk: np.ndarray) -> float:
        """
        Fallback: energy-based VAD.
        Simple RMS threshold — less accurate than Silero but always works.
        """
        rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
        # Calibrated threshold for typical microphone input
        ENERGY_THRESHOLD = 0.01
        # Sigmoid-like mapping: 0 at silence, 1 at clear speech
        ratio = rms / ENERGY_THRESHOLD
        return float(min(1.0, max(0.0, ratio)))

    def _trim_silence(self, audio: np.ndarray) -> np.ndarray:
        """Remove leading and trailing silence from audio."""
        FRAME = self.config.window_size_samples
        probs = []
        for i in range(0, len(audio) - FRAME, FRAME):
            p = self.is_speech(audio[i:i + FRAME])
            probs.append(p >= self.config.threshold)

        if not any(probs):
            return audio

        # Find first and last speech frame
        first = next(i for i, p in enumerate(probs) if p)
        last = len(probs) - 1 - next(i for i, p in enumerate(reversed(probs)) if p)

        # Add 100ms padding around speech
        PAD = int(0.1 * self.config.sample_rate)
        start = max(0, first * FRAME - PAD)
        end = min(len(audio), (last + 1) * FRAME + PAD)

        return audio[start:end]
