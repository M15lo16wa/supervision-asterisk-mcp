# src/application/analyze_call_quality.py
"""Use case: analyse RTP/RTCP quality of a channel (zone analyse — superviseur+)."""
from src.domain.ports import AsteriskGateway, DataSanitizer


class AnalyzeCallQualityUseCase:
    """Analysis zone: read + compute, superviseur minimum, no HITL (non-intrusive)."""

    def __init__(self, gateway: AsteriskGateway, sanitizer: DataSanitizer):
        self._gateway = gateway
        self._sanitizer = sanitizer

    async def execute(self, channel_id: str) -> dict:
        quality = await self._gateway.get_channel_quality(channel_id)
        return self._sanitizer.sanitize(quality.to_dict())
