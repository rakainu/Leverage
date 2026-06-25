"""COMPREHENSIVE entry-setup grid for Reclaim — find the best REAL (recreatable
on Lighter) entry, sweeping every knob in BOTH directions (tighter AND looser
than live), not just the conservative side.

Live entry = M13 reclaim @ gap 0.05%, overshoot 0.2%, timeout 6 bars, F_LIVE
(no-Sunday, slope>=0.15, block body 0.3-0.5). This grid moves each knob both ways.

Models (all recreatable on Lighter except M0, the phantom ceiling):
  M0   ideal ema[i] fill        — NOT recreatable; reference ceiling only
  M3   market @ open[i+1]       — recreatable
  M12  M3 + entry-gap cap       — recreatable (gap known at fill)
  M13  touch->reclaim + gap cap — recreatable (LIVE model)
  M11  bounce-break stop entry  — recreatable

Knobs swept BOTH directions:
  gap_cap   : uncapped, 0.03, 0.04, 0.05*, 0.06, 0.07, 0.08, 0.10, 0.12, 0.15, 0.20 %
  overshoot : 0.10, 0.20*, 0.30, 0.40 %     (how deep the wick may pierce EMA9)
  timeout   : 3, 6*, 9, 12 bars             (how long the setup stays armed)
  slope_min : 0.00, 0.03, 0.08, 0.15*, 0.25 % (trend-clarity floor)
  filters   : Sunday on/off  x  body-band on/off

Pooled across the 7 live coins, ZERO-FEE (Lighter), sl=82.5, trail exit. KPIs:
n, net$, %M0 (net vs phantom ceiling), PF, WR, maxDD, trades/wk. Finalists get an
IS/OOS split (first half vs second half) so we pick a REAL entry, not an overfit.

Run: ../venv/Scripts/python.exe entry_grid.py [days]   (default 150)
"""
import os, sys
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ["", "../analysis", "../v3.1-drafts", "../analysis/sweeps/2026-05-20"]:
    sys.path.insert(0, os.path.normpath(os.path.join(HERE, p)) if p else HERE)

import entry_v2_search as EV
import zec_v3_realistic as Z
from engine import fetch_ohlcv
from v3_2_lab import run_bt, ExitModel, base_params
from zec_v3_realistic import generate_v3_signals, apply_entry_filter, EntryFilters

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 150
COINS = ["BNB-USDT", "BTC-USDT", "DOGE-USDT", "HYPE-USDT", "SOL-USDT", "XRP-USDT", "ZEC-USDT"]
P = base_params(sl=82.5, fee=0.0, slip=0.0)     # Lighter zero-fee, no entry slip in model
EM = ExitModel("trail")

# live defaults (for restore)
_LIVE_OVER, _LIVE_TIMEOUT, _LIVE_SLOPE = 0.2, 6, 0.15


def set_globals(overshoot, timeout, slope_min):
    EV.RETEST_OVERSHOOT_PCT = overshoot
    Z.RETEST_OVERSHOOT_PCT = overshoot
    EV.RETEST_TIMEOUT_BARS = timeout
    Z.RETEST_TIMEOUT_BARS = timeout
    EV.MIN_SLOPE_PCT = slope_min
    Z.MIN_SLOPE_PCT = slope_min


def mk_filter(sunday_block=True, body_block=True):
    return EntryFilters(
        block_weekdays={6} if sunday_block else None,
        min_abs_slope_pct=0.0,                         # slope handled via MIN_SLOPE_PCT
        block_body_band=(0.3, 0.5) if body_block else None)


def kpis(trades, days):
    if trades is None or len(trades) == 0:
        return dict(n=0, net=0.0, pf=0.0, wr=0.0, dd=0.0, tpw=0.0)
    t = trades.sort_values("entry_ts")
    pnl = t["pnl_net"].values            # zero-fee => net
    n = len(pnl); net = float(pnl.sum())
    win = pnl[pnl > 0].sum(); loss = pnl[pnl < 0].sum()
    pf = win / -loss if loss < 0 else float("inf")
    wr = 100.0 * (pnl > 0).mean()
    eq = np.cumsum(pnl); dd = float((eq - np.maximum.accumulate(eq)).min())
    return dict(n=n, net=net, pf=pf, wr=wr, dd=dd, tpw=n / (days / 7.0))


def run_model(dfs, model, gap=None, buf=0.0, filt=None, days=DAYS):
    """Pool one config across coins -> KPI dict."""
    parts = []
    for df in dfs:
        if model == "M0":
            t = run_bt(df, P, EM, filt)
        elif model == "M3":
            t = EV.run_m3_gap(df, P, filt, gap_cap=gap)
        elif model == "M13":
            t = EV.run_reclaim_gap(df, P, filt, gap_cap=gap)
        elif model == "M11":
            t = EV.run_bounce_break(df, P, filt, buf_pct=buf)
        else:
            raise ValueError(model)
        if t is not None and not t.empty:
            parts.append(t)
    allt = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    return kpis(allt, days)


def row(lbl, k, m0net):
    pct = (k["net"] / m0net * 100) if m0net else 0.0
    print(f"{lbl:30s} {k['n']:>5d} {k['net']:>9.0f} {pct:>6.0f} {k['pf']:>6.2f} "
          f"{k['wr']:>5.1f} {k['dd']:>8.0f} {k['tpw']:>6.1f}")


def hdr(title):
    print(f"\n{'='*86}\n{title}\n{'='*86}")
    print(f"{'config':30s} {'n':>5s} {'net$':>9s} {'%M0':>6s} {'PF':>6s} {'WR':>5s} {'maxDD':>8s} {'t/wk':>6s}")


def main():
    print(f"fetching BloFin 5m, {DAYS}d, {len(COINS)} coins (zero-fee/Lighter scoring)...")
    raw = []
    for c in COINS:
        df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                         days_back=DAYS, exchange="blofin", cache=True, verbose=False)
        df = apply_entry_filter(generate_v3_signals(df.copy()))
        raw.append(df); print(f"  {c}: {len(df)} bars")
    # IS/OOS split by row midpoint per coin
    dfs_full = raw
    dfs_is = [d.iloc[:len(d)//2] for d in raw]
    dfs_oos = [d.iloc[len(d)//2:] for d in raw]
    HALF = DAYS / 2.0

    FL = mk_filter(True, True)   # live non-slope filter (slope via MIN_SLOPE_PCT)
    set_globals(_LIVE_OVER, _LIVE_TIMEOUT, _LIVE_SLOPE)
    m0 = run_model(dfs_full, "M0", filt=FL)
    m0net = m0["net"] or 1e-9

    hdr("REFERENCE — phantom ceiling vs live entry (full window, F_LIVE)")
    row("M0 ideal (NOT recreatable)", m0, m0net)
    row("M13 reclaim gap0.05 (LIVE)", run_model(dfs_full, "M13", gap=0.0005, filt=FL), m0net)

    # ---- STAGE A: model x gap, both directions (default overshoot/timeout/slope) ----
    GAPS = [None, 0.0003, 0.0004, 0.0005, 0.0006, 0.0007, 0.0008, 0.0010, 0.0012, 0.0015, 0.0020]
    set_globals(_LIVE_OVER, _LIVE_TIMEOUT, _LIVE_SLOPE)
    hdr("A) MODEL x GAP-CAP  (overshoot0.2 timeout6 slope0.15, F_LIVE)")
    row("M3 market (uncapped)", run_model(dfs_full, "M3", gap=None, filt=FL), m0net)
    for g in GAPS:
        lbl = "uncapped" if g is None else f"{g*100:.2f}%"
        star = " *" if g == 0.0005 else ""
        row(f"M13 reclaim gap {lbl}{star}", run_model(dfs_full, "M13", gap=g, filt=FL), m0net)
    for g in GAPS:
        lbl = "uncapped" if g is None else f"{g*100:.2f}%"
        row(f"M12 market  gap {lbl}", run_model(dfs_full, "M12" if False else "M3", gap=g, filt=FL), m0net)
    for b in [0.0, 0.05, 0.10]:
        row(f"M11 break buf{b:.2f}", run_model(dfs_full, "M11", buf=b, filt=FL), m0net)

    # ---- STAGE B: M13 x overshoot x timeout, both directions (gap 0.05 + 0.08) ----
    for gap in [0.0005, 0.0008]:
        hdr(f"B) M13 x OVERSHOOT x TIMEOUT  (gap {gap*100:.2f}%, slope0.15, F_LIVE)")
        for over in [0.10, 0.20, 0.30, 0.40]:
            for to in [3, 6, 9, 12]:
                set_globals(over, to, _LIVE_SLOPE)
                star = " *" if (over == 0.2 and to == 6 and gap == 0.0005) else ""
                row(f"over{over:.2f} to{to:<2d}{star}",
                    run_model(dfs_full, "M13", gap=gap, filt=FL), m0net)
    set_globals(_LIVE_OVER, _LIVE_TIMEOUT, _LIVE_SLOPE)

    # ---- STAGE C: slope floor + filter directions on M13 (gap 0.05 & 0.08) ----
    for gap in [0.0005, 0.0008]:
        hdr(f"C) M13 x SLOPE-FLOOR x FILTERS  (gap {gap*100:.2f}%, over0.2 to6)")
        for slope in [0.00, 0.03, 0.08, 0.15, 0.25]:
            set_globals(_LIVE_OVER, _LIVE_TIMEOUT, slope)
            star = " *" if slope == 0.15 else ""
            row(f"slope{slope:.2f} F_LIVE{star}",
                run_model(dfs_full, "M13", gap=gap, filt=FL), m0net)
        set_globals(_LIVE_OVER, _LIVE_TIMEOUT, 0.08)   # mid slope to expose filter effects
        for sun, body, nm in [(True, True, "noSun+noBody"), (True, False, "noSun only"),
                              (False, True, "noBody only"), (False, False, "BARE")]:
            row(f"slope0.08 {nm}",
                run_model(dfs_full, "M13", gap=gap, filt=mk_filter(sun, body)), m0net)
    set_globals(_LIVE_OVER, _LIVE_TIMEOUT, _LIVE_SLOPE)

    # ---- STAGE D: IS/OOS robustness on a hand-picked finalist set ----
    # (model, gap, over, timeout, slope, sunday, body, label)
    FINALS = [
        ("M13", 0.0005, 0.20, 6, 0.15, True, True, "LIVE reclaim 0.05"),
        ("M13", 0.0006, 0.20, 6, 0.15, True, True, "reclaim 0.06"),
        ("M13", 0.0008, 0.20, 6, 0.15, True, True, "reclaim 0.08"),
        ("M13", 0.0008, 0.30, 9, 0.08, True, True, "reclaim 0.08 loose over/to"),
        ("M13", 0.0010, 0.20, 6, 0.08, True, False, "reclaim 0.10 noBody slope0.08"),
        ("M13", None,   0.20, 6, 0.15, True, True, "reclaim uncapped"),
        ("M3",  0.0005, 0.20, 6, 0.15, True, True, "market gap0.05"),
        ("M3",  0.0008, 0.20, 6, 0.15, True, True, "market gap0.08"),
        ("M11", None,   0.20, 6, 0.15, True, True, "break buf0"),
    ]
    hdr("D) IS / OOS ROBUSTNESS on finalists (PF + net must hold in BOTH halves)")
    print(f"{'config':30s} {'IS_n':>5s} {'IS_net':>7s} {'IS_PF':>6s} | "
          f"{'OOS_n':>5s} {'OOS_net':>7s} {'OOS_PF':>6s}  verdict")
    for mdl, gap, over, to, slope, sun, body, name in FINALS:
        set_globals(over, to, slope)
        f = mk_filter(sun, body)
        buf = 0.0
        ki = run_model(dfs_is, mdl, gap=gap, buf=buf, filt=f, days=HALF)
        ko = run_model(dfs_oos, mdl, gap=gap, buf=buf, filt=f, days=HALF)
        ok = ki["pf"] >= 1.2 and ko["pf"] >= 1.2 and ki["net"] > 0 and ko["net"] > 0
        v = "ROBUST" if ok else ("oos-fade" if ki["net"] > 0 and ko["net"] <= 0 else "weak")
        print(f"{name:30s} {ki['n']:>5d} {ki['net']:>7.0f} {ki['pf']:>6.2f} | "
              f"{ko['n']:>5d} {ko['net']:>7.0f} {ko['pf']:>6.2f}  {v}")
    set_globals(_LIVE_OVER, _LIVE_TIMEOUT, _LIVE_SLOPE)

    print("\nread: %M0 = how much of the phantom ceiling a REAL entry captures. Want")
    print("more trades (t/wk up) while net/%M0 and PF hold and OOS doesn't fade.")


if __name__ == "__main__":
    main()
