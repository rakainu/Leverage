"""V3 strategy port — Pine signals + EMA-retest+slope-gate entry + bridge trail state machine.

Port of the live V3 logic, symbol-agnostic. Reproduces:
  - V3 Pine signals (Heikin Ashi smoothed + body/ATR fakeout + ADX range filter)
  - Bridge entry pipeline (pending queue, EMA(9) retest, slope gate, position lock,
    pending expiry RETEST_TIMEOUT_BARS, optional entry filters)
  - Trail-SL state machine (SL -> BE -> lock -> trail jump -> trail)
  - Realistic fees + slippage

Entry point:
  run_backtest(df, params, filters) -> (trades: list[Trade], summary: dict)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, replace, field
from datetime import datetime, timezone
from statistics import median
from typing import Any, Optional

import numpy as np
import pandas as pd

from engine import calc_ema, calc_atr, calc_smma


# ---------- Pine signal defaults (mirror live V3 Pine inputs) ----------

SENS = 8
NOISE = 0.0
FAKEOUT = 0.2
RANGE_F = 0.2

EMA_PERIOD = 9
RETEST_OVERSHOOT_PCT = 0.2
SLOPE_LOOKBACK_BARS = 3
MIN_SLOPE_PCT = 0.03  # base slope gate kept alive in pending (matches live)
RETEST_TIMEOUT_BARS = 6


# ---------- Param dataclasses ----------

@dataclass
class TrailParams:
    """Exit-side state-machine parameters. Defaults match live V3.1 ZEC config."""
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
    sl_slippage_pct: float = 0.0006   # 0.06% past trigger — measured from live SL fills
    commission_pct: float = 0.0006    # BloFin taker; set to 0 for Lighter pass


@dataclass
class EntryFilters:
    """Per-bar gates applied at entry. Any falsy → disabled (i.e. matches live default)."""
    block_weekdays: set = field(default_factory=set)
    block_hours_utc: set = field(default_factory=set)
    min_abs_slope_pct: float = 0.0       # extra slope gate above live's 0.03 base
    min_body_atr_ratio: float = 0.0
    max_body_atr_ratio: Optional[float] = None
    block_body_band: Optional[tuple] = None   # (lo, hi) — body/ATR in [lo, hi) blocked
    min_adx: float = 0.0
    max_adx: Optional[float] = None
    block_adx_band: Optional[tuple] = None

    def passes(self, ts, slope_pct: float, body_atr: float, adx: float) -> bool:
        if self.block_weekdays and ts.weekday() in self.block_weekdays:
            return False
        if self.block_hours_utc and ts.hour in self.block_hours_utc:
            return False
        if self.min_abs_slope_pct and abs(slope_pct) < self.min_abs_slope_pct:
            return False
        if body_atr < self.min_body_atr_ratio:
            return False
        if self.max_body_atr_ratio is not None and body_atr > self.max_body_atr_ratio:
            return False
        if self.block_body_band and self.block_body_band[0] <= body_atr < self.block_body_band[1]:
            return False
        if adx < self.min_adx:
            return False
        if self.max_adx is not None and adx > self.max_adx:
            return False
        if self.block_adx_band and self.block_adx_band[0] <= adx < self.block_adx_band[1]:
            return False
        return True


@dataclass
class SimResult:
    pnl_usdt: float = 0.0
    exit_reason: str = ""
    exit_price: float = 0.0
    final_state: int = 0
    duration_bars: int = 0
    max_state: int = 0


@dataclass
class Trade:
    idx: int = 0
    side: str = ""
    entry_ts: Any = None
    entry_price: float = 0.0
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_usdt: float = 0.0   # gross PnL (no fees)
    pnl_net: float = 0.0    # net PnL after commission
    duration_bars: int = 0
    max_state: int = 0
    hour_utc: int = 0
    weekday: int = 0
    adx_at_entry: float = 0.0
    body_atr_ratio: float = 0.0
    slope_pct: float = 0.0
    ema9: float = 0.0


# ---------- Pine signal regenerator (V3) ----------

def generate_v3_signals(df: pd.DataFrame, sensitivity: int = SENS, noise: float = NOISE,
                        fakeout: float = FAKEOUT, range_filt: float = RANGE_F) -> pd.DataFrame:
    """Replicate the V3 Pine: HA-smoothed flip + body/ATR fakeout + ADX range filter."""
    df = df.copy()
    closes = df["Close"].values.astype(float)
    opens = df["Open"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    n = len(df)

    ha_close = (opens + highs + lows + closes) / 4.0
    ha_open = np.zeros(n)
    ha_open[0] = (opens[0] + closes[0]) / 2.0
    for i in range(1, n):
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

    # ADX (Wilder)
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
        tr[i] = max(highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]))
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
    df["atr14"] = atr14
    return df


def _compute_ema_and_slope(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    ema = calc_ema(df["Close"], EMA_PERIOD).values
    n = len(df)
    slope_pct = np.zeros(n)
    for i in range(SLOPE_LOOKBACK_BARS, n):
        prev = ema[i - SLOPE_LOOKBACK_BARS]
        if prev and not np.isnan(prev) and not np.isnan(ema[i]):
            slope_pct[i] = (ema[i] - prev) / prev * 100.0
    df["ema9"] = ema
    df["slope_pct"] = slope_pct
    return df


def prepare_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Run Pine + EMA/slope pre-compute. One-time cost per symbol-window."""
    df = generate_v3_signals(df)
    df = _compute_ema_and_slope(df)
    return df


# ---------- Exit state machine ----------

def _dollars_to_price_distance(usd: float, p: TrailParams, ref_price: float) -> float:
    notional = p.margin_usdt * p.leverage
    return (usd / notional) * ref_price


def _pnl_at_price(side: str, entry: float, price: float, p: TrailParams) -> float:
    notional = p.margin_usdt * p.leverage
    pct = (price - entry) / entry if side == "long" else (entry - price) / entry
    return pct * notional


def simulate_trade(side: str, entry_price: float, bars: list,
                   p: TrailParams, ordering: str = "avg",
                   fav_mode: str = "extreme") -> SimResult:
    """Replay one trade through the state machine. `bars`: list of (ts, o, h, l, c).

    fav_mode controls the favorable price the protective state machine (BE / lock /
    trail / TP-ceiling) is allowed to "see" per bar:
      "extreme" — use the bar HIGH (long) / LOW (short). Optimistic: assumes the
                  state machine captured the best intrabar tick. The live bridge
                  samples the order-book MID every 5s and never sees that wick, so
                  this overstates how often BE/lock arm and understates full stops.
      "close"   — use the bar CLOSE for advancement + TP. Realistic floor: only
                  end-of-bar favorable progress arms protection. The adverse SL
                  check ALWAYS uses the bar extreme (a stop fills on any tick).
    """
    if not bars:
        return SimResult(exit_reason="unresolved", exit_price=entry_price)

    def _one(order: str) -> SimResult:
        state = 0
        sl_dist0 = _dollars_to_price_distance(p.sl_loss_usdt, p, entry_price)
        sl = entry_price - sl_dist0 if side == "long" else entry_price + sl_dist0
        trail_high = entry_price
        max_state = 0

        def advance(fav, sl_in, st_in, th_in):
            sl_, st_, th_ = sl_in, st_in, th_in
            peak = _pnl_at_price(side, entry_price, fav, p)
            if st_ == 0 and peak >= p.breakeven_usdt:
                sl_ = entry_price
                st_ = 1
            if st_ == 1 and peak >= p.lock_profit_activate_usdt:
                ld = _dollars_to_price_distance(p.lock_profit_usdt, p, entry_price)
                sl_ = entry_price + ld if side == "long" else entry_price - ld
                st_ = 2
            if st_ == 2 and peak >= p.trail_activate_usdt:
                jl = p.trail_start_usdt - p.trail_distance_usdt
                jd = _dollars_to_price_distance(jl, p, entry_price)
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
                td = _dollars_to_price_distance(p.trail_distance_usdt, p, th_)
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
            return SimResult(pnl_usdt=_pnl_at_price(side, entry_price, exit_p, p),
                             exit_reason=reason, exit_price=exit_p,
                             final_state=st_now, max_state=st_now)

        for i, bar in enumerate(bars):
            _, b_o, b_h, b_l, b_c = bar[:5]
            adv = b_l if side == "long" else b_h          # adverse extreme — SL always fills on any tick
            if fav_mode == "close":
                fav = b_c                                  # realistic: only end-of-bar favorable progress arms protection
            else:
                fav = b_h if side == "long" else b_l       # optimistic: best intrabar tick

            # TP ceiling (first thing checked per bar — favorable extreme)
            peak = _pnl_at_price(side, entry_price, fav, p)
            if peak >= p.margin_usdt * p.tp_ceiling_pct:
                cd = _dollars_to_price_distance(p.margin_usdt * p.tp_ceiling_pct, p, entry_price)
                cp = entry_price + cd if side == "long" else entry_price - cd
                return SimResult(pnl_usdt=_pnl_at_price(side, entry_price, cp, p),
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
        return SimResult(pnl_usdt=_pnl_at_price(side, entry_price, last_c, p),
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


# ---------- Backtest loop ----------

def run_backtest(df: pd.DataFrame, p: TrailParams,
                 filters: Optional[EntryFilters] = None,
                 max_lookahead_bars: int = 288,
                 entry_mode: str = "next_open",
                 fav_advance: str = "close") -> tuple[list[Trade], pd.DataFrame]:
    """Bar-walk loop matching live bridge: Pine signal -> pending queue -> EMA retest
    -> slope gate -> optional filters -> position lock -> trail state machine.

    DEFAULTS ARE HONEST (calibrated to the live Lighter bridge, 2026-05-27). Two
    optimism bugs once inflated this engine's ZEC 180d net from a realistic -$7.8k
    to a fantasy +$29.3k — see project_v3_entry_fill_phantom. Do NOT revert these
    defaults to reproduce old sweep numbers; the old numbers were fiction.

    entry_mode — assumed FILL price on the retest bar:
      "next_open"— fill at the next bar's open (DEFAULT; matches live market order
                   placed when the bar closes; live entry slippage measured ~0).
      "close"    — fill at the retest bar's close (near-identical to next_open).
      "ema"      — fill at the exact EMA(9). THE ORIGINAL LIE: an unfillable limit
                   at the dip. Kept only to reproduce the historical fantasy.

    fav_advance — favorable price the protective state machine may "see" per bar:
      "close"    — bar close only (DEFAULT; realistic — live samples the mid every
                   5s and never catches the intrabar wick). SL still fills on the
                   adverse extreme.
      "extreme"  — bar high/low. OPTIMISTIC: pretends the SM caught the best tick,
                   arming BE/lock far too often. Second source of the old fantasy.
    """
    if "buy_sig" not in df.columns:
        df = prepare_dataframe(df)

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
    n = len(df)

    trades: list[Trade] = []
    pending: list[tuple[int, str]] = []
    blocked_until = -1

    for i in range(n):
        new_pending = []
        for sig_i, side in pending:
            if i - sig_i > RETEST_TIMEOUT_BARS:
                continue  # expired

            # EMA(9) retest check
            ema_v = ema[i]
            if np.isnan(ema_v):
                new_pending.append((sig_i, side))
                continue
            overshoot = ema_v * (RETEST_OVERSHOOT_PCT / 100.0)
            if entry_mode == "limit_ema":
                # Resting limit at the EMA fills whenever price REACHES it, with no
                # overshoot cap — including bars that gap straight through (the
                # falling knives a market-on-clean-retest entry skips). This is the
                # adverse-selection cost of passive entry, modeled honestly.
                touched = lows[i] <= ema_v if side == "long" else highs[i] >= ema_v
            elif side == "long":
                touched = lows[i] <= ema_v and lows[i] >= ema_v - overshoot
            else:
                touched = highs[i] >= ema_v and highs[i] <= ema_v + overshoot
            if not touched:
                new_pending.append((sig_i, side))
                continue

            # Base slope gate (live MIN_SLOPE_PCT — keep pending alive if fails)
            if abs(slope[i]) < MIN_SLOPE_PCT:
                new_pending.append((sig_i, side))
                continue

            # Position lock — keep pending alive
            if i <= blocked_until:
                new_pending.append((sig_i, side))
                continue

            # Optional entry filters — drop pending if blocked here
            if filters is not None:
                _adx = float(adx[i]) if not np.isnan(adx[i]) else 0.0
                if not filters.passes(ts[i], float(slope[i]), float(body_a[i]), _adx):
                    continue

            # Fire entry at the assumed fill price (see entry_mode docstring)
            if entry_mode == "close":
                entry_price = float(closes[i])
            elif entry_mode == "next_open":
                entry_price = float(opens[i + 1]) if i + 1 < n else float(closes[i])
            else:  # "ema" — original idealized limit fill
                entry_price = float(ema_v)
            j_end = min(i + 1 + max_lookahead_bars, n)
            bars = [(int(ts[j].timestamp()), opens[j], highs[j], lows[j], closes[j])
                    for j in range(i + 1, j_end)]
            res = simulate_trade(side, entry_price, bars, p, ordering="avg",
                                 fav_mode=fav_advance)

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
                ema9=float(ema_v),
            ))
            blocked_until = i + max(1, res.duration_bars)
            # Drop remaining pending of same side (net position mode)
            new_pending = [(s, sd) for (s, sd) in new_pending if sd != side]

        pending = new_pending

        if buy_sig[i]:
            pending.append((i, "long"))
        if sell_sig[i]:
            pending.append((i, "short"))

    tdf = pd.DataFrame([asdict(t) for t in trades])
    return trades, tdf


# ---------- KPIs ----------

def kpis(tdf: pd.DataFrame, starting_capital: float = 2000.0) -> dict:
    if tdf is None or tdf.empty:
        return {"n": 0, "net_pnl": 0.0, "profit_factor": 0.0, "max_dd": 0.0,
                "win_rate": 0.0, "avg_trade": 0.0, "largest_loss_streak": 0,
                "largest_loss_run_usdt": 0.0}
    wins = tdf[tdf["pnl_net"] > 0]
    loss = tdf[tdf["pnl_net"] <= 0]
    net = float(tdf["pnl_net"].sum())
    gw = float(wins["pnl_net"].sum())
    gl = float(-loss["pnl_net"].sum())
    pf = (gw / gl) if gl > 0 else float("inf")
    cum = tdf["pnl_net"].cumsum().values
    peak = np.maximum.accumulate(cum) if len(cum) else np.array([0])
    dd = float((cum - peak).min()) if len(cum) else 0.0

    # Largest losing streak (count + cumulative $)
    streak_n = streak_usd = max_streak_n = max_streak_usd = 0
    cur_streak_usd = 0.0
    for p in tdf["pnl_net"].values:
        if p <= 0:
            streak_n += 1
            cur_streak_usd += p
            max_streak_n = max(max_streak_n, streak_n)
            max_streak_usd = min(max_streak_usd, cur_streak_usd)
        else:
            streak_n = 0
            cur_streak_usd = 0.0

    return {
        "n": int(len(tdf)),
        "wins": int(len(wins)),
        "losses": int(len(loss)),
        "win_rate": round(len(wins) / len(tdf), 4),
        "net_pnl": round(net, 2),
        "gross_win": round(gw, 2),
        "gross_loss": round(gl, 2),
        "profit_factor": round(pf, 3) if pf != float("inf") else 99.999,
        "avg_win": round(gw / max(1, len(wins)), 2),
        "avg_loss": round(-gl / max(1, len(loss)), 2),
        "avg_trade": round(net / len(tdf), 3),
        "max_dd": round(dd, 2),
        "max_dd_pct_of_capital": round(dd / starting_capital * 100, 2),
        "largest_loss_streak": int(max_streak_n),
        "largest_loss_run_usdt": round(max_streak_usd, 2),
    }


if __name__ == "__main__":
    # Smoke: live V3.1 ZEC defaults over recent 30d
    from engine import load_symbol
    print("=" * 72)
    print("STRATEGY SMOKE TEST — V3.1 ZEC defaults, last 30d")
    print("=" * 72)
    df = load_symbol("ZEC", "5m", days_back=30)
    df = prepare_dataframe(df)
    print(f"Bars: {len(df)}  range: {df.index[0]} -> {df.index[-1]}")
    print(f"Raw Pine sigs: buy={int(df['buy_sig'].sum())}  sell={int(df['sell_sig'].sum())}")
    _, tdf = run_backtest(df, TrailParams())
    k = kpis(tdf)
    print()
    for kk, vv in k.items():
        print(f"  {kk}: {vv}")
