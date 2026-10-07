"""
kaoruko/voice/pipeline.py

Master Voice Pipeline — orchestrates the complete audio I/O chain.

Flow:
  [Mic] → [NoiseSuppressor] → [WakeWordDetector]
                                     ↓  (wake)
                             [VAD records utterance]
                                     ↓
                             [Whisper STT transcribes]
                                     ↓
                             [EventBus: VOICE_TRANSCRIPT_READY]
                                     ↓
                      (NLU + Execution happens in assistant.py)
                                     ↓
                             [TTS — speak response]

Fix applied:
  - _recording_active and _tts_busy are now threading.Event objects instead
    of bare booleans. The audio hardware callback runs on a dedicated OS thread
    (sounddevice), while the asyncio event loop runs on the main thread.
    A plain bool assignment is not guaranteed to be visible across threads
    without a memory barrier. threading.Event uses an internal Lock + Condition
    which provides proper cross-thread visibility and avoids subtle race conditions
    where wake-word could fire mid-utterance.

  - asyncio.get_event_loop() replaced with asyncio.get_running_loop() where
    called from within a running coroutine (start()), which is the correct
    Python 3.10+ API.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Optional, TYPE_CHECKING

from kaoruko.core.event_bus import EventBus, KaorukoEvent
from kaoruko.infrastructure.logging.logger import get_logger, bind_request_context
from kaoruko.voice.stt.audio_capture import AudioCapture, AudioConfig as AudioCaptureConfig
from kaoruko.voice.stt.noise_suppressor import NoiseSuppressor
from kaoruko.voice.stt.vad import SileroVAD, VADConfig
from kaoruko.voice.stt.recognizer import AutoSTTEngine, WhisperSTTEngine
from kaoruko.voice.wake_word.detector import WakeWordDetector
from kaoruko.voice.tts.synthesizer import TTSManager
from kaoruko.nlu.language_detector import LanguageDetector

if TYPE_CHECKING:
    from kaoruko.infrastructure.config.schema import KaorukoConfig

log = get_logger("voice.pipeline")


class VoicePipeline:
    """
    End-to-end voice processing pipeline.

    Manages:
    - Audio capture stream (sounddevice)
    - Noise suppression (noisereduce)
    - Wake word detection (OpenWakeWord)
    - VAD-gated utterance recording (Silero)
    - Speech recognition (faster-whisper)
    - TTS response synthesis and playback (Edge TTS)
    - Event bus integration throughout

    Thread model:
    - Audio capture: hardware callback thread (sounddevice)
    - Wake word detection: dedicated daemon thread
    - STT inference: asyncio thread pool executor
    - TTS synthesis: asyncio coroutine
    - Pipeline coordination: main asyncio event loop

    Thread-safety:
    - _recording_active: threading.Event — set/cleared from async tasks,
      read from audio hardware thread. Provides proper memory visibility.
    - _tts_busy: threading.Event — same cross-thread pattern.
    """

    def __init__(self, config: "KaorukoConfig", bus: EventBus) -> None:
        self.config = config
        self.bus = bus

        self._audio_capture: Optional[AudioCapture] = None
        self._noise_suppressor: Optional[NoiseSuppressor] = None
        self._wake_detector: Optional[WakeWordDetector] = None
        self._vad: Optional[SileroVAD] = None
        self._stt: Optional[WhisperSTTEngine] = None
        self._tts: Optional[TTSManager] = None
        self._lang_detector: Optional[LanguageDetector] = None

        self._running = False

        # FIX: threading.Event instead of bare bool.
        # The audio hardware callback (sounddevice) runs on its own OS thread.
        # Reads/writes to a plain bool from two different threads are not
        # guaranteed to be ordered correctly without a memory barrier.
        # threading.Event uses a Lock internally, providing that barrier.
        self._recording_active = threading.Event()
        self._tts_busy = threading.Event()

        self._pipeline_task: Optional[asyncio.Task] = None
        self._sessions_processed = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """
        Initialize all components and start the pipeline.
        Order matters — audio capture must start last.
        """
        log.info("voice_pipeline_starting")
        # FIX: get_running_loop() is the correct call inside a coroutine.
        # get_event_loop() is deprecated in 3.10+ and errors in some contexts.
        loop = asyncio.get_running_loop()

        self._lang_detector = LanguageDetector()
        self._lang_detector.initialize()

        self._tts = TTSManager(config=self.config, event_bus=self.bus)
        await self._tts.initialize()

        stt_cfg = self.config.voice.stt
        self._stt = WhisperSTTEngine(
            model_size=stt_cfg.model_size.value,
            device=stt_cfg.device,
            compute_type=stt_cfg.compute_type,
            beam_size=stt_cfg.beam_size,
        )
        log.info("stt_loading", model=stt_cfg.model_size.value)
        await self._stt.initialize()
        log.info("stt_ready")

        vad_cfg = self.config.voice.stt
        self._vad = SileroVAD(VADConfig(
            sample_rate=self.config.voice.audio.sample_rate,
            threshold=vad_cfg.vad_threshold,
            min_speech_duration_ms=vad_cfg.min_speech_duration_ms,
            max_silence_duration_ms=vad_cfg.max_silence_duration_ms,
        ))
        await self._vad.initialize()

        audio_cfg = self.config.voice.audio
        self._noise_suppressor = NoiseSuppressor(
            strength=audio_cfg.noise_suppression_strength,
            sample_rate=audio_cfg.sample_rate,
            stationary=audio_cfg.noise_suppression_stationary,
        )
        self._noise_suppressor.start()

        self._audio_capture = AudioCapture(AudioCaptureConfig(
            sample_rate=audio_cfg.sample_rate,
            channels=audio_cfg.channels,
            chunk_size=audio_cfg.chunk_size,
            dtype=audio_cfg.dtype,
        ))

        ww_cfg = self.config.voice.wake_word
        self._wake_detector = WakeWordDetector(
            event_bus=self.bus,
            phrases=ww_cfg.phrases,
            sensitivity=ww_cfg.sensitivity,
            loop=loop,   # FIX: pass the explicit running loop
        )
        await self._wake_detector.initialize()

        # Wire audio → noise suppressor → wake word
        def on_audio_chunk(chunk):
            if self._noise_suppressor:
                chunk = self._noise_suppressor.process(chunk)
            # FIX: Use threading.Event.is_set() — thread-safe check
            if (
                self._wake_detector
                and not self._recording_active.is_set()
                and not self._tts_busy.is_set()
            ):
                self._wake_detector.on_audio_chunk(chunk)

        self._audio_capture.add_listener(on_audio_chunk)

        self.bus.subscribe(KaorukoEvent.VOICE_WAKE_DETECTED, self._on_wake_detected, priority=5)
        self.bus.subscribe(KaorukoEvent.TTS_SPEAKING_START, self._on_tts_start)
        self.bus.subscribe(KaorukoEvent.TTS_SPEAKING_END, self._on_tts_end)

        self._audio_capture.start()
        if ww_cfg.enabled:
            self._wake_detector.start()

        self._running = True
        log.info(
            "voice_pipeline_running",
            wake_word_enabled=ww_cfg.enabled,
            wake_phrases=ww_cfg.phrases,
        )

        await asyncio.sleep(1.0)
        if self._noise_suppressor:
            self._noise_suppressor.update_noise_profile()

    async def stop(self) -> None:
        """Gracefully stop all voice components."""
        self._running = False

        if self._wake_detector:
            self._wake_detector.stop()
        if self._audio_capture:
            self._audio_capture.stop()
        if self._noise_suppressor:
            self._noise_suppressor.stop()
        if self._pipeline_task:
            self._pipeline_task.cancel()

        log.info("voice_pipeline_stopped", sessions=self._sessions_processed)

    async def speak(self, text: str) -> None:
        """Speak a response aloud."""
        if self._tts:
            await self._tts.speak(text)
        else:
            log.warning("tts_not_ready", text=text[:50])

    # ── Event Handlers ────────────────────────────────────────────────────────

    async def _on_wake_detected(self, event) -> None:
        """Wake word detected → start recording an utterance."""
        # FIX: threading.Event.is_set() — safe cross-thread read
        if self._recording_active.is_set() or self._tts_busy.is_set():
            return

        # FIX: Set the Event atomically before launching the task,
        # preventing a second wake trigger from racing in before the task starts.
        self._recording_active.set()

        asyncio.create_task(self._play_wake_chime())
        asyncio.create_task(self._record_and_transcribe())

    async def _on_tts_start(self, event) -> None:
        self._tts_busy.set()    # FIX: threading.Event.set()

    async def _on_tts_end(self, event) -> None:
        self._tts_busy.clear()  # FIX: threading.Event.clear()

    # ── Core Pipeline ─────────────────────────────────────────────────────────

    async def _record_and_transcribe(self) -> None:
        """Full utterance capture + STT pipeline, called after wake detection."""
        start = time.perf_counter()
        try:
            log.info("utterance_recording_start")
            await self.bus.publish(KaorukoEvent.VOICE_LISTENING_START, source="voice_pipeline")

            segment = await self._vad.record_until_silence(
                audio_capture=self._audio_capture,
                max_duration_seconds=15.0,
            )

            await self.bus.publish(KaorukoEvent.VOICE_LISTENING_STOP, source="voice_pipeline")

            if segment is None or segment.audio is None:
                log.info("no_speech_detected")
                return

            clean_audio = (
                self._noise_suppressor.process_full_utterance(segment.audio)
                if self._noise_suppressor
                else segment.audio
            )

            log.info("stt_transcribing", duration_ms=round(segment.duration_ms, 1))
            result = await self._stt.transcribe(
                audio=clean_audio,
                sample_rate=self.config.voice.audio.sample_rate,
                language=None,  # Let Whisper auto-detect for multilingual support
            )

            elapsed = (time.perf_counter() - start) * 1000
            log.info(
                "utterance_complete",
                text=result.text[:80],
                language=result.language,
                confidence=round(result.confidence, 3),
                total_ms=round(elapsed, 1),
            )

            if result.is_empty():
                log.info("empty_transcript_ignored")
                return

            lang = self._lang_detector.detect(result.text) if self._lang_detector else "en"
            self._sessions_processed += 1

            await self.bus.publish(
                KaorukoEvent.VOICE_TRANSCRIPT_READY,
                data={
                    "text": result.text,
                    "language": lang,
                    "confidence": result.confidence,
                    "duration_ms": segment.duration_ms,
                    "engine": result.engine,
                },
                source="voice_pipeline",
            )

        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error("voice_pipeline_error", error=str(e))
            await self.bus.publish(
                KaorukoEvent.SYSTEM_ERROR,
                data={"component": "voice_pipeline", "error": str(e)},
                source="voice_pipeline",
            )
        finally:
            # FIX: Always clear the Event, even if an exception occurred.
            # With a bare bool, a crash mid-utterance would leave
            # _recording_active = True forever, silently killing the pipeline.
            self._recording_active.clear()

    async def _play_wake_chime(self) -> None:
        await self.bus.publish(
            KaorukoEvent.UI_ORB_STATE_CHANGED,
            data={"state": "listening"},
            source="voice_pipeline",
        )

    # ── Diagnostics ───────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_recording(self) -> bool:
        return self._recording_active.is_set()

    @property
    def is_tts_busy(self) -> bool:
        return self._tts_busy.is_set()

    def get_audio_devices(self) -> list[dict]:
        return AudioCapture.list_devices()
