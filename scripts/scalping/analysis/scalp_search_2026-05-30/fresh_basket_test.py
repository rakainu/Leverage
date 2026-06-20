"""Fresh-data validation for the LIVE 6-coin scalper basket: accel-guard tail fix
+ leverage/sizing sweep, on the CURRENT window (incl. the recent rough week).

Fetches OKX 5m for ETH/BTC/SOL/HYPE/BNB/XMR over `DAYS`, resamples to 15m, and:
  A) accel-guard sweep  -> does declining to fade climax bars cut the tail?
  B) leverage sweep     -> net vs maxDD vs LIQUIDATIONS as the cap rises.

Run: ../../venv/Scripts/python.exe fresh_basket_test.py [days]
"""
from __future__ import annotations
import os, sys, time
import numpy as np
import pandas as pd
import ccxt

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "sol_strategy_2026-05-30"))
from btengine import simulate, metrics, Costs, RiskCfg  # noqa: E402
import strat_lib as SL  # noqa: E402

LIVE_COINS = ["ETH", "BTC", "SOL", "HYPE", "BNB", "XMR"]
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 45
LIVE = dict(trend_len=200, slope_lb=20, z_period=30, z_entry=1.5,
            sl_atr=2.0, tp_frac=0.3, max_bars=12, limit_atr=0.25, atr_p=14)
LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)


def fetch_15m(coin, days):
    ex = ccxt.okx({"enableRateLimit": True})
    sym = f"{coin}/USDT:USDT"
    end = ex.milliseconds()
    since = end - days * 86400 * 1000
    rows = {}
    cursor = since
    stall = 0
    while cursor < end and stall < 3:
        try:
            ch = ex.fetch_ohlcv(sym, "5m", since=cursor, limit=300)
        except Exception as e:
            print(f"  {coin} fetch err: {e}"); stall += 1; time.sleep(1); continue
        if not ch:
            stall += 1; cursor += 300 * 5 * 60 * 1000; continue
        stall = 0
        for t, o, h, l, c, v in ch:
            rows[t] = (o, h, l, c, v)
        cursor = ch[-1][0] + 5 * 60 * 1000
        time.sleep(0.25)
    if not rows:
        return None
    df = pd.DataFrame([(k, *v) for k, v in sorted(rows.items())],
                      columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts").astype(float)
    return df.resample("15min").agg({"Open": "first", "High": "max", "Low": "min",
                                     "Close": "last", "Volume": "sum"}).dropna()


def run_df(df, params, risk):
    sigs = SL.REGISTRY["regime_mr"](df, side="both", **params)
    trades = simulate(df, sigs, LIGHTER, risk, 15)
    return metrics(trades, risk.starting_equity), trades


def tail(trades):
    losses = sorted([t.pnl_usd for t in trades if t.pnl_usd < 0])
    if not losses:
        return 0.0, 0.0
    k = max(1, len(losses) // 10)
    return losses[0], sum(losses[:k])


def main():
    print(f"fetching OKX 5m -> 15m, {DAYS}d, basket={LIVE_COINS} ...")
    dfs = {}
    for c in LIVE_COINS:
        d = fetch_15m(c, DAYS)
        if d is None or len(d) < 300:
            print(f"  {c}: unavailable on OKX (skipped)"); continue
        dfs[c] = d
        print(f"  {c}: {len(d)} 15m bars {d.index[0].date()}->{d.index[-1].date()}")
    coins = list(dfs)
    if not coins:
        print("no data"); return

    # ---- A) accel-guard sweep (risk model = live-ish: 1% risk, 10x cap) ----
    riskA = RiskCfg(starting_equity=3600.0, risk_frac=0.01, max_leverage=10, liq_buffer=2.5, compounding=True)
    print(f"\n{'='*72}\nA) ACCEL-GUARD SWEEP (1% risk, 10x cap, zero-fee) — pooled basket\n{'='*72}")
    print(f"{'accel':>6} {'n':>5} {'PF':>6} {'WR%':>5} {'net%':>7} {'DD%':>6} "
          f"{'worst$':>8} {'worst10%$':>10} {'liq':>4}")
    for g in [0.0, 3.5, 3.0, 2.5]:
        params = dict(LIVE)
        if g > 0:
            params["accel_mult"] = g
        n = wins = liq = 0; nets = []; dds = []; wsum = lsum = 0.0; alltr = []
        for c in coins:
            m, tr = run_df(dfs[c], params, riskA)
            n += m["n"]; wins += round(m["win_rate"] / 100 * m["n"])
            nets.append(m["net_pct"]); dds.append(m["max_dd_pct"]); liq += m["liq_hits"]
            wsum += sum(t.pnl_usd for t in tr if t.pnl_usd > 0)
            lsum += sum(t.pnl_usd for t in tr if t.pnl_usd <= 0)
            alltr.extend(tr)
        pf = wsum / -lsum if lsum < 0 else float("inf")
        wr = wins / n * 100 if n else 0
        worst, worst10 = tail(alltr)
        lbl = "OFF" if g == 0 else f"{g:.1f}"
        print(f"{lbl:>6} {n:>5} {pf:>6.2f} {wr:>5.0f} {np.mean(nets):>+7.1f} "
              f"{np.mean(dds):>6.1f} {worst:>8.1f} {worst10:>10.0f} {liq:>4}")

    print(f"\n--- per-coin net% (current {DAYS}d window): baseline vs guard 3.0 ---")
    pg = dict(LIVE); pg["accel_mult"] = 3.0
    print(f"{'coin':5} {'base PF':>8} {'base net%':>10} {'g3.0 PF':>8} {'g3.0 net%':>10} {'n':>5}")
    for c in coins:
        mb, _ = run_df(dfs[c], LIVE, riskA)
        mg, _ = run_df(dfs[c], pg, riskA)
        print(f"{c:5} {mb['profit_factor']:>8.2f} {mb['net_pct']:>+10.1f} "
              f"{mg['profit_factor']:>8.2f} {mg['net_pct']:>+10.1f} {mb['n']:>5}")

    # ---- B) leverage sweep (guard 3.0 ON), net vs DD vs liquidations ----
    print(f"\n{'='*72}\nB) LEVERAGE SWEEP (accel-guard 3.0 on, 1% risk, zero-fee)\n{'='*72}")
    print(f"{'maxLev':>6} {'net%':>7} {'DD%':>6} {'liq':>4} {'maxLevUsed':>11} {'net/DD':>7}")
    for lev in [10, 15, 20, 30, 50]:
        riskB = RiskCfg(starting_equity=3600.0, risk_frac=0.01, max_leverage=lev,
                        liq_buffer=2.5, compounding=True)
        nets = []; dds = []; liq = 0; mlev = 0.0
        for c in coins:
            m, _ = run_df(dfs[c], pg, riskB)
            nets.append(m["net_pct"]); dds.append(m["max_dd_pct"])
            liq += m["liq_hits"]; mlev = max(mlev, m["max_leverage_used"])
        netm = np.mean(nets); ddm = np.mean(dds)
        ratio = netm / abs(ddm) if ddm else 0
        print(f"{lev:>6} {netm:>+7.1f} {ddm:>6.1f} {liq:>4} {mlev:>11.1f} {ratio:>7.2f}")
    print("\nread A: guard wins if PF/net up + tail (worst10%) shrinks at ~same n.")
    print("read B: net rises with leverage but so do liq + DD — sweet spot = best "
          "net/DD with liq still ~0.")


if __name__ == "__main__":
    main()
