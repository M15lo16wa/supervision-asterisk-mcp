# src/main.py
"""Application entrypoint."""
import logging
from src.config import settings
from src.interfaces.mcp_tools import mcp

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """Start the MCP server."""
    logger.info(f"Starting MCP server on {settings.mcp_host}:{settings.mcp_port}")
    logger.info(f"Keycloak: {settings.keycloak_base_url}/realms/{settings.keycloak_realm}")
    logger.info(f"Asterisk AMI: {settings.asterisk_host}:{settings.asterisk_ami_port}")

    mcp.run(
        transport=settings.mcp_transport,
        host=settings.mcp_host,
        port=settings.mcp_port,
    )


if __name__ == "__main__":
    main()
