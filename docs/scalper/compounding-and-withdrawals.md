# Scalper — compounding sizing + profit withdrawals

Deployed 2026-06-14 on the paper bridge (`scalper-bridge`, Lighter). Designed to
be **identical for paper and live trading** — the only difference at go-live is
that a withdrawal row also corresponds to a real USDT transfer off the exchange.

## What it does

1. **Compounding sizing.** Each position's margin scales with live account equity
   instead of a flat amount: a coin trades its base margin (`$500`) when equity is
   at `base_equity` (`$3,600`), and grows proportionally as the account grows —
   capped at `base × cap_mult` (`3× = $1,500 margin / $15k notional`) and floored
   at 0 (so a drawn-down account de-risks automatically).

2. **Weekly profit withdrawal.** Once per ISO week, the bridge skims **realized**
   equity above the target (`base_equity × target_mult = $10,800`) into a ledger.
   It never touches unrealized (open) P&L. Holding the account at the target keeps
   sizing pinned at the cap, so the book trades full-size while the surplus is paid
   out. Backtest of this structure: ~$1,871/wk after the account reaches target
   (~50 days from $3,600), keeping 6-coin trade frequency.

## Why these choices (real-money correctness)

- **Size off true equity, not a fixed number** — compounds gains, de-risks losses.
- **Cap at 3×** — the thin coins (BNB/XMR, ~$4–8M daily vol) bound how large a
  position the book absorbs cleanly; $15k notional is comfortably inside that.
- **Withdraw realized-only** — never bank open-trade gains that can reverse.
- **Curve tracks GROSS trading value** — a weekly skim drops the balance but is not
  a trading loss, so it must not register as drawdown. The bridge snapshots gross
  (`initial + realized + unrealized`); the dashboard shows the net post-withdrawal
  balance as "Equity" and the true total return (`gross/initial − 1`).
- **Cadence enforced via the ledger** (not in-memory state) — survives restarts;
  at most one withdrawal per ISO week even across redeploys.

## Code map

- `src/lighter_bridge/sizing.py` — pure, unit-tested math (`compound_margin`,
  `withdrawal_surplus`). No I/O; the single source of truth for both modes.
  Tests: `tests/test_sizing_withdrawal.py`.
- `config.py` — `SizingConfig` (`mode`/`base_equity`/`cap_mult`) +
  `WithdrawalConfig` (`enabled`/`cadence`/`target_mult`). Off by default; opt-in.
- `executor.py` — `open_position(..., margin_override=)` threads the compounded
  margin; the executor stays a pure executor, the *policy* lives in the bridge.
- `main.py` — `_entry_margin()` (applied at every regime entry), `_maybe_withdraw()`
  (weekly skim in the heartbeat), `_equity_breakdown()` (net of withdrawn for
  sizing), gross snapshot for the curve, `/status` + startup banners.
- `db.py` — `withdrawals` ledger + `record_withdrawal` / `withdrawn_total` /
  `last_withdrawal_ts`.
- Dashboard — `/panel/withdrawals` + `partials/withdrawals.html`; KPI equity shows
  the net balance with a "$X withdrawn" note and total-return %.

## Live config (paper)

```yaml
sizing:     { mode: compound, base_equity: 3600, cap_mult: 3.0 }
withdrawal: { enabled: true,  cadence: weekly,   target_mult: 3.0 }
```

## Going live (future)

The bridge ledger is the instruction set: when `_maybe_withdraw()` records a row,
transfer that USDT off the exchange. Everything else (sizing off real collateral,
realized-only skim, caps) already behaves correctly against a live account — point
`_equity_breakdown` at the exchange balance instead of the paper book.

## Revert

- Sizing back to fixed: `sizing.mode: fixed`.
- Stop withdrawals: `withdrawal.enabled: false`.
- Both are inert when disabled; restart to apply. Pre-change DB archived at
  `data/archive/scalper-pre-compound-2026-06-14.db`.
