# src/adapters/ollama_llm.py
"""LLM local via Ollama (HTTP/1.1, endpoint ``/api/chat``).

Adaptateur générique réutilisé par :
  * le serveur MCP — outil superviseur ``llm_chat`` (Module 2, prompts) ;
  * le pipeline vocal S2S (Module 3) via ``src/voice/llm.build_llm``.

Streaming afin que le premier jeton puisse déclencher la synthèse vocale.
Les défaillances HTTP sont mappées sur :class:`LlmUnavailableError` pour un
comportement métier homogène avec le reste du système.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

from src.domain.exceptions import LlmUnavailableError
from src.domain.ports import LanguageModel

logger = logging.getLogger(__name__)


class OllamaLLM(LanguageModel):
    def __init__(
        self,
        base_url: str,
        model: str,
        system_prompt: str,
        *,
        temperature: float = 0.3,
        num_predict: int = 512,
        timeout: float = 60.0,
        max_history: int = 8,
    ):
        self._base_url = base_url
        self._model = model
        self._system_prompt = system_prompt
        self._temperature = temperature
        self._num_predict = num_predict
        self._max_history = max_history
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    @property
    def model(self) -> str:
        return self._model

    def _messages(
        self,
        prompt: str,
        history: list[dict] | None,
        system_prompt: str | None,
    ) -> list[dict]:
        msgs = [{"role": "system", "content": system_prompt if system_prompt is not None else self._system_prompt}]
        if history:
            msgs.extend(history[-self._max_history:])
        msgs.append({"role": "user", "content": prompt})
        return msgs

    def _payload(
        self,
        prompt: str,
        history: list[dict] | None,
        system_prompt: str | None,
        temperature: float | None,
        max_tokens: int | None,
        stream: bool,
    ) -> dict:
        return {
            "model": self._model,
            "messages": self._messages(prompt, history, system_prompt),
            "stream": stream,
            "options": {
                "num_predict": max_tokens or self._num_predict,
                "temperature": temperature if temperature is not None else self._temperature,
            },
        }

    async def reply(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        payload = self._payload(prompt, history, system_prompt, temperature, max_tokens, stream=False)
        try:
            r = await self._client.post("/api/chat", json=payload)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LlmUnavailableError(
                f"Ollama injoignable ({self._base_url}): {e}"
            ) from e
        return r.json().get("message", {}).get("content", "").strip()

    async def stream_reply(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        payload = self._payload(prompt, history, system_prompt, temperature, max_tokens, stream=True)
        try:
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
        except httpx.HTTPError as e:
            raise LlmUnavailableError(
                f"Ollama injoignable ({self._base_url}): {e}"
            ) from e

    async def aclose(self) -> None:
        await self._client.aclose()