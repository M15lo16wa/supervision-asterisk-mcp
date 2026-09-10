# src/domain/ports.py
"""Abstract interfaces (ports) for the domain layer.

These define contracts without implementation details. Concrete adapters live
in ``src/adapters`` (Asterisk) and ``src/voice`` (Speech-to-Speech engines).
"""
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # le domaine ne dépend pas du framework à l'exécution
    from fastmcp.server.auth import AccessToken
else:
    AccessToken = Any

from src.domain.entities import (
    CallDetailRecord,
    CallQuality,
    Channel,
    Extension,
    HangupResult,
    OriginateResult,
    QueueStats,
    SpyMode,
    SpyResult,
    TransferResult,
    TrunkUtilization,
)


class AsteriskGateway(ABC):
    """Port: read events from and send commands to Asterisk (AMI/ARI).

    Split conceptually into three zones aligned with the RBAC matrix:
      * lecture   (operateur+)   : list_channels, list_extensions, get_cdr
      * analyse   (superviseur+) : get_channel_quality
      * pilotage  (admin)        : originate, hangup, transfer, start_spy
    """

    # ---- lecture -------------------------------------------------------------
    @abstractmethod
    async def list_channels(self) -> list[Channel]:
        """List all active channels."""
        ...

    @abstractmethod
    async def get_channel(self, channel_id: str) -> Channel:
        """Return one channel's details. Raises ChannelNotFound if absent."""
        ...

    @abstractmethod
    async def list_extensions(self, context: str | None = None) -> list[Extension]:
        """List dialplan extensions/hints and their device state."""
        ...

    @abstractmethod
    async def get_queues(self) -> list[QueueStats]:
        """Return call-queue statistics (waiting calls, members, SLA, hold time)."""
        ...

    # ---- analyse ------------------------------------------------------------
    @abstractmethod
    async def get_recent_cdr(self, limit: int = 20) -> list[CallDetailRecord]:
        """Return the most recent Call Detail Records."""
        ...

    @abstractmethod
    async def get_channel_quality(self, channel_id: str) -> CallQuality:
        """Return RTP/RTCP quality metrics (jitter, loss, RTT, estimated MOS)."""
        ...

    @abstractmethod
    async def get_trunks(self) -> list[TrunkUtilization]:
        """Return per-trunk load (active vs. configured channels)."""
        ...

    # ---- pilotage --------------------------------------------------------------
    @abstractmethod
    async def originate(self, endpoint: str, context: str, exten: str) -> OriginateResult:
        """Originate a new call towards ``endpoint`` then send it to context/exten."""
        ...

    @abstractmethod
    async def hangup(self, channel_id: str) -> HangupResult:
        """Hang up a channel."""
        ...

    @abstractmethod
    async def transfer(
        self,
        channel_id: str,
        destination: str,
        context: str,
        attended: bool = False,
    ) -> TransferResult:
        """Blind (default) or attended transfer of a channel to a destination."""
        ...

    @abstractmethod
    async def start_spy(
        self,
        target_channel: str,
        supervisor_endpoint: str,
        mode: SpyMode = SpyMode.LISTEN,
    ) -> SpyResult:
        """Start supervision (ChanSpy) of ``target_channel`` for a supervisor."""
        ...

    async def close(self) -> None:  # pragma: no cover - optional lifecycle hook
        """Release the underlying connection, if any."""
        return None


class HitlConfirmation(ABC):
    """Port: Human-in-the-Loop confirmation mechanism."""

    @abstractmethod
    async def confirm(self, action: str, user: str, details: dict | None = None) -> bool:
        """Request explicit human confirmation for an action.

        Returns ``True`` if confirmed. Raises ``HitlConfirmationDenied`` when the
        human declines, cancels, or does not answer.
        """
        ...


class SecurityManager(ABC):
    """Port: Authentication and RBAC enforcement."""

    @abstractmethod
    async def verify_token(self, token: str) -> AccessToken:
        """Verify and decode a JWT. Raises on invalid/expired tokens."""
        ...

    @abstractmethod
    def require_role(self, token: AccessToken, required_role: str) -> None:
        """Raise ``UnauthorizedAction`` if the token lacks ``required_role``."""
        ...


class DataSanitizer(ABC):
    """Port: protection against indirect prompt injection."""

    @abstractmethod
    def sanitize(self, value: Any) -> Any:
        """Wrap untrusted external data in an explicit envelope and neutralise
        suspicious instruction-like patterns. Recurses through str/dict/list."""
        ...


# ─────────────────────────────  Module 3 : voix  ──────────────────────────────

class SpeechToText(ABC):
    """Port: convert a slin16 PCM buffer into text."""

    @abstractmethod
    async def transcribe(self, pcm16: bytes, sample_rate: int = 16000) -> str:
        ...


class LanguageModel(ABC):
    """Port: generate an assistant reply from a user utterance."""

    @abstractmethod
    async def reply(self, prompt: str, history: list[dict] | None = None) -> str:
        ...

    async def stream_reply(  # pragma: no cover - default wraps reply()
        self, prompt: str, history: list[dict] | None = None
    ) -> AsyncIterator[str]:
        yield await self.reply(prompt, history)


class TextToSpeech(ABC):
    """Port: synthesise slin16 PCM from text."""

    @abstractmethod
    async def synthesize(self, text: str, sample_rate: int = 16000) -> bytes:
        ...
