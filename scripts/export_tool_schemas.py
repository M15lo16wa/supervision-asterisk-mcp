#!/usr/bin/env python3
"""Exporte les schémas JSON des outils MCP (livrable A.7).

    python scripts/export_tool_schemas.py            # -> docs/tool-schemas.json
    python scripts/export_tool_schemas.py --stdout

N'a pas besoin de Keycloak ni d'Asterisk : instancie le serveur en mémoire et
lit sa table d'outils.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-server"))
os.environ.setdefault("MCP_AUTH_MODE", "static")


async def collect() -> dict:
    from src import __version__
    from src.interfaces.mcp_tools import mcp

    tools = await mcp.list_tools()
    entries = []
    for t in sorted(tools, key=lambda x: x.name):
        mcp_tool = t.to_mcp_tool()
        entries.append(
            {
                "name": t.name,
                "description": (t.description or "").strip(),
                "inputSchema": getattr(mcp_tool, "inputSchema", None) or t.parameters,
                "outputSchema": getattr(mcp_tool, "outputSchema", None) or t.output_schema,
                "annotations": getattr(t, "annotations", None)
                and t.annotations.model_dump(exclude_none=True),
            }
        )
    return {
        "server": mcp.name,
        "version": __version__,
        "protocol": "MCP / JSON-RPC 2.0",
        "generated_by": "scripts/export_tool_schemas.py",
        "tools": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--out", default="docs/tool-schemas.json")
    args = parser.parse_args()

    data = asyncio.run(collect())
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    if args.stdout:
        sys.stdout.write(text)
    else:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"{len(data['tools'])} schémas d'outils écrits dans {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
