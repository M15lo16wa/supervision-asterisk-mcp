# src/security/auth.py
"""JWT authentication with Keycloak (OIDC).

The token authenticates the MCP *session* only — it is never forwarded to
Asterisk (no token passthrough). Asterisk uses its own dedicated AMI/ARI
credentials, configured separately.
"""
from fastmcp.server.auth import AccessToken

try:  # fastmcp 3.x
    from fastmcp.server.auth import JWTVerifier
except ImportError:  # fastmcp >= 4.0
    from fastmcp.server.auth.providers.jwt import JWTVerifier

from src.config import settings
from src.domain.ports import SecurityManager
from src.security.exceptions import TokenVerificationError
from src.security.rbac import require_role as rbac_require_role


def build_jwt_verifier() -> JWTVerifier:
    """Build the FastMCP JWT verifier from Keycloak config.

    The returned verifier is passed to ``FastMCP(auth=...)`` so that the
    transport layer authenticates every request before a tool runs.
    """
    return JWTVerifier(
        jwks_uri=settings.keycloak_jwks_url,
        issuer=settings.keycloak_issuer,
        algorithm="RS256",
    )


class KeycloakSecurityManager(SecurityManager):
    """Manages authentication via Keycloak JWT tokens."""

    def __init__(self):
        """Initialize security manager with Keycloak config from environment."""
        self.verifier = build_jwt_verifier()
        self.jwks_uri = self.verifier.jwks_uri
        self.issuer = self.verifier.issuer

    async def verify_token(self, token: str) -> AccessToken:
        """Verify and decode a JWT token.

        Raises:
            TokenVerificationError: If token is invalid or expired.
        """
        # FastMCP's JWTVerifier expects the token without the 'Bearer ' prefix
        token = token.removeprefix("Bearer ")
        try:
            access_token = await self.verifier.verify_token(token)
        except Exception as e:
            raise TokenVerificationError(f"Token verification failed: {e!s}") from e
        if access_token is None:
            raise TokenVerificationError("Token verification failed: invalid or expired token")
        return access_token

    def require_role(self, token: AccessToken, required_role: str) -> None:
        """Enforce role requirement.
        
        Raises:
            UnauthorizedAction: If user lacks the required role.
        """
        rbac_require_role(token, required_role)


# Global singleton instance
_security_manager: SecurityManager | None = None


def get_security_manager() -> SecurityManager:
    """Get or create security manager singleton."""
    global _security_manager
    if _security_manager is None:
        _security_manager = KeycloakSecurityManager()
    return _security_manager


def set_security_manager(manager: SecurityManager) -> None:
    """Override security manager (useful for testing)."""
    global _security_manager
    _security_manager = manager
