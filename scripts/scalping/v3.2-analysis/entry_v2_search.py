"""Entry-parity v2 search — the "closest honest twin" experiments.

Background: the engine (M0) fills at ema[i] — this bar's EMA9, which needs this
bar's close. Non-causal. realizable_fill_compare.py proved the best honest
replica (M3 = market on a CLOSED-bar confirmed retest, fill at open[i+1]) keeps
~96% of M0's trade SELECTION but loses ~7-9% of net because its mean fill sits
~0.34% off the phantom EMA9, and at 30x that ~$29/trade ≈ the whole edge.

This module attacks the *cost*, not the selection. New realizable entries:

  M3   reference best-realizable (market at open[i+1] on confirmed retest).
  M12  M3 + MAX-ENTRY-GAP filter: skip the trade when |open[i+1]-ema[i]|/ema[i]
       exceeds a cap. The gap is KNOWN at fill time (bar i closed, fill=open[i+1])
       so this is causal + deployable. Hypothesis: count drops, PF/net climb,
       and some cap turns M3 net-positive WITH fees.
  M13  touch->reclaim (M6) + gap cap: enter at close[i] only when the bar wicked
       to EMA9 AND closed back on the trade side AND |close[i]-ema[i]|/ema[i]<=cap.
  M11  bounce-break STOP entry: on a closed-bar confirmed retest at bar i, arm a
       stop just beyond that bar's extreme (long: buy-stop at high[i]*(1+buf)).
       Fill only if a later bar (within stop_life) trades through it — i.e. only
       after the bounce PROVES itself. Cancel if price breaks the EMA9 overshoot
       guard against us first. Worse price, far fewer knives.

Every model holds SIGNAL + EXIT identical to M0; only ENTRY differs. Scored with
full KPIs (n, net, PF, WR, maxDD) and net as % of M0, at BloFin fees and Lighter
zero-fee.

Run:
  PYTHONPATH="../analysis;../v3.1-drafts;../analysis/sweeps/2026-05-20" \
      ../venv/Scripts/python.exe entry_v2_search.py [days] [coins...]
"""
import sys
import numpy as np
import pandas as pd

from engine import fetch_ohlcv
from zec_v3_realistic import (
    generate_v3_signals, apply_entry_filter, simulate_trade,
    _check_retest, RETEST_TIMEOUT_BARS, MIN_SLOPE_PCT, RETEST_OVERSHOOT_PCT,
)
from v3_2_lab import (run_bt, ExitModel, F_LIVE, base_params, kpis as lab_kpis,
                      _simulate)

LIVE_COINS = ["BNB-USDT", "BTC-USDT", "DOGE-USDT", "HYPE-USDT",
              "SOL-USDT", "XRP-USDT", "ZEC-USDT"]


def _fee_for(entry_price, exit_price, p):
    notional_in = p.margin_usdt * p.leverage
    notional_out = (exit_price / entry_price) * notional_in
    return (notional_in + notional_out) * p.commission_pct


def _arrays(df):
    return dict(
        buy=df["buy_sig"].values, sell=df["sell_sig"].values,
        o=df["Open"].values.astype(float), h=df["High"].values.astype(float),
        l=df["Low"].values.astype(float), c=df["Close"].values.astype(float),
        ema=df["ema9"].values, slope=df["slope_pct"].values,
        adx=df["adx"].values, body=df["body_atr_ratio"].values, ts=df.index,
    )


def _gate(a, i, side, blocked_until, filters):
    """Shared post-retest gates (slope, lock, filters). Returns True if the
    entry may fire on bar i."""
    if abs(a["slope"][i]) < MIN_SLOPE_PCT:
        return None  # keep-pending sentinel
    if i <= blocked_until:
        return None
    if filters is not None:
        adx = float(a["adx"][i]) if not np.isnan(a["adx"][i]) else 0.0
        if not filters.passes(a["ts"][i], float(a["slope"][i]), float(a["body"][i]), adx):
            return False  # consume-pending sentinel
    return True


def _exit_and_record(a, side, entry_price, i_fill_forward_start, p, trades, n,
                     em=None):
    """Simulate the exit (trail by default, any ExitModel) from the given
    forward-bar start and append a trade dict. Returns duration_bars for lock."""
    start = i_fill_forward_start
    j_end = min(start + 288, n)
    bars = [(int(a["ts"][j].timestamp()), a["o"][j], a["h"][j], a["l"][j], a["c"][j])
            for j in range(start, j_end)]
    if em is None or em.kind == "trail":
        res = simulate_trade(side, entry_price, bars, p, ordering="avg")
        pnl, reason, dur = res.pnl_usdt, res.exit_reason, res.duration_bars
        exit_price = res.exit_price
    else:
        pnl, reason, dur = _simulate(side, entry_price, bars, p, em)
        pct = pnl / (p.margin_usdt * p.leverage)
        exit_price = entry_price * (1 + pct) if side == "long" else entry_price * (1 - pct)
    fee = _fee_for(entry_price, exit_price, p)
    trades.append(dict(side=side, entry_ts=a["ts"][start - 1] if start >= 1 else a["ts"][0],
                       entry_price=entry_price, pnl_usdt=pnl,
                       pnl_net=pnl - fee, exit_reason=reason, duration_bars=dur,
                       adx_at_entry=0.0, slope_pct=0.0))
    return dur


def run_m3_gap(df, p, filters, gap_cap=None, em=None, allowed_sides=("long", "short")):
    """M3 (+M12 when gap_cap set): confirm retest on closed bar i, market-enter
    at open[i+1]; if gap_cap is not None, skip when |open[i+1]-ema[i]|/ema[i]>cap."""
    a = _arrays(df); n = len(df)
    trades = []; pending = []; blocked_until = -1
    for i in range(n):
        new_pending = []
        for sig_i, side in pending:
            if i - sig_i > RETEST_TIMEOUT_BARS:
                continue
            if not (_check_retest(side, a["ema"][i], a["l"][i], a["h"][i]) and i + 1 < n):
                new_pending.append((sig_i, side)); continue
            g = _gate(a, i, side, blocked_until, filters)
            if g is None:
                new_pending.append((sig_i, side)); continue
            if g is False:
                continue
            if side not in allowed_sides:
                continue
            entry_price = a["o"][i + 1]
            if gap_cap is not None:
                gap = abs(entry_price - a["ema"][i]) / a["ema"][i]
                if gap > gap_cap:
                    continue  # edge already gone — skip (consume pending)
            dur = _exit_and_record(a, side, entry_price, i + 1, p, trades, n, em)
            blocked_until = i + max(1, dur)
            new_pending = [(s, sd) for (s, sd) in new_pending if sd != side]
        pending = new_pending
        if a["buy"][i]:
            pending.append((i, "long"))
        if a["sell"][i]:
            pending.append((i, "short"))
    return pd.DataFrame(trades)


def run_reclaim_gap(df, p, filters, gap_cap=None, em=None,
                    allowed_sides=("long", "short")):
    """M13: enter at close[i] when bar wicked to EMA9 AND closed back on the
    trade side (a confirmed bounce). Optional gap cap on |close[i]-ema[i]|/ema."""
    a = _arrays(df); n = len(df)
    trades = []; pending = []; blocked_until = -1
    for i in range(n):
        new_pending = []
        for sig_i, side in pending:
            if i - sig_i > RETEST_TIMEOUT_BARS:
                continue
            touched = _check_retest(side, a["ema"][i], a["l"][i], a["h"][i])
            reclaimed = (a["c"][i] > a["ema"][i]) if side == "long" else (a["c"][i] < a["ema"][i])
            if not (touched and reclaimed and i + 1 < n):
                new_pending.append((sig_i, side)); continue
            g = _gate(a, i, side, blocked_until, filters)
            if g is None:
                new_pending.append((sig_i, side)); continue
            if g is False:
                continue
            entry_price = a["c"][i]
            if gap_cap is not None:
                gap = abs(entry_price - a["ema"][i]) / a["ema"][i]
                if gap > gap_cap:
                    continue
            dur = _exit_and_record(a, side, entry_price, i + 1, p, trades, n)
            blocked_until = i + max(1, dur)
            new_pending = [(s, sd) for (s, sd) in new_pending if sd != side]
        pending = new_pending
        if a["buy"][i]:
            pending.append((i, "long"))
        if a["sell"][i]:
            pending.append((i, "short"))
    return pd.DataFrame(trades)


def run_bounce_break(df, p, filters, buf_pct=0.0, stop_life=3):
    """M11: on a closed-bar confirmed retest at bar i, arm a stop just beyond
    bar i's extreme (long: buy-stop at high[i]*(1+buf)). Fill at the stop level
    (+slip) only if a later bar within stop_life trades through it. Cancel if a
    bar first breaks the EMA9 overshoot guard against us (knife continuation)."""
    a = _arrays(df); n = len(df)
    over = RETEST_OVERSHOOT_PCT / 100.0
    buf = buf_pct / 100.0
    trades = []; pending = []; armed = []; blocked_until = -1
    for i in range(n):
        # 1. try to trigger armed stops
        still = []
        for trig, side, armed_at, guard, ema_ref in armed:
            if i - armed_at > stop_life:
                continue  # expired unfilled
            if i <= blocked_until:
                still.append((trig, side, armed_at, guard, ema_ref)); continue
            broke = (a["l"][i] < guard) if side == "long" else (a["h"][i] > guard)
            hit = (a["h"][i] >= trig) if side == "long" else (a["l"][i] <= trig)
            # if both happen in-bar, assume the adverse guard-break came first
            if broke and not hit:
                continue  # cancel — knife kept going
            if not hit:
                still.append((trig, side, armed_at, guard, ema_ref)); continue
            slip = trig * p.sl_slippage_pct
            entry_price = trig + slip if side == "long" else trig - slip
            dur = _exit_and_record(a, side, entry_price, i + 1, p, trades, n)
            blocked_until = i + max(1, dur)
        armed = still
        # 2. confirm retests on bar i -> arm a stop
        new_pending = []
        for sig_i, side in pending:
            if i - sig_i > RETEST_TIMEOUT_BARS:
                continue
            if not _check_retest(side, a["ema"][i], a["l"][i], a["h"][i]):
                new_pending.append((sig_i, side)); continue
            g = _gate(a, i, side, blocked_until, filters)
            if g is None:
                new_pending.append((sig_i, side)); continue
            if g is False:
                continue
            ema_i = float(a["ema"][i])
            if side == "long":
                trig = a["h"][i] * (1 + buf)
                guard = ema_i * (1 - over)
            else:
                trig = a["l"][i] * (1 - buf)
                guard = ema_i * (1 + over)
            armed.append((trig, side, i, guard, ema_i))
            new_pending = [(s, sd) for (s, sd) in new_pending if sd != side]
        pending = new_pending
        if a["buy"][i]:
            pending.append((i, "long"))
        if a["sell"][i]:
            pending.append((i, "short"))
    return pd.DataFrame(trades)


def _k(t, p, days):
    if t is None or t.empty:
        return dict(n=0, net=0.0, PF=0.0, WR=0.0, maxDD=0.0)
    k = lab_kpis(t, p, days)
    return dict(n=k["n"], net=k["net"], PF=k["PF"], WR=k["WR"], maxDD=k["maxDD"])


def run_pass(days, coins, fee, slip, label):
    p = base_params(sl=82.5, fee=fee, slip=slip)
    em = ExitModel("trail")
    gap_caps = [0.0005, 0.0008, 0.0010, 0.0012, 0.0015, 0.0020]
    print(f"\n{'='*88}\n{label}  (window={days}d, exit=trail, filters=F_LIVE, sl=82.5)\n{'='*88}")

    # accumulate trades across coins per model so KPIs are basket-level
    bins = {"M0_ideal": [], "M3_market": [],
            "M11_break_b0": [], "M11_break_b5": [],
            "M13_reclaim": []}
    for cap in gap_caps:
        bins[f"M12_gap{cap*100:.2f}"] = []
        bins[f"M13_gap{cap*100:.2f}"] = []

    for c in coins:
        df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                         days_back=days, exchange="blofin", cache=False, verbose=False)
        df = apply_entry_filter(generate_v3_signals(df.copy()))
        bins["M0_ideal"].append(run_bt(df, p, em, F_LIVE))
        bins["M3_market"].append(run_m3_gap(df, p, F_LIVE, gap_cap=None))
        bins["M11_break_b0"].append(run_bounce_break(df, p, F_LIVE, buf_pct=0.0))
        bins["M11_break_b5"].append(run_bounce_break(df, p, F_LIVE, buf_pct=0.05))
        bins["M13_reclaim"].append(run_reclaim_gap(df, p, F_LIVE, gap_cap=None))
        for cap in gap_caps:
            bins[f"M12_gap{cap*100:.2f}"].append(run_m3_gap(df, p, F_LIVE, gap_cap=cap))
            bins[f"M13_gap{cap*100:.2f}"].append(run_reclaim_gap(df, p, F_LIVE, gap_cap=cap))

    agg = {m: (pd.concat(v, ignore_index=True) if any(not x.empty for x in v)
               else pd.DataFrame()) for m, v in bins.items()}
    m0_net = _k(agg["M0_ideal"], p, days)["net"] or 1e-9

    print(f"{'model':18s} {'n':>4s} {'net$':>8s} {'%M0':>6s} {'PF':>5s} {'WR':>5s} {'maxDD':>7s}")
    order = (["M0_ideal", "M3_market"]
             + [f"M12_gap{cap*100:.2f}" for cap in gap_caps]
             + ["M13_reclaim"] + [f"M13_gap{cap*100:.2f}" for cap in gap_caps]
             + ["M11_break_b0", "M11_break_b5"])
    for m in order:
        k = _k(agg[m], p, days)
        pct = k["net"] / m0_net * 100
        star = "  <-- +EV" if (k["net"] > 0 and m != "M0_ideal") else ""
        print(f"{m:18s} {k['n']:4d} {k['net']:8.0f} {pct:5.0f}% {k['PF']:5.2f} "
              f"{k['WR']:5.2f} {k['maxDD']:7.0f}{star}")
    return agg


TIGHT_CAPS = [0.0002, 0.0003, 0.0004, 0.0005, 0.0006, 0.0008, 0.0010]


def _fetch_signalled(coins, days):
    out = {}
    for c in coins:
        df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                         days_back=days, exchange="blofin", cache=False, verbose=False)
        out[c] = apply_entry_filter(generate_v3_signals(df.copy()))
    return out


def _model(dfs, p, runner, **kw):
    """Run a model fn across all coins, return concatenated trades."""
    parts = [runner(df, p, F_LIVE, **kw) for df in dfs.values()]
    parts = [x for x in parts if not x.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def mode_validate(days, coins):
    """Zero-fee (Lighter). Find the PF ceiling as the gap tightens, then IS/OOS
    the winners. M12 = market@open[i+1]+gap; M13 = reclaim-close+gap."""
    p = base_params(sl=82.5, fee=0.0, slip=0.0)
    dfs = _fetch_signalled(coins, days)
    span = max((df.index[-1] - df.index[0]).total_seconds() / 86400 for df in dfs.values())
    print(f"\n{'='*92}\nVALIDATE — tight-gap PF ceiling (ZERO-FEE, {len(coins)} coins, "
          f"~{span:.0f}d, exit=trail)\n{'='*92}")
    print(f"{'gap%':>6s} | {'M12 n':>5s} {'net':>7s} {'PF':>5s} {'WR':>5s} {'DD':>6s} "
          f"| {'M13 n':>5s} {'net':>7s} {'PF':>5s} {'WR':>5s} {'DD':>6s}")
    for cap in TIGHT_CAPS:
        m12 = _k(_model(dfs, p, run_m3_gap, gap_cap=cap), p, span)
        m13 = _k(_model(dfs, p, run_reclaim_gap, gap_cap=cap), p, span)
        print(f"{cap*100:5.2f}% | {m12['n']:5d} {m12['net']:7.0f} {m12['PF']:5.2f} "
              f"{m12['WR']:5.2f} {m12['maxDD']:6.0f} | {m13['n']:5d} {m13['net']:7.0f} "
              f"{m13['PF']:5.2f} {m13['WR']:5.2f} {m13['maxDD']:6.0f}")

    # IS/OOS on a representative tight cap for each model (70/30 by time per coin)
    print(f"\n{'-'*92}\nIS/OOS (70/30 time split per coin, zero-fee)\n{'-'*92}")
    print(f"{'config':22s} {'split':4s} {'n':>4s} {'net':>7s} {'PF':>5s} {'WR':>5s} {'DD':>6s}")
    for lbl, runner, cap in (("M12 market gap0.05", run_m3_gap, 0.0005),
                             ("M13 reclaim gap0.04", run_reclaim_gap, 0.0004),
                             ("M13 reclaim gap0.05", run_reclaim_gap, 0.0005)):
        is_parts, oos_parts = [], []
        for df in dfs.values():
            cut = df.index[int(len(df) * 0.7)]
            is_parts.append(runner(df.loc[:cut], p, F_LIVE, gap_cap=cap))
            oos_parts.append(runner(df.loc[cut:], p, F_LIVE, gap_cap=cap))
        for split, parts in (("IS", is_parts), ("OOS", oos_parts)):
            t = pd.concat([x for x in parts if not x.empty], ignore_index=True) \
                if any(not x.empty for x in parts) else pd.DataFrame()
            k = _k(t, p, span * (0.7 if split == "IS" else 0.3))
            print(f"{lbl:22s} {split:4s} {k['n']:4d} {k['net']:7.0f} {k['PF']:5.2f} "
                  f"{k['WR']:5.2f} {k['maxDD']:6.0f}")
    print("\nread: PF should rise then plateau as gap tightens; a peak that only "
          "shows at the tiniest n is overfit. IS/OOS both +EV = the edge is real.")


def mode_lev(days, coins, cap=0.0005, runner_name="m13"):
    """Leverage sweep on the low-DD gap-filtered profile, zero-fee. Fixed-$
    SL=82.5 so the %-stop tightens as leverage rises (live-config behavior).
    margin held $250; notional = margin*lev."""
    from dataclasses import replace as _r
    runner = run_reclaim_gap if runner_name == "m13" else run_m3_gap
    dfs = _fetch_signalled(coins, days)
    span = max((df.index[-1] - df.index[0]).total_seconds() / 86400 for df in dfs.values())
    base = base_params(sl=82.5, fee=0.0, slip=0.0)
    print(f"\n{'='*92}\nLEVERAGE SWEEP — {runner_name.upper()} gap{cap*100:.2f}% "
          f"(ZERO-FEE, margin=$250, fixed-$ SL, ~{span:.0f}d)\n{'='*92}")
    print(f"{'lev':>4s} {'notional':>9s} {'SL%':>6s} {'n':>4s} {'net$':>8s} "
          f"{'PF':>5s} {'WR':>5s} {'maxDD':>7s} {'net/day':>7s}")
    for L in (10, 20, 30, 50, 75, 100):
        p = _r(base, leverage=L)
        sl_pct = p.sl_loss_usdt / (p.margin_usdt * L) * 100
        k = _k(_model(dfs, p, runner, gap_cap=cap), p, span)
        npd = k["net"] / span if span else 0
        print(f"{L:4d} {p.margin_usdt*L:9.0f} {sl_pct:5.2f}% {k['n']:4d} {k['net']:8.0f} "
              f"{k['PF']:5.2f} {k['WR']:5.2f} {k['maxDD']:7.0f} {npd:7.1f}")
    print("\nread: fixed-$ SL means higher lev = tighter %-stop = more stop-outs "
          "(WR/PF erode). The sweet spot maximizes net/day while keeping PF>1 and "
          "DD survivable. SL% nearing the liq band (~0.3-0.7%) = sim-untrustable tail.")


def mode_magnify(days, coins, cap=0.0005):
    """BAR-MAGNIFIER — ground-truth the M13 gap0.05 edge. Find entries on 5m
    (unchanged, both sides), then re-simulate the trail EXIT on true 1-MINUTE
    bars from entry forward, vs the 5m 'avg-of-orderings' approximation the
    engine uses. If net/PF survives the finer path, the edge is real; if it
    craters, it was a 5m intrabar-ordering artifact.

    Columns: 5m-avg (engine's approx) | 1m-avg | 1m-advfirst (pessimistic,
    adverse move resolves first inside each 1m bar)."""
    p = base_params(sl=82.5, fee=0.0, slip=0.0)   # Lighter zero-fee venue
    LOOKAHEAD_1M = 1440   # 288 x 5m
    print(f"\n{'='*92}\nBAR-MAGNIFIER — M13 reclaim gap{cap*100:.2f}% exit on 1m vs 5m "
          f"(ZERO-FEE, both sides, {len(coins)} coins, {days}d)\n{'='*92}", flush=True)
    print(f"{'coin':9s} {'n':>4s} | {'5m-avg net':>11s} | {'1m-avg net':>11s} "
          f"{'PF':>5s} | {'1m-adv net':>11s} {'PF':>5s}", flush=True)

    tot = {"n": 0, "net5": 0.0, "net1a": pd.DataFrame(), "net1p": pd.DataFrame()}
    rows_1a, rows_1p, net5_all = [], [], []
    for c in coins:
        df5 = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                          days_back=days, exchange="blofin", cache=False, verbose=False)
        df5 = apply_entry_filter(generate_v3_signals(df5.copy()))
        ent = run_reclaim_gap(df5, p, F_LIVE, gap_cap=cap)   # 5m-avg trail
        if ent.empty:
            print(f"{c:9s}    0 | (no entries)", flush=True); continue
        df1 = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="1m",
                          days_back=days + 2, exchange="blofin", cache=False, verbose=False)
        o1 = df1["Open"].values.astype(float); h1 = df1["High"].values.astype(float)
        l1 = df1["Low"].values.astype(float); c1 = df1["Close"].values.astype(float)
        idx1 = df1.index
        net5 = float(ent.pnl_net.sum())
        n1a = n1p = 0.0
        recs_a, recs_p = [], []
        for _, t in ent.iterrows():
            # 1m bars from the 5m bar's CLOSE (entry_ts is the 5m open; +5m = close)
            start_ts = t.entry_ts + pd.Timedelta(minutes=5)
            lo = idx1.searchsorted(start_ts, side="left")
            hi = min(lo + LOOKAHEAD_1M, len(idx1))
            if lo >= len(idx1):
                continue
            bars = [(int(idx1[j].timestamp()), o1[j], h1[j], l1[j], c1[j])
                    for j in range(lo, hi)]
            ra = simulate_trade(t.side, t.entry_price, bars, p, ordering="avg")
            rp = simulate_trade(t.side, t.entry_price, bars, p, ordering="adv_first")
            n1a += ra.pnl_usdt; n1p += rp.pnl_usdt
            recs_a.append(ra.pnl_usdt); recs_p.append(rp.pnl_usdt)
        pfa = _pf(recs_a); pfp = _pf(recs_p)
        print(f"{c:9s} {len(ent):4d} | {net5:11.0f} | {n1a:11.0f} {pfa:5.2f} | "
              f"{n1p:11.0f} {pfp:5.2f}", flush=True)
        tot["n"] += len(ent); net5_all.append(net5)
        rows_1a += recs_a; rows_1p += recs_p

    print(f"{'-'*92}", flush=True)
    pfa = _pf(rows_1a); pfp = _pf(rows_1p)
    print(f"{'TOTAL':9s} {tot['n']:4d} | {sum(net5_all):11.0f} | "
          f"{sum(rows_1a):11.0f} {pfa:5.2f} | {sum(rows_1p):11.0f} {pfp:5.2f}", flush=True)
    print("\nread: 1m-avg net close to 5m-avg = the 5m approximation was honest. "
          "1m-advfirst (pessimistic) still >0 and PF>1 = the exit edge survives "
          "true intrabar resolution -> real, deployable. Both crater = 5m artifact.",
          flush=True)


def _pf(pnls):
    if not pnls:
        return 0.0
    gw = sum(x for x in pnls if x > 0); gl = -sum(x for x in pnls if x <= 0)
    return gw / gl if gl > 0 else float("inf")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "magnify":
        d = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 45
        cs = [a for a in sys.argv[2:] if a.endswith("-USDT")] or LIVE_COINS
        cap = next((float(a) for a in sys.argv[2:] if a.replace(".", "", 1).isdigit()
                    and "." in a), 0.0005)
        mode_magnify(d, cs, cap=cap)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "validate":
        d = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 90
        cs = [a for a in sys.argv[2:] if a.endswith("-USDT")] or LIVE_COINS
        mode_validate(d, cs)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "lev":
        d = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 90
        cs = [a for a in sys.argv[2:] if a.endswith("-USDT")] or LIVE_COINS
        rn = "m12" if "m12" in sys.argv[2:] else "m13"
        cap = next((float(a) for a in sys.argv[2:] if a.replace(".", "", 1).isdigit()
                    and "." in a), 0.0005)
        mode_lev(d, cs, cap=cap, runner_name=rn)
        return
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 45
    coins = [a for a in sys.argv[1:] if a.endswith("-USDT")] or LIVE_COINS
    run_pass(days, coins, fee=0.0006, slip=0.0006,
             label="REALISTIC BloFin (fee 0.06%/side, slip 0.06%)")
    run_pass(days, coins, fee=0.0, slip=0.0,
             label="ZERO-FEE Lighter endgame (fee 0, slip 0)")
    print("\nread: a gap cap that lifts M12/M13 net ABOVE M3 (ideally +EV on BloFin) "
          "= the cost-control idea works. M11 break-entry trades fewer/cleaner; "
          "compare its PF to M3. M0 is the non-causal dream — diagnostic only.")


if __name__ == "__main__":
    main()
