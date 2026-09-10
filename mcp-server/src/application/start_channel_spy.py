# src/application/start_channel_spy.py
"""Use case: supervise a live call — listen / whisper / barge.

RBAC :
  * listen  → superviseur (écoute discrète, non intrusive)
  * whisper / barge → admin (intrusif : on injecte de l'audio dans l'appel)

HITL : obligatoire pour whisper et barge ; l'écoute discrète est seulement
auditée (elle reste sensible mais n'altère pas l'appel).
"""
from src.domain.entities import SpyMode
from src.domain.ports import AsteriskGateway, DataSanitizer, HitlConfirmation

_INTRUSIVE = {SpyMode.WHISPER, SpyMode.BARGE}


class StartChannelSpyUseCase:
    def __init__(
        self,
        gateway: AsteriskGateway,
        hitl: HitlConfirmation,
        sanitizer: DataSanitizer,
    ):
        self._gateway = gateway
        self._hitl = hitl
        self._sanitizer = sanitizer

    async def execute(
        self,
        target_channel: str,
        supervisor_endpoint: str,
        mode: SpyMode,
        user: str,
    ) -> dict:
        if mode in _INTRUSIVE:
            await self._hitl.confirm(
                action=f"channel_spy:{mode.value}",
                user=user,
                details={"target_channel": target_channel, "supervisor": supervisor_endpoint},
            )
        result = await self._gateway.start_spy(target_channel, supervisor_endpoint, mode)
        return self._sanitizer.sanitize(result.to_dict())
