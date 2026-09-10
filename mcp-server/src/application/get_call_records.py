# src/application/get_call_records.py
"""Use case: read recent Call Detail Records (lecture seule)."""
from src.domain.ports import AsteriskGateway, DataSanitizer


class GetCallRecordsUseCase:
    """Autonomous zone: read-only, operateur minimum."""

    MAX_LIMIT = 200

    def __init__(self, gateway: AsteriskGateway, sanitizer: DataSanitizer):
        self._gateway = gateway
        self._sanitizer = sanitizer

    async def execute(self, limit: int = 20) -> list:
        limit = max(1, min(int(limit), self.MAX_LIMIT))
        records = await self._gateway.get_recent_cdr(limit=limit)
        return self._sanitizer.sanitize([r.to_dict() for r in records])
