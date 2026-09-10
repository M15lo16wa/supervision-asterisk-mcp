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

Après le premier démarrage avec le profil `voice`, tirer un modèle Ollama :

```bash
docker compose exec ollama ollama pull qwen2.5:3b-instruct
```

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

## Outils MCP exposés

| Outil | Rôle mini | HITL | Zone |
|---|---|---|---|
| `list_active_channels` | operateur | — | lecture |
| `list_extensions` | operateur | — | lecture |
| `get_call_records` | operateur | — | lecture |
| `analyze_call_quality` | superviseur | — | analyse (MOS/RTCP, modèle E simplifié) |
| `start_channel_spy` (`listen`) | superviseur | — | analyse |
| `start_channel_spy` (`whisper` / `barge`) | admin | ✅ | pilotage |
| `originate_call` | admin | ✅ | pilotage |
| `hangup_channel` | admin | ✅ | pilotage |
| `transfer_call` | admin | ✅ | pilotage |

Chaque outil : (1) `require_role(token, …)` en première ligne — la hiérarchie
`operateur < superviseur < admin` est appliquée ; (2) confirmation HITL injectée
comme dépendance pour le pilotage ; (3) sortie passée au `DataSanitizer`.

### Test de fumée

```bash
./scripts/get_token.sh admin_demo > /tmp/tok.json
python scripts/smoke_mcp.py --token-file /tmp/tok.json --confirm oui
```

### Tester contre un Asterisk déjà en place (sans Keycloak)

```bash
./scripts/setup_test_asterisk.sh <conteneur>     # crée les comptes mcp_ami/mcp_ari,
                                                 # active ARI, ajoute 1001/1002 + dialplan
MCP_AUTH_MODE=static python scripts/live_test_asterisk.py   # adapter AMI <-> Asterisk
```

En mode `static`, trois jetons opaques remplacent Keycloak :
`dev-operateur`, `dev-superviseur`, `dev-admin` (surcouche `MCP_STATIC_TOKENS`).

> Validé sur un conteneur **Asterisk 20.6** : login AMI, `list_active_channels`
> (canaux réels), `list_extensions` (hints), `originate`/`hangup`, capture CDR
> temps réel, et la chaîne complète *client MCP → HTTP → auth → RBAC → HITL →
> AMI* (RBAC refuse bien l'`originate_call` à un opérateur, l'accepte pour un
> admin après confirmation).

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

Les tests utilisent des doublures en mémoire (`tests/fakes.py`) : RBAC, sanitizer,
HITL (accept/decline/cancel), cas d'usage (HITL réellement bloquant, sortie
assainie), parsing AMI, endpointing VAD et budget de latence du pipeline S2S.

---

## Module 2 — Asterisk

`asterisk/config/` fournit une configuration Asterisk 22 minimale :

* `pjsip.conf` — transport UDP + postes `1001`, `1002`, superviseur `1099` (codec `slin16` autorisé) ;
* `extensions.conf` — appels internes `_10XX`, `hint` pour `list_extensions`,
  `700 => Stasis(mcp-voice)` (assistant vocal), `701 => Echo()` (diagnostic) ;
* `manager.conf` / `ari.conf` — comptes dédiés `mcp_ami` / `mcp_ari` ;
* `cdr_manager.conf` — CDR émis en événements AMI `Cdr`, bufferisés côté serveur
  (`get_call_records`) ;
* `http.conf`, `rtp.conf` — ARI + plage RTP.

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

## Stack

Python 3.10+ · FastMCP 3.x · Keycloak 26 · Panoramisk (AMI) · aiohttp (ARI) ·
Asterisk 22 · faster-whisper · Ollama · Piper · Prometheus + Grafana.

## Avertissement

Configuration destinée au **développement local** : changer tous les mots de passe
(`admin/admin`, `POSTGRES_PASSWORD`, secrets AMI/ARI, secret client Keycloak),
activer HTTPS, et remplacer `start-dev` par `start` avant tout déploiement.
