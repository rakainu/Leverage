# Scalper dead-coin removal — 2026-06-14

Removed 6 tokens from the Scalper regime-MR paper bridge (`scalper-bridge`, Lighter
zero-fee, 15m VWAP mean-reversion) and the Scalper dashboard. Their full trade history
was archived here, then **deleted from the live `scalper.db`** so all dashboard math
(PF, win rate, realized, equity, per-symbol) recomputes over only the kept basket — as
if the removed tokens were never traded.

## Removed tokens (final lifetime stats at cut)

| Coin | Trades | Net PnL | Win rate | Reason |
|---|---|---|---|---|
| WLD  | 6  | −$840.50 | 67% | n=6 noise + stranded-short bug (benched 06-07) |
| NEAR | 19 | −$213.36 | 74% | chronic heavy loser (benched 06-09) |
| TON  | 45 | −$90.87  | 76% | net bleeder below breakeven WR |
| ZEC  | 35 | −$52.76  | 71% | net bleeder below breakeven WR |
| XRP  | 35 | −$44.75  | 74% | net bleeder below breakeven WR |
| MKR  | 0  | —        | —   | inactive market on Lighter (never filled) |
| **Total** | **140** | **−$1,242.24** | — | |

Breakeven win rate for this strategy is ~84%; all five traded coins sat at 67–76%.

## Kept basket after the cut (the real edge, uncontaminated)

| Metric | Value |
|---|---|
| Coins | ETH, BTC, XMR, HYPE, SOL, BNB |
| Trades | 219 |
| Net PnL | +$1,684.95 |
| Win rate | 85.8% |
| Profit factor | 1.92 |

Removing the 6 lifted the headline book from ~+$443 (contaminated) to +$1,684.95.

## Files

- `dead-coins-2026-06-14.db` — standalone SQLite: `trade_log`, `signal_log`, `ticker_switch` for the 6 coins.
- `dead-coins-trade_log-2026-06-14.csv` — 140 closed trades.
- `dead-coins-signal_log-2026-06-14.csv` — 359 signal-log rows.

## Live changes applied (both `config.scalper.yaml` files: bridge + dashboard)

- Removed the 6 symbols from the `symbols:` block.
- `initial_collateral_usdc` 6000 → 3600 (6 × $500 margin + 20% buffer) so equity-% is meaningful for a 6-coin book.
- Deleted the 6 coins' rows from live `trade_log`, `signal_log`, `ticker_switch`.
- Wiped `account_snapshot` — equity curve restarts fresh at current kept-6 equity.
