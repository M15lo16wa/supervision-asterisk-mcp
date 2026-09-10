# Supervision Asterisk via MCP

Serveur **MCP (Model Context Protocol)** qui expose les interfaces **ARI** et
**AMI** d'un PBX **Asterisk** à un LLM, pour une supervision temps réel, l'analyse
des tickets de taxation (**CDR**) et le pilotage d'appels **sous contrôle humain
obligatoire (HITL)**. Un second palier ajoute un **assistant vocal
Speech-to-Speech (S2S) 100 % local**.

Développement direct sur **FastMCP** + clients **ARI/AMI natifs** (panoramisk) —
aucune dépendance payante, aucun framework tiers (pas de LiveKit), pas d'OpenAPI.

---

## 1. Correspondance au cahier des charges

### Partie A — Socle commun

| § | Exigence | Statut | Implémentation |
|---|---|---|---|
| A.5 | Test/démo sans surcoût via **MCP Inspector**, hôtes locaux gratuits | ✅ | [`docs/mcp-inspector.md`](docs/mcp-inspector.md) ; outils à **paramètres nets** (types simples, `ctx`/`token` injectés) |
| A.6 | **RBAC** adossé à des **JWT Keycloak (OIDC)** | ✅ | `src/security/{auth,rbac}.py`, realm `asterik`, hiérarchie `Opérateur < Superviseur < Admin` |
| A.6 | **OAuth 2.1 + PKCE** | ✅ | `MCP_PUBLIC_URL` → métadonnée de ressource protégée (RFC 9728) → le client MCP découvre Keycloak et enchaîne le flux PKCE |
| A.6 | **Token passthrough interdit** | ✅ | le JWT n'authentifie que la session MCP ; Asterisk a ses propres comptes AMI/ARI |
| A.6 | **Consentement explicite** par exécution (≠ `destructiveHint`) | ✅ | HITL via *elicitation* MCP sur le pilotage ; sur **tous** les outils si `MCP_HITL_MODE=all` |
| A.6 | **Sorties = entrées non fiables** (anti-injection) | ✅ | `src/security/sanitizer.py` — enveloppe + neutralisation de motifs |
| A.6 | **Journal d'audit** de toutes les actions | ✅ | `src/audit/` — JSON Lines : acteur, outil, paramètres (secrets masqués), résultat, `request_id`, horodatage |
| A.7 | Dépôt public + **Dockerfile** + **docker-compose.yml** | ✅ | `mcp-server/Dockerfile`, `mcp-server/Dockerfile.voice`, `docker-compose.yml` |
| A.7 | **Schémas JSON** des outils | ✅ | [`docs/tool-schemas.json`](docs/tool-schemas.json) (`scripts/export_tool_schemas.py`) |
| A.7 | Environnement de démo **MCP Inspector** (hors OpenAPI/Swagger) | ✅ | [`docs/mcp-inspector.md`](docs/mcp-inspector.md) |
| A.7 | Rapport PDF + support PPT | ⛔ | hors dépôt (livrables de soutenance) |

### Partie B — Spécifique Supervision Asterisk

| § | Exigence | Statut | Implémentation |
|---|---|---|---|
| B.2 | Serveur MCP embarquant **≥ 5 outils de production** | ✅ | **11 outils** (voir §5) |
| B.3 | **Asterisk 22 LTS**, PJSIP (trunks/extensions/dialplan), Stasis | ✅ | image `andrius/asterisk:22.5.0`, `asterisk/config/` (trunk `trunk-out`, postes, `Stasis(mcp-voice)`) |
| B.3 | ARI (REST + WebSocket) + AMI (**panoramisk**), **moindre privilège** | ✅ | `src/adapters/asterisk_gateway.py`, `src/voice/external_media.py`, comptes `mcp_ami`/`mcp_ari` à droits ciblés |
| B.4 | Outils **lecture / analyse / pilotage** | ✅ | §5 — noms conformes à la spec |
| B.5 | Pipeline **S2S local** : ARI External Media slin16 → faster-whisper → Ollama → Piper, **latence 1–1,5 s** | ✅ | `src/voice/` — budget de latence, TTS par phrase, LLM streamé |
| B.6 | RBAC sectorisé Admin / Superviseur / Opérateur | ✅ | `src/security/rbac.py` |
| B.6 | `spy_channel` : **avertissements légaux** (consentement, enregistrement) | ✅ | paramètre obligatoire `acknowledge_legal`, notice RGPD renvoyée + auditée |
| B.7 | Tests de charge **jusqu'à 50 canaux (SIPp)** + softphones | ✅ | [`loadtest/`](loadtest/) (scénario SIPp + procédure) |
| B.7 | Démo live : supervision, pilotage validé, appel S2S | ✅ | [`docs/mcp-inspector.md`](docs/mcp-inspector.md) |

**Validation réelle** : l'ensemble a été exécuté contre un conteneur Asterisk
20.6 vivant — les 11 outils, le journal d'audit, l'avertissement légal de
`spy_channel`, la chaîne *client MCP → HTTP → auth → RBAC → HITL → AMI*, ainsi
que Prometheus (scrape `mcp-server` + `asterisk`) et Grafana (dashboard). Le
`docker-compose` cible Asterisk **22**. Non mesurés faute d'infra dédiée :
latence S2S de bout en bout, run SIPp 50 canaux (scénario prêt).

---

## 2. Architecture

Architecture hexagonale (ports & adapters) :

```
interfaces (outils MCP, /metrics)         src/interfaces/
      │
application (cas d'usage)                 src/application/
      │
domain (entités + ports, sans framework)  src/domain/
      ▲                    ▲
adapters (Asterisk AMI/ARI)  voice (STT/LLM/TTS, External Media)
src/adapters/                src/voice/

transverses : security (auth, RBAC, sanitizer) · audit · observability
```

```
mcp-server/
  src/
    domain/         entities.py, ports.py, exceptions.py
    application/    un cas d'usage par fichier
    adapters/       asterisk_gateway.py (panoramisk/AMI), hitl_confirmation.py
    interfaces/     mcp_tools.py  (11 outils + route /metrics)
    security/       auth.py (Keycloak/JWT + mode static), rbac.py, sanitizer.py
    audit/          journal.py   (JSON Lines)
    observability/  metrics.py (Prometheus), http.py
    voice/          external_media.py, audio.py, stt.py, llm.py, tts.py,
                    pipeline.py, runner.py
    config.py, main.py
  tests/            58 tests (pytest) — doublures en mémoire
  Dockerfile, Dockerfile.voice, pyproject.toml
asterisk/config/    pjsip, extensions, manager, ari, queues, cdr_manager,
                    prometheus, http, rtp, modules
keycloak/realm-export/asterik-realm.json   (client mcp-server + 3 users)
monitoring/         prometheus.yml, grafana/ (datasource + dashboard)
loadtest/           scénario SIPp + procédure (B.7)
scripts/            get_token, smoke_mcp, setup_test_asterisk, live_test_asterisk,
                    export_tool_schemas, ollama_pull
docs/               tool-schemas.json, mcp-inspector.md
```

---

## 3. Installation & démarrage (Docker)

Pré-requis : Docker + Docker Compose v2.

```bash
git clone https://github.com/M15lo16wa/supervision-asterisk-mcp.git
cd supervision-asterisk-mcp
cp .env.example .env          # ⚠️ changer tous les mots de passe
```

### Socle (Module 1) — Keycloak + serveur MCP

```bash
docker compose up -d
```

| Service | URL | Accès |
|---|---|---|
| Keycloak | http://localhost:8080 | console `admin` / *(`KC_BOOTSTRAP_ADMIN_PASSWORD`)* |
| Serveur MCP | http://localhost:8000/mcp | JWT Bearer (voir §4) |
| Métriques MCP | http://localhost:8000/metrics | — |

### Profils optionnels

```bash
docker compose --profile asterisk up -d      # + Asterisk 22 (Module 2)
docker compose --profile voice up -d         # + Ollama (pull auto du modèle) + pipeline S2S (Module 3)
docker compose --profile monitoring up -d    # + Prometheus + Grafana

# tout à la fois :
docker compose --profile asterisk --profile voice --profile monitoring up -d
```

| Service (profil) | URL |
|---|---|
| Asterisk ARI (`asterisk`) | http://localhost:8088 — SIP `udp/5060`, AMI `5038`, RTP `10000-10099/udp` |
| Ollama (`voice`) | http://localhost:11434 |
| Pipeline vocal — métriques (`voice`) | http://localhost:9092/metrics |
| Prometheus (`monitoring`) | http://localhost:9090 |
| Grafana (`monitoring`) | http://localhost:3000 — `admin` / `admin` |

Avec le profil `voice`, le conteneur `ollama-init` télécharge `OLLAMA_MODEL`
(défaut `qwen2.5:3b-instruct`) puis se termine. Manuellement :
`./scripts/ollama_pull.sh [modèle]`.

### Sans Keycloak (démo rapide)

```bash
MCP_AUTH_MODE=static docker compose up -d
```

Trois jetons opaques remplacent Keycloak : `dev-operateur`, `dev-superviseur`,
`dev-admin` (personnalisables via `MCP_STATIC_TOKENS`). **Jamais en production.**

---

## 4. Utilisation

### 4.1 Obtenir un jeton (mode Keycloak)

Comptes de test (realm `asterik`, mot de passe `admin`) :

| Utilisateur | Rôle | Autorisations |
|---|---|---|
| `operateur_demo`   | `operateur`   | lecture seule |
| `superviseur_demo` | `superviseur` | + analyse (CDR, MOS/RTCP, trunks) + écoute discrète |
| `admin_demo`       | `admin`       | + pilotage (origination, hangup, transfert, whisper/barge) |

```bash
./scripts/get_token.sh admin_demo | tee /tmp/tok.json
# -> { "access_token": "eyJ...", ... }   (valable 5 min)
```

Client confidentiel Keycloak : `mcp-server` / secret
`dev-only-mcp-server-secret-CHANGE-ME` (**à régénérer**).

### 4.2 MCP Inspector (démo — livrable A.7)

```bash
npx @modelcontextprotocol/inspector      # ouvre http://localhost:6274
```

| Champ | Valeur |
|---|---|
| Transport Type | `Streamable HTTP` |
| URL | `http://localhost:8000/mcp` |
| Authentication | `Bearer Token` |
| Token | `access_token` (Keycloak) ou `dev-admin` (mode static) |

Parcours de démonstration complet (supervision → pilotage avec validation
humaine → écoute) : [`docs/mcp-inspector.md`](docs/mcp-inspector.md).

### 4.3 Client de fumée scripté

```bash
./scripts/get_token.sh admin_demo > /tmp/tok.json
python scripts/smoke_mcp.py --token-file /tmp/tok.json --confirm oui
```

---

## 5. Outils MCP (B.4)

11 outils. Schémas JSON complets : [`docs/tool-schemas.json`](docs/tool-schemas.json).

| Outil | Rôle min. | HITL | Catégorie |
|---|---|---|---|
| `list_active_channels` | Opérateur | — | lecture — canaux actifs |
| `get_channel_info` | Opérateur | — | lecture — détails d'un canal |
| `get_queue_stats` | Opérateur | — | lecture — files d'attente (attente, membres, SLA) |
| `get_extension_status` | Opérateur | — | lecture — état des lignes (hints) |
| `get_cdr_report` | Superviseur | — | analyse — historique des appels |
| `analyze_call_quality` | Superviseur | — | analyse — **MOS** + gigue via RTCP (modèle E, ITU-T G.107) |
| `get_trunk_utilization` | Superviseur | — | analyse — charge des trunks (actifs vs capacité) |
| `spy_channel` (`listen`) | Superviseur | — ¹ | analyse — écoute discrète ChanSpy |
| `spy_channel` (`whisper` / `barge`) | Admin | ✅ | pilotage — audio injecté |
| `originate_call` | Admin | ✅ | pilotage — initiation d'appel |
| `hangup_channel` | Admin | ✅ | pilotage — libération |
| `redirect_call` | Admin | ✅ | pilotage — transfert (aveugle ou supervisé) |

¹ `spy_channel` exige **toujours** `acknowledge_legal=true` (encadrement légal de
l'écoute et de l'enregistrement) et renvoie un avertissement RGPD. Toute écoute
est tracée dans le journal d'audit.

**Chaîne appliquée à chaque appel d'outil** (`src/interfaces/mcp_tools.py`) :

1. **RBAC** — `require_role(token, …)` en première ligne (hiérarchie ascendante).
2. **Consentement humain** — *elicitation* MCP (pas une annotation `destructiveHint`)
   sur le pilotage ; sur tous les outils si `MCP_HITL_MODE=all`.
3. **Assainissement** — la sortie (données Asterisk = non fiables) passe par le `DataSanitizer`.
4. **Audit** — `tool_call` / `rbac_denied` / `hitl_denied` / `channel_spy` écrits en JSON Lines.
5. **Métriques** — compteurs et histogrammes Prometheus.

---

## 6. Sécurité (A.6)

| Mécanisme | Détail |
|---|---|
| **Authentification** | JWT Keycloak (OIDC, RS256, JWKS). `MCP_PUBLIC_URL` publie la métadonnée de ressource protégée (RFC 9728) → **OAuth 2.1 + PKCE** côté client MCP. Mode `static` pour le dev. |
| **RBAC** | `realm_access.roles` du JWT ; `Opérateur < Superviseur < Admin` (un rôle hérite des permissions inférieures). |
| **Pas de token passthrough** | le JWT n'est jamais transmis à Asterisk. AMI (`mcp_ami`) et ARI (`mcp_ari`) ont leurs propres identifiants à droits ciblés. |
| **Consentement humain** | *elicitation* MCP obligatoire avant toute action de pilotage ; `MCP_HITL_MODE=all` l'étend à la lecture. |
| **Anti-injection indirecte** | toute donnée externe est encapsulée `[UNTRUSTED DATA] … [END UNTRUSTED DATA]` et les motifs suspects sont neutralisés. |
| **Journal d'audit** | `AUDIT_LOG_PATH` (défaut `logs/audit.jsonl`, volume `mcp_audit`). Une ligne JSON par événement : `event`, `actor`, `client_id`, `tool`, `params` (secrets masqués, valeurs tronquées), `outcome`, `detail`, `request_id`, `ts` (UTC). Rotation par taille. |

```bash
docker compose exec mcp-server tail -f /app/logs/audit.jsonl
```

---

## 7. Module 2 — Infrastructure Asterisk

`asterisk/config/` (monté en lecture seule dans le conteneur `asterisk`) :

| Fichier | Contenu |
|---|---|
| `pjsip.conf` | transport UDP ; postes `1001`, `1002`, superviseur `1099` (codec `slin16`) ; **trunk sortant `trunk-out`** (registration / auth / identify) |
| `extensions.conf` | appels internes `_10XX` ; `hint` (→ `get_extension_status`) ; `700 => Stasis(mcp-voice)` ; `701 => Echo()` ; `800 => Queue(support)` ; `_0. => Dial(...@trunk-out)` ; contexte `from-trunk` |
| `queues.conf` | file `support` (2 membres) → `get_queue_stats` |
| `manager.conf` + `manager.d/` | compte AMI `mcp_ami` — `read = system,call,cdr,dialplan,reporting` / `write = call,reporting,command` |
| `ari.conf` | utilisateur ARI `mcp_ari` |
| `cdr_manager.conf` | CDR émis en événements AMI `Cdr` → bufferisés côté serveur (`get_cdr_report`) |
| `prometheus.conf` | `res_prometheus` (Basic Auth) → `/metrics` |
| `http.conf`, `rtp.conf`, `modules.conf` | serveur HTTP ARI, plage RTP `10000-10099`, modules requis |

**Qualité d'appel** — `analyze_call_quality` lit `PJSIPShowChannelStats`
(repli : variables `RTPAUDIOQOS` / `RTPAUDIOQOSBRIDGED`) et estime le **MOS**
via le modèle E simplifié → note `excellent … bad`.

**Sur un Asterisk déjà en place** (hors compose) :

```bash
./scripts/setup_test_asterisk.sh <nom_conteneur>
# crée mcp_ami / mcp_ari, active HTTP+ARI, cdr_manager, res_prometheus,
# ajoute les postes 1001/1002, la file support et un dialplan de test.
MCP_AUTH_MODE=static python scripts/live_test_asterisk.py    # test de l'adaptateur AMI
```

---

## 8. Module 3 — Pipeline vocal Speech-to-Speech (B.5)

```
appel ──Stasis(mcp-voice)──▶ ARI External Media (slin16 / RTP bidirectionnel)
        ──▶ VAD endpointing ──▶ faster-whisper (STT) ──▶ Ollama (LLM, streamé)
        ──▶ Piper (TTS, phrase par phrase) ──▶ RTP ──▶ appel
```

Processus séparé du serveur MCP (`python -m src.voice.runner`, conteneur
`voice-pipeline`) car il embarque les modèles.

**Stratégie de latence** (`LATENCY_BUDGET_MS`, défaut `1500`) :

* l'énoncé est émis dès la fin de parole (`SILENCE_HANGOVER_MS`) ;
* le LLM est **streamé** ; la synthèse démarre à la **première phrase complète**,
  donc l'audio commence avant la fin de la génération ;
* `faster-whisper` en `beam_size=1` + petit modèle ; `LLM_NUM_PREDICT` borné.

Chaque tour est chronométré (`VoiceTurn.timings` : `stt_ms`, `llm_ms`, `tts_ms`,
`time_to_first_audio_ms`) ; `within_budget` indique si le **temps jusqu'au
premier son** tient dans le budget.

**LLM** : par défaut **Qwen2.5-3B-Instruct** via Ollama (`OLLAMA_MODEL`).
Interchangeable sans toucher au code (`gemma2:2b`, `qwen2.5:1.5b-instruct`, …).

---

## 9. Observabilité — Prometheus & Grafana

`docker compose --profile monitoring up -d`

| Cible | Endpoint | Métriques |
|---|---|---|
| Serveur MCP | `mcp-server:8000/metrics` | `mcp_tool_calls_total{tool,status}`, `mcp_tool_duration_seconds`, `mcp_rbac_denials_total`, `mcp_hitl_total{outcome}`, `mcp_asterisk_errors_total`, `mcp_active_channels` |
| Pipeline vocal | `voice-pipeline:9092/metrics` | `voice_turns_total{within_budget}`, `voice_stage_duration_seconds{stage}`, `voice_budget_exceeded_total`, `voice_active_calls` |
| Asterisk | `asterisk:8088/metrics` (Basic Auth) | `asterisk_channels_count`, `asterisk_calls_count`, `asterisk_endpoints_state` |
| Ollama | `ollama:11434/metrics` | latence des requêtes LLM |

**Grafana** — http://localhost:3000 (`admin`/`admin`). Datasource Prometheus et
dashboard **« Supervision Asterisk MCP »** provisionnés (`monitoring/grafana/`) :
débit/latence des outils, refus RBAC, issues HITL, erreurs Asterisk, latence S2S
par étape vs seuil 1,5 s, canaux/appels Asterisk, requêtes Ollama.

> Le job Asterisk force `fallback_scrape_protocol: PrometheusText0.0.4`
> (`res_prometheus` répond sans en-tête `Content-Type`, rejeté sinon par
> Prometheus ≥ 3.0).

---

## 10. Tests & montée en charge (B.7)

### Tests unitaires

```bash
cd mcp-server
pip install -e ".[dev]"
pytest -q          # 58 tests
ruff check src tests
```

Doublures en mémoire (`tests/fakes.py`) : RBAC hiérarchique, sanitizer, HITL
(accept / decline / cancel), cas d'usage (HITL réellement bloquant, sortie
assainie), **journal d'audit**, **métriques**, parsing AMI, avertissement légal
`spy_channel`, endpointing VAD, budget de latence S2S.

### Charge SIPp — jusqu'à 50 canaux

```bash
docker compose --profile asterisk up -d
./loadtest/run_loadtest.sh 127.0.0.1 701 50 5 15000
#                          ip        ext max rate durée_ms
```

Paliers `10 → 25 → 50`, observation via Grafana ou
`asterisk -rx "core show channels count"`, validation croisée sur softphone
(Linphone / Zoiper enregistré sur `1001`/`1002`). Procédure et critères :
[`loadtest/README.md`](loadtest/README.md).

---

## 11. Développement (hors Docker)

```bash
cd mcp-server
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # ajouter ".[voice]" pour le pipeline S2S
cp .env.example .env               # ASTERISK_HOST=localhost, etc.
export PYTHONPATH=$(pwd)

python -m src.main                 # serveur MCP (Modules 1 & 2)
python -m src.voice.runner         # pipeline vocal (Module 3), séparément

python ../scripts/export_tool_schemas.py   # régénère docs/tool-schemas.json
```

FastMCP est épinglé `>=3.0,<4.0` (le code reste compatible 4.x).

---

## 12. Référence de configuration (`.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `MCP_AUTH_MODE` | `keycloak` | `keycloak` \| `static` (jetons `dev-*`) |
| `MCP_PUBLIC_URL` | `http://localhost:8000` | métadonnée OAuth de ressource protégée (PKCE) |
| `MCP_HITL_MODE` | `pilotage` | `pilotage` \| `all` (HITL sur tous les outils) |
| `AUDIT_LOG_PATH` | `logs/audit.jsonl` | journal d'audit |
| `KEYCLOAK_REALM` / `KEYCLOAK_CLIENT_ID` | `asterik` / `mcp-server` | doivent correspondre au realm importé |
| `ASTERISK_HOST` / `ASTERISK_AMI_PORT` | `asterisk` / `5038` | AMI |
| `ASTERISK_AMI_USER` / `ASTERISK_AMI_SECRET` | `mcp_ami` / … | compte AMI |
| `ASTERISK_ARI_BASE_URL` / `ASTERISK_ARI_USER` / `ASTERISK_ARI_PASSWORD` | `http://asterisk:8088` / `mcp_ari` / … | ARI |
| `ASTERISK_DEFAULT_CONTEXT` | `internal` | contexte par défaut des originations |
| `ASTERISK_TRUNKS` / `ASTERISK_TRUNK_MAX_<NOM>` | `trunk-out` / — | trunks suivis par `get_trunk_utilization` |
| `OLLAMA_MODEL` | `qwen2.5:3b-instruct` | LLM local du pipeline S2S |
| `STT_MODEL` / `STT_DEVICE` | `small` / `cpu` | faster-whisper |
| `TTS_VOICE` | `fr_FR-siwis-medium` | voix Piper |
| `LATENCY_BUDGET_MS` | `1500` | budget de latence S2S |
| `VOICE_RTP_PORT` / `VOICE_METRICS_PORT` | `40000` / `9092` | pipeline vocal |
| `ASTERISK_METRICS_PASSWORD` | `changeme_metrics` | Basic Auth de `/metrics` Asterisk |
| `GRAFANA_ADMIN_PASSWORD` | `admin` | Grafana |

---

## Stack

Python 3.10+ · FastMCP 3.x · Keycloak 26 · panoramisk (AMI) · aiohttp (ARI) ·
Asterisk 22 (testé 20/22) · faster-whisper · Ollama (Qwen2.5) · Piper ·
prometheus-client · Prometheus + Grafana · SIPp.

## Avertissement — développement local

Cette configuration est destinée au **développement**. Avant tout déploiement :
changer **tous** les mots de passe (`admin/admin`, `POSTGRES_PASSWORD`, secrets
AMI/ARI, secret client Keycloak, `ASTERISK_METRICS_PASSWORD`), activer **HTTPS**,
remplacer `start-dev` par `start` pour Keycloak, restreindre l'accès réseau à
`/metrics` et au journal d'audit.
