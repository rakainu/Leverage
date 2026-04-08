#!/usr/bin/env bash
# BloFin bridge smoke test — fires the full webhook lifecycle against the
# deployed bridge. Requires BRIDGE_URL and BRIDGE_SECRET env vars.

set -euo pipefail
: "${BRIDGE_URL:?must be set}"
: "${BRIDGE_SECRET:?must be set}"

fire() {
  local action=$1
  echo "==> $action"
  curl -sS -X POST "$BRIDGE_URL/webhook/pro-v3" \
    -H 'Content-Type: application/json' \
    -d "{\"secret\":\"$BRIDGE_SECRET\",\"symbol\":\"SOL-USDT\",\"action\":\"$action\",\"source\":\"smoke\"}"
  echo
  sleep 2
}

echo "HEALTH CHECK"
curl -sS "$BRIDGE_URL/health"
echo

echo
echo "== LONG LIFECYCLE: buy -> tp1 -> tp2 -> tp3 =="
fire buy
fire tp1
fire tp2
fire tp3

echo
echo "STATUS:"
curl -sS "$BRIDGE_URL/status?secret=$BRIDGE_SECRET" | head -c 2000
echo
