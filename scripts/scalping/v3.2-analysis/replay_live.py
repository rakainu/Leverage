"""Replay the 103 REAL filled live trades through the engine trail machine on
real ZEC bars, from each trade's actual entry price+time. Compare modeled vs
actual P&L per side -> separates SIGNAL quality from EXECUTION quality."""
import sys, json, time, urllib.request, sqlite3
import numpy as np, pandas as pd
sys.path.insert(0, 'strategies')
from zec_v3_realistic import TrailParams, simulate_trade
from dataclasses import replace

def fetch(symbol="ZECUSDT", interval="5m", start="2026-05-15", end="2026-06-17"):
    s=int(pd.Timestamp(start,tz="UTC").timestamp()*1000); e=int(pd.Timestamp(end,tz="UTC").timestamp()*1000)
    rows=[]; cur=s
    while cur<e:
        u=f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&startTime={cur}&limit=1000"
        with urllib.request.urlopen(u,timeout=30) as r: data=json.load(r)
        if not data: break
        rows+=data; cur=data[-1][0]+1
        if len(data)<1000: break
        time.sleep(0.2)
    df=pd.DataFrame(rows,columns=["t","Open","High","Low","Close","v","ct","q","n","tb","tq","ig"])
    df["dt"]=pd.to_datetime(df["t"],unit="ms",utc=True)
    return df.set_index("dt")[["Open","High","Low","Close"]].astype(float).sort_index()

bars_df = fetch()
con=sqlite3.connect('/docker/scalping-v3.1/data/bridge.db')
tr=pd.read_sql_query("SELECT * FROM trade_log WHERE symbol='ZEC-USDT' ORDER BY opened_at", con)
tr['opened_at']=pd.to_datetime(tr['opened_at'])
p=replace(TrailParams(), sl_loss_usdt=82.5, commission_pct=0.0, sl_slippage_pct=0.0006)

rows=[]
for _,t in tr.iterrows():
    et=t['opened_at']; side=t['side']; entry=t['entry_price']
    fut=bars_df[bars_df.index>=et].head(288)
    if len(fut)<2: continue
    bars=[(int(ix.timestamp()),r.Open,r.High,r.Low,r.Close) for ix,r in fut.iterrows()]
    res=simulate_trade(side, entry, bars, p, ordering="avg")
    rows.append(dict(id=t['id'], side=side, actual=t['pnl_usdt'], modeled=res.pnl_usdt,
                     actual_reason=t['exit_reason'], modeled_reason=res.exit_reason))
r=pd.DataFrame(rows)
def agg(d,label):
    print(f"\n## {label} (n={len(d)})")
    print(f"  ACTUAL  net=${d.actual.sum():.1f}  avg=${d.actual.mean():.2f}  WR={ (d.actual>0).mean()*100:.0f}%")
    print(f"  MODELED net=${d.modeled.sum():.1f}  avg=${d.modeled.mean():.2f}  WR={ (d.modeled>0).mean()*100:.0f}%")
    print(f"  delta (modeled-actual) total=${(d.modeled-d.actual).sum():.1f}  avg=${(d.modeled-d.actual).mean():.2f}")
print("="*60); print("REPLAY: real live entries -> engine trail machine on real bars"); print("="*60)
agg(r,"ALL filled ZEC"); agg(r[r.side=='long'],"LONG"); agg(r[r.side=='short'],"SHORT")
print("\n## exit-reason match (actual -> modeled)")
print(r.groupby(['actual_reason','modeled_reason']).size().to_string())
print("\n## biggest modeled-vs-actual divergences")
r['div']=r.modeled-r.actual
print(r.reindex(r.div.abs().sort_values(ascending=False).index).head(10)[['id','side','actual','modeled','actual_reason','modeled_reason']].to_string(index=False))
