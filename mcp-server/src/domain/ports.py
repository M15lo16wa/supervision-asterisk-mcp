# src/domain/ports.py
"""Abstract interfaces (ports) for domain layer.
These define contracts without implementation details.
"""
from abc import ABC, abstractmethod
from src.domain.entities import Channel, OriginateResult, HangupResult
from fastmcp.server.auth import AccessToken


class AriGateway(ABC):
    """Port: Interface to Asterisk ARI."""

    @abstractmethod
    async def list_channels(self) -> list[Channel]:
        """List all active channels in Asterisk."""
        ...

    @abstractmethod
    async def originate(self, endpoint: str, context: str, exten: str) -> OriginateResult:
        """Originate a new call to the given endpoint."""
        ...

    @abstractmethod
    async def hangup(self, channel_id: str) -> HangupResult:
        """Hangup a channel by ID."""
        ...


class HitlConfirmation(ABC):
    """Port: Human-in-the-Loop confirmation mechanism."""

    @abstractmethod
    async def confirm(
        self, action: str, user: str, details: dict | None = None
    ) -> bool:
        """Request and get human confirmation for an action.
        
        Args:
            action: Action identifier (e.g., 'originate_call')
            user: Username requesting the action
            details: Additional context about the action
            
        Returns:
            True if confirmed, False if denied or no response.
            
        Raises:
            HitlConfirmationDenied: If denied or timeout.
        """
        ...


class SecurityManager(ABC):
    """Port: Authentication and RBAC enforcement."""

    @abstractmethod
    def verify_token(self, token: str) -> AccessToken:
        """Verify and decode JWT token.
        
        Raises:
            UnauthorizedAction: If token is invalid.
        """
        ...

    @abstractmethod
    def require_role(self, token: AccessToken, required_role: str) -> None:
        """Check if token has required role.
        
        Raises:
            UnauthorizedAction: If role is missing.
        """
        ...


class DataSanitizer(ABC):
    """Port: Protection against injection attacks."""

    @abstractmethod
    def sanitize(self, value: any) -> any:
        """Sanitize external data (Asterisk, CDR, transcriptions).
        
        Wraps in safe envelope and neutralizes suspicious patterns.
        Works recursively on str, dict, list.
        """
        ...
