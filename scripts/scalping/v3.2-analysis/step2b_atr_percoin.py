"""STEP 2b — which coins benefit from the ATR fix? All 7 coins.
The flat $60/$8 ladder is tuned to the average coin; coins far from average
volatility get a mis-scaled stop/trail. For EACH coin: compare its fixed-$
result vs its own ATR-tuned ladder, at 0.06% slip, with IS/OOS. Output = the
coins where ATR-scaling actually helps (-> candidates for a per-coin/hybrid fix).

Run: ../venv/Scripts/python.exe step2b_atr_percoin.py
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
SLIP = 0.0006
FIXED = dict(sl=60, be=25, act=25, lock=20, dist=8, tp=2.0)
K_SL = [2.5, 3, 4, 5]
K_DIST = [0.3, 0.5, 0.7]
K_ACT = 2.0


def trades(df, p, lo=None, hi=None):
    G.set_globals(ENT[1], ENT[2], ENT[3])
    t = EV.run_reclaim_gap(df, p, FILT, gap_cap=GAP)
    if t is None or t.empty:
        return pd.DataFrame()
    if lo is not None:
        ts = pd.to_datetime(t["entry_ts"], utc=True)
        t = t[(ts >= lo) & (ts < hi)]
    return t


def main():
    dfs, atrs = {}, {}
    for c in COINS:
        df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                         days_back=DAYS, exchange="blofin", cache=True, verbose=False)
        df = apply_entry_filter(generate_v3_signals(df.copy()))
        dfs[c] = df; atrs[c] = atr_usd(df)
    t0 = max(dfs[c].index[0] for c in COINS)
    t1 = min(dfs[c].index[-1] for c in COINS)
    mid = t0 + (t1 - t0) / 2
    H = DAYS / 2.0

    def kp(df, p, full=True):
        if full:
            return G.kpis(trades(df, p), DAYS)
        ki = G.kpis(trades(df, p, t0, mid), H)
        ko = G.kpis(trades(df, p, mid, t1), H)
        return ki, ko

    print(f"\n{'='*104}\nSTEP 2b — per-coin: FIXED-$ vs best ATR-tuned ladder (0.06% slip, {DAYS}d)\n{'='*104}")
    print(f"{'coin':6} {'ATR$':>6} {'$60=':>5} | {'FIXED net/PF/DD':>22} | "
          f"{'best-ATR (k_sl,k_d) net/PF/DD':>30} | {'ATR OOS':>7} verdict")
    rows = []
    for c in COINS:
        df, a = dfs[c], atrs[c]
        kf = kp(df, make_p(**FIXED, slip=SLIP))
        kfi, kfo = kp(df, make_p(**FIXED, slip=SLIP), full=False)
        # sweep ATR ladder for this coin, pick best net w/ PF>=1.10 AND OOS PF>=1.0
        best = None
        for ksl in K_SL:
            for kd in K_DIST:
                p = make_p(sl=ksl*a, be=K_ACT*a, act=K_ACT*a, lock=K_ACT*a, dist=kd*a, tp=2.0, slip=SLIP)
                k = kp(df, p)
                ki, ko = kp(df, p, full=False)
                if k["pf"] >= 1.10 and ko["pf"] >= 1.0 and k["net"] > 0:
                    if best is None or k["net"] > best["k"]["net"]:
                        best = dict(ksl=ksl, kd=kd, k=k, oos=ko["pf"])
        # decide
        improve = best is not None and best["k"]["net"] > kf["net"] * 1.10 and best["k"]["pf"] >= kf["pf"]
        if best is None:
            bs = "  (none robust)"; verdict = "fixed"
        else:
            bs = f"sl{best['ksl']}x d{best['kd']}x {best['k']['net']:>6.0f}/{best['k']['pf']:.2f}/{best['k']['dd']:.0f}"
            verdict = "ATR-FIX" if improve else "fixed"
        oosstr = f"{best['oos']:.2f}" if best else "  - "
        print(f"{c.split('-')[0]:6} {a:>6.1f} {60/a:>4.1f}x | "
              f"{kf['net']:>7.0f}/{kf['pf']:.2f}/{kf['dd']:>6.0f}    | {bs:>30} | {oosstr:>7} {verdict}")
        rows.append((c, verdict, kf, best))

    fixers = [r[0].split('-')[0] for r in rows if r[1] == "ATR-FIX"]
    print(f"\nCoins where ATR-scaling beats fixed-$ (>10% net, PF>=, OOS robust): "
          f"{fixers if fixers else 'NONE'}")
    print("read: high $60=x ATR multiple => low-vol coin, fixed stop too LOOSE; low multiple")
    print("(e.g. ZEC) => high-vol coin, fixed stop too TIGHT. Those are the ATR-fix candidates.")


if __name__ == "__main__":
    main()
