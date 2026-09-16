# src/observability/metrics.py
"""Prometheus metrics for the MCP server and the voice pipeline.

Exposed at ``GET /metrics`` (MCP server) and on a small HTTP server for the
voice pipeline. ``prometheus_client`` is a hard dependency of the server; if it
is ever missing, every helper degrades to a no-op so the app still runs.
"""
from __future__ import annotations

import time
from contextlib import contextmanager

try:
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

    _ENABLED = True
except ModuleNotFoundError:  # pragma: no cover
    _ENABLED = False
    CONTENT_TYPE_LATEST = "text/plain"

    class _Noop:
        def labels(self, *_, **__):
            return self

        def inc(self, *_, **__):
            return None

        def observe(self, *_, **__):
            return None

        def set(self, *_, **__):
            return None

    def Counter(*_, **__):
        return _Noop()

    Gauge = Histogram = Counter

    def generate_latest(*_, **__):
        return b""


_LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 1.5, 2.5, 5.0, 10.0)

# ── serveur MCP ──────────────────────────────────────────────────────────────
TOOL_CALLS = Counter(
    "mcp_tool_calls_total", "Appels d'outils MCP", ["tool", "status"]
)
TOOL_LATENCY = Histogram(
    "mcp_tool_duration_seconds", "Durée d'exécution d'un outil MCP", ["tool"],
    buckets=_LATENCY_BUCKETS,
)
RBAC_DENIALS = Counter(
    "mcp_rbac_denials_total", "Refus RBAC", ["tool", "required_role"]
)
HITL_OUTCOMES = Counter(
    "mcp_hitl_total", "Résultats des confirmations Human-in-the-Loop", ["tool", "outcome"]
)
ASTERISK_ERRORS = Counter(
    "mcp_asterisk_errors_total", "Erreurs de communication Asterisk", ["tool", "kind"]
)
ACTIVE_CHANNELS = Gauge(
    "mcp_active_channels", "Canaux actifs vus au dernier list_active_channels"
)

# ── LLM superviseur (llm_chat) ────────────────────────────────────────────────
LLM_CALLS = Counter(
    "mcp_llm_calls_total", "Appels au LLM local (Ollama)", ["model", "status"]
)
LLM_DURATION = Histogram(
    "mcp_llm_duration_seconds", "Latence d'une réponse LLM", ["model"],
    buckets=_LATENCY_BUCKETS,
)

# ── pipeline vocal ───────────────────────────────────────────────────────────
VOICE_TURNS = Counter(
    "voice_turns_total", "Tours du pipeline Speech-to-Speech", ["within_budget"]
)
VOICE_STAGE_LATENCY = Histogram(
    "voice_stage_duration_seconds", "Latence par étape du pipeline vocal", ["stage"],
    buckets=_LATENCY_BUCKETS,
)
VOICE_BUDGET_EXCEEDED = Counter(
    "voice_budget_exceeded_total", "Tours dépassant le budget de latence"
)
VOICE_ACTIVE_CALLS = Gauge(
    "voice_active_calls", "Appels en cours dans le pipeline vocal"
)


def enabled() -> bool:
    return _ENABLED


def render() -> tuple[bytes, str]:
    """Retourne (corps, content-type) pour l'endpoint /metrics."""
    return generate_latest(), CONTENT_TYPE_LATEST


# ── helpers serveur MCP ──────────────────────────────────────────────────────
@contextmanager
def tool_timer(tool: str):
    """Chronomètre un outil et enregistre sa latence (le statut est posé à part)."""
    start = time.perf_counter()
    try:
        yield
    finally:
        TOOL_LATENCY.labels(tool=tool).observe(time.perf_counter() - start)


def tool_result(tool: str, status: str) -> None:
    TOOL_CALLS.labels(tool=tool, status=status).inc()


def rbac_denied(tool: str, required_role: str) -> None:
    RBAC_DENIALS.labels(tool=tool, required_role=required_role).inc()


def hitl_outcome(tool: str, outcome: str) -> None:
    HITL_OUTCOMES.labels(tool=tool, outcome=outcome).inc()


def asterisk_error(tool: str, kind: str) -> None:
    ASTERISK_ERRORS.labels(tool=tool, kind=kind).inc()


def observe_active_channels(count: int) -> None:
    ACTIVE_CHANNELS.set(count)


# ── helpers LLM superviseur ───────────────────────────────────────────────────
@contextmanager
def llm_timer(model: str):
    """Chronomètre un appel LLM (le statut est posé à part)."""
    start = time.perf_counter()
    try:
        yield
    finally:
        LLM_DURATION.labels(model=model).observe(time.perf_counter() - start)


def llm_result(model: str, status: str) -> None:
    LLM_CALLS.labels(model=model, status=status).inc()


# ── helpers pipeline vocal ───────────────────────────────────────────────────
def record_voice_turn(timings: dict, within_budget: bool) -> None:
    VOICE_TURNS.labels(within_budget=str(within_budget).lower()).inc()
    for stage in ("stt_ms", "llm_ms", "tts_ms", "time_to_first_audio_ms"):
        if stage in timings:
            VOICE_STAGE_LATENCY.labels(stage=stage.removesuffix("_ms")).observe(
                float(timings[stage]) / 1000.0
            )
    if not within_budget:
        VOICE_BUDGET_EXCEEDED.inc()
