"""Zero-fee viability check for V3.2 (the "is there hope on Lighter?" question).

Same V3.2 HA-V3 strategy (live filters + 5-stage trail, %-stop = ZEC's 1.1%) as
coin_expansion.py, but sources OHLCV from BloFin via ccxt (Binance is geo-blocked
451 from this host). Runs each coin at BOTH zero fee (= Lighter) and BloFin fee
(0.06%/side) over ~180d, ranked by net. The DEMO/zero-fee block is the Lighter
scenario.

Run:
    PYTHONPATH="analysis;v3.1-drafts;analysis/sweeps/2026-05-20" \
        venv/Scripts/python.exe v3.2-analysis/lighter_zerofee_check.py
"""
import sys
import pandas as pd

from engine import fetch_ohlcv
from zec_v3_realistic import generate_v3_signals, apply_entry_filter
from v3_2_lab import run_bt, kpis, ExitModel, F_LIVE, base_params, span_days

# V3.2 live basket (the 7 coins actually trading on the box).
COINS = ["ZEC", "XRP", "DOGE", "SOL", "BTC", "BNB", "HYPE"]
DAYS = 180

rows = []
for c in COINS:
    try:
        # cache=False: skip engine's parquet cache (pyarrow's pandas-period
        # extension clashes on this box); fetch fresh each run.
        df = fetch_ohlcv(f"{c}/USDT:USDT", timeframe="5m", days_back=DAYS,
                         exchange="blofin", cache=False, verbose=False)
    except Exception as ex:
        print(f"  {c:5s} -- fetch failed: {ex}", flush=True)
        continue
    if df is None or len(df) < 2000:
        print(f"  {c:5s} -- insufficient bars", flush=True)
        continue
    df = apply_entry_filter(generate_v3_signals(df))
    days = span_days(df)
    for fee, tag in ((0.0, "demo"), (0.0006, "fee")):
        p = base_params(sl=82.5, fee=fee, slip=0.0006)
        tdf = run_bt(df, p, ExitModel("trail"), F_LIVE)
        k = kpis(tdf, p, days)
        ln = tdf[tdf.side == 'long'].pnl_net.sum() if not tdf.empty else 0
        sn = tdf[tdf.side == 'short'].pnl_net.sum() if not tdf.empty else 0
        rows.append(dict(coin=c, mode=tag, days=round(days), n=k['n'], WR=k['WR'],
                         net=k['net'], PF=k['PF'], maxDD=k['maxDD'],
                         netday=k.get('net_per_day'),
                         longnet=round(ln, 0), shortnet=round(sn, 0)))
    print(f"  {c:5s} done ({len(df)} bars)", flush=True)

if not rows:
    print("no data — aborting")
    sys.exit(1)

R = pd.DataFrame(rows)
for mode, label in (("demo", "DEMO / ZERO-FEE  (= Lighter)"), ("fee", "BLOFIN FEE 0.06%/side")):
    sub = R[R['mode'] == mode].sort_values('net', ascending=False)
    print(f"\n================ {label} — ranked by net ================")
    print(sub[['coin', 'days', 'n', 'WR', 'net', 'PF', 'maxDD', 'netday',
               'longnet', 'shortnet']].to_string(index=False))
    print(f"  BASKET net: {sub['net'].sum():+.0f}  | longs {sub['longnet'].sum():+.0f}"
          f"  shorts {sub['shortnet'].sum():+.0f}")

R.to_csv("data/lighter_zerofee_results.csv", index=False)
print("\nCSV: data/lighter_zerofee_results.csv")
