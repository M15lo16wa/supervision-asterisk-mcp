# src/application/hangup_channel.py
"""Use case: hang up a channel (zone supervisée — HITL obligatoire)."""
from src.domain.ports import AsteriskGateway, DataSanitizer, HitlConfirmation


class HangupChannelUseCase:
    """Supervised zone: mandatory HITL confirmation before disconnecting a call."""

    def __init__(
        self,
        gateway: AsteriskGateway,
        hitl: HitlConfirmation,
        sanitizer: DataSanitizer,
    ):
        self._gateway = gateway
        self._hitl = hitl
        self._sanitizer = sanitizer

    async def execute(self, channel_id: str, user: str) -> dict:
        await self._hitl.confirm(
            action="hangup_channel",
            user=user,
            details={"channel_id": channel_id},
        )
        result = await self._gateway.hangup(channel_id)
        return self._sanitizer.sanitize(result.to_dict())
