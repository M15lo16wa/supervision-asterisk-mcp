#!/usr/bin/env bash
# Configure un conteneur Asterisk EXISTANT pour les tests du serveur MCP :
#   - active le serveur HTTP (requis par ARI)
#   - crée l'utilisateur ARI  mcp_ari  / changeme_ari
#   - crée l'utilisateur AMI  mcp_ami  / changeme_ami  et écoute sur 0.0.0.0
#   - charge un mini dialplan + 2 endpoints PJSIP de test (1001, 1002)
#
#   ./scripts/setup_test_asterisk.sh [nom_conteneur]     (défaut: asterisk-server)
#
# Modifs additives (manager.d/, http.conf, ari.conf, pjsip_mcp.conf,
# extensions_mcp.conf) — la config existante n'est pas écrasée.
set -euo pipefail
# Git Bash / MSYS : ne pas réécrire les chemins /etc/... passés à docker exec
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'
C="${1:-asterisk-server}"

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
docker exec -i "$C" tee /etc/asterisk/ari.conf >/dev/null <<'EOF'
[general]
enabled = yes
pretty = yes
allowed_origins = *

[mcp_ari]
type = user
read_only = no
password = changeme_ari
password_format = plain
EOF

# --- Métriques Prometheus (res_prometheus, Basic Auth) ---
docker exec -i "$C" tee /etc/asterisk/prometheus.conf >/dev/null <<'EOF'
[general]
enabled = yes
core_metrics_enabled = yes
uri = metrics
auth_username = prometheus
auth_password = changeme_metrics
EOF

# --- CDR temps réel via AMI ---
docker exec -i "$C" tee /etc/asterisk/cdr_manager.conf >/dev/null <<'EOF'
[general]
enabled = yes
[mappings]
EOF
docker exec "$C" bash -c 'grep -q "^enable *= *yes" /etc/asterisk/cdr.conf || sed -i "s/^enable *=.*/enable = yes/" /etc/asterisk/cdr.conf'

# --- AMI ---
docker exec "$C" sed -i 's/^bindaddr *= *127\.0\.0\.1/bindaddr = 0.0.0.0/' /etc/asterisk/manager.conf
docker exec "$C" bash -c 'grep -q "manager.d" /etc/asterisk/manager.conf || echo "#include \"manager.d/*.conf\"" >> /etc/asterisk/manager.conf'
docker exec -i "$C" tee /etc/asterisk/manager.d/mcp.conf >/dev/null <<'EOF'
[mcp_ami]
secret = changeme_ami
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
echo "==> OK  (AMI mcp_ami/changeme_ami · ARI mcp_ari/changeme_ari · /metrics prometheus/changeme_metrics)"
