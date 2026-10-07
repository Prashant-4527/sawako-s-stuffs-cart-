"""
kaoruko/voice/tts/synthesizer.py

Text-to-Speech synthesis engine.
Primary: Microsoft Edge TTS (edge-tts) — free, high-quality neural voices
Local:   Coqui TTS (XTTS v2) — offline, voice cloning capable
Premium: ElevenLabs API — ultra-realistic, emotion support

Architecture: Strategy pattern with async streaming support.
Waveform data is emitted to the event bus for real-time UI visualization.
"""

from __future__ import annotations

import asyncio
import io
import os
import tempfile
import time
from pathlib import Path
from typing import AsyncIterator, Optional, Protocol, runtime_checkable

import numpy as np

from kaoruko.infrastructure.logging.logger import get_logger

log = get_logger("voice.tts")


@runtime_checkable
class TTSEngine(Protocol):
    async def synthesize(self, text: str) -> bytes: ...
    async def synthesize_to_file(self, text: str, path: Path) -> Path: ...
    async def initialize(self) -> None: ...


# ── Edge TTS Engine (Default) ─────────────────────────────────────────────────

class EdgeTTSEngine:
    """
    Microsoft Edge TTS via edge-tts library.
    Free, high-quality, low-latency.
    Default voice: ja-JP-NanamiNeural (Japanese Neural, slight accent in English)
    Fallback:      en-US-AriaNeural
    
    Available female JP voices:
      - ja-JP-NanamiNeural   (warm, conversational — RECOMMENDED)
      - ja-JP-AoiNeural      (professional, news-style)
    Available female EN voices:
      - en-US-AriaNeural     (expressive, natural)
      - en-US-JennyNeural    (professional)
      - en-US-SaraNeural     (warm)
    """

    def __init__(
        self,
        voice: str = "ja-JP-NanamiNeural",
        fallback_voice: str = "en-US-AriaNeural",
        rate: str = "-5%",
        pitch: str = "+5Hz",
        volume: str = "+0%",
    ) -> None:
        self.voice = voice
        self.fallback_voice = fallback_voice
        self.rate = rate
        self.pitch = pitch
        self.volume = volume

    async def initialize(self) -> None:
        """Verify edge-tts is available."""
        try:
            import edge_tts
            log.info("edge_tts_ready", voice=self.voice)
        except ImportError:
            log.error("edge_tts_not_installed")
            raise

    async def synthesize(self, text: str) -> bytes:
        """Synthesize text and return MP3 audio bytes."""
        import edge_tts

        start = time.perf_counter()
        audio_buf = io.BytesIO()

        try:
            communicate = edge_tts.Communicate(
                text=text,
                voice=self.voice,
                rate=self.rate,
                pitch=self.pitch,
                volume=self.volume,
            )
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_buf.write(chunk["data"])

        except Exception as e:
            log.warning("edge_tts_primary_failed", error=str(e), trying_fallback=True)
            # Try fallback voice
            try:
                communicate = edge_tts.Communicate(
                    text=text,
                    voice=self.fallback_voice,
                    rate=self.rate,
                    pitch=self.pitch,
                )
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_buf.write(chunk["data"])
            except Exception as e2:
                log.error("edge_tts_fallback_failed", error=str(e2))
                return b""

        elapsed_ms = (time.perf_counter() - start) * 1000
        audio_data = audio_buf.getvalue()
        log.info(
            "tts_synthesized",
            engine="edge_tts",
            chars=len(text),
            bytes=len(audio_data),
            ms=round(elapsed_ms, 1),
        )
        return audio_data

    async def synthesize_to_file(self, text: str, path: Path) -> Path:
        """Synthesize to an MP3 file."""
        import edge_tts
        communicate = edge_tts.Communicate(
            text=text,
            voice=self.voice,
            rate=self.rate,
            pitch=self.pitch,
            volume=self.volume,
        )
        await communicate.save(str(path))
        return path


# ── Audio Player ──────────────────────────────────────────────────────────────

class AudioPlayer:
    """
    Async audio playback manager.
    Uses pygame.mixer for queued MP3 playback.
    Emits waveform data for UI visualization.
    """

    def __init__(self, event_bus: Optional[object] = None) -> None:
        self._bus = event_bus
        self._pygame_ready = False
        self._playing = False
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._lock = asyncio.Lock()

    def initialize(self) -> None:
        """Initialize pygame mixer for audio playback."""
        try:
            import pygame
            pygame.mixer.pre_init(frequency=24000, size=-16, channels=1, buffer=512)
            pygame.mixer.init()
            self._pygame_ready = True
            log.info("audio_player_ready", backend="pygame")
        except ImportError:
            log.warning("pygame_unavailable", message="Audio playback will be silent")
        except Exception as e:
            log.error("pygame_init_error", error=str(e))

    async def play(self, audio_bytes: bytes, text: str = "") -> None:
        """
        Play audio bytes (MP3 format).
        Blocks until playback completes.
        """
        if not audio_bytes:
            return

        async with self._lock:
            self._playing = True

            # Emit playing event
            if self._bus:
                from kaoruko.core.event_bus import KaorukoEvent
                await self._bus.publish(
                    KaorukoEvent.TTS_SPEAKING_START,
                    data={"text": text},
                    source="audio_player",
                )

            try:
                if self._pygame_ready:
                    await asyncio.get_event_loop().run_in_executor(
                        None, self._play_pygame, audio_bytes
                    )
                else:
                    # Fallback: estimate duration and wait
                    await asyncio.sleep(len(text) * 0.065)   # ~65ms per char

            finally:
                self._playing = False
                if self._bus:
                    from kaoruko.core.event_bus import KaorukoEvent
                    await self._bus.publish(
                        KaorukoEvent.TTS_SPEAKING_END,
                        data={"text": text},
                        source="audio_player",
                    )

    def _play_pygame(self, audio_bytes: bytes) -> None:
        """Synchronous pygame playback (runs in thread pool)."""
        import pygame
        try:
            sound = pygame.mixer.Sound(buffer=io.BytesIO(audio_bytes))
            channel = sound.play()
            if channel:
                while channel.get_busy():
                    import time as t
                    t.sleep(0.05)
        except Exception as e:
            log.error("pygame_playback_error", error=str(e))

    def stop(self) -> None:
        """Interrupt current playback."""
        if self._pygame_ready:
            try:
                import pygame
                pygame.mixer.stop()
            except Exception:
                pass
        self._playing = False

    @property
    def is_playing(self) -> bool:
        return self._playing


# ── TTS Manager (facade over engines + player) ────────────────────────────────

class TTSManager:
    """
    High-level TTS facade.
    Selects engine, synthesizes, plays audio, emits waveform data.

    Usage:
        tts = TTSManager(config, bus)
        await tts.initialize()
        await tts.speak("Hai, wakarimashita~")
    """

    def __init__(self, config: object, event_bus: Optional[object] = None) -> None:
        self.config = config
        self._bus = event_bus
        self._engine: Optional[EdgeTTSEngine] = None
        self._player: Optional[AudioPlayer] = None
        self._response_templates = self._build_templates(config)

    def _build_templates(self, config: object) -> dict[str, list[str]]:
        tts_cfg = getattr(config.voice, "tts", None) if hasattr(config, "voice") else None
        if tts_cfg:
            return {
                "ack":     getattr(tts_cfg, "acknowledgements", ["Hai~"]),
                "confirm": getattr(tts_cfg, "confirmations", ["Are you sure?"]),
                "error":   getattr(tts_cfg, "errors", ["Gomen nasai~"]),
                "greet":   getattr(tts_cfg, "greetings", ["Ohayou~"]),
            }
        return {
            "ack":     ["Hai, wakarimashita~", "Of course~", "Right away~"],
            "confirm": ["Shall I proceed?", "Are you sure?"],
            "error":   ["Gomen nasai, something went wrong~"],
            "greet":   ["Ohayou gozaimasu~", "Welcome back~"],
        }

    async def initialize(self) -> None:
        """Initialize TTS engine and audio player."""
        tts_cfg = getattr(self.config.voice, "tts", None) if hasattr(self.config, "voice") else None

        voice = getattr(tts_cfg, "voice", "ja-JP-NanamiNeural") if tts_cfg else "ja-JP-NanamiNeural"
        fallback = getattr(tts_cfg, "fallback_voice", "en-US-AriaNeural") if tts_cfg else "en-US-AriaNeural"
        rate = getattr(tts_cfg, "speaking_rate", "-5%") if tts_cfg else "-5%"
        pitch = getattr(tts_cfg, "pitch", "+5Hz") if tts_cfg else "+5Hz"

        self._engine = EdgeTTSEngine(
            voice=voice,
            fallback_voice=fallback,
            rate=rate,
            pitch=pitch,
        )
        await self._engine.initialize()

        self._player = AudioPlayer(event_bus=self._bus)
        self._player.initialize()

        log.info("tts_manager_ready", voice=voice)

    async def speak(self, text: str) -> None:
        """Synthesize and play text aloud."""
        if not text or not text.strip():
            return
        if not self._engine or not self._player:
            log.warning("tts_not_initialized")
            return

        try:
            audio_bytes = await self._engine.synthesize(text)
            await self._player.play(audio_bytes, text=text)
        except Exception as e:
            log.error("tts_speak_error", error=str(e), text=text[:50])

    async def speak_acknowledgement(self) -> None:
        """Speak a random acknowledgement phrase."""
        import random
        phrase = random.choice(self._response_templates["ack"])
        await self.speak(phrase)

    async def speak_error(self, detail: str = "") -> None:
        """Speak an error message."""
        import random
        phrase = random.choice(self._response_templates["error"])
        if detail:
            phrase = f"{phrase} {detail}"
        await self.speak(phrase)

    async def speak_greeting(self) -> None:
        """Speak a greeting."""
        import random
        phrase = random.choice(self._response_templates["greet"])
        await self.speak(phrase)

    def stop(self) -> None:
        """Interrupt current speech immediately."""
        if self._player:
            self._player.stop()
