"""Module 3 — pipeline vocal Speech-to-Speech local.

Chaîne : Asterisk (ARI External Media, slin16) → STT (faster-whisper)
         → LLM local (Ollama) → TTS (Piper) → Asterisk.

Ce paquet s'exécute dans son propre processus (``python -m src.voice.runner``),
séparé du serveur MCP, car il embarque des modèles lourds.
"""
