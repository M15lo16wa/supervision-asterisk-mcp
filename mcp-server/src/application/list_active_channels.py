# src/application/list_active_channels.py
"""Use case: list active channels (zone autonome — lecture seule)."""
from src.domain.ports import AsteriskGateway, DataSanitizer


class ListActiveChannelsUseCase:
    """Autonomous zone: read-only, RBAC only (operateur minimum), no HITL."""

    def __init__(self, gateway: AsteriskGateway, sanitizer: DataSanitizer):
        self._gateway = gateway
        self._sanitizer = sanitizer

    async def execute(self) -> list:
        channels = await self._gateway.list_channels()
        return self._sanitizer.sanitize([c.to_dict() for c in channels])
