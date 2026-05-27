import pandas as pd
from engine import load_symbol
from strat_zscore import ZParams, run
from strategy import kpis
from strat_emavwap import split_oos

print("Z-score MR symbol robustness — EMA off, ATR 1.5/3.0, Lighter 0-fee, 180d 5m")
print(f"{'sym':>4} {'z':>5} {'n':>5} {'WR':>6} {'net$':>8} {'PF':>5} {'maxDD$':>8} {'avg$':>7} {'OOSnet$':>9}")
for sym in ["SOL","BTC","ETH"]:
    try:
        df = load_symbol(sym,"5m",days_back=180)
    except Exception as e:
        print(f"{sym}: load failed {e}"); continue
    for z in [2.5, 3.0, 3.5]:
        t = run(df, ZParams(z_thresh=z, sl_atr=1.5, tp_atr=3.0, use_ema=False))
        if t.empty: 
            print(f"{sym:>4} {z:>5.1f}   (no trades)"); continue
        k=kpis(t); _,oos=split_oos(t); ok=kpis(oos)
        print(f"{sym:>4} {z:>5.1f} {k['n']:>5} {k['win_rate']*100:>5.1f}% {k['net_pnl']:>8,.0f} "
              f"{k['profit_factor']:>5.2f} {k['max_dd']:>8,.0f} {k['avg_trade']:>7.2f} {ok['net_pnl']:>9,.0f}")
    print()
