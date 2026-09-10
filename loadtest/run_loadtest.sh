#!/usr/bin/env bash
# Test de charge / montée en charge Asterisk (B.7) — jusqu'à 50 canaux simultanés.
#
#   ./loadtest/run_loadtest.sh [asterisk_ip] [exten] [max_calls] [rate] [duration_ms]
#   défauts :                  127.0.0.1     701     50           5      15000
#
# Prérequis : sipp installé (apt install sip-tester  /  docker run ctaloi/sipp).
# Pendant le test, observer Grafana ("Supervision Asterisk MCP") ou :
#   watch -n1 'docker compose exec asterisk asterisk -rx "core show channels count"'
set -euo pipefail

IP="${1:-127.0.0.1}"
EXTEN="${2:-701}"
MAX="${3:-50}"
RATE="${4:-5}"
DUR="${5:-15000}"
HERE="$(cd "$(dirname "$0")" && pwd)"

command -v sipp >/dev/null || { echo "sipp introuvable (apt install sip-tester)"; exit 1; }

echo "==> $MAX appels max vers sip:$EXTEN@$IP:5060, $RATE/s, durée ${DUR}ms"
exec sipp "$IP:5060" \
  -sf "$HERE/uac_invite.xml" \
  -s "$EXTEN" \
  -l "$MAX" \
  -r "$RATE" -rp 1000 \
  -d "$DUR" \
  -m $((MAX * 10)) \
  -trace_stat -trace_err \
  -stf "$HERE/loadtest_stats.csv" \
  -fd 5
