# src/application/get_queue_stats.py
"""Use case: call-queue statistics (zone autonome — lecture seule)."""
from src.domain.ports import AsteriskGateway, DataSanitizer


class GetQueueStatsUseCase:
    """Autonomous zone: read-only, operateur minimum."""

    def __init__(self, gateway: AsteriskGateway, sanitizer: DataSanitizer):
        self._gateway = gateway
        self._sanitizer = sanitizer

    async def execute(self, queue: str | None = None) -> list:
        queues = await self._gateway.get_queues()
        if queue:
            queues = [q for q in queues if q.name == queue]
        return self._sanitizer.sanitize([q.to_dict() for q in queues])
