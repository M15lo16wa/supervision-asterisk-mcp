# src/interfaces/mcp_tools.py
"""MCP tools exposed to clients (noms conformes au cahier des charges B.4).

Chaque outil :
  1. RBAC en première ligne          -> require_role(token, "...")
  2. consentement humain              -> HITL sur le pilotage ; sur TOUS les
     outils si MCP_HITL_MODE=all (A.6 : « chaque exécution requiert le
     consentement explicite »). Les annotations destructiveHint ne sont pas
     considérées comme suffisantes.
  3. sortie assainie                  -> DataSanitizer (entrées non fiables)
  4. journal d'audit                  -> src.audit (acteur/outil/paramètres/résultat)
  5. métriques Prometheus             -> GET /metrics

Matrice de droits (B.6) :
  Opérateur   : lecture seule
  Superviseur : + analyse (CDR, MOS/RTCP, trunks) + écoute discrète ChanSpy
  Admin       : + pilotage (origination, hangup, transfert, whisper/barge)

Outils (B.4) :
  list_active_channels · get_channel_info · get_queue_stats · get_extension_status
  get_cdr_report · analyze_call_quality · get_trunk_utilization
  originate_call · hangup_channel · redirect_call · spy_channel
"""
import logging
import os
from collections.abc import Awaitable, Callable

from fastmcp import Context, FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import CurrentAccessToken
from starlette.requests import Request
from starlette.responses import Response

from src.adapters.asterisk_gateway import PanoramiskGateway
from src.adapters.hitl_confirmation import FastMcpHitlConfirmation
from src.adapters.ollama_llm import OllamaLLM
from src.application.analyze_call_quality import AnalyzeCallQualityUseCase
from src.application.get_call_records import GetCallRecordsUseCase
from src.application.get_channel_info import GetChannelInfoUseCase
from src.application.get_queue_stats import GetQueueStatsUseCase
from src.application.get_trunk_utilization import GetTrunkUtilizationUseCase
from src.application.hangup_channel import HangupChannelUseCase
from src.application.list_active_channels import ListActiveChannelsUseCase
from src.application.list_extensions import ListExtensionsUseCase
from src.application.llm_chat import MAX_PROMPT_CHARS, LlmChatUseCase
from src.application.originate_call import OriginateCallUseCase
from src.application.start_channel_spy import StartChannelSpyUseCase
from src.application.transfer_call import TransferCallUseCase
from src.audit import audit_log
from src.config import settings
from src.domain.entities import SpyMode
from src.domain.exceptions import (
    AsteriskCommandError,
    AsteriskConnectionError,
    ChannelNotFound,
    HitlConfirmationDenied,
    LlmUnavailableError,
    UnauthorizedAction,
)
from src.domain.legal import SPY_LEGAL_NOTICE
from src.observability import metrics
from src.security.auth import build_auth_provider, get_security_manager
from src.security.sanitizer import DataSanitizerImpl

logger = logging.getLogger(__name__)

mcp = FastMCP(name="asterisk-mcp-supervision", auth=build_auth_provider())

_security = get_security_manager()
_sanitizer = DataSanitizerImpl()
_gateway = PanoramiskGateway(
    host=settings.asterisk_host,
    port=settings.asterisk_ami_port,
    username=settings.asterisk_ami_user,
    secret=settings.asterisk_ami_secret,
    default_context=settings.asterisk_default_context,
)

_llm = OllamaLLM(
    base_url=settings.llm.base_url,
    model=settings.llm.model,
    system_prompt=settings.llm.system_prompt,
    temperature=settings.llm.temperature,
    num_predict=settings.llm.num_predict,
    timeout=settings.llm.timeout_s,
)


@mcp.custom_route("/metrics", methods=["GET"])
async def prometheus_metrics(_request: Request) -> Response:
    body, content_type = metrics.render()
    return Response(content=body, media_type=content_type)


def _username(token: AccessToken) -> str:
    return (token.claims or {}).get("preferred_username") or token.client_id or "unknown"


def _request_id(ctx: Context | None) -> str | None:
    return getattr(ctx, "request_id", None) if ctx else None


def _llm_model() -> str:
    """Nom du modèle LLM actif (l'adaptateur exposé via ``_llm``)."""
    return getattr(_llm, "model", settings.llm.model)


def _hitl_mode() -> str:
    """"pilotage" (défaut) ou "all" — consentement humain sur tous les outils."""
    return os.getenv("MCP_HITL_MODE", settings.hitl_mode).strip().lower()


async def _guarded(
    tool: str,
    required_role: str,
    token: AccessToken,
    work: Callable[[], Awaitable[dict]],
    *,
    ctx: Context | None = None,
    params: dict | None = None,
    hitl_in_usecase: bool = False,
    pre_check: Callable[[], dict | None] | None = None,
) -> dict:
    """RBAC + consentement + exécution + mapping d'erreur + audit + métriques.

    ``pre_check`` : verrou additionnel propre à l'outil, exécuté **après** le
    RBAC et avant l'exécution (ordre volontaire : on n'exige pas une
    attestation de base légale d'un acteur qui n'a pas le droit d'agir).
    Il renvoie ``None`` pour passer, sinon la réponse d'erreur ; l'événement
    d'audit reprend ``_event`` et porte le ``client_id``/``request_id`` de la
    requête, comme les autres entrées.
    """
    actor = _username(token)
    client_id = getattr(token, "client_id", "") or ""
    params = params or {}
    rid = _request_id(ctx)

    with metrics.tool_timer(tool):
        # 1. RBAC
        try:
            _security.require_role(token, required_role)
        except UnauthorizedAction as e:
            metrics.rbac_denied(tool, required_role)
            metrics.tool_result(tool, "unauthorized")
            audit_log("rbac_denied", actor=actor, tool=tool, outcome="unauthorized",
                      client_id=client_id, params=params, detail=str(e), request_id=rid)
            return {"status": "error", "error": "unauthorized", "detail": str(e)}

        # 1b. Verrou propre à l'outil — après le RBAC (voir docstring).
        if pre_check is not None:
            denial = pre_check()
            if denial is not None:
                event = denial.pop("_event", "tool_refused")
                metrics.tool_result(tool, "cancelled")
                audit_log(event, actor=actor, tool=tool, outcome="cancelled",
                          client_id=client_id, params=params,
                          detail=denial.get("detail"), request_id=rid)
                return denial

        # 2. Consentement explicite pour TOUS les outils si MCP_HITL_MODE=all
        #    (les outils de pilotage font déjà leur propre HITL dans le use case).
        if _hitl_mode() == "all" and ctx is not None and not hitl_in_usecase:
            try:
                confirmed = await FastMcpHitlConfirmation(ctx).confirm(
                    action=tool, user=actor, details=params
                )
                if not confirmed:
                    raise HitlConfirmationDenied(f"{tool} refusé")
            except HitlConfirmationDenied as e:
                metrics.hitl_outcome(tool, "denied")
                metrics.tool_result(tool, "cancelled")
                audit_log("hitl_denied", actor=actor, tool=tool, outcome="cancelled",
                          client_id=client_id, params=params, detail=str(e), request_id=rid)
                return {"status": "cancelled", "reason": str(e)}

        # 3. Exécution
        try:
            payload = await work()
            metrics.tool_result(tool, "success")
            audit_log("tool_call", actor=actor, tool=tool, outcome="success",
                      client_id=client_id, params=params, request_id=rid)
            return {"status": "success", **payload}
        except HitlConfirmationDenied as e:
            metrics.hitl_outcome(tool, "denied")
            metrics.tool_result(tool, "cancelled")
            audit_log("hitl_denied", actor=actor, tool=tool, outcome="cancelled",
                      client_id=client_id, params=params, detail=str(e), request_id=rid)
            return {"status": "cancelled", "reason": str(e)}
        except ChannelNotFound as e:
            metrics.tool_result(tool, "channel_not_found")
            audit_log("tool_call", actor=actor, tool=tool, outcome="error",
                      client_id=client_id, params=params, detail=str(e), request_id=rid)
            return {"status": "error", "error": "channel_not_found", "detail": str(e)}
        except (AsteriskConnectionError, AsteriskCommandError) as e:
            metrics.asterisk_error(tool, type(e).__name__)
            metrics.tool_result(tool, "asterisk_unavailable")
            audit_log("tool_call", actor=actor, tool=tool, outcome="error",
                      client_id=client_id, params=params, detail=str(e), request_id=rid)
            return {"status": "error", "error": "asterisk_unavailable", "detail": str(e)}
        except LlmUnavailableError as e:
            # Durée/statut LLM mesurés par l'adaptateur OllamaLLM (point de
            # passage commun) : les recompter ici compterait deux fois l'échec.
            metrics.tool_result(tool, "llm_unavailable")
            audit_log("tool_call", actor=actor, tool=tool, outcome="error",
                      client_id=client_id, params=params, detail=str(e), request_id=rid)
            return {"status": "error", "error": "llm_unavailable", "detail": str(e)}
        except Exception as e:
            logger.exception("unexpected error in %s", tool)
            metrics.tool_result(tool, "internal")
            audit_log("tool_call", actor=actor, tool=tool, outcome="error",
                      client_id=client_id, params=params, detail=str(e), request_id=rid)
            return {"status": "error", "error": "internal", "detail": str(e)}


# ─────────────────────────  lecture (Opérateur+)  ─────────────────────────

@mcp.tool()
async def list_active_channels(ctx: Context, token: AccessToken = CurrentAccessToken()) -> dict:
    """Liste les canaux Asterisk actifs (état, appelant, contexte, durée)."""
    async def work():
        data = await ListActiveChannelsUseCase(_gateway, _sanitizer).execute()
        metrics.observe_active_channels(len(data))
        return {"channels": data}

    return await _guarded("list_active_channels", "operateur", token, work, ctx=ctx)


@mcp.tool()
async def get_channel_info(
    channel_id: str, ctx: Context, token: AccessToken = CurrentAccessToken()
) -> dict:
    """Détails d'un canal précis (nom, état, CLID, contexte/exten, application, durée)."""
    async def work():
        return {"channel": await GetChannelInfoUseCase(_gateway, _sanitizer).execute(channel_id)}

    return await _guarded("get_channel_info", "operateur", token, work,
                          ctx=ctx, params={"channel_id": channel_id})


@mcp.tool()
async def get_queue_stats(
    ctx: Context, queue: str | None = None, token: AccessToken = CurrentAccessToken()
) -> dict:
    """Statistiques des files d'attente (appels en attente, membres, SLA, temps d'attente)."""
    async def work():
        return {"queues": await GetQueueStatsUseCase(_gateway, _sanitizer).execute(queue)}

    return await _guarded("get_queue_stats", "operateur", token, work,
                          ctx=ctx, params={"queue": queue})


@mcp.tool()
async def get_extension_status(
    ctx: Context, context: str | None = None, token: AccessToken = CurrentAccessToken()
) -> dict:
    """État des lignes / postes (hints du dialplan) — filtrable par contexte."""
    async def work():
        return {"extensions": await ListExtensionsUseCase(_gateway, _sanitizer).execute(context=context)}

    return await _guarded("get_extension_status", "operateur", token, work,
                          ctx=ctx, params={"context": context})


# ───────────────────────  analyse (Superviseur+)  ────────────────────────

@mcp.tool()
async def get_cdr_report(
    ctx: Context, limit: int = 20, token: AccessToken = CurrentAccessToken()
) -> dict:
    """Historique des appels (CDR) — journal temps réel bufferisé par le serveur."""
    async def work():
        return {"records": await GetCallRecordsUseCase(_gateway, _sanitizer).execute(limit=limit)}

    return await _guarded("get_cdr_report", "superviseur", token, work,
                          ctx=ctx, params={"limit": limit})


@mcp.tool()
async def analyze_call_quality(
    channel_id: str, ctx: Context, token: AccessToken = CurrentAccessToken()
) -> dict:
    """Qualité RTP/RTCP d'un canal : gigue, perte, RTT et MOS estimé (modèle E)."""
    async def work():
        return {"quality": await AnalyzeCallQualityUseCase(_gateway, _sanitizer).execute(channel_id)}

    return await _guarded("analyze_call_quality", "superviseur", token, work,
                          ctx=ctx, params={"channel_id": channel_id})


@mcp.tool()
async def get_trunk_utilization(ctx: Context, token: AccessToken = CurrentAccessToken()) -> dict:
    """Charge des trunks SIP : canaux actifs (entrants/sortants) vs capacité configurée."""
    async def work():
        return {"trunks": await GetTrunkUtilizationUseCase(_gateway, _sanitizer).execute()}

    return await _guarded("get_trunk_utilization", "superviseur", token, work, ctx=ctx)


@mcp.tool()
async def llm_chat(
    prompt: str,
    ctx: Context,
    context: list[str] | None = None,
    system_prompt: str | None = None,
    max_tokens: int | None = None,
    history: list[dict] | None = None,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Interroge le LLM local (Ollama) pour l'aide à la supervision.

    Le superviseur pose une question en langage naturel ; `context` annexe des
    données temps réel d'Asterisk (traitées comme contenu, jamais comme instruction) :
    `["channels", "cdr:10", "queues", "trunks", "extensions"]` (le préfixe `:N`
    borne le nombre d'enregistrements, ex. `cdr:5`).

    `history` transmet les tours précédents `[{"role": "user"|"assistant",
    "content": "..."}]` pour un dialogue multi-tours.
    """
    if not prompt or not prompt.strip():
        return {"status": "error", "error": "empty_prompt"}
    if len(prompt) > MAX_PROMPT_CHARS:
        return {"status": "error", "error": "prompt_too_long",
                "detail": f"le prompt dépasse {MAX_PROMPT_CHARS} caractères"}

    async def work():
        # Durée/statut LLM mesurés par l'adaptateur OllamaLLM lui-même :
        # mesurer ici aussi compterait deux fois le même appel.
        result = await LlmChatUseCase(_gateway, _llm, _sanitizer).execute(
            prompt,
            system_prompt=system_prompt,
            context=context,
            max_tokens=max_tokens,
            history=history,
        )
        return {"reply": result["reply"], "model": _llm_model(),
                "context_length": result["context_length"]}

    return await _guarded("llm_chat", "superviseur", token, work, ctx=ctx,
                          params={"prompt": prompt, "context": context,
                                  "max_tokens": max_tokens})


# ─────────────────────────  pilotage (Admin) — HITL  ─────────────────────

@mcp.tool()
async def originate_call(
    endpoint: str,
    exten: str,
    ctx: Context,
    context: str | None = None,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Initie un appel vers `endpoint` puis le route vers context/exten. Validation humaine obligatoire."""
    async def work():
        data = await OriginateCallUseCase(_gateway, FastMcpHitlConfirmation(ctx), _sanitizer).execute(
            endpoint=endpoint, context=context or settings.asterisk_default_context,
            exten=exten, user=_username(token),
        )
        metrics.hitl_outcome("originate_call", "confirmed")
        return {"result": data}

    return await _guarded("originate_call", "admin", token, work, ctx=ctx, hitl_in_usecase=True,
                          params={"endpoint": endpoint, "exten": exten, "context": context})


@mcp.tool()
async def hangup_channel(
    channel_id: str, ctx: Context, token: AccessToken = CurrentAccessToken()
) -> dict:
    """Libère (raccroche) un canal. Validation humaine obligatoire."""
    async def work():
        data = await HangupChannelUseCase(_gateway, FastMcpHitlConfirmation(ctx), _sanitizer).execute(
            channel_id=channel_id, user=_username(token)
        )
        metrics.hitl_outcome("hangup_channel", "confirmed")
        return {"result": data}

    return await _guarded("hangup_channel", "admin", token, work, ctx=ctx, hitl_in_usecase=True,
                          params={"channel_id": channel_id})


@mcp.tool()
async def redirect_call(
    channel_id: str,
    destination: str,
    ctx: Context,
    context: str | None = None,
    attended: bool = False,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Transfère un canal vers une autre destination (aveugle par défaut). Validation humaine obligatoire."""
    async def work():
        data = await TransferCallUseCase(_gateway, FastMcpHitlConfirmation(ctx), _sanitizer).execute(
            channel_id=channel_id, destination=destination,
            context=context or settings.asterisk_default_context,
            user=_username(token), attended=attended,
        )
        metrics.hitl_outcome("redirect_call", "confirmed")
        return {"result": data}

    return await _guarded("redirect_call", "admin", token, work, ctx=ctx, hitl_in_usecase=True,
                          params={"channel_id": channel_id, "destination": destination,
                                  "context": context, "attended": attended})


@mcp.tool()
async def spy_channel(
    target_channel: str,
    supervisor_endpoint: str,
    ctx: Context,
    mode: str = "listen",
    acknowledge_legal: bool = False,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Écoute discrète d'un appel en cours (ChanSpy).

    ⚠️ ENCADREMENT LÉGAL : l'écoute et l'enregistrement de communications sont
    réglementés. `acknowledge_legal=true` est requis pour confirmer que vous
    disposez d'une base légale et avez informé les personnes concernées.

    mode = "listen" (Superviseur) | "whisper" | "barge" (Admin — audio injecté,
    validation humaine obligatoire).
    """
    actor = _username(token)
    try:
        spy_mode = SpyMode(mode.lower())
    except ValueError:
        return {"status": "error", "error": "bad_mode", "detail": f"mode invalide: {mode}"}

    required = "admin" if spy_mode in (SpyMode.WHISPER, SpyMode.BARGE) else "superviseur"
    request_id = _request_id(ctx)
    client_id = getattr(token, "client_id", "") or ""

    def legal_gate() -> dict | None:
        """Verrou légal — exige `acknowledge_legal` quel que soit le mode.

        Exécuté par `_guarded` **après** le RBAC : un acteur sans le droit
        d'écouter n'est jamais invité à attester d'une base légale, et le
        journal distingue nettement « pas de droits » de « pas de base légale ».
        """
        if acknowledge_legal:
            return None
        metrics.legal_denied("spy_channel", spy_mode.value)
        return {
            "_event": "spy_refused_legal",
            "status": "error",
            "error": "legal_acknowledgement_required",
            "detail": "acknowledge_legal manquant",
            "legal_notice": SPY_LEGAL_NOTICE,
        }

    async def work():
        data = await StartChannelSpyUseCase(_gateway, FastMcpHitlConfirmation(ctx), _sanitizer).execute(
            target_channel=target_channel, supervisor_endpoint=supervisor_endpoint,
            mode=spy_mode, user=actor,
        )
        # Preuve d'une écoute réelle : corrélée au client MCP et à la
        # requête, avec l'attestation légale de l'opérateur (responsabilité).
        audit_log("channel_spy", actor=actor, tool="spy_channel", outcome="success",
                  client_id=client_id, request_id=request_id,
                  params={"target_channel": target_channel, "supervisor": supervisor_endpoint,
                          "mode": spy_mode.value, "acknowledge_legal": acknowledge_legal})
        if spy_mode in (SpyMode.WHISPER, SpyMode.BARGE):
            metrics.hitl_outcome("spy_channel", "confirmed")
        return {"spy": data, "legal_notice": SPY_LEGAL_NOTICE}

    return await _guarded("spy_channel", required, token, work, ctx=ctx,
                          hitl_in_usecase=(spy_mode in (SpyMode.WHISPER, SpyMode.BARGE)),
                          pre_check=legal_gate,
                          params={"target_channel": target_channel, "mode": spy_mode.value,
                                  "acknowledge_legal": acknowledge_legal})
