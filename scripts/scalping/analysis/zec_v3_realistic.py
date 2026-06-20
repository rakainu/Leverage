"""ZEC V3 realistic backtest — V3 Pine signals + bridge EMA-retest+slope gate
+ bridge trail state machine. Self-contained.

Usage:
  .venv/bin/python strategies/zec_v3_realistic.py             # baseline + KPIs
  .venv/bin/python strategies/zec_v3_realistic.py calibrate   # vs V3-era live
  .venv/bin/python strategies/zec_v3_realistic.py sl-sweep    # sweep init SL
  .venv/bin/python strategies/zec_v3_realistic.py filter-analysis [sl]
  .venv/bin/python strategies/zec_v3_realistic.py sl-sweep-filtered  # SL on best buckets
"""
import sys
import os
from dataclasses import dataclass, asdict, replace, field
from pathlib import Path
from statistics import median
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
import pandas as pd
from engine import calc_ema, calc_atr, calc_smma
try:                                      # only used by __main__ TV-export modes
    from engine import load_tv_export
except ImportError:                       # lost in repo reconcile; not needed for
    load_tv_export = None                 # the Binance-fed coin-expansion path

DATA_FILE = "BINANCE_ZECUSDT, 5.csv"


@dataclass
class TrailParams:
    margin_usdt: float = 250.0
    leverage: float = 30.0
    sl_loss_usdt: float = 32.50
    breakeven_usdt: float = 30.00
    lock_profit_activate_usdt: float = 45.00
    lock_profit_usdt: float = 37.50
    trail_activate_usdt: float = 75.00
    trail_start_usdt: float = 80.00
    trail_distance_usdt: float = 37.50
    tp_ceiling_pct: float = 2.0
    sl_slippage_pct: float = 0.0006
    commission_pct: float = 0.0006


SENS, NOISE, FAKEOUT, RANGE_F = 8, 0.0, 0.2, 0.2
EMA_PERIOD = 9
RETEST_OVERSHOOT_PCT = 0.2
SLOPE_LOOKBACK_BARS = 3
MIN_SLOPE_PCT = 0.03
RETEST_TIMEOUT_BARS = 6
# Live bridge does NOT block any hours of day — earlier version of this script
# mistakenly imported the SMC trading system's 11-18 UTC block. Keeping the
# variable so the mechanism is in place if we ever want to test an hours block
# as a filter, but defaulting to empty matches live behavior.
BLOCK_HOURS_UTC: set[int] = set()


def generate_v3_signals(df: pd.DataFrame, sensitivity: int = SENS, noise: float = NOISE,
                        fakeout: float = FAKEOUT, range_filt: float = RANGE_F) -> pd.DataFrame:
    df = df.copy()
    closes = df["Close"].values.astype(float)
    opens = df["Open"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    n = len(df)

    ha_close = np.zeros(n)
    ha_open = np.zeros(n)
    for i in range(n):
        ha_close[i] = (opens[i] + highs[i] + lows[i] + closes[i]) / 4.0
        if i == 0:
            ha_open[i] = (opens[i] + closes[i]) / 2.0
        else:
            ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2.0

    smooth_len = 16 - sensitivity
    if smooth_len <= 1:
        smoothed = ha_close.copy()
    else:
        smoothed = calc_ema(pd.Series(ha_close, index=df.index), smooth_len).values

    ha_bull = np.zeros(n, dtype=bool)
    ha_bear = np.zeros(n, dtype=bool)
    for i in range(1, n):
        ha_bull[i] = smoothed[i] > smoothed[i - 1]
        ha_bear[i] = smoothed[i] < smoothed[i - 1]

    atr14 = calc_atr(df, 14).values
    fakeout_pass = np.ones(n, dtype=bool)
    if fakeout > 0:
        body = np.abs(closes - opens)
        valid = ~np.isnan(atr14)
        fakeout_pass[valid] = body[valid] > fakeout * atr14[valid]

    dm_p = np.zeros(n)
    dm_m = np.zeros(n)
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        dm_p[i] = up if (up > dn and up > 0) else 0
        dm_m[i] = dn if (dn > up and dn > 0) else 0
    sdm_p = calc_smma(pd.Series(dm_p, index=df.index), 14).values
    sdm_m = calc_smma(pd.Series(dm_m, index=df.index), 14).values
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    str_ = calc_smma(pd.Series(tr, index=df.index), 14).values
    dx = np.zeros(n)
    for i in range(n):
        if str_[i] != 0 and not np.isnan(str_[i]):
            dip = sdm_p[i] / str_[i] * 100
            dim = sdm_m[i] / str_[i] * 100
            if dip + dim != 0:
                dx[i] = abs(dip - dim) / (dip + dim) * 100
    adx = calc_smma(pd.Series(dx, index=df.index), 14).values
    range_pass = np.ones(n, dtype=bool)
    if range_filt > 0:
        threshold = 20.0 * range_filt
        valid = ~np.isnan(adx)
        range_pass[valid] = adx[valid] > threshold

    buy_sig = np.zeros(n, dtype=bool)
    sell_sig = np.zeros(n, dtype=bool)
    for i in range(1, n):
        buy_sig[i] = ha_bull[i] and not ha_bull[i - 1] and fakeout_pass[i] and range_pass[i]
        sell_sig[i] = ha_bear[i] and not ha_bear[i - 1] and fakeout_pass[i] and range_pass[i]

    df["buy_sig"] = buy_sig
    df["sell_sig"] = sell_sig
    df["adx"] = adx
    df["body_atr_ratio"] = np.where(atr14 > 0, np.abs(closes - opens) / atr14, 0)
    return df


def _compute_ema_and_slope(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    n = len(df)
    ema = calc_ema(df["Close"], EMA_PERIOD).values
    slope_pct = np.zeros(n)
    for i in range(SLOPE_LOOKBACK_BARS, n):
        prev = ema[i - SLOPE_LOOKBACK_BARS]
        if prev and not np.isnan(prev) and not np.isnan(ema[i]):
            slope_pct[i] = (ema[i] - prev) / prev * 100.0
    df["ema9"] = ema
    df["slope_pct"] = slope_pct
    return df


def apply_entry_filter(df: pd.DataFrame) -> pd.DataFrame:
    """Pre-compute ema9 + slope_pct cols. Pending-signal pipeline runs in
    run_v3_backtest so position lock can interact with pending queue (matches
    live bridge behavior)."""
    df = _compute_ema_and_slope(df)
    n = len(df)
    df["long_entry"] = np.zeros(n, dtype=bool)
    df["short_entry"] = np.zeros(n, dtype=bool)
    return df


def _check_retest(side: str, ema_val: float, bar_low: float, bar_high: float) -> bool:
    if np.isnan(ema_val):
        return False
    overshoot = ema_val * (RETEST_OVERSHOOT_PCT / 100.0)
    if side == "long":
        return bar_low <= ema_val and bar_low >= ema_val - overshoot
    return bar_high >= ema_val and bar_high <= ema_val + overshoot


@dataclass
class EntryFilters:
    """Per-bar filters applied at entry time. Falsy => disabled."""
    block_weekdays: set | None = None
    block_hours_utc: set | None = None
    min_abs_slope_pct: float = 0.0
    min_body_atr_ratio: float = 0.0
    max_body_atr_ratio: float | None = None
    block_body_band: tuple[float, float] | None = None
    min_adx: float = 0.0
    max_adx: float | None = None
    block_adx_band: tuple[float, float] | None = None

    def passes(self, ts, slope_pct: float, body_atr: float, adx: float) -> bool:
        if self.block_weekdays and ts.weekday() in self.block_weekdays: return False
        if self.block_hours_utc and ts.hour in self.block_hours_utc:    return False
        if self.min_abs_slope_pct and abs(slope_pct) < self.min_abs_slope_pct: return False
        if body_atr < self.min_body_atr_ratio: return False
        if self.max_body_atr_ratio is not None and body_atr > self.max_body_atr_ratio: return False
        if self.block_body_band and self.block_body_band[0] <= body_atr < self.block_body_band[1]: return False
        if adx < self.min_adx: return False
        if self.max_adx is not None and adx > self.max_adx: return False
        if self.block_adx_band and self.block_adx_band[0] <= adx < self.block_adx_band[1]: return False
        return True


def dollars_to_price_distance(usd: float, p: TrailParams, ref_price: float) -> float:
    notional = p.margin_usdt * p.leverage
    return (usd / notional) * ref_price


def pnl_at_price(side: str, entry: float, price: float, p: TrailParams) -> float:
    notional = p.margin_usdt * p.leverage
    pct = (price - entry) / entry if side == "long" else (entry - price) / entry
    return pct * notional


@dataclass
class SimResult:
    pnl_usdt: float = 0.0
    exit_reason: str = ""
    exit_price: float = 0.0
    final_state: int = 0
    duration_bars: int = 0
    max_state: int = 0


def simulate_trade(side: str, entry_price: float, bars: list, p: TrailParams,
                   ordering: str = "avg") -> SimResult:
    if not bars:
        return SimResult(exit_reason="unresolved", exit_price=entry_price)

    def _one(order: str) -> SimResult:
        state = 0
        sl_dist0 = dollars_to_price_distance(p.sl_loss_usdt, p, entry_price)
        sl = entry_price - sl_dist0 if side == "long" else entry_price + sl_dist0
        trail_high = entry_price
        max_state = 0

        def advance(fav, sl_in, st_in, th_in):
            sl_, st_, th_ = sl_in, st_in, th_in
            peak = pnl_at_price(side, entry_price, fav, p)
            if st_ == 0 and peak >= p.breakeven_usdt:
                sl_ = entry_price
                st_ = 1
            if st_ == 1 and peak >= p.lock_profit_activate_usdt:
                ld = dollars_to_price_distance(p.lock_profit_usdt, p, entry_price)
                sl_ = entry_price + ld if side == "long" else entry_price - ld
                st_ = 2
            if st_ == 2 and peak >= p.trail_activate_usdt:
                jl = p.trail_start_usdt - p.trail_distance_usdt
                jd = dollars_to_price_distance(jl, p, entry_price)
                sl_ = entry_price + jd if side == "long" else entry_price - jd
                st_ = 3
                th_ = fav
            if st_ == 3 and peak >= p.trail_start_usdt:
                st_ = 4
                th_ = fav
            if st_ == 4:
                better = (side == "long" and fav > th_) or (side == "short" and fav < th_)
                if better:
                    th_ = fav
                td = dollars_to_price_distance(p.trail_distance_usdt, p, th_)
                new_sl = th_ - td if side == "long" else th_ + td
                sl_ = max(sl_, new_sl) if side == "long" else min(sl_, new_sl)
            return sl_, st_, th_

        def check_sl(price, sl_now, st_now):
            slip = entry_price * p.sl_slippage_pct
            hit = (side == "long" and price <= sl_now) or (side == "short" and price >= sl_now)
            if not hit:
                return None
            exit_p = sl_now - slip if side == "long" else sl_now + slip
            reason = "trail_sl" if st_now >= 2 else ("sl_be" if st_now == 1 else "sl")
            return SimResult(pnl_usdt=pnl_at_price(side, entry_price, exit_p, p),
                             exit_reason=reason, exit_price=exit_p,
                             final_state=st_now, max_state=st_now)

        for i, bar in enumerate(bars):
            _, b_o, b_h, b_l, b_c = bar[:5]
            adv = b_l if side == "long" else b_h
            fav = b_h if side == "long" else b_l
            peak = pnl_at_price(side, entry_price, fav, p)
            if peak >= p.margin_usdt * p.tp_ceiling_pct:
                cd = dollars_to_price_distance(p.margin_usdt * p.tp_ceiling_pct, p, entry_price)
                cp = entry_price + cd if side == "long" else entry_price - cd
                return SimResult(pnl_usdt=pnl_at_price(side, entry_price, cp, p),
                                 exit_reason="tp_ceiling", exit_price=cp,
                                 final_state=state, duration_bars=i + 1,
                                 max_state=max(max_state, state))
            if order == "fav_first":
                fav_first = True
            elif order == "adv_first":
                fav_first = False
            else:
                bullish = b_c >= b_o
                fav_first = (not bullish) if side == "long" else bullish

            if fav_first:
                sl, state, trail_high = advance(fav, sl, state, trail_high)
                max_state = max(max_state, state)
                r = check_sl(adv, sl, state)
                if r:
                    r.duration_bars = i + 1
                    r.max_state = max(max_state, state)
                    return r
            else:
                r = check_sl(adv, sl, state)
                if r:
                    r.duration_bars = i + 1
                    r.max_state = max(max_state, state)
                    return r
                sl, state, trail_high = advance(fav, sl, state, trail_high)
                max_state = max(max_state, state)

        last_c = bars[-1][4]
        return SimResult(pnl_usdt=pnl_at_price(side, entry_price, last_c, p),
                         exit_reason="unresolved", exit_price=last_c,
                         final_state=state, duration_bars=len(bars),
                         max_state=max_state)

    if ordering == "avg":
        a = _one("fav_first")
        b = _one("adv_first")
        worse = a if a.pnl_usdt <= b.pnl_usdt else b
        avg_pnl = (a.pnl_usdt + b.pnl_usdt) / 2
        return SimResult(pnl_usdt=avg_pnl, exit_reason=worse.exit_reason,
                         exit_price=worse.exit_price, final_state=worse.final_state,
                         duration_bars=worse.duration_bars, max_state=worse.max_state)
    return _one(ordering)


@dataclass
class Trade:
    idx: int = 0
    side: str = ""
    entry_ts: Any = None
    entry_price: float = 0.0
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_usdt: float = 0.0
    pnl_net: float = 0.0
    duration_bars: int = 0
    max_state: int = 0
    hour_utc: int = 0
    weekday: int = 0
    adx_at_entry: float = 0.0
    body_atr_ratio: float = 0.0
    slope_pct: float = 0.0
    ema9: float = 0.0


def run_v3_backtest(df: pd.DataFrame, p: TrailParams,
                    max_lookahead_bars: int = 288,
                    filters: EntryFilters | None = None) -> tuple[list[Trade], pd.DataFrame]:
    """Mirror live bridge: bar-walking loop with pending queue + position lock.

    Pending signals stay alive across blocked bars (lock) and only expire
    after RETEST_TIMEOUT_BARS or fire on confirm-without-lock. This matches
    live's `_process_pending_signals` where handle_entry rejects during open
    positions but the pending is kept in the queue until expiry."""
    buy_sig = df["buy_sig"].values
    sell_sig = df["sell_sig"].values
    closes = df["Close"].values.astype(float)
    opens = df["Open"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    ts = df.index
    adx = df["adx"].values
    body_a = df["body_atr_ratio"].values
    slope = df["slope_pct"].values
    ema = df["ema9"].values
    hours = ts.hour.values
    n = len(df)

    trades: list[Trade] = []
    pending: list[tuple[int, str]] = []  # (signal_bar_idx, side)
    blocked_until = -1
    for i in range(n):
        # Time block: clear pending and skip
        if hours[i] in BLOCK_HOURS_UTC:
            pending = []
            continue

        # Process pending signals
        new_pending = []
        for sig_i, side in pending:
            if i - sig_i > RETEST_TIMEOUT_BARS:
                continue  # expired
            confirmed = _check_retest(side, ema[i], lows[i], highs[i])
            if not confirmed:
                new_pending.append((sig_i, side))
                continue
            # Slope gate: NOT fatal in live — pending stays alive on slope-fail
            if abs(slope[i]) < MIN_SLOPE_PCT:
                new_pending.append((sig_i, side))
                continue
            # Retest confirmed + base slope gate ok. Try to enter.
            if i <= blocked_until:
                # Position open — keep pending alive for later poll (matches live)
                new_pending.append((sig_i, side))
                continue
            # Optional entry filters (consume the pending — same as live: if user
            # blocks at handle_entry stage, signal is dropped from queue).
            if filters is not None:
                _adx = float(adx[i]) if not np.isnan(adx[i]) else 0.0
                if not filters.passes(ts[i], float(slope[i]), float(body_a[i]), _adx):
                    continue
            # Fire entry
            entry_price = float(ema[i])
            j_end = min(i + 1 + max_lookahead_bars, n)
            bars = [(int(ts[j].timestamp()), opens[j], highs[j], lows[j], closes[j])
                    for j in range(i + 1, j_end)]
            res = simulate_trade(side, entry_price, bars, p, ordering="avg")
            notional_in = p.margin_usdt * p.leverage
            notional_out = (res.exit_price / entry_price) * notional_in
            fee = (notional_in + notional_out) * p.commission_pct
            pnl_net = res.pnl_usdt - fee
            trades.append(Trade(
                idx=int(i), side=side, entry_ts=ts[i], entry_price=entry_price,
                exit_price=res.exit_price, exit_reason=res.exit_reason,
                pnl_usdt=res.pnl_usdt, pnl_net=pnl_net,
                duration_bars=res.duration_bars, max_state=res.max_state,
                hour_utc=ts[i].hour, weekday=ts[i].weekday(),
                adx_at_entry=float(adx[i]) if not np.isnan(adx[i]) else 0.0,
                body_atr_ratio=float(body_a[i]), slope_pct=float(slope[i]),
                ema9=float(ema[i]),
            ))
            blocked_until = i + max(1, res.duration_bars)
            # Pending consumed — do not re-add. Drop remaining pending of same side
            # (live's net position mode: opposite-side pendings continue to sit until expiry).
            # Conservative model: drop all pending of the same side, keep opposite.
            new_pending = [(s, sd) for (s, sd) in new_pending if sd != side]
        pending = new_pending

        # Add new Pine signals from this bar
        if buy_sig[i]:
            pending.append((i, "long"))
        if sell_sig[i]:
            pending.append((i, "short"))

    tdf = pd.DataFrame([asdict(t) for t in trades])
    return trades, tdf


def kpis(tdf: pd.DataFrame) -> dict:
    if tdf.empty:
        return {"n": 0, "msg": "no trades"}
    wins = tdf[tdf["pnl_net"] > 0]
    loss = tdf[tdf["pnl_net"] <= 0]
    net = float(tdf["pnl_net"].sum())
    gw = float(wins["pnl_net"].sum())
    gl = float(-loss["pnl_net"].sum())
    pf = (gw / gl) if gl > 0 else float("inf")
    cum = tdf["pnl_net"].cumsum().values
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak).min()
    by_reason = tdf["exit_reason"].value_counts().to_dict()
    return {
        "n": int(len(tdf)),
        "wins": int(len(wins)),
        "losses": int(len(loss)),
        "win_rate": round(len(wins) / len(tdf), 4),
        "net_pnl": round(net, 2),
        "gross_win": round(gw, 2),
        "gross_loss": round(gl, 2),
        "profit_factor": round(pf, 3),
        "avg_trade": round(net / len(tdf), 3),
        "median_trade": round(float(tdf["pnl_net"].median()), 3),
        "max_dd": round(float(dd), 2),
        "long_n": int((tdf["side"] == "long").sum()),
        "short_n": int((tdf["side"] == "short").sum()),
        "avg_dur_bars": round(float(tdf["duration_bars"].mean()), 1),
        "exit_reasons": by_reason,
    }


def load_and_signal(slice_dates: tuple[str, str] | None = None) -> pd.DataFrame:
    df = load_tv_export(DATA_FILE)
    if slice_dates:
        df = df.loc[slice_dates[0]:slice_dates[1]]
    df = generate_v3_signals(df)
    df = apply_entry_filter(df)
    return df


def mode_baseline():
    df = load_and_signal()
    print(f"Bars: {len(df)} from {df.index[0]} to {df.index[-1]}")
    print(f"Raw Pine sigs: buy={int(df['buy_sig'].sum())} sell={int(df['sell_sig'].sum())}")
    _, tdf = run_v3_backtest(df, TrailParams())
    k = kpis(tdf)
    print("--- KPIs (V3 ZEC baseline, live params) ---")
    for kk, vv in k.items():
        print(f"  {kk}: {vv}")
    out_path = Path(__file__).resolve().parent.parent / "data" / "zec_v3_baseline_trades.csv"
    tdf.to_csv(out_path, index=False)
    print(f"trades: {out_path}")


def mode_calibrate():
    # V3 went live 2026-05-12 17:05 UTC. Compare engine to live over that exact window.
    df = load_and_signal(("2026-05-12 17:00", "2026-05-15"))
    print(f"Window: {df.index[0]} -> {df.index[-1]}, bars={len(df)}")
    print(f"Raw Pine sigs in window: buy={int(df['buy_sig'].sum())} sell={int(df['sell_sig'].sum())}")
    _, tdf = run_v3_backtest(df, TrailParams())
    k = kpis(tdf)
    print("--- ENGINE KPIs (V3-era window) ---")
    for kk, vv in k.items():
        print(f"  {kk}: {vv}")
    print("Compare to live: 102 trades, net_pnl from bridge DB")


def mode_sl_sweep(df: pd.DataFrame | None = None, label: str = "all",
                  filters: EntryFilters | None = None,
                  sl_grid: list | None = None):
    if df is None:
        df = load_and_signal()
    if sl_grid is None:
        sl_grid = [round(x, 2) for x in np.arange(15.0, 100.01, 2.5)]
    rows = []
    for sl in sl_grid:
        p = replace(TrailParams(), sl_loss_usdt=sl)
        _, tdf = run_v3_backtest(df, p, filters=filters)
        k = kpis(tdf)
        k.pop("exit_reasons", None)
        rows.append({"sl_zec_usdt": sl, "sl_baseline": round(sl / 2.5, 2), **k})
    out = pd.DataFrame(rows).sort_values("net_pnl", ascending=False)
    today = pd.Timestamp.now().strftime("%Y-%m-%d")
    out_path = Path(__file__).resolve().parent.parent / "data" / f"zec_v3_sl_sweep_{label}_{today}.csv"
    out.to_csv(out_path, index=False)
    print(f"--- SL sweep ({label}, ranked by net_pnl) ---")
    print(out[["sl_zec_usdt", "sl_baseline", "n", "win_rate", "net_pnl",
               "profit_factor", "max_dd", "avg_trade"]].to_string(index=False))
    print(f"CSV: {out_path}")
    return out


# Recipes for filter combinations to test
FILTER_RECIPES = {
    "F0_baseline": EntryFilters(),
    "F1_no_sunday": EntryFilters(block_weekdays={6}),
    "F2_slope10": EntryFilters(min_abs_slope_pct=0.10),
    "F3_slope15": EntryFilters(min_abs_slope_pct=0.15),
    "F4_no_sun_slope10": EntryFilters(block_weekdays={6}, min_abs_slope_pct=0.10),
    "F5_no_sun_slope15": EntryFilters(block_weekdays={6}, min_abs_slope_pct=0.15),
    "F6_no_sun_slope10_nobodymid": EntryFilters(
        block_weekdays={6}, min_abs_slope_pct=0.10, block_body_band=(0.3, 0.5)),
    "F7_no_sun_slope10_nobodymid_nobadhours": EntryFilters(
        block_weekdays={6}, min_abs_slope_pct=0.10, block_body_band=(0.3, 0.5),
        block_hours_utc={5, 8, 10, 18, 22}),
    "F8_no_sun_slope15_nobodymid": EntryFilters(
        block_weekdays={6}, min_abs_slope_pct=0.15, block_body_band=(0.3, 0.5)),
}


def mode_filter_sweep():
    """Compare all filter recipes head-to-head at live baseline SL=$32.50."""
    df = load_and_signal()
    rows = []
    for name, f in FILTER_RECIPES.items():
        _, tdf = run_v3_backtest(df, TrailParams(), filters=f)
        k = kpis(tdf); k.pop("exit_reasons", None)
        rows.append({"recipe": name, **k})
    out = pd.DataFrame(rows).sort_values("net_pnl", ascending=False)
    today = pd.Timestamp.now().strftime("%Y-%m-%d")
    out_path = Path(__file__).resolve().parent.parent / "data" / f"zec_v3_filter_sweep_{today}.csv"
    out.to_csv(out_path, index=False)
    print("--- Filter recipe sweep (SL=$32.50, ranked by net_pnl) ---")
    print(out[["recipe", "n", "win_rate", "net_pnl", "profit_factor",
               "max_dd", "avg_trade"]].to_string(index=False))
    print(f"CSV: {out_path}")
    return out


def mode_sl_sweep_filtered(recipe: str = "F4_no_sun_slope10"):
    """Re-sweep SL on the filtered entry subset."""
    f = FILTER_RECIPES.get(recipe)
    if f is None:
        print(f"unknown recipe {recipe}"); return
    df = load_and_signal()
    # Coarse first, then fine band around winning region
    sl_grid = [round(x, 2) for x in np.arange(15.0, 100.01, 2.5)]
    mode_sl_sweep(df, label=f"filtered_{recipe}", filters=f, sl_grid=sl_grid)


def mode_compare_baseline_vs_best(recipe: str = "F4_no_sun_slope10",
                                  baseline_sl: float = 32.50,
                                  best_sl: float = 82.50):
    df = load_and_signal()
    print(f"\n=== BASELINE (live config) SL=${baseline_sl}, all filters off ===")
    _, t1 = run_v3_backtest(df, replace(TrailParams(), sl_loss_usdt=baseline_sl))
    k1 = kpis(t1); k1.pop("exit_reasons", None)
    for kk, vv in k1.items(): print(f"  {kk}: {vv}")
    f = FILTER_RECIPES[recipe]
    print(f"\n=== PROPOSED SL=${best_sl}, filters={recipe} ===")
    _, t2 = run_v3_backtest(df, replace(TrailParams(), sl_loss_usdt=best_sl), filters=f)
    k2 = kpis(t2); k2.pop("exit_reasons", None)
    for kk, vv in k2.items(): print(f"  {kk}: {vv}")


def mode_filter_analysis(sl_override: float | None = None):
    df = load_and_signal()
    p = TrailParams()
    if sl_override is not None:
        p = replace(p, sl_loss_usdt=sl_override)
    print(f"Filter analysis at SL=${p.sl_loss_usdt:.2f}")
    _, tdf = run_v3_backtest(df, p)
    if tdf.empty:
        print("no trades"); return
    out_dir = Path(__file__).resolve().parent.parent / "data"

    def bucket(name, col, bins=None, labels=None):
        if bins is not None:
            tdf[name] = pd.cut(tdf[col], bins=bins, labels=labels)
        else:
            tdf[name] = tdf[col]
        g = tdf.groupby(name, observed=True).agg(
            n=("pnl_net", "size"),
            wins=("pnl_net", lambda s: int((s > 0).sum())),
            net=("pnl_net", "sum"),
            avg=("pnl_net", "mean"),
            median=("pnl_net", "median"),
        ).reset_index()
        g["win_rate"] = (g["wins"] / g["n"]).round(3)
        g["net"] = g["net"].round(2); g["avg"] = g["avg"].round(3); g["median"] = g["median"].round(3)
        g = g.sort_values("net", ascending=False)
        return g

    print("\n=== HOUR OF DAY (UTC) ===")
    print(bucket("hour", "hour_utc").to_string(index=False))

    print("\n=== WEEKDAY (0=Mon) ===")
    print(bucket("dow", "weekday").to_string(index=False))

    print("\n=== ADX-at-entry ===")
    print(bucket("adx_b", "adx_at_entry",
                 bins=[-1, 15, 20, 25, 30, 40, 200],
                 labels=["<15", "15-20", "20-25", "25-30", "30-40", ">40"]).to_string(index=False))

    print("\n=== Body/ATR ratio ===")
    print(bucket("body_b", "body_atr_ratio",
                 bins=[-1, 0.3, 0.5, 0.8, 1.2, 5],
                 labels=["<0.3", "0.3-0.5", "0.5-0.8", "0.8-1.2", ">1.2"]).to_string(index=False))

    print("\n=== Slope magnitude (abs) ===")
    tdf["abs_slope"] = tdf["slope_pct"].abs()
    print(bucket("slope_b", "abs_slope",
                 bins=[0, 0.03, 0.06, 0.1, 0.2, 5],
                 labels=["0.03-0.06", "0.03-0.06", "0.06-0.1", "0.1-0.2", ">0.2"]).to_string(index=False) if False
          else bucket("slope_b", "abs_slope",
                      bins=[0, 0.06, 0.1, 0.15, 0.25, 5],
                      labels=["0.03-0.06", "0.06-0.1", "0.1-0.15", "0.15-0.25", ">0.25"]).to_string(index=False))

    print("\n=== Side ===")
    print(bucket("side_b", "side").to_string(index=False))

    print("\n=== Session (UTC) ===")
    def session(h):
        if 0 <= h < 8: return "Asia"
        if 8 <= h < 11: return "EU-pre"
        if 18 <= h < 24: return "US-late"
        return "blocked"
    tdf["sess"] = tdf["hour_utc"].apply(session)
    print(bucket("sess_b", "sess").to_string(index=False))

    out_path = out_dir / f"zec_v3_filter_trades_sl{int(p.sl_loss_usdt)}.csv"
    tdf.to_csv(out_path, index=False)
    print(f"\nTrades CSV: {out_path}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    arg2 = sys.argv[2] if len(sys.argv) > 2 else None
    if mode == "baseline":
        mode_baseline()
    elif mode == "calibrate":
        mode_calibrate()
    elif mode == "sl-sweep":
        mode_sl_sweep()
    elif mode == "filter-analysis":
        sl = float(arg2) if arg2 else None
        mode_filter_analysis(sl)
    elif mode == "filter-sweep":
        mode_filter_sweep()
    elif mode == "sl-sweep-filtered":
        mode_sl_sweep_filtered(arg2 or "F4_no_sun_slope10")
    elif mode == "compare":
        mode_compare_baseline_vs_best(
            recipe=arg2 or "F4_no_sun_slope10",
            baseline_sl=float(sys.argv[3]) if len(sys.argv) > 3 else 32.50,
            best_sl=float(sys.argv[4]) if len(sys.argv) > 4 else 82.50,
        )
    else:
        print(f"unknown mode: {mode}")
