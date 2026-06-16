"""V3.2 lab — extends zec_v3_realistic with:
  - alternative exit models: trail(V3.1), fixed-R all-out, partial+runner
  - per-side enable/disable (long-only / short-only / both)
  - slippage + fee grid
  - IS/OOS split, walk-forward folds
  - remove-best-N, ADX-regime + ATR-volatility buckets

Authoritative entry signal + trail machine are imported from zec_v3_realistic
so this stays parity with the live bridge model. Run on the VPS:
  .venv/bin/python strategies/v3_2_lab.py <mode>
modes: matrix | exits | sides | slip | isoos | walkforward | regime | best
"""
import sys
from dataclasses import dataclass, replace, asdict
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from zec_v3_realistic import (
    TrailParams, EntryFilters, generate_v3_signals, apply_entry_filter,
    load_and_signal as _load_and_signal_raw, dollars_to_price_distance,
    pnl_at_price, simulate_trade,
    _check_retest, RETEST_TIMEOUT_BARS, MIN_SLOPE_PCT, EMA_PERIOD,
)

_CACHE = Path(__file__).resolve().parent.parent / "data" / "_zec_signalled.pkl"


def load_and_signal(slice_dates=None):
    """Cached: signal-gen over 52k bars is ~minutes; cache the full signalled
    frame and slice in-memory so a multi-variant session pays it once."""
    if slice_dates is None and _CACHE.exists():
        return pd.read_pickle(_CACHE)
    if slice_dates is None:
        df = _load_and_signal_raw()
        df.to_pickle(_CACHE)
        return df
    return _load_and_signal_raw(slice_dates)

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 60)


# --------------------------------------------------------------------------
# Alternative exit simulators (same intrabar avg-ordering as the base engine)
# --------------------------------------------------------------------------
@dataclass
class ExitModel:
    kind: str = "trail"          # trail | fixedR | partial
    r_mult: float = 2.0          # target in R for fixedR
    use_be: bool = False         # fixedR: move stop to entry after be_r * R favorable
    be_r: float = 1.0
    partial_frac: float = 0.5    # partial: fraction booked at r1
    r1: float = 1.0              # partial: first target in R
    runner_trail_r: float = 1.0  # partial: runner trail distance in R after r1


def _sim_fixedR(side, entry, bars, p: TrailParams, em: ExitModel):
    """All-out at r_mult*R or stop at -1R. Optional BE move. avg of orderings."""
    sl_d = dollars_to_price_distance(p.sl_loss_usdt, p, entry)
    tp_d = dollars_to_price_distance(p.sl_loss_usdt * em.r_mult, p, entry)
    be_d = dollars_to_price_distance(p.sl_loss_usdt * em.be_r, p, entry)
    slip = entry * p.sl_slippage_pct

    def one(order):
        sl = entry - sl_d if side == "long" else entry + sl_d
        moved_be = False
        for i, bar in enumerate(bars):
            _, o, h, l, c = bar[:5]
            adv = l if side == "long" else h
            fav = h if side == "long" else l
            fav_first = (order == "fav_first") if order in ("fav_first", "adv_first") else (
                (c < o) if side == "long" else (c >= o))

            def hit_tp():
                if side == "long":
                    return h >= entry + tp_d
                return l <= entry - tp_d

            def hit_sl(slv):
                if side == "long":
                    return l <= slv
                return h >= slv

            def maybe_be(slv, mb):
                if em.use_be and not mb:
                    reach = (h >= entry + be_d) if side == "long" else (l <= entry - be_d)
                    if reach:
                        return (entry if side == "long" else entry), True
                return slv, mb

            if fav_first:
                if hit_tp():
                    ep = entry + tp_d if side == "long" else entry - tp_d
                    return pnl_at_price(side, entry, ep, p), "tp_R", i + 1
                sl, moved_be = maybe_be(sl, moved_be)
                if hit_sl(sl):
                    ep = sl - slip if side == "long" else sl + slip
                    return pnl_at_price(side, entry, ep, p), ("be" if moved_be else "sl"), i + 1
            else:
                sl, moved_be = maybe_be(sl, moved_be)
                if hit_sl(sl):
                    ep = sl - slip if side == "long" else sl + slip
                    return pnl_at_price(side, entry, ep, p), ("be" if moved_be else "sl"), i + 1
                if hit_tp():
                    ep = entry + tp_d if side == "long" else entry - tp_d
                    return pnl_at_price(side, entry, ep, p), "tp_R", i + 1
        return pnl_at_price(side, entry, bars[-1][4], p), "unresolved", len(bars)

    a = one("fav_first"); b = one("adv_first")
    pnl = (a[0] + b[0]) / 2
    worse = a if a[0] <= b[0] else b
    return pnl, worse[1], worse[2]


def _sim_partial(side, entry, bars, p: TrailParams, em: ExitModel):
    """Book partial_frac at r1*R, move stop to BE, trail runner at runner_trail_r*R.
    avg of orderings."""
    sl_d = dollars_to_price_distance(p.sl_loss_usdt, p, entry)
    r1_d = dollars_to_price_distance(p.sl_loss_usdt * em.r1, p, entry)
    trail_d = dollars_to_price_distance(p.sl_loss_usdt * em.runner_trail_r, p, entry)
    slip = entry * p.sl_slippage_pct
    f = em.partial_frac

    def one(order):
        sl = entry - sl_d if side == "long" else entry + sl_d
        booked = 0.0
        took_partial = False
        peak = entry
        for i, bar in enumerate(bars):
            _, o, h, l, c = bar[:5]
            adv = l if side == "long" else h
            fav = h if side == "long" else l
            fav_first = (order == "fav_first") if order in ("fav_first", "adv_first") else (
                (c < o) if side == "long" else (c >= o))

            def hit_r1():
                return (h >= entry + r1_d) if side == "long" else (l <= entry - r1_d)

            def hit_sl(slv):
                return (l <= slv) if side == "long" else (h >= slv)

            def do_partial():
                nonlocal booked, took_partial, sl
                tp_price = entry + r1_d if side == "long" else entry - r1_d
                booked += pnl_at_price(side, entry, tp_price, p) * f
                took_partial = True
                sl = entry  # runner to breakeven

            def update_trail():
                nonlocal peak, sl
                if side == "long":
                    if fav > peak:
                        peak = fav
                        sl = max(sl, peak - trail_d)
                else:
                    if fav < peak:
                        peak = fav
                        sl = min(sl, peak + trail_d)

            if fav_first:
                if not took_partial and hit_r1():
                    do_partial()
                if took_partial:
                    update_trail()
                if hit_sl(sl):
                    ep = sl - slip if side == "long" else sl + slip
                    runner = pnl_at_price(side, entry, ep, p) * (1 - f) if took_partial else \
                        pnl_at_price(side, entry, ep, p)
                    return booked + runner, ("runner_tsl" if took_partial else "sl"), i + 1
            else:
                if hit_sl(sl):
                    ep = sl - slip if side == "long" else sl + slip
                    runner = pnl_at_price(side, entry, ep, p) * (1 - f) if took_partial else \
                        pnl_at_price(side, entry, ep, p)
                    return booked + runner, ("runner_tsl" if took_partial else "sl"), i + 1
                if not took_partial and hit_r1():
                    do_partial()
                if took_partial:
                    update_trail()
        last = bars[-1][4]
        runner = pnl_at_price(side, entry, last, p) * (1 - f) if took_partial else \
            pnl_at_price(side, entry, last, p)
        return booked + runner, ("runner_open" if took_partial else "unresolved"), len(bars)

    a = one("fav_first"); b = one("adv_first")
    pnl = (a[0] + b[0]) / 2
    worse = a if a[0] <= b[0] else b
    return pnl, worse[1], worse[2]


def _simulate(side, entry, bars, p, em: ExitModel):
    if not bars:
        return 0.0, "unresolved", 0
    if em.kind == "trail":
        r = simulate_trade(side, entry, bars, p, ordering="avg")
        return r.pnl_usdt, r.exit_reason, r.duration_bars
    if em.kind == "fixedR":
        return _sim_fixedR(side, entry, bars, p, em)
    if em.kind == "partial":
        return _sim_partial(side, entry, bars, p, em)
    raise ValueError(em.kind)


# --------------------------------------------------------------------------
# Backtest loop (parity with run_v3_backtest, + allowed_sides + exit model)
# --------------------------------------------------------------------------
def run_bt(df, p: TrailParams, em: ExitModel | None = None,
           filters: EntryFilters | None = None,
           allowed_sides=("long", "short"), max_lookahead_bars=288):
    em = em or ExitModel()
    buy_sig = df["buy_sig"].values; sell_sig = df["sell_sig"].values
    closes = df["Close"].values.astype(float); opens = df["Open"].values.astype(float)
    highs = df["High"].values.astype(float); lows = df["Low"].values.astype(float)
    ts = df.index; adx = df["adx"].values; body_a = df["body_atr_ratio"].values
    slope = df["slope_pct"].values; ema = df["ema9"].values
    n = len(df)
    trades = []
    pending = []
    blocked_until = -1
    for i in range(n):
        new_pending = []
        for sig_i, side in pending:
            if i - sig_i > RETEST_TIMEOUT_BARS:
                continue
            if not _check_retest(side, ema[i], lows[i], highs[i]):
                new_pending.append((sig_i, side)); continue
            if abs(slope[i]) < MIN_SLOPE_PCT:
                new_pending.append((sig_i, side)); continue
            if i <= blocked_until:
                new_pending.append((sig_i, side)); continue
            if filters is not None:
                _adx = float(adx[i]) if not np.isnan(adx[i]) else 0.0
                if not filters.passes(ts[i], float(slope[i]), float(body_a[i]), _adx):
                    continue
            if side not in allowed_sides:
                continue
            entry_price = float(ema[i])
            j_end = min(i + 1 + max_lookahead_bars, n)
            bars = [(int(ts[j].timestamp()), opens[j], highs[j], lows[j], closes[j])
                    for j in range(i + 1, j_end)]
            pnl, reason, dur = _simulate(side, entry_price, bars, p, em)
            notional_in = p.margin_usdt * p.leverage
            # exit price approx for fee: recover from pnl
            pct = pnl / notional_in
            exit_price = entry_price * (1 + pct) if side == "long" else entry_price * (1 - pct)
            notional_out = (exit_price / entry_price) * notional_in
            fee = (notional_in + notional_out) * p.commission_pct
            trades.append(dict(
                idx=int(i), side=side, entry_ts=ts[i], entry_price=entry_price,
                exit_reason=reason, pnl_usdt=pnl, pnl_net=pnl - fee,
                duration_bars=dur, hour_utc=ts[i].hour, weekday=ts[i].weekday(),
                adx_at_entry=float(adx[i]) if not np.isnan(adx[i]) else 0.0,
                body_atr_ratio=float(body_a[i]), slope_pct=float(slope[i]),
            ))
            blocked_until = i + max(1, dur)
            new_pending = [(s, sd) for (s, sd) in new_pending if sd != side]
        pending = new_pending
        if buy_sig[i]:
            pending.append((i, "long"))
        if sell_sig[i]:
            pending.append((i, "short"))
    return pd.DataFrame(trades)


def R_of(tdf, p):
    risk = p.sl_loss_usdt
    return tdf["pnl_net"] / risk if risk else tdf["pnl_net"] * 0


def kpis(tdf, p=None, days=None):
    if tdf is None or tdf.empty:
        return {"n": 0}
    w = tdf[tdf.pnl_net > 0]; l = tdf[tdf.pnl_net <= 0]
    net = tdf.pnl_net.sum(); gw = w.pnl_net.sum(); gl = -l.pnl_net.sum()
    pf = gw / gl if gl > 0 else float("inf")
    cum = tdf.sort_values("entry_ts").pnl_net.cumsum().values
    dd = float((cum - np.maximum.accumulate(cum)).min()) if len(cum) else 0.0
    mc = c = 0
    for x in tdf.sort_values("entry_ts").pnl_net.values:
        if x <= 0: c += 1; mc = max(mc, c)
        else: c = 0
    out = {
        "n": int(len(tdf)), "WR": round(len(w) / len(tdf), 3),
        "net": round(net, 1), "PF": round(pf, 2),
        "avg_win": round(w.pnl_net.mean(), 1) if len(w) else 0,
        "avg_loss": round(l.pnl_net.mean(), 1) if len(l) else 0,
        "avg_trade": round(net / len(tdf), 2),
        "maxDD": round(dd, 1), "maxConsecL": mc,
        "long_n": int((tdf.side == "long").sum()),
        "short_n": int((tdf.side == "short").sum()),
    }
    if p is not None:
        out["avg_R"] = round(float(R_of(tdf, p).mean()), 3)
    if days:
        out["net_per_day"] = round(net / days, 2)
        out["calmar"] = round((net / days * 365) / abs(dd), 2) if dd else None
    return out


def span_days(df):
    return (df.index[-1] - df.index[0]).total_seconds() / 86400


def pr(label, k):
    print(f"  {label:42s} " + "  ".join(f"{kk}={vv}" for kk, vv in k.items()))


# --------------------------------------------------------------------------
# Recipes
# --------------------------------------------------------------------------
F_LIVE = EntryFilters(block_weekdays={6}, min_abs_slope_pct=0.15, block_body_band=(0.3, 0.5))
F_TREND = EntryFilters(block_weekdays={6}, min_abs_slope_pct=0.15, block_body_band=(0.3, 0.5), min_adx=22.0)
F_NOFILT = EntryFilters()


def base_params(sl=82.5, fee=0.0006, slip=0.0006):
    return replace(TrailParams(), sl_loss_usdt=sl, commission_pct=fee, sl_slippage_pct=slip)


def mode_matrix():
    """The 9 required comparisons at ZEC live SL=82.5, realistic fee+slip."""
    df = load_and_signal()
    days = span_days(df)
    p = base_params()
    print(f"ZEC 5m, {len(df)} bars, {days:.0f} days, fee=0.06%/side slip=0.06%\n")
    print("=== CORE COMPARISONS (net is $ over full ~%.0f-day sample) ===" % days)
    # 1 baseline V3.1 (trail, live filters, both sides)
    pr("1 V3.1 baseline (trail/Flive/both)", kpis(run_bt(df, p, ExitModel("trail"), F_LIVE), p, days))
    # 2 current entries, improved exits
    pr("2 entries=Flive, exit=2R allout", kpis(run_bt(df, p, ExitModel("fixedR", r_mult=2.0), F_LIVE), p, days))
    pr("2b entries=Flive, exit=partial0.5@1R+runner", kpis(run_bt(df, p, ExitModel("partial", r1=1.0, partial_frac=0.5, runner_trail_r=1.0), F_LIVE), p, days))
    # 3 improved entries, current exits
    pr("3 entries=Ftrend(adx>=22), exit=trail", kpis(run_bt(df, p, ExitModel("trail"), F_TREND), p, days))
    pr("3b shorts-only, exit=trail, Flive", kpis(run_bt(df, p, ExitModel("trail"), F_LIVE, allowed_sides=("short",)), p, days))
    # 4 improved both
    pr("4 Ftrend + shorts-only + trail", kpis(run_bt(df, p, ExitModel("trail"), F_TREND, allowed_sides=("short",)), p, days))
    pr("4b Ftrend + shorts + 2R allout", kpis(run_bt(df, p, ExitModel("fixedR", r_mult=2.0), F_TREND, allowed_sides=("short",)), p, days))
    # 5 aggressive V3.2
    pr("5 AGGR: Ftrend both + partial0.5@1.5R+runner", kpis(run_bt(df, p, ExitModel("partial", r1=1.5, partial_frac=0.5, runner_trail_r=1.5), F_TREND), p, days))


def mode_exits():
    df = load_and_signal(); days = span_days(df); p = base_params()
    print("=== EXIT MODEL SWEEP (Flive filters, both sides, ZEC SL=82.5) ===")
    pr("trail (V3.1)", kpis(run_bt(df, p, ExitModel("trail"), F_LIVE), p, days))
    for r in (1.5, 2.0, 2.5, 3.0):
        pr(f"fixedR {r}R allout", kpis(run_bt(df, p, ExitModel("fixedR", r_mult=r), F_LIVE), p, days))
    for r in (1.5, 2.0, 2.5, 3.0):
        pr(f"fixedR {r}R +BE@1R", kpis(run_bt(df, p, ExitModel("fixedR", r_mult=r, use_be=True, be_r=1.0), F_LIVE), p, days))
    for r1 in (1.0, 1.5):
        for frac in (0.4, 0.5, 0.6):
            pr(f"partial {frac}@{r1}R +runner1R", kpis(run_bt(df, p, ExitModel("partial", r1=r1, partial_frac=frac, runner_trail_r=1.0), F_LIVE), p, days))


def mode_sides():
    df = load_and_signal(); days = span_days(df); p = base_params()
    print("=== LONG vs SHORT (trail exit) ===")
    for f, fn in ((F_LIVE, "Flive"), (F_TREND, "Ftrend")):
        for sides in (("long", "short"), ("long",), ("short",)):
            pr(f"{fn} {sides}", kpis(run_bt(df, p, ExitModel("trail"), f, allowed_sides=sides), p, days))


def mode_slip():
    df = load_and_signal(); days = span_days(df)
    print("=== SLIPPAGE / FEE GRID (V3.1 trail, Flive, both, SL=82.5) ===")
    for fee, slip, lbl in ((0.0, 0.0, "demo 0fee/0slip"),
                            (0.0, 0.0002, "0fee/0.02%slip"),
                            (0.0, 0.0005, "0fee/0.05%slip"),
                            (0.0, 0.0010, "0fee/0.10%slip"),
                            (0.0006, 0.0006, "blofin 0.06%/0.06%")):
        p = base_params(fee=fee, slip=slip)
        pr(lbl, kpis(run_bt(df, p, ExitModel("trail"), F_LIVE), p, days))
    print("\n=== same grid, BEST variant (shorts-only Ftrend trail) ===")
    for fee, slip, lbl in ((0.0, 0.0, "demo 0fee/0slip"),
                            (0.0, 0.0005, "0fee/0.05%slip"),
                            (0.0, 0.0010, "0fee/0.10%slip"),
                            (0.0006, 0.0006, "blofin 0.06%/0.06%")):
        p = base_params(fee=fee, slip=slip)
        pr(lbl, kpis(run_bt(df, p, ExitModel("trail"), F_TREND, allowed_sides=("short",)), p, days))


def _variants(df, p, days):
    return {
        "V3.1 trail Flive both": run_bt(df, p, ExitModel("trail"), F_LIVE),
        "shorts-only Flive trail": run_bt(df, p, ExitModel("trail"), F_LIVE, allowed_sides=("short",)),
        "Ftrend both trail": run_bt(df, p, ExitModel("trail"), F_TREND),
        "Ftrend shorts trail": run_bt(df, p, ExitModel("trail"), F_TREND, allowed_sides=("short",)),
        "Ftrend shorts 2R": run_bt(df, p, ExitModel("fixedR", r_mult=2.0), F_TREND, allowed_sides=("short",)),
    }


def mode_isoos():
    df = load_and_signal(); p = base_params()
    cut = df.index[int(len(df) * 0.7)]
    IS = df.loc[:cut]; OOS = df.loc[cut:]
    print(f"IS: {IS.index[0].date()}->{IS.index[-1].date()} ({len(IS)} bars)  "
          f"OOS: {OOS.index[0].date()}->{OOS.index[-1].date()} ({len(OOS)} bars)\n")
    vis = _variants(IS, p, 0); vos = _variants(OOS, p, 0)
    for name in vis:
        print(f"  {name}")
        pr("    IS ", kpis(vis[name], p, span_days(IS)))
        pr("    OOS", kpis(vos[name], p, span_days(OOS)))


def mode_walkforward():
    df = load_and_signal(); p = base_params()
    folds = 5
    idx = np.array_split(np.arange(len(df)), folds)
    print(f"=== WALK-FORWARD {folds} folds (shorts-only Ftrend trail) ===")
    for fi, ix in enumerate(idx):
        sub = df.iloc[ix[0]:ix[-1] + 1]
        t = run_bt(sub, p, ExitModel("trail"), F_TREND, allowed_sides=("short",))
        pr(f"fold{fi} {sub.index[0].date()}->{sub.index[-1].date()}", kpis(t, p, span_days(sub)))
    print(f"\n=== WALK-FORWARD {folds} folds (V3.1 baseline both) ===")
    for fi, ix in enumerate(idx):
        sub = df.iloc[ix[0]:ix[-1] + 1]
        t = run_bt(sub, p, ExitModel("trail"), F_LIVE)
        pr(f"fold{fi} {sub.index[0].date()}->{sub.index[-1].date()}", kpis(t, p, span_days(sub)))


def mode_regime():
    df = load_and_signal(); days = span_days(df); p = base_params()
    for name, t in _variants(df, p, days).items():
        if t.empty:
            continue
        t = t.copy()
        t["regime"] = pd.cut(t.adx_at_entry, [-1, 18, 25, 200], labels=["range<18", "mid18-25", "trend>25"])
        print(f"\n=== {name} by ADX regime ===")
        g = t.groupby("regime", observed=True).agg(n=("pnl_net", "size"),
            net=("pnl_net", "sum"), wins=("pnl_net", lambda s: (s > 0).sum())).round(1)
        g["WR"] = (g.wins / g.n).round(2)
        print(g.to_string())


def mode_best():
    """Rank the candidate set + remove-best-3 robustness."""
    df = load_and_signal(); days = span_days(df); p = base_params()
    print("=== CANDIDATE RANKING (full sample, blofin fee+slip) ===")
    rows = []
    for name, t in _variants(df, p, days).items():
        k = kpis(t, p, days)
        # remove best 3
        if len(t) > 3:
            k3 = kpis(t.drop(t.nlargest(3, "pnl_net").index), p, days)
            k["net_ex_top3"] = k3["net"]; k["PF_ex_top3"] = k3["PF"]
        rows.append({"variant": name, **k})
    out = pd.DataFrame(rows).sort_values("net", ascending=False)
    cols = ["variant", "n", "WR", "net", "PF", "avg_R", "maxDD", "net_per_day",
            "calmar", "net_ex_top3", "PF_ex_top3", "long_n", "short_n"]
    print(out[[c for c in cols if c in out.columns]].to_string(index=False))


def mode_all():
    for name, fn in (("MATRIX", mode_matrix), ("EXITS", mode_exits),
                     ("SIDES", mode_sides), ("SLIP", mode_slip),
                     ("BEST", mode_best), ("REGIME", mode_regime),
                     ("ISOOS", mode_isoos), ("WALKFORWARD", mode_walkforward)):
        print("\n" + "#" * 72 + f"\n# {name}\n" + "#" * 72)
        fn()
        sys.stdout.flush()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "matrix"
    {"matrix": mode_matrix, "exits": mode_exits, "sides": mode_sides,
     "slip": mode_slip, "isoos": mode_isoos, "walkforward": mode_walkforward,
     "regime": mode_regime, "best": mode_best, "all": mode_all}.get(mode, mode_matrix)()
