# src/domain/entities.py
"""
Domain entities representing core Asterisk concepts.
These are independent of any framework or external dependency.
"""
from dataclasses import dataclass, field
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
    UNKNOWN = "Unknown"

    @classmethod
    def from_asterisk(cls, value: str | int | None) -> "ChannelState":
        """Best-effort mapping from AMI/ARI state (numeric code or description)."""
        if value is None:
            return cls.UNKNOWN
        text = str(value).strip()
        # numeric ChannelState code
        numeric = {
            "0": cls.DOWN, "1": cls.RESERVED, "2": cls.OFFHOOK, "3": cls.DIALING,
            "4": cls.RING, "5": cls.RINGING, "6": cls.UP, "7": cls.BUSY,
            "8": cls.DIALING_OFFHOOK, "9": cls.PRERING,
        }
        if text in numeric:
            return numeric[text]
        for state in cls:
            if state.value.lower() == text.lower():
                return state
        aliases = {"rsrvd": cls.RESERVED, "pre-ring": cls.PRERING}
        return aliases.get(text.lower(), cls.UNKNOWN)


class ExtensionState(Enum):
    """Device / hint state for an extension (AMI ExtensionStatus)."""
    UNKNOWN = "Unknown"
    NOT_INUSE = "Idle"
    INUSE = "InUse"
    BUSY = "Busy"
    UNAVAILABLE = "Unavailable"
    RINGING = "Ringing"
    ONHOLD = "OnHold"

    @classmethod
    def from_status_code(cls, code: str | int | None) -> "ExtensionState":
        mapping = {
            "-1": cls.UNKNOWN, "0": cls.NOT_INUSE, "1": cls.INUSE, "2": cls.BUSY,
            "4": cls.UNAVAILABLE, "8": cls.RINGING, "16": cls.ONHOLD,
        }
        return mapping.get(str(code).strip(), cls.UNKNOWN)


class SpyMode(Enum):
    """Listening modes for channel supervision."""
    LISTEN = "listen"      # écoute discrète
    WHISPER = "whisper"    # souffler à l'agent uniquement
    BARGE = "barge"        # conférence à trois


@dataclass(frozen=True)
class Channel:
    """Represents an Asterisk channel (immutable value object)."""
    id: str
    name: str
    state: ChannelState
    caller_id_num: str
    caller_id_name: str
    connected_line_num: str | None = None
    connected_line_name: str | None = None
    language: str = "en"
    accountcode: str = ""
    context: str | None = None
    exten: str | None = None
    application: str | None = None
    duration_seconds: int | None = None
    created_at: datetime | None = None

    def to_dict(self) -> dict:
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
            "context": self.context,
            "exten": self.exten,
            "application": self.application,
            "duration_seconds": self.duration_seconds,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class Extension:
    """A dialplan extension / hint and its current device state."""
    extension: str
    context: str
    state: ExtensionState
    status_text: str = ""
    hint: str = ""

    def to_dict(self) -> dict:
        return {
            "extension": self.extension,
            "context": self.context,
            "state": self.state.value,
            "status_text": self.status_text,
            "hint": self.hint,
        }


@dataclass(frozen=True)
class QueueStats:
    """Snapshot of an Asterisk call queue (AMI QueueSummary/QueueStatus)."""
    name: str
    calls_waiting: int
    members: int
    available_members: int
    callers_completed: int
    callers_abandoned: int
    service_level_perf: float          # % answered within the SL threshold
    average_hold_seconds: int
    average_talk_seconds: int

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "calls_waiting": self.calls_waiting,
            "members": self.members,
            "available_members": self.available_members,
            "callers_completed": self.callers_completed,
            "callers_abandoned": self.callers_abandoned,
            "service_level_perf": self.service_level_perf,
            "average_hold_seconds": self.average_hold_seconds,
            "average_talk_seconds": self.average_talk_seconds,
        }


@dataclass(frozen=True)
class TrunkUtilization:
    """Load of a SIP trunk: active channels vs. configured capacity."""
    name: str
    state: str                         # available / unavailable / unknown
    active_channels: int
    max_channels: int | None           # None = uncapped
    inbound_channels: int
    outbound_channels: int

    @property
    def utilization_percent(self) -> float:
        if not self.max_channels:
            return 0.0
        return round(100.0 * self.active_channels / self.max_channels, 1)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "active_channels": self.active_channels,
            "max_channels": self.max_channels,
            "inbound_channels": self.inbound_channels,
            "outbound_channels": self.outbound_channels,
            "utilization_percent": self.utilization_percent,
        }


@dataclass(frozen=True)
class CallDetailRecord:
    """A Call Detail Record (CDR) line."""
    uniqueid: str
    source: str
    destination: str
    destination_context: str
    caller_id: str
    channel: str
    destination_channel: str
    start_time: str
    answer_time: str
    end_time: str
    duration_seconds: int
    billable_seconds: int
    disposition: str          # ANSWERED / NO ANSWER / BUSY / FAILED
    last_application: str

    def to_dict(self) -> dict:
        return {
            "uniqueid": self.uniqueid,
            "source": self.source,
            "destination": self.destination,
            "destination_context": self.destination_context,
            "caller_id": self.caller_id,
            "channel": self.channel,
            "destination_channel": self.destination_channel,
            "start_time": self.start_time,
            "answer_time": self.answer_time,
            "end_time": self.end_time,
            "duration_seconds": self.duration_seconds,
            "billable_seconds": self.billable_seconds,
            "disposition": self.disposition,
            "last_application": self.last_application,
        }


@dataclass(frozen=True)
class CallQuality:
    """RTP/RTCP quality metrics for a channel, with an estimated MOS."""
    channel: str
    ssrc: str
    round_trip_ms: float
    jitter_ms: float
    packets_lost: int
    packets_received: int
    loss_percent: float
    mos_estimate: float
    rating: str               # excellent / good / fair / poor / bad

    @staticmethod
    def rate_mos(mos: float) -> str:
        if mos >= 4.3:
            return "excellent"
        if mos >= 4.0:
            return "good"
        if mos >= 3.6:
            return "fair"
        if mos >= 3.1:
            return "poor"
        return "bad"

    @classmethod
    def from_rtcp(
        cls,
        channel: str,
        ssrc: str,
        rtt_ms: float,
        jitter_ms: float,
        packets_lost: int,
        packets_received: int,
    ) -> "CallQuality":
        """Estimate MOS with the simplified E-model (ITU-T G.107).

        Effective latency folds in jitter buffer + a fixed 100 ms codec/net term;
        R is then degraded by effective latency and by packet loss.
        """
        total = max(packets_received + packets_lost, 1)
        loss_pct = 100.0 * packets_lost / total
        effective_latency = rtt_ms / 2.0 + 2.0 * jitter_ms + 100.0
        if effective_latency < 160.0:
            r_value = 93.2 - effective_latency / 40.0
        else:
            r_value = 93.2 - (effective_latency - 120.0) / 10.0
        r_value -= 2.5 * loss_pct           # ~2.5 R points per 1 % loss
        r_value = max(0.0, min(r_value, 100.0))
        if r_value < 0:
            mos = 1.0
        elif r_value > 100:
            mos = 4.5
        else:
            mos = 1.0 + 0.035 * r_value + r_value * (r_value - 60.0) * (100.0 - r_value) * 7e-6
        mos = round(max(1.0, min(mos, 4.5)), 2)
        return cls(
            channel=channel,
            ssrc=str(ssrc),
            round_trip_ms=round(rtt_ms, 2),
            jitter_ms=round(jitter_ms, 2),
            packets_lost=packets_lost,
            packets_received=packets_received,
            loss_percent=round(loss_pct, 2),
            mos_estimate=mos,
            rating=cls.rate_mos(mos),
        )

    def to_dict(self) -> dict:
        return {
            "channel": self.channel,
            "ssrc": self.ssrc,
            "round_trip_ms": self.round_trip_ms,
            "jitter_ms": self.jitter_ms,
            "packets_lost": self.packets_lost,
            "packets_received": self.packets_received,
            "loss_percent": self.loss_percent,
            "mos_estimate": self.mos_estimate,
            "rating": self.rating,
        }


@dataclass(frozen=True)
class OriginateResult:
    """Result of an originate (call creation) operation."""
    channel_id: str
    channel_name: str
    status: str

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
    status: str

    def to_dict(self) -> dict:
        return {"channel_id": self.channel_id, "status": self.status}


@dataclass(frozen=True)
class TransferResult:
    """Result of a blind or attended transfer."""
    channel_id: str
    destination: str
    destination_context: str
    attended: bool
    status: str

    def to_dict(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "destination": self.destination,
            "destination_context": self.destination_context,
            "attended": self.attended,
            "status": self.status,
        }


@dataclass(frozen=True)
class SpyResult:
    """Result of starting channel supervision (spy / whisper / barge)."""
    target_channel: str
    spy_channel_id: str
    supervisor_endpoint: str
    mode: SpyMode
    status: str

    def to_dict(self) -> dict:
        return {
            "target_channel": self.target_channel,
            "spy_channel_id": self.spy_channel_id,
            "supervisor_endpoint": self.supervisor_endpoint,
            "mode": self.mode.value,
            "status": self.status,
        }


@dataclass
class VoiceTurn:
    """One turn of the Speech-to-Speech pipeline, with per-stage timings (ms)."""
    channel_id: str
    transcript: str = ""
    response_text: str = ""
    stt_ms: float = 0.0
    llm_ms: float = 0.0
    tts_ms: float = 0.0
    total_ms: float = 0.0
    within_budget: bool = True
    timings: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "transcript": self.transcript,
            "response_text": self.response_text,
            "stt_ms": round(self.stt_ms, 1),
            "llm_ms": round(self.llm_ms, 1),
            "tts_ms": round(self.tts_ms, 1),
            "total_ms": round(self.total_ms, 1),
            "within_budget": self.within_budget,
            "timings": self.timings,
        }
