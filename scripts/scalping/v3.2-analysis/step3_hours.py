"""CAMPAIGN STEP 3 — hour-of-day filter. Which UTC hours does Entry B bleed in,
and does blocking the IN-SAMPLE bad hours help OUT-OF-SAMPLE (or do bad hours
shuffle like coins did)? All 7 coins, fixed-$ ladder, 0.06% slip, 300d.

NOTE: the model uses a flat 0.06% slip for every hour (we have no per-hour slip
data), so this measures EDGE-by-hour only. If blocking dead hours helps here, the
LIVE benefit is likely bigger (dead hours also slip worse). Hour filtering done
post-hoc on entry timestamp.

Run: ../venv/Scripts/python.exe step3_hours.py
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


def main():
    allt = []
    rng = []
    for c in COINS:
        df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                         days_back=DAYS, exchange="blofin", cache=True, verbose=False)
        df = apply_entry_filter(generate_v3_signals(df.copy()))
        G.set_globals(ENT[1], ENT[2], ENT[3])
        t = EV.run_reclaim_gap(df, make_p(**FIXED, slip=SLIP), FILT, gap_cap=GAP)
        if t is not None and not t.empty:
            t = t.copy(); t["entry_ts"] = pd.to_datetime(t["entry_ts"], utc=True)
            allt.append(t); rng.append((df.index[0], df.index[-1]))
    T = pd.concat(allt, ignore_index=True)
    T["hour"] = T["entry_ts"].dt.hour
    t0 = max(r[0] for r in rng); t1 = min(r[1] for r in rng); mid = t0 + (t1-t0)/2
    H = DAYS/2.0

    def k(df, days=DAYS):
        if df is None or len(df) == 0:
            return dict(n=0, net=0, pf=0, wr=0, dd=0, tpw=0)
        return G.kpis(df, days)

    # per-hour table (full window)
    print(f"\n{'='*78}\nPER-HOUR (UTC) — Entry B, all 7 coins, fixed-$ ladder, 0.06% slip\n{'='*78}")
    print(f"{'hr':>3} {'n':>5} {'net$':>8} {'PF':>6} {'WR':>5}   {'bar':<20}")
    for h in range(24):
        g = T[T["hour"] == h]
        kk = k(g)
        bar = ("+" * int(kk['net']/40)) if kk['net'] > 0 else ("-" * int(-kk['net']/40))
        print(f"{h:>3} {kk['n']:>5d} {kk['net']:>8.0f} {kk['pf']:>6.2f} {kk['wr']:>5.1f}   {bar:<20}")

    # IS bad hours -> validate on OOS
    IS = T[(T["entry_ts"] >= t0) & (T["entry_ts"] < mid)]
    OOS = T[(T["entry_ts"] >= mid) & (T["entry_ts"] < t1)]
    is_hour = IS.groupby("hour")["pnl_net"].agg(["sum", "count"])
    bad = sorted([h for h in is_hour.index if is_hour.loc[h, "sum"] < 0 and is_hour.loc[h, "count"] >= 5])
    print(f"\nIN-SAMPLE loss-making hours (block set): {bad}")

    def show(label, df, days):
        kk = k(df, days)
        print(f"{label:28} {kk['n']:>5d} {kk['net']:>8.0f} {kk['pf']:>6.2f} {kk['wr']:>5.1f} "
              f"{kk['dd']:>7.0f} {kk['tpw']:>5.1f}")

    print(f"\n{'='*78}\nBLOCK IS-BAD HOURS -> OOS HOLDOUT TEST\n{'='*78}")
    print(f"{'set':28} {'n':>5} {'net$':>8} {'PF':>6} {'WR':>5} {'maxDD':>7} {'t/wk':>5}")
    show("IS  all hours", IS, H)
    show("IS  blocked", IS[~IS["hour"].isin(bad)], H)
    print("-"*78)
    show("OOS all hours (holdout)", OOS, H)
    show("OOS blocked (holdout)", OOS[~OOS["hour"].isin(bad)], H)
    print("-"*78)
    show("FULL all hours", T, DAYS)
    show("FULL blocked", T[~T["hour"].isin(bad)], DAYS)
    # principled a-priori blocks (structural dead zone, NOT data-mined)
    print(f"\n{'='*78}\nA-PRIORI DEAD-ZONE BLOCKS (structural, not fit) — IS / OOS / FULL\n{'='*78}")
    print(f"{'set':28} {'n':>5} {'net$':>8} {'PF':>6} {'WR':>5} {'maxDD':>7} {'t/wk':>5}")
    for name, blk in [("[3,4,5,6]", [3,4,5,6]), ("[3,4,5,6,7]", [3,4,5,6,7]),
                      ("[3,6] worst-2", [3,6]), ("[2,3,4,5,6,7]", [2,3,4,5,6,7])]:
        show(f"OOS block {name}", OOS[~OOS["hour"].isin(blk)], H)
    print(f"  (ref) OOS all hours          : PF 1.17, net 2445")
    print("-"*78)
    for name, blk in [("[3,4,5,6]", [3,4,5,6]), ("[3,4,5,6,7]", [3,4,5,6,7]), ("[3,6]", [3,6])]:
        show(f"FULL block {name}", T[~T["hour"].isin(blk)], DAYS)
    print(f"  (ref) FULL all hours         : PF 1.09, net 2776")
    print("\nverdict: adopt a dead-zone block ONLY if it lifts OOS PF without cratering net.")


if __name__ == "__main__":
    main()
