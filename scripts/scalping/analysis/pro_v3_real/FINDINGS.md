# Pro V3 Scale-Out — Honest Findings (2026-05-29)

Tested the user's thesis ("Pro V3 entries are good; cut losers fast with a smaller SL")
against the **real, no-repaint Pro V3 webhook history** (1015 signals, ZEC+SOL, Apr 10 –
May 29) replayed over real 5m OHLCV with honest fills (next-bar-open entry, wick SL/TP fills,
measured slippage, conservative intrabar resolution). Exits = ATR scale-out (TP1/TP2/TP3 +
BE-after-TP1). Dual fee profiles + 70/30 walk-forward. Scripts: `extract.py`, `replay.py`,
`sweep_real.py`, `probe_sl.py`. Raw grid: `runs/ALL_real_full.csv`.

## Headline

1. **ZEC has no edge — anywhere.** Every config, every SL, both entry timings, in- and
   out-of-sample, is net-negative (PF 0.55–0.92). The historical "ZEC PnL driver" was the
   phantom-fill artifact; honest ZEC loses. Drop it.

2. **SOL has a real but modest edge — under specific conditions only:**
   - **EMA9 retest entry** (NOT enter-at-signal). The retest filters 518→~110 signals and
     lifts win rate to 72–83%. Keep the EMA9 gate.
   - **WIDE stop (~3.5–4.0× ATR), not tight.** The "smaller SL" hypothesis is *refuted* —
     tighter stops are strictly worse. Edge rises monotonically 1.5→4.0 ATR then peaks
     (interior optimum, not edge-running).
   - **Scale-out with a runner** (≈ 1/3 at TP1=1ATR, 1/3 at TP2=2ATR, 1/3 runs to TP3=3ATR).
   - **BE-after-TP1** — this is the actual loss-cutter (turns would-be losers into scratches),
     not a tight initial stop.
   - **Lighter zero-fee only.** BloFin's 0.06%/side fee makes every config deeply negative.

## SOL retest, Lighter zero-fee — SL curve (ladder 1/2/3 ATR, 34/33/33, BE)

| SL×ATR | n | net | PF | OOS net | OOS PF | WR | maxDD |
|---|---|---|---|---|---|---|---|
| 1.5 | 111 | −137 | 0.89 | −121 | 0.71 | 60% | −364 |
| 2.0 | 112 | +28 | 1.02 | −68 | 0.84 | 67% | −227 |
| 2.5 | 110 | +147 | 1.12 | +106 | 1.32 | 72% | −197 |
| 3.0 | 108 | +172 | 1.14 | +205 | 1.75 | 75% | −204 |
| **3.5** | **107** | **+323** | **1.27** | **+391** | 3.84 | **79%** | −262 |
| **4.0** | **102** | **+453** | **1.43** | **+480** | 10.84 | **83%** | −325 |
| 5.0 | 96 | +266 | 1.24 | +467 | 8.92 | 83% | −382 |

Exit mix @ SL4.0: 36 runners→TP3, 53 BE scratches, 13 full losses. Top-3 wins = 37% of net
(not concentration-driven). OOS PF >4 is inflated by small n (31 OOS trades) — trust the sign
and the smooth peak, not the exact PF.

## Caveats (honest)
- Small sample: ~102 SOL trades (31 OOS). Edge is real-shaped but thin.
- One symbol only. ZEC dead. ETH/HYPE have no live history (forward-collect to test).
- Absolute size is modest: ~+$450 over 7 weeks on $250 margin / $7.5k notional SOL.
- Hard dependency on Lighter zero fees.

## Recommended config (SOL, Lighter paper, forward-validate)
- Entry: Pro V3 buy/sell **+ EMA9 retest gate** (as live bridge already does).
- SL = **3.5×ATR(14)** at entry (robust middle of the 3.0–4.0 plateau).
- TP1/TP2/TP3 = 1/2/3 ATR; scale **34/33/33**; **move SL→breakeven after TP1**.
- Venue: **Lighter only**. Do not run on BloFin (fees kill it).
