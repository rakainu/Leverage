# Scalping V3 — deploy + revert runbook

## What V3 is

Multi-symbol scalping bridge with **per-symbol margin** and **auto-scaled $-thresholds**.

- Strategy DNA (SL / BE / trail dollar values) lives in `defaults` at $100 baseline margin.
- Each enabled symbol sets its own `margin_usdt`. All $-thresholds auto-scale by `(symbol_margin / default_margin)`.
- Per-symbol explicit overrides supported (rare — only if a token needs a different ratio than the default).
- Designed to generalize to any number of tokens. Adding a new symbol = 5-line YAML block.

## Active deployment (2026-05-12)

| Container | Status | Path | DB | Use |
|---|---|---|---|---|
| `scalping` (v1) | stopped | `/docker/scalping/` | preserved | V1 trade history reference |
| `scalping-v2` | stopped | `/docker/scalping-v2/` | preserved | V2 trade history reference |
| `scalping-v3` | **running** | `/docker/scalping-v3/` | fresh | **ACTIVE** |

Traefik route `blofin-bridge.srv1370094.hstgr.cloud/webhook/pro-v3` → `127.0.0.1:8788` → whichever container holds the port. Only one at a time.

## Effective V3 thresholds (at the time of deploy)

Defaults at $100 baseline:

| Field | Value |
|---|---|
| sl_loss_usdt | $13 |
| breakeven_usdt | $12 |
| lock_profit_activate_usdt | $18 |
| lock_profit_usdt | $15 |
| trail_activate_usdt | $30 |
| trail_start_usdt | $32 |
| trail_distance_usdt | $15 |

Per-symbol (auto-scaled):

| Symbol | margin | sl | be | lock_act | lock | trail_act | trail_start | trail_dist |
|---|---|---|---|---|---|---|---|---|
| ZEC-USDT | $250 | $32.50 | $30.00 | $45.00 | $37.50 | $75.00 | $80.00 | $37.50 |
| SOL-USDT | $30 | $3.90 | $3.60 | $5.40 | $4.50 | $9.00 | $9.60 | $4.50 |

## Health-check commands

```bash
ssh root@46.202.146.30 "docker ps --filter name=scalping --format 'table {{.Names}}\t{{.Status}}'"
ssh root@46.202.146.30 "docker exec scalping-v3 python -c 'import httpx; print(httpx.get(\"http://localhost:8787/health\", timeout=3).json())'"
curl -s "https://blofin-bridge.srv1370094.hstgr.cloud/audit?secret=<BRIDGE_AUDIT_SECRET>&limit=200" | jq .summary_all
```

`BRIDGE_AUDIT_SECRET` lives in `/docker/scalping-v3/.env` (copied from v2).

## Revert procedures

### Revert V3 → V2 (Option B, $25 SL wider trail)

```bash
ssh root@46.202.146.30 "
  docker stop scalping-v3 &&
  cd /docker/scalping-v2 && docker compose up -d
"
```

### Revert V3 → V1 (original $13 SL, single-config)

```bash
ssh root@46.202.146.30 "
  docker stop scalping-v3 &&
  cd /docker/scalping && docker compose up -d
"
```

### Full restore from V3-pre-deploy backup (paranoid mode)

```bash
ssh root@46.202.146.30 "
  docker stop scalping-v3 scalping-v2 scalping 2>/dev/null
  cd /docker && tar -xzf /root/scalping-v2-pre-v3-backup-2026-05-12.tar.gz
  cd /docker/scalping-v2 && docker compose up -d
"
```

## Code changes (relative to V2)

Files touched:

- `src/blofin_bridge/config.py` — added `ResolvedSymbolConfig` with auto-scaling; extended `SymbolConfig` with optional $-overrides
- `src/blofin_bridge/db/schema.sql` — added `margin_usdt`, `leverage` columns to `positions` table
- `src/blofin_bridge/state.py` — `PositionRow` carries margin/leverage; `create_position` stores them (defaults preserved for backward compat)
- `src/blofin_bridge/handlers/entry.py` — passes margin/leverage to `create_position` at entry
- `src/blofin_bridge/poller.py` — every state-machine method resolves thresholds via `_thresholds_for(pos)`; falls back to instance defaults if no `symbol_configs` (tests)
- `src/blofin_bridge/main.py` — symbol_configs built from ResolvedSymbolConfig dict; audit endpoint returns full defaults + per-symbol view
- `tests/test_config.py` — +5 scaling tests
- `tests/test_poller.py` — +4 per-symbol tests
- `config/blofin_bridge.v3.yaml` — V3 config

Backward compat: poller's instance kwargs still accepted as fallbacks. Old tests pass without modification.

## How to add a new token

```yaml
symbols:
  DOGE-USDT:
    enabled: true
    margin_usdt: 100        # any positive number — thresholds scale automatically
    leverage: 30
    margin_mode: isolated
    sl_policy: p2_step_stop
```

If DOGE needs a tighter SL (more volatile than ZEC), add the explicit override:

```yaml
  DOGE-USDT:
    enabled: true
    margin_usdt: 100
    leverage: 30
    margin_mode: isolated
    sl_policy: p2_step_stop
    sl_loss_usdt: 8           # overrides the auto-scaled value (would be $13 at $100 margin)
```

## TradingView alerts

**No change.** Same webhook URL `blofin-bridge.srv1370094.hstgr.cloud/webhook/pro-v3`. Whichever container holds the port handles them. Pro V3 alerts on ZEC and SOL fire as before.

## Eval window

Target: 14 days OR ≥40 closed trades, whichever first.

- **Day-7 checkpoint:** ZEC ≥ $15/day → continue. Below $5/day → pause + reassess.
- **Day-14 decision:** compare to V1 baseline ($1.99/trade ZEC, $9.96/day total). If V3 holds the per-trade edge at 2.5× sizing, ship to live with a starting balance.

## Future upgrades to consider

- **More tokens:** target high-vol majors first (AVAX, INJ, SUI). Test each at baseline margin for 2 weeks before scaling.
- **Per-symbol enable/disable from Telegram:** already exists via EntryGate, exposed in the `/Fin` bot menu.
- **Strategy variants per symbol:** if a token needs different SL/trail RATIOS (not just scaled $), add a `strategy: <name>` selector per symbol. Out of scope for V3.
- **Live deployment:** once V3 proves out on demo, flip `BLOFIN_ENV=live` in `/docker/scalping-v3/.env` and restart. Use a deliberately small starting balance for the first 30 days.
- **ATR-based threshold scaling:** instead of fixed $-thresholds, scale by realized vol. Bigger refactor — only consider if regime changes cause meaningful underperformance during V3 eval.
