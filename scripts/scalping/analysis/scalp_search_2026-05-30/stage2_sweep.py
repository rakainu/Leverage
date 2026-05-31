"""STAGE 2 — focused parameter sweep on the families/combos that survived triage.

For each (family, coin, tf) target, sweep a per-family grid x side{long,short,both}
under Lighter zero-fee costs. Emphasis on maker/limit entries (no entry slippage)
and time stops (scalps must not bleed). Rank by expectancy with a frequency &
PF gate, write runs/stage2_<tag>.json.

Usage:
  python stage2_sweep.py            # uses TARGETS derived from stage1 shortlist
  python stage2_sweep.py FAM COIN TF   # sweep one explicit target
"""
from __future__ import annotations
import os, sys, json, time, itertools
import common as K

OUT = os.path.join(os.path.dirname(__file__), "runs")
os.makedirs(OUT, exist_ok=True)


def grid(**ranges):
    keys = list(ranges)
    return [dict(zip(keys, combo)) for combo in itertools.product(*[ranges[k] for k in keys])]


# Per-family parameter grids (scalping-oriented, maker-friendly).
GRIDS = {
    "bb_revert": grid(length=[20, 30], mult=[2.0, 2.5, 3.0], sl_atr=[1.0, 1.5, 2.0],
                      tp_frac=[0.6, 1.0], max_bars=[12, 24], limit_atr=[0.0, 0.25, 0.5]),
    "kc_revert": grid(length=[20, 30], mult=[2.0, 2.5, 3.0], sl_atr=[1.0, 1.5, 2.0],
                      tp_frac=[0.6, 1.0], max_bars=[12, 24], limit_atr=[0.0, 0.25, 0.5]),
    "mr_fade2": grid(z_period=[20, 30], z_entry=[2.0, 2.5, 3.0], sl_atr=[1.5, 2.0, 2.5],
                     tp_frac=[0.7, 1.0], max_bars=[12, 24], limit_atr=[0.0, 0.25, 0.5]),
    "vwap_revert": grid(z_period=[30, 40, 60], z_entry=[1.5, 2.0, 2.5], sl_atr=[1.0, 1.5, 2.0],
                        tp_frac=[0.5, 0.7, 1.0], max_bars=[12, 24], limit_atr=[0.0, 0.25]),
    "range_fade": grid(lookback=[30, 50], edge_frac=[0.08, 0.15], adx_max=[25, 35],
                       sl_atr=[1.0, 1.5], tp_to=["mid", "far"], max_bars=[24, 48],
                       limit_atr=[0.0, 0.25, 0.5]),
    "rsi_snapback": grid(rsi_p=[7, 14], lo=[20, 25, 30], hi=[70, 75, 80], sl_atr=[1.0, 1.5],
                         tp_atr=[1.0, 1.5, 2.0], max_bars=[12, 24], entry=["market", "limit"],
                         limit_atr=[0.25]),
    "stoch_snapback": grid(k_len=[14, 21], smooth=[3], lo=[10, 20], hi=[80, 90],
                           sl_atr=[1.0, 1.5], tp_atr=[1.0, 1.5, 2.0], max_bars=[12, 24],
                           entry=["market", "limit"], limit_atr=[0.25]),
    "sweep_reversal": grid(lookback=[15, 25], sl_atr=[0.5, 1.0], tp_atr=[1.5, 2.0, 3.0],
                           wick_atr=[0.0, 0.25], max_bars=[16, 32], entry=["market", "limit"],
                           limit_atr=[0.25]),
    "wick_fade": grid(wick_frac=[0.5, 0.6, 0.7], min_range_atr=[0.8, 1.2], sl_atr=[0.25, 0.5],
                      tp_atr=[1.0, 1.5, 2.0], max_bars=[8, 16], entry=["market", "limit"],
                      limit_atr=[0.25]),
    "orb_fade": grid(open_bars=[6, 12, 24], sl_atr=[1.0, 1.5], tp_frac=[0.5, 0.7, 1.0],
                     max_bars=[24, 48], limit_atr=[0.0, 0.25]),
    "failed_breakout": grid(lookback=[15, 25], sl_atr=[0.75, 1.0, 1.5], tp_atr=[1.5, 2.0, 3.0],
                            max_bars=[24, 48], entry=["market", "limit"], limit_atr=[0.25]),
    "atr_climax_fade": grid(range_mult=[1.5, 2.0, 2.5], sl_atr=[0.75, 1.0, 1.5],
                            tp_atr=[1.0, 1.5, 2.0], max_bars=[8, 16, 24], entry=["market", "limit"],
                            limit_atr=[0.25]),
    "micro_pullback": grid(impulse_atr=[1.2, 1.5, 2.0], pull_bars=[1, 2, 3], sl_atr=[0.75, 1.0],
                           tp_atr=[1.5, 2.0, 3.0], max_bars=[12, 20], entry=["market"], limit_atr=[0.0]),
    "vwap_reclaim": grid(sl_atr=[1.0, 1.5], tp_atr=[1.5, 2.0, 3.0], buf_atr=[0.0, 0.1, 0.25],
                         max_bars=[16, 32], entry=["market", "limit"], limit_atr=[0.25]),
    "squeeze_expansion": grid(bb_len=[20], sl_atr=[1.0, 1.5, 2.0], tp_atr=[2.0, 3.0, 4.0],
                              min_squeeze=[4, 6, 10], max_bars=[24, 48], trail=[False, True]),
    "reclaim_pullback": grid(fast=[10, 20], slow=[50, 100], sl_atr=[1.0, 1.5, 2.0],
                             tp_atr=[2.0, 3.0], max_bars=[24, 48], trail=[False, True]),
}

SIDES = ["both", "long", "short"]


def sweep_target(fam, coin, tf, min_tpw=5.0):
    df = K.load(coin, tf)
    best = []
    for params in GRIDS[fam]:
        for side in SIDES:
            try:
                m, _ = K.run(fam, df, tf, side, params)
            except Exception:
                continue
            if m["n"] < 30:           # need a real sample
                continue
            rec = dict(family=fam, coin=coin, tf=tf, side=side, params=params,
                       n=m["n"], tpw=round(m["trades_per_wk"], 2),
                       pf=(None if m["profit_factor"] == float("inf") else round(m["profit_factor"], 3)),
                       wr=round(m["win_rate"], 1), avg_r=round(m["avg_r"], 4),
                       net_pct=round(m["net_pct"], 1), dd=round(m["max_dd_pct"], 1),
                       hold_min=round(m["avg_hold_min"], 1), streak=m["worst_streak"])
            best.append(rec)
    # gate: frequency + positive expectancy; rank by avg_r then PF
    keep = [r for r in best if r["tpw"] >= min_tpw and r["avg_r"] > 0]
    keep.sort(key=lambda r: (r["avg_r"], (r["pf"] or 0)), reverse=True)
    return best, keep


def main():
    # Curated targets from Stage 1: MR-led families on the scalping timeframes that
    # were closest to edge, plus deliberate 1m maker-entry probes (to honestly test
    # whether maker fills rescue 1m). Stage 2 grids add limit/maker entries + tuning.
    CURATED = [
        # mr_fade2 — best family, highest-freq near-breakeven cells
        ("mr_fade2", "SOL", "3m"), ("mr_fade2", "HYPE", "5m"), ("mr_fade2", "HYPE", "3m"),
        ("mr_fade2", "ZEC", "3m"), ("mr_fade2", "ZEC", "5m"), ("mr_fade2", "ETH", "5m"),
        ("mr_fade2", "SOL", "5m"), ("mr_fade2", "HYPE", "15m"), ("mr_fade2", "BTC", "15m"),
        # range_fade / sweep / rsi / reclaim
        ("range_fade", "SOL", "5m"), ("range_fade", "HYPE", "15m"), ("range_fade", "ZEC", "15m"),
        ("sweep_reversal", "ZEC", "15m"), ("sweep_reversal", "ZEC", "5m"),
        ("rsi_snapback", "SOL", "15m"), ("rsi_snapback", "ZEC", "15m"),
        ("reclaim_pullback", "ZEC", "15m"), ("reclaim_pullback", "ZEC", "5m"),
        ("vwap_revert", "HYPE", "15m"), ("vwap_revert", "SOL", "15m"),
        ("wick_fade", "ZEC", "3m"), ("wick_fade", "ZEC", "5m"),
        # 1m maker-entry probes
        ("mr_fade2", "SOL", "1m"), ("mr_fade2", "HYPE", "1m"),
    ]
    if len(sys.argv) == 4:
        targets = [(sys.argv[1], sys.argv[2], sys.argv[3])]
        tag = "_".join(sys.argv[1:4])
    else:
        targets = CURATED
        tag = "curated"

    all_rows = []; t0 = time.time()
    print(f"sweeping {len(targets)} targets", flush=True)
    print(K.HDR, flush=True)
    for fam, coin, tf in targets:
        if fam not in GRIDS:
            continue
        _, keep = sweep_target(fam, coin, tf)
        all_rows.extend(keep)
        for r in keep[:3]:
            pfs = "inf" if r["pf"] is None else f"{r['pf']:.2f}"
            print(f"{r['family']:17}{r['coin']:5}{r['tf']:4}{r['side']:5}{r['n']:>6}"
                  f"{r['tpw']:>7.1f}{pfs:>7}{r['wr']:>6.0f}{r['avg_r']:>+8.3f}"
                  f"{r['net_pct']:>+8.1f}{r['dd']:>7.1f}{r['hold_min']:>8.0f}", flush=True)

    all_rows.sort(key=lambda r: (r["avg_r"], (r["pf"] or 0)), reverse=True)
    json.dump(all_rows, open(os.path.join(OUT, f"stage2_{tag}.json"), "w"), indent=2)

    print("\n===== TOP 40 OVERALL (freq>=5, avg_r>0) =====", flush=True)
    print(K.HDR, flush=True)
    for r in all_rows[:40]:
        pfs = "inf" if r["pf"] is None else f"{r['pf']:.2f}"
        print(f"{r['family']:17}{r['coin']:5}{r['tf']:4}{r['side']:5}{r['n']:>6}"
              f"{r['tpw']:>7.1f}{pfs:>7}{r['wr']:>6.0f}{r['avg_r']:>+8.3f}"
              f"{r['net_pct']:>+8.1f}{r['dd']:>7.1f}{r['hold_min']:>8.0f}", flush=True)
    print(f"\n{len(all_rows)} passing configs, {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
