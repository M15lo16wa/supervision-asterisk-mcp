"""Parsing / mapping tests for the AMI gateway (no live Asterisk)."""
from src.adapters.asterisk_gateway import PanoramiskGateway
from src.domain.entities import ChannelState, ExtensionState


def _gw():
    return PanoramiskGateway(host="x", port=5038, username="u", secret="p")


def test_channel_state_mapping():
    assert ChannelState.from_asterisk("6") is ChannelState.UP
    assert ChannelState.from_asterisk("Up") is ChannelState.UP
    assert ChannelState.from_asterisk("Rsrvd") is ChannelState.RESERVED
    assert ChannelState.from_asterisk("nonsense") is ChannelState.UNKNOWN
    assert ChannelState.from_asterisk(None) is ChannelState.UNKNOWN


def test_extension_state_mapping():
    assert ExtensionState.from_status_code("0") is ExtensionState.NOT_INUSE
    assert ExtensionState.from_status_code("1") is ExtensionState.INUSE
    assert ExtensionState.from_status_code("8") is ExtensionState.RINGING
    assert ExtensionState.from_status_code("999") is ExtensionState.UNKNOWN


def test_to_channel_parses_ami_event():
    event = {
        "Event": "CoreShowChannel", "Channel": "PJSIP/1001-00000001",
        "Uniqueid": "1700000000.1", "ChannelStateDesc": "Up",
        "CallerIDNum": "1001", "CallerIDName": "Agent 1001",
        "ConnectedLineNum": "1002", "Context": "internal", "Exten": "1002",
        "Application": "Dial", "Duration": "00:01:23",
    }
    ch = PanoramiskGateway._to_channel(event)
    assert ch.state is ChannelState.UP
    assert ch.caller_id_num == "1001"
    assert ch.duration_seconds == 83
    assert ch.id == "1700000000.1"


def test_cdr_event_buffered():
    gw = _gw()
    gw._on_cdr_event(None, {
        "Event": "Cdr", "UniqueID": "1700000000.2", "Source": "1001",
        "Destination": "1002", "DestinationContext": "internal",
        "CallerID": "Agent <1001>", "Channel": "PJSIP/1001-2",
        "StartTime": "2026-09-10 10:00:00", "AnswerTime": "2026-09-10 10:00:03",
        "EndTime": "2026-09-10 10:01:00", "Duration": "60", "BillableSeconds": "57",
        "Disposition": "ANSWERED", "LastApplication": "Dial",
    })
    assert len(gw._cdr) == 1
    assert gw._cdr[0].billable_seconds == 57


def test_rtpqos_fallback_parsing():
    raw = "ssrc=111;themssrc=222;lp=3;rxjitter=0.004;rxcount=980;txcount=1000;rlp=1;rtt=0.045"
    q = PanoramiskGateway._quality_from_rtpqos("PJSIP/1001-1", raw)
    assert q.packets_received == 980
    assert q.jitter_ms == 4.0
    assert q.round_trip_ms == 45.0
    assert 1.0 <= q.mos_estimate <= 4.5
