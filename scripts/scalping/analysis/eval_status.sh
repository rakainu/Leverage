#!/bin/bash
# Slope-gate evaluation window status report.
# Run on VPS: ssh root@46.202.146.30 'bash /docker/scalping/eval_status.sh'

DEPLOY_TS="2026-04-25T01:10:25Z"
DEPLOY_EPOCH=$(date -u -d "$DEPLOY_TS" +%s)
DB=/docker/scalping/data/bridge.db
NOW_EPOCH=$(date -u +%s)
ELAPSED=$((NOW_EPOCH - DEPLOY_EPOCH))
HOURS=$((ELAPSED / 3600))
MINS=$(((ELAPSED / 60) % 60))

echo "=== Slope-gate eval window ==="
echo "Deploy:  $DEPLOY_TS (threshold = 0.03%)"
echo "Now:     $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
echo "Elapsed: ${HOURS}h ${MINS}m"
echo "End:     48h elapsed AND >=30 fills (whichever is longer)"
echo

echo "=== Fills since deploy ==="
sqlite3 -header -column "$DB" "
SELECT COUNT(*) AS n,
       SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) AS wins,
       SUM(CASE WHEN pnl_usdt < 0 THEN 1 ELSE 0 END) AS losses,
       ROUND(SUM(pnl_usdt), 2) AS pnl,
       ROUND(AVG(pnl_usdt), 2) AS avg_pnl
FROM trade_log
WHERE opened_at >= '$DEPLOY_TS';"
echo

echo "=== Per-symbol since deploy ==="
sqlite3 -header -column "$DB" "
SELECT symbol,
       COUNT(*) AS n,
       SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) AS wins,
       SUM(CASE WHEN pnl_usdt < 0 THEN 1 ELSE 0 END) AS losses,
       ROUND(SUM(pnl_usdt), 2) AS pnl,
       ROUND(AVG(pnl_usdt), 2) AS avg
FROM trade_log
WHERE opened_at >= '$DEPLOY_TS'
GROUP BY symbol;"
echo

echo "=== Open positions right now ==="
sqlite3 -header -column "$DB" "
SELECT id, symbol, side, ROUND(entry_price, 4) AS entry,
       trail_active, ROUND(trail_high_price, 4) AS trail_high
FROM positions WHERE closed_at IS NULL;"
echo

echo "=== Flat-slope fill blocks (from current docker log buffer) ==="
# docker logs only retains a finite buffer — counts here are best-effort.
BLOCK_LINES=$(docker logs scalping --since "$DEPLOY_TS" 2>&1 | grep -c "fill blocked by flat EMA slope")
echo "blocks logged in current buffer: $BLOCK_LINES"
echo "(Last 5 block events:)"
docker logs scalping --since "$DEPLOY_TS" 2>&1 | grep "fill blocked by flat EMA slope" | tail -5
echo

echo "=== Pending signals right now ==="
sqlite3 -header -column "$DB" "
SELECT id, symbol, action, ROUND(signal_price, 4) AS signal_px,
       created_at, expires_at, status
FROM pending_signals
WHERE status = 'pending'
ORDER BY id DESC;"
