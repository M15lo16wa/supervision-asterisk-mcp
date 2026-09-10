# src/voice/llm.py
"""Local LLM via Ollama (HTTP). Streaming so the first token gates TTS start."""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

from src.domain.ports import LanguageModel
from src.voice.config import VoiceSettings

logger = logging.getLogger(__name__)


class OllamaLLM(LanguageModel):
    def __init__(self, settings: VoiceSettings):
        self._s = settings
        self._client = httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=30.0)

    def _messages(self, prompt: str, history: list[dict] | None) -> list[dict]:
        msgs = [{"role": "system", "content": self._s.llm_system_prompt}]
        if history:
            msgs.extend(history[-8:])
        msgs.append({"role": "user", "content": prompt})
        return msgs

    async def reply(self, prompt: str, history: list[dict] | None = None) -> str:
        payload = {
            "model": self._s.ollama_model,
            "messages": self._messages(prompt, history),
            "stream": False,
            "options": {"num_predict": self._s.llm_num_predict, "temperature": 0.3},
        }
        r = await self._client.post("/api/chat", json=payload)
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "").strip()

    async def stream_reply(
        self, prompt: str, history: list[dict] | None = None
    ) -> AsyncIterator[str]:
        payload = {
            "model": self._s.ollama_model,
            "messages": self._messages(prompt, history),
            "stream": True,
            "options": {"num_predict": self._s.llm_num_predict, "temperature": 0.3},
        }
        async with self._client.stream("POST", "/api/chat", json=payload) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                piece = chunk.get("message", {}).get("content", "")
                if piece:
                    yield piece
                if chunk.get("done"):
                    break

    async def aclose(self) -> None:
        await self._client.aclose()


class EchoLLM(LanguageModel):
    """Test double: echoes the prompt, no server required."""

    async def reply(self, prompt: str, history: list[dict] | None = None) -> str:
        return f"Vous avez dit : {prompt}"


def build_llm(settings: VoiceSettings) -> LanguageModel:
    return OllamaLLM(settings)
