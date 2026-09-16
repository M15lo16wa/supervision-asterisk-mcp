# src/voice/llm.py
"""LLM local pour le pipeline vocal S2S (Module 3).

Ré-export de l'adaptateur générique :class:`OllamaLLM` (src/adapters) construit
avec les paramètres du pipeline vocal.
"""
from src.adapters.ollama_llm import OllamaLLM
from src.domain.ports import LanguageModel
from src.voice.config import VoiceSettings


class EchoLLM(LanguageModel):
    """Test double: echoes the prompt, no server required."""

    async def reply(self, prompt: str, history: list[dict] | None = None) -> str:
        return f"Vous avez dit : {prompt}"


def build_llm(settings: VoiceSettings) -> LanguageModel:
    return OllamaLLM(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        system_prompt=settings.llm_system_prompt,
        temperature=0.3,
        num_predict=settings.llm_num_predict,
    )