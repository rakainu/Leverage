# Next Steps — Build Order

Follow this order. Do not skip or merge steps. Each step produces a working artifact before the next one starts.

## 1. Connect Binance data

**Goal:** a thin client that talks to Binance public futures REST. No scoring, no ranking, just reliable data.

Endpoints to wire up (all public, no auth):

- `GET /fapi/v1/exchangeInfo` — list all USDT-M perp symbols
- `GET /fapi/v1/klines` — OHLCV bars (1d, 4h)
- `GET /fapi/v1/premiumIndex` — current funding rate per symbol
- `GET /fapi/v1/fundingRate` — historical funding
- `GET /fapi/v1/openInterest` — current OI per symbol
- `GET /futures/data/openInterestHist` — OI history (5m/15m/30m/1h/2h/4h/6h/12h/1d)
- `GET /fapi/v1/ticker/24hr` — 24h volume / quote volume

Deliverable: `src/binance_client.py` with typed return shapes; `tests/test_binance_client.py` hitting captured fixtures.

## 2. Fetch symbols

**Goal:** produce the candidate universe — every USDT-M perp except delisted / non-trading.

Filter out: non-`TRADING` status, non-`PERPETUAL` contract type, non-`USDT` quote, banned tickers (configurable).

Deliverable: `src/scanner.py::fetch_universe()` returning a list of symbols with metadata (baseAsset, onboardDate, contractSize).

## 3. Score symbols

**Goal:** for each symbol, compute the five component scores and the composite Squeeze Score.

Components:
- Flatness (0–100)
- Funding (0–100)
- OI growth (0–100)
- Non-pumped (0–100)
- Liquidity gate (binary + ramp)

Plus meme/small-cap bias multiplier (Phase 2 — stub returns 1.0 in Phase 1).

See `docs/scoring-rules.md` for formulas.

Deliverable: `src/scoring.py` — pure functions, no I/O. 100% unit tested.

## 4. Save daily history

**Goal:** every scan run writes a timestamped snapshot so we can diff tomorrow.

Two files per run:
- `data/snapshots/YYYY-MM-DD.json` — full detail (every symbol, every component)
- `data/history/scores.csv` — append-only, one row per symbol per day (symbol, date, score, components, price)

See `docs/data-schema.md`.

Deliverable: `src/history.py::write_snapshot()` + `append_history()`.

## 5. Compare changes

**Goal:** day-over-day and week-over-week diffs. Who got hotter? Who just entered the top 30? Who graduated out (already pumped)?

Deliverable: `src/compare.py` — functions that read two snapshots and produce `{new_entries, score_risers, score_fallers, exits}`.

## 6. Format alerts

**Goal:** ranked list + diff → human-readable Telegram/Discord messages.

No sending yet. Just produce the text. Templates in `alerts/templates/`, rules in `docs/alert-rules.md`.

Deliverable: `src/alerts.py::format_daily_digest()` returning a string for each channel type. Example output saved to `outputs/daily/YYYY-MM-DD.md`.

## 7. (Optional) Companion data sources

Only after 1–6 are solid.

- **CoinGecko:** market cap, age, category tags (meme, new-listing). Feeds the meme/small-cap multiplier.
- **Coinalyze:** cross-exchange OI aggregation, liquidation feed. Adds a "liquidation pressure" component.

Keep these in `src/sources/` as drop-in modules so the core scanner doesn't care which source is enabled.

---

## Nightly cron (deployed 2026-04-22)

The scanner runs automatically every day on the VPS.

| | |
|---|---|
| **Schedule** | `30 6 * * *` — 06:30 UTC daily |
| **Crontab entry** | `30 6 * * * /root/SqueezeWatch/scripts/run_daily_scan.sh >> /var/log/squeezewatch.log 2>&1` |
| **Wrapper script** | `/root/SqueezeWatch/scripts/run_daily_scan.sh` (tracked at `SqueezeWatch/scripts/run_daily_scan.sh`) |
| **Log file** | `/var/log/squeezewatch.log` — rotated monthly, keeps 12, compressed (`/etc/logrotate.d/squeezewatch`) |
| **Snapshot output** | `/root/SqueezeWatch/data/snapshots/YYYY-MM-DD.json` |
| **History CSV** | `/root/SqueezeWatch/data/history/scores.csv` (append-only) |
| **Digest output** | `/root/SqueezeWatch/outputs/daily/YYYY-MM-DD.md` |
| **VPS timezone** | UTC (`Etc/UTC`) — cron uses system time, so `06:30` = 06:30 UTC natively |

### Verify the cron ran (next morning)

```bash
ssh root@46.202.146.30 'tail -30 /var/log/squeezewatch.log'
ssh root@46.202.146.30 'ls -la /root/SqueezeWatch/outputs/daily/ /root/SqueezeWatch/data/snapshots/ | tail -5'
```

If the `YYYY-MM-DD.md` digest exists and the log shows `Done in XXs. NNN scored...`,
the cron ran successfully.

### Manually trigger the same cron command right now (no wait)

```bash
ssh root@46.202.146.30 '/root/SqueezeWatch/scripts/run_daily_scan.sh >> /var/log/squeezewatch.log 2>&1'
```

This runs the identical invocation cron will run — same env, same paths, same log
destination — so it's the exact reproduction test.

### Next build phase

Day-over-day comparison now has something to chew on after 2 consecutive scans.
After ~5–7 days of accumulated snapshots, good follow-ups are:

1. Add the four positioning endpoints from the skills hub (long-short-ratio, etc).
2. Add Telegram push for the triggered-alert section of the digest.
3. Hit-rate evaluation (did today's top 15 actually squeeze in the next 7/14/30d?).

None of these should block the cron — it just runs.
