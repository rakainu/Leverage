"""Asymmetric long/short sweep for the LIVE Scalper (regime_mr).  2026-06-26

WHY: live long/short review (paper, 06-15->06-27) showed longs are dead weight
(PF 0.81 / net -$252) while shorts are the edge (PF 1.55 / +$654). Equal WR ~77/80
-> the gap is WIN SIZE: long reversions stall (~$12.5) vs shorts (~$18). Most of the
long bleed was the now-PAUSED coins; among the 3 keepers longs are PF ~1.11 (marginal
ballast), shorts PF ~2.67. It's regime-driven: longs catch knives in downtrends.

regime_mr ALREADY gates side by trend (up = EMA-slope>0: long only in up, short only
in down/flat), but `min_slope_pct` (trend-clarity) and `sl_atr` (stop) are SYMMETRIC.
So in a downtrend longs still fire on noise-positive slope blips (dead-cat bounces).

GOAL = grow NET by reallocating toward the edge, NOT trimming for lower risk:
  (a) stop feeding the losing side  -> stricter long slope-gate / tighter long stop
  (b) feed the winning side MORE    -> looser short z-entry / slope-gate (more shorts)

This makes TWO EXISTING KNOBS ASYMMETRIC by calling regime_mr(side="long") and
regime_mr(side="short") with different params and pooling. ZERO change to strat_lib.

SCORING (hard rule, Rich): NET first, at 0.05% AND 0.10% slip. A variant that lifts
PF/DD but lowers net is REJECTED (same call as the trailing-exit test). t/wk shown so
a conservative frequency cut can't masquerade as an improvement.

Run: ../../venv/Scripts/python.exe side_asym_sweep.py [days] [coins] [end-before-ISO]
  e.g. ../../venv/Scripts/python.exe side_asym_sweep.py 120 "ETH,HYPE,BNB"
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

KEEPERS = ["ETH", "HYPE", "BNB"]                 # current LIVE basket
FULL = ["ETH", "BTC", "SOL", "HYPE", "BNB", "XMR", "DOGE", "SUI"]
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 120
COINS = sys.argv[2].split(",") if len(sys.argv) > 2 and sys.argv[2] else KEEPERS
END_BEFORE = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None

# Current LIVE config (config.scalper.yaml, post 06-25 keepers cut) minus the
# per-side knobs we make asymmetric. sl_atr is 1.75 live now (was 2.0).
LIVE = dict(trend_len=200, slope_lb=20, z_period=30, z_entry=1.5,
            tp_frac=0.3, max_bars=12, limit_atr=0.25, atr_p=14, accel_mult=3.0)
LIVE_SL = 1.75
LIVE_SLOPE = 0.08
LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
HISLIP = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.10, funding_pct_per_8h=0.01)
RISK = RiskCfg(starting_equity=3600.0, risk_frac=0.01, max_leverage=10,
               liq_buffer=2.5, compounding=True)


def fetch_15m(coin, days):
    ex = ccxt.okx({"enableRateLimit": True})
    sym = f"{coin}/USDT:USDT"
    end = ex.milliseconds()
    since = end - days * 86400 * 1000
    rows, cursor, stall = {}, since, 0
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


def run_combo(dfs, long_p, short_p, costs=LIGHTER):
    """Pool all coins; longs use long_p, shorts use short_p. Returns metrics + per-side net."""
    n = wins = liq = 0
    nets, dds = [], []
    wsum = lsum = 0.0
    n_win = n_loss = 0
    long_net = short_net = 0.0
    long_n = short_n = 0
    alltr = []
    weeks = 0.0
    for c, df in dfs.items():
        weeks += len(df) * 15 / (60 * 24 * 7)
        sigs = []
        if long_p is not None:
            sigs += SL.REGISTRY["regime_mr"](df, side="long", **long_p)
        if short_p is not None:
            sigs += SL.REGISTRY["regime_mr"](df, side="short", **short_p)
        sigs.sort(key=lambda s: s.i)            # one-position-at-a-time, time order
        tr = simulate(df, sigs, costs, RISK, 15)
        m = metrics(tr, RISK.starting_equity)
        n += m["n"]; wins += round(m["win_rate"] / 100 * m["n"])
        nets.append(m["net_pct"]); dds.append(m["max_dd_pct"]); liq += m["liq_hits"]
        for t in tr:
            if t.pnl_usd > 0:
                wsum += t.pnl_usd; n_win += 1
            else:
                lsum += t.pnl_usd; n_loss += 1
            if t.side > 0:
                long_net += t.pnl_usd; long_n += 1
            else:
                short_net += t.pnl_usd; short_n += 1
        alltr.extend(tr)
    pf = wsum / -lsum if lsum < 0 else float("inf")
    wr = wins / n * 100 if n else 0
    losses = sorted([t.pnl_usd for t in alltr if t.pnl_usd < 0])
    k = max(1, len(losses) // 10)
    worst10 = sum(losses[:k]) if losses else 0.0
    tpw = n / weeks if weeks else 0
    return dict(n=n, tpw=tpw, pf=pf, wr=wr, net=float(np.mean(nets)), dd=float(np.mean(dds)),
                worst10=worst10, liq=liq, long_net=long_net, short_net=short_net,
                long_n=long_n, short_n=short_n)


def run_both(dfs, long_p, short_p):
    """Run at 0.05% and 0.10% slip; return (base_metrics, hislip_net%)."""
    m = run_combo(dfs, long_p, short_p, LIGHTER)
    mh = run_combo(dfs, long_p, short_p, HISLIP)
    m["net_hi"] = mh["net"]
    return m


def header(title):
    print(f"\n{'='*108}\n{title}\n{'='*108}")
    print(f"{'config':>26} {'n':>4} {'t/wk':>5} {'PF':>5} {'WR%':>5} "
          f"{'net%':>6} {'net%hi':>7} {'DD%':>5} {'Lnet$':>7} {'Snet$':>7} "
          f"{'Ln':>4} {'Sn':>4} {'wrst10':>7} {'liq':>3}")


def row(label, m):
    print(f"{label:>26} {m['n']:>4} {m['tpw']:>5.1f} {m['pf']:>5.2f} {m['wr']:>5.1f} "
          f"{m['net']:>+6.1f} {m['net_hi']:>+7.1f} {m['dd']:>5.1f} "
          f"{m['long_net']:>+7.0f} {m['short_net']:>+7.0f} {m['long_n']:>4} {m['short_n']:>4} "
          f"{m['worst10']:>7.0f} {m['liq']:>3}")


def main():
    print(f"fetching OKX 5m->15m, {DAYS}d, coins={COINS}"
          + (f", end<{END_BEFORE}" if END_BEFORE else ""))
    dfs = {}
    for c in COINS:
        d = fetch_15m(c, DAYS)
        if d is None or len(d) < 300:
            print(f"  {c}: unavailable on OKX (skipped)"); continue
        dfs[c] = d
        print(f"  {c}: {len(d)} 15m bars {d.index[0].date()}->{d.index[-1].date()}")
    if not dfs:
        print("no data"); return

    L = dict(LIVE, sl_atr=LIVE_SL, min_slope_pct=LIVE_SLOPE)   # long base params
    S = dict(LIVE, sl_atr=LIVE_SL, min_slope_pct=LIVE_SLOPE)   # short base params

    # ---- references ----
    header("REFERENCES (net% is per-coin avg; Lnet/Snet = pooled $ by side)")
    row("LIVE both (sym)", run_both(dfs, dict(L), dict(S)))
    row("SHORT-ONLY (longs off)", run_both(dfs, None, dict(S)))
    row("LONG-ONLY (shorts off)", run_both(dfs, dict(L), None))

    # ---- C) cut the long bleed: stricter long slope-gate (shorts unchanged) ----
    header("C) ASYM LONG SLOPE-GATE  (long min_slope_pct up; short=0.08, sl=1.75 both)")
    for ms in [0.08, 0.12, 0.16, 0.20]:
        lp = dict(L, min_slope_pct=ms)
        row(f"Lslope{ms} / Sslope0.08", run_both(dfs, lp, dict(S)))

    # ---- D) cut the long bleed: tighter long stop (shorts unchanged) ----
    header("D) ASYM LONG STOP  (long sl_atr down; short sl=1.75, slope 0.08 both)")
    for sl in [1.25, 1.5, 1.75]:
        lp = dict(L, sl_atr=sl)
        row(f"Lsl{sl} / Ssl1.75", run_both(dfs, lp, dict(S)))

    # ---- E) feed the edge: take MORE shorts (longs unchanged) ----
    header("E) AGGRESSIVE SHORTS  (short z / slope looser; long=base)")
    for zz in [1.25, 1.5]:
        for ms in [0.05, 0.08]:
            sp = dict(S, z_entry=zz, min_slope_pct=ms)
            row(f"Sz{zz} Sslope{ms}", run_both(dfs, dict(L), sp))

    # ---- F) combos: best long-fix + aggressive shorts together ----
    header("F) COMBOS  (cut longs + feed shorts)")
    combos = [
        ("Lslope0.16 + Sz1.25",      dict(L, min_slope_pct=0.16), dict(S, z_entry=1.25)),
        ("Lsl1.25 + Sz1.25",         dict(L, sl_atr=1.25),        dict(S, z_entry=1.25)),
        ("Lslope0.16+Lsl1.5 + Sz1.25", dict(L, min_slope_pct=0.16, sl_atr=1.5), dict(S, z_entry=1.25)),
        ("SHORT-ONLY + Sz1.25",      None,                        dict(S, z_entry=1.25)),
    ]
    for lbl, lp, sp in combos:
        row(lbl, run_both(dfs, lp, sp))

    print("\nREAD: judge on net% (0.05 slip) AND net%hi (0.10 slip) vs 'LIVE both'.")
    print("Reject anything that only cuts DD/raises PF while LOWERING net (conservative trap).")
    print("Lnet/Snet$ = pooled $ contributed by each side; Ln/Sn = trades per side.")
    print("A real win = higher net with longs' drag removed AND/OR shorts' edge amplified.")


if __name__ == "__main__":
    main()
