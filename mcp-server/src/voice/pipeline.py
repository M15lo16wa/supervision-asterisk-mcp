# src/voice/pipeline.py
"""Speech-to-Speech orchestration with an explicit latency budget.

Per turn:  slin16 utterance ─▶ STT ─▶ LLM (streamed) ─▶ TTS (per sentence) ─▶ slin16

Latency strategy to stay under ``latency_budget_ms`` (default 1500 ms):
  * endpointing emits the utterance as soon as speech stops (audio.UtteranceDetector);
  * the LLM is streamed and TTS starts on the first complete sentence, so audio
    playback begins well before the full reply is generated;
  * STT uses beam_size=1 and a small model by default;
  * ``num_predict`` caps the reply length.
Each stage is timed; ``VoiceTurn.within_budget`` reports whether STT+LLM+TTS of
the *first* audible sentence fit the budget.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from src.domain.entities import VoiceTurn
from src.domain.exceptions import VoicePipelineError
from src.domain.ports import LanguageModel, SpeechToText, TextToSpeech
from src.observability import metrics
from src.voice.audio import UtteranceDetector, iter_frames
from src.voice.config import VoiceSettings

logger = logging.getLogger(__name__)

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n+")

AudioSink = Callable[[bytes], Awaitable[None]]


def _split_sentences(text: str) -> tuple[list[str], str]:
    """Return (complete sentences, trailing incomplete fragment)."""
    parts = _SENTENCE_END.split(text)
    if len(parts) == 1:
        return [], text
    return [p.strip() for p in parts[:-1] if p.strip()], parts[-1]


class S2SPipeline:
    def __init__(
        self,
        stt: SpeechToText,
        llm: LanguageModel,
        tts: TextToSpeech,
        settings: VoiceSettings,
    ):
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._s = settings
        self._history: list[dict] = []

    # ---------------------------------------------------------------- one turn
    async def process_utterance(self, pcm16: bytes, channel_id: str, sink: AudioSink) -> VoiceTurn:
        turn = VoiceTurn(channel_id=channel_id)
        budget = self._s.latency_budget_ms

        t0 = time.perf_counter()
        transcript = await self._stt.transcribe(pcm16, self._s.sample_rate)
        turn.stt_ms = (time.perf_counter() - t0) * 1000
        turn.transcript = transcript
        if not transcript:
            turn.timings = {"note": "empty transcript, skipped"}
            return turn

        # Stream the LLM; synthesise + play each sentence as soon as it is ready.
        llm_start = time.perf_counter()
        first_audio_at: float | None = None
        pending = ""
        full_reply: list[str] = []
        tts_total = 0.0

        try:
            async for piece in self._llm.stream_reply(transcript, self._history):
                pending += piece
                sentences, pending = _split_sentences(pending)
                for sentence in sentences:
                    full_reply.append(sentence)
                    tts_t0 = time.perf_counter()
                    audio = await self._tts.synthesize(sentence, self._s.sample_rate)
                    tts_total += (time.perf_counter() - tts_t0) * 1000
                    if audio:
                        await sink(audio)
                        if first_audio_at is None:
                            first_audio_at = time.perf_counter()
            if pending.strip():
                full_reply.append(pending.strip())
                tts_t0 = time.perf_counter()
                audio = await self._tts.synthesize(pending.strip(), self._s.sample_rate)
                tts_total += (time.perf_counter() - tts_t0) * 1000
                if audio:
                    await sink(audio)
                    if first_audio_at is None:
                        first_audio_at = time.perf_counter()
        except Exception as e:
            raise VoicePipelineError(f"generation failed: {e}") from e

        turn.llm_ms = (time.perf_counter() - llm_start) * 1000
        turn.tts_ms = tts_total
        turn.response_text = " ".join(full_reply).strip()
        turn.total_ms = (time.perf_counter() - t0) * 1000

        time_to_first_audio_ms = (
            (first_audio_at - t0) * 1000 if first_audio_at is not None else turn.total_ms
        )
        turn.within_budget = time_to_first_audio_ms <= budget
        turn.timings = {
            "time_to_first_audio_ms": round(time_to_first_audio_ms, 1),
            "budget_ms": budget,
            "stt_ms": round(turn.stt_ms, 1),
            "llm_ms": round(turn.llm_ms, 1),
            "tts_ms": round(turn.tts_ms, 1),
        }
        if not turn.within_budget:
            logger.warning(
                "latency budget exceeded on %s: first audio in %.0f ms (budget %d ms)",
                channel_id, time_to_first_audio_ms, budget,
            )
        metrics.record_voice_turn(turn.timings, turn.within_budget)

        self._history.append({"role": "user", "content": transcript})
        self._history.append({"role": "assistant", "content": turn.response_text})
        self._history[:] = self._history[-12:]
        return turn

    # ------------------------------------------------------------ full stream
    async def run(
        self,
        frames: AsyncIterator[bytes],
        sink: AudioSink,
        channel_id: str = "unknown",
        on_turn: Callable[[VoiceTurn], None] | None = None,
    ) -> None:
        """Drive the pipeline from a stream of raw slin16 bytes until it ends."""
        detector = UtteranceDetector(
            sample_rate=self._s.sample_rate,
            frame_ms=self._s.frame_ms,
            energy_threshold=self._s.vad_energy_threshold,
            silence_hangover_ms=self._s.silence_hangover_ms,
            max_utterance_ms=self._s.max_utterance_ms,
            min_utterance_ms=self._s.min_utterance_ms,
        )
        frame_bytes = self._s.frame_bytes
        leftover = b""
        async for chunk in frames:
            data = leftover + chunk
            usable = len(data) - (len(data) % frame_bytes)
            leftover = data[usable:]
            for frame in iter_frames(data[:usable], frame_bytes):
                result = detector.push(frame)
                if not result.utterance_ended:
                    continue
                try:
                    turn = await self.process_utterance(result.pcm, channel_id, sink)
                    if on_turn:
                        on_turn(turn)
                except VoicePipelineError as e:
                    logger.error("turn dropped on %s: %s", channel_id, e)
                await asyncio.sleep(0)
