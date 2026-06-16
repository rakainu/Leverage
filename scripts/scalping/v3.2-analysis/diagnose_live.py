"""Fetch real ZEC 5m bars for the live window and run the V3.1 engine model
on them — isolates regime vs signal divergence vs execution for the live gap."""
import sys, json, time, urllib.request
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, 'strategies')
from zec_v3_realistic import generate_v3_signals, apply_entry_filter
from v3_2_lab import run_bt, kpis, ExitModel, F_LIVE, F_NOFILT, base_params, span_days

def fetch_binance(symbol="ZECUSDT", interval="5m", start="2026-05-14", end="2026-06-17"):
    s = int(pd.Timestamp(start, tz="UTC").timestamp()*1000)
    e = int(pd.Timestamp(end, tz="UTC").timestamp()*1000)
    rows = []
    url0 = "https://api.binance.com/api/v3/klines"
    cur = s
    while cur < e:
        u = f"{url0}?symbol={symbol}&interval={interval}&startTime={cur}&limit=1000"
        with urllib.request.urlopen(u, timeout=30) as r:
            data = json.load(r)
        if not data: break
        rows += data
        cur = data[-1][0] + 1
        if len(data) < 1000: break
        time.sleep(0.2)
    df = pd.DataFrame(rows, columns=["t","Open","High","Low","Close","v","ct","q","n","tb","tq","ig"])
    df["dt"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df = df.set_index("dt")[["Open","High","Low","Close"]].astype(float)
    df = df[~df.index.duplicated()].sort_index()
    return df

print("Fetching ZEC 5m from Binance for live window...")
df = fetch_binance()
print(f"Got {len(df)} bars: {df.index[0]} -> {df.index[-1]}  ({span_days(df):.1f} days)")
df = generate_v3_signals(df); df = apply_entry_filter(df)
print(f"Raw Pine sigs: buy={int(df['buy_sig'].sum())} sell={int(df['sell_sig'].sum())}")
days = span_days(df)

for fee, lbl in ((0.0,"ZERO fee (demo-like)"), (0.0006,"BloFin 0.06% fee")):
    p = base_params(sl=82.5, fee=fee, slip=0.0006)
    print(f"\n=== LIVE-WINDOW ENGINE, V3.1 config (F_LIVE, SL82.5), {lbl} ===")
    t = run_bt(df, p, ExitModel("trail"), F_LIVE)
    print("  ALL  :", kpis(t, p, days))
    if not t.empty:
        print("  LONG :", kpis(t[t.side=='long'], p, days))
        print("  SHORT:", kpis(t[t.side=='short'], p, days))
        print("  exit reasons:", t.exit_reason.value_counts().to_dict())

# also unfiltered to compare signal count vs live (live had ~133 trades/month)
p = base_params(sl=82.5, fee=0.0, slip=0.0006)
t = run_bt(df, p, ExitModel("trail"), F_NOFILT)
print(f"\n=== LIVE-WINDOW, NO filters, SL82.5, zero fee (compare trade count to live 103 ZEC) ===")
print("  ALL  :", kpis(t, p, days))
if not t.empty:
    print("  LONG :", kpis(t[t.side=='long'], p, days))
    print("  SHORT:", kpis(t[t.side=='short'], p, days))
