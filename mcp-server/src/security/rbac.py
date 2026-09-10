# src/security/rbac.py
"""Role-Based Access Control (RBAC)."""
from fastmcp.server.auth import AccessToken
from src.domain.exceptions import UnauthorizedAction


def get_roles(token: AccessToken) -> set[str]:
    """Extract roles from Keycloak JWT token.
    
    Reads from realm_access.roles claim in the JWT.
    """
    realm_access = token.claims.get("realm_access", {})
    roles = realm_access.get("roles", [])

    if not isinstance(roles, list):
        return set()

    return set(roles)


def has_role(token: AccessToken, role: str) -> bool:
    """Check if token bearer has a specific role."""
    return role in get_roles(token)


def require_role(token: AccessToken, role: str) -> None:
    """Enforce role requirement.
    
    Raises:
        UnauthorizedAction: If user lacks the required role.
    """
    if not has_role(token, role):
        roles = get_roles(token)
        raise UnauthorizedAction(
            f"Required role '{role}' not found. User has roles: {roles}"
        )
