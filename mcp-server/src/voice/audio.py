# src/voice/audio.py
"""slin16 audio helpers: framing, energy VAD, utterance endpointing.

slin16 = signed linear PCM, 16-bit little-endian, mono. Asterisk External Media
delivers it at 16 kHz when the channel format is ``slin16``.
"""
from __future__ import annotations

import array
import collections
import math
from dataclasses import dataclass

try:  # stdlib until 3.12, `audioop-lts` backport on 3.13+
    import audioop  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.13+ without backport
    audioop = None


def frame_rms(pcm16: bytes) -> float:
    """Root-mean-square amplitude of a PCM16 buffer (0..32767)."""
    if not pcm16:
        return 0.0
    if audioop is not None:
        return float(audioop.rms(pcm16, 2))
    samples = array.array("h")
    samples.frombytes(pcm16[: len(pcm16) - (len(pcm16) % 2)])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def iter_frames(pcm16: bytes, frame_bytes: int):
    """Yield fixed-size frames; a trailing partial frame is dropped."""
    for i in range(0, len(pcm16) - frame_bytes + 1, frame_bytes):
        yield pcm16[i : i + frame_bytes]


def resample(pcm16: bytes, src_rate: int, dst_rate: int, state=None):
    """Resample mono PCM16. Returns ``(data, state)`` for streaming use."""
    if src_rate == dst_rate:
        return pcm16, state
    if audioop is None:  # pragma: no cover
        raise RuntimeError("resampling requires the 'audioop' module (add 'audioop-lts' on Python 3.13+)")
    return audioop.ratecv(pcm16, 2, 1, src_rate, dst_rate, state)


@dataclass
class VadResult:
    speech: bool
    utterance_ended: bool
    pcm: bytes = b""          # full utterance audio when utterance_ended is True


class UtteranceDetector:
    """Energy-based endpointer.

    Accumulates frames while there is speech; emits the whole utterance once
    ``silence_hangover_ms`` of trailing silence is seen (or the utterance hits
    ``max_utterance_ms``). Pre-roll frames are kept so the first phoneme is not
    clipped.
    """

    def __init__(
        self,
        sample_rate: int,
        frame_ms: int,
        energy_threshold: float,
        silence_hangover_ms: int,
        max_utterance_ms: int,
        min_utterance_ms: int,
        preroll_ms: int = 160,
    ):
        self.frame_ms = frame_ms
        self.energy_threshold = energy_threshold
        self._hangover_frames = max(1, silence_hangover_ms // frame_ms)
        self._max_frames = max(1, max_utterance_ms // frame_ms)
        self._min_frames = max(1, min_utterance_ms // frame_ms)
        self._preroll = collections.deque(maxlen=max(1, preroll_ms // frame_ms))
        self._buf: list[bytes] = []
        self._in_speech = False
        self._silence_run = 0

    def reset(self) -> None:
        self._buf.clear()
        self._in_speech = False
        self._silence_run = 0

    def push(self, frame: bytes) -> VadResult:
        is_speech = frame_rms(frame) >= self.energy_threshold

        if not self._in_speech:
            self._preroll.append(frame)
            if is_speech:
                self._in_speech = True
                self._buf = list(self._preroll)
                self._silence_run = 0
            return VadResult(speech=is_speech, utterance_ended=False)

        # in speech
        self._buf.append(frame)
        self._silence_run = 0 if is_speech else self._silence_run + 1

        too_long = len(self._buf) >= self._max_frames
        silent_enough = self._silence_run >= self._hangover_frames
        if too_long or silent_enough:
            pcm = b"".join(self._buf)
            long_enough = len(self._buf) - self._silence_run >= self._min_frames
            self.reset()
            if long_enough:
                return VadResult(speech=False, utterance_ended=True, pcm=pcm)
            return VadResult(speech=False, utterance_ended=False)

        return VadResult(speech=is_speech, utterance_ended=False)
