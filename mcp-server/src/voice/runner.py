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

from src.observability import metrics
from src.observability.http import start_metrics_server
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


async def _handle_call(ari: AriClient, channel_id: str, stt, llm, tts) -> None:
    logger.info("call %s entered Stasis", channel_id)
    endpoint, port = await open_rtp_endpoint(voice_settings)
    bridge_id = None
    extmedia_id = None
    metrics.VOICE_ACTIVE_CALLS.inc()
    try:
        await ari.answer(channel_id)
        bridge_id = await ari.create_bridge()
        # Le canal Stasis entre dans le pont AVANT la création du UnicastRTP :
        # Asterisk peut refuser un canal pas encore attaché ("not in Stasis
        # application"), et l'ordre inverse augmente cette occurrence.
        await ari.add_channel(bridge_id, channel_id)
        # Asterisk connects back to us on this address:
        local_addr = f"{voice_settings.rtp_host if voice_settings.rtp_host != '0.0.0.0' else _guess_local_ip()}:{port}"
        extmedia_id = await ari.create_external_media(bridge_id, local_addr)

        # stt/llm/tts sont partagés par tous les appels : le modèle Whisper/Piper
        # n'est donc chargé qu'une fois. L'historique, lui, reste par appel.
        pipeline = S2SPipeline(
            stt=stt,
            llm=llm,
            tts=tts,
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
        metrics.VOICE_ACTIVE_CALLS.dec()
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


async def _warm_up_llm(llm) -> None:
    """Précharge le modèle LLM en tâche de fond (non bloquant pour l'ARI)."""
    warm = getattr(llm, "warm_up", None)
    if warm is None:
        return
    try:
        elapsed = await asyncio.wait_for(warm(), timeout=voice_settings.llm_timeout_s)
        logger.info("LLM prêt en %.1f s (%s)", elapsed, voice_settings.ollama_model)
    except asyncio.TimeoutError:
        logger.warning("préchauffage LLM dépassé après %ds", voice_settings.llm_timeout_s)
    except Exception as e:  # le service reste utilisable, seul le 1er tour sera lent
        logger.warning("préchauffage LLM impossible : %s", e)


async def main() -> None:
    logger.info(
        "voice pipeline starting — app=%s ari=%s ollama=%s budget=%dms",
        voice_settings.ari_app, voice_settings.ari_base_url,
        voice_settings.ollama_base_url, voice_settings.latency_budget_ms,
    )
    await start_metrics_server(port=int(os.getenv("VOICE_METRICS_PORT", "9092")))
    # Construit une seule fois : un modèle manquant doit faire échouer le
    # démarrage, pas chaque appel.
    stt = build_stt(voice_settings)
    tts = build_tts(voice_settings)
    llm = build_llm(voice_settings)
    logger.info("voice components ready (stt=%s tts=%s)", type(stt).__name__, type(tts).__name__)
    # Le modèle LLM vit en RAM : on le charge tout de suite, sinon le premier
    # appel d'un client attend plusieurs minutes.
    warmup = asyncio.create_task(_warm_up_llm(llm))
    warmup.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    async with AriClient(voice_settings) as ari:
        calls: dict[str, asyncio.Task] = {}

        async def shutdown(channel_id: str, reason: str) -> None:
            """Annule le pipeline d'un canal et attend sa libération."""
            task = calls.pop(channel_id, None)
            if task is None or task.done():
                return
            logger.info("cancelling pipeline %s (%s)", channel_id, reason)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

        async for event in ari.events():
            etype = event.get("type")
            if etype == "StasisStart":
                channel = event["channel"]["id"]
                # ignore the External Media channel we create ourselves
                if event["channel"].get("name", "").startswith("UnicastRTP"):
                    continue
                if channel in calls:
                    continue
                task = asyncio.create_task(_handle_call(ari, channel, stt, llm, tts))
                calls[channel] = task
                task.add_done_callback(lambda t, c=channel: calls.pop(c, None))
            elif etype == "StasisEnd":
                channel_id = event["channel"]["id"]
                if channel_id in calls:
                    await shutdown(channel_id, "StasisEnd")
                else:
                    logger.info("channel %s left Stasis", channel_id)

        await asyncio.gather(*calls.values(), return_exceptions=True)


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
