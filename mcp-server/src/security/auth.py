# src/security/auth.py
"""JWT authentication with Keycloak."""
import os
from fastmcp.server.auth import JWTVerifier, AccessToken
from src.security.exceptions import TokenVerificationError
from src.domain.ports import SecurityManager
from src.security.rbac import require_role as rbac_require_role
from src.domain.exceptions import UnauthorizedAction


class KeycloakSecurityManager(SecurityManager):
    """Manages authentication via Keycloak JWT tokens."""

    def __init__(self):
        """Initialize security manager with Keycloak config from environment."""
        keycloak_base_url = os.getenv(
            "KEYCLOAK_BASE_URL", "http://localhost:8080"
        )
        keycloak_realm = os.getenv("KEYCLOAK_REALM", "asterik")

        self.jwks_uri = f"{keycloak_base_url}/realms/{keycloak_realm}/protocol/openid-connect/certs"
        self.issuer = f"{keycloak_base_url}/realms/{keycloak_realm}"

        self.verifier = JWTVerifier(
            jwks_uri=self.jwks_uri,
            issuer=self.issuer,
            algorithm="RS256",
        )

    def verify_token(self, token: str) -> AccessToken:
        """Verify and decode JWT token.
        
        Raises:
            TokenVerificationError: If token is invalid or expired.
        """
        try:
            # FastMCP's JWTVerifier expects token without 'Bearer ' prefix
            if token.startswith("Bearer "):
                token = token[7:]
            return self.verifier.verify(token)
        except Exception as e:
            raise TokenVerificationError(f"Token verification failed: {str(e)}")

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
