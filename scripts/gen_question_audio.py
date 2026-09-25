#!/usr/bin/env python3
"""Genere la question vocale du test E2E (WAV 16 kHz mono 16 bits).

Utilise le **meme** TTS (Piper) que le pipeline, execute dans le conteneur
`voice-pipeline` : pas de dependance native sur l'hote.

    python scripts/gen_question_audio.py                      # -> question.wav
    python scripts/gen_question_audio.py /tmp/ma_question.wav

Le WAV produit est en little-endian natif ; `scripts/e2e_voice.py` se charge
de le convertir en slin16 big-endian avant l'injection RTP.
"""
from __future__ import annotations

import os
import subprocess
import sys
import wave
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
CONTAINER = os.getenv("VOICE_CONTAINER", "supervision-voice-pipeline")
QUESTION = os.getenv("QUESTION_TEXT", "Bonjour, combien y a t-il d'appels en attente dans la file ?")
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "question.wav")
TMP_IN_CONTAINER = "/tmp/question.wav"

# Genere dans le conteneur : build_tts() lit TTS_VOICE/VOICE_* de son environnement.
SNIPPET = f"""
import asyncio, wave
from src.voice.config import voice_settings
from src.voice.tts import build_tts

async def main():
    tts = build_tts(voice_settings)
    pcm = await tts.synthesize({QUESTION!r}, voice_settings.sample_rate)
    with wave.open({TMP_IN_CONTAINER!r}, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(voice_settings.sample_rate)
        w.writeframes(pcm)
    print(f"{{len(pcm) // 2}} {{len(pcm) / 2 / voice_settings.sample_rate:.2f}}")

asyncio.run(main())
"""


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def main() -> int:
    state = run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER])
    if state.returncode != 0 or state.stdout.strip() != "true":
        print(f"ERREUR : le conteneur {CONTAINER} n'est pas demarre.")
        print("        docker compose --profile voice up -d voice-pipeline")
        return 2

    print(f"Generation de la question dans {CONTAINER} : {QUESTION!r}")
    res = run(["docker", "exec", CONTAINER, "python", "-c", SNIPPET])
    if res.returncode != 0:
        print(res.stdout + res.stderr)
        return 1

    cp = run(["docker", "cp", f"{CONTAINER}:{TMP_IN_CONTAINER}", str(OUT)])
    run(["docker", "exec", CONTAINER, "rm", "-f", TMP_IN_CONTAINER])
    if cp.returncode != 0:
        print(cp.stdout + cp.stderr)
        return 1

    with wave.open(str(OUT), "rb") as w:
        n, rate = w.getnframes(), w.getframerate()
    print(f"ecrit {OUT} : {n} echantillons, {n / rate:.2f}s, {rate} Hz mono 16 bits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
