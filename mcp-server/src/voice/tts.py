# src/voice/tts.py
"""Text-to-Speech — Piper. Produces slin16 PCM at the pipeline sample rate."""
from __future__ import annotations

import asyncio
import logging
import wave
from io import BytesIO
from pathlib import Path

from src.config import _env_bool
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

    def _render(self, text: str) -> tuple[bytes, int]:
        """Return (pcm16 natif, sample_rate) for `text`."""
        voice = self._ensure_voice()
        if hasattr(voice, "synthesize_wav"):
            buf = BytesIO()
            with wave.open(buf, "wb") as wav:
                voice.synthesize_wav(text, wav)
            buf.seek(0)
            with wave.open(buf, "rb") as wav:
                return wav.readframes(wav.getnframes()), wav.getframerate()
        # piper < 1.2 : API par morceaux
        pcm = b""
        rate = 22050
        for chunk in voice.synthesize(text):  # pragma: no cover - version ancienne
            pcm += getattr(chunk, "audio_int16_bytes", b"")
            rate = getattr(chunk, "sample_rate", rate)
        return pcm, rate

    def _synth_sync(self, text: str, sample_rate: int) -> bytes:
        pcm, src_rate = self._render(text)
        pcm, _ = resample(pcm, src_rate, sample_rate)
        return pcm

    async def synthesize(self, text: str, sample_rate: int = 16000) -> bytes:
        text = (text or "").strip()
        if not text:
            return b""
        return await asyncio.to_thread(self._synth_sync, text, sample_rate)


class SilenceTTS(TextToSpeech):
    """Test double: returns silence sized to the text length, no model required.

    Uniquement sur banc de test : ``VOICE_ALLOW_STUB=1``. En production, un
    modèle manquant doit faire échouer le démarrage, sinon le conteneur paraît
    sain alors qu'il ne rend que du silence.
    """

    async def synthesize(self, text: str, sample_rate: int = 16000) -> bytes:
        millis = min(4000, 60 * max(1, len(text.split())))
        return b"\x00\x00" * int(sample_rate * millis / 1000)


def build_tts(settings: VoiceSettings) -> TextToSpeech:
    if _env_bool("VOICE_ALLOW_STUB", False):
        logger.warning("VOICE_ALLOW_STUB=1 — TTS silencieux (aucune voix Piper)")
        return SilenceTTS()
    try:
        import piper  # noqa: F401
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "piper-tts absent : installez l'extra 'voice' dans l'image vocale "
            "(ou VOICE_ALLOW_STUB=1 pour un banc de test silencieux)"
        ) from e
    model = Path(settings.tts_model_dir) / f"{settings.tts_voice}.onnx"
    if not model.is_file():
        raise RuntimeError(
            f"voix Piper absente : {model} — montez le volume de modèles ou "
            "téléchargez la voix dans l'image (voir Dockerfile.voice)"
        )
    return PiperTTS(settings)
