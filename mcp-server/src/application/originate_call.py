# src/application/originate_call.py
"""Use case: originate a call (zone supervisée — HITL obligatoire)."""
from src.domain.ports import AsteriskGateway, DataSanitizer, HitlConfirmation


class OriginateCallUseCase:
    """Supervised zone: HITL confirmation is a mandatory injected dependency,
    never an optional check at the end of the function."""

    def __init__(
        self,
        gateway: AsteriskGateway,
        hitl: HitlConfirmation,
        sanitizer: DataSanitizer,
    ):
        self._gateway = gateway
        self._hitl = hitl
        self._sanitizer = sanitizer

    async def execute(self, endpoint: str, context: str, exten: str, user: str) -> dict:
        # 1. Confirmation humaine — lève HitlConfirmationDenied si refus/absence.
        await self._hitl.confirm(
            action="originate_call",
            user=user,
            details={"endpoint": endpoint, "context": context, "exten": exten},
        )
        # 2. Exécution
        result = await self._gateway.originate(endpoint, context, exten)
        # 3. Assainissement de la sortie
        return self._sanitizer.sanitize(result.to_dict())
