"""Journal d'audit (A.6) : chaque action, refus RBAC et HITL est tracé."""
import json

import pytest
from fastmcp.server.elicitation import AcceptedElicitation

import src.interfaces.mcp_tools as tools
from src.audit import audit_log, get_audit_logger
from src.security.rbac import require_role
from tests.fakes import FakeAsteriskGateway, FakeElicitContext, FakeToken, sample_channel


class _Ctx:
    request_id = "req-a"


class _Security:
    def require_role(self, token, role):
        require_role(token, role)


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    monkeypatch.setattr(tools, "_gateway", FakeAsteriskGateway(channels=[sample_channel("PJSIP/1001-1")]))
    monkeypatch.setattr(tools, "_security", _Security())
    monkeypatch.setenv("MCP_HITL_MODE", "pilotage")


def _entries():
    return get_audit_logger().tail(100)


def test_redacts_secret_keys_and_truncates():
    audit_log("tool_call", actor="x", tool="t", outcome="success",
              params={"password": "hunter2", "note": "a" * 900})
    e = _entries()[-1]
    assert e["params"]["password"] == "***"
    assert e["params"]["note"].endswith("…") and len(e["params"]["note"]) <= 502


async def test_tool_call_success_is_audited():
    await tools.list_active_channels(_Ctx(), token=FakeToken(["operateur"]))
    e = _entries()[-1]
    assert e["event"] == "tool_call" and e["tool"] == "list_active_channels"
    assert e["outcome"] == "success" and e["actor"] == "tester" and e["request_id"] == "req-a"


async def test_rbac_denial_is_audited():
    await tools.originate_call("PJSIP/1", "2", FakeElicitContext(AcceptedElicitation(data=True)),
                               token=FakeToken(["operateur"]))
    events = _entries()
    assert any(x["event"] == "rbac_denied" and x["tool"] == "originate_call" for x in events)


async def test_spy_is_audited_with_mode():
    ctx = FakeElicitContext(AcceptedElicitation(data=True))
    await tools.spy_channel("PJSIP/1001-1", "PJSIP/1099", ctx, acknowledge_legal=True,
                            token=FakeToken(["superviseur"]))
    events = _entries()
    spy = [x for x in events if x["event"] == "channel_spy"]
    assert spy and spy[-1]["params"]["mode"] == "listen"


async def test_spy_without_legal_ack_is_audited():
    ctx = FakeElicitContext(AcceptedElicitation(data=True))
    await tools.spy_channel("PJSIP/1001-1", "PJSIP/1099", ctx, token=FakeToken(["superviseur"]))
    assert any(x["event"] == "spy_refused_legal" for x in _entries())


def test_audit_lines_are_valid_jsonl():
    audit_log("tool_call", actor="a", tool="b", outcome="success", params={"k": [1, 2, 3]})
    for entry in _entries():
        assert json.dumps(entry)  # round-trip parse/serialise
