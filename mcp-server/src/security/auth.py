# src/security/auth.py
"""JWT authentication with Keycloak (OIDC).

The token authenticates the MCP *session* only — it is never forwarded to
Asterisk (no token passthrough). Asterisk uses its own dedicated AMI/ARI
credentials, configured separately.
"""
import json
import os

from fastmcp.server.auth import AccessToken

try:  # fastmcp 3.x
    from fastmcp.server.auth import JWTVerifier
except ImportError:  # fastmcp >= 4.0
    from fastmcp.server.auth.providers.jwt import JWTVerifier

try:
    from fastmcp.server.auth import StaticTokenVerifier
except ImportError:  # pragma: no cover
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

from src.config import settings
from src.domain.ports import SecurityManager
from src.security.exceptions import TokenVerificationError
from src.security.rbac import require_role as rbac_require_role

# Jetons de développement (MCP_AUTH_MODE=static) — PAS pour la production.
# Surcharge possible via MCP_STATIC_TOKENS='{"tok": ["role", ...]}'.
_DEFAULT_STATIC_TOKENS = {
    "dev-operateur": ["operateur"],
    "dev-superviseur": ["superviseur"],
    "dev-admin": ["admin"],
}


def _auth_mode() -> str:
    return os.getenv("MCP_AUTH_MODE", "keycloak").strip().lower()


def _static_token_map() -> dict[str, list[str]]:
    raw = os.getenv("MCP_STATIC_TOKENS")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return _DEFAULT_STATIC_TOKENS


def build_auth_provider():
    """Auth provider passé à ``FastMCP(auth=...)``.

    - ``keycloak`` (défaut) : JWTVerifier OIDC (RS256, JWKS Keycloak).
    - ``static``            : StaticTokenVerifier — jetons opaques -> rôles,
      pour tester en local sans Keycloak.
    """
    if _auth_mode() == "static":
        # StaticTokenVerifier copie tout le dict dans AccessToken.claims :
        # on met donc realm_access / preferred_username à la racine.
        tokens = {
            tok: {
                "client_id": f"dev:{tok}",
                "scopes": [],
                "preferred_username": tok,
                "realm_access": {"roles": roles},
            }
            for tok, roles in _static_token_map().items()
        }
        return StaticTokenVerifier(tokens=tokens)
    return JWTVerifier(
        jwks_uri=settings.keycloak_jwks_url,
        issuer=settings.keycloak_issuer,
        algorithm="RS256",
    )


# Rétrocompat : ancien nom.
def build_jwt_verifier():
    return build_auth_provider()


class KeycloakSecurityManager(SecurityManager):
    """Manages authentication via Keycloak JWT tokens (ou jetons statiques en dev)."""

    def __init__(self):
        """Initialize security manager with Keycloak config from environment."""
        self.verifier = build_auth_provider()
        self.jwks_uri = getattr(self.verifier, "jwks_uri", None)
        self.issuer = getattr(self.verifier, "issuer", None)

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
