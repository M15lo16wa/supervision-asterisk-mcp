# src/interfaces/mcp_tools.py
"""MCP tools exposed to clients.

Every tool:
  1. checks a role first        -> require_role(token, "...")
  2. (pilotage) confirms via HITL -> injected into the use case
  3. sanitises its output        -> DataSanitizer in the use case
  4. is instrumented for Prometheus (calls, latency, RBAC denials, HITL, errors)

Zones (matrice de droits) :
  lecture   operateur   : list_active_channels, list_extensions, get_call_records
  analyse   superviseur : analyze_call_quality, start_channel_spy(listen)
  pilotage  admin       : originate_call, hangup_channel, transfer_call,
                          start_channel_spy(whisper|barge)

Endpoint Prometheus : GET /metrics (non authentifié — à protéger au niveau réseau).
"""
import logging
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
from src.application.hangup_channel import HangupChannelUseCase
from src.application.list_active_channels import ListActiveChannelsUseCase
from src.application.list_extensions import ListExtensionsUseCase
from src.application.originate_call import OriginateCallUseCase
from src.application.start_channel_spy import StartChannelSpyUseCase
from src.application.transfer_call import TransferCallUseCase
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

# MCP server — Keycloak JWT auth enforced at the transport layer.
mcp = FastMCP(name="asterisk-mcp-supervision", auth=build_auth_provider())

# Singletons
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


async def _guarded(
    tool: str,
    required_role: str,
    token: AccessToken,
    work: Callable[[], Awaitable[dict]],
) -> dict:
    """RBAC + exécution + mapping d'erreur + métriques, factorisés pour tous les outils.

    ``work`` renvoie le corps métier (ex. ``{"channels": [...]}``) fusionné dans
    la réponse ``{"status": "success", ...}``.
    """
    with metrics.tool_timer(tool):
        try:
            _security.require_role(token, required_role)
        except UnauthorizedAction as e:
            metrics.rbac_denied(tool, required_role)
            metrics.tool_result(tool, "unauthorized")
            return {"status": "error", "error": "unauthorized", "detail": str(e)}

        try:
            payload = await work()
            metrics.tool_result(tool, "success")
            return {"status": "success", **payload}
        except HitlConfirmationDenied as e:
            metrics.hitl_outcome(tool, "denied")
            metrics.tool_result(tool, "cancelled")
            return {"status": "cancelled", "reason": str(e)}
        except ChannelNotFound as e:
            metrics.tool_result(tool, "channel_not_found")
            return {"status": "error", "error": "channel_not_found", "detail": str(e)}
        except (AsteriskConnectionError, AsteriskCommandError) as e:
            metrics.asterisk_error(tool, type(e).__name__)
            metrics.tool_result(tool, "asterisk_unavailable")
            return {"status": "error", "error": "asterisk_unavailable", "detail": str(e)}
        except Exception as e:
            logger.exception("unexpected error in %s", tool)
            metrics.tool_result(tool, "internal")
            return {"status": "error", "error": "internal", "detail": str(e)}


# ───────────────────────────  lecture (operateur+)  ───────────────────────────

@mcp.tool()
async def list_active_channels(token: AccessToken = CurrentAccessToken()) -> dict:
    """Liste les canaux Asterisk actifs (état, appelant, contexte, durée)."""
    async def work():
        data = await ListActiveChannelsUseCase(_gateway, _sanitizer).execute()
        metrics.observe_active_channels(len(data))
        return {"channels": data}

    return await _guarded("list_active_channels", "operateur", token, work)


@mcp.tool()
async def list_extensions(
    context: str | None = None,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Liste les extensions du dialplan et l'état des terminaux (hints)."""
    async def work():
        data = await ListExtensionsUseCase(_gateway, _sanitizer).execute(context=context)
        return {"extensions": data}

    return await _guarded("list_extensions", "operateur", token, work)


@mcp.tool()
async def get_call_records(
    limit: int = 20,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Retourne les CDR récents (journal temps réel bufferisé par le serveur)."""
    async def work():
        data = await GetCallRecordsUseCase(_gateway, _sanitizer).execute(limit=limit)
        return {"records": data}

    return await _guarded("get_call_records", "operateur", token, work)


# ──────────────────────────  analyse (superviseur+)  ──────────────────────────

@mcp.tool()
async def analyze_call_quality(
    channel_id: str,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Analyse la qualité RTP/RTCP d'un canal (jitter, perte, RTT, MOS estimé)."""
    async def work():
        data = await AnalyzeCallQualityUseCase(_gateway, _sanitizer).execute(channel_id)
        return {"quality": data}

    return await _guarded("analyze_call_quality", "superviseur", token, work)


@mcp.tool()
async def start_channel_spy(
    target_channel: str,
    supervisor_endpoint: str,
    ctx: Context,
    mode: str = "listen",
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Supervise un appel en cours.

    mode = "listen" (écoute discrète, superviseur) | "whisper" | "barge" (admin, HITL).
    """
    try:
        spy_mode = SpyMode(mode.lower())
    except ValueError:
        return {"status": "error", "error": "bad_mode", "detail": f"mode invalide: {mode}"}

    required = "admin" if spy_mode in (SpyMode.WHISPER, SpyMode.BARGE) else "superviseur"

    async def work():
        hitl = FastMcpHitlConfirmation(ctx)
        data = await StartChannelSpyUseCase(_gateway, hitl, _sanitizer).execute(
            target_channel=target_channel,
            supervisor_endpoint=supervisor_endpoint,
            mode=spy_mode,
            user=_username(token),
        )
        if spy_mode in (SpyMode.WHISPER, SpyMode.BARGE):
            metrics.hitl_outcome("start_channel_spy", "confirmed")
        return {"spy": data}

    return await _guarded("start_channel_spy", required, token, work)


# ────────────────────────────  pilotage (admin)  ─────────────────────────────

@mcp.tool()
async def originate_call(
    endpoint: str,
    exten: str,
    ctx: Context,
    context: str | None = None,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Établit un nouvel appel vers `endpoint` puis le route vers context/exten. HITL."""
    async def work():
        hitl = FastMcpHitlConfirmation(ctx)
        data = await OriginateCallUseCase(_gateway, hitl, _sanitizer).execute(
            endpoint=endpoint,
            context=context or settings.asterisk_default_context,
            exten=exten,
            user=_username(token),
        )
        metrics.hitl_outcome("originate_call", "confirmed")
        return {"result": data}

    return await _guarded("originate_call", "admin", token, work)


@mcp.tool()
async def hangup_channel(
    channel_id: str,
    ctx: Context,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Raccroche un canal. HITL obligatoire."""
    async def work():
        hitl = FastMcpHitlConfirmation(ctx)
        data = await HangupChannelUseCase(_gateway, hitl, _sanitizer).execute(
            channel_id=channel_id, user=_username(token)
        )
        metrics.hitl_outcome("hangup_channel", "confirmed")
        return {"result": data}

    return await _guarded("hangup_channel", "admin", token, work)


@mcp.tool()
async def transfer_call(
    channel_id: str,
    destination: str,
    ctx: Context,
    context: str | None = None,
    attended: bool = False,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Transfère un canal vers une autre destination (aveugle par défaut). HITL."""
    async def work():
        hitl = FastMcpHitlConfirmation(ctx)
        data = await TransferCallUseCase(_gateway, hitl, _sanitizer).execute(
            channel_id=channel_id,
            destination=destination,
            context=context or settings.asterisk_default_context,
            user=_username(token),
            attended=attended,
        )
        metrics.hitl_outcome("transfer_call", "confirmed")
        return {"result": data}

    return await _guarded("transfer_call", "admin", token, work)
