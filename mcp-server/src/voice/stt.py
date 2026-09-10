# src/voice/stt.py
"""Speech-to-Text — faster-whisper (CTranslate2). Lazy model load, off-thread inference."""
from __future__ import annotations

import asyncio
import logging

from src.domain.ports import SpeechToText
from src.voice.config import VoiceSettings

logger = logging.getLogger(__name__)


class FasterWhisperSTT(SpeechToText):
    def __init__(self, settings: VoiceSettings):
        self._s = settings
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # heavy import, deferred

            logger.info("loading faster-whisper model=%s device=%s", self._s.stt_model, self._s.stt_device)
            self._model = WhisperModel(
                self._s.stt_model,
                device=self._s.stt_device,
                compute_type=self._s.stt_compute_type,
            )
        return self._model

    def _transcribe_sync(self, pcm16: bytes, sample_rate: int) -> str:
        import numpy as np

        model = self._ensure_model()
        audio = np.frombuffer(pcm16, dtype=np.int16).astype("float32") / 32768.0
        segments, _info = model.transcribe(
            audio,
            language=self._s.stt_language,
            beam_size=self._s.stt_beam_size,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    async def transcribe(self, pcm16: bytes, sample_rate: int = 16000) -> str:
        if not pcm16:
            return ""
        return await asyncio.to_thread(self._transcribe_sync, pcm16, sample_rate)


class EchoSTT(SpeechToText):
    """Test double: returns a fixed transcript, no model required."""

    def __init__(self, transcript: str = "bonjour"):
        self.transcript = transcript

    async def transcribe(self, pcm16: bytes, sample_rate: int = 16000) -> str:
        return self.transcript if pcm16 else ""


def build_stt(settings: VoiceSettings) -> SpeechToText:
    try:
        import faster_whisper  # noqa: F401
    except ModuleNotFoundError:
        logger.warning("faster-whisper not installed — using EchoSTT (install extra 'voice')")
        return EchoSTT()
    return FasterWhisperSTT(settings)
