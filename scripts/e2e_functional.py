#!/usr/bin/env python3
"""Test fonctionnel de bout en bout du systeme de supervision Asterisk.

Parcours couvert : Keycloak (JWT/RBAC) -> serveur MCP (12 outils) -> Asterisk
(AMI/ARI, veritables canaux) -> Ollama (LLM local) -> securite (HITL, legal).

Prerequis : le compose racine, Asterisk et la stack monitoring sont demarres
(cf. README, section « Reproduction pas a pas »).

    python scripts/e2e_functional.py

Code de sortie : 0 si toutes les verifications passent, 1 sinon, 2 si aucun
jeton n'a pu etre obtenu (stack Keycloak absente).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
from fastmcp import Client
from fastmcp.client.auth import BearerAuth

if hasattr(sys.stdout, "reconfigure"):  # Windows : cp1252 casse les accents
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.getenv("ENV_FILE", ROOT / ".env"))


def env_value(key: str, default: str = "") -> str:
    """Valeur d'environnement, avec repli sur le .env racine."""
    if key in os.environ:
        return os.environ[key]
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() == key:
                return value.strip()
    return default


MCP_URL = os.getenv("MCP_URL", f"http://localhost:{env_value('MCP_PORT', '8000')}/mcp")
KC_PUBLIC = env_value("KEYCLOAK_PUBLIC_URL", "http://localhost:8080")
REALM = env_value("KEYCLOAK_REALM", "asterisk")
CLIENT_ID = env_value("KEYCLOAK_CLIENT_ID", "mcp-server")
CLIENT_SECRET = env_value("KEYCLOAK_CLIENT_SECRET", "dev-only-mcp-server-secret-CHANGE-ME")
USER_PASSWORD = os.getenv("TEST_USER_PASSWORD", "admin")
TOKEN_URL = f"{KC_PUBLIC}/realms/{REALM}/protocol/openid-connect/token"
CTX = env_value("ASTERISK_DEFAULT_CONTEXT", "mcp-internal")
QUEUE = "support"
ECHO = f"Local/701@{CTX}"  # Answer() + Echo()  -> canal media reel
QUEUE_CH = f"Local/800@{CTX}"  # Answer() + Queue() -> attente en file

RESULTS: list[tuple[str, str, bool, str]] = []
ANSWER = {"v": True}


def rec(phase: str, name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((phase, name, ok, detail))
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f"\n         {detail}" if detail else ""), flush=True)


def phase(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(4, 60 - len(title)), flush=True)


def brief(data, n: int = 200) -> str:
    s = json.dumps(data, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + "..."


def find_numbers(obj, pattern: str) -> list[int]:
    rx = re.compile(pattern, re.I)
    out: list[int] = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if rx.search(str(k)) and isinstance(v, (int, float)) and not isinstance(v, bool):
                    out.append(int(v))
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(obj)
    return out


async def get_token(user: str) -> tuple[str, list[str]]:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "username": user,
                "password": USER_PASSWORD,
            },
        )
        r.raise_for_status()
        tok = r.json()["access_token"]
    payload = json.loads(base64.urlsafe_b64decode(tok.split(".")[1] + "=="))
    roles = sorted(
        x for x in payload.get("realm_access", {}).get("roles", []) if x != f"default-roles-{REALM}"
    )
    return tok, roles


async def elicit(message, response_type, params, ctx):  # noqa: ARG001
    v = ANSWER["v"]
    print(f"         [HITL] {message} -> {'OUI' if v else 'NON'}", flush=True)
    return v


async def call(client, tool: str, args: dict) -> dict:
    res = await client.call_tool(tool, args)
    return res.data if isinstance(res.data, dict) else {"raw": str(res.data)}


def ok(data: dict) -> bool:
    return isinstance(data, dict) and data.get("status") == "success"


def err(data: dict, code: str) -> bool:
    return isinstance(data, dict) and data.get("error") == code


def channels_of(data: dict) -> list[dict]:
    chans = data.get("channels") or []
    return chans if isinstance(chans, list) else []


def first_id(chans: list[dict]) -> str:
    for c in chans:
        cid = c.get("channel_id") or c.get("name") or c.get("channel") or ""
        if cid:
            return cid
    return ""


async def main() -> int:
    print("TEST FONCTIONNEL DE BOUT EN BOUT - supervision Asterisk + MCP", flush=True)
    print(f"MCP={MCP_URL}  Keycloak={KC_PUBLIC}  realm={REALM}  contexte={CTX}", flush=True)

    # ---------------------------------------------------------- 1. AUTHENTIFICATION
    phase("1. Authentification Keycloak (JWT + roles)")
    tokens: dict[str, str] = {}
    expected = {
        "admin_demo": "admin",
        "superviseur_demo": "superviseur",
        "operateur_demo": "operateur",
    }
    for user, want in expected.items():
        try:
            tok, roles = await get_token(user)
            tokens[user] = tok
            rec("1", f"JWT {user}", want in roles, f"roles={roles}")
        except Exception as e:  # noqa: BLE001
            rec("1", f"JWT {user}", False, str(e)[:120])
    if not tokens:
        print("\nARRET : aucun jeton obtenu (Keycloak demarre ? realm importe ?).")
        return 2

    admin = Client(MCP_URL, auth=BearerAuth(tokens["admin_demo"]), elicitation_handler=elicit)
    superv = Client(MCP_URL, auth=BearerAuth(tokens["superviseur_demo"]), elicitation_handler=elicit)
    oper = Client(MCP_URL, auth=BearerAuth(tokens["operateur_demo"]), elicitation_handler=elicit)

    async with admin, superv, oper:
        # ------------------------------------------------------------- 2. DECOUVERTE
        phase("2. Decouverte MCP")
        tools = sorted(t.name for t in await admin.list_tools())
        rec("2", f"{len(tools)} outils exposes", len(tools) == 12, ", ".join(tools))

        # ------------------------------------------------------------- 3. LECTURE
        phase("3. Outils de lecture (operateur_demo)")
        d = await call(oper, "list_active_channels", {})
        rec("3", "list_active_channels", ok(d), f"{len(channels_of(d))} canal/aux | {brief(d, 120)}")
        d = await call(oper, "get_queue_stats", {"queue": QUEUE})
        rec("3", f"get_queue_stats({QUEUE})", ok(d), brief(d))
        d = await call(oper, "get_extension_status", {"context": CTX})
        n_ext = len(d.get("extensions") or [])
        rec("3", f"get_extension_status({CTX})", ok(d) and n_ext > 0, f"{n_ext} extension(s) | {brief(d, 120)}")
        d = await call(oper, "get_channel_info", {"channel_id": "PJSIP/inexistant-00000000;1"})
        rec("3", "get_channel_info(inexistant) -> erreur propre", err(d, "channel_not_found"), brief(d))

        # ------------------------------------------------------------- 4. ANALYSE
        phase("4. Outils d'analyse (superviseur_demo)")
        d = await call(superv, "get_trunk_utilization", {})
        rec("4", "get_trunk_utilization", ok(d), brief(d))
        d = await call(superv, "get_cdr_report", {"limit": 5})
        n_cdr = len(d.get("records") or [])
        rec("4", "get_cdr_report(5)", ok(d), f"{n_cdr} enregistrement(s) | {brief(d, 120)}")
        d = await call(superv, "analyze_call_quality", {"channel_id": "PJSIP/inexistant-00000000;1"})
        rec("4", "analyze_call_quality(inexistant) -> erreur propre", err(d, "channel_not_found"), brief(d))

        # ------------------------------------------------------------- 5. LLM LOCAL
        phase("5. LLM local (Ollama) - llm_chat")
        t0 = time.perf_counter()
        d = await call(
            superv,
            "llm_chat",
            {
                "prompt": "En une phrase : querecommandes-tu au superviseur avec 0 canal actif ?",
                "context": ["trunks", "queues", "channels"],
                "max_tokens": 120,
            },
        )
        dt = time.perf_counter() - t0
        reply = (d.get("reply") or "") if isinstance(d, dict) else ""
        rec(
            "5",
            "llm_chat + contexte Asterisk",
            bool(reply) and "context_length" in d,
            f"{d.get('model')} | contexte={d.get('context_length')} | {dt:.1f}s | {brief(reply, 220)}",
        )

        # ------------------------------------------------- 6. APPEL REEL + PILOTAGE
        phase("6. Appel reel (Echo/RTP) + inspection + raccrochage")
        ANSWER["v"] = True
        d = await call(admin, "originate_call", {"endpoint": ECHO, "exten": "701", "context": CTX})
        rec("6", "originate_call (HITL confirme)", ok(d), brief(d))
        time.sleep(4)
        d = await call(admin, "list_active_channels", {})
        live = [c for c in channels_of(d) if "701" in json.dumps(c, default=str)]
        rec(
            "6",
            "canaux crees et actifs",
            len(live) >= 2,
            f"{len(channels_of(d))} canaux | "
            + " ; ".join(str(c.get("channel") or c.get("name") or c)[:60] for c in channels_of(d)),
        )
        cid = first_id(live)
        if cid:
            d = await call(admin, "get_channel_info", {"channel_id": cid})
            rec("6", f"get_channel_info({cid[:34]})", ok(d), brief(d, 220))
            d = await call(admin, "analyze_call_quality", {"channel_id": cid})
            # Les canaux Local/ n'ont pas de socket RTP : PJSIPShowChannelStats ne
            # s'applique pas et RTPAUDIOQOS n'est pas positionne. L'outil doit le
            # dire clairement (une qualite RTP reelle exige un canal PJSIP
            # enregistre, absent de ce banc d'essai).
            rec(
                "6",
                "analyze_call_quality -> erreur explicite (canal Local sans RTCP)",
                d.get("error") == "asterisk_unavailable" and "RTCP" in (d.get("detail") or ""),
                brief(d, 200),
            )
            d = await call(admin, "hangup_channel", {"channel_id": cid})
            rec("6", "hangup_channel (HITL confirme)", ok(d), brief(d))
        else:
            rec("6", "canal cible introuvable pour l'inspection", False)
        time.sleep(3)
        d = await call(admin, "list_active_channels", {})
        left = [c for c in channels_of(d) if "701" in json.dumps(c, default=str)]
        rec("6", "canaux liberes apres raccrochage", len(left) == 0, f"{len(left)} restant(s)")

        # ------------------------------------------------- 7. FILE D'ATTENTE REELLE
        phase("7. File d'attente (Queue support) - entree et stats")
        ANSWER["v"] = True
        d = await call(admin, "originate_call", {"endpoint": QUEUE_CH, "exten": "800", "context": CTX})
        rec("7", "originate_call vers extension 800 (file)", ok(d), brief(d, 120))
        time.sleep(4)
        d = await call(superv, "get_queue_stats", {"queue": QUEUE})
        waiting = find_numbers(d, r"waiting|attente|calls_waiting|en_attente")
        rec(
            "7",
            "get_queue_stats pendant l'appel",
            ok(d) and any(w >= 1 for w in waiting),
            f"attente={waiting} | {brief(d, 200)}",
        )
        d = await call(admin, "list_active_channels", {})
        qch = [c for c in channels_of(d) if "800" in json.dumps(c, default=str)]
        qid = first_id(qch)
        if qid:
            d = await call(admin, "hangup_channel", {"channel_id": qid})
            rec("7", "hangup_channel (sortie de file)", ok(d), brief(d, 120))
        else:
            rec("7", "canal de file introuvable", False)

        # ------------------------------------------------- 8. HITL REFUSE
        phase("8. HITL - refus humain")
        ANSWER["v"] = False
        d = await call(admin, "originate_call", {"endpoint": ECHO, "exten": "701", "context": CTX})
        rec("8", "originate_call refuse -> aucun appel", d.get("status") == "cancelled", brief(d))
        time.sleep(2)
        d = await call(admin, "list_active_channels", {})
        newc = [c for c in channels_of(d) if "701" in json.dumps(c, default=str)]
        rec("8", "aucun canal cree apres refus", len(newc) == 0, f"{len(newc)} canal/aux 701")
        ANSWER["v"] = True

        # ------------------------------------------------- 9. ENCADREMENT LEGAL
        phase("9. Ecoute (ChanSpy) - encadrement legal")
        d = await call(
            admin,
            "spy_channel",
            {
                "target_channel": "PJSIP/1001-00000000;1",
                "supervisor_endpoint": "PJSIP/1002",
                "mode": "listen",
            },
        )
        rec(
            "9",
            "spy_channel sans acknowledge_legal -> refuse",
            err(d, "legal_acknowledgement_required"),
            brief(d, 160),
        )
        d = await call(
            admin,
            "spy_channel",
            {
                "target_channel": "PJSIP/inexistant-00000000;1",
                "supervisor_endpoint": "PJSIP/1002",
                "mode": "listen",
                "acknowledge_legal": True,
            },
        )
        rec(
            "9",
            "spy_channel (legal OK) sur canal inexistant -> channel_not_found",
            err(d, "channel_not_found"),
            brief(d, 160),
        )

        # ------------------------------------------------- 10. RBAC
        phase("10. RBAC - hierarchie des roles")
        d = await call(oper, "get_cdr_report", {"limit": 1})
        rec("10", "operateur -> get_cdr_report (superviseur+)", err(d, "unauthorized"), brief(d, 140))
        d = await call(oper, "originate_call", {"endpoint": ECHO, "exten": "701", "context": CTX})
        rec("10", "operateur -> originate_call (admin)", err(d, "unauthorized"), brief(d, 140))
        d = await call(superv, "originate_call", {"endpoint": ECHO, "exten": "701", "context": CTX})
        rec("10", "superviseur -> originate_call (admin)", err(d, "unauthorized"), brief(d, 140))
        d = await call(oper, "list_active_channels", {})
        rec("10", "operateur -> list_active_channels (autorise)", ok(d), "acces conserve")

        # ------------------------------------------------- 11. NETTOYAGE
        phase("11. Nettoyage")
        d = await call(admin, "list_active_channels", {})
        leftovers = channels_of(d)
        rec("11", "canaux residuels", True, f"{len(leftovers)} | " + brief(leftovers, 160))

    # ------------------------------------------------------------------- BILAN
    good = [r for r in RESULTS if r[2]]
    bad = [r for r in RESULTS if not r[2]]
    print("\n" + "=" * 68)
    print(f"BILAN : {len(good)}/{len(RESULTS)} verifications OK")
    if bad:
        print("\nECHECS :")
        for ph, name, _, detail in bad:
            print(f"  - [{ph}] {name} | {detail}")
    print("=" * 68)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
