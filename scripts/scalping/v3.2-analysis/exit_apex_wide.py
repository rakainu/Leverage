"""WIDER exit-ladder sweep — the prior sweep pinned SL at 50 (top of range) and
trail-activate at 25 (bottom), so the true optimum is outside that box. Extend
SL up to 70 (still < the rejected 82) and activate down to 15.

PLUS a SLIPPAGE STRESS pass: Rich's standing observation is that wide-stop /
tight-trail configs look great in backtest but "are too much" live. So every
finalist is re-scored at realistic Lighter stop/trail slippage (0.06%, 0.12%)
alongside zero-slip — to see whether the edge is real or a frictionless mirage.

Entry held fixed (validated): A = reclaim gap0.05 over0.10 to9 slope0.15 (~11/wk);
B = same, slope0.08 (~40/wk). Exit = Apex-style ladder, $250@30x, 5m, 7 coins.

Run: ../venv/Scripts/python.exe exit_apex_wide.py 150
"""
import dataclasses
import pandas as pd
import entry_grid as G
import entry_v2_search as EV
from engine import fetch_ohlcv
from zec_v3_realistic import generate_v3_signals, apply_entry_filter
from v3_2_lab import base_params

DAYS = G.DAYS
COINS = G.COINS
FILT = G.mk_filter(True, True)
GAP = 0.0005
ENTRIES = [("A slope0.15", 0.10, 9, 0.15), ("B slope0.08", 0.10, 9, 0.08)]


def make_p(sl, be, act, lock, dist, tp, slip=0.0):
    p = base_params(sl=sl, fee=0.0, slip=slip)
    return dataclasses.replace(
        p, margin_usdt=250.0, leverage=30.0,
        sl_loss_usdt=sl, breakeven_usdt=be,
        lock_profit_activate_usdt=act, lock_profit_usdt=lock,
        trail_activate_usdt=act, trail_start_usdt=act, trail_distance_usdt=dist,
        tp_ceiling_pct=tp, commission_pct=0.0, sl_slippage_pct=slip)


def run(dfs, ent, p, days):
    _, over, to, slope = ent
    G.set_globals(over, to, slope)
    parts = []
    for df in dfs:
        t = EV.run_reclaim_gap(df, p, FILT, gap_cap=GAP)
        if t is not None and not t.empty:
            parts.append(t)
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
    print(f"\n{'='*94}\n{t}\n{'='*94}")
    print(f"{'sl':>4} {'be':>4} {'act':>4} {'dist':>5} | {'n':>5} {'net$':>8} {'PF':>6} "
          f"{'WR':>5} {'maxDD':>8} {'t/wk':>6}")


def line(c, k, mark=""):
    print(f"{c['sl']:>4.0f} {c['be']:>4.0f} {c['act']:>4.0f} {c['dist']:>5.0f} | "
          f"{k['n']:>5d} {k['net']:>8.0f} {k['pf']:>6.2f} {k['wr']:>5.1f} {k['dd']:>8.0f} "
          f"{k['tpw']:>6.1f} {mark}")


def main():
    dfs = load()
    dis = [d.iloc[:len(d)//2] for d in dfs]
    doos = [d.iloc[len(d)//2:] for d in dfs]
    H = DAYS / 2.0

    SL = [40, 50, 55, 60, 65, 70]          # extended up toward (not to) 82
    ACT = [15, 20, 25, 30, 35]             # extended down past 25
    DIST = [8, 10, 12, 15]
    BE, LOCK, TP = 25, 20, 2.0

    for ent in ENTRIES:
        el = ent[0]
        # STAGE 1: wide factorial (zero-slip — the "backtest-best" Rich predicts)
        res = []
        for sl in SL:
            for act in ACT:
                for dist in DIST:
                    c = dict(sl=sl, be=BE, act=act, lock=LOCK, dist=dist, tp=TP)
                    res.append((c, run(dfs, ent, make_p(**c), DAYS)))
        res.sort(key=lambda r: (r[1]["pf"] >= 1.10, r[1]["net"]), reverse=True)
        hdr(f"ENTRY {el}  |  WIDE STAGE 1: SL x ACTIVATE x DIST  (top 12, zero-slip)")
        for c, k in res[:12]:
            edge = " <-SL@max" if c["sl"] == SL[-1] else (" <-act@min" if c["act"] == ACT[0] else "")
            line(c, k, edge)

        # STAGE 2: IS/OOS on top 8 (zero-slip)
        hdr(f"ENTRY {el}  |  WIDE STAGE 2: IS/OOS (top 8, zero-slip)")
        print(f"{'sl':>4} {'be':>4} {'act':>4} {'dist':>5} | {'IS_net':>7} {'IS_PF':>6} | "
              f"{'OOS_net':>7} {'OOS_PF':>6}  verdict")
        for c, _ in res[:8]:
            p = make_p(**c)
            ki, ko = run(dis, ent, p, H), run(doos, ent, p, H)
            ok = ki["net"] > 0 and ko["net"] > 0 and ki["pf"] >= 1.10 and ko["pf"] >= 1.10
            v = "ROBUST" if ok else ("oos-fade" if ki["net"] > 0 >= ko["net"] else "weak")
            print(f"{c['sl']:>4.0f} {c['be']:>4.0f} {c['act']:>4.0f} {c['dist']:>5.0f} | "
                  f"{ki['net']:>7.0f} {ki['pf']:>6.2f} | {ko['net']:>7.0f} {ko['pf']:>6.2f}  {v}")

        # STAGE 3: SLIPPAGE STRESS — does the wide-stop edge survive real fills?
        # compare the wide winner vs a mid (SL40) vs a tight (SL30) at 0/0.06/0.12% slip
        stress = [res[0][0],
                  dict(sl=40, be=BE, act=res[0][0]["act"], lock=LOCK, dist=res[0][0]["dist"], tp=TP),
                  dict(sl=30, be=BE, act=res[0][0]["act"], lock=LOCK, dist=res[0][0]["dist"], tp=TP)]
        hdr(f"ENTRY {el}  |  WIDE STAGE 3: SLIPPAGE STRESS (net$ / PF at each slip)")
        print(f"{'config':>16} | {'slip0.00%':>16} | {'slip0.06%':>16} | {'slip0.12%':>16} | haircut@.06")
        for c in stress:
            cells = []
            base_net = None
            for slip in [0.0, 0.0006, 0.0012]:
                k = run(dfs, ent, make_p(**c, slip=slip), DAYS)
                if slip == 0.0:
                    base_net = k["net"]
                cells.append(f"{k['net']:>7.0f}/{k['pf']:>4.2f}")
            k06 = run(dfs, ent, make_p(**c, slip=0.0006), DAYS)
            hc = (k06["net"] - base_net) if base_net is not None else 0
            tag = f"sl{c['sl']:.0f} act{c['act']:.0f} d{c['dist']:.0f}"
            print(f"{tag:>16} | {cells[0]:>16} | {cells[1]:>16} | {cells[2]:>16} | {hc:>+7.0f}")

    print("\nlegend: ladder $ at 250@30x, 5m, zero-FEE Lighter. slip = stop/trail fill")
    print("slippage. ROBUST = +net & PF>=1.10 both halves. SL capped at 70 (< rejected 82).")
    print("read STAGE 3: if the wide-SL row's edge collapses under slip but a tighter SL")
    print("holds, the backtest 'best' really was 'too much' for live — pick the survivor.")


if __name__ == "__main__":
    main()
