# Supervision Asterisk via MCP

Serveur **MCP (Model Context Protocol)** qui expose les interfaces **ARI** et
**AMI** d'un PBX **Asterisk** à un LLM : supervision temps réel, analyse des
tickets de taxation (**CDR**), pilotage d'appels **sous contrôle humain
obligatoire (HITL)**, et assistant vocal **Speech-to-Speech (S2S) 100 % local**.

Développement direct sur **FastMCP** + clients **ARI/AMI natifs** (panoramisk) —
aucune dépendance payante, aucun framework tiers, pas d'OpenAPI/Swagger.

---

## Résumé de l'implémentation

**Socle & sécurité (Module 1)** — serveur FastMCP en JSON-RPC 2.0, transport
`stdio` ou **Streamable HTTP**. Authentification par **JWT Keycloak (OIDC)** ;
`MCP_PUBLIC_URL` publie la métadonnée de ressource protégée (RFC 9728) pour le
flux **OAuth 2.1 + PKCE** côté client. **RBAC** hiérarchique
`Opérateur < Superviseur < Admin` lu depuis `realm_access.roles`. Le jeton
n'authentifie que la session MCP — **jamais relayé à Asterisk** (comptes AMI/ARI
dédiés, moindre privilège). **Consentement humain** via *elicitation* MCP (pas
une simple annotation `destructiveHint`) sur toute action de pilotage, extensible
à tous les outils (`MCP_HITL_MODE=all`). Les sorties (données Asterisk) sont
traitées comme **entrées non fiables** : enveloppe + neutralisation d'injections.
Chaque appel, refus RBAC et confirmation HITL alimente un **journal d'audit**
JSON Lines (acteur, outil, paramètres masqués, résultat, `request_id`, horodatage).

**Outillage Asterisk (Module 2)** — **11 outils** de production, répartis
lecture / analyse / pilotage :

| Outil | Rôle min. | HITL | Description |
|---|---|---|---|
| `list_active_channels` | Opérateur | — | canaux actifs |
| `get_channel_info` | Opérateur | — | détails d'un canal |
| `get_queue_stats` | Opérateur | — | files d'attente (attente, membres, SLA) |
| `get_extension_status` | Opérateur | — | état des lignes (hints) |
| `get_cdr_report` | Superviseur | — | historique des appels (CDR) |
| `analyze_call_quality` | Superviseur | — | **MOS** + gigue via RTCP (modèle E, ITU-T G.107) |
| `get_trunk_utilization` | Superviseur | — | charge des trunks (actifs vs capacité) |
| `spy_channel` `listen` | Superviseur | — ¹ | écoute discrète (ChanSpy) |
| `spy_channel` `whisper`/`barge` | Admin | ✅ | audio injecté |
| `originate_call` | Admin | ✅ | initiation d'appel |
| `hangup_channel` | Admin | ✅ | libération |
| `redirect_call` | Admin | ✅ | transfert (aveugle ou supervisé) |

¹ `spy_channel` exige **toujours** `acknowledge_legal=true` et renvoie un
avertissement sur l'encadrement légal de l'écoute et de l'enregistrement.

Adaptateur **AMI (panoramisk)** avec garde de délai (aucune action bloquante),
CDR captés en temps réel via les événements `Cdr`, MOS estimé depuis
`PJSIPShowChannelStats` (repli `RTPAUDIOQOS`).

**Pipeline vocal S2S (Module 3)** — `src/voice/`, processus séparé
(`python -m src.voice.runner`). Audio via **ARI External Media** en `slin16` (RTP
bidirectionnel), endpointing **VAD**, **faster-whisper** (STT), **Ollama**
(LLM local, par défaut *Qwen2.5-3B-Instruct*, streamé), **Piper** (TTS, phrase
par phrase). Budget de latence **1–1,5 s** (`LATENCY_BUDGET_MS`) : l'audio de
réponse démarre à la première phrase, chaque tour est chronométré et
`within_budget` signale les dépassements. Sans LiveKit ni API tierce.

**Observabilité** — endpoint `GET /metrics` (Prometheus) sur le serveur MCP et le
pipeline vocal ; `res_prometheus` côté Asterisk ; dashboard Grafana
« Supervision Asterisk MCP » provisionné (`monitoring/grafana/`).

**Tests** — `pytest` (58) sur doublures en mémoire : RBAC, sanitizer, HITL
(accept/decline/cancel), cas d'usage bloquants, journal d'audit, métriques,
parsing AMI, avertissement légal, VAD, budget de latence. Charge : scénario
**SIPp** jusqu'à 50 canaux dans [`loadtest/`](loadtest/).

Schémas JSON des outils : [`docs/tool-schemas.json`](docs/tool-schemas.json)
(régénérables : `python scripts/export_tool_schemas.py`).

---

## Guide d'utilisation

### 1. Démarrer le socle (Keycloak + serveur MCP)

```bash
git clone https://github.com/M15lo16wa/supervision-asterisk-mcp.git
cd supervision-asterisk-mcp
cp .env.example .env                       # ⚠️ changer tous les mots de passe
docker compose up -d                       # PostgreSQL + Keycloak (realm importé) + serveur MCP
docker compose ps
```

| Service | URL |
|---|---|
| Keycloak | http://localhost:8080 (console `admin` / `KC_BOOTSTRAP_ADMIN_PASSWORD`) |
| Serveur MCP | http://localhost:8000/mcp |
| Métriques | http://localhost:8000/metrics |

### 2. Brancher Asterisk (conteneur externe)

Asterisk tourne dans **son propre conteneur** sur la machine (il n'est pas géré
par ce `docker-compose`). Le connecter au réseau du socle et le configurer :

```bash
# a) joindre le conteneur Asterisk au réseau du serveur MCP
docker network connect supervision-net <nom_conteneur_asterisk>

# b) créer les comptes mcp_ami / mcp_ari, activer HTTP+ARI, cdr_manager,
#    res_prometheus, ajouter postes 1001/1002 + file "support" + dialplan de test
./scripts/setup_test_asterisk.sh <nom_conteneur_asterisk>
```

Puis renseigner dans `.env` (si le hostname n'est pas `asterisk`) :

```dotenv
ASTERISK_HOST=<nom_conteneur_asterisk>
ASTERISK_AMI_USER=mcp_ami
ASTERISK_AMI_SECRET=changeme_ami
ASTERISK_ARI_BASE_URL=http://<nom_conteneur_asterisk>:8088
ASTERISK_ARI_USER=mcp_ari
ASTERISK_ARI_PASSWORD=changeme_ari
ASTERISK_DEFAULT_CONTEXT=mcp-internal
```

```bash
docker compose up -d mcp-server            # recharger avec les nouvelles variables
```

> Sans Asterisk joignable, le socle reste fonctionnel : les outils Module 2
> renvoient `{"status":"error","error":"asterisk_unavailable"}`.

Vérifier l'adaptateur AMI de bout en bout :

```bash
MCP_AUTH_MODE=static PYTHONPATH=mcp-server python scripts/live_test_asterisk.py
```

### 3. Obtenir un jeton

Comptes de test (realm `asterik`, mot de passe `admin`) :

| Utilisateur | Rôle | Peut |
|---|---|---|
| `operateur_demo`   | `operateur`   | lecture seule |
| `superviseur_demo` | `superviseur` | + analyse (CDR, MOS/RTCP, trunks) + écoute discrète |
| `admin_demo`       | `admin`       | + pilotage (origination, hangup, transfert, whisper/barge) |

```bash
./scripts/get_token.sh admin_demo | tee /tmp/tok.json      # access_token valable 5 min
```

### 4. Appeler les outils

**Via MCP Inspector :**

```bash
npx @modelcontextprotocol/inspector
# Transport: Streamable HTTP | URL: http://localhost:8000/mcp
# Authentication: Bearer Token | Token: <access_token>
```

**Via le client de fumée scripté :**

```bash
python scripts/smoke_mcp.py --token-file /tmp/tok.json --confirm oui
```

**Exemple d'appel HTTP direct (JSON-RPC 2.0) :**

```bash
TOKEN=$(python -c "import json;print(json.load(open('/tmp/tok.json'))['access_token'])")
curl -s http://localhost:8000/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_active_channels","arguments":{}}}'
```

### 5. Pipeline vocal S2S (optionnel)

Nécessite un **Ollama** joignable (conteneur externe) et les extras `voice` :

```bash
docker network connect supervision-net <conteneur_ollama>        # si en conteneur
docker compose exec -T mcp-server true                           # (socle déjà up)

cd mcp-server
pip install -e ".[voice]"
export OLLAMA_BASE_URL=http://<conteneur_ollama>:11434
export ASTERISK_ARI_BASE_URL=http://<conteneur_asterisk>:8088
export ASTERISK_ARI_USER=mcp_ari ASTERISK_ARI_PASSWORD=changeme_ari
python -m src.voice.runner
```

Côté Asterisk, le dialplan `700 => Stasis(mcp-voice)` route l'appel vers le
pipeline. `python -m src.voice.runner` expose ses métriques sur `:9092/metrics`.

### 6. Observabilité (optionnel)

Prometheus et Grafana tournent en **conteneurs externes**. Config prête :

* Prometheus — monter [`monitoring/prometheus/prometheus.yml`](monitoring/prometheus/prometheus.yml)
  (jobs `mcp-server`, `voice-pipeline`, `asterisk`, `ollama`) ; joindre le
  conteneur à `supervision-net`.
* Grafana — monter [`monitoring/grafana/provisioning`](monitoring/grafana/provisioning)
  et [`monitoring/grafana/dashboards`](monitoring/grafana/dashboards) → datasource
  + dashboard « Supervision Asterisk MCP » chargés automatiquement.

```bash
docker network connect supervision-net <conteneur_prometheus>
docker network connect supervision-net <conteneur_grafana>
```

### 7. Charge (SIPp)

```bash
./loadtest/run_loadtest.sh <asterisk_ip> 701 50 5 15000     # 50 canaux
```

Paliers `10 → 25 → 50`, procédure et critères : [`loadtest/README.md`](loadtest/README.md).

### 8. Développement & tests

```bash
cd mcp-server
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
export PYTHONPATH=$(pwd)
python -m src.main                          # serveur MCP (Modules 1 & 2)

pytest -q                                   # 58 tests
ruff check src tests
```

En mode `static` (sans Keycloak), trois jetons opaques suffisent :
`dev-operateur`, `dev-superviseur`, `dev-admin` (`MCP_AUTH_MODE=static`,
personnalisables via `MCP_STATIC_TOKENS`).

---

## Composants externes

Ce dépôt n'orchestre que **PostgreSQL + Keycloak + serveur MCP**. Les briques
suivantes sont fournies par des **conteneurs séparés déjà présents sur la
machine**, connectés au réseau `supervision-net` :

| Brique | Rôle | Config fournie ici |
|---|---|---|
| **Asterisk 20/22** | PBX (AMI/ARI/Stasis) | `asterisk/config/` + `scripts/setup_test_asterisk.sh` |
| **Ollama** | LLM local du pipeline S2S | `OLLAMA_MODEL` (défaut `qwen2.5:3b-instruct`) |
| **Prometheus** | collecte des métriques | `monitoring/prometheus/prometheus.yml` |
| **Grafana** | dashboards | `monitoring/grafana/` (datasource + dashboard) |

```bash
docker network connect supervision-net <conteneur>
```

---

## Architecture

Hexagonale (ports & adapters) :

```
interfaces (outils MCP, /metrics)     src/interfaces/
      │
application (cas d'usage)             src/application/
      │
domain (entités + ports)             src/domain/          ← sans framework
      ▲                    ▲
adapters (Asterisk AMI/ARI)  voice (STT/LLM/TTS, External Media)
src/adapters/                src/voice/

transverses : security (auth, RBAC, sanitizer) · audit · observability
```

```
mcp-server/
  src/
    domain/         entities.py, ports.py, exceptions.py
    application/     un cas d'usage par fichier
    adapters/       asterisk_gateway.py (panoramisk/AMI), hitl_confirmation.py
    interfaces/     mcp_tools.py  (11 outils + route /metrics)
    security/       auth.py, rbac.py, sanitizer.py
    audit/          journal.py   (JSON Lines)
    observability/  metrics.py, http.py
    voice/          external_media.py, audio.py, stt.py, llm.py, tts.py,
                    pipeline.py, runner.py
    config.py, main.py
  tests/            58 tests (pytest)
  Dockerfile, Dockerfile.voice, pyproject.toml
asterisk/config/    pjsip, extensions, manager, ari, queues, cdr_manager,
                    prometheus, http, rtp, modules
keycloak/realm-export/asterik-realm.json    (client mcp-server + 3 users)
monitoring/          prometheus.yml, grafana/ (datasource + dashboard)
loadtest/           scénario SIPp + procédure
scripts/            get_token, smoke_mcp, setup_test_asterisk, live_test_asterisk,
                    export_tool_schemas, ollama_pull
docs/               tool-schemas.json, mcp-inspector.md
```

---

## Module 2 — Configuration Asterisk

`asterisk/config/` (appliqué par `scripts/setup_test_asterisk.sh` ou monté en
`/etc/asterisk`) :

| Fichier | Contenu |
|---|---|
| `pjsip.conf` | transport UDP ; postes `1001`, `1002`, superviseur `1099` (codec `slin16`) ; **trunk sortant `trunk-out`** (registration / auth / identify) |
| `extensions.conf` | `_10XX` interne ; `hint` (→ `get_extension_status`) ; `700 => Stasis(mcp-voice)` ; `701 => Echo()` ; `800 => Queue(support)` ; `_0. => Dial(...@trunk-out)` ; contexte `from-trunk` |
| `queues.conf` | file `support` (2 membres) → `get_queue_stats` |
| `manager.conf` + `manager.d/` | AMI `mcp_ami` — `read = system,call,cdr,dialplan,reporting` / `write = call,reporting,command` |
| `ari.conf` | utilisateur ARI `mcp_ari` |
| `cdr_manager.conf` | CDR en événements AMI `Cdr` → bufferisés (`get_cdr_report`) |
| `prometheus.conf` | `res_prometheus` (Basic Auth) → `/metrics` |
| `http.conf`, `rtp.conf`, `modules.conf` | serveur HTTP ARI, plage RTP `10000-10099`, modules requis |

---

## Référence de configuration (`.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `MCP_AUTH_MODE` | `keycloak` | `keycloak` \| `static` |
| `MCP_PUBLIC_URL` | `http://localhost:8000` | métadonnée OAuth de ressource protégée (PKCE) |
| `MCP_HITL_MODE` | `pilotage` | `pilotage` \| `all` |
| `AUDIT_LOG_PATH` | `logs/audit.jsonl` | journal d'audit (volume `mcp_audit` en Docker) |
| `KEYCLOAK_REALM` / `KEYCLOAK_CLIENT_ID` | `asterik` / `mcp-server` | doivent correspondre au realm importé |
| `ASTERISK_HOST` / `ASTERISK_AMI_PORT` | `asterisk` / `5038` | conteneur Asterisk externe (joint à `supervision-net`) |
| `ASTERISK_AMI_USER` / `ASTERISK_AMI_SECRET` | `mcp_ami` / `changeme_ami` | compte AMI |
| `ASTERISK_ARI_BASE_URL` / `ASTERISK_ARI_USER` / `ASTERISK_ARI_PASSWORD` | `http://asterisk:8088` / `mcp_ari` / `changeme_ari` | ARI |
| `ASTERISK_DEFAULT_CONTEXT` | `internal` | contexte des originations |
| `ASTERISK_TRUNKS` / `ASTERISK_TRUNK_MAX_<NOM>` | `trunk-out` / — | trunks suivis par `get_trunk_utilization` |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | `http://localhost:11434` / `qwen2.5:3b-instruct` | LLM S2S |
| `STT_MODEL` / `STT_DEVICE` / `TTS_VOICE` | `small` / `cpu` / `fr_FR-siwis-medium` | faster-whisper / Piper |
| `LATENCY_BUDGET_MS` | `1500` | budget de latence S2S |
| `VOICE_RTP_PORT` / `VOICE_METRICS_PORT` | `40000` / `9092` | pipeline vocal |

---

## Stack

Python 3.10+ · FastMCP 3.x · Keycloak 26 · panoramisk (AMI) · aiohttp (ARI) ·
Asterisk 20/22 · faster-whisper · Ollama (Qwen2.5) · Piper · prometheus-client ·
Prometheus + Grafana · SIPp.

## Avertissement — développement local

Configuration destinée au **développement**. Avant tout déploiement : changer
**tous** les mots de passe (`admin/admin`, `POSTGRES_PASSWORD`, secrets AMI/ARI,
secret client Keycloak, `ASTERISK_METRICS_PASSWORD`), activer **HTTPS**,
remplacer `start-dev` par `start` pour Keycloak, restreindre l'accès réseau à
`/metrics` et au journal d'audit.
