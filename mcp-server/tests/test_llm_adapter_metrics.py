"""Métriques LLM portées par l'adaptateur, et non plus par le call-site.

``OllamaLLM`` est le seul point de passage commun à l'outil superviseur
``llm_chat`` et au pipeline vocal (``stream_reply``). La mesure se faisait
pourtant dans ``mcp_tools.llm_chat`` : les tours vocaux étaient donc
totalement absents de Prometheus, d'où des panneaux Ollama vides.
"""
import httpx
import pytest

from src.domain.exceptions import LlmUnavailableError
from src.observability import metrics
from tests.fakes import StubLLMTransport, measuring_llm

MODEL = "fake-model"

OPENAI_REPLY = {"choices": [{"message": {"content": "réponse"}}]}
OPENAI_STREAM = [
    'data: {"choices":[{"delta":{"content":"bonjour"}}]}',
    "data: [DONE]",
]


def _val(counter, **labels):
    return counter.labels(**labels)._value.get()


def _dur(**labels):
    return metrics.LLM_DURATION.labels(**labels)


# ── reply() — outil superviseur llm_chat ─────────────────────────────────────
async def test_reply_records_success():
    before = _val(metrics.LLM_CALLS, model=MODEL, status="success")
    dur_before = _dur(model=MODEL)._sum.get()

    llm = measuring_llm(transport=StubLLMTransport(payload=OPENAI_REPLY))
    assert await llm.reply("salut") == "réponse"

    assert _val(metrics.LLM_CALLS, model=MODEL, status="success") == before + 1
    assert _dur(model=MODEL)._sum.get() > dur_before  # durée observée


async def test_reply_records_error():
    before_err = _val(metrics.LLM_CALLS, model=MODEL, status="error")
    before_ok = _val(metrics.LLM_CALLS, model=MODEL, status="success")

    llm = measuring_llm(
        transport=StubLLMTransport(post_error=httpx.ConnectError("panne"))
    )
    with pytest.raises(LlmUnavailableError):
        await llm.reply("salut")

    assert _val(metrics.LLM_CALLS, model=MODEL, status="error") == before_err + 1
    # Le statut est posé une seule fois : pas de comptage en double.
    assert _val(metrics.LLM_CALLS, model=MODEL, status="success") == before_ok


# ── stream_reply() — pipeline vocal ──────────────────────────────────────────
async def test_stream_reply_records_success():
    before = _val(metrics.LLM_CALLS, model=MODEL, status="success")

    llm = measuring_llm(
        transport=StubLLMTransport(lines=OPENAI_STREAM, payload=OPENAI_REPLY)
    )
    chunks = [piece async for piece in llm.stream_reply("salut")]

    assert chunks == ["bonjour"]
    assert _val(metrics.LLM_CALLS, model=MODEL, status="success") == before + 1


async def test_stream_reply_records_error():
    before = _val(metrics.LLM_CALLS, model=MODEL, status="error")

    llm = measuring_llm(
        transport=StubLLMTransport(stream_error=httpx.ConnectError("panne"))
    )
    with pytest.raises(LlmUnavailableError):
        async for _ in llm.stream_reply("salut"):
            pass

    assert _val(metrics.LLM_CALLS, model=MODEL, status="error") == before + 1


async def test_stream_reply_records_cancelled_when_consumer_stops():
    """Appel raccroché pendant la génération : ni succès ni erreur."""
    before_cancelled = _val(metrics.LLM_CALLS, model=MODEL, status="cancelled")
    before_ok = _val(metrics.LLM_CALLS, model=MODEL, status="success")

    llm = measuring_llm(
        transport=StubLLMTransport(lines=OPENAI_STREAM, payload=OPENAI_REPLY)
    )
    gen = llm.stream_reply("salut")
    assert await gen.__anext__() == "bonjour"  # le pipeline a commencé à écouter
    await gen.aclose()  # StasisEnd : le consommateur abandonne

    assert _val(metrics.LLM_CALLS, model=MODEL, status="cancelled") == before_cancelled + 1
    assert _val(metrics.LLM_CALLS, model=MODEL, status="success") == before_ok
