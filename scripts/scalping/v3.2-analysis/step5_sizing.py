"""CAMPAIGN STEP 5 — sizing / leverage. Running best: Entry B + fixed-$ ladder +
block UTC 3-6, all 7 coins, 0.06% slip. The $ ladder is leverage-coupled: a $60
stop is 0.8% at 30x but 0.48% at 50x, so leverage re-tunes the EFFECTIVE % ladder
AND changes slippage's share of each (smaller) gross move. Sweep leverage with the
fixed $ ladder; also test scaling the ladder WITH leverage (pure size = PF-neutral
control). Report net/PF/maxDD/t-wk + OOS holdout. $250 margin fixed.

Run: ../venv/Scripts/python.exe step5_sizing.py
"""
import dataclasses
import pandas as pd
import entry_grid as G
import entry_v2_search as EV
from engine import fetch_ohlcv
from zec_v3_realistic import generate_v3_signals, apply_entry_filter
from v3_2_lab import base_params

DAYS = 300
COINS = G.COINS
FILT = G.mk_filter(True, True)
GAP = 0.0005
ENT = ("B", 0.10, 9, 0.08)
SLIP = 0.0006
LAD = dict(sl=60, be=25, act=25, lock=20, dist=8, tp=2.0)
BLOCK = [3, 4, 5, 6]


def make_p(lev, scale=1.0):
    s = scale
    return dataclasses.replace(
        base_params(sl=LAD["sl"], fee=0.0, slip=SLIP),
        margin_usdt=250.0, leverage=lev,
        sl_loss_usdt=LAD["sl"]*s, breakeven_usdt=LAD["be"]*s,
        lock_profit_activate_usdt=LAD["act"]*s, lock_profit_usdt=LAD["lock"]*s,
        trail_activate_usdt=LAD["act"]*s, trail_start_usdt=LAD["act"]*s,
        trail_distance_usdt=LAD["dist"]*s, tp_ceiling_pct=LAD["tp"],
        commission_pct=0.0, sl_slippage_pct=SLIP)


def main():
    raw, rng = {}, []
    for c in COINS:
        df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                         days_back=DAYS, exchange="blofin", cache=True, verbose=False)
        raw[c] = apply_entry_filter(generate_v3_signals(df.copy())); rng.append((raw[c].index[0], raw[c].index[-1]))
    t0 = max(r[0] for r in rng); t1 = min(r[1] for r in rng); mid = t0 + (t1-t0)/2
    H = DAYS/2.0

    def pooled(p, lo=None, hi=None, days=DAYS):
        parts = []
        for c in COINS:
            G.set_globals(ENT[1], ENT[2], ENT[3])
            t = EV.run_reclaim_gap(raw[c], p, FILT, gap_cap=GAP)
            if t is None or t.empty:
                continue
            t = t.copy(); t["entry_ts"] = pd.to_datetime(t["entry_ts"], utc=True)
            t = t[~t["entry_ts"].dt.hour.isin(BLOCK)]
            if lo is not None:
                t = t[(t["entry_ts"] >= lo) & (t["entry_ts"] < hi)]
            if not t.empty:
                parts.append(t)
        return G.kpis(pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(), days)

    print(f"\n{'='*84}\nSTEP 5a — LEVERAGE sweep, FIXED $ ladder (effective % stop changes)\n{'='*84}")
    print(f"{'lev':>4} {'notl$':>7} {'$60SL=':>7} | {'n':>5} {'net$':>8} {'PF':>6} {'WR':>5} "
          f"{'maxDD':>7} {'t/wk':>5} | {'OOS_PF':>6}")
    for lev in [10, 15, 20, 30, 40, 50, 75]:
        p = make_p(lev)
        k = pooled(p); ko = pooled(p, mid, t1, H)
        sl_pct = 60.0 / (250*lev) * 100
        star = " *" if lev == 30 else ""
        print(f"{lev:>4} {250*lev:>7.0f} {sl_pct:>6.2f}% | {k['n']:>5d} {k['net']:>8.0f} "
              f"{k['pf']:>6.2f} {k['wr']:>5.1f} {k['dd']:>7.0f} {k['tpw']:>5.1f} | {ko['pf']:>6.2f}{star}")

    print(f"\n{'='*84}\nSTEP 5b — CONTROL: scale $ ladder WITH leverage (pure size, % stop fixed)\n{'='*84}")
    print(f"{'lev':>4} {'scale':>6} | {'n':>5} {'net$':>8} {'PF':>6} {'maxDD':>7}  (PF should be ~flat)")
    for lev in [15, 30, 50]:
        sc = 30.0/lev   # keep $ ladder at the same % as 30x
        k = pooled(make_p(lev, scale=sc))
        print(f"{lev:>4} {sc:>6.2f} | {k['n']:>5d} {k['net']:>8.0f} {k['pf']:>6.2f} {k['dd']:>7.0f}")
    print("\nread 5a: if PF peaks away from 30x, leverage (vs fixed-$ ladder) is a real lever.")
    print("read 5b: PF flat across lev confirms pure size is neutral — only $/DD scale.")


if __name__ == "__main__":
    main()
