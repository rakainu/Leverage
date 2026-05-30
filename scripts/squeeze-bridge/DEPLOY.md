# Squeeze paper bridge + dashboard — deployment (2026-05-30)

Live paper-trading deployment of the **squeeze compression→expansion** strategy
(validated in `../scalping/analysis/lighter_strat_2026-05-30/STRATEGY.md`) on the
Lighter zero-fee paper venue. 4-coin basket (SOL/ETH/ZEC/HYPE), 1h, long+short,
native signals (no TradingView/webhook dependency).

## What runs on the VPS (srv1370094, 46.202.146.30)

| Container | Dir | Port | Purpose |
|---|---|---|---|
| `squeeze-bridge` | `/docker/squeeze-paper` | — (native, no webhook) | the strategy |
| `squeeze-dashboard` | `/docker/lighter-dashboard` (compose `docker-compose.squeeze.yml`) | 127.0.0.1:8095 | stats UI |

- **Dashboard:** https://squeeze.agentneo.cloud (Traefik file-provider route
  `/docker/traefik-mncm/config/squeeze-dash.yml`, LetsEncrypt + basic-auth `radk9`).
- **DB:** `/docker/squeeze-paper/data/squeeze.db` (bridge writes; dashboard reads RO).
- **Telegram:** shares the existing bot (`.env` `TELEGRAM_BOT_TOKEN`/`CHAT_ID`),
  tagged `SQUEEZE` via compose env. Swap `.env` token for a dedicated bot if wanted.

## Strategy = code mapping (vs the bridge defaults it was cloned from)
- `src/lighter_bridge/squeeze.py` — NEW. `prepare_squeeze()` = exact port of the
  backtest `strat_lib.squeeze_expansion` (215/216 signal parity). Stateless.
- `indicators.py` — added `calc_sma`, `calc_stdev` (population, matches backtest BB).
- `config.py` — added `SqueezeConfig` (params + risk-based sizing knobs).
- `main.py` — `signal_source: squeeze` branch (`_on_new_bar_squeeze`, `_open_squeeze`,
  `_squeeze_trail_update`, `_close_squeeze`) + `exit_model: atr_trail` in the position
  loop + squeeze restart-recovery in `restore_open_positions`.
- `executor.py` — `OpenPosition` gains `atr_entry/best_close/bars_held`; `open_position`
  accepts a `base_amount` override for risk-based sizing.
- Exits: initial 1.5×ATR hard stop; trail 3×ATR behind best **close** (ratcheted on bar
  close, matching the backtest); 48-bar time stop. Stop checked on the 5s live mark.
- Sizing: **risk-based** — notional so the hard stop risks `risk_frac` (0.75%) of equity,
  capped at 20× — reproduces the backtested 13.3% maxDD (not the bridge's fixed margin×lev).

## Config: `config.squeeze.yaml` (bridge), `deploy/dashboard.config.squeeze.yaml` (UI)
Market IDs (live Lighter): SOL=2, ETH=0, ZEC=90, HYPE=24. Start equity $1000.

## Deploy / operate
```bash
# bridge
cd /docker/squeeze-paper && docker compose -f docker-compose.squeeze.yml up -d --build
docker logs squeeze-bridge --tail 50
# dashboard
cd /docker/lighter-dashboard && docker compose -f docker-compose.squeeze.yml up -d --build
# traefik route is hot-reloaded from /docker/traefik-mncm/config/squeeze-dash.yml
```

## Paper kill-switch (from STRATEGY.md)
Drop if portfolio paper PF < 1.20 after 40 closed trades, or maxDD > 18%. Cut any
single coin with PF < 1.0 after 40 (watch ETH — weakest OOS).

## Notes
- First start can fail the 30s mark-feed verify on cold order books; `restart:
  unless-stopped` self-heals (observed once on first deploy).
- Bootstrap bars never fire entries — only genuinely new 1h closes trigger trades.
- Pro V3 (the prior Lighter strategy) was archived to
  `/docker/_archive/pro-v3-lighter-2026-05-30.tar.gz` and its containers stopped.
