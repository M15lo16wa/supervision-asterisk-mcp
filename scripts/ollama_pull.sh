#!/usr/bin/env bash
# Télécharge le modèle LLM dans le conteneur Ollama externe.
#   ./scripts/ollama_pull.sh [conteneur] [modele]
#   défauts :                 ollama      $OLLAMA_MODEL | qwen2.5:3b-instruct
set -euo pipefail
CONTAINER="${1:-${OLLAMA_CONTAINER:-supervision-ollama}}"
MODEL="${2:-${OLLAMA_MODEL:-qwen2.5:3b-instruct}}"
NETWORK="${OLLAMA_NETWORK:-supervision_net}"
IMAGE="${OLLAMA_IMAGE:-ollama/ollama:latest}"
VOLUME="${OLLAMA_VOLUME:-ollama_data}"

# Crée le conteneur s'il n'existe pas, connecté au réseau du reste du système.
if ! docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "==> Conteneur '$CONTAINER' absent, création sur le réseau '$NETWORK'…"
  docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create "$NETWORK" >/dev/null
  docker run -d --name "$CONTAINER" --network "$NETWORK" --restart unless-stopped \
    -p 11434:11434 -v "${VOLUME}:/root/.ollama" "$IMAGE" >/dev/null
  echo -n "==> Attente du démarrage d'ollama"
  for i in $(seq 1 30); do
    docker exec "$CONTAINER" ollama list >/dev/null 2>&1 && { echo " OK"; break; }
    echo -n "."; sleep 1
    [ "$i" -eq 30 ] && { echo; echo "ERREUR: ollama n'a pas démarré." >&2; exit 1; }
  done
elif ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "==> Démarrage du conteneur existant '$CONTAINER'…"
  docker start "$CONTAINER" >/dev/null && sleep 2
fi

echo "==> $CONTAINER : ollama pull $MODEL"
if ! docker exec "$CONTAINER" ollama pull "$MODEL"; then
  echo "ERREUR: conteneur '$CONTAINER' introuvable ou ollama indisponible." >&2
  echo "Conteneurs disponibles :" >&2
  docker ps --format '  - {{.Names}} ({{.Image}})' >&2
  echo "Usage: $0 [conteneur] [modele]  (ex: $0 supervision-ollama qwen2.5:3b-instruct)" >&2
  exit 1
fi
docker exec "$CONTAINER" ollama list
