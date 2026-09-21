#!/usr/bin/env python3
"""Client de fumée pour le serveur MCP.

Se connecte avec un JWT Keycloak, liste les outils, appelle un outil de lecture,
puis un outil de pilotage (avec réponse automatique à la confirmation HITL).

    python scripts/get_token.sh admin_demo > /tmp/tok.json   # (bash)
    ./scripts/get_token.sh admin_demo > /tmp/tok.json
    python scripts/smoke_mcp.py --token-file /tmp/tok.json --confirm oui

Dépend de fastmcp (déjà dans les dépendances du serveur).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from fastmcp import Client
from fastmcp.client.auth import BearerAuth


async def elicitation_handler(message, response_type, params, ctx):  # noqa: ARG001
    answer = CONFIRM
    print(f"  [HITL] serveur: {message!r} -> réponse simulée: {answer}")
    return answer


CONFIRM = True


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000/mcp")
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--confirm", choices=["oui", "non"], default="non")
    parser.add_argument("--endpoint", default="PJSIP/1001")
    parser.add_argument("--exten", default="1002")
    parser.add_argument(
        "--context",
        default=os.getenv("ASTERISK_DEFAULT_CONTEXT", "mcp-internal"),
    )
    args = parser.parse_args()

    global CONFIRM
    CONFIRM = args.confirm == "oui"

    try:
        with open(args.token_file, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as e:
        parser.error(f"--token-file illisible ({args.token_file}) : {e}")
    if not raw.strip():
        parser.error(
            f"--token-file vide ({args.token_file}) : "
            "le jeton n'a pas été obtenu. Relancez "
            "'./scripts/get_token.sh admin_demo > /tmp/tok.json' et vérifiez "
            "qu'il affiche du JSON (realm 'asterisk' importé, Keycloak UP)."
        )
    try:
        token = json.loads(raw)["access_token"]
    except (json.JSONDecodeError, KeyError) as e:
        parser.error(
            f"--token-file invalide ({args.token_file}) : {e}. "
            "Contenu attendu : JSON Keycloak avec clé 'access_token'."
        )

    client = Client(args.url, auth=BearerAuth(token), elicitation_handler=elicitation_handler)
    async with client:
        tools = await client.list_tools()
        print("Outils disponibles :", ", ".join(t.name for t in tools))

        print("\n-> list_active_channels")
        print(json.dumps((await client.call_tool("list_active_channels", {})).data, indent=2, ensure_ascii=False))

        print("\n-> originate_call (HITL)")
        try:
            res = await client.call_tool(
                "originate_call",
                {"endpoint": args.endpoint, "exten": args.exten, "context": args.context},
            )
            print(json.dumps(res.data, indent=2, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            print("appel bloqué :", e)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
