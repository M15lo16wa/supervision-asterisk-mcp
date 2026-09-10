# src/application/transfer_call.py
"""Use case: transfer a channel to another destination (zone supervisée — HITL)."""
from src.domain.ports import AsteriskGateway, DataSanitizer, HitlConfirmation


class TransferCallUseCase:
    """Supervised zone: mandatory HITL confirmation before redirecting a call."""

    def __init__(
        self,
        gateway: AsteriskGateway,
        hitl: HitlConfirmation,
        sanitizer: DataSanitizer,
    ):
        self._gateway = gateway
        self._hitl = hitl
        self._sanitizer = sanitizer

    async def execute(
        self,
        channel_id: str,
        destination: str,
        context: str,
        user: str,
        attended: bool = False,
    ) -> dict:
        await self._hitl.confirm(
            action="transfer_call",
            user=user,
            details={
                "channel_id": channel_id,
                "destination": destination,
                "context": context,
                "attended": attended,
            },
        )
        result = await self._gateway.transfer(channel_id, destination, context, attended=attended)
        return self._sanitizer.sanitize(result.to_dict())
