# src/application/get_channel_info.py
"""Use case: details of a single channel (zone autonome — lecture seule)."""
from src.domain.ports import AsteriskGateway, DataSanitizer


class GetChannelInfoUseCase:
    """Autonomous zone: read-only, operateur minimum."""

    def __init__(self, gateway: AsteriskGateway, sanitizer: DataSanitizer):
        self._gateway = gateway
        self._sanitizer = sanitizer

    async def execute(self, channel_id: str) -> dict:
        channel = await self._gateway.get_channel(channel_id)
        return self._sanitizer.sanitize(channel.to_dict())
