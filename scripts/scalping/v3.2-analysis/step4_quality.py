"""CAMPAIGN STEP 4 — quality screen on Entry B's marginal trades.
Carry forward running-best: Entry B + fixed-$ ladder + block UTC 3-6, all 7 coins.
Question: does a CAUSAL entry feature (trend strength/ADX, reclaim gap, slope mag,
body) flag a droppable losing slice so we keep frequency but shed dead trades?
Show per-feature quintiles, then IS-calibrate a principled threshold and validate
on the OOS holdout (avoid the data-mining trap). 0.06% slip, 300d.

Run: ../venv/Scripts/python.exe step4_quality.py
"""
import numpy as np, pandas as pd
import entry_grid as G
import entry_v2_search as EV
from engine import fetch_ohlcv
from zec_v3_realistic import generate_v3_signals, apply_entry_filter
from exit_apex_wide import make_p

DAYS = 300
COINS = G.COINS
FILT = G.mk_filter(True, True)
GAP = 0.0005
ENT = ("B", 0.10, 9, 0.08)
SLIP = 0.0006
FIXED = dict(sl=60, be=25, act=25, lock=20, dist=8, tp=2.0)
BLOCK_HOURS = [3, 4, 5, 6]
FEATS = ["adx", "slope_abs", "gap", "body"]


def main():
    allt, rng = [], []
    for c in COINS:
        df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                         days_back=DAYS, exchange="blofin", cache=True, verbose=False)
        df = apply_entry_filter(generate_v3_signals(df.copy()))
        G.set_globals(ENT[1], ENT[2], ENT[3])
        t = EV.run_reclaim_gap(df, make_p(**FIXED, slip=SLIP), FILT, gap_cap=GAP)
        if t is None or t.empty:
            continue
        t = t.copy(); t["entry_ts"] = pd.to_datetime(t["entry_ts"], utc=True)
        # merge causal entry features by signal-bar timestamp
        feat = pd.DataFrame(index=df.index)
        feat["adx"] = df["adx"]
        feat["slope_abs"] = df["slope_pct"].abs()
        feat["gap"] = (df["Close"] - df["ema9"]).abs() / df["ema9"] * 100
        feat["body"] = df["body_atr_ratio"]
        for col in FEATS:
            t[col] = feat[col].reindex(t["entry_ts"]).values
        allt.append(t); rng.append((df.index[0], df.index[-1]))
    T = pd.concat(allt, ignore_index=True)
    T["hour"] = T["entry_ts"].dt.hour
    T = T[~T["hour"].isin(BLOCK_HOURS)].dropna(subset=FEATS).reset_index(drop=True)
    t0 = max(r[0] for r in rng); t1 = min(r[1] for r in rng); mid = t0 + (t1-t0)/2
    H = DAYS/2.0

    def kp(df, days=DAYS):
        return G.kpis(df, days) if len(df) else dict(n=0, net=0, pf=0, wr=0, dd=0, tpw=0)

    base = kp(T)
    print(f"running-best (block 3-6, all hours kept else): n={base['n']} net={base['net']:.0f} "
          f"PF={base['pf']:.2f} t/wk={base['tpw']:.1f}")

    # quintile tables per feature (full window)
    for f in FEATS:
        print(f"\n{'='*70}\nFEATURE: {f} — quintiles (Q1=lowest)\n{'='*70}")
        print(f"{'Q':>3} {'range':>16} {'n':>5} {'net$':>8} {'PF':>6} {'WR':>5}")
        T["_q"] = pd.qcut(T[f], 5, labels=False, duplicates="drop")
        for q in sorted(T["_q"].dropna().unique()):
            g = T[T["_q"] == q]; kk = kp(g)
            lo, hi = g[f].min(), g[f].max()
            print(f"{int(q)+1:>3} {f'{lo:.2f}-{hi:.2f}':>16} {kk['n']:>5d} {kk['net']:>8.0f} "
                  f"{kk['pf']:>6.2f} {kk['wr']:>5.1f}")

    # IS-calibrated threshold per feature -> OOS holdout. Direction per feature:
    # higher-is-better -> keep >= thr ; lower-is-better -> keep <= thr.
    DIRN = {"adx": ">=", "slope_abs": ">=", "gap": "<=", "body": "<="}
    IS = T[(T["entry_ts"] >= t0) & (T["entry_ts"] < mid)]
    OOS = T[(T["entry_ts"] >= mid) & (T["entry_ts"] < t1)]
    is_all, oos_all = kp(IS, H), kp(OOS, H)
    print(f"\n{'='*92}\nIS-CALIBRATED SCREEN -> OOS HOLDOUT  (drop IS-worst slice, dir per feature)\n{'='*92}")
    print(f"{'screen':26} {'thr':>7} | {'IS_PF':>6} | {'OOS_n':>5} {'OOS_net':>8} {'OOS_PF':>6} "
          f"{'t/wk':>5}  vs OOS-all")
    print(f"{'(none) baseline':26} {'-':>7} | {is_all['pf']:>6.2f} | {oos_all['n']:>5d} "
          f"{oos_all['net']:>8.0f} {oos_all['pf']:>6.2f} {oos_all['tpw']:>5.1f}  ref")
    for f in FEATS:
        d = DIRN[f]
        best = None
        for q in [0.1, 0.2, 0.3, 0.4, 0.5]:
            thr = np.quantile(IS[f], q if d == ">=" else 1-q)
            keep = IS[IS[f] >= thr] if d == ">=" else IS[IS[f] <= thr]
            kk = kp(keep, H)
            if kk["pf"] > is_all["pf"] and (best is None or kk["pf"] > best[2]):
                best = (thr, q, kk["pf"])
        if best is None:
            print(f"{'keep '+f+' '+d:26} {'-':>7} | {'(no IS improvement)':>20}")
            continue
        thr = best[0]
        ko = kp(OOS[OOS[f] >= thr] if d == ">=" else OOS[OOS[f] <= thr], H)
        flag = "OK" if ko["pf"] > oos_all["pf"] and ko["net"] > 0.7*oos_all["net"] else "x"
        print(f"{'keep '+f+' '+d+' thr':26} {thr:>7.2f} | {best[2]:>6.2f} | {ko['n']:>5d} "
              f"{ko['net']:>8.0f} {ko['pf']:>6.2f} {ko['tpw']:>5.1f}  {flag}")
    print("\nverdict: adopt a screen only if OOS-screened PF beats OOS-all AND net stays")
    print("healthy. If the screen drops net hard for tiny PF gain, it's not worth the trades.")


if __name__ == "__main__":
    main()
