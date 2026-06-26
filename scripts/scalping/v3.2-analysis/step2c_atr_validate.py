"""STEP 2c — clean validation of the ATR fix (no per-coin curve-fitting).
Three options, all 7 coins, pooled:
  A FIXED-$        : flat $60/$8 ladder (current baseline)
  B GLOBAL ATR     : every rung = k * coin_ATR$, ONE global (k_sl,k_dist)
  C SL-ONLY ATR    : only the STOP scaled per coin (SL=k_sl*ATR$), BE/act/trail
                     stay fixed-$  -> Rich's "one-off scaled stop"
Calibrate B & C's k on the IN-SAMPLE half only; report the OUT-OF-SAMPLE half as a
true holdout; then stress full-window at 0.06% AND 0.12% slip. ATR fix is adopted
only if it beats fixed on the OOS holdout AND survives 0.12%.

Run: ../venv/Scripts/python.exe step2c_atr_validate.py
"""
import pandas as pd
import entry_grid as G
import entry_v2_search as EV
from engine import fetch_ohlcv
from zec_v3_realistic import generate_v3_signals, apply_entry_filter
from exit_apex_wide import make_p
from step2_atr import atr_usd

DAYS = 300
COINS = G.COINS
FILT = G.mk_filter(True, True)
GAP = 0.0005
ENT = ("B", 0.10, 9, 0.08)
FIXED = dict(sl=60, be=25, act=25, lock=20, dist=8, tp=2.0)
dfs, atrs = {}, {}


def trades(df, p):
    G.set_globals(ENT[1], ENT[2], ENT[3])
    t = EV.run_reclaim_gap(df, p, FILT, gap_cap=GAP)
    if t is None or t.empty:
        return pd.DataFrame()
    t = t.copy(); t["entry_ts"] = pd.to_datetime(t["entry_ts"], utc=True)
    return t


def pooled(ladder_fn, slip, lo=None, hi=None, days=DAYS):
    parts = []
    for c in COINS:
        p = make_p(**ladder_fn(c), slip=slip)
        t = trades(dfs[c], p)
        if not t.empty and lo is not None:
            t = t[(t["entry_ts"] >= lo) & (t["entry_ts"] < hi)]
        if not t.empty:
            parts.append(t)
    return G.kpis(pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(), days)


def main():
    for c in COINS:
        df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                         days_back=DAYS, exchange="blofin", cache=True, verbose=False)
        dfs[c] = apply_entry_filter(generate_v3_signals(df.copy())); atrs[c] = atr_usd(dfs[c])
    t0 = max(dfs[c].index[0] for c in COINS); t1 = min(dfs[c].index[-1] for c in COINS)
    mid = t0 + (t1 - t0) / 2; H = DAYS / 2.0

    fixed_fn = lambda c: FIXED
    atr_full = lambda ksl, kd: (lambda c: dict(sl=ksl*atrs[c], be=2*atrs[c], act=2*atrs[c],
                                               lock=2*atrs[c], dist=kd*atrs[c], tp=2.0))
    atr_sl = lambda ksl: (lambda c: dict(FIXED, sl=ksl*atrs[c]))

    # ---- calibrate B (full ATR) on IS@0.06 ----
    bestB = None
    for ksl in [3, 4, 5]:
        for kd in [0.4, 0.6, 0.8]:
            k = pooled(atr_full(ksl, kd), 0.0006, t0, mid, H)
            if k["pf"] >= 1.10 and (bestB is None or k["net"] > bestB[1]["net"]):
                bestB = ((ksl, kd), k)
    # ---- calibrate C (SL-only ATR) on IS@0.06 ----
    bestC = None
    for ksl in [2.5, 3.0, 3.5, 4.0]:
        k = pooled(atr_sl(ksl), 0.0006, t0, mid, H)
        if k["pf"] >= 1.10 and (bestC is None or k["net"] > bestC[1]["net"]):
            bestC = (ksl, k)

    print(f"calibrated on IS half: B full-ATR k=({bestB[0][0]}x sl, {bestB[0][1]}x dist) | "
          f"C SL-only k={bestC[0]}x ATR")
    print(f"\n{'='*98}\nSTEP 2c — ATR fix clean validation (pooled 7 coins, IS-calibrated)\n{'='*98}")
    print(f"{'option':22} | {'IS net/PF':>13} | {'OOS net/PF (holdout)':>22} | "
          f"{'full@.06 net/PF':>16} | {'full@.12 net/PF':>16}")

    def report(name, fn):
        ki = pooled(fn, 0.0006, t0, mid, H)
        ko = pooled(fn, 0.0006, mid, t1, H)
        f6 = pooled(fn, 0.0006); f12 = pooled(fn, 0.0012)
        print(f"{name:22} | {ki['net']:>6.0f}/{ki['pf']:>4.2f}   | "
              f"{ko['net']:>7.0f}/{ko['pf']:>4.2f} {('OK' if ko['pf']>=1.1 and ko['net']>0 else 'x'):>4} | "
              f"{f6['net']:>7.0f}/{f6['pf']:>4.2f}  | {f12['net']:>7.0f}/{f12['pf']:>4.2f}")
        return f12

    report("A fixed-$", fixed_fn)
    f12B = report(f"B full-ATR {bestB[0]}", atr_full(*bestB[0]))
    f12C = report(f"C SL-only {bestC[0]}x", atr_sl(bestC[0]))

    print(f"\nthe SL-only (C) per-coin stops at k={bestC[0]}x ATR:")
    for c in COINS:
        print(f"  {c.split('-')[0]:5} ${bestC[0]*atrs[c]:>5.0f}  (was $60; ATR$ {atrs[c]:.0f})")
    print("\nverdict: adopt the ATR fix ONLY if B or C beats A on the OOS holdout AND stays")
    print("positive at 0.12% slip. Else keep fixed-$ (maybe just the 1-2 worst-fit coins).")


if __name__ == "__main__":
    main()
