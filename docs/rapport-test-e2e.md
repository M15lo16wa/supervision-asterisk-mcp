# Rapport de campagne E2E — supervision Asterisk + MCP

Date : 2026-10-01 · Cible : Asterisk **bare metal** (`22.11.0`) sur hôte Ubuntu,
pile Docker pour Keycloak / serveur MCP / Prometheus / Grafana.

Résultat global : **29/30 vérifications** du parcours
`scripts/e2e_functional.py` passent. La seule non-validée est `llm_chat`
(voir « Limites »).

---

## 1. Ce qui a été validé

| Domaine | Vérifications | Résultat |
| --- | --- | --- |
| Socle Docker | keycloak, keycloak-db, mcp-server, prometheus, grafana | `healthy` |
| Prometheus | cibles `asterisk`, `mcp-server`, `prometheus` | `up` |
| Asterisk | AMI `mcp_ami`, ARI `mcp_ari`, `res_prometheus`, `/metrics` | 200 avec secret / 401 sans |
| Authentification | 3 jetons (operateur, superviseur, admin), durée de vie 5 min | issuer correct, rôles corrects |
| RBAC | hiérarchie operateur < superviseur < admin | refus correctly instrumentés |
| Tests unitaires | `pytest` dans le conteneur | **76 passés** |
| Outils AMI de lecture | `list_active_channels`, `get_extension_status`, `get_queue_stats`, `get_cdr_report`, `get_trunk_utilization` | OK |
| Erreurs propres | `get_channel_info` et `analyze_call_quality` sur canal inexistant | `channel_not_found` explicite |
| Origination réelle | `originate_call` vers `Local/701@mcp-internal` (Echo) | 2 canaux créés, `get_channel_info` OK |
| HITL | confirmation **acceptée** puis **refusée** | élicitation fonctionnelle, aucun canal en cas de refus |
| Raccrochage | `hangup_channel` | canaux libérés |
| File d'attente | appel vers `Local/800`, `get_queue_stats` pendant l'appel | `calls_waiting=2` |
| ChanSpy | garde légale `acknowledge_legal` | refus sans accusé, `channel_not_found` ensuite |
| Comptes PJSIP | 11 postes (1001-1010 + 1099) générés et déployés | endpoints `Unavailable`, AOR/auth présents, hints OK |

---

## 2. Défauts trouvés par la campagne

La campagne a mis au jour **12 défauts**, dont 5 dans le code du dépôt.

### Code du dépôt (corrigés)

| # | Défaut | Effet observé |
| --- | --- | --- |
| 1 | `asterisk.conf` écrasait `astdbdir`/`astkeydir`/`astagidir` sur `astvarlibdir` | `Module initialization failed. ASTERISK EXITING!` |
| 2 | `provision_asterisk.sh` faisait `chmod 0750 /etc/asterisk` | répertoire illisible pour l'utilisateur `asterisk` → **mort silencieuse** |
| 3 | Aucun utilisateur/groupe `asterisk` créé sur l'hôte | `No such group 'asterisk'!` |
| 4 | Commentaires `>` au lieu de `;` dans `asterisk.conf` | `parse error: No category context for line 15` |
| 5 | `LLM_NUM_PRED` au lieu de `LLM_NUM_PREDICT` dans le compose | réglage LLM inopérant |
| 6 | `KEYCLOAK_REALM="asterisk "` (espace final, doublon) | JWT rejeté |
| 7 | `scripts/get_token.sh` sans bit d'exécution | `Permission denied` alors que le README documente `./scripts/…` |
| 8 | `scripts/e2e_functional.py` appelait `env.getenv` alors que le module n'importe que `os` | `NameError` au chargement : le script E2E **n'avait jamais pu tourner** |
| 9 | `provision_asterisk.sh` ne rechargeait pas `pjsip.conf` et ne vérifiait pas le redémarrage | nouvelle config jamais appliquée, sans erreur |
| 10 | `prometheus.yml` ciblait l'alias Docker `asterisk` | cible injoignable en bare metal |
| 11 | README annonçait 69 tests au lieu de 76 | documentation fausse |
| 12 | Marqueurs `TRUNK-BEGIN/END` absents du gabarit `pjsip.conf` | trunk factice déployé et cassé au chargement |

### Environnement (traités en cours de route)

| # | Défaut | Effet | Résolution |
| --- | --- | --- | --- |
| E1 | Disque hôte saturé (0 octet libre) | Grafana en boucle de redémarrage : `database or disk is full (13)` | caches purgés (+3,2 Go) |
| E2 | Image Ollama trop volumineuse pour 24 Go | `llm_chat` non testable | **accepté comme limite**, cf. §4 |
| E3 | `monitoring/.env` absent | Prometheus sans secret métriques | créé et aligné sur `.env` |

### Le piège le plus coûteux : la mort silencieuse d'Asterisk

Deux symptômes distincts, même signature trompeuse — `systemctl is-active` renvoie
`active` alors qu'aucun daemon ne tourne :

1. **`/etc/asterisk` en `0750`** : répertoire illisible pour l'utilisateur
   `asterisk`, donc `modules.conf` illisible, donc Asterisk s'arrête.
   Le mode daemon ne produit **aucun log**.
2. **État périmé** : le script d'init SysV détecte un `asterisk.ctl` ou un
   `asterisk.pid` laissé par un arrêt sale, répond `already running` **en sortant
   avec le code 0**, et systemd annonce `Started LSB: Asterisk PBX.`

Le contournement : `pgrep -af asterisk` ne suffit pas, car il trouve les processus
`systemd --user` de l'utilisateur `asterisk`. Il faut `pgrep -x asterisk`.
Le provisionneur compare désormais le PID avant/après et purge l'état périmé,
puis **échoue bruyamment** si le daemon ne repart pas.

---

## 3. Précisions utiles pour la suite

- **Durée de vie des jetons : 5 minutes.** Tout script de campagne doit les
  renouveler, sinon il reçoit des `401` silencieux en cours d'exécution.
- **Issuers acceptés par le serveur MCP : deux** — l'URL publique
  (`KEYCLOAK_PUBLIC_URL`) et l'URL interne Docker (`KEYCLOAK_BASE_URL`).
  C'est pourquoi `e2e_functional.py` doit pointer `KEYCLOAK_PUBLIC_URL` sur
  `http://keycloak:8080` **quand il tourne dans le conteneur** : le `iss` du jeton
  dépend de l'URL par laquelle le client a joint Keycloak.
- **`get_trunk_utilization` renvoie un trunk `trunk-out` en état `unknown`** même
  quand le bloc trunk n'est pas livré : l'outil lit la configuration du serveur
  MCP (`ASTERISK_TRUNKS`), pas l'état réel d'Asterisk. À ne pas lire comme une
  preuve que le trunk existe.
- **`get_extension_status` a besoin des hints dans le contexte interrogé.** Les
  blocs de hints sont désormais générés par `create_pjsip_accounts.sh` dans
  `internal` **et** `mcp-internal`.

---

## 4. Limites assumées

- **`llm_chat` non testé sur ce poste.** L'image Ollama ne tient pas sur un
  disque de 24 Go (Docker n'a rien à libérer : 0 B récupérable). Le modèle a été
  abaissé à `qwen2.5:0.5b-instruct` dans `.env` pour être compatible avec une
  installation future. Le code de `llm_chat` reste couvert par les tests
  unitaires.
- **Ports RTP 10000-10099** seulement : suffisant pour 2 appels simultanés,
  insuffisant pour un test de charge SIPp.
- **`external_media_address` non configuré.** Nécessaire si un softphone doit
  joindre Asterisk à travers un NAT.
- **`ufw` inactif** sur cet hôte : les règles de pare-feu du provisionneur
  n'ont donc aucun effet ici. À vérifier sur un hôte où `ufw` est actif.
- **`open_firewall()` n'ouvre que `TRUSTED_FIRST`** alors que l'ACL RFC1918 en
  déclare quatre : à corriger avant tout test depuis un réseau externe.

---

## 5. Suite

1. Enregistrer un vrai softphone sur `1001` et `1002` (hôte en `10.0.2.15`,
   UDP 5060) — cf. `docs/sip-accounts.md`.
2. Rejouer les phases d'appel avec de vrais canaux `PJSIP/…` : c'est la seule
   façon d'obtenir des statistiques RTCP et donc de valider
   `analyze_call_quality` (aujourd'hui rejeuable uniquement via son chemin
   d'erreur sur un canal `Local`).
3. Traiter le pipeline vocal S2S, différé.

---

## 6. Recommandation de durcissement

Les mots de passe des postes sont écrits **en clair** dans
`asterisk/config/pjsip.conf`, qui est versionné. C'est une pratique déjà en place
dans le dépôt (le fichier contenait auparavant `agent1001pass`, etc.), mais le
générateur la perpétue.

Le mécanisme existe déjà pour les autres secrets : `ari.conf` et `manager.conf`
utilisent des marqueurs `{{…}}` que `provision_asterisk.sh` remplace au moment
du déploiement (`manager.conf` est d'ailleurs en `0640`).

Piste de durcissement : faire écrire `{{SIP_PASSWORD_1001}}`… dans le gabarit,
et faire lire les mots de passe au provisionneur depuis un fichier ignoré par
git (`.env` étant déjà ignoré, voir `.gitignore:3`). Les secrets resteraient ainsi
hors du dépôt tout en gardant les softphones fonctionnels. Non implémenté dans
cette campagne : cela change l'architecture de gestion des secrets et mérite un
arbitrage explicite.