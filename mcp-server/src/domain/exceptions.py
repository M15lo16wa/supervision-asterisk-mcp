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


class AsteriskCommandError(DomainException):
    """Raised when Asterisk accepts the connection but rejects an action."""
    pass


class LlmUnavailableError(DomainException):
    """Raised when the local language model (Ollama) is unreachable or fails."""
    pass


class VoicePipelineError(DomainException):
    """Raised when the Speech-to-Speech pipeline cannot process a turn."""
    pass
