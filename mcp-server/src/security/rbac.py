# src/security/rbac.py
"""Role-Based Access Control (RBAC).

Matrice de droits (cahier des charges) — hiérarchie ascendante :

    operateur   : lecture seule (états canaux/extensions, CDR)
    superviseur : operateur + analyse (qualité MOS/RTCP) + écoute discrète
    admin       : superviseur + pilotage (origination, transfert, hangup, barge)

Un rôle supérieur hérite des permissions des rôles inférieurs.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.domain.exceptions import UnauthorizedAction

if TYPE_CHECKING:
    from fastmcp.server.auth import AccessToken

# Du moins privilégié au plus privilégié.
ROLE_HIERARCHY: tuple[str, ...] = ("operateur", "superviseur", "admin")


def get_roles(token: AccessToken) -> set[str]:
    """Extract realm roles from the Keycloak JWT (``realm_access.roles`` claim)."""
    realm_access = token.claims.get("realm_access", {}) if token and token.claims else {}
    roles = realm_access.get("roles", []) if isinstance(realm_access, dict) else []
    return set(roles) if isinstance(roles, list) else set()


def _effective_roles(token: AccessToken) -> set[str]:
    """Expand held roles with everything they outrank in the hierarchy."""
    held = get_roles(token)
    effective: set[str] = set(held)
    for i, role in enumerate(ROLE_HIERARCHY):
        if role in held:
            effective.update(ROLE_HIERARCHY[: i + 1])
    return effective


def has_role(token: AccessToken, role: str) -> bool:
    """True if the bearer holds ``role`` or a role that outranks it."""
    if role in ROLE_HIERARCHY:
        return role in _effective_roles(token)
    return role in get_roles(token)


def require_role(token: AccessToken, role: str) -> None:
    """Raise ``UnauthorizedAction`` unless the bearer satisfies ``role``."""
    if not has_role(token, role):
        raise UnauthorizedAction(
            f"Rôle '{role}' requis. Rôles présents : {sorted(get_roles(token)) or '—'}."
        )
