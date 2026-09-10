"""Auth provider selection + static-token dev mode."""
import pytest

from src.security import auth


def test_default_mode_is_keycloak(monkeypatch):
    monkeypatch.delenv("MCP_AUTH_MODE", raising=False)
    assert auth._auth_mode() == "keycloak"
    assert type(auth.build_auth_provider()).__name__ == "JWTVerifier"


def test_static_mode_builds_static_verifier(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_MODE", "static")
    provider = auth.build_auth_provider()
    assert type(provider).__name__ == "StaticTokenVerifier"
    assert set(provider.tokens) == {"dev-operateur", "dev-superviseur", "dev-admin"}


async def test_static_token_yields_role_claims(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_MODE", "static")
    provider = auth.build_auth_provider()
    token = await provider.verify_token("dev-admin")
    assert token is not None
    assert token.claims["realm_access"]["roles"] == ["admin"]
    assert await provider.verify_token("inconnu") is None


def test_static_tokens_overridable(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_MODE", "static")
    monkeypatch.setenv("MCP_STATIC_TOKENS", '{"k9": ["superviseur"]}')
    provider = auth.build_auth_provider()
    assert set(provider.tokens) == {"k9"}
