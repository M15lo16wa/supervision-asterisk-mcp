# src/adapters/hitl_confirmation.py
"""Human-in-the-Loop confirmation implementation."""
import logging

from fastmcp.server.elicitation import AcceptedElicitation

from src.domain.exceptions import HitlConfirmationDenied
from src.domain.ports import HitlConfirmation

logger = logging.getLogger(__name__)


class FastMcpHitlConfirmation(HitlConfirmation):
    """HITL implementation using FastMCP's elicit primitive.
    
    Requires the client to implement confirmation via MCP protocol.
    """

    def __init__(self, mcp_context):
        """Initialize with MCP context for eliciting responses.
        
        Args:
            mcp_context: FastMCP Context object for calling elicit()
        """
        self._context = mcp_context

    async def confirm(
        self, action: str, user: str, details: dict | None = None
    ) -> bool:
        """Request human confirmation via MCP elicit primitive.
        
        Args:
            action: Action identifier (e.g., 'originate_call')
            user: Username requesting the action
            details: Additional context about the action
            
        Returns:
            True if human confirms, False otherwise
            
        Raises:
            HitlConfirmationDenied: If denied or no response.
        """
        message = f"Confirm action '{action}' requested by user '{user}'"
        if details:
            message += f" with details: {details}"

        try:
            result = await self._context.elicit(
                message=message,
                response_type=bool,
            )

            confirmed = isinstance(result, AcceptedElicitation) and bool(result.data)

            if not confirmed:
                logger.warning(
                    f"HITL confirmation denied for action '{action}' by user '{user}'"
                )
                raise HitlConfirmationDenied(
                    f"Action '{action}' cancelled: confirmation refused "
                    f"(response: {type(result).__name__})"
                )

            logger.info(f"HITL confirmation accepted for action '{action}' by user '{user}'")
            return True

        except HitlConfirmationDenied:
            raise
        except Exception as e:
            logger.error(f"HITL confirmation error for action '{action}': {e}")
            raise HitlConfirmationDenied(f"HITL confirmation failed: {e!s}") from e
