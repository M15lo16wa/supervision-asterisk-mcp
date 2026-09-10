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
from src.application.analyze_call_quality import AnalyzeCallQualityUseCase
from src.application.get_call_records import GetCallRecordsUseCase
from src.application.get_channel_info import GetChannelInfoUseCase
from src.application.get_queue_stats import GetQueueStatsUseCase
from src.application.get_trunk_utilization import GetTrunkUtilizationUseCase
from src.application.hangup_channel import HangupChannelUseCase
from src.application.list_active_channels import ListActiveChannelsUseCase
from src.application.list_extensions import ListExtensionsUseCase
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
    UnauthorizedAction,
)
from src.observability import metrics
from src.security.auth import build_auth_provider, get_security_manager
from src.security.sanitizer import DataSanitizerImpl

logger = logging.getLogger(__name__)

SPY_LEGAL_NOTICE = (
    "AVERTISSEMENT LÉGAL — L'écoute (ChanSpy) et l'enregistrement de communications "
    "sont strictement encadrés. Assurez-vous d'une base légale, de l'information "
    "préalable des personnes concernées et, le cas échéant, de leur consentement, "
    "conformément au RGPD et au droit local des télécommunications. Toute écoute "
    "est tracée dans le journal d'audit."
)

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


@mcp.custom_route("/metrics", methods=["GET"])
async def prometheus_metrics(_request: Request) -> Response:
    body, content_type = metrics.render()
    return Response(content=body, media_type=content_type)


def _username(token: AccessToken) -> str:
    return (token.claims or {}).get("preferred_username") or token.client_id or "unknown"


def _request_id(ctx: Context | None) -> str | None:
    return getattr(ctx, "request_id", None) if ctx else None


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
) -> dict:
    """RBAC + consentement + exécution + mapping d'erreur + audit + métriques."""
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

    if not acknowledge_legal:
        audit_log("spy_refused_legal", actor=actor, tool="spy_channel", outcome="cancelled",
                  params={"target_channel": target_channel, "mode": mode},
                  detail="acknowledge_legal manquant")
        return {"status": "error", "error": "legal_acknowledgement_required",
                "legal_notice": SPY_LEGAL_NOTICE}

    required = "admin" if spy_mode in (SpyMode.WHISPER, SpyMode.BARGE) else "superviseur"

    async def work():
        data = await StartChannelSpyUseCase(_gateway, FastMcpHitlConfirmation(ctx), _sanitizer).execute(
            target_channel=target_channel, supervisor_endpoint=supervisor_endpoint,
            mode=spy_mode, user=actor,
        )
        audit_log("channel_spy", actor=actor, tool="spy_channel", outcome="success",
                  params={"target_channel": target_channel, "supervisor": supervisor_endpoint,
                          "mode": spy_mode.value})
        if spy_mode in (SpyMode.WHISPER, SpyMode.BARGE):
            metrics.hitl_outcome("spy_channel", "confirmed")
        return {"spy": data, "legal_notice": SPY_LEGAL_NOTICE}

    return await _guarded("spy_channel", required, token, work, ctx=ctx,
                          hitl_in_usecase=(spy_mode in (SpyMode.WHISPER, SpyMode.BARGE)),
                          params={"target_channel": target_channel, "mode": spy_mode.value})
