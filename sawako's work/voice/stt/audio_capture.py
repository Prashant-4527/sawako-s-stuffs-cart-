"""
kaoruko/voice/stt/audio_capture.py

Microphone audio capture engine.
Uses sounddevice for reliable, low-latency audio streaming on Windows.

Architecture:
- Continuous background stream via sounddevice callback
- Thread-safe ring buffer stores audio chunks
- Notifies listeners when audio is available
- Handles device enumeration, selection, and hot-plug recovery
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from kaoruko.infrastructure.logging.logger import get_logger

log = get_logger("voice.audio_capture")

# Try to import sounddevice; provide graceful stub if unavailable
try:
    import sounddevice as sd
    _SD_AVAILABLE = True
except (ImportError, OSError):
    sd = None  # type: ignore
    _SD_AVAILABLE = False
    log.warning("sounddevice_unavailable", message="Audio capture will be stubbed")


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    chunk_size: int = 512          # frames per callback (~32ms at 16kHz)
    dtype: str = "float32"
    device_index: Optional[int] = None   # None = system default
    max_buffer_chunks: int = 200   # ring buffer size (~6s of audio)


ChunkCallback = Callable[[np.ndarray], None]


class AudioCapture:
    """
    Continuous microphone capture with ring buffer.

    Usage:
        cap = AudioCapture(AudioConfig())
        cap.add_listener(my_callback)
        cap.start()
        ...
        cap.stop()

    Callback receives float32 numpy arrays of shape (chunk_size,)
    at sample_rate Hz.
    """

    def __init__(self, config: AudioConfig) -> None:
        self.config = config
        self._stream: Optional[object] = None
        self._buffer: deque[np.ndarray] = deque(maxlen=config.max_buffer_chunks)
        self._listeners: list[ChunkCallback] = []
        self._running = False
        self._lock = threading.Lock()
        self._drop_count = 0
        self._chunk_count = 0
        self._start_time: float = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    def add_listener(self, callback: ChunkCallback) -> None:
        """Register a callback to receive audio chunks in real-time."""
        with self._lock:
            self._listeners.append(callback)

    def remove_listener(self, callback: ChunkCallback) -> None:
        with self._lock:
            self._listeners = [cb for cb in self._listeners if cb is not callback]

    def start(self) -> None:
        """Begin audio capture. Non-blocking."""
        if self._running:
            return

        if not _SD_AVAILABLE:
            log.warning("audio_capture_stubbed", reason="sounddevice not available")
            self._running = True
            return

        self._validate_device()

        self._stream = sd.InputStream(
            samplerate=self.config.sample_rate,
            channels=self.config.channels,
            dtype=self.config.dtype,
            blocksize=self.config.chunk_size,
            device=self.config.device_index,
            callback=self._sd_callback,
            latency="low",
        )
        self._stream.start()
        self._running = True
        self._start_time = time.time()
        log.info(
            "audio_capture_started",
            sample_rate=self.config.sample_rate,
            chunk_size=self.config.chunk_size,
            device=self._get_device_name(),
        )

    def stop(self) -> None:
        """Stop audio capture and release the device."""
        if not self._running:
            return
        self._running = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                log.warning("audio_stop_error", error=str(e))
            self._stream = None
        log.info(
            "audio_capture_stopped",
            chunks_processed=self._chunk_count,
            drops=self._drop_count,
        )

    def read_chunk(self, timeout: float = 0.1) -> Optional[np.ndarray]:
        """
        Read one chunk from the buffer (blocking with timeout).
        Returns None if no data available within timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self._buffer:
                    return self._buffer.popleft()
            time.sleep(0.005)
        return None

    def drain(self) -> list[np.ndarray]:
        """Return all buffered chunks and clear the buffer."""
        with self._lock:
            chunks = list(self._buffer)
            self._buffer.clear()
            return chunks

    def is_running(self) -> bool:
        return self._running

    @staticmethod
    def list_devices() -> list[dict]:
        """List available audio input devices."""
        if not _SD_AVAILABLE:
            return []
        devices = []
        for i, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                devices.append({
                    "index": i,
                    "name": dev["name"],
                    "sample_rate": dev["default_samplerate"],
                    "channels": dev["max_input_channels"],
                })
        return devices

    # ── Internal ──────────────────────────────────────────────────────────────

    def _sd_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: object,
    ) -> None:
        """sounddevice callback — called on audio hardware thread."""
        if status and hasattr(status, "input_overflow") and status.input_overflow:
            self._drop_count += 1
            if self._drop_count % 50 == 0:
                log.warning("audio_overflow", drop_count=self._drop_count)

        # Flatten to mono float32
        chunk = indata[:, 0].copy() if self.config.channels == 1 else indata.copy()
        self._chunk_count += 1

        with self._lock:
            self._buffer.append(chunk)
            for cb in self._listeners:
                try:
                    cb(chunk)
                except Exception:
                    pass  # Never crash the audio thread

    def _validate_device(self) -> None:
        """Verify the selected device exists and supports required config."""
        if not _SD_AVAILABLE:
            return
        try:
            if self.config.device_index is not None:
                sd.check_input_settings(
                    device=self.config.device_index,
                    channels=self.config.channels,
                    dtype=self.config.dtype,
                    samplerate=self.config.sample_rate,
                )
        except sd.PortAudioError as e:
            log.error("audio_device_invalid", device=self.config.device_index, error=str(e))
            log.info("audio_falling_back_to_default")
            self.config.device_index = None  # Fall back to system default

    def _get_device_name(self) -> str:
        if not _SD_AVAILABLE:
            return "stub"
        try:
            idx = self.config.device_index if self.config.device_index is not None \
                  else sd.default.device[0]
            return sd.query_devices(idx)["name"]
        except Exception:
            return "unknown"
