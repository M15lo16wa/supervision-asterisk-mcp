# src/security/exceptions.py
"""Security-specific exceptions."""


class SecurityException(Exception):
    """Base security exception."""
    pass


class TokenVerificationError(SecurityException):
    """Raised when JWT token verification fails."""
    pass


class PermissionDenied(SecurityException):
    """Raised when user lacks required permissions."""
    pass
