# scalping-v3.1 — deploy / revert / status

V3.1 = V3 + three entry-quality filters from the 2026-05-15 engine sweep:
1. **slope gate**: `min_5m_slope_pct` raised 0.03 → 0.15
2. **weekday block**: `block_weekdays_utc: [6]` (block Sunday UTC)
3. **body/ATR band block**: `block_body_atr_band: [0.3, 0.5]` (skip mid-body retests)

Trail state machine, EMA-retest logic, per-symbol margin scaling — all unchanged.
ZEC SL stays at $32.50 effective. Engine showed the wider-SL variant ($82.50)
is also viable but the conservative call is to validate filters alone first;
see "Wide-SL variant" below to switch later.

V3 (`/docker/scalping-v3/`) is untouched. v3 and v3.1 cannot run simultaneously
(both bind host port 8788 and would race the same BloFin account).

A tarball of v3 at the moment of v3.1 creation lives at
`/root/scalping-v3-backup-2026-05-15T162153Z.tar.gz`.

---

## Status

```bash
ssh root@46.202.146.30 "docker ps -a --filter name=scalping --format '{{.Names}}\t{{.Status}}'"
```

## Deploy v3 → v3.1

```bash
ssh root@46.202.146.30 "
  cd /docker/scalping-v3 && docker compose stop &&
  cd /docker/scalping-v3.1 && docker compose up -d --build
"
```

The first deploy will `--build` the image. Subsequent restarts can drop the flag.

Verify it came up healthy:

```bash
ssh root@46.202.146.30 "docker ps --filter name=scalping-v3.1 --format '{{.Names}}\t{{.Status}}'"
# tail logs for a sanity-check
ssh root@46.202.146.30 "docker logs -f scalping-v3.1 --tail 50"
```

You should see slope/weekday/body filter logs appear when retests are blocked:
- `fill blocked by flat EMA slope: |0.0521%| < 0.150% threshold ...`
- `fill blocked: weekday 6 in block list [6]`
- `fill blocked: body/ATR 0.412 in skip band [0.30, 0.50)`

## Revert v3.1 → v3

```bash
ssh root@46.202.146.30 "
  cd /docker/scalping-v3.1 && docker compose stop &&
  cd /docker/scalping-v3 && docker compose up -d
"
```

V3 container resumes against the same `/docker/scalping-v3/data/bridge.db`
it had before — no state loss. Open positions (if any) continue.

## Hard revert (if v3 dir got corrupted somehow)

```bash
ssh root@46.202.146.30 "
  docker stop scalping-v3.1 2>/dev/null;
  cd /docker && rm -rf scalping-v3 &&
  tar -xzf /root/scalping-v3-backup-2026-05-15T162153Z.tar.gz &&
  cd /docker/scalping-v3 && docker compose up -d
"
```

## Eval window for v3.1

Run for whichever comes first: **>= 30 closed trades** OR **14 days**.
Then compare v3.1 trade_log against v3 (combined V1+V2+V3 baseline if needed
for sample size — see `scripts/scalping/analysis/v3_zec_sl_sweep.py`).

```bash
# v3.1 trade log
ssh root@46.202.146.30 "sqlite3 /docker/scalping-v3.1/data/bridge.db '
  SELECT
    COUNT(*) AS n,
    ROUND(SUM(pnl_usdt),2) AS net,
    SUM(CASE WHEN pnl_usdt>0 THEN 1 ELSE 0 END) AS wins,
    ROUND(100.0 * SUM(CASE WHEN pnl_usdt>0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS wr_pct
  FROM trade_log WHERE symbol = \"ZEC-USDT\";'"
```

If after 30 trades:
- PF >= 1.5 AND WR >= 55% → ship v3.1 as the new live config.
- PF 1.0-1.5 → run another 30 trades before deciding.
- PF < 1.0 → revert to v3, investigate which filter overshot.

## Staged rollout (more cautious)

Instead of flipping all three filters at once, you can disable any subset in
`/docker/scalping-v3.1/config/blofin_bridge.yaml`:

- **Slope only** (the biggest single win):
  ```yaml
  min_5m_slope_pct: 0.15
  block_weekdays_utc: []
  block_body_atr_band: null
  ```
- **Slope + weekday**:
  ```yaml
  min_5m_slope_pct: 0.15
  block_weekdays_utc: [6]
  block_body_atr_band: null
  ```

Edit, then `docker compose restart` v3.1 (no rebuild needed for config changes).

## Wide-SL variant (optional later step)

Once filters are validated, you can widen ZEC's SL to the engine optimum.
Edit `/docker/scalping-v3.1/config/blofin_bridge.yaml` under the ZEC block:

```yaml
ZEC-USDT:
  enabled: true
  margin_usdt: 250
  ...
  sl_loss_usdt: 82.50   # <- uncomment / add this line
```

Then `docker compose restart scalping-v3.1`. The override defeats the
auto-scaling for sl_loss_usdt only; all other thresholds still scale 2.5×.

Engine sweep showed PF stays 2.83–3.10 across SL $42.50–$95 with F8 filters,
so this is well within the robustness basin.

## Key paths

```
/docker/scalping-v3.1/
  config/blofin_bridge.yaml    # filters + thresholds
  data/bridge.db               # state — created fresh on first start
  docker-compose.yml           # container = scalping-v3.1, port 8788
  src/blofin_bridge/
    config.py    # Defaults: + block_weekdays_utc, block_body_atr_band, min_body_atr_ratio
    poller.py    # _process_pending_signals: post-slope filter checks
    main.py      # wires the new Defaults fields to PositionPoller
```
