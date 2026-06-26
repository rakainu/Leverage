"""CAMPAIGN STEP 1 — per-coin prune for Entry B + wide ladder.
Which of the 7 Reclaim coins actually carry Entry B? Score each coin standalone
at REALISTIC 0.06% slip, with IS/OOS, then show cumulative pooled subsets in
rank order so we can see where adding coins stops helping.

Baseline (Step 0): Entry B (reclaim gap0.05 over0.10 to9 slope0.08) + wide ladder
SL60/BE25/act25/lock20/dist8/tp2.0, $250@30x, 5m, zero-fee Lighter.

Run: ../venv/Scripts/python.exe step1_percoin.py 150
"""
import pandas as pd
import entry_grid as G
import entry_v2_search as EV
from engine import fetch_ohlcv
from zec_v3_realistic import generate_v3_signals, apply_entry_filter
from exit_apex_wide import make_p   # Apex-style ladder builder w/ slip

DAYS = G.DAYS
COINS = G.COINS
FILT = G.mk_filter(True, True)
GAP = 0.0005
ENT = ("B slope0.08", 0.10, 9, 0.08)
LADDER = dict(sl=60, be=25, act=25, lock=20, dist=8, tp=2.0)
SLIP = 0.0006                     # 0.06% realistic Lighter stop/trail slip


def trades_for(df, slip):
    _, over, to, slope = ENT
    G.set_globals(over, to, slope)
    t = EV.run_reclaim_gap(df, make_p(**LADDER, slip=slip), FILT, gap_cap=GAP)
    return t if (t is not None and not t.empty) else pd.DataFrame()


def main():
    print(f"loading {DAYS}d cached, {len(COINS)} coins...  Entry B, ladder {LADDER}, slip {SLIP*100:.2f}%")
    raw = {}
    for c in COINS:
        df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                         days_back=DAYS, exchange="blofin", cache=True, verbose=False)
        raw[c] = apply_entry_filter(generate_v3_signals(df.copy()))

    H = DAYS / 2.0
    per = {}
    for c, df in raw.items():
        full = trades_for(df, SLIP)
        is_ = trades_for(df.iloc[:len(df)//2], SLIP)
        oos = trades_for(df.iloc[len(df)//2:], SLIP)
        full0 = trades_for(df, 0.0)            # zero-slip ref
        per[c] = dict(full=full, is_=is_, oos=oos, full0=full0,
                      kf=G.kpis(full, DAYS), ki=G.kpis(is_, H), ko=G.kpis(oos, H),
                      k0=G.kpis(full0, DAYS))

    # rank by OOS slipped PF (robustness first), then OOS net
    order = sorted(COINS, key=lambda c: (per[c]["ko"]["pf"], per[c]["ko"]["net"]), reverse=True)

    print(f"\n{'='*96}\nPER-COIN (Entry B, wide ladder, {SLIP*100:.2f}% slip) — ranked by OOS PF\n{'='*96}")
    print(f"{'coin':5} {'n':>5} {'net$':>8} {'PF':>6} {'WR':>5} {'maxDD':>7} {'t/wk':>5} | "
          f"{'noslipPF':>8} | {'IS_PF':>6} {'OOS_PF':>6} {'OOS_net':>8}  verdict")
    for c in order:
        p = per[c]
        ok = p["ki"]["net"] > 0 and p["ko"]["net"] > 0 and p["ki"]["pf"] >= 1.10 and p["ko"]["pf"] >= 1.10
        v = "KEEP" if ok else ("oos-fade" if p["ki"]["net"] > 0 >= p["ko"]["net"] else "drag")
        print(f"{c:5} {p['kf']['n']:>5d} {p['kf']['net']:>8.0f} {p['kf']['pf']:>6.2f} "
              f"{p['kf']['wr']:>5.1f} {p['kf']['dd']:>7.0f} {p['kf']['tpw']:>5.1f} | "
              f"{p['k0']['pf']:>8.2f} | {p['ki']['pf']:>6.2f} {p['ko']['pf']:>6.2f} "
              f"{p['ko']['net']:>8.0f}  {v}")

    print(f"\n{'='*96}\nCUMULATIVE POOLED SUBSETS (add coins in rank order) — {SLIP*100:.2f}% slip\n{'='*96}")
    print(f"{'subset (best->worst added)':45} {'n':>5} {'net$':>8} {'PF':>6} {'WR':>5} "
          f"{'maxDD':>7} {'t/wk':>5}")
    for i in range(1, len(order)+1):
        sub = order[:i]
        full = pd.concat([per[c]["full"] for c in sub], ignore_index=True)
        k = G.kpis(full, DAYS)
        label = "+".join(sub)
        if len(label) > 44:
            label = label[:41] + "..."
        mark = " <- all 7 (Step 0)" if i == len(order) else ""
        print(f"{label:45} {k['n']:>5d} {k['net']:>8.0f} {k['pf']:>6.2f} {k['wr']:>5.1f} "
              f"{k['dd']:>7.0f} {k['tpw']:>5.1f}{mark}")

    # also pooled IS/OOS for each cumulative subset (robustness of the subset)
    print(f"\nIS/OOS of cumulative subsets ({SLIP*100:.2f}% slip):")
    print(f"{'subset':45} {'IS_net':>8} {'IS_PF':>6} | {'OOS_net':>8} {'OOS_PF':>6}  verdict")
    for i in range(1, len(order)+1):
        sub = order[:i]
        ti = pd.concat([per[c]["is_"] for c in sub], ignore_index=True)
        to = pd.concat([per[c]["oos"] for c in sub], ignore_index=True)
        ki, ko = G.kpis(ti, H), G.kpis(to, H)
        ok = ki["net"] > 0 and ko["net"] > 0 and ki["pf"] >= 1.10 and ko["pf"] >= 1.10
        v = "ROBUST" if ok else "weak"
        label = "+".join(sub)
        if len(label) > 44:
            label = label[:41] + "..."
        print(f"{label:45} {ki['net']:>8.0f} {ki['pf']:>6.2f} | {ko['net']:>8.0f} {ko['pf']:>6.2f}  {v}")


if __name__ == "__main__":
    main()
