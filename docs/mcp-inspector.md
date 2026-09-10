# Démonstration & test avec MCP Inspector

L'outil de diagnostic officiel **MCP Inspector** sert d'environnement de démo
(livrable A.7 — sans OpenAPI/Swagger, non natif à MCP). Aucun surcoût : il se
lance via `npx`.

## Prérequis

```bash
docker compose up -d                       # Keycloak + serveur MCP
# ou, pour tester sans Keycloak :
MCP_AUTH_MODE=static docker compose up -d
```

## Lancer l'Inspector

```bash
npx @modelcontextprotocol/inspector
```

Puis dans l'UI (http://localhost:6274) :

| Champ | Valeur |
|---|---|
| Transport Type | `Streamable HTTP` |
| URL | `http://localhost:8000/mcp` |
| Authentication | `Bearer Token` |
| Token | voir ci-dessous |

### Jeton — mode Keycloak (défaut)

```bash
./scripts/get_token.sh admin_demo | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])"
```

### Jeton — mode statique (`MCP_AUTH_MODE=static`)

`dev-operateur`, `dev-superviseur` ou `dev-admin` (jeton opaque, tel quel).

## Parcours de démonstration (B.7)

1. **Supervision** — `List Tools`, puis exécuter :
   - `list_active_channels`
   - `get_extension_status`
   - `get_cdr_report`
   - `analyze_call_quality` (avec un `channel_id` vu à l'étape 1) — MOS/gigue
   - `get_trunk_utilization`
2. **Pilotage avec validation humaine** — exécuter `originate_call`
   (`endpoint=PJSIP/1001`, `exten=1002`). L'Inspector affiche une **demande de
   confirmation (elicitation)** ; accepter → l'appel part, refuser → `cancelled`.
   Répéter avec un compte `operateur` → `unauthorized` (RBAC).
3. **Écoute** — `spy_channel` (`mode=listen`) : sans `acknowledge_legal=true`
   → `legal_acknowledgement_required` + avertissement légal ; avec, l'écoute
   démarre et l'action est tracée dans le journal d'audit.

## Vérifications transverses

```bash
curl -s http://localhost:8000/metrics | grep mcp_          # Prometheus
docker compose exec mcp-server tail -f logs/audit.jsonl     # journal d'audit
```

## Schémas JSON des outils

Exportés dans [`docs/tool-schemas.json`](tool-schemas.json) —
régénérer : `python scripts/export_tool_schemas.py`.
