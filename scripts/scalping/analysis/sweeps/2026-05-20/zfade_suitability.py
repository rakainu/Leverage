import numpy as np, pandas as pd
from engine import load_symbol
from strat_zscore import ZParams, run
from strategy import kpis
from strat_emavwap import split_oos

# Z-Fade winning config: z=3.0, ATR 1.5/3.0, no EMA filter. Add ADX regime gate.
print("Z-Fade per-symbol suitability — ADX regime gate (z=3.0, ATR 1.5/3.0, EMA off)")
print("Does gating to ranging regimes rescue ETH without starving SOL/BTC?\n")
for sym in ["SOL","BTC","ETH","ZEC"]:
    df = load_symbol(sym,"5m",days_back=180)
    base = run(df, ZParams(z_thresh=3.0, sl_atr=1.5, tp_atr=3.0, use_ema=False, use_adx=False))
    base_n = len(base) if not base.empty else 0
    print(f"=== {sym} ===  (no gate: n={base_n})")
    print(f"  {'adx_max':>7} {'n':>5} {'kept%':>6} {'WR':>6} {'net$':>8} {'PF':>5} {'maxDD$':>8} {'avg$':>7} {'OOS$':>8}")
    for amax in [999, 40, 35, 30, 25, 20]:
        use = amax < 999
        t = run(df, ZParams(z_thresh=3.0, sl_atr=1.5, tp_atr=3.0, use_ema=False, use_adx=use, adx_max=amax))
        if t.empty or len(t) < 8:
            print(f"  {('off' if not use else amax):>7} {len(t):>5}   (<8 trades)"); continue
        k=kpis(t); _,oos=split_oos(t); ok=kpis(oos)
        kept = 100*len(t)/base_n if base_n else 0
        lbl = "off" if not use else str(amax)
        print(f"  {lbl:>7} {k['n']:>5} {kept:>5.0f}% {k['win_rate']*100:>5.1f}% {k['net_pnl']:>8,.0f} "
              f"{k['profit_factor']:>5.2f} {k['max_dd']:>8,.0f} {k['avg_trade']:>7.2f} {ok['net_pnl']:>8,.0f}")
    print()
