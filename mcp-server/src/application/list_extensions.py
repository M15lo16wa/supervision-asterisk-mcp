# src/application/list_extensions.py
"""Use case: list dialplan extensions and their device state (lecture seule)."""
from src.domain.ports import AsteriskGateway, DataSanitizer


class ListExtensionsUseCase:
    """Autonomous zone: read-only, operateur minimum."""

    def __init__(self, gateway: AsteriskGateway, sanitizer: DataSanitizer):
        self._gateway = gateway
        self._sanitizer = sanitizer

    async def execute(self, context: str | None = None) -> list:
        extensions = await self._gateway.list_extensions(context=context)
        return self._sanitizer.sanitize([e.to_dict() for e in extensions])
