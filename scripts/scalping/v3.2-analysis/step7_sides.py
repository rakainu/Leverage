"""CAMPAIGN STEP 7 — long vs short asymmetry. Does one side carry the edge, and
does any coin have a DEAD side worth dropping? Carry forward: EMA12, Entry B,
fixed ladder, block 3-6, 30x, all 7 coins, 0.06% slip.
Tests: (1) pooled both/long/short (global asymmetry, robust); (2) per-coin-side
table; (3) IS->OOS — drop coin-sides that lose IN-SAMPLE, validate on holdout
(guard against the per-coin-side overfit, same trap as coins/hours).

Run: ../venv/Scripts/python.exe step7_sides.py
"""
import pandas as pd
import entry_grid as G
import entry_v2_search as EV
import zec_v3_realistic as Z
from engine import fetch_ohlcv
from exit_apex_wide import make_p

DAYS = 300
COINS = G.COINS
FILT = G.mk_filter(True, True)
GAP = 0.0005
ENT = ("B", 0.10, 9, 0.08)
SLIP = 0.0006
LAD = dict(sl=60, be=25, act=25, lock=20, dist=8, tp=2.0)
BLOCK = [3, 4, 5, 6]
EMA = 12


def main():
    Z.EMA_PERIOD = EMA
    p = make_p(**LAD, slip=SLIP)
    allt, rng = [], []
    for c in COINS:
        df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                         days_back=DAYS, exchange="blofin", cache=True, verbose=False)
        sig = Z.apply_entry_filter(Z.generate_v3_signals(df.copy()))
        G.set_globals(ENT[1], ENT[2], ENT[3])
        t = EV.run_reclaim_gap(sig, p, FILT, gap_cap=GAP)
        if t is None or t.empty:
            continue
        t = t.copy(); t["coin"] = c.split("-")[0]
        t["entry_ts"] = pd.to_datetime(t["entry_ts"], utc=True)
        t = t[~t["entry_ts"].dt.hour.isin(BLOCK)]
        allt.append(t); rng.append((sig.index[0], sig.index[-1]))
    T = pd.concat(allt, ignore_index=True)
    t0 = max(r[0] for r in rng); t1 = min(r[1] for r in rng); mid = t0 + (t1-t0)/2
    H = DAYS/2.0

    def kp(d, days=DAYS):
        return G.kpis(d, days) if len(d) else dict(n=0, net=0, pf=0, wr=0, dd=0, tpw=0)
    OOS = lambda d: d[(d["entry_ts"] >= mid) & (d["entry_ts"] < t1)]
    IS = lambda d: d[(d["entry_ts"] >= t0) & (d["entry_ts"] < mid)]

    print(f"\n{'='*70}\nSTEP 7 — POOLED side asymmetry (EMA12, all 7 coins)\n{'='*70}")
    print(f"{'side':>6} {'n':>5} {'net$':>8} {'PF':>6} {'WR':>5} {'t/wk':>5} | {'OOS_net':>8} {'OOS_PF':>6}")
    for s in ["both", "long", "short"]:
        d = T if s == "both" else T[T["side"] == s]
        k = kp(d); ko = kp(OOS(d), H)
        print(f"{s:>6} {k['n']:>5d} {k['net']:>8.0f} {k['pf']:>6.2f} {k['wr']:>5.1f} {k['tpw']:>5.1f} "
              f"| {ko['net']:>8.0f} {ko['pf']:>6.2f}")

    print(f"\n{'='*70}\nPER-COIN x SIDE (full window) — net / PF\n{'='*70}")
    print(f"{'coin':6} | {'LONG net/PF':>16} | {'SHORT net/PF':>16}")
    for c in sorted(T["coin"].unique()):
        lo = kp(T[(T.coin == c) & (T.side == "long")])
        sh = kp(T[(T.coin == c) & (T.side == "short")])
        print(f"{c:6} | {lo['net']:>9.0f}/{lo['pf']:.2f} {lo['n']:>3d}| "
              f"{sh['net']:>9.0f}/{sh['pf']:.2f} {sh['n']:>3d}")

    # IS-bad coin-sides -> drop on OOS holdout
    isd = IS(T).groupby(["coin", "side"])["pnl_net"].agg(["sum", "count"])
    bad = [(c, s) for (c, s) in isd.index if isd.loc[(c, s), "sum"] < 0 and isd.loc[(c, s), "count"] >= 5]
    print(f"\nIN-SAMPLE losing coin-sides (drop set): {bad}")
    keep_mask = ~T.set_index(["coin", "side"]).index.isin(bad)
    base = kp(OOS(T), H)
    dropped = kp(OOS(T[keep_mask]), H)
    print(f"\n{'OOS both-sides all':28} net={base['net']:>7.0f} PF={base['pf']:.2f} t/wk={base['tpw']:.1f}")
    print(f"{'OOS drop IS-bad coin-sides':28} net={dropped['net']:>7.0f} PF={dropped['pf']:.2f} t/wk={dropped['tpw']:.1f}")
    Z.EMA_PERIOD = 9
    print("\nverdict: adopt a side rule only if it's a GLOBAL asymmetry or survives OOS.")
    print("If dropping IS-bad coin-sides hurts OOS, it's noise (per-coin-side overfit).")


if __name__ == "__main__":
    main()
