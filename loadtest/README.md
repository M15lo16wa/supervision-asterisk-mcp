# Tests de charge & montée en charge (B.7)

Simulation jusqu'à **50 canaux simultanés** avec **SIPp**, complétée par des
validations manuelles sur softphones (Linphone / Zoiper).

## 1. Préparer Asterisk

```bash
docker compose --profile asterisk up -d
# (ou ./scripts/setup_test_asterisk.sh <conteneur> sur un Asterisk existant)
```

L'extension `701` du dialplan répond par `Answer()` + `Echo()` — idéale pour
mesurer la boucle audio sous charge.

## 2. Lancer le test

```bash
./loadtest/run_loadtest.sh 127.0.0.1 701 50 5 15000
#                          ip        ext max rate duration_ms
```

Paliers recommandés : `10` → `25` → `50` canaux.

## 3. Observer

* **Grafana** — dashboard « Supervision Asterisk MCP » : panneaux
  *Asterisk — canaux / appels* et *Latence par étape* (si le pipeline S2S tourne).
* **En direct** :
  ```bash
  watch -n1 'docker compose exec asterisk asterisk -rx "core show channels count"'
  ```
* **Via l'outil MCP** : appeler `list_active_channels` / `get_trunk_utilization`
  pendant le test.
* **SIPp** : colonnes *Successful call* / *Failed call* / *Response Time* +
  `loadtest_stats.csv`.

## 4. Critères

| Palier | Attendu |
|---|---|
| 10 canaux | 100 % réussite, RTT SIP < 50 ms |
| 25 canaux | ≥ 99 % réussite |
| 50 canaux | ≥ 95 % réussite, pas de fuite de canaux après le test (`core show channels count` revient à 0) |

## 5. Validation manuelle

Enregistrer un softphone (Linphone / Zoiper) sur `1001` / `1002`
(`agent1001pass` / `agent1002pass`), passer un appel `1001 → 1002` **pendant**
le test de charge et vérifier la qualité audio + `analyze_call_quality`.
