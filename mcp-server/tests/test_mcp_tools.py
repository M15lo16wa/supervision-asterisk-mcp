"""Tool layer: noms conformes B.4, RBAC, HITL, avertissement légal spy."""
import pytest
from fastmcp.server.elicitation import AcceptedElicitation, DeclinedElicitation

import src.interfaces.mcp_tools as tools
from src.domain.entities import QueueStats, TrunkUtilization
from src.security.rbac import require_role
from tests.fakes import (
    FakeAsteriskGateway,
    FakeElicitContext,
    FakeToken,
    PassthroughSanitizer,
    sample_channel,
    sample_extension,
)


class _Ctx:
    request_id = "req-1"


class _Security:
    def require_role(self, token, role):
        require_role(token, role)


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    gw = FakeAsteriskGateway(
        channels=[sample_channel("PJSIP/1001-1")],
        extensions=[sample_extension("1001")],
        queues=[QueueStats("support", 2, 3, 1, 40, 5, 92.0, 12, 180)],
        trunks=[TrunkUtilization("trunk-out", "available", 3, 10, 1, 2)],
    )
    monkeypatch.setattr(tools, "_gateway", gw)
    monkeypatch.setattr(tools, "_security", _Security())
    monkeypatch.setattr(tools, "_sanitizer", PassthroughSanitizer())
    monkeypatch.setenv("MCP_HITL_MODE", "pilotage")
    return gw


# ---- noms d'outils conformes B.4 -------------------------------------------
async def test_all_spec_tool_names_are_registered():
    names = {t.name for t in await tools.mcp.list_tools()}
    assert {
        "list_active_channels", "get_channel_info", "get_queue_stats", "get_extension_status",
        "get_cdr_report", "analyze_call_quality", "get_trunk_utilization",
        "originate_call", "hangup_channel", "redirect_call", "spy_channel",
    } <= names


# ---- lecture --------------------------------------------------------------
async def test_list_channels_ok_for_operateur(_wire):
    out = await tools.list_active_channels(_Ctx(), token=FakeToken(["operateur"]))
    assert out["status"] == "success" and len(out["channels"]) == 1


async def test_list_channels_forbidden_without_role(_wire):
    out = await tools.list_active_channels(_Ctx(), token=FakeToken(["autre"]))
    assert out["error"] == "unauthorized"


async def test_get_channel_info(_wire):
    ok = await tools.get_channel_info("PJSIP/1001-1", _Ctx(), token=FakeToken(["operateur"]))
    assert ok["status"] == "success"
    missing = await tools.get_channel_info("nope", _Ctx(), token=FakeToken(["operateur"]))
    assert missing["error"] == "channel_not_found"


async def test_get_queue_stats(_wire):
    out = await tools.get_queue_stats(_Ctx(), token=FakeToken(["operateur"]))
    assert out["queues"][0]["name"] == "support"


async def test_get_trunk_utilization_needs_superviseur(_wire):
    assert (await tools.get_trunk_utilization(_Ctx(), token=FakeToken(["operateur"])))["error"] == "unauthorized"
    ok = await tools.get_trunk_utilization(_Ctx(), token=FakeToken(["superviseur"]))
    assert ok["trunks"][0]["utilization_percent"] == 30.0


async def test_cdr_report_needs_superviseur(_wire):
    assert (await tools.get_cdr_report(_Ctx(), token=FakeToken(["operateur"])))["error"] == "unauthorized"
    assert (await tools.get_cdr_report(_Ctx(), token=FakeToken(["superviseur"])))["status"] == "success"


# ---- pilotage ------------------------------------------------------------
async def test_originate_call_admin_and_hitl(_wire):
    ctx = FakeElicitContext(AcceptedElicitation(data=True))
    out = await tools.originate_call("PJSIP/1001", "1002", ctx, context="internal", token=FakeToken(["admin"]))
    assert out["status"] == "success" and _wire.calls[0][0] == "originate"


async def test_originate_call_cancelled_when_hitl_declines(_wire):
    ctx = FakeElicitContext(DeclinedElicitation())
    out = await tools.originate_call("PJSIP/1001", "1002", ctx, context="internal", token=FakeToken(["admin"]))
    assert out["status"] == "cancelled" and _wire.calls == []


async def test_redirect_call_is_admin_hitl(_wire):
    ctx = FakeElicitContext(DeclinedElicitation())
    out = await tools.redirect_call("c1", "1099", ctx, context="internal", token=FakeToken(["admin"]))
    assert out["status"] == "cancelled"


# ---- spy : avertissement légal + RBAC ------------------------------------
async def test_spy_requires_legal_acknowledgement(_wire):
    ctx = FakeElicitContext(AcceptedElicitation(data=True))
    out = await tools.spy_channel("PJSIP/1001-1", "PJSIP/1099", ctx, token=FakeToken(["superviseur"]))
    assert out["error"] == "legal_acknowledgement_required"
    assert "LÉGAL" in out["legal_notice"]


async def test_spy_listen_ok_for_superviseur_with_ack(_wire):
    ctx = FakeElicitContext(AcceptedElicitation(data=True))
    out = await tools.spy_channel(
        "PJSIP/1001-1", "PJSIP/1099", ctx, acknowledge_legal=True, token=FakeToken(["superviseur"])
    )
    assert out["status"] == "success"
    assert _wire.calls[0][0] == "spy"
    assert "legal_notice" in out


async def test_spy_barge_requires_admin(_wire):
    ctx = FakeElicitContext(AcceptedElicitation(data=True))
    denied = await tools.spy_channel(
        "PJSIP/1001-1", "PJSIP/1099", ctx, mode="barge", acknowledge_legal=True,
        token=FakeToken(["superviseur"]),
    )
    assert denied["error"] == "unauthorized"


# ---- MCP_HITL_MODE=all : consentement aussi sur la lecture ---------------
async def test_hitl_all_mode_prompts_on_read(_wire, monkeypatch):
    monkeypatch.setenv("MCP_HITL_MODE", "all")
    ctx = FakeElicitContext(DeclinedElicitation())
    out = await tools.list_active_channels(ctx, token=FakeToken(["operateur"]))
    assert out["status"] == "cancelled"
