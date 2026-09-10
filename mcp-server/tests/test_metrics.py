"""Métriques Prometheus : compteurs des outils + endpoint /metrics."""
import pytest

import src.interfaces.mcp_tools as tools
from src.observability import metrics
from tests.fakes import FakeAsteriskGateway, FakeToken, sample_channel


class _Security:
    def require_role(self, token, role):
        from src.security.rbac import require_role
        require_role(token, role)


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    monkeypatch.setattr(tools, "_gateway", FakeAsteriskGateway(channels=[sample_channel()]))
    monkeypatch.setattr(tools, "_security", _Security())


def _val(counter, **labels):
    return counter.labels(**labels)._value.get()


async def test_success_and_denial_are_counted():
    before_ok = _val(metrics.TOOL_CALLS, tool="list_active_channels", status="success")
    before_denied = _val(metrics.RBAC_DENIALS, tool="originate_call", required_role="admin")

    r1 = await tools.list_active_channels(token=FakeToken(["operateur"]))
    assert r1["status"] == "success"
    r2 = await tools.originate_call("PJSIP/1", "2", None, token=FakeToken(["operateur"]))
    assert r2["error"] == "unauthorized"

    assert _val(metrics.TOOL_CALLS, tool="list_active_channels", status="success") == before_ok + 1
    assert _val(metrics.RBAC_DENIALS, tool="originate_call", required_role="admin") == before_denied + 1
    assert metrics.ACTIVE_CHANNELS._value.get() == 1


async def test_metrics_endpoint_renders_prometheus_text():
    await tools.list_active_channels(token=FakeToken(["operateur"]))
    body, content_type = metrics.render()
    text = body.decode()
    assert "mcp_tool_calls_total" in text
    assert "text/plain" in content_type or "openmetrics" in content_type


def test_voice_turn_metrics():
    before = metrics.VOICE_TURNS.labels(within_budget="false")._value.get()
    metrics.record_voice_turn(
        {"stt_ms": 300, "llm_ms": 900, "tts_ms": 500, "time_to_first_audio_ms": 1700},
        within_budget=False,
    )
    assert metrics.VOICE_TURNS.labels(within_budget="false")._value.get() == before + 1
