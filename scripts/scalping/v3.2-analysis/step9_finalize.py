"""CAMPAIGN STEP 9 — assemble + validate the final config, re-check coins on the
fully-tuned (EMA12) strategy, and measure the whole campaign's lift vs the
CURRENT LIVE Reclaim config.

NEW (campaign winner): EMA12 | reclaim gap0.05 | overshoot0.10 | timeout9 | slope0.08
  | ladder SL60/BE25/act25/lock20/dist8/tp2 | block UTC 3-6 | 30x | both sides | market entry.
OLD (live now):        EMA9  | reclaim gap0.05 | overshoot0.20 | timeout6 | slope0.15
  | V3.2 ladder SL82.5/BE30/lockact45/lock37.5/trail75-80/dist37.5/tp2 | all hours | 30x | both.

Run: ../venv/Scripts/python.exe step9_finalize.py
"""
import dataclasses
import pandas as pd
import entry_grid as G
import entry_v2_search as EV
import zec_v3_realistic as Z
from engine import fetch_ohlcv
from v3_2_lab import base_params

DAYS = 300
COINS = G.COINS
FILT = G.mk_filter(True, True)
GAP = 0.0005
SLIP = 0.0006
WIN, STEP = 90, 30
NEW = dict(ema=12, over=0.10, to=9, slope=0.08, block=[3, 4, 5, 6],
           lad=dict(sl=60, be=25, act=25, lock=20, dist=8, tp=2.0))
OLD = dict(ema=9, over=0.20, to=6, slope=0.15, block=[],
           lad=dict(sl=82.5, be=30, act=45, lock=37.5, dist=37.5, tp=2.0, trail_start=80, trail_act=75))

raw = {}


def make_p(lad, slip):
    p = base_params(sl=lad["sl"], fee=0.0, slip=slip)
    return dataclasses.replace(
        p, margin_usdt=250.0, leverage=30.0,
        sl_loss_usdt=lad["sl"], breakeven_usdt=lad["be"],
        lock_profit_activate_usdt=lad["act"], lock_profit_usdt=lad["lock"],
        trail_activate_usdt=lad.get("trail_act", lad["act"]),
        trail_start_usdt=lad.get("trail_start", lad["act"]),
        trail_distance_usdt=lad["dist"], tp_ceiling_pct=lad["tp"],
        commission_pct=0.0, sl_slippage_pct=slip)


def coin_trades(c, cfg, slip):
    Z.EMA_PERIOD = cfg["ema"]
    sig = Z.apply_entry_filter(Z.generate_v3_signals(raw[c].copy()))
    G.set_globals(cfg["over"], cfg["to"], cfg["slope"])
    t = EV.run_reclaim_gap(sig, make_p(cfg["lad"], slip), FILT, gap_cap=GAP)
    if t is None or t.empty:
        return pd.DataFrame()
    t = t.copy(); t["entry_ts"] = pd.to_datetime(t["entry_ts"], utc=True)
    if cfg["block"]:
        t = t[~t["entry_ts"].dt.hour.isin(cfg["block"])]
    return t


def kpis(t, days):
    return G.kpis(t, days) if len(t) else dict(n=0, net=0, pf=0, wr=0, dd=0, tpw=0)


def keeppct(t, t0, t1):
    s, k, v = t0, 0, 0
    while s + pd.Timedelta(days=WIN) <= t1 + pd.Timedelta(days=1):
        sub = t[(t["entry_ts"] >= s) & (t["entry_ts"] < s + pd.Timedelta(days=WIN))]
        if len(sub) >= 8:
            p = sub["pnl_net"].values
            pf = p[p > 0].sum()/-p[p < 0].sum() if (p < 0).any() else 9.9
            v += 1; k += int(p.sum() > 0 and pf >= 1.10)
        s += pd.Timedelta(days=STEP)
    return 100*k/v if v else 0


def main():
    rng = []
    for c in COINS:
        raw[c] = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                             days_back=DAYS, exchange="blofin", cache=True, verbose=False)
        rng.append((raw[c].index[0], raw[c].index[-1]))
    t0 = max(r[0] for r in rng); t1 = min(r[1] for r in rng); mid = t0 + (t1-t0)/2; H = DAYS/2.0

    # ---- A) per-coin re-check on the NEW (EMA12) config + rolling stability ----
    newt = {c: coin_trades(c, NEW, SLIP) for c in COINS}
    print(f"\n{'='*88}\nA) PER-COIN re-check on FINAL config (EMA12), 0.06% slip — + rolling stability\n{'='*88}")
    print(f"{'coin':6} {'n':>4} {'net$':>7} {'PF':>6} {'t/wk':>5} {'keep%':>6}  verdict")
    drags = []
    for c in sorted(COINS, key=lambda x: kpis(newt[x], DAYS)["net"], reverse=True):
        k = kpis(newt[c], DAYS); kep = keeppct(newt[c], t0, t1)
        v = "STABLE-KEEP" if kep >= 60 and k["net"] > 0 else ("DRAG" if k["net"] <= 0 else "rotator")
        if v == "DRAG":
            drags.append(c)
        print(f"{c.split('-')[0]:6} {k['n']:>4d} {k['net']:>7.0f} {k['pf']:>6.2f} {k['tpw']:>5.1f} "
              f"{kep:>5.0f}%  {v}")
    keep = [c for c in COINS if c not in drags]
    print(f"\n  -> cut net-negative DRAG coins: {[c.split('-')[0] for c in drags] or 'none'}  "
          f"| final set: {[c.split('-')[0] for c in keep]}")

    def pool(tr, coins, lo=None, hi=None, days=DAYS):
        parts = [tr[c] for c in coins if len(tr[c])]
        T = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        if lo is not None and len(T):
            T = T[(T["entry_ts"] >= lo) & (T["entry_ts"] < hi)]
        return kpis(T, days)

    # ---- B) final config validation ----
    print(f"\n{'='*88}\nB) FINAL CONFIG VALIDATION — coins={[c.split('-')[0] for c in keep]}\n{'='*88}")
    print(f"{'window':22} {'n':>5} {'net$':>8} {'PF':>6} {'WR':>5} {'maxDD':>7} {'t/wk':>5}")
    for lbl, lo, hi, d in [("full @0.06%", None, None, DAYS), ("IS @0.06%", t0, mid, H),
                           ("OOS @0.06% holdout", mid, t1, H)]:
        k = pool(newt, keep, lo, hi, d)
        print(f"{lbl:22} {k['n']:>5d} {k['net']:>8.0f} {k['pf']:>6.2f} {k['wr']:>5.1f} {k['dd']:>7.0f} {k['tpw']:>5.1f}")
    newt12 = {c: coin_trades(c, NEW, 0.0012) for c in keep}
    k12 = pool(newt12, keep)
    print(f"{'full @0.12% stress':22} {k12['n']:>5d} {k12['net']:>8.0f} {k12['pf']:>6.2f} "
          f"{k12['wr']:>5.1f} {k12['dd']:>7.0f} {k12['tpw']:>5.1f}")
    print(f"  rolling 90d keep% (final set): {keeppct(pd.concat([newt[c] for c in keep], ignore_index=True), t0, t1):.0f}%")

    # ---- C) campaign lift: OLD live config vs NEW, all 7 coins, 0.06% ----
    print(f"\n{'='*88}\nC) CAMPAIGN LIFT — current LIVE config vs NEW (all 7 coins, 0.06% slip)\n{'='*88}")
    oldt = {c: coin_trades(c, OLD, SLIP) for c in COINS}
    print(f"{'config':22} {'n':>5} {'net$':>8} {'PF':>6} {'maxDD':>7} {'t/wk':>5} | {'OOS_PF':>6}")
    for lbl, tr, coins in [("OLD live (EMA9/$82)", oldt, COINS),
                           ("NEW all-7", newt, COINS),
                           ("NEW final set", newt, keep)]:
        k = pool(tr, coins); ko = pool(tr, coins, mid, t1, H)
        print(f"{lbl:22} {k['n']:>5d} {k['net']:>8.0f} {k['pf']:>6.2f} {k['dd']:>7.0f} {k['tpw']:>5.1f} | {ko['pf']:>6.2f}")
    Z.EMA_PERIOD = 9


if __name__ == "__main__":
    main()
