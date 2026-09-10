# src/application/list_active_channels.py
"""Use case: List all active Asterisk channels (autonomous zone, RBAC only)."""
from src.domain.ports import AriGateway, DataSanitizer
from src.domain.entities import Channel


class ListActiveChannelsUseCase:
    """Autonomous zone: Read-only operation, no HITL confirmation needed.
    
    Only requires RBAC check (operateur role minimum).
    """

    def __init__(self, ari_gateway: AriGateway, sanitizer: DataSanitizer):
        self._ari = ari_gateway
        self._sanitizer = sanitizer

    async def execute(self) -> list[dict]:
        """List active channels from Asterisk.
        
        Returns:
            Sanitized list of channel dictionaries.
        """
        channels: list[Channel] = await self._ari.list_channels()
        result = [channel.to_dict() for channel in channels]
        return self._sanitizer.sanitize(result)
