# Plan de test end-to-end — supervision Asterisk MCP

Test de bout en bout de **toutes** les fonctionnalités de l'outil, en partant
d'un socle propre, en incluant la **création des comptes PJSIP**.

État du plan : à valider avec l'utilisateur avant exécution.
Dernière mise à jour : phase 0 non encore exécutée.

---

## 1. Périmètre : les 12 outils MCP

| # | Outil | Phase | Prérequis spécifique |
|---|---|---|---|
| 1 | `list_active_channels` | P3 | AMI joignable |
| 2 | `get_channel_info` | P3/P4 | canal existant pour le cas nominal |
| 3 | `get_queue_stats` | P6 | file `support` + `app_queue` |
| 4 | `get_extension_status` | P3 | hints dans `mcp-internal` |
| 5 | `get_cdr_report` | P3/P4 | `cdr_manager` + 1 appel accompli |
| 6 | `analyze_call_quality` | P4 | **RTP réel** (sinon erreur RTCP attendue) |
| 7 | `get_trunk_utilization` | P8 | endpoints `trunk-*` présents |
| 8 | `llm_chat` | P9 | Ollama joignable + modèle tiré |
| 9 | `originate_call` | P4 | endpoint PJSIP joignable **ou** `Local/701` |
| 10 | `hangup_channel` | P4 | canal actif |
| 11 | `redirect_call` | P5 | canal actif + dialplan cible |
| 12 | `spy_channel` | P5 | `app_chanspy` + `acknowledge_legal=true` |

Hors périmètre MCP mais testés : **Prometheus**, **Grafana**, **pipeline
vocal S2S** (P10, différé), **charge SIPp** (P11, différé).

---

## 2. Bloqueurs à lever AVANT tout test

| # | Bloqueur | Impact | Correctif |
|---|---|---|---|
| B1 | `KEYCLOAK_REALM = "asterisk "` (espace final, `.env:15`) | claim `iss` = `.../realms/asterisk ` → **rejet de tous les JWT** | supprimer l'espace (doublon avec `.env:72`) |
| B2 | `monitoring/.env` **absent** | stack monitoring non lançable, secret Prometheus non résolu | `cp monitoring/.env.example monitoring/.env` + aligner sur le `.env` racine |
| B3 | `ASTERISK_HOST=asterisk` (alias Docker) alors qu'Asterisk tourne **sur l'hôte** | AMI injoignable | passer à `host.docker.internal` (déjà mappé via `extra_hosts`) |
| B4 | Python hôte = 3.8.10, `pytest`/`fastmcp` absents, `pyproject` exige ≥3.10 | aucun script Python ni test unitaire exécutable hors conteneur | exécuter **dans** le conteneur `mcp-server` (Python 3.12) |
| B5 | `docker-compose.yml:100` déclare `LLM_NUM_PRED` alors que le code lit `LLM_NUM_PREDICT` | réglage LLM inopérant | renommer la variable compose |
| B6 | Secrets faibles `passer123` (`.env`) | acceptable en dev, à noter | regenerer via `provision_asterisk.sh` siinal |
| B7 | README annonçait 69 tests, le code en contient **76** | documentation fausse | **corrigé** : README mis à jour |

---

## 3. Décision d'architecture (à trancher)

Deux chemins possibles, impact sur toute la suite :

| | **Option A — bare metal** | **Option B — conteneur Docker** |
|---|---|---|
| Asterisk | sur l'hôte (déjà installé : 22.11.0) | conteneur `asterisk-server` |
| Provisionnement | `scripts/provision_asterisk.sh` (déjà écrit et testé) | `scripts/setup_test_asterisk.sh` |
| Résolution `ASTERISK_HOST` | `host.docker.internal` | `asterisk` (alias réseau) |
| Comportement PJSIP | `pjsip.conf` à modèles, endpoints 1001/1002/**1099** | `pjsip_mcp.conf` inline, endpoints 1001/1002 (**pas 1099**) |
| Recommandé ? | **Oui** — conforme à l'objectif « configurer la machine » | Non — deux configs qui divergent |

⚠️ Les deux chemins ont des configs PJSIP **divergentes**. Il faut en choisir
une seule pour la session, sinon les résultats ne sont pas comparables.

### Décision actée le 2026-10-01

- **Cible** : **bare metal sur l'hôte** (option A). Asterisk 22.11.0 y est déjà
  installé ; on valide `provision_asterisk.sh`, qui est l'objectif du projet.
- **Comptes PJSIP** : **vrais comptes softphone** (mode `register`), pour
  validation personnelle hors MCP. `ASTERISK_HOST` devient donc
  `host.docker.internal` (déjà mappé par `extra_hosts` dans le compose racine).

---

## 4. Phases

Chaque phase a un **point de contrôle** : on valide avant de passer à la
suivante.

### Phase 0 — Socle et prérequis
Objectif : tous les services `healthy` et les secrets alignés.

1. Lever B1, B3, B5 (corrections `.env` / compose).
2. Vérifier l'état d'Asterisk sur l'hôte :
   `asterisk -rx "core show version"`, `systemctl status asterisk`.
3. Provisionner : `sudo ./scripts/provision_asterisk.sh --dry-run`
   puis `sudo ./scripts/provision_asterisk.sh`.
   → rend les secrets, corrige l'ACL AMI, remplit `mcp-internal`.
4. Vérifier Asterisk : `manager show users`, `ari show users`,
   `http show status`, `pjsip show endpoints`, `module show like res_prometheus`.
5. Démarrer le socle : `docker compose up -d` (keycloak-db → keycloak →
   mcp-server). Attendre les healthchecks.
6. Démarrer la supervision : `cd monitoring && docker compose up -d`.
7. Aligner le secret Prometheus entre `.env` racine et `monitoring/.env` (B2).

**Contrôle P0** : `docker compose ps` tout en `healthy` ; `/metrics` d'Asterisk
répond en 200 avec Basic Auth.

### Phase 1 — Authentification et RBAC
1. `docker compose exec mcp-server pytest -q` → **76 tests unitaires**.
2. Tokens : `./scripts/get_token.sh operateur_demo`, `superviseur_demo`,
   `admin_demo`.
3. Vérifier le `iss` du jeton (correctif B1) et la présence de
   `realm_access.roles`.
4. Lister les outils : 12 exactement.
5. RBAC négatif : `operateur_demo` sur `originate_call` → refus ;
   `superviseur_demo` sur `originate_call` → refus (admin requis) ;
   `admin_demo` → accepté.

**Contrôle P1** : 76 tests verts, 3 tokens valides, RBAC conforme.

### Phase 2 — Création des comptes PJSIP  ← demande explicite

**Décision retenue** : de **vrais comptes utilisables sur softphones**
(authentification `userpass` réelle), afin de pouvoir valider personnellement
hors du MCP. Cible : **bare metal sur l'hôte**.

Constat actuel : seuls **1001, 1002, 1099** existent ; le dialplan `_10XX`
(couvertures 1000–1099) laisse donc **1000 et 1003–1098 orphelins**. Et aucun
AOR n'a de contact (seul `trunk-out` a un `type = registration`), donc
`Dial(PJSIP/1001)` échoue tant qu'aucun poste ne s'est enregistré.

Script à créer : `scripts/create_pjsip_accounts.sh`

- idempotent, `--dry-run`, comme `provision_asterisk.sh` ;
- génère les endpoints à partir des modèles existants (`endpoint-internal`,
  `auth-userpass`, `aor-single`) ;
- **PJSIP n'a pas de directive `#include`** → il faut injecter les comptes dans
  `pjsip.conf` via un bloc `; >>> ACCOUNTS-BEGIN` / `; <<< ACCOUNTS-END` ;
- génère **aussi les hints** dans `extensions.conf` (contextes `internal` et
  `mcp-internal`) pour chaque compte, sinon `get_extension_status` (outil 4)
  ne verra que 1001/1002/1099 ;
- produit une table d'identifiants lisible dans `docs/sip-accounts.md`
  (pour configurer Zoiper / Linphone / app SIP mobile) ;
- codecs `ulaw`, `alaw`, `slin16` (PCMU/PCMA/PCM16) : compatibles avec tous
  les softphones. SRTP/TLS non activés initialement — à traiter séparément.

Plage cible : **1001–1010** (postes agents) + **1099** (superviseur).

Deux modes de joignabilité, coexistants :
- **`register` (défaut)** — un softphone s'enregistre avec
  `username`/`password` ; l'AOR se remplit, `Dial()` aboutit. C'est le mode
  retenu, et il couvre les tests MCP dès qu'un poste est enregistré.
- **`static` (option `--contact`)** — `contact = sip:<ext>@127.0.0.1:<port>`
  sur l'AOR, pour permitre l'automatisation sans softphone. Nécessite un
  pair qui réponde (SIPp en UAS) pour obtenir du **vrai RTP**, donc un vrai
  MOS pour `analyze_call_quality` (Phase 4). Sans pair, l'INVITE part puis le
  canal expire : les outils AMI de lecture restent valides, mais l'analyse de
  qualité ne l'est pas.

Étapes :
1. Écrire le script + `--dry-run` + tests de rendu et d'idempotence.
2. Créer la plage 1001–1010 + 1099, recharger `pjsip` / `dialplan`.
3. Vérifier : `pjsip show endpoints`, `pjsip show aor 1001`,
   `pjsip show contacts 1001`, et `dialplan show hints mcp-internal`.
4. Rédiger la procédure softphone (extension, hôte, codecs, mot de passe,
   mot de passe du compte SIP distinct du secret AMI).

**Contrôle P2** : les endpoints annoncés par Asterisk correspondent exactement
à la plage créée ; aucun 1003–1098 fantôme ; chaque compte a son hint.

**Validation personnelle conjointe** : configurer un softphone sur 1001, un
second sur 1002, et vérifier : (a) l'enregistrement apparaît dans
`pjsip show contacts`, (b) l'appel 1001 → 1002 sonne, (c) `get_channel_info`
reflète l'appel en cours, (c) `analyze_call_quality` renvoie un MOS.

### Phase 3 — Outils AMI de lecture (sans appel)
1. `list_active_channels` → liste vide, structure correcte.
2. `get_channel_info(channel_id="inexistant")` → `channel_not_found` propre.
3. `get_extension_status(context="mcp-internal")` → 1001, 1002, 1099 en
   `RINGING`/`UNAVAIL` selon hints.
4. `get_queue_stats()` → file `support` présente, 0 en attente.
5. `get_cdr_report(limit=10)` → vide au départ.
6. `get_trunk_utilization()` → aucun trunk livré → 0 % (comportement attendu
   quand `ASTERISK_TRUNK_HOST` n'est pas renseigné).

**Contrôle P3** : aucune erreur `asterisk_unavailable` ; les cas nominaux et
les cas d'erreur sont tous distingués.

### Phase 4 — Cycle de vie d'un appel (le cœur)
1. `originate_call(endpoint="PJSIP/1001", exten="1002", context="mcp-internal")`
   → HITL elicitation (accepter).
2. `list_active_channels` → **2 canaux** (legs).
3. `get_channel_info` sur chacun → CallerID, durée, état.
4. `analyze_call_quality` → si RTP réel : MOS/jigue/perte. Sinon : erreur RTCP
   explicite (c'est le comportement documenté, pas un bug).
5. `get_cdr_report` → le CDR apparaît après raccrochage.
6. `hangup_channel` → 0 canal résiduel.
7. Variante sans PJSIP (toujours disponible) :
   `originate_call` vers `Local/701@mcp-internal` (`Echo()`), comme le fait
   `scripts/e2e_functional.py`.

**Contrôle P4** : un appel 1001→1002 s'établit, se supervise, se raccroche ;
0 canal résiduel.

### Phase 5 — Contrôle d'appel et spy
1. `hangup_channel` (Cause=16) — déjà couvert en P4, rejouer sur un 2e appel.
2. `redirect_call` mode aveugle : `Local/701@mcp-internal` → `1002`.
3. `redirect_call` mode `attended=true` (Atxfer) : 1002 décroche puis transfert.
4. `spy_channel(mode="listen")` — superviseur.
5. `spy_channel(mode="whisper")` sans `acknowledge_legal` → refus.
6. `spy_channel(mode="whisper", acknowledge_legal=true)` → accepté.
7. `spy_channel` sur canal inexistant → `channel_not_found`.

**Contrôle P5** : chaque mode et chaque refus se comportent comme documenté.

### Phase 6 — Files d'attente
1. `originate_call` → `Local/800@mcp-internal` (`Queue(support)`).
2. `get_queue_stats()` → `calls_waiting ≥ 1`.
3. `hangup_channel` → file vide.

**Contrôle P6** : compteurs de file cohérents.

### Phase 7 — HITL (validation humaine)
1. Configurer `MCP_HITL_MODE=pilotage` (défaut).
2. Refus de l'elicitation sur `originate_call` → **aucun canal créé**
   (invariant de sécurité).
3. Acceptation → canal créé.
4. Vérifier l'audit : `logs/audit.jsonl` contient les 12 outils et les décisions.

**Contrôle P7** : un refus d'approbation n'aboutit **jamais** à une action.

### Phase 8 — Supervision Prometheus / Grafana
1. `curl -u prometheus:<secret> http://<asterisk>:8088/metrics` → 200.
2. Job Prometheus `asterisk` → state `up` (le point ouvert de la Phase B :
   `.env` racine vs `monitoring/.env`).
3. `curl http://localhost:8000/metrics` → métriques du serveur MCP
   (`voice_turns_total`, etc.).
4. Grafana : les ~20 panneaux du dashboard `supervision` se peuplent.
5. `get_trunk_utilization` vs métriques Prometheus → cohérence.

**Contrôle P8** : `asterisk` est `up` dans Prometheus, aucun panneau en erreur.

### Phase 9 — `llm_chat`
1. Ollama joignable : `curl http://localhost:11434/api/tags`.
2. Modèle tiré : `./scripts/ollama_pull.sh supervision-ollama`.
3. `llm_chat(message="résume l'état du système")` sans contexte.
4. Avec `context="channels"` / `"cdr:5"` / `"queues"` → le contexte est injecté.
5. Vérifier la sanitisation (aucune donnée sensible dans le prompt).

**Contrôle P9** : réponses cohérentes, contexte réellement transmis.

### Phase 10 — Voix S2S *(différé)*
`python scripts/gen_question_audio.py` puis `python scripts/e2e_voice.py` :
Stasis, RTP externe, transcription, métriques, 2 appels simultanés.

### Phase 11 — Charge SIPp *(différé, si `sip-tester` installé)*
`./loadtest/run_loadtest.sh <ip> 701 10 5 15000` → paliers 10 → 25 → 50 canaux.

---

## 5. Ordre d'exécution et critères d'arrêt

```
P0 socle ──► P1 auth ──► P2 comptes PJSIP ──► P3 lecture
                                              │
                                              ▼
                          P4 appel ──► P5 contrôle ──► P6 files
                                              │
                                              ▼
                          P7 HITL ──► P8 supervision ──► P9 llm_chat
                                              │
                                              ▼
                                    P10 voix ──► P11 charge
```

**Règle** : on ne passe à la phase N+1 que si le contrôle de la phase N est
validé. Un blocage est remonté et traité, jamais contourné.

## 6. Journal de résultats

À remplir au fil de l'eau : une ligne par test, avec l'outil appelé, le rôle
utilisé, le résultat attendu, le résultat obtenu et l'écart.