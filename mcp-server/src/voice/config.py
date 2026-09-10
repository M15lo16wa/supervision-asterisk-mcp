# src/voice/config.py
"""Configuration of the Speech-to-Speech pipeline (Module 3)."""
import os
from dataclasses import dataclass, field

from src.config import _env, _env_int  # reuse the repo-root .env loader


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class VoiceSettings:
    # Audio — Asterisk External Media délivre du slin16 (PCM 16 bits, mono, 16 kHz)
    sample_rate: int = 16000
    frame_ms: int = 20                       # trame RTP standard
    channels: int = 1

    # Endpointing (détection de fin de parole)
    vad_energy_threshold: float = field(default_factory=lambda: _env_float("VAD_ENERGY_THRESHOLD", 500.0))
    silence_hangover_ms: int = field(default_factory=lambda: _env_int("SILENCE_HANGOVER_MS", 500))
    max_utterance_ms: int = field(default_factory=lambda: _env_int("MAX_UTTERANCE_MS", 12000))
    min_utterance_ms: int = field(default_factory=lambda: _env_int("MIN_UTTERANCE_MS", 300))

    # STT — faster-whisper
    stt_model: str = field(default_factory=lambda: _env("STT_MODEL", "small"))
    stt_device: str = field(default_factory=lambda: _env("STT_DEVICE", "cpu"))
    stt_compute_type: str = field(default_factory=lambda: _env("STT_COMPUTE_TYPE", "int8"))
    stt_language: str = field(default_factory=lambda: _env("STT_LANGUAGE", "fr"))
    stt_beam_size: int = field(default_factory=lambda: _env_int("STT_BEAM_SIZE", 1))

    # LLM — Ollama
    ollama_base_url: str = field(default_factory=lambda: _env("OLLAMA_BASE_URL", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", "qwen2.5:3b-instruct"))
    llm_system_prompt: str = field(default_factory=lambda: _env(
        "LLM_SYSTEM_PROMPT",
        "Tu es l'assistant vocal d'un standard téléphonique. Réponds en français, "
        "en une à deux phrases courtes, sans formatage.",
    ))
    llm_num_predict: int = field(default_factory=lambda: _env_int("LLM_NUM_PREDICT", 80))

    # TTS — Piper
    tts_voice: str = field(default_factory=lambda: _env("TTS_VOICE", "fr_FR-siwis-medium"))
    tts_model_dir: str = field(default_factory=lambda: _env("TTS_MODEL_DIR", "/models/piper"))

    # ARI External Media
    ari_base_url: str = field(default_factory=lambda: _env("ASTERISK_ARI_BASE_URL", "http://localhost:8088"))
    ari_user: str = field(default_factory=lambda: _env("ASTERISK_ARI_USER", "mcp_ari"))
    ari_password: str = field(default_factory=lambda: _env("ASTERISK_ARI_PASSWORD", "mcp_ari"))
    ari_app: str = field(default_factory=lambda: _env("ASTERISK_ARI_APP", "mcp-voice"))
    rtp_host: str = field(default_factory=lambda: _env("VOICE_RTP_HOST", "0.0.0.0"))
    rtp_port: int = field(default_factory=lambda: _env_int("VOICE_RTP_PORT", 40000))

    # Budget de latence global (STT + LLM + TTS), en millisecondes
    latency_budget_ms: int = field(default_factory=lambda: _env_int("LATENCY_BUDGET_MS", 1500))

    @property
    def frame_bytes(self) -> int:
        return int(self.sample_rate * self.frame_ms / 1000) * 2 * self.channels


voice_settings = VoiceSettings()
