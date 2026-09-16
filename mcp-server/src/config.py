# src/config.py
"""Application configuration, read from environment variables.

A local ``.env`` file (next to the ``mcp-server`` directory or at the repo root)
is loaded automatically if present — no dependency on python-dotenv.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    """Minimal .env loader: ``KEY=VALUE`` lines, ``#`` comments, no interpolation."""
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent / ".env",        # mcp-server/.env
        here.parent.parent.parent / ".env",  # repo-root/.env
    ]
    for path in candidates:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class LlmSettings:
    """Configuration de l'outil superviseur `llm_chat` (Module 2).

    Réutilise Ollama comme modèle local — mêmes variables que le pipeline vocal
    S2S (OLLAMA_BASE_URL, OLLAMA_MODEL), mais un prompt système et des limites
    dédiés à la supervision.
    """

    base_url: str = field(default_factory=lambda: _env("OLLAMA_BASE_URL", "http://localhost:11434"))
    model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", "qwen2.5:3b-instruct"))
    system_prompt: str = field(default_factory=lambda: _env(
        "LLM_SYSTEM_PROMPT",
        "Tu es l'assistant superviseur d'un PBX Asterisk. Réponds en français, "
        "de façon factuelle et concise, en t'appuyant uniquement sur les données fournies.",
    ))
    temperature: float = field(default_factory=lambda: _env_float("LLM_TEMPERATURE", 0.3))
    num_predict: int = field(default_factory=lambda: _env_int("LLM_NUM_PREDICT", 512))
    timeout_s: int = field(default_factory=lambda: _env_int("LLM_TIMEOUT_S", 60))
    max_history: int = field(default_factory=lambda: _env_int("LLM_MAX_HISTORY", 8))


@dataclass(frozen=True)
class Settings:
    """Immutable settings snapshot, built from the environment at import time."""

    # Keycloak
    keycloak_base_url: str = field(default_factory=lambda: _env("KEYCLOAK_BASE_URL", "http://localhost:8080"))
    keycloak_realm: str = field(default_factory=lambda: _env("KEYCLOAK_REALM", "asterisk"))
    keycloak_client_id: str = field(default_factory=lambda: _env("KEYCLOAK_CLIENT_ID", "mcp-server"))

    # Asterisk AMI
    asterisk_host: str = field(default_factory=lambda: _env("ASTERISK_HOST", "localhost"))
    asterisk_ami_port: int = field(default_factory=lambda: _env_int("ASTERISK_AMI_PORT", 5038))
    asterisk_ami_user: str = field(default_factory=lambda: _env("ASTERISK_AMI_USER", "mcp_ami"))
    asterisk_ami_secret: str = field(default_factory=lambda: _env("ASTERISK_AMI_SECRET", "mcp_ami"))

    # Asterisk ARI (REST, utilisé pour la qualité RTCP et l'External Media)
    asterisk_ari_base_url: str = field(default_factory=lambda: _env("ASTERISK_ARI_BASE_URL", "http://localhost:8088"))
    asterisk_ari_user: str = field(default_factory=lambda: _env("ASTERISK_ARI_USER", "mcp_ari"))
    asterisk_ari_password: str = field(default_factory=lambda: _env("ASTERISK_ARI_PASSWORD", "mcp_ari"))
    asterisk_ari_app: str = field(default_factory=lambda: _env("ASTERISK_ARI_APP", "mcp-supervision"))
    asterisk_default_context: str = field(default_factory=lambda: _env("ASTERISK_DEFAULT_CONTEXT", "from-internal"))

    # MCP Server
    mcp_transport: str = field(default_factory=lambda: _env("MCP_TRANSPORT", "streamable-http"))
    mcp_host: str = field(default_factory=lambda: _env("MCP_HOST", "0.0.0.0"))
    mcp_port: int = field(default_factory=lambda: _env_int("MCP_PORT", 8000))
    # URL publique du serveur MCP — exposée dans la métadonnée OAuth de ressource
    # protégée (RFC 9728) pour que le client MCP découvre Keycloak (OAuth 2.1 + PKCE).
    mcp_public_url: str = field(default_factory=lambda: _env("MCP_PUBLIC_URL", ""))
    # Portée du consentement humain : "pilotage" (défaut) ou "all" (tous les outils).
    hitl_mode: str = field(default_factory=lambda: _env("MCP_HITL_MODE", "pilotage"))

    # Audit
    audit_log_path: str = field(default_factory=lambda: _env("AUDIT_LOG_PATH", "logs/audit.jsonl"))

    # Logging
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))

    # LLM superviseur (prompts)
    llm: LlmSettings = LlmSettings()

    @property
    def keycloak_issuer(self) -> str:
        return f"{self.keycloak_base_url}/realms/{self.keycloak_realm}"

    @property
    def keycloak_jwks_url(self) -> str:
        return f"{self.keycloak_issuer}/protocol/openid-connect/certs"


settings = Settings()
