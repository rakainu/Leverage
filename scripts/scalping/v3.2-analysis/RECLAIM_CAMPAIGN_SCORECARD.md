# Reclaim "Entry B" Edge Campaign — Scorecard

Sequential, one-change-at-a-time tuning of the Reclaim strategy toward a deployable
config. Started 2026-06-25.

**Strategy:** M13 reclaim entry on 5m, $250 @ 30x, Lighter zero-fee. Engine =
`zec_v3_realistic.simulate_trade` (bridge-parity trail ladder). Data = BloFin 5m.

**Scoring rules**
- Primary metric = **PF & net at 0.06% slippage** (realistic Lighter fills), NOT zero-slip.
- Robustness = holds across an **IS/OOS split** AND across **rolling 90d windows** (keep%).
- One change at a time; carry the winner forward; reject what doesn't clear the bar.
- No conservative bias — numbers decide.

**Fixed entry (Entry B, validated):** reclaim gap 0.05% · overshoot 0.10% · timeout 9 · slope 0.08
(~40 t/wk pooled). Exit base = Apex wide ladder: SL 60 / BE 25 / activate 25 / lock 20 /
trail-dist 8 / TP 2.0× ($250@30x). **No $82 SL.**

---

## Step log

| Step | Change | Result (0.06% slip) | Decision |
|------|--------|---------------------|----------|
| 0 | Baseline: Entry B + wide ladder, **all 7 coins** | 150d: PF 1.17 / +$2,422 / 39 t/wk · 300d: PF ~1.17 | baseline |
| 1 | **Per-coin prune** (+ stability test) | see below | **ADOPT: XRP+ZEC+DOGE** |
| 2 | **ATR-scaled exits** (full + SL-only) | clean IS→OOS holdout: fixed-$ wins OOS (PF 1.17 vs ATR 1.09/1.13); per-coin gains were overfit | **REJECTED — fixed-$ stays** |
| 3 | **Hour-of-day filter** (block 3–6 UTC dead zone) | OOS holdout net 2445→3509 (+43%), PF 1.17→1.31, lower DD, −6 t/wk. Structural (a-priori); data-mined version overfit/failed. Live likely bigger (worst-slip hours). | **ADOPT: block UTC 3,4,5,6** |
| 4 | **Quality screen** (ADX/slope/gap/body) | Every screen lifts PF only by cutting net+frequency — no net-NEGATIVE slice to drop (dead hours already removed; remainder all +edge). slope≥0.11 = PF 1.58/16 t-wk but just the B→A freq dial. | **NO ADOPT — B stays; slope = optional PF/freq dial** |
| 5 | **Sizing / leverage** | OOS PF peaks at 30x (1.31); <30x too-wide % ladder (PF~1.0), >30x more raw $ but lower OOS PF + worse DD + razor stops. Pure bet-size is PF-neutral. | **CONFIRM 30x, no change** |
| 6 | **EMA period** (reclaim reference) | EMA12 = same net ($4551 vs 4615) at HALF the drawdown (−568 vs −1041), OOS PF 1.42 vs 1.31, but 19 t/wk vs 33. Rich chose quality over frequency. | **ADOPT: EMA 12** |
| 7 | **Long/short asymmetry** | Real global long-tilt (long OOS PF 1.60 vs short 1.27, partly crypto's up-drift), but shorts still +EV (OOS 1.27) and a regime hedge; long-only halves freq + lowers net + bull-bet. Surgical coin-side drop = noise (hurt OOS). | **KEEP BOTH (long-tilt is informational)** |
| 8 | **Maker-limit entry** | Backtest can't reward it (sim gives MARKET a free frictionless entry it won't get live). Live-adjusted at 0.06% entry slip: maker ~$2146 vs market ~$883; crossover ~0.04%. Depends on real Lighter entry slip — measure it. | **DEPLOY MARKET; maker = #1 live A/B** |
| 9 | **Assemble + validate + coins** | Final 5-coin set BNB/BTC/DOGE/SOL/ZEC (cut HYPE/XRP: net-neg + low rolling keep% under EMA12). Final config: full PF 1.67 / OOS 1.66 / survives 0.12% slip (1.31) / 100% rolling-window stability / +$4702/300d (~$472/mo fixed, ~$913/mo compound 3x). vs OLD LIVE config which was UNDERWATER (PF 0.85, −$1528). | **DEPLOYED LIVE 2026-06-25 (fixed sizing)** |

### Step 1 detail — per-coin prune
- Single-window (150d) rank suggested BTC/DOGE/XRP/ZEC (PF 1.38). **Stability test (8 rolling
  90d windows) overturned half of it:** only **XRP (75%) and ZEC (75%)** are regime-stable;
  BTC/DOGE/SOL/HYPE/BNB are UNSTABLE (coins rotate by regime — HYPE/SOL hot Aug–Nov, BTC/XRP/DOGE
  hot Dec–Mar). The "PF 1.38" prune lift was regime-luck; over 300d the trio PF = 1.17 = all-7.
- **Pruning's real benefit = concentration on durable coins + lower tail risk, NOT higher PF.**
- Best-3 search (fix stable XRP+ZEC, test each 3rd): **XRP+ZEC+DOGE** = best net (+$2,254/300d) +
  strongest recent quarter (PF 1.32). XRP+ZEC pair alone = most stable (88% keep) but lower freq.
- **ADOPTED: XRP + ZEC + DOGE** (300d: PF 1.17 / +$2,254 / 18 t/wk / recent-90d PF 1.32).
  Caveat: DOGE is a recency/hot-hand 3rd; XRP+ZEC are the durable core.

**Running best config:** Entry B (over0.10/to9/slope0.08/gap0.05) + **EMA 12** + fixed-$ wide
ladder (SL60/BE25/act25/lock20/dist8/tp2) + **block UTC 3,4,5,6** + 30x + coins = all 7
(final set decided at Step 9). Full 300d @0.06% slip: PF 1.37 / net ~4551 / ~19 t/wk. OOS PF 1.42.

---

## Parked
- **BNB/BTC stops** are genuinely too loose ($60 = ~5× ATR); BNB is a net loser under fixed-$.
  A *surgical* one-off tighter stop on just those two is worth re-checking AT STEP 9 — if BNB/BTC
  survive into the final basket. Not a pooled edge (full ATR-scaling rejected).
- Coins reopened to all 7 (Rich) for structural steps; hour/sizing/etc. are coin-agnostic.

## DEPLOYED 2026-06-25 (fixed sizing; switch to compound once proven)
Final config live on reclaim-bridge (/docker/reclaim-bridge, paper $3,600, FRESH DB):
EMA12 · reclaim gap0.05 · overshoot0.10 · timeout9 · slope0.08 · block UTC 3-6 ·
ladder SL60/BE25/act25/lock20/dist8/tp2 · 30x · both sides · market entry ·
cooldown 3-loss/60m · coins {BNB,BTC,DOGE,SOL,ZEC}. Bridge code now in repo
(scripts/reclaim-bridge/) with new block_hours filter + tests. Dashboard baselines
updated (bt_pf 1.50 realistic / bt_wr 78). NEXT: live A/B maker-entry (Step 8);
watch live PF vs ~1.5 target; switch to compound + re-check coins once proven.

## Remaining steps (done)
- **3** — Hour-of-day filter
- **4** — Quality screen on B's marginal trades
- **5** — Sizing / leverage
- **6** — EMA period
- **7** — Long vs short asymmetry per coin
- **8** — Maker-limit entry (structural / bridge change)
- **9** — Re-check coins on tuned strategy + assemble + full IS/OOS + slippage validation → deploy

## Harnesses
`entry_grid.py` · `entry_final_check.py` · `exit_apex_sweep.py` · `exit_apex_wide.py` ·
`step1_percoin.py` · `step1b_stability.py` · `step1c_trio.py`
