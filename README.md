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

**Outillage Asterisk (Module 2)** — **12 outils** de production, répartis
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
| `llm_chat` | Superviseur | — ² | prompts au **LLM local (Ollama)**, contexte Asterisk optionnel |
| `spy_channel` `listen` | Superviseur | — ¹ | écoute discrète (ChanSpy) |
| `spy_channel` `whisper`/`barge` | Admin | ✅ | audio injecté |
| `originate_call` | Admin | ✅ | initiation d'appel |
| `hangup_channel` | Admin | ✅ | libération |
| `redirect_call` | Admin | ✅ | transfert (aveugle ou supervisé) |

¹ `spy_channel` exige **toujours** `acknowledge_legal=true` et renvoie un
avertissement sur l'encadrement légal de l'écoute et de l'enregistrement.

² `llm_chat(prompt, context=["channels","cdr:10","queues","trunks","extensions"], …)`
interroge Ollama avec le système de prompts de supervision ; les données
Asterisk annexées sont traitées comme **contenu non fiable** (enveloppe
`DataSanitizer`, jamais comme instruction). Rôle `superviseur` minimum.

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
pipeline vocal ; `res_prometheus` côté Asterisk (scrapé par Prometheus via
`ASTERISK_METRICS_PASSWORD`, cf. `monitoring/.env.example`) ; dashboard Grafana
« Supervision Asterisk MCP » provisionné (`monitoring/grafana/`) — MCP, voix,
Asterisk et LLM superviseur.

**Tests** — `pytest` (69) sur doublures en mémoire : RBAC, sanitizer, HITL
(accept/decline/cancel), cas d'usage bloquants, journal d'audit, métriques,
parsing AMI, avertissement légal, VAD, budget de latence, outil `llm_chat`.
Charge : scénario **SIPp** jusqu'à 50 canaux dans [`loadtest/`](loadtest/).

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
par ce `docker-compose`). Le connecter au réseau du socle **avec l'alias
`asterisk`** (c'est le nom résolu par le serveur MCP et par Prometheus) puis le
configurer :

```bash
# a) joindre le conteneur Asterisk au réseau du socle, alias `asterisk`
docker network connect --alias asterisk supervision-net <nom_conteneur_asterisk>

# b) créer les comptes mcp_ami / mcp_ari, activer HTTP+ARI, cdr_manager,
#    res_prometheus, ajouter postes 1001/1002 + file "support" + dialplan de test
./scripts/setup_test_asterisk.sh <nom_conteneur_asterisk>
```

> Sans l'alias `asterisk`, le serveur MCP ne résout pas `ASTERISK_HOST`/`ARI_BASE_URL`
> et Prometheus ne résout pas la cible `asterisk:8088` — **ajoutez-le avec l'alias**.

Le script affiche en fin d'exécution les identifiants AMI/ARI/metrics déployés.
Reporter ces valeurs dans `.env` (si le hostname ou les secrets diffèrent) :

```dotenv
ASTERISK_HOST=asterisk
ASTERISK_AMI_USER=mcp_ami
ASTERISK_AMI_SECRET=msS0swB2PjjVtfJkEFJLpMYxp1VngJnXvkSOhRVB290
ASTERISK_ARI_BASE_URL=http://asterisk:8088
ASTERISK_ARI_USER=mcp_ari
ASTERISK_ARI_PASSWORD=780HNl27IF2gKx2aBy3B7CeKbqXHLyVg8sAUS9x8Qqw
ASTERISK_DEFAULT_CONTEXT=mcp-internal    # contexte du dialplan de test
```

Les valeurs ci-dessus sont celles déployées par
`scripts/setup_test_asterisk.sh` et présentes dans
[`asterisk/config/`](asterisk/config/) — **développement uniquement**, à
changer en production (voir l'avertissement en fin de document).

```bash
docker compose up -d mcp-server            # recharger avec les nouvelles variables
```

> Sans Asterisk joignable, le socle reste fonctionnel : les outils Module 2
> renvoient `{"status":"error","error":"asterisk_unavailable"}`.

Vérifier l'adaptateur AMI de bout en bout (sans appeler le serveur MCP ;
lit `ASTERISK_HOST`/`ASTERISK_AMI_USER`/`ASTERISK_AMI_SECRET` de l'environnement
ou du `.env` racine) :

```bash
PYTHONPATH=mcp-server python scripts/live_test_asterisk.py
```

### 3. Obtenir un jeton

Comptes de test (realm `asterisk`, mot de passe `admin`) :

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

Nécessite un **Ollama** joignable et les extras `voice`. Deux options pour
Ollama : le conteneur de la stack `monitoring/` (recommandée, section 6) ou un
conteneur externe joint au réseau. Dans les deux cas, il doit être résolvable
sous le nom `ollama` sur `supervision-net`.

```bash
# a) Ollama de la stack monitoring -> déjà sur supervision-net avec l'alias `ollama`
#    (Ollama externe -> docker network connect --alias ollama supervision-net <conteneur_ollama>)

# b) télécharger le modèle utilisé par la voix (qwen2.5:3b-instruct)
./scripts/ollama_pull.sh

# c) installer les extras lourds (faster-whisper, piper) sur l'hôte du pipeline
cd mcp-server
pip install -e ".[voice]"

# d) variables d'accès (valeurs du .env racine / de la config Asterisk déployée)
export OLLAMA_BASE_URL=http://ollama:11434
export ASTERISK_ARI_BASE_URL=http://asterisk:8088
export ASTERISK_ARI_USER=mcp_ari
export ASTERISK_ARI_PASSWORD=780HNl27IF2gKx2aBy3B7CeKbqXHLyVg8sAUS9x8Qqw

# e) lancer le pipeline (métriques sur :9092/metrics)
python -m src.voice.runner
```

Côté Asterisk, le dialplan `700 => Stasis(mcp-voice)` route l'appel vers le
pipeline. Le runner doit être **résolvable sous le nom `voice-pipeline`** sur
`supervision-net` pour que le job Prometheus correspondant soit « UP » :
déployez-le dans un conteneur nommé `voice-pipeline` (ou joint avec cet alias),
sinon ce job reste simplement « down » sans impacter les autres.

### 6. Observabilité — Ollama, Prometheus, Grafana

Ces trois services sont orchestrés par une stack **dédiée et autonome**,
séparée du socle (`monitoring/docker-compose.yml`), mais rattachée au même
réseau Docker `supervision-net`.

```bash
cd monitoring
cp .env.example .env          # ajuster les ports/identifiants si besoin
docker compose up -d
docker compose ps
```

| Service | URL |
|---|---|
| Ollama | http://localhost:11434 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin / `GRAFANA_ADMIN_PASSWORD`) |

Prometheus scrute automatiquement (`monitoring/prometheus/prometheus.yml`) :
- `asterisk:8088` — `res_prometheus`, Basic Auth `prometheus` / `ASTERISK_METRICS_PASSWORD`
  (le mot de passe de `monitoring/.env` doit être identique à celui d'Asterisk :
  `asterisk/config/prometheus.conf` — voir section 2) ;
- `mcp-server:8000` — `/metrics` exposé par le serveur MCP (conteneur du compose racine) ;
- `ollama:11434` — métriques natives d'Ollama (alias `ollama` déjà déclaré dans
  `monitoring/docker-compose.yml`) ;
- `voice-pipeline:9092` — pipeline vocal S2S (cible UP seulement si le runner est
  résolvable sous le nom `voice-pipeline`, cf. section 5).

> **Résolution de noms** : les cibles `asterisk` et `ollama` sont des *aliases*
> Docker sur `supervision-net`. L'alias `ollama` est déclaré dans le compose de
> la stack ; l'alias `asterisk` s'ajoute à la connexion du conteneur Asterisk
> (`docker network connect --alias asterisk supervision-net <nom_conteneur>`).

Grafana charge automatiquement la datasource Prometheus et les dashboards du
dossier `monitoring/grafana/dashboards/` via provisioning.

**Intégration Keycloak (optionnelle) :** pour que Grafana délègue son
authentification au même Keycloak que le reste du système (cohérent avec le
rapport §6.4), créez un client OIDC `grafana` dans Keycloak (confidentiel,
redirect URI `http://localhost:3000/login/generic_oauth`), puis dans
`monitoring/.env` :

```dotenv
GRAFANA_OIDC_ENABLED=true
GRAFANA_OIDC_CLIENT_SECRET=<secret_reel_copie_depuis_keycloak>
```

```bash
docker compose up -d grafana    # recharger avec la nouvelle config
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

pytest -q                                   # 69 tests
ruff check src tests
```

En mode `static` (sans Keycloak), trois jetons opaques suffisent :
`dev-operateur`, `dev-superviseur`, `dev-admin` (`MCP_AUTH_MODE=static`,
personnalisables via `MCP_STATIC_TOKENS`).

---
## Composants externes et stacks additionnelles

Le socle (`docker-compose.yml` racine) n'orchestre que **PostgreSQL +
Keycloak + serveur MCP**. Deux catégories de briques complètent le système :

### Reste externe (conteneur déjà présent sur la machine, à connecter manuellement)

| Brique | Rôle | Config fournie ici |
|---|---|---|
| **Asterisk 20/22** | PBX (AMI/ARI/Stasis) | `asterisk/config/` + `scripts/setup_test_asterisk.sh` |

```bash
docker network connect --alias asterisk supervision-net <nom_conteneur_asterisk>
```

### Gérées par une stack dédiée (`monitoring/docker-compose.yml`)

| Brique | Rôle | Config fournie ici |
|---|---|---|
| **Ollama** | LLM local du pipeline S2S et des prompts superviseur | `monitoring/.env` (`OLLAMA_MODEL`, défaut `qwen2.5:3b-instruct`) |
| **Prometheus** | collecte des métriques | `monitoring/prometheus/prometheus.yml` |
| **Grafana** | dashboards | `monitoring/grafana/provisioning/` (datasource + dashboards) |

```bash
cd monitoring
cp .env.example .env
docker compose up -d
```

Cette stack se rattache au réseau `supervision-net` créé par le socle
(`external: true` dans `monitoring/docker-compose.yml`) — lancez donc toujours
le compose racine **avant** celui de `monitoring/`.

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
    application/     un cas d'usage par fichier (dont llm_chat.py)
    adapters/       asterisk_gateway.py (panoramisk/AMI), hitl_confirmation.py,
                    ollama_llm.py (LLM local partagé voix + superviseur)
    interfaces/     mcp_tools.py  (12 outils + route /metrics)
    security/       auth.py, rbac.py, sanitizer.py
    audit/          journal.py   (JSON Lines)
    observability/  metrics.py, http.py
    voice/          external_media.py, audio.py, stt.py, llm.py, tts.py,
                    pipeline.py, runner.py
    config.py, main.py
  tests/            69 tests (pytest)
  Dockerfile, Dockerfile.voice, pyproject.toml
asterisk/config/    pjsip, extensions, manager, ari, queues, cdr_manager,
                    prometheus, http, rtp, modules
keycloak/realm-export/asterisk-realm.json    (client mcp-server + 3 users)
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
| `KEYCLOAK_REALM` / `KEYCLOAK_CLIENT_ID` | `asterisk` / `mcp-server` | doivent correspondre au realm importé |
| `ASTERISK_HOST` / `ASTERISK_AMI_PORT` | `asterisk` / `5038` | conteneur Asterisk externe (joint à `supervision-net`) |
| `ASTERISK_AMI_USER` / `ASTERISK_AMI_SECRET` | `mcp_ami` / `changeme_ami` | compte AMI — **même valeur** que `manager.conf` / celle affichée par `setup_test_asterisk.sh` |
| `ASTERISK_ARI_BASE_URL` / `ASTERISK_ARI_USER` / `ASTERISK_ARI_PASSWORD` | `http://asterisk:8088` / `mcp_ari` / `changeme_ari` | ARI — mot de passe aligné sur `ari.conf` |
| `ASTERISK_DEFAULT_CONTEXT` | `mcp-internal` | contexte des originations (contexte `mcp-internal` déployé par `setup_test_asterisk.sh`) |
| `ASTERISK_TRUNKS` / `ASTERISK_TRUNK_MAX_<NOM>` | `trunk-out` / — | trunks suivis par `get_trunk_utilization` |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | `http://localhost:11434` (`http://ollama:11434` dans `.env` Docker) / `qwen2.5:3b-instruct` | LLM vocal S2S et prompts superviseur (`llm_chat`) |
| `LLM_TEMPERATURE` / `LLM_NUM_PREDICT` / `LLM_TIMEOUT_S` | `0.3` / `512` / `60` | LLM superviseur (`llm_chat`) |
| `STT_MODEL` / `STT_DEVICE` / `TTS_VOICE` | `small` / `cpu` / `fr_FR-siwis-medium` | faster-whisper / Piper |
| `LATENCY_BUDGET_MS` | `1500` | budget de latence S2S |
| `VOICE_RTP_PORT` / `VOICE_METRICS_PORT` | `40000` / `9092` | pipeline vocal |

---

## Stack

Python 3.10+ · FastMCP 3.x · Keycloak 26 · panoramisk (AMI) · aiohttp (ARI) ·
Asterisk 20/22 · faster-whisper · Ollama (Qwen2.5) · Piper · prometheus-client ·
Prometheus + Grafana · SIPp.

## Avertissement — développement local

Configuration destinée au **développement** (les secrets `asterisk/config/`,
la valeur `admin/admin` et `admin/passer` etc. sont publics dans le dépôt).
Avant tout déploiement : changer **tous** les mots de passe
(`admin/admin`, `POSTGRES_PASSWORD`, `KC_BOOTSTRAP_ADMIN_PASSWORD`, secrets
AMI/ARI `ASTERISK_AMI_SECRET`/`ASTERISK_ARI_PASSWORD`,
`ASTERISK_METRICS_PASSWORD`, secret client Keycloak), puis les aligner dans
`.env`, `asterisk/config/*.conf` et `monitoring/.env`. Activer **HTTPS**,
remplacer `start-dev` par `start` pour Keycloak, restreindre l'accès réseau à
`/metrics` et au journal d'audit.
