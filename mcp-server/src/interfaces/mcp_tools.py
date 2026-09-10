# src/interfaces/mcp_tools.py
"""MCP tools exposed to clients.

Every tool:
  1. checks a role first        -> require_role(token, "...")
  2. (pilotage) confirms via HITL -> injected into the use case
  3. sanitises its output        -> DataSanitizer in the use case

Zones (matrice de droits) :
  lecture   operateur   : list_active_channels, list_extensions, get_call_records
  analyse   superviseur : analyze_call_quality, start_channel_spy(listen)
  pilotage  admin       : originate_call, hangup_channel, transfer_call,
                          start_channel_spy(whisper|barge)
"""
import logging

from fastmcp import Context, FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import CurrentAccessToken

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


def _username(token: AccessToken) -> str:
    return (token.claims or {}).get("preferred_username") or token.client_id or "unknown"


def _fail(exc: Exception) -> dict:
    """Map a domain exception to a structured, non-raising tool result."""
    if isinstance(exc, UnauthorizedAction):
        return {"status": "error", "error": "unauthorized", "detail": str(exc)}
    if isinstance(exc, HitlConfirmationDenied):
        return {"status": "cancelled", "reason": str(exc)}
    if isinstance(exc, ChannelNotFound):
        return {"status": "error", "error": "channel_not_found", "detail": str(exc)}
    if isinstance(exc, (AsteriskConnectionError, AsteriskCommandError)):
        return {"status": "error", "error": "asterisk_unavailable", "detail": str(exc)}
    logger.exception("unexpected tool error")
    return {"status": "error", "error": "internal", "detail": str(exc)}


# ───────────────────────────  lecture (operateur+)  ───────────────────────────

@mcp.tool()
async def list_active_channels(token: AccessToken = CurrentAccessToken()) -> dict:
    """Liste les canaux Asterisk actifs (état, appelant, contexte, durée)."""
    try:
        _security.require_role(token, "operateur")
        data = await ListActiveChannelsUseCase(_gateway, _sanitizer).execute()
        return {"status": "success", "channels": data}
    except Exception as e:
        return _fail(e)


@mcp.tool()
async def list_extensions(
    context: str | None = None,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Liste les extensions du dialplan et l'état des terminaux (hints)."""
    try:
        _security.require_role(token, "operateur")
        data = await ListExtensionsUseCase(_gateway, _sanitizer).execute(context=context)
        return {"status": "success", "extensions": data}
    except Exception as e:
        return _fail(e)


@mcp.tool()
async def get_call_records(
    limit: int = 20,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Retourne les CDR récents (journal temps réel bufferisé par le serveur)."""
    try:
        _security.require_role(token, "operateur")
        data = await GetCallRecordsUseCase(_gateway, _sanitizer).execute(limit=limit)
        return {"status": "success", "records": data}
    except Exception as e:
        return _fail(e)


# ──────────────────────────  analyse (superviseur+)  ──────────────────────────

@mcp.tool()
async def analyze_call_quality(
    channel_id: str,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Analyse la qualité RTP/RTCP d'un canal (jitter, perte, RTT, MOS estimé)."""
    try:
        _security.require_role(token, "superviseur")
        data = await AnalyzeCallQualityUseCase(_gateway, _sanitizer).execute(channel_id)
        return {"status": "success", "quality": data}
    except Exception as e:
        return _fail(e)


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
        try:
            spy_mode = SpyMode(mode.lower())
        except ValueError:
            return {"status": "error", "error": "bad_mode", "detail": f"mode invalide: {mode}"}

        required = "admin" if spy_mode in (SpyMode.WHISPER, SpyMode.BARGE) else "superviseur"
        _security.require_role(token, required)

        hitl = FastMcpHitlConfirmation(ctx)
        use_case = StartChannelSpyUseCase(_gateway, hitl, _sanitizer)
        data = await use_case.execute(
            target_channel=target_channel,
            supervisor_endpoint=supervisor_endpoint,
            mode=spy_mode,
            user=_username(token),
        )
        return {"status": "success", "spy": data}
    except Exception as e:
        return _fail(e)


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
    try:
        _security.require_role(token, "admin")
        hitl = FastMcpHitlConfirmation(ctx)
        use_case = OriginateCallUseCase(_gateway, hitl, _sanitizer)
        data = await use_case.execute(
            endpoint=endpoint,
            context=context or settings.asterisk_default_context,
            exten=exten,
            user=_username(token),
        )
        return {"status": "success", "result": data}
    except Exception as e:
        return _fail(e)


@mcp.tool()
async def hangup_channel(
    channel_id: str,
    ctx: Context,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Raccroche un canal. HITL obligatoire."""
    try:
        _security.require_role(token, "admin")
        hitl = FastMcpHitlConfirmation(ctx)
        data = await HangupChannelUseCase(_gateway, hitl, _sanitizer).execute(
            channel_id=channel_id, user=_username(token)
        )
        return {"status": "success", "result": data}
    except Exception as e:
        return _fail(e)


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
    try:
        _security.require_role(token, "admin")
        hitl = FastMcpHitlConfirmation(ctx)
        data = await TransferCallUseCase(_gateway, hitl, _sanitizer).execute(
            channel_id=channel_id,
            destination=destination,
            context=context or settings.asterisk_default_context,
            user=_username(token),
            attended=attended,
        )
        return {"status": "success", "result": data}
    except Exception as e:
        return _fail(e)
