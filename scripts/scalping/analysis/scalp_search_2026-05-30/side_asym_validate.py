"""Validate the aggressive-short candidate vs LIVE baseline. 2026-06-26

Candidate (from side_asym_sweep test E): longs UNCHANGED + shorts z_entry 1.5->1.25
and min_slope_pct 0.08->0.05 (take more shorts). On 120d keepers it lifted net
+46->+59%, held PF 1.74, lowered DD, 0 liq. This script gates it BEFORE any deploy:

  1) IS/OOS 70/30        — does the lift survive out-of-sample?
  2) Regime segments     — split the window into K contiguous slices, tag each by
                           basket price drift (UP/FLAT/DOWN), and check the looser
                           shorts DON'T blow up in UP-drift windows (the mirror of
                           the recent long bleed). This is the real risk.
  3) Double-slip (0.10%) — every number also reported at stress slippage.

Scoring (Rich): candidate must beat baseline on NET out-of-sample AND not turn
net-negative on shorts in any UP segment. PF-held, DD-not-worse, 0 liq required.

Run: ../../venv/Scripts/python.exe side_asym_validate.py [days] [coins]
"""
from __future__ import annotations
import os, sys
import numpy as np

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "sol_strategy_2026-05-30"))
from side_asym_sweep import (fetch_15m, run_combo, LIVE, LIVE_SL, LIVE_SLOPE,  # noqa: E402
                             LIGHTER, HISLIP)

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 180
COINS = sys.argv[2].split(",") if len(sys.argv) > 2 and sys.argv[2] else ["ETH", "HYPE", "BNB"]
K_SEG = 6   # regime segments

L = dict(LIVE, sl_atr=LIVE_SL, min_slope_pct=LIVE_SLOPE)            # longs: unchanged
S_BASE = dict(LIVE, sl_atr=LIVE_SL, min_slope_pct=LIVE_SLOPE)       # shorts: live
S_CAND = dict(LIVE, sl_atr=LIVE_SL, min_slope_pct=0.05, z_entry=1.25)  # shorts: looser


def drift_pct(dfs):
    return float(np.mean([(df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100
                          for df in dfs.values() if len(df) > 1]))


def tag(d):
    return "UP  " if d > 5 else ("DOWN" if d < -5 else "FLAT")


def slice_dfs(dfs, lo_frac, hi_frac):
    out = {}
    for c, df in dfs.items():
        lo, hi = int(len(df) * lo_frac), int(len(df) * hi_frac)
        out[c] = df.iloc[lo:hi]
    return out


def both(dfs, lp, sp):
    m = run_combo(dfs, lp, sp, LIGHTER)
    m["net_hi"] = run_combo(dfs, lp, sp, HISLIP)["net"]
    return m


def line(label, m):
    print(f"{label:>22} {m['n']:>4} {m['pf']:>5.2f} {m['wr']:>5.1f} "
          f"{m['net']:>+6.1f} {m['net_hi']:>+7.1f} {m['dd']:>5.1f} "
          f"{m['short_net']:>+7.0f} {m['short_n']:>4} {m['worst10']:>7.0f} {m['liq']:>3}")


def hdr(title):
    print(f"\n{'='*92}\n{title}\n{'='*92}")
    print(f"{'window/config':>22} {'n':>4} {'PF':>5} {'WR%':>5} {'net%':>6} "
          f"{'net%hi':>7} {'DD%':>5} {'Snet$':>7} {'Sn':>4} {'wrst10':>7} {'liq':>3}")


def main():
    print(f"fetching OKX 5m->15m, {DAYS}d, coins={COINS} ...")
    dfs = {}
    for c in COINS:
        d = fetch_15m(c, DAYS)
        if d is None or len(d) < 600:
            print(f"  {c}: unavailable/short on OKX (skipped)"); continue
        dfs[c] = d
        print(f"  {c}: {len(d)} 15m bars {d.index[0].date()}->{d.index[-1].date()}")
    if not dfs:
        print("no data"); return
    print(f"full-window basket drift: {drift_pct(dfs):+.1f}%  ({tag(drift_pct(dfs)).strip()})")

    # ---- 1) IS / OOS 70-30 ----
    is_dfs = slice_dfs(dfs, 0.0, 0.70)
    oos_dfs = slice_dfs(dfs, 0.70, 1.0)
    hdr(f"1) IS/OOS 70-30   (IS drift {drift_pct(is_dfs):+.0f}% / OOS drift {drift_pct(oos_dfs):+.0f}%)")
    line("IS  baseline", both(is_dfs, dict(L), dict(S_BASE)))
    line("IS  candidate", both(is_dfs, dict(L), dict(S_CAND)))
    line("OOS baseline", both(oos_dfs, dict(L), dict(S_BASE)))
    line("OOS candidate", both(oos_dfs, dict(L), dict(S_CAND)))

    # ---- 2) regime segments (the up-window stress) ----
    hdr(f"2) REGIME SEGMENTS ({K_SEG} slices) — does looser-short survive UP drift?")
    for k in range(K_SEG):
        lo, hi = k / K_SEG, (k + 1) / K_SEG
        seg = slice_dfs(dfs, lo, hi)
        d = drift_pct(seg)
        d0 = list(seg.values())[0].index[0].date()
        d1 = list(seg.values())[0].index[-1].date()
        print(f"\n-- seg {k+1}/{K_SEG}  {d0}->{d1}  drift {d:+.0f}% [{tag(d).strip()}] --")
        line("  baseline", both(seg, dict(L), dict(S_BASE)))
        line("  candidate", both(seg, dict(L), dict(S_CAND)))

    print("\nGATE: candidate must beat baseline on OOS net (0.05 AND 0.10 slip),")
    print("hold PF, not worsen DD, 0 liq — AND short Snet$ must stay >=0 in every UP")
    print("segment. A negative Snet in an UP window = looser shorts fading a bull = veto.")


if __name__ == "__main__":
    main()
