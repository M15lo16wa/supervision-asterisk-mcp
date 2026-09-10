#!/usr/bin/env bash
# Télécharge le modèle LLM dans le conteneur Ollama du stack.
#   ./scripts/ollama_pull.sh [modele]      (défaut: $OLLAMA_MODEL ou qwen2.5:3b-instruct)
set -euo pipefail
MODEL="${1:-${OLLAMA_MODEL:-qwen2.5:3b-instruct}}"
echo "==> ollama pull $MODEL"
docker compose exec ollama ollama pull "$MODEL"
docker compose exec ollama ollama list
