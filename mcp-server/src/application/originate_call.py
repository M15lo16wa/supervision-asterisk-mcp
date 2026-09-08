# src/application/originate_call.py
from src.domain.ports import AriGateway, HitlConfirmation

class OriginateCallUseCase:
    """Zone supervisée — la confirmation HITL est une dépendance injectée,
    jamais une simple vérification optionnelle en fin de fonction."""

    def __init__(self, ari_gateway: AriGateway, hitl: HitlConfirmation) -> None:
        self._ari = ari_gateway
        self._hitl = hitl

    async def execute(self, endpoint: str, requested_by: str) -> dict:
        if not await self._hitl.confirm(action="originate_call", user=requested_by):
            raise PermissionError("Confirmation humaine requise et refusée ou absente")
        result = await self._ari.originate(endpoint)
        return result.to_dict()
