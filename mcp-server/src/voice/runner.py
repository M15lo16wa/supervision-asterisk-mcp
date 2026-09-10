# src/voice/runner.py
"""Stasis application entrypoint for the Speech-to-Speech pipeline.

    python -m src.voice.runner

Subscribes to the ARI Stasis app; for each channel entering the app it sets up
an External Media RTP flow and runs one :class:`S2SPipeline` per call.

Dialplan side (see asterisk/config/extensions.conf):

    exten => 700,1,Stasis(mcp-voice)
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from src.voice.config import voice_settings
from src.voice.external_media import AriClient, open_rtp_endpoint
from src.voice.llm import build_llm
from src.voice.pipeline import S2SPipeline
from src.voice.stt import build_stt
from src.voice.tts import build_tts

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("voice.runner")


async def _handle_call(ari: AriClient, channel_id: str) -> None:
    logger.info("call %s entered Stasis", channel_id)
    endpoint, port = await open_rtp_endpoint(voice_settings)
    bridge_id = None
    extmedia_id = None
    try:
        await ari.answer(channel_id)
        bridge_id = await ari.create_bridge()
        # Asterisk connects back to us on this address:
        local_addr = f"{voice_settings.rtp_host if voice_settings.rtp_host != '0.0.0.0' else _guess_local_ip()}:{port}"
        extmedia_id = await ari.create_external_media(bridge_id, local_addr)
        await ari.add_channel(bridge_id, channel_id)

        pipeline = S2SPipeline(
            stt=build_stt(voice_settings),
            llm=build_llm(voice_settings),
            tts=build_tts(voice_settings),
            settings=voice_settings,
        )
        await pipeline.run(
            frames=endpoint.frames(),
            sink=endpoint.send_pcm,
            channel_id=channel_id,
            on_turn=lambda t: logger.info("turn: %s", t.to_dict()),
        )
    except Exception:
        logger.exception("pipeline failed for %s", channel_id)
    finally:
        endpoint.close()
        if extmedia_id:
            with contextlib.suppress(Exception):
                await ari.hangup(extmedia_id)
        if bridge_id:
            with contextlib.suppress(Exception):
                await ari.destroy_bridge(bridge_id)
        logger.info("call %s cleaned up", channel_id)


def _guess_local_ip() -> str:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


async def main() -> None:
    logger.info(
        "voice pipeline starting — app=%s ari=%s ollama=%s budget=%dms",
        voice_settings.ari_app, voice_settings.ari_base_url,
        voice_settings.ollama_base_url, voice_settings.latency_budget_ms,
    )
    async with AriClient(voice_settings) as ari:
        tasks: set[asyncio.Task] = set()
        async for event in ari.events():
            etype = event.get("type")
            if etype == "StasisStart":
                channel = event["channel"]["id"]
                # ignore the External Media channel we create ourselves
                if event["channel"].get("name", "").startswith("UnicastRTP"):
                    continue
                task = asyncio.create_task(_handle_call(ari, channel))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            elif etype == "StasisEnd":
                logger.info("channel %s left Stasis", event["channel"]["id"])


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
