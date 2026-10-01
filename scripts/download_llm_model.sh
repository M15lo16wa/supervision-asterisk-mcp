#!/usr/bin/env bash
# Télécharge le modèle GGUF pour llama.cpp dans monitoring/models/.
# Usage : ./scripts/download_llm_model.sh [0.5b|3b]
set -euo pipefail

SIZE="${1:-0.5b}"
DEST_DIR="$(cd "$(dirname "$0")/.." && pwd)/monitoring/models"
mkdir -p "$DEST_DIR"

case "$SIZE" in
  0.5b)
    URL="https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"
    ;;
  3b)
    URL="https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf"
    ;;
  *)
    echo "Usage : $0 [0.5b|3b]" >&2
    exit 1
    ;;
esac

DEST="$DEST_DIR/model.gguf"
echo "Téléchargement du modèle $SIZE vers $DEST ..."
curl -fSL --retry 3 -o "$DEST" "$URL"
echo "Terminé : $DEST"
