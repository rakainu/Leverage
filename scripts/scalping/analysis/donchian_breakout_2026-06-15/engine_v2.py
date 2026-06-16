"""v2 portfolio engine for the 1H Donchian family — adds the mechanics that prior
testing says actually matter (not just more param sweeps):

  * ENTRY MODE: 'breakout' (close beyond Donchian, fill next open) OR 'pullback'
    (after the breakout, rest a maker limit at the broken level = retest; fill only
    if a later bar trades back to it within N bars — avoids buying the faded spike).
  * CROSS-SECTIONAL RS GATE: each bar, rank the whole basket by momentum; longs only
    allowed on the top rs_long coins, shorts only on the bottom rs_short coins.
  * TREND-STRENGTH GATE: ADX(adx_len) >= adx_min (only breakouts inside real trends).
  * EXIT MODELS A-E (ATR-trail only / Donchian-only / partial+trail / two-TP+trail).
  * SIZING: fixed-risk ($/trade) or fixed-notional ($). If the ATR stop is wider than
    the coin's cap, SKIP the trade (per Rich) rather than tighten it.

Honesty rules unchanged from v1: no lookahead (signal on close, fill next bar/limit),
honest fills (market+slip taker; limit/TP maker no-slip; stop at level+slip taker;
both-hit bar = stop first), close-based ATR trail.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
import pandas as pd


@dataclass
class Costs:
    taker_pct: float = 0.0
    maker_pct: float = 0.0
    slippage_pct: float = 0.05
    funding_pct_per_8h: float = 0.01


@dataclass
class Cfg:
    # --- entry ---
    entry_mode: str = "breakout"     # 'breakout' | 'pullback'
    pullback_valid: int = 4          # bars the retest limit stays live
    don_entry: int = 20
    don_exit: int = 10
    ema_len: int = 100
    ema_slope_lb: int = 3
    atr_len: int = 14
    vol_mult: float = 1.2            # 0 => off
    vol_sma: int = 20
    atr_min_pct: float = 0.6
    adx_len: int = 14
    adx_min: float = 0.0            # 0 => off
    # --- cross-sectional relative strength ---
    rs_lb: int = 24                 # momentum lookback (bars) for RS ranking
    rs_long: int = 0               # longs only if coin in top-N by momentum (0 => off)
    rs_short: int = 0              # shorts only if coin in bottom-N (0 => off)
    # --- risk / sizing ---
    risk_mode: str = "risk"         # 'risk' | 'notional'
    risk_usd: float = 75.0
    notional_usd: float = 7500.0
    atr_stop: float = 1.5
    atr_trail: float = 2.5
    stop_cap_pct: dict = field(default_factory=dict)
    default_stop_cap: float = 2.2
    wide_stop_action: str = "skip"  # 'skip' | 'cap'
    # --- exit model ---
    exit_model: str = "D"           # A|B|C|D|E
    # --- portfolio ---
    start_equity: float = 3000.0
    leverage: float = 10.0
    max_positions: int = 2
    max_total_notional: float = 18000.0
    max_daily_loss: float = 225.0
    max_trades_day: int = 4
    tf_minutes: int = 60


# exit model -> (use_tp1, tp1_r, tp1_frac, use_tp2, tp2_r, tp2_frac, use_trail, use_donch)
EXIT_MODELS = {
    "A": (False, 0, 0.0, False, 0, 0.0, True, False),                 # 100% ATR trail
    "B": (False, 0, 0.0, False, 0, 0.0, False, True),                 # 100% Donchian exit
    "C": (True, 2.0, 0.30, False, 0, 0.0, True, True),                # 30%@2R, trail 70%, donch safety
    "D": (True, 1.5, 0.30, True, 3.0, 0.30, True, True),              # 30%@1.5R,30%@3R,trail 40%
    "E": (True, 2.0, 0.40, False, 0, 0.0, True, True),                # 40%@2R, trail 60% (prior-test pick)
}


@dataclass
class Trade:
    coin: str; side: int
    entry_time: pd.Timestamp; entry_price: float
    exit_time: pd.Timestamp; exit_price: float
    notional: float; risk_usd: float
    pnl_usd: float; r_multiple: float; bars_held: int
    reasons: str; equity_after: float


def _adx(df, n):
    h, l, c = df["High"], df["Low"], df["Close"]
    up = h.diff(); dn = -l.diff()
    plus = ((up > dn) & (up > 0)) * up
    minus = ((dn > up) & (dn > 0)) * dn
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    pdi = 100 * plus.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean() / atr
    mdi = 100 * minus.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def prepare(df, cfg: Cfg):
    d = df.copy()
    h, l, c = d["High"], d["Low"], d["Close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1.0 / cfg.atr_len, adjust=False, min_periods=cfg.atr_len).mean()
    d["atr_pct"] = d["atr"] / c * 100.0
    d["ema"] = c.ewm(span=cfg.ema_len, adjust=False, min_periods=cfg.ema_len).mean()
    d["ema_rising"] = d["ema"] > d["ema"].shift(cfg.ema_slope_lb)
    d["ema_falling"] = d["ema"] < d["ema"].shift(cfg.ema_slope_lb)
    d["don_hi"] = h.rolling(cfg.don_entry).max().shift(1)
    d["don_lo"] = l.rolling(cfg.don_entry).min().shift(1)
    d["exit_hi"] = h.rolling(cfg.don_exit).max().shift(1)
    d["exit_lo"] = l.rolling(cfg.don_exit).min().shift(1)
    d["volsma"] = d["Volume"].rolling(cfg.vol_sma).mean()
    d["adx"] = _adx(d, cfg.adx_len)
    d["mom"] = c / c.shift(cfg.rs_lb) - 1.0
    return d


def _passes_filters(r, cfg):
    if not np.isfinite(r.atr) or r.atr <= 0 or not np.isfinite(r.ema):
        return False
    if not np.isfinite(r.don_hi) or not np.isfinite(r.volsma) or r.volsma <= 0:
        return False
    if r.atr_pct <= cfg.atr_min_pct:
        return False
    if cfg.vol_mult > 0 and r.Volume <= cfg.vol_mult * r.volsma:
        return False
    if cfg.adx_min > 0 and (not np.isfinite(r.adx) or r.adx < cfg.adx_min):
        return False
    return True


def _raw_side(r, cfg):
    if r.Close > r.don_hi and r.Close > r.ema and r.ema_rising:
        return +1
    if r.Close < r.don_lo and r.Close < r.ema and r.ema_falling:
        return -1
    return 0


class _Pos:
    __slots__ = ("coin", "side", "entry_time", "entry_price", "atr0", "stop_dist",
                 "tp1", "tp2", "qty", "notional", "risk_usd", "rem", "peak", "trail",
                 "tp1_done", "tp2_done", "gross", "fees", "reasons", "ex")

    def __init__(self, coin, side, t, px, atr0, stop_dist, qty, notional, risk_usd, ex):
        self.coin, self.side, self.entry_time, self.entry_price = coin, side, t, px
        self.atr0, self.stop_dist = atr0, stop_dist
        u_tp1, r1, f1, u_tp2, r2, f2, u_trail, u_donch = ex
        self.tp1 = px + side * r1 * stop_dist if u_tp1 else None
        self.tp2 = px + side * r2 * stop_dist if u_tp2 else None
        self.qty, self.notional, self.risk_usd = qty, notional, risk_usd
        self.rem = 1.0; self.peak = px; self.trail = px - side * stop_dist
        self.tp1_done = self.tp2_done = False
        self.gross = 0.0; self.fees = 0.0; self.reasons = []
        self.ex = ex


def simulate(coins_data: dict, cfg: Cfg, costs: Costs):
    slip = costs.slippage_pct / 100.0
    taker = costs.taker_pct / 100.0
    maker = costs.maker_pct / 100.0
    ex = EXIT_MODELS[cfg.exit_model]
    u_tp1, r1, f1, u_tp2, r2, f2, u_trail, u_donch = ex
    f_tp1 = f1 if u_tp1 else 0.0
    f_tp2 = f2 if u_tp2 else 0.0

    times = sorted(set().union(*[set(df.index) for df in coins_data.values()]))
    rows = {c: {t: r for t, r in zip(df.index, df.itertuples())} for c, df in coins_data.items()}

    equity = cfg.start_equity
    positions: dict[str, _Pos] = {}
    pending: list[dict] = []
    day_loss: dict = {}; day_trades: dict = {}
    trades: list[Trade] = []
    curve = []

    def size(entry_px, stop_dist, c):
        stop_pct = stop_dist / entry_px
        if cfg.risk_mode == "notional":
            notional = cfg.notional_usd
            risk_usd = notional * stop_pct
        else:
            risk_usd = cfg.risk_usd
            notional = risk_usd / stop_pct
        return notional, risk_usd

    def open_pos(c, side, t, raw_px, fee_is_maker):
        r = rows[c].get(t)
        if r is None or not np.isfinite(r.atr) or r.atr <= 0:
            return False
        cap = cfg.stop_cap_pct.get(c, cfg.default_stop_cap) / 100.0
        want = cfg.atr_stop * r.atr
        if want > cap * raw_px:
            if cfg.wide_stop_action == "skip":
                return False
            want = cap * raw_px
        stop_dist = want
        if stop_dist <= 0:
            return False
        entry_px = raw_px * (1 + slip) if (side > 0 and not fee_is_maker) else (
            raw_px * (1 - slip) if (side < 0 and not fee_is_maker) else raw_px)
        notional, risk_usd = size(entry_px, stop_dist, c)
        open_notional = sum(p.notional for p in positions.values())
        room = cfg.max_total_notional - open_notional
        if room <= 0:
            return False
        if notional > room:
            notional = room; risk_usd = notional * (stop_dist / entry_px)
        qty = notional / entry_px
        pos = _Pos(c, side, t, entry_px, r.atr, stop_dist, qty, notional, risk_usd, ex)
        pos.fees += notional * (maker if fee_is_maker else taker)  # entry fee
        positions[c] = pos
        return True

    def close_leg(pos, frac, price, fee_rate, reason):
        leg_qty = pos.qty * frac
        pos.gross += (price - pos.entry_price) * leg_qty * pos.side
        pos.fees += leg_qty * price * fee_rate
        pos.rem -= frac
        pos.reasons.append(reason)

    def finalize(pos, t, last_price):
        nonlocal equity
        hours = (t - pos.entry_time).total_seconds() / 3600.0
        funding = pos.notional * (costs.funding_pct_per_8h / 100.0) * (hours / 8.0)
        pnl = pos.gross - pos.fees - funding
        equity += pnl
        d = t.date()
        day_loss[d] = day_loss.get(d, 0.0) + pnl
        bars = int(round(hours / (cfg.tf_minutes / 60.0)))
        trades.append(Trade(pos.coin, pos.side, pos.entry_time, pos.entry_price, t, last_price,
                            pos.notional, pos.risk_usd, pnl, pnl / pos.risk_usd if pos.risk_usd else 0.0,
                            bars, "+".join(pos.reasons), equity))

    for t in times:
        # A) fill pending (market next-open, or pullback maker-limit retest)
        for e in pending:
            c = e["coin"]
            if c in positions or len(positions) >= cfg.max_positions:
                continue
            d = t.date()
            if day_loss.get(d, 0.0) <= -cfg.max_daily_loss or day_trades.get(d, 0) >= cfg.max_trades_day:
                continue
            r = rows[c].get(t)
            if r is None:
                continue
            opened = False
            if cfg.entry_mode == "breakout":
                opened = open_pos(c, e["side"], t, r.Open, fee_is_maker=False)
            else:  # pullback: limit at the broken level; fill if this bar trades through it
                lvl = e["level"]
                if e["side"] > 0 and r.Low <= lvl:
                    opened = open_pos(c, +1, t, lvl, fee_is_maker=True)
                elif e["side"] < 0 and r.High >= lvl:
                    opened = open_pos(c, -1, t, lvl, fee_is_maker=True)
            if opened:
                day_trades[d] = day_trades.get(d, 0) + 1
        # carry unfilled pullback limits forward until they expire
        nxt = []
        for e in pending:
            if cfg.entry_mode == "pullback" and e["coin"] not in positions and e["bars_left"] > 1:
                nxt.append({**e, "bars_left": e["bars_left"] - 1})
        pending = nxt

        # B) process exits
        for c in list(positions.keys()):
            pos = positions[c]; r = rows[c].get(t)
            if r is None:
                continue
            hi, lo, cl = r.High, r.Low, r.Close
            side = pos.side
            hit_stop = (lo <= pos.trail) if side > 0 else (hi >= pos.trail)
            if hit_stop:
                px = pos.trail * (1 - slip) if side > 0 else pos.trail * (1 + slip)
                init = pos.entry_price - side * pos.stop_dist
                reason = "stop" if abs(pos.trail - init) < 1e-9 else "trail"
                close_leg(pos, pos.rem, px, taker, reason); finalize(pos, t, px); del positions[c]; continue
            if pos.tp1 is not None and not pos.tp1_done:
                if (hi >= pos.tp1) if side > 0 else (lo <= pos.tp1):
                    close_leg(pos, f_tp1, pos.tp1, maker, "tp1"); pos.tp1_done = True
            if pos.tp2 is not None and pos.tp1_done and not pos.tp2_done:
                if (hi >= pos.tp2) if side > 0 else (lo <= pos.tp2):
                    close_leg(pos, f_tp2, pos.tp2, maker, "tp2"); pos.tp2_done = True
            if u_donch:
                opp = r.exit_lo if side > 0 else r.exit_hi
                if np.isfinite(opp) and pos.rem > 0:
                    if (cl < opp) if side > 0 else (cl > opp):
                        px = cl * (1 - slip) if side > 0 else cl * (1 + slip)
                        close_leg(pos, pos.rem, px, taker, "donch"); finalize(pos, t, px); del positions[c]; continue
            if u_trail:
                if side > 0:
                    pos.peak = max(pos.peak, cl); pos.trail = max(pos.trail, pos.peak - cfg.atr_trail * pos.atr0)
                else:
                    pos.peak = min(pos.peak, cl); pos.trail = min(pos.trail, pos.peak + cfg.atr_trail * pos.atr0)
            if pos.rem <= 1e-9:
                finalize(pos, t, pos.tp2 if pos.tp2 else pos.entry_price); del positions[c]

        # C) scan + RS gate + rank, queue for next bar
        slots = cfg.max_positions - len(positions)
        d = t.date()
        blocked = day_loss.get(d, 0.0) <= -cfg.max_daily_loss or day_trades.get(d, 0) >= cfg.max_trades_day
        if slots > 0 and not blocked:
            present = [(c, rows[c][t]) for c in rows if t in rows[c]]
            mom = {c: r.mom for c, r in present if np.isfinite(r.mom)}
            ranked = sorted(mom, key=mom.get, reverse=True)
            long_ok = set(ranked[:cfg.rs_long]) if cfg.rs_long > 0 else None
            short_ok = set(ranked[-cfg.rs_short:]) if cfg.rs_short > 0 else None
            cands = []
            for c, r in present:
                if c in positions or not _passes_filters(r, cfg):
                    continue
                side = _raw_side(r, cfg)
                if side == 0:
                    continue
                if side > 0 and long_ok is not None and c not in long_ok:
                    continue
                if side < 0 and short_ok is not None and c not in short_ok:
                    continue
                strength = (r.Volume / r.volsma, abs(r.Close - r.ema) / r.atr, abs(r.mom) if np.isfinite(r.mom) else 0)
                lvl = r.don_hi if side > 0 else r.don_lo
                cands.append(dict(coin=c, side=side, strength=strength, level=lvl, bars_left=cfg.pullback_valid))
            cands.sort(key=lambda x: x["strength"], reverse=True)
            pending = (pending + cands[:slots]) if cfg.entry_mode == "pullback" else cands[:slots]
        elif cfg.entry_mode != "pullback":
            pending = []
        curve.append((t, equity))

    return trades, curve
