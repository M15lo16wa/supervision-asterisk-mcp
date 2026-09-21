#!/usr/bin/env bash
# Obtient un JWT Keycloak (password grant) pour un utilisateur de test.
#
#   ./scripts/get_token.sh operateur_demo
#   ./scripts/get_token.sh admin_demo | tee /tmp/admin_token.json
#
# Variables (défauts alignés sur .env.example / le realm asterisk) :
#   KEYCLOAK_URL   http://localhost:8080
#   REALM          asterisk
#   CLIENT_ID      mcp-server
#   CLIENT_SECRET  dev-only-mcp-server-secret-CHANGE-ME
#   PASSWORD       admin
set -euo pipefail

USER="${1:-operateur_demo}"
KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8080}"
REALM="${REALM:-asterisk}"
CLIENT_ID="${CLIENT_ID:-mcp-server}"
CLIENT_SECRET="${CLIENT_SECRET:-dev-only-mcp-server-secret-CHANGE-ME}"
PASSWORD="${PASSWORD:-admin}"

curl -sf -X POST \
  "${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=password" \
  -d "client_id=${CLIENT_ID}" \
  -d "client_secret=${CLIENT_SECRET}" \
  -d "username=${USER}" \
  -d "password=${PASSWORD}" \
  -d "scope=openid"
echo
