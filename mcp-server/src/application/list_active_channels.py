# src/application/list_active_channels.py
from src.domain.ports import AriGateway

class ListActiveChannelsUseCase:
    """Zone autonome — lecture seule, aucune confirmation HITL requise."""

    def __init__(self, ari_gateway: AriGateway) -> None:
        self._ari = ari_gateway

    async def execute(self) -> list[dict]:
        channels = await self._ari.list_channels()
        return [c.to_dict() for c in channels]
