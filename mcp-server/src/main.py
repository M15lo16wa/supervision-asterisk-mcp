# src/main.py
"""Application entrypoint for the MCP supervision server (Modules 1 & 2)."""
import logging
import os

from src.audit import get_audit_logger
from src.config import settings
from src.interfaces.mcp_tools import mcp

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    get_audit_logger()  # crée le fichier de journal d'audit au démarrage
    logger.info("MCP server -> %s:%s (%s)", settings.mcp_host, settings.mcp_port, settings.mcp_transport)
    logger.info("Auth       -> mode=%s issuer=%s", os.getenv("MCP_AUTH_MODE", "keycloak"),
                settings.keycloak_issuer)
    logger.info("HITL       -> mode=%s | audit -> %s", settings.hitl_mode, settings.audit_log_path)
    logger.info("Asterisk   -> AMI %s:%s / ARI %s", settings.asterisk_host,
                settings.asterisk_ami_port, settings.asterisk_ari_base_url)
    mcp.run(
        transport=settings.mcp_transport,
        host=settings.mcp_host,
        port=settings.mcp_port,
    )


if __name__ == "__main__":
    main()
