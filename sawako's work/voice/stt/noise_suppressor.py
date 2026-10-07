"""
kaoruko/voice/stt/noise_suppressor.py

Real-time noise suppression pipeline.
Uses noisereduce (spectral subtraction + stationary noise estimation).

Applied before both wake word detection and STT for cleaner audio.
Runs in <5ms per chunk on CPU.
"""

from __future__ import annotations

import threading
from typing import Optional

import numpy as np

from kaoruko.infrastructure.logging.logger import get_logger

log = get_logger("voice.noise_suppressor")

try:
    import noisereduce as nr
    _NR_AVAILABLE = True
except ImportError:
    nr = None  # type: ignore
    _NR_AVAILABLE = False
    log.warning("noisereduce_unavailable", message="Noise suppression disabled")


class NoiseSuppressor:
    """
    Real-time noise suppressor.
    Maintains a rolling noise profile from recent audio.

    Usage:
        ns = NoiseSuppressor(strength=0.75, sample_rate=16000)
        ns.start()
        clean_chunk = ns.process(noisy_chunk)
    """

    NOISE_PROFILE_DURATION_S = 0.5   # How much audio to use for noise profile
    UPDATE_INTERVAL_S = 5.0          # How often to update the noise profile

    def __init__(
        self,
        strength: float = 0.75,
        sample_rate: int = 16000,
        stationary: bool = True,
    ) -> None:
        self.strength = strength
        self.sample_rate = sample_rate
        self.stationary = stationary
        self._noise_profile: Optional[np.ndarray] = None
        self._profile_buffer: list[np.ndarray] = []
        self._profile_samples = int(self.NOISE_PROFILE_DURATION_S * sample_rate)
        self._lock = threading.Lock()
        self._last_profile_update: float = 0.0
        self._running = False
        self._chunk_count = 0

    def start(self) -> None:
        """Begin noise suppression processing."""
        self._running = True
        log.info(
            "noise_suppressor_started",
            strength=self.strength,
            stationary=self.stationary,
            backend="noisereduce" if _NR_AVAILABLE else "passthrough",
        )

    def stop(self) -> None:
        self._running = False

    def process(self, audio: np.ndarray) -> np.ndarray:
        """
        Apply noise suppression to an audio chunk.

        Args:
            audio: float32 numpy array (mono, 16kHz)

        Returns:
            Noise-suppressed audio (same shape as input)
        """
        if not self._running or not _NR_AVAILABLE:
            return audio

        self._chunk_count += 1

        # Accumulate audio for noise profile
        with self._lock:
            self._profile_buffer.append(audio.copy())
            # Keep only the last N samples for the profile
            total = sum(len(c) for c in self._profile_buffer)
            while total > self._profile_samples * 2 and self._profile_buffer:
                removed = self._profile_buffer.pop(0)
                total -= len(removed)

        try:
            if self._noise_profile is not None and self.stationary:
                # Stationary noise reduction (most common case — fan noise, HVAC)
                clean = nr.reduce_noise(
                    y=audio,
                    sr=self.sample_rate,
                    y_noise=self._noise_profile,
                    prop_decrease=self.strength,
                    stationary=True,
                    n_fft=512,
                    n_std_thresh_stationary=1.5,
                )
            else:
                # Non-stationary reduction — slower but handles dynamic noise
                clean = nr.reduce_noise(
                    y=audio,
                    sr=self.sample_rate,
                    prop_decrease=self.strength,
                    stationary=False,
                    n_fft=512,
                )
            return clean.astype(np.float32)

        except Exception as e:
            if self._chunk_count % 100 == 0:
                log.warning("noise_suppression_error", error=str(e))
            return audio

    def update_noise_profile(self) -> None:
        """
        Update the noise profile from recent audio.
        Call this during guaranteed silence (before wake word activation).
        """
        with self._lock:
            if not self._profile_buffer:
                return
            self._noise_profile = np.concatenate(self._profile_buffer[-20:])

        log.debug("noise_profile_updated", samples=len(self._noise_profile))

    def process_full_utterance(self, audio: np.ndarray) -> np.ndarray:
        """
        Apply stronger noise reduction to a complete utterance (for STT).
        Can use higher quality settings since it's not real-time.
        """
        if not _NR_AVAILABLE:
            return audio

        try:
            clean = nr.reduce_noise(
                y=audio,
                sr=self.sample_rate,
                prop_decrease=min(1.0, self.strength * 1.3),
                stationary=self.stationary,
                n_fft=1024,          # Higher quality FFT for full utterance
                n_std_thresh_stationary=1.0,
            )
            return clean.astype(np.float32)
        except Exception as e:
            log.warning("utterance_suppression_error", error=str(e))
            return audio
