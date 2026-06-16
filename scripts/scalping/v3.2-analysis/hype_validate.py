"""Validate HYPE on Bitget 5m through the same HA-V3 engine (live filters, trail,
%-based stop) — same test the Binance basket got."""
import sys, json, time, urllib.request
import numpy as np, pandas as pd
sys.path.insert(0, 'strategies')
from zec_v3_realistic import generate_v3_signals, apply_entry_filter
from v3_2_lab import run_bt, kpis, ExitModel, F_LIVE, base_params, span_days

def fetch_bitget(symbol="HYPEUSDT", start="2026-02-17", end="2026-06-17"):
    s=int(pd.Timestamp(start,tz="UTC").timestamp()*1000); e=int(pd.Timestamp(end,tz="UTC").timestamp()*1000)
    rows=[]; cur=s; step=1000*5*60*1000
    while cur<e:
        en=min(cur+step,e)
        u=(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={symbol}"
           f"&productType=USDT-FUTURES&granularity=5m&startTime={cur}&endTime={en}&limit=1000")
        try:
            req=urllib.request.Request(u,headers={"User-Agent":"curl/8"})
            with urllib.request.urlopen(req,timeout=30) as r: j=json.load(r)
        except Exception as ex:
            print("fetch err",ex); break
        data=j.get("data") or []
        if not data: cur=en; continue
        rows+=data; cur=int(data[-1][0])+1; time.sleep(0.12)
    if len(rows)<2000: return None
    df=pd.DataFrame(rows)
    df=df.iloc[:,:5]; df.columns=["t","Open","High","Low","Close"]
    df["dt"]=pd.to_datetime(df["t"].astype(np.int64),unit="ms",utc=True)
    df=df.set_index("dt")[["Open","High","Low","Close"]].astype(float)
    return df[~df.index.duplicated()].sort_index()

print("Fetching HYPE 5m from Bitget...")
df=fetch_bitget()
if df is None: print("NO HYPE DATA"); sys.exit(0)
df=apply_entry_filter(generate_v3_signals(df)); days=span_days(df)
print(f"HYPE: {len(df)} bars, {days:.0f}d, {df.index[0]}->{df.index[-1]}")
for fee,tag in ((0.0,"demo/zero-fee"),(0.0006,"blofin fee")):
    p=base_params(sl=82.5, fee=fee, slip=0.0006)
    t=run_bt(df,p,ExitModel("trail"),F_LIVE); k=kpis(t,p,days)
    ln=t[t.side=='long'].pnl_net.sum() if not t.empty else 0
    sn=t[t.side=='short'].pnl_net.sum() if not t.empty else 0
    k3=kpis(t.drop(t.nlargest(3,'pnl_net').index),p,days) if len(t)>3 else k
    print(f"  {tag:14s} n={k['n']} WR={k['WR']} net=${k['net']} PF={k['PF']} avgR={k.get('avg_R')} maxDD=${k['maxDD']} long=${round(ln)} short=${round(sn)} | ex-top3 net=${k3['net']} PF={k3['PF']}")
