# src/adapters/asterisk_gateway.py
"""Asterisk gateway over AMI, implemented with Panoramisk.

Covers the Module 2 tooling:
  * lecture   : list_channels, list_extensions, get_recent_cdr
  * analyse   : get_channel_quality (PJSIPShowChannelStats -> MOS estimate)
  * pilotage  : originate, hangup, transfer (Redirect/Atxfer), start_spy (ChanSpy)

CDR history: cdr_manager emits ``Cdr`` events in real time; we keep a bounded
ring buffer of the most recent ones (there is no "list past CDRs" AMI action).
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque

from panoramisk import Manager

from src.domain.entities import (
    CallDetailRecord,
    CallQuality,
    Channel,
    ChannelState,
    Extension,
    ExtensionState,
    HangupResult,
    OriginateResult,
    SpyMode,
    SpyResult,
    TransferResult,
)
from src.domain.exceptions import (
    AsteriskCommandError,
    AsteriskConnectionError,
    ChannelNotFound,
)
from src.domain.ports import AsteriskGateway

logger = logging.getLogger(__name__)

_SPY_OPTIONS = {SpyMode.LISTEN: "q", SpyMode.WHISPER: "qw", SpyMode.BARGE: "qB"}


def _g(msg, key: str, default: str = "") -> str:
    """Case-insensitive header access on a panoramisk Message or a plain dict."""
    try:
        value = msg.get(key, default)
    except AttributeError:
        value = default
    return "" if value is None else str(value)


class PanoramiskGateway(AsteriskGateway):
    """Concrete AMI gateway."""

    CDR_BUFFER_SIZE = 500
    ACTION_TIMEOUT = 8.0  # s — une action AMI qui n'émet pas l'event de fin ne doit pas bloquer

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5038,
        username: str = "mcp_ami",
        secret: str = "mcp_ami",
        default_context: str = "from-internal",
    ):
        self.host = host
        self.port = int(port)
        self.username = username
        self.secret = secret
        self.default_context = default_context
        self._manager: Manager | None = None
        self._cdr: deque[CallDetailRecord] = deque(maxlen=self.CDR_BUFFER_SIZE)
        self._connect_lock = asyncio.Lock()

    # ------------------------------------------------------------------ lifecycle
    async def _connect(self) -> Manager:
        if self._manager is not None:
            return self._manager
        async with self._connect_lock:
            if self._manager is not None:
                return self._manager
            try:
                manager = Manager(
                    host=self.host,
                    port=self.port,
                    username=self.username,
                    secret=self.secret,
                )
                manager.register_event("Cdr", self._on_cdr_event)
                await manager.connect()
                auth_future = getattr(manager, "authenticated_future", None)
                if auth_future is not None:
                    await auth_future
                self._manager = manager
                return manager
            except Exception as e:
                logger.error("AMI connection failed: %s", e)
                raise AsteriskConnectionError(f"AMI connection failed: {e}") from e

    async def close(self) -> None:
        if self._manager is not None:
            try:
                self._manager.close()
            finally:
                self._manager = None

    async def _send(self, action: dict, *, as_list: bool = False):
        manager = await self._connect()
        try:
            return await asyncio.wait_for(
                manager.send_action(action, as_list=as_list), timeout=self.ACTION_TIMEOUT
            )
        except asyncio.TimeoutError as e:
            raise AsteriskCommandError(
                f"AMI action {action.get('Action')} timed out after {self.ACTION_TIMEOUT}s "
                f"(l'événement de fin n'a pas été reçu)"
            ) from e
        except Exception as e:
            raise AsteriskConnectionError(f"AMI action {action.get('Action')} failed: {e}") from e

    @staticmethod
    def _raise_if_error(response, context: str) -> None:
        if response is None:
            return
        if not getattr(response, "success", True):
            message = _g(response, "Message") or _g(response, "Response")
            if "no such channel" in message.lower():
                raise ChannelNotFound(message)
            raise AsteriskCommandError(f"{context}: {message or 'rejected by Asterisk'}")

    # -------------------------------------------------------------------- lecture
    async def list_channels(self) -> list[Channel]:
        messages = await self._send({"Action": "CoreShowChannels"}, as_list=True)
        channels: list[Channel] = []
        for msg in messages or []:
            if _g(msg, "Event") != "CoreShowChannel":
                continue
            try:
                channels.append(self._to_channel(msg))
            except Exception as e:
                logger.warning("skipping unparseable channel event: %s", e)
        return channels

    async def list_extensions(self, context: str | None = None) -> list[Extension]:
        messages = await self._send({"Action": "ExtensionStateList"}, as_list=True)
        extensions: list[Extension] = []
        for msg in messages or []:
            if _g(msg, "Event") != "ExtensionStatus":
                continue
            ctx = _g(msg, "Context")
            if context and ctx != context:
                continue
            extensions.append(
                Extension(
                    extension=_g(msg, "Exten"),
                    context=ctx,
                    state=ExtensionState.from_status_code(_g(msg, "Status", "-1")),
                    status_text=_g(msg, "StatusText"),
                    hint=_g(msg, "Hint"),
                )
            )
        return extensions

    async def get_recent_cdr(self, limit: int = 20) -> list[CallDetailRecord]:
        await self._connect()  # ensure the Cdr handler is registered
        records = list(self._cdr)[-limit:]
        records.reverse()
        return records

    def _on_cdr_event(self, manager, message) -> None:
        try:
            self._cdr.append(
                CallDetailRecord(
                    uniqueid=_g(message, "UniqueID"),
                    source=_g(message, "Source"),
                    destination=_g(message, "Destination"),
                    destination_context=_g(message, "DestinationContext"),
                    caller_id=_g(message, "CallerID"),
                    channel=_g(message, "Channel"),
                    destination_channel=_g(message, "DestinationChannel"),
                    start_time=_g(message, "StartTime"),
                    answer_time=_g(message, "AnswerTime"),
                    end_time=_g(message, "EndTime"),
                    duration_seconds=int(_g(message, "Duration", "0") or 0),
                    billable_seconds=int(_g(message, "BillableSeconds", "0") or 0),
                    disposition=_g(message, "Disposition"),
                    last_application=_g(message, "LastApplication"),
                )
            )
        except Exception as e:
            logger.warning("could not buffer Cdr event: %s", e)

    # -------------------------------------------------------------------- analyse
    async def get_channel_quality(self, channel_id: str) -> CallQuality:
        stats = None
        try:
            messages = await self._send(
                {"Action": "PJSIPShowChannelStats", "Channel": channel_id}, as_list=True
            )
            for msg in messages or []:
                if _g(msg, "Event") in ("ChannelStats", "PJSIPShowChannelStats"):
                    stats = msg
                    break
        except AsteriskCommandError:
            stats = None  # canal non-PJSIP ou action indisponible -> repli

        if stats is None:
            # Repli : variable RTP QoS posée par Asterisk (RTPAUDIOQOS / RTPQOS).
            for variable in ("RTPAUDIOQOS", "RTPAUDIOQOSBRIDGED"):
                try:
                    var = await self._send(
                        {"Action": "Getvar", "Channel": channel_id, "Variable": variable}
                    )
                except AsteriskCommandError:
                    continue
                self._raise_if_error(var, "get_channel_quality")
                raw = _g(var, "Value")
                if raw:
                    return self._quality_from_rtpqos(channel_id, raw)
            raise AsteriskCommandError(
                f"get_channel_quality: aucune statistique RTCP disponible pour {channel_id}"
            )

        def num(key: str) -> float:
            try:
                return float(_g(stats, key, "0") or 0)
            except ValueError:
                return 0.0

        return CallQuality.from_rtcp(
            channel=channel_id,
            ssrc=_g(stats, "RxSsrc") or _g(stats, "Ssrc"),
            rtt_ms=num("Rtt") * (1000.0 if num("Rtt") < 10 else 1.0),
            jitter_ms=num("RxJitter") * (1000.0 if num("RxJitter") < 10 else 1.0),
            packets_lost=int(num("RxLoss") or num("RxPacketsLost")),
            packets_received=int(num("RxCount") or num("RxPackets")),
        )

    @staticmethod
    def _quality_from_rtpqos(channel_id: str, raw: str) -> CallQuality:
        # Format: "ssrc=.. themssrc=.. lp=.. rxjitter=.. rxcount=.. txcount=.. rlp=.. rtt=.."
        fields = dict(
            part.split("=", 1) for part in raw.replace(";", " ").split() if "=" in part
        )

        def f(key: str) -> float:
            try:
                return float(fields.get(key, "0"))
            except ValueError:
                return 0.0

        return CallQuality.from_rtcp(
            channel=channel_id,
            ssrc=fields.get("ssrc", ""),
            rtt_ms=f("rtt") * 1000.0,
            jitter_ms=f("rxjitter") * 1000.0,
            packets_lost=int(f("lp") or f("rlp")),
            packets_received=int(f("rxcount")),
        )

    # ------------------------------------------------------------------- pilotage
    async def originate(self, endpoint: str, context: str, exten: str) -> OriginateResult:
        response = await self._send(
            {
                "Action": "Originate",
                "Channel": endpoint,
                "Context": context or self.default_context,
                "Exten": exten,
                "Priority": "1",
                "CallerID": f"MCP Supervision <{exten}>",
                "Async": "true",
                "Timeout": "30000",
            }
        )
        self._raise_if_error(response, "originate")
        return OriginateResult(
            channel_id=_g(response, "Channel") or _g(response, "Uniqueid") or "pending",
            channel_name=endpoint,
            status="success" if getattr(response, "success", False) else _g(response, "Message"),
        )

    async def hangup(self, channel_id: str) -> HangupResult:
        response = await self._send(
            {"Action": "Hangup", "Channel": channel_id, "Cause": "16"}
        )
        self._raise_if_error(response, "hangup")
        return HangupResult(
            channel_id=channel_id,
            status="success" if getattr(response, "success", False) else _g(response, "Message"),
        )

    async def transfer(
        self,
        channel_id: str,
        destination: str,
        context: str,
        attended: bool = False,
    ) -> TransferResult:
        context = context or self.default_context
        if attended:
            action = {
                "Action": "Atxfer",
                "Channel": channel_id,
                "Exten": destination,
                "Context": context,
                "Priority": "1",
            }
        else:
            action = {
                "Action": "Redirect",
                "Channel": channel_id,
                "Exten": destination,
                "Context": context,
                "Priority": "1",
            }
        response = await self._send(action)
        self._raise_if_error(response, "transfer")
        return TransferResult(
            channel_id=channel_id,
            destination=destination,
            destination_context=context,
            attended=attended,
            status="success" if getattr(response, "success", False) else _g(response, "Message"),
        )

    async def start_spy(
        self,
        target_channel: str,
        supervisor_endpoint: str,
        mode: SpyMode = SpyMode.LISTEN,
    ) -> SpyResult:
        options = _SPY_OPTIONS[mode]
        response = await self._send(
            {
                "Action": "Originate",
                "Channel": supervisor_endpoint,
                "Application": "ChanSpy",
                "Data": f"{target_channel},{options}",
                "CallerID": "MCP Supervision <spy>",
                "Async": "true",
                "Timeout": "30000",
            }
        )
        self._raise_if_error(response, "start_spy")
        return SpyResult(
            target_channel=target_channel,
            spy_channel_id=_g(response, "Channel") or "pending",
            supervisor_endpoint=supervisor_endpoint,
            mode=mode,
            status="success" if getattr(response, "success", False) else _g(response, "Message"),
        )

    # -------------------------------------------------------------------- helpers
    @staticmethod
    def _to_channel(msg) -> Channel:
        duration = _g(msg, "Duration")
        seconds: int | None = None
        if duration and ":" in duration:
            parts = [int(p) for p in duration.split(":")]
            while len(parts) < 3:
                parts.insert(0, 0)
            seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
        return Channel(
            id=_g(msg, "Uniqueid") or _g(msg, "Channel"),
            name=_g(msg, "Channel"),
            state=ChannelState.from_asterisk(_g(msg, "ChannelStateDesc") or _g(msg, "ChannelState")),
            caller_id_num=_g(msg, "CallerIDNum"),
            caller_id_name=_g(msg, "CallerIDName"),
            connected_line_num=_g(msg, "ConnectedLineNum") or None,
            connected_line_name=_g(msg, "ConnectedLineName") or None,
            language=_g(msg, "Language") or "en",
            accountcode=_g(msg, "AccountCode"),
            context=_g(msg, "Context") or None,
            exten=_g(msg, "Exten") or None,
            application=_g(msg, "Application") or None,
            duration_seconds=seconds,
        )
