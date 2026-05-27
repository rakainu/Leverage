import pandas as pd
from engine import load_symbol
from strat_zscore import ZParams, run
from strategy import kpis
from strat_emavwap import split_oos

print("Z-Fade: market vs LIMIT entry (z=3.0, ATR 1.5/3.0, EMA off, ADX<=40 gate), 180d 5m")
print("Question: does resting a limit at the band fatten the edge AND add fills?\n")
def cfg(mode): return ZParams(z_thresh=3.0, sl_atr=1.5, tp_atr=3.0, use_ema=False,
                              use_adx=True, adx_max=40, entry_mode=mode)
tot = {"market":0, "limit":0}
for sym in ["SOL","BTC","ETH"]:
    df = load_symbol(sym,"5m",days_back=180)
    print(f"=== {sym} ===")
    print(f"  {'mode':>7} {'n':>5} {'/day':>5} {'WR':>6} {'net$':>8} {'PF':>5} {'maxDD$':>8} {'avg$':>7} {'OOS$':>8}")
    for mode in ["market","limit"]:
        t = run(df, cfg(mode))
        if t.empty or len(t) < 10:
            print(f"  {mode:>7} {len(t):>5}  (<10)"); continue
        k=kpis(t); _,oos=split_oos(t); ok=kpis(oos)
        tot[mode] += len(t)
        print(f"  {mode:>7} {k['n']:>5} {len(t)/180:>5.2f} {k['win_rate']*100:>5.1f}% {k['net_pnl']:>8,.0f} "
              f"{k['profit_factor']:>5.2f} {k['max_dd']:>8,.0f} {k['avg_trade']:>7.2f} {ok['net_pnl']:>8,.0f}")
    print()
print(f"COMBINED SOL+BTC+ETH entries/day:  market={tot['market']/180:.2f}   limit={tot['limit']/180:.2f}")
