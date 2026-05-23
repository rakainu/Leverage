# 2026-05-23 — ZEC SL widened to $82.50 + stale blofin-bridge removed

## ZEC SL change

Uncommented the prepared `sl_loss_usdt: 82.50` override in
`scripts/scalping/v3.1-drafts/blofin_bridge.yaml` for ZEC-USDT. The line had
been authored on 2026-05-15 with explicit "uncomment later" instructions; the
follow-up cycle never happened until today.

### Why now

Live evidence over the last 4 days made the case:

| | SL | 17-trade result |
|---|---|---|
| Lighter bridge (V3.1, ZEC + SOL) | $80 | +$954 realized (paper) |
| `scalping-v3.1` BloFin demo (ZEC) | $32.50 | −$48 net (10 SL hits avg −$32, 7 trail wins avg +$39) |

Same signals, same strategy, same V3.1 filters. Only the SL width differed.
Engine sweep (`reference_v3_zec_filter_sl_sweep_2026-05-15`) showed
PF ≥ 2.83 across SL $42–$95 with $82.50 as the recommended value.

### Scope of the change

- One knob, one symbol. ZEC-USDT goes from $32.50 → $82.50.
- SOL-USDT unchanged ($3.90 — only 2 SOL trades on record, no evidence to act on).
- All other parameters (slope 0.15, body band, Sunday block, trail mechanics) unchanged.
- Lighter bridge unchanged.

### Tradeoff

Worst-case loss per SL hit goes from −$41 (real) to ~−$82 (target). 2× per-trade
risk. Engine sweep showed this is the optimum on a 6-month / 52k-bar backtest.

### What to watch

- Net PnL over the next ~20 ZEC trades vs the −$48/17 baseline.
- Trail-SL exit rate (currently 7/17 = 41%) — should rise if more trades reach the trail.
- Max drawdown per trade — explicit acceptance of −$82 worst case.

### Deployment

```bash
scp scripts/scalping/v3.1-drafts/blofin_bridge.yaml \
  root@46.202.146.30:/docker/scalping-v3.1/config/blofin_bridge.yaml
ssh root@46.202.146.30 "cd /docker/scalping-v3.1 && docker compose restart"
```

Verified the new config is loaded in-container:

```
$ docker exec scalping-v3.1 cat /app/config/blofin_bridge.yaml | grep sl_loss_usdt
  sl_loss_usdt: 13      # baseline
    sl_loss_usdt: 82.50 # ZEC-USDT override
```

## blofin-bridge container removed

The original `blofin-bridge` container (port 8787) was removed today after
confirmation that it hadn't received a real webhook since 2026-04-09. The
V3→V3.1 transition rerouted all webhooks to `scalping-v3.1` (port 8788);
the old container was running but doing nothing.

### Actions taken

1. Tarball `/docker/blofin-bridge/` → `/tmp/blofin-bridge-deprecated-2026-05-23.tar.gz` (67 KB).
2. Streamed to backup VPS at `srv1447693:/root/blofin-bridge-deprecated-2026-05-23.tar.gz` (sha256 verified).
3. `docker stop blofin-bridge && docker rm blofin-bridge`.
4. `rm -rf /docker/blofin-bridge`.
5. `docker network prune -f` — removed orphan `blofin-bridge_default` network.

### Final container set on srv1370094

```
NAMES                    STATUS
lighter-bridge           Up 31 minutes (healthy)
scalping-v3.1            Up 3 minutes (healthy)
traefik-mncm-traefik-1   Up 7 days
```

## Memory + CLAUDE.md updated

Removed all references to `blofin-bridge` as the BloFin executor — `scalping-v3.1`
now correctly documented as the actual executor in:

- `C:\Users\rakai\CLAUDE.md`
- `~/.claude/.../memory/reference_vps_layout.md`
- `~/.claude/.../memory/reference_blofin_bridge.md` (rewrote to apply to scalping-v3.1)
- `~/.claude/.../memory/reference_blofin_executor.md` (new file from earlier today)
