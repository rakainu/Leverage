"""TP/SL frontier sweep for the LIVE 8-coin Scalper basket (2026-06-25).

Rich's question: are we banking winners too soon (tp_frac=0.3 => take <1/3 of the
move back to VWAP) and is there room to tighten the 2.0-ATR stop?

Fetches OKX 5m -> 15m for the CURRENT live basket (ETH/BTC/SOL/HYPE/BNB/XMR +
DOGE/SUI) over `DAYS`, holds every other live param fixed (accel 3.0, slope-gate
0.08, z 1.5, etc.), and sweeps:
  A) tp_frac x sl_atr grid          -> the payoff-shape frontier
  B) scale-out variants             -> bank part at TP1, trail the rest to ~VWAP

Pooled metrics per config: n, PF, WR%, avg net% (per-coin avg), maxDD%, avg
win/loss $, implied breakeven-WR%, worst-10% tail $, liquidations.

Risk model mirrors the prior validated runs (fresh_basket_test.py): 1% risk,
10x cap, compounding, zero-fee + 0.05% slip — so results are directly comparable
to the deployed accel/slope-gate validation. This is a RELATIVE config compare,
not an absolute-$ live forecast (live = fixed $500@10x per coin).

Run: ../../venv/Scripts/python.exe tp_sl_sweep.py [days]
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

FULL_BASKET = ["ETH", "BTC", "SOL", "HYPE", "BNB", "XMR", "DOGE", "SUI"]
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 60
# argv[2] = comma coins (default full basket); argv[3] = end-before ISO date
# (exclude bars >= this UTC timestamp, e.g. cut a crash week out of sample).
LIVE_COINS = sys.argv[2].split(",") if len(sys.argv) > 2 and sys.argv[2] else FULL_BASKET
END_BEFORE = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None

# Exact live config (config.scalper.yaml) minus the two knobs we sweep.
LIVE = dict(trend_len=200, slope_lb=20, z_period=30, z_entry=1.5,
            max_bars=12, limit_atr=0.25, atr_p=14,
            accel_mult=3.0, min_slope_pct=0.08)
LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
RISK = RiskCfg(starting_equity=3600.0, risk_frac=0.01, max_leverage=10,
               liq_buffer=2.5, compounding=True)


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
    out = df.resample("15min").agg({"Open": "first", "High": "max", "Low": "min",
                                    "Close": "last", "Volume": "sum"}).dropna()
    if END_BEFORE:
        out = out[out.index < pd.Timestamp(END_BEFORE, tz="UTC")]
    return out


def run_combo(dfs, params):
    """Pool all coins under one config. Returns a metrics dict."""
    n = wins = liq = 0
    nets, dds = [], []
    wsum = lsum = 0.0
    n_win = n_loss = 0
    alltr = []
    for c, df in dfs.items():
        sigs = SL.REGISTRY["regime_mr"](df, side="both", **params)
        tr = simulate(df, sigs, LIGHTER, RISK, 15)
        m = metrics(tr, RISK.starting_equity)
        n += m["n"]; wins += round(m["win_rate"] / 100 * m["n"])
        nets.append(m["net_pct"]); dds.append(m["max_dd_pct"]); liq += m["liq_hits"]
        for t in tr:
            if t.pnl_usd > 0:
                wsum += t.pnl_usd; n_win += 1
            else:
                lsum += t.pnl_usd; n_loss += 1
        alltr.extend(tr)
    pf = wsum / -lsum if lsum < 0 else float("inf")
    wr = wins / n * 100 if n else 0
    avg_w = wsum / n_win if n_win else 0
    avg_l = lsum / n_loss if n_loss else 0
    be_wr = (-avg_l) / ((-avg_l) + avg_w) * 100 if (avg_w - avg_l) else 0
    losses = sorted([t.pnl_usd for t in alltr if t.pnl_usd < 0])
    k = max(1, len(losses) // 10)
    worst10 = sum(losses[:k]) if losses else 0.0
    return dict(n=n, pf=pf, wr=wr, net=float(np.mean(nets)), dd=float(np.mean(dds)),
                avg_w=avg_w, avg_l=avg_l, be_wr=be_wr, worst10=worst10, liq=liq)


def row(label, m):
    print(f"{label:>14} {m['n']:>5} {m['pf']:>6.2f} {m['wr']:>5.1f} {m['be_wr']:>6.1f} "
          f"{m['net']:>+7.1f} {m['dd']:>6.1f} {m['avg_w']:>7.1f} {m['avg_l']:>7.1f} "
          f"{m['worst10']:>9.0f} {m['liq']:>4}")


def header(title):
    print(f"\n{'='*92}\n{title}\n{'='*92}")
    print(f"{'config':>14} {'n':>5} {'PF':>6} {'WR%':>5} {'beWR%':>6} {'net%':>7} "
          f"{'DD%':>6} {'avgW$':>7} {'avgL$':>7} {'wrst10$':>9} {'liq':>4}")


def main():
    print(f"fetching OKX 5m -> 15m, {DAYS}d, basket={LIVE_COINS} ...")
    dfs = {}
    for c in LIVE_COINS:
        d = fetch_15m(c, DAYS)
        if d is None or len(d) < 300:
            print(f"  {c}: unavailable on OKX (skipped)"); continue
        dfs[c] = d
        print(f"  {c}: {len(d)} 15m bars {d.index[0].date()}->{d.index[-1].date()}")
    if not dfs:
        print("no data"); return

    # ---- baseline (exact live) ----
    base_params = dict(LIVE, sl_atr=2.0, tp_frac=0.3, tp1_frac=1.0, tp2_mult=0.0)
    header("LIVE BASELINE (sl_atr=2.0, tp_frac=0.3)")
    row("LIVE", run_combo(dfs, base_params))

    # ---- A) tp_frac x sl_atr grid ----
    header("A) TP_FRAC x SL_ATR GRID (single-target)")
    for sl in [1.5, 1.75, 2.0, 2.5]:
        for tp in [0.3, 0.4, 0.5, 0.6, 0.8, 1.0]:
            p = dict(LIVE, sl_atr=sl, tp_frac=tp, tp1_frac=1.0, tp2_mult=0.0)
            star = " *" if (abs(sl - 2.0) < 1e-9 and abs(tp - 0.3) < 1e-9) else ""
            row(f"sl{sl} tp{tp}{star}", run_combo(dfs, p))
        print()

    # ---- B) scale-out variants (bank part at TP1, run remainder toward VWAP) ----
    # tp2_mult is a multiple of tp_dist; tp_dist = tp_frac*gap, so tp2_mult=3.0 at
    # tp_frac=0.3 => runner target ~0.9 of the full gap (near VWAP).
    header("B) SCALE-OUT (bank tp1_frac at TP1, run rest to tp2; be_after_tp1=on)")
    scaleouts = [
        ("so 1/3@.3->VWAP", dict(sl_atr=2.0, tp_frac=0.3, tp1_frac=0.34, tp2_mult=3.0, be_after_tp1=True)),
        ("so 1/2@.3->VWAP", dict(sl_atr=2.0, tp_frac=0.3, tp1_frac=0.50, tp2_mult=3.0, be_after_tp1=True)),
        ("so 1/2@.4->2.5x", dict(sl_atr=2.0, tp_frac=0.4, tp1_frac=0.50, tp2_mult=2.5, be_after_tp1=True)),
        ("so 1/3@.4->2.0x", dict(sl_atr=2.0, tp_frac=0.4, tp1_frac=0.34, tp2_mult=2.0, be_after_tp1=True)),
        ("so 1/2@.3->2x sl1.75", dict(sl_atr=1.75, tp_frac=0.3, tp1_frac=0.50, tp2_mult=3.0, be_after_tp1=True)),
    ]
    for lbl, extra in scaleouts:
        row(lbl, run_combo(dfs, dict(LIVE, **extra)))

    print("\nread: beWR% = breakeven win rate at that payoff shape. A config wins if")
    print("net% up AND (live WR stays above beWR%) — i.e. real cushion, not just")
    print("trading hit-rate for size along the same breakeven line. Watch liq + DD.")


if __name__ == "__main__":
    main()
