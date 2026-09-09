# Supervision Asterisk MCP

Serveur MCP (Model Context Protocol) intégrant Asterisk avec capacités d'IA pour une supervision intelligente d'appels avec contrôle humain (HITL).

## Démarrage rapide

Cloner le repository :
```bash
git clone https://github.com/M15lo16wa/supervision-asterisk-mcp.git
cd supervision-asterisk-mcp
```

Configurer les variables d'environnement :
```bash
cp keycloak/.env.example keycloak/.env
```

Créer le réseau Docker :
```bash
docker network create supervision-net
```

Démarrer Keycloak (authentification) :
```bash
cd keycloak
docker-compose up -d
```

Démarrer le serveur MCP :
```bash
cd mcp-server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip show fastmcp
```

Vérifier l'installation (optionnel) :
```bash
cd ~/Projets/supervision-asterisk-mcp/mcp-server
source .venv/bin/activate
pip show fastmcp
```

```bash
docker-compose up -d
```

Démarrer Ollama (optionnel) :
```bash
cd ollama
docker-compose up -d
```

## Architecture

Le projet suit une architecture hexagonale avec deux zones :

- **Zone autonome** : ListActiveChannelsUseCase (lecture seule)
- **Zone supervisée** : OriginateCallUseCase (confirmation HITL obligatoire)

## Stack

- Python 3.10+
- FastMCP 3.0+ (serveur MCP)
- Keycloak (authentification)
- Prometheus + Grafana (monitoring)
- Asterisk ARI (communication VoIP)

