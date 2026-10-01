#!/usr/bin/env bash
# Configure un conteneur Asterisk EXISTANT pour les tests du serveur MCP :
#   - active le serveur HTTP (requis par ARI)
#   - crée l'utilisateur ARI  mcp_ari
#   - crée l'utilisateur AMI  mcp_ami  et écoute sur 0.0.0.0
#   - charge un mini dialplan + 2 endpoints PJSIP de test (1001, 1002)
#
#   ./scripts/setup_test_asterisk.sh [nom_conteneur]     (défaut: asterisk-server)
#
# Secrets (AMI / ARI / métriques) : lus dans le .env du dépôt (variables
# ASTERISK_AMI_SECRET, ASTERISK_ARI_PASSWORD, ASTERISK_METRICS_PASSWORD). Si
# une valeur est absente ou encore un placeholder `changeme*`, un secret est
# généré et RÉÉCRIT dans le .env — les mêmes valeurs sont donc appliquées à
# Asterisk ET lues par le serveur MCP / Prometheus, sans copier-coller.
#
# Modifs additives (manager.d/, http.conf, ari.conf, pjsip_mcp.conf,
# extensions_mcp.conf) — la config existante n'est pas écrasée.
set -euo pipefail
# Git Bash / MSYS : ne pas réécrire les chemins /etc/... passés à docker exec
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'
C="${1:-asterisk-server}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"

# --- Secrets : le .env est l'unique source de vérité -----------------------
# Les trois secrets ci-dessous sont APPLIQUÉS À ASTERISK (ari.conf,
# manager.d/mcp.conf, prometheus.conf) ET relus par le serveur MCP et
# Prometheus. Une seule valeur doit circuler : si Asterisk et le .env divergent,
# AMI et ARI échouent et les 12 outils renvoient « asterisk_unavailable ».
# Priorité de résolution : variable d'environnement > .env > génération.
# Une valeur générée est réécrite dans le .env — aucun copier-coller.
env_file_get() {
  [ -f "$ENV_FILE" ] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*\(.*\)$/\1/p" "$ENV_FILE" \
    | sed 's/[[:space:]]*$//' | tail -1
}

env_file_set() {
  # $1 = clé, $2 = valeur : remplace la ligne si présente, sinon l'ajoute.
  # Écrit via un temporaire + cat pour conserver inode et permissions.
  [ -f "$ENV_FILE" ] || return 0
  local key="$1" val="$2" tmp
  tmp="$(mktemp)"
  awk -v key="$key" -v val="$val" '
    $0 ~ "^[[:space:]]*" key "[[:space:]]*=" { print key "=" val; seen=1; next }
    { print }
    END { if (!seen) print key "=" val }
  ' "$ENV_FILE" >"$tmp"
  cat "$tmp" >"$ENV_FILE"
  rm -f "$tmp"
}

gen_secret() { openssl rand -hex 24; }

# $1 = clé .env. Échoie le secret résolu ; journalise sur stderr.
resolve_secret() {
  local key="$1" val
  val="${!key:-}"
  [ -n "$val" ] || val="$(env_file_get "$key")"
  case "$(printf '%s' "$val" | tr '[:upper:]' '[:lower:]')" in
    ""|changeme*)
      val="$(gen_secret)"
      echo "==> secret généré pour $key" >&2
      ;;
  esac
  env_file_set "$key" "$val"
  printf '%s' "$val"
}

AMI_SECRET="$(resolve_secret ASTERISK_AMI_SECRET)"
ARI_PASSWORD="$(resolve_secret ASTERISK_ARI_PASSWORD)"
METRICS_PASSWORD="$(resolve_secret ASTERISK_METRICS_PASSWORD)"

if [ ! -f "$ENV_FILE" ]; then
  echo "ATTENTION : $ENV_FILE absent — secrets non persistés, à reporter manuellement." >&2
fi

echo "==> conteneur: $C"
docker exec "$C" asterisk -rx "core show version" | head -1

# --- HTTP (ARI) ---
docker exec -i "$C" tee /etc/asterisk/http.conf >/dev/null <<'EOF'
[general]
enabled = yes
bindaddr = 0.0.0.0
bindport = 8088
enablestatic = no
EOF

# --- ARI ---
# Mot de passe issu du .env (cf. « source de vérité » en tête de script).
docker exec -i "$C" tee /etc/asterisk/ari.conf >/dev/null <<EOF
[general]
enabled = yes
pretty = yes
allowed_origins = *

[mcp_ari]
type = user
read_only = no
password = $ARI_PASSWORD
password_format = plain
EOF

# --- Métriques Prometheus (res_prometheus, Basic Auth) ---
# Mot de passe aligné sur le .env (ASTERISK_METRICS_PASSWORD), source de vérité.
docker exec -i "$C" tee /etc/asterisk/prometheus.conf >/dev/null <<EOF
[general]
enabled = yes
core_metrics_enabled = yes
uri = metrics
auth_username = prometheus
auth_password = $METRICS_PASSWORD
EOF

# --- CDR temps réel via AMI ---
docker exec -i "$C" tee /etc/asterisk/cdr_manager.conf >/dev/null <<'EOF'
[general]
enabled = yes
[mappings]
EOF
docker exec "$C" bash -c 'grep -q "^enable *= *yes" /etc/asterisk/cdr.conf || sed -i "s/^enable *=.*/enable = yes/" /etc/asterisk/cdr.conf'

# --- AMI ---
# Secret aligné sur Asterisk (manager.conf) et le .env (ASTERISK_AMI_SECRET).
docker exec "$C" sed -i 's/^bindaddr *= *127\.0\.0\.1/bindaddr = 0.0.0.0/' /etc/asterisk/manager.conf
docker exec "$C" bash -c 'grep -q "manager.d" /etc/asterisk/manager.conf || echo "#include \"manager.d/*.conf\"" >> /etc/asterisk/manager.conf'
docker exec -i "$C" tee /etc/asterisk/manager.d/mcp.conf >/dev/null <<EOF
[mcp_ami]
secret = $AMI_SECRET
deny = 0.0.0.0/0.0.0.0
permit = 127.0.0.1/255.255.255.255
permit = 10.0.0.0/255.0.0.0
permit = 172.16.0.0/255.240.0.0
permit = 192.168.0.0/255.255.0.0
read = system,call,cdr,dialplan,reporting,agent,user
write = call,reporting,command,originate
EOF

# --- PJSIP endpoints de test ---
docker exec -i "$C" tee /etc/asterisk/pjsip_mcp.conf >/dev/null <<'EOF'
[transport-udp-mcp]
type = transport
protocol = udp
bind = 0.0.0.0:5060

[1001]
type = endpoint
context = mcp-internal
disallow = all
allow = ulaw,alaw,slin16
auth = 1001
aors = 1001
[1001]
type = auth
auth_type = userpass
username = 1001
password = agent1001pass
[1001]
type = aor
max_contacts = 1

[1002]
type = endpoint
context = mcp-internal
disallow = all
allow = ulaw,alaw,slin16
auth = 1002
aors = 1002
[1002]
type = auth
auth_type = userpass
username = 1002
password = agent1002pass
[1002]
type = aor
max_contacts = 1
EOF
docker exec "$C" bash -c 'grep -q pjsip_mcp.conf /etc/asterisk/pjsip.conf || echo "#include \"pjsip_mcp.conf\"" >> /etc/asterisk/pjsip.conf'

# --- File d'attente de démo (get_queue_stats) ---
docker exec -i "$C" tee /etc/asterisk/queues_mcp.conf >/dev/null <<'EOF'
[support]
strategy = leastrecent
timeout = 15
servicelevel = 30
member => PJSIP/1001,0,Agent 1001
member => PJSIP/1002,0,Agent 1002
EOF
docker exec "$C" bash -c 'grep -q queues_mcp.conf /etc/asterisk/queues.conf 2>/dev/null || echo "#include \"queues_mcp.conf\"" >> /etc/asterisk/queues.conf'

# --- Dialplan de test ---
docker exec -i "$C" tee /etc/asterisk/extensions_mcp.conf >/dev/null <<'EOF'
[mcp-internal]
exten => _10XX,1,NoOp(MCP test call to ${EXTEN})
 same => n,Dial(PJSIP/${EXTEN},20)
 same => n,Hangup()
exten => 1001,hint,PJSIP/1001
exten => 1002,hint,PJSIP/1002
exten => 700,1,Answer()
 same => n,Stasis(mcp-voice)
 same => n,Hangup()
exten => 701,1,Answer()
 same => n,Echo()
 same => n,Hangup()
exten => 800,1,Answer()
 same => n,Queue(support,t,,,60)
 same => n,Hangup()
EOF
docker exec "$C" bash -c 'grep -q extensions_mcp.conf /etc/asterisk/extensions.conf || echo "#include \"extensions_mcp.conf\"" >> /etc/asterisk/extensions.conf'

# --- recharge ---
docker exec "$C" asterisk -rx "module reload manager"
docker exec "$C" asterisk -rx "module reload cdr_manager.so" || true
docker exec "$C" asterisk -rx "module reload res_prometheus.so" || true
docker exec "$C" asterisk -rx "module reload app_queue.so" || true
docker exec "$C" asterisk -rx "module reload res_ari.so"
docker exec "$C" asterisk -rx "module reload res_pjsip.so"
docker exec "$C" asterisk -rx "dialplan reload"
docker exec "$C" asterisk -rx "core reload"

echo "==> vérifs"
docker exec "$C" asterisk -rx "manager show users"
docker exec "$C" asterisk -rx "ari show users"
docker exec "$C" asterisk -rx "http show status" | grep -i server
docker exec "$C" asterisk -rx "pjsip show endpoints" | grep -E "Endpoint:|Not in use|Unavailable" || true
docker exec "$C" asterisk -rx "queue show support" || true
docker exec "$C" asterisk -rx "module show like res_prometheus" | tail -1
echo "==> OK"
echo "==> Secrets appliqués à Asterisk ET synchronisés dans : $ENV_FILE"
echo "     ASTERISK_AMI_SECRET / ASTERISK_ARI_PASSWORD / ASTERISK_METRICS_PASSWORD"
echo "     (valeurs non réaffichées : elles restent dans le .env, seul endroit lu par"
echo "      le serveur MCP et Prometheus — les relire avec 'grep ASTERISK_ $ENV_FILE')"
