# src/config.py
"""Application configuration from environment variables."""
import os
from dataclasses import dataclass


@dataclass
class Settings:
    """Application settings."""

    # Keycloak
    keycloak_base_url: str = os.getenv(
        "KEYCLOAK_BASE_URL", "http://localhost:8080"
    )
    keycloak_realm: str = os.getenv("KEYCLOAK_REALM", "asterik")
    keycloak_client_id: str = os.getenv("KEYCLOAK_CLIENT_ID", "mcp-server")

    # Asterisk AMI
    asterisk_host: str = os.getenv("ASTERISK_HOST", "localhost")
    asterisk_ami_port: int = int(os.getenv("ASTERISK_AMI_PORT", 5038))
    asterisk_ami_user: str = os.getenv("ASTERISK_AMI_USER", "admin")
    asterisk_ami_secret: str = os.getenv("ASTERISK_AMI_SECRET", "admin")

    # MCP Server
    mcp_transport: str = os.getenv("MCP_TRANSPORT", "streamable-http")
    mcp_host: str = os.getenv("MCP_HOST", "0.0.0.0")
    mcp_port: int = int(os.getenv("MCP_PORT", 8000))

    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
