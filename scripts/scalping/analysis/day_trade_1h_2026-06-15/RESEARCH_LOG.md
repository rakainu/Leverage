# 1h day-trade test — "regime gate + let winners run" (2026-06-15)

**Question (Rich):** the deployed scalper loses on entries that *look* good on trending
charts. Theory: entries are fine; losses come from (a) no regime filter (taking trades
in chop) and (b) exits that cap winners ~1R while taking full stops. Test on 1h,
day-trade horizon (24h time stop, not swing), SOL/ETH/HYPE/BTC.

**Engine:** btengine (honest fills, no lookahead, stop wins both-hit bars). Data: OKX
USDT-perp 5m → resampled 1h, 180d (~26wk). Risk 1%/trade, no compounding.

## 2×2 result (entries: pullback EMA20-resume & breakout 20-bar)

Every lever moves PF the right way — **theory is directionally correct**:
- Regime gate ON > gate OFF (fewer chop losers).
- RUN exit (scale 50% @1R, BE runner, 3·ATR trail) >> CAPPED ~1:1 exit.
- Best cell: **breakout + gate ON + RUN** → pooled PF 0.89, WR 52%, avgR −0.060,
  OOS PF avg 1.06. (vs the "current style" gateOFF+capped: PF 0.65, avgR −0.23.)

But on **BloFin costs (0.06% taker + 0.05% slip) every config stays net negative.**
The management lifts it from −0.23 avgR garbage to ~breakeven, no further.

## Decisive pass — zero-fee + long/short split (best cell)

Buy&hold over window: SOL −35%, ETH −27%, BTC −14%, HYPE +128%.

| Venue | Pooled PF | WR | avgR | net 26wk | maxDD | long PF | short PF |
|---|---|---|---|---|---|---|---|
| BloFin (0.06%) | 0.89 | 52% | −0.060 | −25.7% | 38% | 0.91 | 0.87 |
| **Lighter (0-fee)** | **1.09** | **53%** | **+0.043** | **+18.3%** | **16.5%** | **1.14** | **1.05** |

Zero-fee flips the **same** strategy positive on **all 4 coins** (SOL +7.5 / ETH +2.6 /
HYPE +3.1 / BTC +5.2%) and on **both sides** (long 1.14, short 1.05). Since SOL/ETH/BTC
were *down* 14–35% over the window yet long-and-short both profit, this is a **real
directional edge, not bull-window beta**. Avg hold 4.6h (max 24h) → genuinely day-trading.

## Conclusion

You CAN get a winning strategy out of these entries — Rich's theory holds — but the
answer is **venue + exit management, not the entry**. On BloFin the ~0.12% round-trip
fee exceeds the per-trade edge; on Lighter (zero fee) it clears at PF ~1.09. Edge is
**real but thin** and lives entirely in the fee saving. Untuned first config — room to
improve entry quality / ATR multiples / trail, but PF 1.09 is the honest baseline.

Next (not yet done, needs Rich's go): tune toward higher PF, then paper on Lighter
alongside regime_mr. Files: run_1h.py (2×2), run_1h_focus.py (zero-fee + side split).

---

# Squeeze re-validation on FRESH data (2026-06-15)

Decision: rather than tune the thin 1h-trend, revive the stronger shelved candidate
`squeeze_expansion` (1h, 4-coin SOL/ETH/ZEC/HYPE, BB-in-KC compression → trade the
release, 1.5·ATR stop, 3·ATR trail, 48h time stop). Pulled FRESH OKX 5m→1h through
**Jun 15** (data_june/) — independent source + a never-seen June-forward slice.

**Replicates cleanly on fresh independent data** (Lighter zero-fee, ~200d):

| Config | n | PF | net | maxDD | OOS PF | June-fwd PF | t |
|---|---|---|---|---|---|---|---|
| BASE 4-coin | 230 | 1.58 | +119% | 20.5% | 1.63 | 2.40 | 2.26 |
| sq12 4-coin (min_sq 12) | 178 | 1.63 | +96% | 16.3% | 2.09 | 2.12 | 2.12 |
| **sq12 3-coin (drop SOL)** | 135 | **1.81** | +96% | **11.2%** | **2.17** | 2.00 | **2.27** |

- Survives BloFin fees (PF 1.41–1.63) → not venue-locked. OOS holds / improves (no decay).
- **June-forward (built-on-May-30 strat never saw it): PF 2.40 base, +13.3% in 2wk** —
  small n=17 but every coin positive.
- Per-coin: ZEC PF 2.67 (star, t=2.40), HYPE 1.65, ETH 1.33, **SOL 0.83 (drag, t=−0.41)**.

**One improvement that earns its place:** min_squeeze 10→12 (tighter compression =
cleaner release) — PF 1.58→1.63, maxDD 20.5→16.3, keeps significance. Dropping SOL
on top → PF 1.81, maxDD 11.2%, same net. (SOL-drop is recency-sensitive; present as
option, not auto-applied.)

**Honest caveats:** low win rate 33% (momentum: many small losses, few big trail
winners — will FEEL wrong); low cadence ~5–8 trades/wk across basket (NOT high-freq
like regime_mr — it's a complementary trend sleeve); avg hold 19h, max 48h (day-trade
to overnight, can tighten time-stop to 24h if stricter intraday wanted). June n small.

Files: fetch_fresh.py, revalidate_squeeze.py (variants), improved_candidate.py.
NOT deployed — awaiting Rich's deploy discussion.
