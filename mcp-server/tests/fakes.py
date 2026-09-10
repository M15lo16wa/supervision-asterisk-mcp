"""In-memory test doubles for the domain ports."""
from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.entities import (
    CallDetailRecord,
    CallQuality,
    Channel,
    ChannelState,
    Extension,
    ExtensionState,
    HangupResult,
    OriginateResult,
    SpyMode,
    SpyResult,
    TransferResult,
)
from src.domain.exceptions import HitlConfirmationDenied, UnauthorizedAction
from src.domain.ports import (
    AsteriskGateway,
    DataSanitizer,
    HitlConfirmation,
    LanguageModel,
    SecurityManager,
    SpeechToText,
    TextToSpeech,
)


class FakeToken:
    """Stand-in for fastmcp's AccessToken."""

    def __init__(self, roles: list[str], username: str = "tester"):
        self.claims = {"realm_access": {"roles": roles}, "preferred_username": username}
        self.client_id = "mcp-server"
        self.token = "fake"
        self.scopes = []


class FakeSecurityManager(SecurityManager):
    def __init__(self, roles: list[str]):
        self._roles = set(roles)

    async def verify_token(self, token: str):
        return FakeToken(sorted(self._roles))

    def require_role(self, token, required_role: str) -> None:
        from src.security.rbac import require_role

        require_role(token, required_role)


class RecordingHitl(HitlConfirmation):
    def __init__(self, approve: bool = True):
        self.approve = approve
        self.calls: list[tuple[str, str, dict | None]] = []

    async def confirm(self, action: str, user: str, details: dict | None = None) -> bool:
        self.calls.append((action, user, details))
        if not self.approve:
            raise HitlConfirmationDenied(f"denied {action}")
        return True


class PassthroughSanitizer(DataSanitizer):
    """Marks that it ran without mutating structure, so assertions stay simple."""

    def __init__(self):
        self.seen = []

    def sanitize(self, value):
        self.seen.append(value)
        return value


@dataclass
class FakeAsteriskGateway(AsteriskGateway):
    channels: list[Channel] = field(default_factory=list)
    extensions: list[Extension] = field(default_factory=list)
    cdr: list[CallDetailRecord] = field(default_factory=list)
    quality: CallQuality | None = None
    calls: list[tuple] = field(default_factory=list)

    async def list_channels(self):
        return list(self.channels)

    async def list_extensions(self, context: str | None = None):
        return [e for e in self.extensions if context is None or e.context == context]

    async def get_recent_cdr(self, limit: int = 20):
        return list(self.cdr)[:limit]

    async def get_channel_quality(self, channel_id: str):
        return self.quality or CallQuality.from_rtcp(channel_id, "1", 20, 5, 1, 199)

    async def originate(self, endpoint, context, exten):
        self.calls.append(("originate", endpoint, context, exten))
        return OriginateResult(channel_id="PJSIP/x-1", channel_name=endpoint, status="success")

    async def hangup(self, channel_id):
        self.calls.append(("hangup", channel_id))
        return HangupResult(channel_id=channel_id, status="success")

    async def transfer(self, channel_id, destination, context, attended=False):
        self.calls.append(("transfer", channel_id, destination, context, attended))
        return TransferResult(channel_id, destination, context, attended, "success")

    async def start_spy(self, target_channel, supervisor_endpoint, mode=SpyMode.LISTEN):
        self.calls.append(("spy", target_channel, supervisor_endpoint, mode))
        return SpyResult(target_channel, "Local/spy-1", supervisor_endpoint, mode, "success")


def sample_channel(cid="PJSIP/1001-0001", state=ChannelState.UP) -> Channel:
    return Channel(
        id=cid, name=cid, state=state, caller_id_num="1001", caller_id_name="Agent",
    )


def sample_extension(ext="1001") -> Extension:
    return Extension(extension=ext, context="internal", state=ExtensionState.NOT_INUSE)


class FakeElicitContext:
    """Fake fastmcp Context.elicit returning a chosen result type."""

    def __init__(self, result):
        self._result = result

    async def elicit(self, message, response_type):
        return self._result
