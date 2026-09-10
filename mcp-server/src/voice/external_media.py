# src/voice/external_media.py
"""ARI External Media: bridge a live channel to this process over RTP (slin16).

    caller ──┐
             ├─ mixing bridge ──┬─ External Media channel ── RTP/UDP ──▶ this process
   (Stasis) ─┘                  └─ (TTS audio sent back on the same RTP flow)

The RTP payload for slin16 is raw PCM16 big-endian at 16 kHz; frames are 20 ms
(320 samples / 640 bytes). We learn Asterisk's source port from the first
inbound packet and send synthesised audio back to it.
"""
from __future__ import annotations

import asyncio
import logging
import random
import struct
from collections.abc import AsyncIterator

import aiohttp

from src.voice.config import VoiceSettings

logger = logging.getLogger(__name__)

_RTP_VERSION = 0x80
_PT_SLIN16 = 118            # dynamic payload type advertised for slin16


class RtpEndpoint(asyncio.DatagramProtocol):
    """Bidirectional RTP endpoint for one call."""

    def __init__(self, sample_rate: int, frame_ms: int):
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.frame_samples = int(sample_rate * frame_ms / 1000)
        self.frame_bytes = self.frame_samples * 2
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
        self._peer = None
        self._transport = None
        self._seq = random.randint(0, 0xFFFF)
        self._ts = random.randint(0, 0xFFFFFFFF)
        self._ssrc = random.randint(0, 0xFFFFFFFF)
        self._closed = asyncio.Event()

    # asyncio protocol ----------------------------------------------------------
    def connection_made(self, transport):
        self._transport = transport

    def datagram_received(self, data: bytes, addr):
        if self._peer is None:
            self._peer = addr
            logger.info("RTP peer learned: %s", addr)
        if len(data) <= 12:
            return
        payload = data[12:]  # skip fixed RTP header (no CSRC/extension expected)
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.debug("inbound RTP queue full, dropping frame")

    def error_received(self, exc):  # pragma: no cover
        logger.warning("RTP socket error: %s", exc)

    def connection_lost(self, exc):  # pragma: no cover
        self._closed.set()

    # public API --------------------------------------------------------------
    async def frames(self) -> AsyncIterator[bytes]:
        while not self._closed.is_set():
            try:
                yield await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

    async def send_pcm(self, pcm16: bytes) -> None:
        """Packetise PCM16 into 20 ms RTP frames and pace them to the peer."""
        if self._peer is None or self._transport is None:
            return
        for i in range(0, len(pcm16), self.frame_bytes):
            frame = pcm16[i : i + self.frame_bytes]
            if len(frame) < self.frame_bytes:
                frame = frame + b"\x00" * (self.frame_bytes - len(frame))
            header = struct.pack(
                "!BBHII", _RTP_VERSION, _PT_SLIN16, self._seq & 0xFFFF,
                self._ts & 0xFFFFFFFF, self._ssrc,
            )
            self._transport.sendto(header + frame, self._peer)
            self._seq += 1
            self._ts += self.frame_samples
            await asyncio.sleep(self.frame_ms / 1000)

    def close(self) -> None:
        self._closed.set()
        if self._transport is not None:
            self._transport.close()


class AriClient:
    """Thin async ARI REST + websocket client (only what the pipeline needs)."""

    def __init__(self, settings: VoiceSettings):
        self._s = settings
        self._auth = aiohttp.BasicAuth(settings.ari_user, settings.ari_password)
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> AriClient:
        self._session = aiohttp.ClientSession(auth=self._auth, base_url=self._s.ari_base_url)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._session:
            await self._session.close()

    async def _post(self, path: str, params: dict) -> dict:
        assert self._session is not None
        async with self._session.post(f"/ari{path}", params=params) as r:
            r.raise_for_status()
            return await r.json() if r.content_type == "application/json" else {}

    async def _delete(self, path: str) -> None:
        assert self._session is not None
        async with self._session.delete(f"/ari{path}") as r:
            if r.status not in (200, 204, 404):
                r.raise_for_status()

    async def create_bridge(self) -> str:
        data = await self._post("/bridges", {"type": "mixing", "name": "mcp-voice"})
        return data["id"]

    async def create_external_media(self, bridge_id: str, local_addr: str) -> str:
        data = await self._post(
            "/channels/externalMedia",
            {
                "app": self._s.ari_app,
                "external_host": local_addr,
                "format": "slin16",
                "encapsulation": "rtp",
                "transport": "udp",
                "connection_type": "client",
                "direction": "both",
            },
        )
        channel_id = data["id"]
        await self._post(f"/bridges/{bridge_id}/addChannel", {"channel": channel_id})
        return channel_id

    async def add_channel(self, bridge_id: str, channel_id: str) -> None:
        await self._post(f"/bridges/{bridge_id}/addChannel", {"channel": channel_id})

    async def answer(self, channel_id: str) -> None:
        await self._post(f"/channels/{channel_id}/answer", {})

    async def hangup(self, channel_id: str) -> None:
        await self._delete(f"/channels/{channel_id}")

    async def destroy_bridge(self, bridge_id: str) -> None:
        await self._delete(f"/bridges/{bridge_id}")

    def events_url(self) -> str:
        base = self._s.ari_base_url.replace("http", "ws", 1)
        return f"{base}/ari/events?app={self._s.ari_app}&api_key={self._s.ari_user}:{self._s.ari_password}"

    async def events(self) -> AsyncIterator[dict]:
        assert self._session is not None
        async with self._session.ws_connect(self.events_url()) as ws:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    yield msg.json()
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break


async def open_rtp_endpoint(settings: VoiceSettings) -> tuple[RtpEndpoint, int]:
    """Bind the local RTP socket; returns (endpoint, bound_port)."""
    loop = asyncio.get_running_loop()
    endpoint = RtpEndpoint(settings.sample_rate, settings.frame_ms)
    transport, _ = await loop.create_datagram_endpoint(
        lambda: endpoint, local_addr=(settings.rtp_host, settings.rtp_port)
    )
    port = transport.get_extra_info("sockname")[1]
    return endpoint, port
