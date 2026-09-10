#!/usr/bin/env python3
"""Test d'intégration réel contre un Asterisk vivant (AMI via Panoramisk).

    python scripts/live_test_asterisk.py

Env (défauts) : ASTERISK_HOST=127.0.0.1 ASTERISK_AMI_PORT=5038
                ASTERISK_AMI_USER=mcp_ami ASTERISK_AMI_SECRET=changeme_ami

N'appelle PAS le serveur MCP : instancie directement PanoramiskGateway pour
prouver que la couche adapters parle à un vrai Asterisk (login AMI,
CoreShowChannels, ExtensionStateList, Originate, événements Cdr).
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp-server"))

from src.adapters.asterisk_gateway import PanoramiskGateway  # noqa: E402
from src.domain.entities import SpyMode  # noqa: E402


async def main() -> int:
    gw = PanoramiskGateway(
        host=os.getenv("ASTERISK_HOST", "127.0.0.1"),
        port=int(os.getenv("ASTERISK_AMI_PORT", "5038")),
        username=os.getenv("ASTERISK_AMI_USER", "mcp_ami"),
        secret=os.getenv("ASTERISK_AMI_SECRET", "changeme_ami"),
        default_context=os.getenv("ASTERISK_DEFAULT_CONTEXT", "mcp-internal"),
    )
    ok = 0
    fail = 0

    async def step(name, coro):
        nonlocal ok, fail
        try:
            res = await coro
            print(f"  [OK] {name}: {res!r}")
            ok += 1
            return res
        except Exception as e:  # noqa: BLE001
            print(f"  [!!] {name}: {type(e).__name__}: {e}")
            fail += 1
            return None

    print("== connexion AMI + inventaire ==")
    await step("list_channels", gw.list_channels())
    await step("list_extensions(mcp-internal)", gw.list_extensions(context="mcp-internal"))
    await step("get_recent_cdr", gw.get_recent_cdr(limit=5))

    print("== pilotage (peut échouer proprement si aucun poste enregistré) ==")
    res = await step("originate PJSIP/1001 -> 1002", gw.originate("PJSIP/1001", "mcp-internal", "1002"))
    if res is not None:
        await asyncio.sleep(1)
        await step("list_channels (après originate)", gw.list_channels())
    await step("transfer (canal bidon)", gw.transfer("PJSIP/0-0", "1002", "mcp-internal"))
    await step("start_spy listen", gw.start_spy("PJSIP/1001-0", "PJSIP/1002", SpyMode.LISTEN))

    await gw.close()
    print(f"\n== bilan : {ok} OK / {fail} en échec ==")
    # Le login AMI + les 3 lectures doivent passer ; le pilotage peut échouer
    # faute de postes SIP enregistrés (échec « propre », pas un crash adapter).
    return 0 if ok >= 3 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
