# src/application/get_trunk_utilization.py
"""Use case: trunk load (zone analyse — superviseur+)."""
from src.domain.ports import AsteriskGateway, DataSanitizer


class GetTrunkUtilizationUseCase:
    """Analysis zone: read-only aggregation, superviseur minimum."""

    def __init__(self, gateway: AsteriskGateway, sanitizer: DataSanitizer):
        self._gateway = gateway
        self._sanitizer = sanitizer

    async def execute(self) -> list:
        trunks = await self._gateway.get_trunks()
        return self._sanitizer.sanitize([t.to_dict() for t in trunks])
