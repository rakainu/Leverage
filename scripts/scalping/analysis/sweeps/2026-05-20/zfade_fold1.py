import numpy as np, pandas as pd
from engine import load_symbol
from strat_zscore import ZParams, run
from strat_bbmr import adx

CFG = ZParams(z_thresh=3.0, sl_atr=1.5, tp_atr=3.0, use_ema=False, use_adx=True, adx_max=40, entry_mode="market")
NF = 6
dfs = {s: load_symbol(s,"5m",days_back=180) for s in ["SOL","BTC","ETH"]}
# common fold edges from the union time span
allidx = dfs["BTC"].index
edges = pd.date_range(allidx.min(), allidx.max(), periods=NF+1)

print("Per-fold regime: price RETURN over fold + mean ADX (was fold 1 a market-wide trend?)")
print(f"{'fold':>5} {'window':>22} | " + " | ".join(f"{s+' ret%/ADX':>14}" for s in dfs))
for k in range(NF):
    lo, hi = edges[k], edges[k+1]
    row = f"{k+1:>5} {lo.strftime('%m-%d'):>10}->{hi.strftime('%m-%d'):>10} | "
    parts=[]
    for s,df in dfs.items():
        seg = df[(df.index>=lo)&(df.index<hi)]
        ret = 100*(seg["Close"].iloc[-1]/seg["Close"].iloc[0]-1) if len(seg)>1 else 0
        ax = np.nanmean(adx(df, 14)[(df.index>=lo)&(df.index<hi)])
        parts.append(f"{ret:>+7.1f}% {ax:>4.0f}")
    print(row + " | ".join(parts))

print("\nFold-1 trades by side (did fades get run over on one direction?):")
for s,df in dfs.items():
    t = run(df, CFG)
    ts = pd.to_datetime(t["entry_ts"])
    f1 = t[(ts>=edges[0])&(ts<edges[1])]
    for side in ["long","short"]:
        ss = f1[f1["side"]==side]
        if len(ss): print(f"  {s} {side:>5}: n={len(ss):>2} net=${ss['pnl_net'].sum():>7,.0f} WR={100*(ss['pnl_net']>0).mean():>3.0f}%")
