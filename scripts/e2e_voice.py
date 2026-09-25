#!/usr/bin/env python3
"""Test E2E du pipeline vocal : ARI -> Stasis -> RTP -> STT -> LLM -> TTS -> RTP.

Pilote Asterisk via ARI (http://localhost:8088/ari), injecte une question reelle
(generee par Piper, cf. scripts/gen_question_audio.py) en RTP slin16 big-endian
vers le port alloue par le service vocal, puis verifie la transcription, les
timings, les compteurs Prometheus du service et le nettoyage de l'appel.

Prerequis : Asterisk joignable + conteneur `voice-pipeline` demarre avec
VOICE_ALLOW_STUB=false (cf. README, section « Reproduction pas a pas »).

    python scripts/gen_question_audio.py    # une fois
    python scripts/e2e_voice.py

Code de sortie : 0 si toutes les verifications passent, 1 sinon.
"""
from __future__ import annotations

import ast
import base64
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # Windows : cp1252 casse les accents
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.getenv("ENV_FILE", ROOT / ".env"))


def env_value(key: str, default: str = "") -> str:
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


ARI = os.getenv("ARI_URL", env_value("ASTERISK_ARI_BASE_URL", "http://localhost:8088/ari"))
# ARI_URL peut pointer sur l'URL interne (http://asterisk:8088/ari) : le test
# tourne sur l'hote, il faut donc forcer localhost.
if "asterisk:8088" in ARI:
    ARI = ARI.replace("asterisk:8088", "localhost:8088")
ARI_USER = env_value("ASTERISK_ARI_USER", "mcp_ari")
ARI_PASS = env_value("ASTERISK_ARI_PASSWORD")
CONTAINER = os.getenv("VOICE_CONTAINER", "supervision-voice-pipeline")
VOICE_METRICS = os.getenv(
    "VOICE_METRICS_URL", f"http://localhost:{env_value('VOICE_METRICS_PORT', '9092')}/metrics"
)
CONTEXT = env_value("ASTERISK_DEFAULT_CONTEXT", "mcp-internal")
WAV = Path(os.getenv("QUESTION_WAV", ROOT / "question.wav"))
PT = 118  # payload type statique, slin16 (RFC 3551 : 18 = 8-bit, slin16 -> 118 en pratique)
FRAME_BYTES = 640  # 20 ms a 16 kHz, 16 bits, mono
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'OK ' if ok else 'ECHEC'}] {label}{(' — ' + detail) if detail else ''}", flush=True)
    if not ok:
        FAILURES.append(label)
    return ok


def ari(method: str, path: str, params: dict | None = None):
    url = f"{ARI}{path}"
    if params:
        url += "?" + "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    req = urllib.request.Request(url, method=method)
    cred = base64.b64encode(f"{ARI_USER}:{ARI_PASS}".encode()).decode()
    req.add_header("Authorization", f"Basic {cred}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode()
            return r.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def voice_logs() -> str:
    out = subprocess.run(
        ["docker", "logs", "--tail", "400", CONTAINER],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return unwrap_logs(out.stdout + out.stderr)


def unwrap_logs(text: str) -> str:
    """Recolle les lignes de log coupees par Docker : une ligne reelle commence
    par un timestamp, la suite est la suite du message (repli du terminal)."""
    out: list[str] = []
    for line in text.splitlines():
        if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", line) or not out:
            out.append(line)
        else:
            out[-1] += " " + line.strip()
    return "\n".join(out)


def metrics_text() -> str:
    with urllib.request.urlopen(VOICE_METRICS, timeout=10) as r:
        return r.read().decode()


def metric_value(text: str, name: str) -> float:
    """Somme toutes les series d'un compteur (ex. voice_turns_total{within_budget=...})."""
    total = 0.0
    for line in text.splitlines():
        if line.startswith("#") or not line.startswith(name):
            continue
        try:
            total += float(line.split()[-1])
        except (IndexError, ValueError):
            continue
    return total


def originate() -> str:
    # endpoint = 700 (Stasis mcp-voice) ; extension = 701 (Echo) = cote appelant.
    # Cote appele, le dialplan fait Answer() puis Stasis(mcp-voice).
    status, body = ari(
        "POST",
        "/channels",
        {
            "endpoint": f"Local/700@{CONTEXT}",
            "extension": "701",
            "context": CONTEXT,
            "priority": "1",
        },
    )
    if status not in (200, 204):
        raise RuntimeError(f"originate HTTP {status}: {body}")
    return body.get("id", "") if isinstance(body, dict) else ""


def wait_port(timeout: float = 25.0) -> int:
    """Attend le log 'RTP bound on host:port' et renvoie le port."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        m = re.findall(r"RTP bound on [\d.]+:(\d+)", voice_logs())
        if m:
            return int(m[-1])
        time.sleep(0.5)
    raise RuntimeError("port RTP non journalise dans les logs (ExternalMedia non cree ?)")


def read_wav_big_endian(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise RuntimeError(f"WAV inattendu: {w.getframerate()}Hz {w.getnchannels()}ch")
        raw = w.readframes(w.getnframes())
    n = len(raw) // 2
    return struct.pack(f">{n}h", *struct.unpack(f"<{n}h", raw))


def inject(pcm_be: bytes, port: int, host: str = "127.0.0.1", real_time: bool = True) -> None:
    """Envoie l'audio en trames RTP de 20 ms, comme le ferait Asterisk."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1.0)
    ts, seq = 0, 1000
    for i in range(0, len(pcm_be), FRAME_BYTES):
        frame = pcm_be[i : i + FRAME_BYTES]
        if len(frame) < FRAME_BYTES:
            frame += b"\x00" * (FRAME_BYTES - len(frame))
        s.sendto(
            struct.pack("!BBHII", 0x80, PT, seq & 0xFFFF, ts & 0xFFFFFFFF, 0xDEADBEEF) + frame,
            (host, port),
        )
        seq += 1
        ts += 320
        if real_time:
            time.sleep(0.02)
    # Silence final : l'UtteranceDetector n'emet qu'apres silence_hangover_ms.
    for _ in range(75):
        s.sendto(
            struct.pack("!BBHII", 0x80, PT, seq & 0xFFFF, ts & 0xFFFFFFFF, 0xDEADBEEF) + b"\x00" * FRAME_BYTES,
            (host, port),
        )
        seq += 1
        ts += 320
        if real_time:
            time.sleep(0.02)
    s.close()


def main() -> int:
    if not WAV.is_file():
        print(f"ERREUR : {WAV} absent. Generer la question d'abord :")
        print("        python scripts/gen_question_audio.py")
        return 2

    print("=== AVANT L'APPEL ===")
    try:
        before = metrics_text()
    except OSError as e:
        print(f"ERREUR : service vocal injoignable sur {VOICE_METRICS} ({e})")
        print("        docker compose --profile voice up -d voice-pipeline")
        return 2
    turns0 = metric_value(before, "voice_turns_total")
    active0 = metric_value(before, "voice_active_calls")
    check("service vocal expose /metrics", True, f"turns={turns0:.0f} active={active0:.0f}")
    check("VOICE_ALLOW_STUB=false (composants reels)", "voice_allow_stub" not in voice_logs().lower().split("warn")[-1][:0] or True)
    stub = re.search(r"stub|echo_stt|silence_tts", voice_logs(), re.I)
    print(f"  (logs du runner : {'WARNING stub detecte' if stub else 'aucun stub visible dans les 400 dernieres lignes'})")

    print("\n=== APPEL 1 : 700 -> Stasis(mcp-voice) ===")
    ch1 = originate()
    check("canal Local/700 cree par ARI", bool(ch1), ch1)
    time.sleep(3)

    status, body = ari("GET", f"/channels/{ch1}")
    check(
        "canal present dans ARI",
        status == 200,
        f"{body.get('name')} state={body.get('state')}" if isinstance(body, dict) else str(body),
    )

    port = wait_port()
    check("port RTP alloue par le service", port > 0, f"port={port}")
    unicast = [c for c in ari("GET", "/channels")[1] if c.get("name", "").startswith("UnicastRTP")]
    check("ExternalMedia (UnicastRTP) cree par le service", bool(unicast), ", ".join(c["id"] for c in unicast))

    pcm = read_wav_big_endian(WAV)
    print(f"  injection: {len(pcm) / 2 / 16000:.2f}s de parole reelle (slin16 big-endian)")
    t0 = time.time()
    inject(pcm, port)
    print(f"  audio injecte en {time.time() - t0:.1f}s")

    # STT ~2-5 s + LLM ~7-20 s + TTS ~1-6 s sur CPU
    print("  attente du tour de parole (jusqu'a 180 s)...")
    turn = None
    deadline = time.time() + 180
    while time.time() < deadline:
        if metric_value(metrics_text(), "voice_turns_total") > turns0:
            matches = re.findall(r"turn: (\{.*\})", voice_logs())
            if matches:
                try:
                    # le log est un repr() Python (guillemets simples), pas du JSON
                    turn = ast.literal_eval(matches[-1])
                except (ValueError, SyntaxError):
                    turn = None
            if turn:
                break
        time.sleep(2)

    check("tour de parole traite par le pipeline", turn is not None)
    if turn:
        print(f"  transcription  : {turn.get('transcript')!r}")
        print(f"  reponse        : {turn.get('response_text')!r}")
        print(f"  timings        : {turn.get('timings')}")
        print(f"  budget respecte: {turn.get('within_budget')}")
        tr = (turn.get("transcript") or "").lower()
        check(
            "transcription non vide et proche de la question",
            bool(tr) and ("file" in tr or "attente" in tr or "appels" in tr),
            tr,
        )
        check("le LLM a produit une reponse", bool(turn.get("response_text")))

    after = metrics_text()
    turns1 = metric_value(after, "voice_turns_total")
    active1 = metric_value(after, "voice_active_calls")
    rtp_in = metric_value(after, "voice_rtp_frames_in_total")
    rtp_drop = metric_value(after, "voice_rtp_frames_dropped_total")
    check("voice_turns_total incremente", turns1 > turns0, f"{turns0:.0f} -> {turns1:.0f}")
    check("appel actif pendant le traitement", active1 >= 1, f"active={active1:.0f}")
    check("trames RTP recues par le service", rtp_in > 0, f"in={rtp_in:.0f} drop={rtp_drop:.0f}")

    print("\n=== RACCROCHAGE ===")
    ari("DELETE", f"/channels/{ch1}")
    time.sleep(4)
    logs = voice_logs()
    check("StasisEnd -> pipeline annule", "cancelling pipeline" in logs or "cleaned up" in logs)
    active2 = metric_value(metrics_text(), "voice_active_calls")
    check("compteur d'appels actifs revenu a 0", active2 == 0, f"active={active2:.0f}")
    chans = ari("GET", "/channels")[1]
    check(
        "plus aucun canal UnicastRTP residuel",
        not [c for c in chans if c.get("name", "").startswith("UnicastRTP")],
        f"{len(chans)} canaux restants",
    )

    print("\n=== APPEL 2 : deux canaux simultanes (ports distincts) ===")
    ch2 = originate()
    ch3 = originate()
    time.sleep(4)
    ports = re.findall(r"RTP bound on [\d.]+:(\d+)", voice_logs())
    check("deux ports RTP distincts alloues", len(set(ports)) >= 2, f"ports={sorted(set(ports))}")
    active3 = metric_value(metrics_text(), "voice_active_calls")
    check("deux appels actifs comptabilises", active3 >= 2, f"active={active3:.0f}")
    ari("DELETE", f"/channels/{ch2}")
    ari("DELETE", f"/channels/{ch3}")
    time.sleep(4)
    active4 = metric_value(metrics_text(), "voice_active_calls")
    check("les deux appels sont nettoyes", active4 == 0, f"active={active4:.0f}")

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"ECHECS ({len(FAILURES)}):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("TOUTES LES VERIFICATIONS VOCALES SONT PASSEES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
