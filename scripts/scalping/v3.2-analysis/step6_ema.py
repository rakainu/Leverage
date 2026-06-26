"""CAMPAIGN STEP 6 — EMA period for the reclaim reference. The entry fires when
price wicks the EMA and closes back across it; the EMA period sets that line AND
the slope gate. Sweep 5/7/9/12/15/21, recomputing signals each time. Carry
forward running-best: Entry B, gap0.05, fixed-$ ladder, block 3-6, 30x, all 7
coins, 0.06% slip. Report net/PF/maxDD/t-wk + OOS holdout.

Run: ../venv/Scripts/python.exe step6_ema.py
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
PERIODS = [5, 7, 9, 12, 15, 21]


def main():
    raw, rng = {}, []
    for c in COINS:
        df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                         days_back=DAYS, exchange="blofin", cache=True, verbose=False)
        raw[c] = df; rng.append((df.index[0], df.index[-1]))
    t0 = max(r[0] for r in rng); t1 = min(r[1] for r in rng); mid = t0 + (t1-t0)/2
    H = DAYS/2.0
    p = make_p(**LAD, slip=SLIP)

    def run_period(ema_p, lo=None, hi=None, days=DAYS):
        Z.EMA_PERIOD = ema_p
        parts = []
        for c in COINS:
            sig = Z.apply_entry_filter(Z.generate_v3_signals(raw[c].copy()))
            G.set_globals(ENT[1], ENT[2], ENT[3])
            t = EV.run_reclaim_gap(sig, p, FILT, gap_cap=GAP)
            if t is None or t.empty:
                continue
            t = t.copy(); t["entry_ts"] = pd.to_datetime(t["entry_ts"], utc=True)
            t = t[~t["entry_ts"].dt.hour.isin(BLOCK)]
            if lo is not None:
                t = t[(t["entry_ts"] >= lo) & (t["entry_ts"] < hi)]
            if not t.empty:
                parts.append(t)
        return G.kpis(pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(), days)

    print(f"\n{'='*78}\nSTEP 6 — EMA PERIOD sweep (reclaim reference), all 7 coins, 0.06% slip\n{'='*78}")
    print(f"{'ema':>4} | {'n':>5} {'net$':>8} {'PF':>6} {'WR':>5} {'maxDD':>7} {'t/wk':>5} | {'OOS_net':>8} {'OOS_PF':>6}")
    for e in PERIODS:
        k = run_period(e); ko = run_period(e, mid, t1, H)
        star = " * (current)" if e == 9 else ""
        print(f"{e:>4} | {k['n']:>5d} {k['net']:>8.0f} {k['pf']:>6.2f} {k['wr']:>5.1f} "
              f"{k['dd']:>7.0f} {k['tpw']:>5.1f} | {ko['net']:>8.0f} {ko['pf']:>6.2f}{star}")
    Z.EMA_PERIOD = 9
    print("\nverdict: adopt a different EMA only if it beats EMA9 on the OOS holdout (PF+net).")


if __name__ == "__main__":
    main()
