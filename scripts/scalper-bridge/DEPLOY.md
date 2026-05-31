# Scalper paper bridge + dashboard — deployment (2026-05-30)

Live paper-trading deployment of the **regime-gated VWAP mean-reversion** strategy
(`regime_mr`, validated in `../scalping/analysis/scalp_search_2026-05-30/STRATEGY.md`)
on the Lighter zero-fee paper venue. 5-coin basket (SOL/ETH/ZEC/HYPE/BTC), **15m**,
long+short, native signals (no TradingView/webhook). Dedicated "Scalper" Telegram bot.

Validated: pooled PF 1.49 | 89% WR | 192 trades/wk | OOS 1.42 | walk-forward 4/4 |
2×-slip 1.43 | every coin PF ≥ 1.33 | 10× = 0 liquidations.

## What runs on the VPS (srv1370094, 46.202.146.30)

| Container | Dir | Port | Purpose |
|---|---|---|---|
| `scalper-bridge` | `/docker/scalper-paper` | — (native, no webhook) | the strategy |
| `scalper-dashboard` | `/docker/lighter-dashboard` (compose `docker-compose.scalper.yml`) | 127.0.0.1:8096 | stats UI |

- **Dashboard:** https://scalper.agentneo.cloud (Traefik file-provider route
  `/docker/traefik-mncm/config/scalper-dash.yml`, LetsEncrypt + basic-auth `radk9`).
- **DB:** `/docker/scalper-paper/data/scalper.db` (bridge writes; dashboard reads RO).
- **Telegram:** dedicated bot `@scalpbigbot` ("Scalper"), token in `.env`, tagged `SCALPER`.

## Strategy = code mapping (vs the bridge template it was cloned from)
- `src/lighter_bridge/regime.py` — NEW. `prepare_regime()` = EXACT port of the backtest
  `strat_lib.regime_mr` (2617/2617 signal parity, 0 entry-level mismatches). Uses
  btengine's plain-EWM EMA + Wilder-RMA ATR + daily session VWAP (NOT indicators.py
  Pine variants). Stateless.
- `config.py` — added `RegimeConfig` (params + entry_valid_bars).
- `main.py` — `signal_source: regime` branch (`_on_new_bar_regime`, `_open_regime`,
  `_close_regime`) + `exit_model: regime` in the position loop + regime restart-recovery.
  Maker-limit entry filled on a bar trading through (matches backtest); SL/TP on the 5s
  live mark (stop wins on a tie); time stop on bar close.
- `executor.py` — `OpenPosition` gains `tp_price`.
- `db.py` — `trade_log.initial_tp` column (so SL+TP survive a restart) + safe migration.
- Sizing: FIXED notional — per-symbol `margin_usdt × leverage` ($250 @ 10×).

Behavioral fidelity (bridge replay vs btengine): WR identical ±1%, all 5 coins
profitable, bridge marginally conservative (~6% fewer trades, slightly lower PF) —
does NOT overstate the edge.

## Config: `config.scalper.yaml` (bridge), `../lighter-dashboard/config.scalper.yaml` (UI)
Market IDs (live Lighter): SOL=2, ETH=0, ZEC=90, HYPE=24, BTC=1 (verify BTC on VPS).
Start equity $1000.

## Deploy / operate
```bash
# bridge
cd /docker/scalper-paper && docker compose -f docker-compose.scalper.yml up -d --build
docker logs scalper-bridge --tail 50
# dashboard
cd /docker/lighter-dashboard && docker compose -f docker-compose.scalper.yml up -d --build
# traefik route hot-reloads from /docker/traefik-mncm/config/scalper-dash.yml
```

## Paper kill-switch (from STRATEGY.md — the edge is thin above breakeven WR ≈ 84%)
- Rolling 100-trade win rate < 85% → halt.
- Rolling 30-day PF < 1.15 → halt and review.
- Any single coin's 50-trade WR < 84% → drop that coin.
- Account drawdown > 6% → halt (backtest 26-wk maxDD ~$54 on $250 @10×).

## Notes
- 10× leverage ONLY (the 2.0×ATR stop sits too close to liquidation at 20×+).
- Bootstrap bars never fire entries — only genuinely new 15m closes trigger trades.
- Window the edge was validated on was net-bullish; short side + down/chop regime are
  the things to confirm in paper.
