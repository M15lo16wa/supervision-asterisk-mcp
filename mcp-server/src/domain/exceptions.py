# src/domain/exceptions.py
"""Domain-level exceptions."""


class DomainException(Exception):
    """Base exception for domain layer."""
    pass


class HitlConfirmationDenied(DomainException):
    """Raised when human-in-the-loop confirmation is refused or absent."""
    pass


class UnauthorizedAction(DomainException):
    """Raised when user lacks required role."""
    pass


class AsteriskConnectionError(DomainException):
    """Raised when connection to Asterisk fails."""
    pass


class ChannelNotFound(DomainException):
    """Raised when requested channel does not exist."""
    pass
