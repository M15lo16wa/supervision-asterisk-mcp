# src/adapters/ollama_llm.py
"""LLM local (Ollama ou llama.cpp) — HTTP/1.1, streaming.

Adaptateur générique réutilisé par :
  * le serveur MCP — outil superviseur ``llm_chat`` (Module 2, prompts) ;
  * le pipeline vocal S2S (Module 3) via ``src/voice/llm.build_llm``.

Deux back-end sont supportés, détectés automatiquement (paramètre ``api``) :

  * **ollama**  — endpoint natif ``/api/chat`` (NDJSON) ;
  * **openai**  — endpoint compatible OpenAI ``/v1/chat/completions`` (SSE),
                  utilisé par **llama.cpp** (et par Ollama récent).

La détection ne probe le serveur qu'une seule fois : ``GET /api/tags``
répond 200 sur Ollama et 404 sur llama.cpp, ce qui choisit le bon mode.

Streaming afin que le premier jeton puisse déclencher la synthèse vocale.
Les défaillances HTTP sont mappées sur :class:`LlmUnavailableError` pour un
comportement métier homogène avec le reste du système.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Literal

import httpx

from src.domain.exceptions import LlmUnavailableError
from src.domain.ports import LanguageModel
from src.observability import metrics

logger = logging.getLogger(__name__)

ApiMode = Literal["ollama", "openai", "auto"]


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
        api: ApiMode = "auto",
    ):
        self._base_url = base_url
        self._model = model
        self._system_prompt = system_prompt
        self._temperature = temperature
        self._num_predict = num_predict
        self._max_history = max_history
        self._requested_api: ApiMode = api
        # Résolu à la première requête quand `api == "auto"`.
        self._api: str | None = None if api == "auto" else api
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    @property
    def model(self) -> str:
        return self._model

    async def _resolve_api(self) -> str:
        """Retourne "ollama" ou "openai", en probeant le serveur une seule fois."""
        if self._api is not None:
            return self._api
        try:
            r = await self._client.get("/api/tags")
            if r.status_code == 200:
                self._api = "ollama"
                logger.info("LLM backend détecté : ollama (%s)", self._base_url)
                return self._api
        except httpx.HTTPError as e:
            # Serveur injoignable : on ne fige pas le mode, on remonte l'erreur.
            raise LlmUnavailableError(
                f"LLM injoignable ({self._base_url}): {e}"
            ) from e
        self._api = "openai"
        logger.info("LLM backend détecté : openai/llama.cpp (%s)", self._base_url)
        return self._api

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
        api: str,
    ) -> dict:
        messages = self._messages(prompt, history, system_prompt)
        temp = temperature if temperature is not None else self._temperature
        n_predict = max_tokens or self._num_predict
        if api == "openai":
            # Format compatible OpenAI (llama.cpp).
            return {
                "model": self._model,
                "messages": messages,
                "stream": stream,
                "max_tokens": n_predict,
                "temperature": temp,
            }
        # Format natif Ollama.
        return {
            "model": self._model,
            "messages": messages,
            "stream": stream,
            "options": {
                "num_predict": n_predict,
                "temperature": temp,
            },
        }

    @staticmethod
    def _extract_content(data: dict, api: str) -> str:
        if api == "openai":
            choices = data.get("choices") or []
            if choices:
                return (choices[0].get("message") or {}).get("content", "") or ""
            return ""
        return (data.get("message") or {}).get("content", "") or ""

    async def reply(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        # Mesure au niveau de l'adaptateur : c'est le point de passage commun
        # aux deux consommateurs (outil ``llm_chat`` ET pipeline vocal). Mesurer
        # au call-site ne voyait que ``llm_chat`` — les tours vocaux restaient
        # donc invisibles dans Prometheus.
        status = "error"
        try:
            with metrics.llm_timer(self.model):
                api = await self._resolve_api()
                payload = self._payload(prompt, history, system_prompt, temperature, max_tokens, stream=False, api=api)
                endpoint = "/v1/chat/completions" if api == "openai" else "/api/chat"
                try:
                    r = await self._client.post(endpoint, json=payload)
                    r.raise_for_status()
                except httpx.HTTPError as e:
                    raise LlmUnavailableError(
                        f"LLM injoignable ({self._base_url}): {e}"
                    ) from e
                text = self._extract_content(r.json(), api).strip()
            status = "success"
            return text
        finally:
            metrics.llm_result(self.model, status)

    async def stream_reply(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        # Mesure au niveau de l'adaptateur (voir ``reply``) : elle couvre aussi
        # les tours du pipeline vocal, invisibles depuis le call-site.
        status = "error"
        try:
            with metrics.llm_timer(self.model):
                api = await self._resolve_api()
                payload = self._payload(prompt, history, system_prompt, temperature, max_tokens, stream=True, api=api)
                endpoint = "/v1/chat/completions" if api == "openai" else "/api/chat"
                try:
                    async with self._client.stream("POST", endpoint, json=payload) as r:
                        r.raise_for_status()
                        async for line in r.aiter_lines():
                            if not line.strip():
                                continue
                            # Le format OpenAI est du SSE : "data: {...}" / "data: [DONE]".
                            if api == "openai":
                                if line.startswith("data:"):
                                    line = line[5:].strip()
                                if not line or line == "[DONE]":
                                    if line == "[DONE]":
                                        break
                                    continue
                            try:
                                chunk = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            piece = self._extract_content(chunk, api)
                            if api == "openai":
                                # En streaming OpenAI le texte est dans delta, pas message.
                                choices = chunk.get("choices") or []
                                if choices:
                                    piece = (choices[0].get("delta") or {}).get("content", "") or ""
                            if piece:
                                yield piece
                            if api == "ollama" and chunk.get("done"):
                                break
                except httpx.HTTPError as e:
                    raise LlmUnavailableError(
                        f"LLM injoignable ({self._base_url}): {e}"
                    ) from e
                status = "success"
        except GeneratorExit:
            # Le consommateur a abandonné le flux (appel raccroché en cours).
            status = "cancelled"
            raise
        finally:
            metrics.llm_result(self.model, status)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def warm_up(self) -> float:
        """Charge le modèle avec une requête d'un token et renvoie la durée (s).

        Le premier appel après un redémarrage doit charger le modèle depuis le
        disque : sans préchauffage, le premier tour de parole d'un appel vocal
        bloque plusieurs minutes.
        """
        api = await self._resolve_api()
        if api == "openai":
            payload = {
                "model": self._model,
                "messages": [{"role": "user", "content": "ok"}],
                "stream": False,
                "max_tokens": 1,
                "temperature": 0.0,
            }
            endpoint = "/v1/chat/completions"
        else:
            payload = {
                "model": self._model,
                "messages": [{"role": "user", "content": "ok"}],
                "stream": False,
                "keep_alive": -1,
                "options": {"num_predict": 1, "temperature": 0.0},
            }
            endpoint = "/api/chat"
        started = time.perf_counter()
        r = await self._client.post(endpoint, json=payload)
        r.raise_for_status()
        return time.perf_counter() - started
