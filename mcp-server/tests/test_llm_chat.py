"""Outil superviseur llm_chat : use case, RBAC, contexte, HITL, métriques."""
import pytest
from fastmcp.server.elicitation import DeclinedElicitation

import src.interfaces.mcp_tools as tools
from src.application.llm_chat import MAX_PROMPT_CHARS, LlmChatUseCase
from src.domain.exceptions import LlmUnavailableError
from src.observability import metrics
from tests.fakes import (
    FailingLLM,
    FakeElicitContext,
    FakeSecurityManager,
    FakeToken,
    PassthroughSanitizer,
    RecordingLLM,
    sample_channel,
)


class _Ctx:
    request_id = "req-llm"


def _val(counter, **labels):
    return counter.labels(**labels)._value.get()


# ── use case ─────────────────────────────────────────────────────────────────
async def test_chat_returns_reply(gateway, sanitizer):
    llm = RecordingLLM("tout va bien")
    out = await LlmChatUseCase(gateway, llm, sanitizer).execute("résume l'état")
    assert out["reply"] == "tout va bien"
    assert llm.calls[0]["prompt"] == "résume l'état"
    assert llm.calls[0]["system_prompt"] is None  # défaut de l'adaptateur


async def test_chat_injects_sanitized_context(gateway):
    from src.security.sanitizer import DataSanitizerImpl

    gateway.channels = [sample_channel("PJSIP/1001-1")]
    llm = RecordingLLM()
    await LlmChatUseCase(gateway, llm, DataSanitizerImpl()).execute(
        "que se passe-t-il ?", context=["channels"]
    )
    system = llm.calls[0]["system_prompt"]
    assert system is not None
    assert "PJSIP/1001-1" in system
    assert "UNTRUSTED DATA" in system  # enveloppe DataSanitizer


async def test_chat_unknown_context_gives_no_context(gateway, sanitizer):
    llm = RecordingLLM()
    await LlmChatUseCase(gateway, llm, sanitizer).execute("analyse", context=["inconnu"])
    assert llm.calls[0]["system_prompt"] is None


async def test_chat_invalid_cdr_limit_falls_back_to_default(gateway, sanitizer):
    llm = RecordingLLM()
    await LlmChatUseCase(gateway, llm, sanitizer).execute("analyse", context=["cdr:bad"])
    assert "Derniers CDR: aucun" in llm.calls[0]["system_prompt"]


async def test_chat_passes_max_tokens_and_custom_system(gateway, sanitizer):
    llm = RecordingLLM()
    await LlmChatUseCase(gateway, llm, sanitizer).execute(
        "hello", system_prompt="Soyez bref.", max_tokens=200, history=[{"role": "user", "content": "bonjour"}]
    )
    call = llm.calls[0]
    assert call["max_tokens"] == 200
    assert call["system_prompt"] == "Soyez bref."
    assert call["history"] == [{"role": "user", "content": "bonjour"}]


async def test_chat_maps_ollama_failure(gateway, sanitizer):
    llm = FailingLLM()
    with pytest.raises(LlmUnavailableError):
        await LlmChatUseCase(gateway, llm, sanitizer).execute("dis bonjour")


# ── couche outil MCP ─────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    from tests.fakes import FakeAsteriskGateway
    gw = FakeAsteriskGateway(channels=[sample_channel("PJSIP/1001-1")])
    monkeypatch.setattr(tools, "_gateway", gw)
    monkeypatch.setattr(tools, "_security", FakeSecurityManager(["superviseur"]))
    monkeypatch.setattr(tools, "_sanitizer", PassthroughSanitizer())
    monkeypatch.setattr(tools, "_llm", RecordingLLM("réponse superviseur"))
    monkeypatch.setenv("MCP_HITL_MODE", "pilotage")
    return gw


async def test_llm_chat_requires_superviseur(_wire):
    denied = await tools.llm_chat("bonjour", _Ctx(), token=FakeToken(["operateur"]))
    assert denied["error"] == "unauthorized"
    ok = await tools.llm_chat("bonjour", _Ctx(), token=FakeToken(["superviseur"]))
    assert ok["status"] == "success"
    assert ok["reply"] == "réponse superviseur"
    assert ok["model"] == "fake-model"


async def test_llm_chat_rejects_empty_and_too_long_prompts(_wire):
    empty = await tools.llm_chat("   ", _Ctx(), token=FakeToken(["superviseur"]))
    assert empty["error"] == "empty_prompt"
    long_p = await tools.llm_chat("a" * (MAX_PROMPT_CHARS + 1), _Ctx(), token=FakeToken(["superviseur"]))
    assert long_p["error"] == "prompt_too_long"


async def test_llm_chat_hitl_all_mode_declines(_wire, monkeypatch):
    monkeypatch.setenv("MCP_HITL_MODE", "all")
    ctx = FakeElicitContext(DeclinedElicitation())
    out = await tools.llm_chat("bonjour", ctx, token=FakeToken(["superviseur"]))
    assert out["status"] == "cancelled"


async def test_llm_chat_reports_ollama_unavailable(_wire, monkeypatch):
    before = _val(metrics.LLM_CALLS, model="fake-model", status="error")
    monkeypatch.setattr(tools, "_llm", FailingLLM())
    out = await tools.llm_chat("bonjour", _Ctx(), token=FakeToken(["superviseur"]))
    assert out["error"] == "llm_unavailable"
    assert _val(metrics.LLM_CALLS, model="fake-model", status="error") == before + 1


async def test_llm_chat_counts_success_metric(_wire):
    before = _val(metrics.LLM_CALLS, model="fake-model", status="success")
    await tools.llm_chat("bonjour", _Ctx(), token=FakeToken(["superviseur"]))
    assert _val(metrics.LLM_CALLS, model="fake-model", status="success") == before + 1