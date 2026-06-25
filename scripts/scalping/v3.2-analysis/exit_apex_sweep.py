"""Exit-ladder sweep on the APEX base (NOT the $82 V3.2 ladder), paired with the
new OOS-validated reclaim entry. Find the best exit setup for each entry.

Entry held fixed (both validated robust in entry_final_check.py):
  ENTRY A : M13 reclaim, gap 0.05%, overshoot 0.10%, timeout 9, slope 0.15  (~11 t/wk, PF~1.25)
  ENTRY B : same but slope 0.08                                              (~40 t/wk, PF~1.15)

Exit = Apex 3-stage ladder mapped onto the engine's 5-level TrailParams:
  SL -$30 | BE +$20 | at +$35 lock +$20 AND start trailing $15 behind | TP cap 2x.
Sizing $250 @ 30x. Zero-fee (Lighter), 5m, pooled across the 7 live coins.

Sweeps every rung both directions (no $82): sl x be x trail_distance factorial,
then refines activate/lock/tp on the leaders, then IS/OOS-validates the top set.

Run: ../venv/Scripts/python.exe exit_apex_sweep.py [days]   (default 150)
"""
import os, sys, dataclasses
import entry_grid as G
import entry_v2_search as EV
from engine import fetch_ohlcv
from zec_v3_realistic import generate_v3_signals, apply_entry_filter, TrailParams
from v3_2_lab import base_params

DAYS = G.DAYS
COINS = G.COINS
FILT = G.mk_filter(True, True)            # no-Sunday + block body 0.3-0.5 (slope via globals)
GAP = 0.0005
# (label, overshoot, timeout, slope)
ENTRIES = [("A slope0.15", 0.10, 9, 0.15), ("B slope0.08", 0.10, 9, 0.08)]
APEX = dict(sl=30.0, be=20.0, act=35.0, lock=20.0, dist=15.0, tp=2.0)


def make_p(sl, be, act, lock, dist, tp):
    """Apex-style ladder in the engine's TrailParams (lock+trail both fire at `act`)."""
    p = base_params(sl=sl, fee=0.0, slip=0.0)
    return dataclasses.replace(
        p, margin_usdt=250.0, leverage=30.0,
        sl_loss_usdt=sl, breakeven_usdt=be,
        lock_profit_activate_usdt=act, lock_profit_usdt=lock,
        trail_activate_usdt=act, trail_start_usdt=act, trail_distance_usdt=dist,
        tp_ceiling_pct=tp, commission_pct=0.0, sl_slippage_pct=0.0)


def run(dfs, ent, p, days):
    _, over, to, slope = ent
    G.set_globals(over, to, slope)
    parts = []
    for df in dfs:
        t = EV.run_reclaim_gap(df, p, FILT, gap_cap=GAP)
        if t is not None and not t.empty:
            parts.append(t)
    import pandas as pd
    allt = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    return G.kpis(allt, days)


def load():
    print(f"loading {DAYS}d cached, {len(COINS)} coins...")
    dfs = []
    for c in COINS:
        df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                         days_back=DAYS, exchange="blofin", cache=True, verbose=False)
        dfs.append(apply_entry_filter(generate_v3_signals(df.copy())))
    return dfs


def hdr(t):
    print(f"\n{'='*92}\n{t}\n{'='*92}")
    print(f"{'sl':>4} {'be':>4} {'act':>4} {'lock':>5} {'dist':>5} {'tp':>4} | "
          f"{'n':>5} {'net$':>8} {'PF':>6} {'WR':>5} {'maxDD':>8} {'t/wk':>6}")


def line(cfg, k, mark=""):
    print(f"{cfg['sl']:>4.0f} {cfg['be']:>4.0f} {cfg['act']:>4.0f} {cfg['lock']:>5.0f} "
          f"{cfg['dist']:>5.0f} {cfg['tp']:>4.1f} | {k['n']:>5d} {k['net']:>8.0f} "
          f"{k['pf']:>6.2f} {k['wr']:>5.1f} {k['dd']:>8.0f} {k['tpw']:>6.1f} {mark}")


def main():
    dfs = load()
    dis = [d.iloc[:len(d)//2] for d in dfs]
    doos = [d.iloc[len(d)//2:] for d in dfs]
    H = DAYS / 2.0

    SL = [20, 25, 30, 35, 40, 50]
    BE = [15, 20, 25]
    DIST = [10, 15, 20, 25]

    for ent in ENTRIES:
        elabel = ent[0]
        # reference: exact Apex base
        pbase = make_p(**APEX)
        kbase = run(dfs, ent, pbase, DAYS)
        hdr(f"ENTRY {elabel}  |  APEX-BASE reference  (gap0.05 over0.10 to9)")
        line(APEX, kbase, "<- Apex base")

        # STAGE 1: sl x be x dist factorial (act/lock/tp at Apex base)
        results = []
        for sl in SL:
            for be in BE:
                for dist in DIST:
                    cfg = dict(sl=sl, be=be, act=APEX["act"], lock=APEX["lock"], dist=dist, tp=APEX["tp"])
                    k = run(dfs, ent, make_p(**cfg), DAYS)
                    results.append((cfg, k))
        results.sort(key=lambda r: (r[1]["pf"] >= 1.10, r[1]["net"]), reverse=True)
        hdr(f"ENTRY {elabel}  |  STAGE 1: SL x BE x TRAIL_DIST  (top 12 by net, PF>=1.10)")
        for cfg, k in results[:12]:
            line(cfg, k)

        # STAGE 2: refine activate x lock x tp on the stage-1 leader
        best_cfg = results[0][0]
        hdr(f"ENTRY {elabel}  |  STAGE 2: refine ACTIVATE x LOCK x TP on leader "
            f"(sl{best_cfg['sl']:.0f} be{best_cfg['be']:.0f} dist{best_cfg['dist']:.0f})")
        s2 = []
        for act in [25, 30, 35, 45, 55]:
            for lock in [10, 15, 20, 25]:
                for tp in [1.0, 1.5, 2.0]:
                    cfg = dict(best_cfg, act=act, lock=lock, tp=tp)
                    if lock > act:
                        continue
                    k = run(dfs, ent, make_p(**cfg), DAYS)
                    s2.append((cfg, k))
        s2.sort(key=lambda r: (r[1]["pf"] >= 1.10, r[1]["net"]), reverse=True)
        for cfg, k in s2[:10]:
            line(cfg, k)

        # STAGE 3: IS/OOS on the overall top 6 (stage1 leaders + stage2 leaders)
        pool = {tuple(sorted(c.items())): c for c, _ in results[:5] + s2[:5]}
        hdr(f"ENTRY {elabel}  |  STAGE 3: IS/OOS robustness (top configs)")
        print(f"{'sl':>4} {'be':>4} {'act':>4} {'lock':>5} {'dist':>5} {'tp':>4} | "
              f"{'IS_n':>5} {'IS_net':>7} {'IS_PF':>6} | {'OOS_n':>5} {'OOS_net':>7} {'OOS_PF':>6}  verdict")
        for cfg in pool.values():
            p = make_p(**cfg)
            ki = run(dis, ent, p, H)
            ko = run(doos, ent, p, H)
            ok = ki["net"] > 0 and ko["net"] > 0 and ki["pf"] >= 1.10 and ko["pf"] >= 1.10
            v = "ROBUST" if ok else ("oos-fade" if ki["net"] > 0 >= ko["net"] else "weak")
            print(f"{cfg['sl']:>4.0f} {cfg['be']:>4.0f} {cfg['act']:>4.0f} {cfg['lock']:>5.0f} "
                  f"{cfg['dist']:>5.0f} {cfg['tp']:>4.1f} | {ki['n']:>5d} {ki['net']:>7.0f} "
                  f"{ki['pf']:>6.2f} | {ko['n']:>5d} {ko['net']:>7.0f} {ko['pf']:>6.2f}  {v}")

    print("\nlegend: cols = sl/be/activate/lock/dist/tp ladder ($, 250@30x). zero-fee 5m.")
    print("ROBUST = positive + PF>=1.10 in BOTH IS and OOS halves. NO $82 SL used.")


if __name__ == "__main__":
    main()
