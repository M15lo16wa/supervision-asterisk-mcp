# src/observability/http.py
"""Serveur HTTP minimal exposant /metrics (utilisé par le pipeline vocal, qui
n'a pas de serveur web propre — le serveur MCP, lui, ajoute la route à son ASGI)."""
from __future__ import annotations

import logging

from aiohttp import web

from src.observability import metrics

logger = logging.getLogger(__name__)


async def start_metrics_server(host: str = "0.0.0.0", port: int = 9092) -> web.AppRunner:
    async def handler(_request: web.Request) -> web.Response:
        body, content_type = metrics.render()
        return web.Response(body=body, content_type=content_type.split(";")[0])

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app = web.Application()
    app.add_routes([web.get("/metrics", handler), web.get("/health", health)])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    logger.info("metrics exposées sur http://%s:%d/metrics", host, port)
    return runner
