import numpy as np, pandas as pd
from engine import load_symbol
from strat_zscore import ZParams, run

CFG = ZParams(z_thresh=3.0, sl_atr=1.5, tp_atr=3.0, use_ema=False, use_adx=True, adx_max=40, entry_mode="market")
NFOLDS = 6

def pf(s):
    g = s[s>0].sum(); ls = -s[s<=0].sum()
    return (g/ls) if ls>0 else float('inf')

def fold_report(t, label):
    if t.empty:
        print(f"  {label}: no trades"); return
    ts = pd.to_datetime(t["entry_ts"])
    edges = pd.date_range(ts.min(), ts.max(), periods=NFOLDS+1)
    print(f"  {label}: total n={len(t)}  net=${t['pnl_net'].sum():,.0f}  PF={pf(t['pnl_net']):.2f}")
    pos=0
    for k in range(NFOLDS):
        m = (ts >= edges[k]) & (ts < edges[k+1] if k < NFOLDS-1 else ts <= edges[k+1])
        f = t[m.values]
        if len(f)==0:
            print(f"    fold {k+1}: (empty)"); continue
        net=f['pnl_net'].sum(); p=pf(f['pnl_net'])
        if net>0: pos+=1
        print(f"    fold {k+1}: n={len(f):>3}  net=${net:>7,.0f}  PF={p:>5.2f}  WR={100*(f['pnl_net']>0).mean():>4.0f}%")
    print(f"    => {pos}/{NFOLDS} folds positive\n")

print("Z-Fade WALK-FORWARD stability — fixed config, 6 consecutive ~30d folds, 180d 5m\n")
all_trades = []
for sym in ["SOL","BTC","ETH"]:
    df = load_symbol(sym,"5m",days_back=180)
    t = run(df, CFG)
    t = t.assign(sym=sym)
    all_trades.append(t)
    fold_report(t, sym)
combined = pd.concat(all_trades, ignore_index=True).sort_values("entry_ts").reset_index(drop=True)
fold_report(combined, "COMBINED SOL+BTC+ETH")
