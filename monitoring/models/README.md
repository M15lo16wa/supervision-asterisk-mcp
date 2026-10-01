# Modèle GGUF pour llama.cpp

Ce dossier doit contenir le fichier `model.gguf` servi par le conteneur
`llama-cpp` (alias réseau `ollama`).

## Télécharger un modèle

Exemple avec Qwen2.5-0.5B-Instruct (léger, ~400 Mo) :

```bash
cd monitoring/models
curl -L -o model.gguf \
  https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf
```

Ou avec un modèle plus performant (3B, ~2 Go) :

```bash
curl -L -o model.gguf \
  https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf
```

## Vérification

```bash
docker compose up -d llama-cpp
docker compose logs -f llama-cpp
# Le serveur répond sur http://localhost:11434/health
```

## Configuration

Le nom du modèle dans `.env` doit correspondre au nom du fichier GGUF
(sans extension) ou être laissé vide pour que llama.cpp utilise le modèle
trouvé dans `/models/`.

```dotenv
# monitoring/.env
OLLAMA_MODEL=qwen2.5-0.5b-instruct-q4_k_m
```
