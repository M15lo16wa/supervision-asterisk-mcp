# src/voice/tts.py
"""Text-to-Speech — Piper. Produces slin16 PCM at the pipeline sample rate."""
from __future__ import annotations

import asyncio
import logging
import wave
from io import BytesIO
from pathlib import Path

from src.domain.ports import TextToSpeech
from src.voice.audio import resample
from src.voice.config import VoiceSettings

logger = logging.getLogger(__name__)


class PiperTTS(TextToSpeech):
    def __init__(self, settings: VoiceSettings):
        self._s = settings
        self._voice = None

    def _ensure_voice(self):
        if self._voice is None:
            from piper import PiperVoice  # deferred heavy import

            model = Path(self._s.tts_model_dir) / f"{self._s.tts_voice}.onnx"
            logger.info("loading Piper voice %s", model)
            self._voice = PiperVoice.load(str(model))
        return self._voice

    def _synth_sync(self, text: str, sample_rate: int) -> bytes:
        voice = self._ensure_voice()
        buf = BytesIO()
        with wave.open(buf, "wb") as wav:
            voice.synthesize(text, wav)
        buf.seek(0)
        with wave.open(buf, "rb") as wav:
            src_rate = wav.getframerate()
            pcm = wav.readframes(wav.getnframes())
        pcm, _ = resample(pcm, src_rate, sample_rate)
        return pcm

    async def synthesize(self, text: str, sample_rate: int = 16000) -> bytes:
        text = (text or "").strip()
        if not text:
            return b""
        return await asyncio.to_thread(self._synth_sync, text, sample_rate)


class SilenceTTS(TextToSpeech):
    """Test double: returns silence sized to the text length, no model required."""

    async def synthesize(self, text: str, sample_rate: int = 16000) -> bytes:
        millis = min(4000, 60 * max(1, len(text.split())))
        return b"\x00\x00" * int(sample_rate * millis / 1000)


def build_tts(settings: VoiceSettings) -> TextToSpeech:
    try:
        import piper  # noqa: F401
    except ModuleNotFoundError:
        logger.warning("piper-tts not installed — using SilenceTTS (install extra 'voice')")
        return SilenceTTS()
    return PiperTTS(settings)
