"""Tool layer: RBAC gate + structured errors, with fakes injected into the module."""
import pytest
from fastmcp.server.elicitation import AcceptedElicitation, DeclinedElicitation

import src.interfaces.mcp_tools as tools
from src.security.rbac import require_role
from tests.fakes import FakeAsteriskGateway, FakeElicitContext, FakeToken, sample_channel


class _Security:
    def require_role(self, token, role):
        require_role(token, role)


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    gw = FakeAsteriskGateway(channels=[sample_channel()])
    monkeypatch.setattr(tools, "_gateway", gw)
    monkeypatch.setattr(tools, "_security", _Security())
    return gw


async def test_list_channels_ok_for_operateur(_wire):
    out = await tools.list_active_channels(token=FakeToken(["operateur"]))
    assert out["status"] == "success"
    assert len(out["channels"]) == 1


async def test_list_channels_forbidden_without_role(_wire):
    out = await tools.list_active_channels(token=FakeToken(["autre"]))
    assert out["status"] == "error"
    assert out["error"] == "unauthorized"


async def test_analyze_quality_needs_superviseur(_wire):
    denied = await tools.analyze_call_quality("c1", token=FakeToken(["operateur"]))
    assert denied["error"] == "unauthorized"
    ok = await tools.analyze_call_quality("c1", token=FakeToken(["superviseur"]))
    assert ok["status"] == "success"
    assert "mos_estimate" in ok["quality"]


async def test_originate_call_admin_and_hitl(_wire):
    ctx = FakeElicitContext(AcceptedElicitation(data=True))
    out = await tools.originate_call(
        "PJSIP/1001", "1002", ctx, context="internal", token=FakeToken(["admin"])
    )
    assert out["status"] == "success"
    assert _wire.calls[0][0] == "originate"


async def test_originate_call_cancelled_when_hitl_declines(_wire):
    ctx = FakeElicitContext(DeclinedElicitation())
    out = await tools.originate_call(
        "PJSIP/1001", "1002", ctx, context="internal", token=FakeToken(["admin"])
    )
    assert out["status"] == "cancelled"
    assert _wire.calls == []


async def test_spy_barge_requires_admin(_wire):
    ctx = FakeElicitContext(AcceptedElicitation(data=True))
    denied = await tools.start_channel_spy(
        "PJSIP/1001-1", "PJSIP/1099", ctx, mode="barge", token=FakeToken(["superviseur"])
    )
    assert denied["error"] == "unauthorized"
