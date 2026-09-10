"""Module 3 — VAD endpointing + S2S pipeline with lightweight doubles."""
import struct

import pytest

from src.domain.entities import CallQuality
from src.voice.audio import UtteranceDetector, frame_rms
from src.voice.config import VoiceSettings
from src.voice.llm import EchoLLM
from src.voice.pipeline import S2SPipeline, _split_sentences
from src.voice.stt import EchoSTT
from src.voice.tts import SilenceTTS


def _tone(ms: int, sample_rate=16000, amp=8000) -> bytes:
    n = int(sample_rate * ms / 1000)
    return b"".join(struct.pack("<h", amp if i % 8 < 4 else -amp) for i in range(n))


def _silence(ms: int, sample_rate=16000) -> bytes:
    return b"\x00\x00" * int(sample_rate * ms / 1000)


def test_frame_rms_distinguishes_speech_from_silence():
    assert frame_rms(_silence(20)) < 10
    assert frame_rms(_tone(20)) > 1000


def test_utterance_detector_emits_on_trailing_silence():
    s = VoiceSettings()
    det = UtteranceDetector(
        sample_rate=s.sample_rate, frame_ms=s.frame_ms,
        energy_threshold=s.vad_energy_threshold,
        silence_hangover_ms=200, max_utterance_ms=5000, min_utterance_ms=100,
    )
    stream = _tone(600) + _silence(400)
    fb = s.frame_bytes
    emitted = None
    for i in range(0, len(stream) - fb + 1, fb):
        r = det.push(stream[i:i + fb])
        if r.utterance_ended:
            emitted = r.pcm
    assert emitted is not None and len(emitted) > s.sample_rate * 2 * 0.4  # ~>=400ms


def test_split_sentences():
    done, rest = _split_sentences("Bonjour. Comment puis-je aider")
    assert done == ["Bonjour."]
    assert rest == "Comment puis-je aider"


async def test_pipeline_turn_produces_audio_and_timings():
    s = VoiceSettings()
    pipe = S2SPipeline(EchoSTT("je voudrais un rendez-vous"), EchoLLM(), SilenceTTS(), s)
    played: list[bytes] = []

    async def sink(pcm: bytes):
        played.append(pcm)

    turn = await pipe.process_utterance(_tone(500), "chan-1", sink)
    assert turn.transcript == "je voudrais un rendez-vous"
    assert "rendez-vous" in turn.response_text
    assert played and all(isinstance(p, (bytes, bytearray)) for p in played)
    assert turn.total_ms > 0
    assert "time_to_first_audio_ms" in turn.timings
    assert turn.within_budget is True  # doubles are instant


async def test_pipeline_skips_empty_transcript():
    s = VoiceSettings()
    pipe = S2SPipeline(EchoSTT(""), EchoLLM(), SilenceTTS(), s)
    turn = await pipe.process_utterance(b"", "c", lambda p: None)
    assert turn.response_text == ""


def test_mos_estimate_monotonic_with_loss():
    good = CallQuality.from_rtcp("c", "1", 20, 3, 0, 1000)
    bad = CallQuality.from_rtcp("c", "1", 20, 3, 200, 800)
    assert good.mos_estimate > bad.mos_estimate
