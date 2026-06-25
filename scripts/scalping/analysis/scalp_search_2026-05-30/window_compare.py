"""Why does the 60d sim (WR ~91% / PF 1.7) beat the live book (WR ~78% / PF 1.08)
over the same dates? Test two hypotheses on the LIVE WINDOW (entries >= START):
  H1 (config timing): the sim applies accel-guard 3.0 + slope-gate 0.08 across the
     whole window, but those guards only went LIVE on 2026-06-20 — most live trades
     ran the OLD bare config. -> sim OLD config should drop toward live.
  H2 (bad slice): the window itself is a rough patch the 60d average dilutes.
Keeper coins, fixed-ish sizing. Reports pooled WR/PF/net/avgW/avgL.

Run: ../../venv/Scripts/python.exe window_compare.py
"""
from __future__ import annotations
import os, sys, time
import numpy as np, pandas as pd, ccxt
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "sol_strategy_2026-05-30"))
from btengine import simulate, metrics, Costs, RiskCfg  # noqa
import strat_lib as SL  # noqa

COINS = ["ETH", "HYPE", "BNB"]
START = pd.Timestamp("2026-06-15", tz="UTC")   # live account restart
GUARDS_LIVE = pd.Timestamp("2026-06-20", tz="UTC")
LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
RISK = RiskCfg(starting_equity=3600.0, risk_frac=0.01, max_leverage=10, liq_buffer=2.5, compounding=True)
BASE = dict(trend_len=200, slope_lb=20, z_period=30, z_entry=1.5, sl_atr=2.0,
            tp_frac=0.3, max_bars=12, limit_atr=0.25, atr_p=14)
NEWG = dict(accel_mult=3.0, min_slope_pct=0.08)   # guards added 06-20
OLDG = dict(accel_mult=0.0, min_slope_pct=0.0)    # bare config (pre-06-20)


def fetch(coin, days=60):
    ex = ccxt.okx({"enableRateLimit": True}); sym = f"{coin}/USDT:USDT"
    end = ex.milliseconds(); since = end - days*86400*1000; rows={}; cur=since; stall=0
    while cur < end and stall < 3:
        try: ch = ex.fetch_ohlcv(sym, "5m", since=cur, limit=300)
        except Exception: stall+=1; time.sleep(1); continue
        if not ch: stall+=1; cur+=300*5*60*1000; continue
        stall=0
        for t,o,h,l,c,v in ch: rows[t]=(o,h,l,c,v)
        cur=ch[-1][0]+5*60*1000; time.sleep(0.25)
    df=pd.DataFrame([(k,*v) for k,v in sorted(rows.items())],columns=["ts","Open","High","Low","Close","Volume"])
    df["ts"]=pd.to_datetime(df["ts"],unit="ms",utc=True)
    return df.set_index("ts").astype(float).resample("15min").agg(
        {"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()


def run(dfs, guards, t_start, t_end=None):
    """Simulate full series (warmup intact), keep trades with entry in [t_start,t_end)."""
    p = dict(BASE, **guards); wsum=lsum=0.0; nw=nl=0; alltr=[]
    for c,df in dfs.items():
        sigs=SL.REGISTRY["regime_mr"](df, side="both", **p)
        for t in simulate(df, sigs, LIGHTER, RISK, 15):
            if t.entry_time < t_start: continue
            if t_end is not None and t.entry_time >= t_end: continue
            alltr.append(t)
            if t.pnl_usd>0: wsum+=t.pnl_usd; nw+=1
            else: lsum+=t.pnl_usd; nl+=1
    n=nw+nl; pf=wsum/-lsum if lsum<0 else float("inf")
    aw=wsum/nw if nw else 0; al=lsum/nl if nl else 0
    return dict(n=n, wr=100*nw/n if n else 0, pf=pf, net=wsum+lsum, aw=aw, al=al,
                be=100*(-al)/((-al)+aw) if (aw-al) else 0)


def line(lbl,m):
    print(f"{lbl:28} n={m['n']:3} WR={m['wr']:5.1f}% PF={m['pf']:5.2f} "
          f"net=${m['net']:+8.0f} avgW=${m['aw']:6.2f} avgL=${m['al']:7.2f} beWR={m['be']:4.1f}%")


def main():
    print(f"fetch {COINS} 60d (warmup), score live window entries >= {START.date()} ...")
    dfs={}
    for c in COINS:
        d=fetch(c); dfs[c]=d; print(f"  {c}: {len(d)} bars -> {d.index[-1].date()}")
    print("\n--- SIM over the live window (06-15 -> now), keepers ---")
    line("sim NEW guards (accel+slope)", run(dfs, NEWG, START))
    line("sim OLD bare (pre-06-20 cfg)", run(dfs, OLDG, START))
    print("\n--- SIM split at guards-live 06-20 (bare before, guards after) ---")
    line("sim bare  06-15->06-20", run(dfs, OLDG, START, GUARDS_LIVE))
    line("sim guards 06-20->now", run(dfs, NEWG, GUARDS_LIVE))
    print("\nLIVE keepers (from DB): all=81.8% PF1.57 | pre-crash<06-22=81.7% PF1.26")
    print("compare: if sim OLD bare WR drops toward ~82%, the gap is mostly the")
    print("sim crediting guards that weren't live yet (+ the 0.05% slip being light).")


if __name__ == "__main__":
    main()
