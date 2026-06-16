"""Coin-expansion test: run the V3.2 HA-V3 strategy (live filters + trail,
%-based stop = ZEC's 1.1%) on a top-10 basket over the same recent window.
Rank by profitability so we add only validated winners to V3.2."""
import sys, json, time, urllib.request
import numpy as np, pandas as pd
sys.path.insert(0, 'strategies')
from zec_v3_realistic import generate_v3_signals, apply_entry_filter
from v3_2_lab import run_bt, kpis, ExitModel, F_LIVE, base_params, span_days

COINS = ["BTC","ETH","SOL","XRP","DOGE","SUI","LINK","AVAX","BNB","HYPE","ZEC"]
START, END = "2026-02-17", "2026-06-17"   # ~120 days, shared window

def fetch(sym, start, end):
    s=int(pd.Timestamp(start,tz="UTC").timestamp()*1000); e=int(pd.Timestamp(end,tz="UTC").timestamp()*1000)
    rows=[]; cur=s
    while cur<e:
        u=f"https://api.binance.com/api/v3/klines?symbol={sym}USDT&interval=5m&startTime={cur}&limit=1000"
        try:
            with urllib.request.urlopen(u,timeout=30) as r: data=json.load(r)
        except Exception as ex:
            return None
        if not data: break
        rows+=data; cur=data[-1][0]+1
        if len(data)<1000: break
        time.sleep(0.15)
    if len(rows)<2000: return None
    df=pd.DataFrame(rows,columns=["t","Open","High","Low","Close","v","ct","q","n","tb","tq","ig"])
    df["dt"]=pd.to_datetime(df["t"],unit="ms",utc=True)
    df=df.set_index("dt")[["Open","High","Low","Close"]].astype(float)
    return df[~df.index.duplicated()].sort_index()

rows=[]
for c in COINS:
    t0=time.time()
    df=fetch(c, START, END)
    if df is None:
        print(f"  {c:5s} -- no Binance 5m data (skipped)", flush=True); continue
    df=apply_entry_filter(generate_v3_signals(df)); days=span_days(df)
    for fee,tag in ((0.0006,"fee"),(0.0,"demo")):
        p=base_params(sl=82.5, fee=fee, slip=0.0006)
        tdf=run_bt(df,p,ExitModel("trail"),F_LIVE)
        k=kpis(tdf,p,days)
        ln=tdf[tdf.side=='long'].pnl_net.sum() if not tdf.empty else 0
        sn=tdf[tdf.side=='short'].pnl_net.sum() if not tdf.empty else 0
        k3=kpis(tdf.drop(tdf.nlargest(3,'pnl_net').index),p,days) if len(tdf)>3 else k
        rows.append(dict(coin=c,mode=tag,days=round(days),n=k['n'],WR=k['WR'],net=k['net'],
            PF=k['PF'],avgR=k.get('avg_R'),maxDD=k['maxDD'],netday=k.get('net_per_day'),
            longnet=round(ln,0),shortnet=round(sn,0),net_ex3=k3['net'],PF_ex3=k3['PF']))
    print(f"  {c:5s} done ({len(df)} bars, {round(time.time()-t0)}s)", flush=True)

R=pd.DataFrame(rows)
print("\n================ DEMO (zero-fee) — ranked by net ================")
d=R[R['mode']=='demo'].sort_values('net',ascending=False)
print(d[['coin','days','n','WR','net','PF','avgR','maxDD','netday','longnet','shortnet','net_ex3','PF_ex3']].to_string(index=False))
print("\n================ BLOFIN FEE (0.06%/side) — ranked by net ================")
f=R[R['mode']=='fee'].sort_values('net',ascending=False)
print(f[['coin','days','n','WR','net','PF','avgR','maxDD','netday','longnet','shortnet','net_ex3','PF_ex3']].to_string(index=False))
R.to_csv("data/coin_expansion_results.csv",index=False)
print("\nCSV: data/coin_expansion_results.csv")
