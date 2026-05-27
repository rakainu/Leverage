import pandas as pd
from engine import load_symbol
from strat_zscore import ZParams, run
from strategy import kpis
from strat_emavwap import split_oos

print("Z-boundary check: is z=3.0 an interior peak or a grid-edge artifact? (SOL, EMA off)")
df = load_symbol("SOL","5m",days_back=180)
print(f"{'z':>5} {'sl':>4} {'tp':>4} {'n':>5} {'WR':>6} {'net$':>8} {'PF':>5} {'maxDD$':>8} {'avg$':>7} {'OOSnet$':>9}")
for z in [2.5, 3.0, 3.25, 3.5, 3.75, 4.0]:
    for sl, tp in [(1.5,3.0),(2.0,3.0)]:
        t = run(df, ZParams(z_thresh=z, sl_atr=sl, tp_atr=tp, use_ema=False))
        if t.empty: 
            print(f"{z:>5.2f} {sl:>4.1f} {tp:>4.1f}   (no trades)"); continue
        k=kpis(t); _,oos=split_oos(t); ok=kpis(oos)
        print(f"{z:>5.2f} {sl:>4.1f} {tp:>4.1f} {k['n']:>5} {k['win_rate']*100:>5.1f}% {k['net_pnl']:>8,.0f} "
              f"{k['profit_factor']:>5.2f} {k['max_dd']:>8,.0f} {k['avg_trade']:>7.2f} {ok['net_pnl']:>9,.0f}")
