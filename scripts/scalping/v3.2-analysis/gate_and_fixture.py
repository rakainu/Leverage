import sys
import numpy as np, pandas as pd
sys.path.insert(0, 'strategies')
from v3_2_lab import load_and_signal, run_bt, kpis, ExitModel, base_params, span_days
from zec_v3_realistic import EntryFilters

df = load_and_signal(); days = span_days(df); p = base_params()

# ---- 1. Golden parity fixture: last 700 bars w/ engine-computed signals ----
fx = df.iloc[-700:][["Open","High","Low","Close","buy_sig","sell_sig","adx","body_atr_ratio","ema9","slope_pct"]].copy()
fx.to_csv("data/signal_golden_fixture.csv")
print(f"FIXTURE: wrote {len(fx)} bars, buy={int(fx.buy_sig.sum())} sell={int(fx.sell_sig.sum())} -> data/signal_golden_fixture.csv")

# ---- 2. Gate-filter decision test (181-day engine, both sides, trail, SL82.5) ----
F = EntryFilters
recipes = {
  "baseline (Flive)":            F(block_weekdays={6}, min_abs_slope_pct=0.15, block_body_band=(0.3,0.5)),
  "+ block hours 14,19":         F(block_weekdays={6}, min_abs_slope_pct=0.15, block_body_band=(0.3,0.5), block_hours_utc={14,19}),
  "+ min_adx 18":                F(block_weekdays={6}, min_abs_slope_pct=0.15, block_body_band=(0.3,0.5), min_adx=18.0),
  "+ min_adx 20":                F(block_weekdays={6}, min_abs_slope_pct=0.15, block_body_band=(0.3,0.5), min_adx=20.0),
  "+ hours14,19 + min_adx18":    F(block_weekdays={6}, min_abs_slope_pct=0.15, block_body_band=(0.3,0.5), block_hours_utc={14,19}, min_adx=18.0),
}
print("\n=== GATE DECISION (181d, both sides, trail, SL82.5, blofin fee+slip) ===")
for name, flt in recipes.items():
    t = run_bt(df, p, ExitModel("trail"), flt)
    k = kpis(t, p, days)
    print(f"  {name:30s} n={k['n']:<4} net=${k['net']:<9} PF={k['PF']:<5} maxDD=${k['maxDD']:<8} calmar={k.get('calmar')} net/day=${k.get('net_per_day')}")
