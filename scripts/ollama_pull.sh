#!/usr/bin/env bash
# Télécharge le modèle LLM dans le conteneur Ollama externe.
#   ./scripts/ollama_pull.sh [conteneur] [modele]
#   défauts :                 ollama      $OLLAMA_MODEL | qwen2.5:3b-instruct
set -euo pipefail
CONTAINER="${1:-ollama}"
MODEL="${2:-${OLLAMA_MODEL:-qwen2.5:3b-instruct}}"
echo "==> $CONTAINER : ollama pull $MODEL"
docker exec "$CONTAINER" ollama pull "$MODEL"
docker exec "$CONTAINER" ollama list
