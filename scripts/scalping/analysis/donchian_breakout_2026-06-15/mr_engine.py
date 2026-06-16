"""'Rebound' — multi-coin range mean-reversion FADE engine (portfolio, event-driven).

Thesis (earned from prior testing): crypto intraday mean-reverts; the only family that
passed walk-forward is fade-the-stretch. So: in a RANGE regime (ADX low, i.e. NOT
trending), fade a poke beyond a Bollinger band when price reclaims back inside, and
target reversion to the mean (the band basis). ATR stop beyond the poke; time stop so
a fade that doesn't revert is cut.

Same honesty discipline as the donchian engines:
  - signal on bar t close, fill at t+1 open (no lookahead); indicators shifted.
  - market entry at next open + slippage (taker); TP at the mean is a resting maker
    limit (no slip); stop at level + slip (taker); both-hit bar -> stop wins.
  - portfolio: scan all coins, rank by stretch, max-N concurrent, daily-loss/trade caps.
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
    mean_anchor: str = "sma"      # 'sma' | 'vwap' — what mean we fade back toward
    vwap_len: int = 24            # rolling VWAP window (bars) when mean_anchor='vwap'
    bb_len: int = 20
    bb_mult: float = 2.0
    z_len: int = 20
    z_entry: float = 2.0          # |z| beyond this to be 'stretched'
    trigger: str = "reclaim"      # 'reclaim' (poke then close back inside) | 'z' (z beyond, turning)
    adx_len: int = 14
    adx_max: float = 25.0         # fade only when ADX < this (range regime); 0 => off
    atr_len: int = 14
    atr_min_pct: float = 0.4
    vol_mult: float = 0.0         # optional volume filter (0 => off)
    vol_sma: int = 20
    # exit
    tp_mode: str = "mean"         # 'mean' (TP at band basis) | 'rmult'
    tp1_frac: float = 0.7         # banked at the mean; runner = 1-tp1_frac
    tp_rmult: float = 1.0         # used if tp_mode='rmult'
    atr_stop: float = 1.5
    atr_trail: float = 2.0        # runner trail (0 => exit full at mean)
    max_bars: int = 16            # time stop (bars); fade must revert promptly
    stop_cap_pct: dict = field(default_factory=dict)
    default_stop_cap: float = 2.5
    wide_stop_action: str = "skip"
    # sizing / portfolio
    risk_mode: str = "risk"
    risk_usd: float = 75.0
    notional_usd: float = 7500.0
    start_equity: float = 3000.0
    leverage: float = 10.0
    max_positions: int = 2
    max_total_notional: float = 18000.0
    max_daily_loss: float = 225.0
    max_trades_day: int = 6
    tf_minutes: int = 60


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
    if cfg.mean_anchor == "vwap":
        tp_ = (d["High"] + d["Low"] + c) / 3.0
        pv = (tp_ * d["Volume"]).rolling(cfg.vwap_len, min_periods=cfg.vwap_len).sum()
        vv = d["Volume"].rolling(cfg.vwap_len, min_periods=cfg.vwap_len).sum()
        basis = pv / vv.replace(0, np.nan)
    else:
        basis = c.rolling(cfg.bb_len, min_periods=cfg.bb_len).mean()
    dev = (c - basis).rolling(cfg.bb_len, min_periods=cfg.bb_len).std(ddof=0)
    d["basis"] = basis
    d["upper"] = basis + cfg.bb_mult * dev
    d["lower"] = basis - cfg.bb_mult * dev
    d["z"] = (c - basis) / dev.replace(0, np.nan)
    d["adx"] = _adx(d, cfg.adx_len)
    d["volsma"] = d["Volume"].rolling(cfg.vol_sma).mean()
    d["c_prev"] = c.shift(1)
    d["lower_prev"] = d["lower"].shift(1)
    d["upper_prev"] = d["upper"].shift(1)
    return d


def _fade_side(r, cfg):
    if not np.isfinite(r.atr) or r.atr <= 0 or not np.isfinite(r.basis):
        return 0, 0.0
    if r.atr_pct <= cfg.atr_min_pct:
        return 0, 0.0
    if cfg.adx_max > 0 and (not np.isfinite(r.adx) or r.adx > cfg.adx_max):
        return 0, 0.0          # only fade in a range, never in a trend
    if cfg.vol_mult > 0 and (not np.isfinite(r.volsma) or r.Volume < cfg.vol_mult * r.volsma):
        return 0, 0.0
    z = r.z if np.isfinite(r.z) else 0.0
    if cfg.trigger == "reclaim":
        # poked below lower band last bar, closed back inside this bar -> long the snap-back
        long = (r.c_prev < r.lower_prev) and (r.Close >= r.lower) and (r.Close < r.basis)
        short = (r.c_prev > r.upper_prev) and (r.Close <= r.upper) and (r.Close > r.basis)
    else:  # 'z': stretched beyond z_entry and turning back toward mean
        long = z <= -cfg.z_entry and r.Close > r.c_prev
        short = z >= cfg.z_entry and r.Close < r.c_prev
    if long:
        return +1, abs(z)
    if short:
        return -1, abs(z)
    return 0, 0.0


class _Pos:
    __slots__ = ("coin", "side", "entry_time", "entry_price", "atr0", "stop_dist", "tp",
                 "qty", "notional", "risk_usd", "rem", "peak", "trail", "tp_done",
                 "gross", "fees", "reasons", "entry_i")

    def __init__(self, coin, side, t, px, atr0, stop_dist, tp_price, qty, notional, risk_usd, i):
        self.coin, self.side, self.entry_time, self.entry_price = coin, side, t, px
        self.atr0, self.stop_dist, self.tp = atr0, stop_dist, tp_price
        self.qty, self.notional, self.risk_usd = qty, notional, risk_usd
        self.rem = 1.0; self.peak = px; self.trail = px - side * stop_dist
        self.tp_done = False; self.gross = 0.0; self.fees = 0.0; self.reasons = []
        self.entry_i = i


def simulate(coins_data, cfg: Cfg, costs: Costs):
    slip = costs.slippage_pct / 100.0; taker = costs.taker_pct / 100.0; maker = costs.maker_pct / 100.0
    times = sorted(set().union(*[set(df.index) for df in coins_data.values()]))
    tindex = {t: i for i, t in enumerate(times)}
    rows = {c: {t: r for t, r in zip(df.index, df.itertuples())} for c, df in coins_data.items()}
    equity = cfg.start_equity
    positions: dict[str, _Pos] = {}
    pending: list[dict] = []
    day_loss: dict = {}; day_trades: dict = {}
    trades: list[Trade] = []; curve = []

    def finalize(pos, t, last_px):
        nonlocal equity
        hours = (t - pos.entry_time).total_seconds() / 3600.0
        funding = pos.notional * (costs.funding_pct_per_8h / 100.0) * (hours / 8.0)
        pnl = pos.gross - pos.fees - funding
        equity += pnl
        d = t.date(); day_loss[d] = day_loss.get(d, 0.0) + pnl
        bars = int(round(hours / (cfg.tf_minutes / 60.0)))
        trades.append(Trade(pos.coin, pos.side, pos.entry_time, pos.entry_price, t, last_px,
                            pos.notional, pos.risk_usd, pnl, pnl / pos.risk_usd if pos.risk_usd else 0.0,
                            bars, "+".join(pos.reasons), equity))

    def close_leg(pos, frac, price, fee_rate, reason):
        q = pos.qty * frac
        pos.gross += (price - pos.entry_price) * q * pos.side
        pos.fees += q * price * fee_rate; pos.rem -= frac; pos.reasons.append(reason)

    for t in times:
        ti = tindex[t]
        # A) fill pending at open
        for e in pending:
            c = e["coin"]
            if c in positions or len(positions) >= cfg.max_positions:
                continue
            d = t.date()
            if day_loss.get(d, 0.0) <= -cfg.max_daily_loss or day_trades.get(d, 0) >= cfg.max_trades_day:
                continue
            r = rows[c].get(t)
            if r is None or not np.isfinite(r.atr) or r.atr <= 0:
                continue
            side = e["side"]; raw = r.Open
            cap = cfg.stop_cap_pct.get(c, cfg.default_stop_cap) / 100.0
            want = cfg.atr_stop * r.atr
            if want > cap * raw:
                if cfg.wide_stop_action == "skip":
                    continue
                want = cap * raw
            entry_px = raw * (1 + slip) if side > 0 else raw * (1 - slip)
            stop_dist = want
            stop_pct = stop_dist / entry_px
            if cfg.tp_mode == "mean":
                tp_price = e["basis"]                 # the mean at signal time
                # require the target to be on the right side & worth it
                if (side > 0 and tp_price <= entry_px) or (side < 0 and tp_price >= entry_px):
                    continue
            else:
                tp_price = entry_px + side * cfg.tp_rmult * stop_dist
            notional = cfg.notional_usd if cfg.risk_mode == "notional" else cfg.risk_usd / stop_pct
            risk_usd = notional * stop_pct if cfg.risk_mode == "notional" else cfg.risk_usd
            room = cfg.max_total_notional - sum(p.notional for p in positions.values())
            if room <= 0:
                continue
            if notional > room:
                notional = room; risk_usd = notional * stop_pct
            qty = notional / entry_px
            pos = _Pos(c, side, t, entry_px, r.atr, stop_dist, tp_price, qty, notional, risk_usd, ti)
            pos.fees += notional * taker
            positions[c] = pos
            day_trades[d] = day_trades.get(d, 0) + 1
        pending = []

        # B) exits
        for c in list(positions.keys()):
            pos = positions[c]; r = rows[c].get(t)
            if r is None:
                continue
            hi, lo, cl = r.High, r.Low, r.Close; side = pos.side
            # stop (both-hit -> stop first)
            if (lo <= pos.trail) if side > 0 else (hi >= pos.trail):
                px = pos.trail * (1 - slip) if side > 0 else pos.trail * (1 + slip)
                init = pos.entry_price - side * pos.stop_dist
                close_leg(pos, pos.rem, px, taker, "stop" if abs(pos.trail - init) < 1e-9 else "trail")
                finalize(pos, t, px); del positions[c]; continue
            # TP at mean (maker)
            if not pos.tp_done:
                if (hi >= pos.tp) if side > 0 else (lo <= pos.tp):
                    frac = cfg.tp1_frac if (cfg.atr_trail > 0 and cfg.tp1_frac < 1.0) else 1.0
                    close_leg(pos, frac, pos.tp, maker, "mean")
                    pos.tp_done = True
                    if frac >= 1.0 or pos.rem <= 1e-9:
                        finalize(pos, t, pos.tp); del positions[c]; continue
                    pos.trail = max(pos.trail, pos.entry_price) if side > 0 else min(pos.trail, pos.entry_price)
            # time stop
            if ti - pos.entry_i >= cfg.max_bars:
                px = cl * (1 - slip) if side > 0 else cl * (1 + slip)
                close_leg(pos, pos.rem, px, taker, "time"); finalize(pos, t, px); del positions[c]; continue
            # ratchet runner trail
            if pos.tp_done and cfg.atr_trail > 0:
                if side > 0:
                    pos.peak = max(pos.peak, cl); pos.trail = max(pos.trail, pos.peak - cfg.atr_trail * pos.atr0)
                else:
                    pos.peak = min(pos.peak, cl); pos.trail = min(pos.trail, pos.peak + cfg.atr_trail * pos.atr0)

        # C) scan + rank by stretch
        slots = cfg.max_positions - len(positions)
        d = t.date()
        blocked = day_loss.get(d, 0.0) <= -cfg.max_daily_loss or day_trades.get(d, 0) >= cfg.max_trades_day
        if slots > 0 and not blocked:
            cands = []
            for c in rows:
                if c in positions or t not in rows[c]:
                    continue
                r = rows[c][t]
                side, stretch = _fade_side(r, cfg)
                if side != 0:
                    cands.append(dict(coin=c, side=side, stretch=stretch, basis=r.basis))
            cands.sort(key=lambda x: x["stretch"], reverse=True)
            pending = cands[:slots]
        curve.append((t, equity))

    return trades, curve
