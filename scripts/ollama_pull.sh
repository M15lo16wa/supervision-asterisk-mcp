#!/usr/bin/env bash
# Télécharge le modèle LLM dans le conteneur Ollama externe.
#   ./scripts/ollama_pull.sh [conteneur] [modele]
#   défauts :                 ollama      $OLLAMA_MODEL | qwen2.5:3b-instruct
set -euo pipefail
CONTAINER="${1:-${OLLAMA_CONTAINER:-supervision-ollama}}"
MODEL="${2:-${OLLAMA_MODEL:-qwen2.5:3b-instruct}}"
echo "==> $CONTAINER : ollama pull $MODEL"
if ! docker exec "$CONTAINER" ollama pull "$MODEL"; then
  echo "ERREUR: conteneur '$CONTAINER' introuvable ou ollama indisponible." >&2
  echo "Conteneurs disponibles :" >&2
  docker ps --format '  - {{.Names}} ({{.Image}})' >&2
  echo "Usage: $0 [conteneur] [modele]  (ex: $0 supervision-ollama qwen2.5:3b-instruct)" >&2
  exit 1
fi
docker exec "$CONTAINER" ollama list
