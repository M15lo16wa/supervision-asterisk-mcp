# src/application/originate_call.py
"""Use case: Originate a call (supervised zone, HITL mandatory)."""
from src.domain.ports import AriGateway, HitlConfirmation, DataSanitizer
from src.domain.exceptions import HitlConfirmationDenied


class OriginateCallUseCase:
    """Supervised zone: Confirmation is a mandatory dependency.
    
    HITL confirmation is NEVER optional - it's injected as a port
    that MUST be satisfied before executing any action.
    """

    def __init__(
        self,
        ari_gateway: AriGateway,
        hitl: HitlConfirmation,
        sanitizer: DataSanitizer,
    ):
        self._ari = ari_gateway
        self._hitl = hitl
        self._sanitizer = sanitizer

    async def execute(self, endpoint: str, context: str, exten: str, user: str) -> dict:
        """Originate a call after human confirmation.
        
        Args:
            endpoint: Destination endpoint (e.g., 'SIP/2000')
            context: Dial context
            exten: Extension to dial
            user: Username requesting the action
            
        Returns:
            Sanitized result dictionary
            
        Raises:
            HitlConfirmationDenied: If human refuses confirmation.
        """
        # Step 1: Request human confirmation (mandatory)
        confirmed = await self._hitl.confirm(
            action="originate_call",
            user=user,
            details={"endpoint": endpoint, "context": context, "exten": exten},
        )

        if not confirmed:
            raise HitlConfirmationDenied(
                f"Human confirmation required and refused for originate_call by {user}"
            )

        # Step 2: Execute the action
        result = await self._ari.originate(endpoint, context, exten)

        # Step 3: Sanitize and return
        return self._sanitizer.sanitize(result.to_dict())
