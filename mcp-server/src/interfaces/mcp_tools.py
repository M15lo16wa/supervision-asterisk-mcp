# src/interfaces/mcp_tools.py
"""MCP tool definitions exposing use cases to clients."""
import os
import logging
from fastmcp import FastMCP, Context
from fastmcp.server.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken

from src.security.auth import get_security_manager, build_jwt_verifier
from src.security.sanitizer import DataSanitizerImpl
from src.adapters.ari_gateway import PanoramiskAriGateway
from src.adapters.hitl_confirmation import FastMcpHitlConfirmation
from src.application.list_active_channels import ListActiveChannelsUseCase
from src.application.originate_call import OriginateCallUseCase
from src.domain.exceptions import UnauthorizedAction, HitlConfirmationDenied

logger = logging.getLogger(__name__)

# Initialize MCP server with Keycloak JWT authentication enforced at the
# transport layer. Every request is authenticated before a tool runs, and
# the verified token is injected into tools via CurrentAccessToken().
mcp = FastMCP(name="asterisk-mcp-supervision", auth=build_jwt_verifier())

# Initialize dependencies (singletons)
_security_manager = get_security_manager()
_sanitizer = DataSanitizerImpl()
_ari_gateway = PanoramiskAriGateway(
    host=os.getenv("ASTERISK_HOST", "localhost"),
    port=int(os.getenv("ASTERISK_AMI_PORT", 5038)),
    username=os.getenv("ASTERISK_AMI_USER", "admin"),
    secret=os.getenv("ASTERISK_AMI_SECRET", "admin"),
)


@mcp.tool()
async def list_active_channels(token: AccessToken = CurrentAccessToken()) -> dict:
    """List all active Asterisk channels.
    
    Autonomous zone: Only requires operateur or higher role.
    No HITL confirmation needed.
    
    Args:
        token: JWT token from Keycloak (injected by FastMCP)
        
    Returns:
        List of active channels with details
        
    Raises:
        UnauthorizedAction: If user lacks operateur role
    """
    try:
        # Step 1: Verify role
        _security_manager.require_role(token, "operateur")

        # Step 2: Execute use case
        use_case = ListActiveChannelsUseCase(_ari_gateway, _sanitizer)
        channels = await use_case.execute()

        return {"status": "success", "channels": channels}

    except UnauthorizedAction as e:
        logger.warning(f"Unauthorized access to list_active_channels: {e}")
        return {"status": "error", "error": str(e)}
    except Exception as e:
        logger.error(f"Error listing channels: {e}")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def originate_call(
    endpoint: str,
    context: str,
    exten: str,
    ctx: Context,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Originate a new call to a destination.
    
    Supervised zone: Requires admin role + mandatory HITL confirmation.
    
    Args:
        endpoint: Destination endpoint (e.g., 'SIP/2000')
        context: Dial context (e.g., 'from-internal')
        exten: Extension to dial
        ctx: MCP Context for HITL elicit (injected by FastMCP)
        token: JWT token from Keycloak (injected by FastMCP)
        
    Returns:
        Result of origination attempt
        
    Raises:
        UnauthorizedAction: If user lacks admin role
        HitlConfirmationDenied: If human refuses confirmation
    """
    try:
        # Step 1: Verify role (admin only)
        _security_manager.require_role(token, "admin")

        # Step 2: Create HITL handler
        hitl = FastMcpHitlConfirmation(ctx)

        # Step 3: Execute use case (includes HITL confirmation)
        use_case = OriginateCallUseCase(_ari_gateway, hitl, _sanitizer)
        result = await use_case.execute(
            endpoint=endpoint,
            context=context,
            exten=exten,
            user=token.claims.get("preferred_username", "unknown"),
        )

        return {"status": "success", "result": result}

    except UnauthorizedAction as e:
        logger.warning(f"Unauthorized access to originate_call: {e}")
        return {"status": "error", "error": str(e)}
    except HitlConfirmationDenied as e:
        logger.warning(f"HITL confirmation denied: {e}")
        return {"status": "cancelled", "reason": str(e)}
    except Exception as e:
        logger.error(f"Error originating call: {e}")
        return {"status": "error", "error": str(e)}
