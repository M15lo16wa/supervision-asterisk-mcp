# Supervision Asterisk via MCP

Serveur **MCP (Model Context Protocol)** intégrant Asterisk avec des capacités d'IA
pour une supervision intelligente d'appels sous contrôle humain (**HITL**).

Le projet couvre les trois modules du PPP :

| Module | Contenu | Où |
|---|---|---|
| **1 — Socle Framework MCP & Sécurité** | serveur FastMCP (JSON-RPC 2.0, stdio / Streamable HTTP), Keycloak/OIDC, RBAC 3 rôles, HITL obligatoire, anti-injection | `mcp-server/src/{security,interfaces}` |
| **2 — Télécoms & Outillage Asterisk** | Asterisk 22, PJSIP, dialplan + Stasis, ARI/AMI, outils métier (canaux, extensions, CDR, MOS/RTCP, origination, transfert, espionnage) | `mcp-server/src/{adapters,application}`, `asterisk/` |
| **3 — Pipeline Vocal S2S local** | ARI External Media (slin16), STT `faster-whisper`, LLM local `Ollama`, TTS `Piper`, budget de latence < 1,5 s | `mcp-server/src/voice/` |

Architecture hexagonale : `domain` (entités + ports) ← `application` (cas d'usage)
← `adapters` / `voice` (implémentations) ← `interfaces` (outils MCP).

---

## Démarrage rapide (Docker)

```bash
cp .env.example .env          # ajuster les mots de passe
docker compose up -d          # Postgres + Keycloak (realm importé) + serveur MCP
```

| Service | URL | Notes |
|---|---|---|
| Keycloak | http://localhost:8080 | console `admin` / `admin` (cf. `.env`) |
| Serveur MCP | http://localhost:8000/mcp | transport Streamable HTTP |

Profils optionnels :

```bash
docker compose --profile asterisk up -d     # Asterisk 22 (Module 2)
docker compose --profile voice up -d        # Ollama + pipeline vocal (Module 3)
docker compose --profile monitoring up -d   # Prometheus + Grafana
```

Avec le profil `voice`, le service `ollama-init` télécharge automatiquement le
modèle (`OLLAMA_MODEL`, défaut `qwen2.5:3b-instruct`). Pour le refaire à la main :
`./scripts/ollama_pull.sh`.

---

## Comptes de test (realm `asterik`)

| Utilisateur | Mot de passe | Rôle | Peut |
|---|---|---|---|
| `operateur_demo`   | `admin` | `operateur`   | lecture (canaux, extensions, CDR) |
| `superviseur_demo` | `admin` | `superviseur` | + analyse qualité, écoute discrète |
| `admin_demo`       | `admin` | `admin`       | + origination, transfert, hangup, whisper/barge |

Client confidentiel : `mcp-server` / secret `dev-only-mcp-server-secret-CHANGE-ME`
(**à régénérer avant tout déploiement réel**).

### Obtenir un jeton

```bash
./scripts/get_token.sh admin_demo | tee /tmp/tok.json
```

Le jeton authentifie **uniquement la session MCP** — il n'est jamais transmis à
Asterisk (pas de *token passthrough*). Asterisk utilise ses propres identifiants
AMI/ARI. Durée de vie : 5 minutes.

---

## Outils MCP exposés (noms conformes B.4)

| Outil | Rôle mini | HITL | Zone |
|---|---|---|---|
| `list_active_channels` | Opérateur | — | lecture |
| `get_channel_info` | Opérateur | — | lecture |
| `get_queue_stats` | Opérateur | — | lecture |
| `get_extension_status` | Opérateur | — | lecture |
| `get_cdr_report` | Superviseur | — | analyse |
| `analyze_call_quality` | Superviseur | — | analyse (MOS/RTCP, modèle E) |
| `get_trunk_utilization` | Superviseur | — | analyse |
| `spy_channel` (`listen`) | Superviseur | — | analyse (+ `acknowledge_legal`) |
| `spy_channel` (`whisper` / `barge`) | Admin | ✅ | pilotage (+ `acknowledge_legal`) |
| `originate_call` | Admin | ✅ | pilotage |
| `hangup_channel` | Admin | ✅ | pilotage |
| `redirect_call` | Admin | ✅ | pilotage |

Schémas JSON : [`docs/tool-schemas.json`](docs/tool-schemas.json)
(régénérer : `python scripts/export_tool_schemas.py`).

Chaîne appliquée à chaque outil :

1. **RBAC** `require_role(token, …)` en première ligne — hiérarchie `Opérateur < Superviseur < Admin`.
2. **Consentement humain** — HITL (elicitation MCP, jamais une simple annotation
   `destructiveHint`) sur le pilotage ; sur **tous** les outils si `MCP_HITL_MODE=all`.
   `spy_channel` exige en plus `acknowledge_legal=true` et renvoie un avertissement
   sur l'encadrement légal de l'écoute et de l'enregistrement.
3. **Sortie assainie** — `DataSanitizer` (données Asterisk = entrées non fiables).
4. **Journal d'audit** — chaque appel, refus RBAC et confirmation HITL est écrit en
   JSON Lines (`AUDIT_LOG_PATH`, défaut `logs/audit.jsonl`) : acteur, outil,
   paramètres (secrets masqués), résultat, `request_id`, horodatage UTC.
5. **Métriques Prometheus** — `GET /metrics`.

### Authentification (A.6)

JWT Keycloak (OIDC) vérifié en Resource Server. `MCP_PUBLIC_URL` fait publier la
**métadonnée OAuth de ressource protégée** (RFC 9728) : le client MCP y découvre
Keycloak comme Authorization Server et enchaîne **OAuth 2.1 + PKCE**. Le jeton
n'authentifie que la session MCP — jamais relayé à Asterisk (*no token passthrough*).

### Démo & test — MCP Inspector (livrable A.7)

```bash
npx @modelcontextprotocol/inspector      # http://localhost:6274
```

Transport `Streamable HTTP`, URL `http://localhost:8000/mcp`, Bearer token.
Parcours complet (supervision → pilotage avec validation humaine → écoute) :
[`docs/mcp-inspector.md`](docs/mcp-inspector.md).

### Test de fumée & charge

```bash
./scripts/get_token.sh admin_demo > /tmp/tok.json
python scripts/smoke_mcp.py --token-file /tmp/tok.json --confirm oui
./loadtest/run_loadtest.sh 127.0.0.1 701 50 5 15000   # SIPp, 50 canaux (B.7)
```

### Tester contre un Asterisk déjà en place (sans Keycloak)

```bash
./scripts/setup_test_asterisk.sh <conteneur>     # crée les comptes mcp_ami/mcp_ari,
                                                 # active ARI, ajoute 1001/1002 + dialplan
MCP_AUTH_MODE=static python scripts/live_test_asterisk.py   # adapter AMI <-> Asterisk
```

En mode `static`, trois jetons opaques remplacent Keycloak :
`dev-operateur`, `dev-superviseur`, `dev-admin` (surcouche `MCP_STATIC_TOKENS`).

> Validé sur un conteneur **Asterisk 20.6** : login AMI, les 11 outils B.4
> (`get_queue_stats`, `get_trunk_utilization`, `get_channel_info`… inclus),
> `originate`/`hangup`, capture CDR temps réel, `spy_channel` (refus sans
> `acknowledge_legal`, écoute + trace d'audit avec), et la chaîne complète
> *client MCP → HTTP → auth → RBAC → HITL → AMI* (l'`originate_call` refusé à un
> opérateur, accepté pour un admin après confirmation). Journal d'audit vérifié
> (chaque action tracée), Prometheus scrappe `mcp-server`/`asterisk`, Grafana
> charge le dashboard.

---

## Développement (hors Docker)

```bash
cd mcp-server
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # + ".[voice]" pour le pipeline vocal
cp .env.example .env
export PYTHONPATH=$(pwd)
python -m src.main                 # serveur MCP (Modules 1 & 2)
python -m src.voice.runner         # pipeline vocal (Module 3), séparément
```

Tests + lint :

```bash
cd mcp-server
pytest -q
ruff check src tests
```

Les tests (`pytest`, 58) utilisent des doublures en mémoire (`tests/fakes.py`) :
RBAC hiérarchique, sanitizer, HITL (accept/decline/cancel), cas d'usage (HITL
réellement bloquant, sortie assainie), **journal d'audit**, **métriques**,
parsing AMI, endpointing VAD et budget de latence du pipeline S2S.

---

## Module 2 — Asterisk

`asterisk/config/` fournit une configuration Asterisk 22 :

* `pjsip.conf` — transport UDP + postes `1001`, `1002`, superviseur `1099`
  (codec `slin16`) + **trunk sortant `trunk-out`** (registration + identify) ;
* `queues.conf` — file d'attente `support` (2 membres) pour `get_queue_stats` ;
* `extensions.conf` — appels internes `_10XX`, `hint` pour `get_extension_status`,
  `700 => Stasis(mcp-voice)`, `701 => Echo()`, `800 => Queue(support)`,
  `_0. => Dial(...@trunk-out)`, contexte `from-trunk` pour les entrants ;
* `manager.conf` / `ari.conf` — comptes dédiés `mcp_ami` / `mcp_ari` (moindre privilège) ;
* `cdr_manager.conf` — CDR en événements AMI `Cdr`, bufferisés (`get_cdr_report`) ;
* `prometheus.conf` — `res_prometheus` (Basic Auth) pour `/metrics` ;
* `http.conf`, `rtp.conf`, `modules.conf`.

Qualité d'appel : `analyze_call_quality` lit `PJSIPShowChannelStats` (repli sur la
variable `RTPAUDIOQOS`) et estime le **MOS** via le modèle E (ITU-T G.107)
simplifié → note `excellent … bad`.

---

## Module 3 — Pipeline vocal S2S

```
appel ──Stasis(mcp-voice)──▶ ARI External Media (slin16 / RTP)
        ──▶ VAD endpointing ──▶ faster-whisper ──▶ Ollama (streamé)
        ──▶ Piper (par phrase) ──▶ RTP ──▶ appel
```

Stratégie de latence (`LATENCY_BUDGET_MS`, défaut `1500`) :

* l'énoncé est émis dès la fin de parole (`silence_hangover_ms`) ;
* le LLM est **streamé** et la synthèse démarre à la **première phrase complète**,
  donc l'audio commence bien avant la fin de la génération ;
* `faster-whisper` en `beam_size=1` + petit modèle, `num_predict` borné.

Chaque tour est chronométré (`VoiceTurn.timings`) et `within_budget` indique si le
**temps jusqu'au premier son** tient dans le budget.

Variables clés (`.env`) : `OLLAMA_MODEL`, `STT_MODEL`, `STT_DEVICE`, `TTS_VOICE`,
`VOICE_RTP_PORT`, `LATENCY_BUDGET_MS`.

---

## Observabilité — Prometheus & Grafana

`docker compose --profile monitoring up -d`

| Cible scrappée | Endpoint | Métriques clés |
|---|---|---|
| Serveur MCP | `mcp-server:8000/metrics` | `mcp_tool_calls_total{tool,status}`, `mcp_tool_duration_seconds`, `mcp_rbac_denials_total`, `mcp_hitl_total{outcome}`, `mcp_asterisk_errors_total`, `mcp_active_channels` |
| Pipeline vocal | `voice-pipeline:9092/metrics` | `voice_turns_total{within_budget}`, `voice_stage_duration_seconds{stage}`, `voice_budget_exceeded_total`, `voice_active_calls` |
| Asterisk | `asterisk:8088/metrics` (Basic Auth, `res_prometheus`) | `asterisk_channels_count`, `asterisk_calls_count`, `asterisk_endpoints_state` |
| Ollama | `ollama:11434/metrics` | latence des requêtes LLM |

Grafana : **http://localhost:3000** (`admin`/`admin`) — datasource Prometheus et
dashboard **« Supervision Asterisk MCP »** provisionnés automatiquement
(`monitoring/grafana/`). Panneaux : débit/latence des outils, refus RBAC,
issues HITL, latence S2S par étape vs budget 1,5 s, canaux/appels Asterisk.

> Le job Asterisk force `fallback_scrape_protocol: PrometheusText0.0.4` car
> `res_prometheus` répond sans en-tête `Content-Type` (rejeté sinon par
> Prometheus ≥ 3.0). Testé : Prometheus scrappe `mcp-server` et `asterisk`
> (`up`), Grafana interroge la datasource et charge le dashboard.

---

## Stack

Python 3.10+ · FastMCP 3.x · Keycloak 26 · Panoramisk (AMI) · aiohttp (ARI) ·
Asterisk 20/22 · faster-whisper · Ollama · Piper · prometheus-client ·
Prometheus + Grafana.

## Avertissement

Configuration destinée au **développement local** : changer tous les mots de passe
(`admin/admin`, `POSTGRES_PASSWORD`, secrets AMI/ARI, secret client Keycloak),
activer HTTPS, et remplacer `start-dev` par `start` avant tout déploiement.
