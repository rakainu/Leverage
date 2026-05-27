import numpy as np, pandas as pd
from engine import load_symbol
from strat_zscore import ZParams, run

NF = 6
dfs = {s: load_symbol(s,"5m",days_back=180) for s in ["SOL","BTC","ETH"]}
edges = pd.date_range(dfs["BTC"].index.min(), dfs["BTC"].index.max(), periods=NF+1)
def pf(s):
    g=s[s>0].sum(); l=-s[s<=0].sum(); return g/l if l>0 else float('inf')

def evaluate(name, mk):
    allt=[]
    for s,df in dfs.items():
        t = run(df, mk()); allt.append(t)
    c = pd.concat(allt, ignore_index=True)
    if c.empty: print(f"  {name:28} no trades"); return
    ts=pd.to_datetime(c["entry_ts"])
    f1 = c[(ts>=edges[0])&(ts<edges[1])]
    posfolds=sum(1 for k in range(NF) if c[((ts>=edges[k])&(ts< (edges[k+1] if k<NF-1 else edges[k+1]+pd.Timedelta(seconds=1)))).values]["pnl_net"].sum()>0)
    print(f"  {name:28} n={len(c):>4} net=${c['pnl_net'].sum():>7,.0f} PF={pf(c['pnl_net']):>4.2f} "
          f"| fold1 net=${f1['pnl_net'].sum():>6,.0f} PF={pf(f1['pnl_net']):>4.2f} | folds+={posfolds}/{NF}")

B = dict(z_thresh=3.0, sl_atr=1.5, tp_atr=3.0, use_ema=False, use_adx=True, adx_max=40, entry_mode="market")
print("Fixing fold 1 (combined SOL+BTC+ETH). Baseline then vol-floor and directional variants:\n")
evaluate("baseline (bbmin .02)", lambda: ZParams(**B))
evaluate("vol floor bbmin .03",  lambda: ZParams(**{**B,'bb_width_min':0.03}))
evaluate("vol floor bbmin .05",  lambda: ZParams(**{**B,'bb_width_min':0.05}))
evaluate("vol floor bbmin .08",  lambda: ZParams(**{**B,'bb_width_min':0.08}))
evaluate("directional EMA100",   lambda: ZParams(**{**B,'use_ema':True,'ema_len':100}))
evaluate("directional EMA50",    lambda: ZParams(**{**B,'use_ema':True,'ema_len':50}))
