# src/domain/entities.py
"""
Domain entities representing core Asterisk concepts.
These are independent of any framework or external dependency.
"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ChannelState(Enum):
    """Possible states for an Asterisk channel."""
    DOWN = "Down"
    RESERVED = "Reserved"
    OFFHOOK = "OffHook"
    DIALING = "Dialing"
    RING = "Ring"
    RINGING = "Ringing"
    UP = "Up"
    BUSY = "Busy"
    DIALING_OFFHOOK = "Dialing Offhook"
    PRERING = "PreRing"


@dataclass(frozen=True)
class Channel:
    """
    Represents an Asterisk channel.
    Immutable value object following DDD principles.
    """
    id: str
    name: str
    state: ChannelState
    caller_id_num: str
    caller_id_name: str
    connected_line_num: str | None = None
    connected_line_name: str | None = None
    language: str = "en"
    accountcode: str = ""
    created_at: datetime | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state.value,
            "caller_id_num": self.caller_id_num,
            "caller_id_name": self.caller_id_name,
            "connected_line_num": self.connected_line_num,
            "connected_line_name": self.connected_line_name,
            "language": self.language,
            "accountcode": self.accountcode,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class OriginateResult:
    """Result of an originate (call creation) operation."""
    channel_id: str
    channel_name: str
    status: str  # "success" or specific error code

    def to_dict(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "status": self.status,
        }


@dataclass(frozen=True)
class HangupResult:
    """Result of a hangup operation."""
    channel_id: str
    status: str  # "success" or specific error code

    def to_dict(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "status": self.status,
        }
