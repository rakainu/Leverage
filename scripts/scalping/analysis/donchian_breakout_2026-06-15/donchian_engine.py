"""Portfolio-level event-driven backtester for the 1H Donchian Momentum Breakout.

Why a new engine: this strategy has CROSS-COIN rules the per-coin btengine can't do:
  - scan every coin each 1H close, rank, take only the best N signals
  - max N concurrent positions across the whole book
  - max total notional, max daily loss, max trades/day
  - multi-leg exits (TP1/TP2/runner) + ATR trail + opposite-Donchian emergency exit

Honesty rules (same discipline as btengine):
  - NO LOOKAHEAD: a signal is decided on bar t's CLOSE; the order fills at t+1's OPEN.
    Indicators only ever use data up to the decision bar (Donchian/vol/ATR are shifted).
  - HONEST FILLS: market entry at next open + adverse slippage (taker). Hard/trail stop
    fills at the stop price + adverse slippage (taker). TP1/TP2 are resting maker limits
    (fill at the level, no slippage). Emergency Donchian close = taker at the close.
  - BOTH-HIT BAR: if a bar touches both stop and a TP, the STOP is taken first
    (conservative — we can't see intrabar order).
  - CLOSE-BASED TRAIL: the 2.5*ATR trail only ratchets on a bar's close, and is checked
    against the NEXT bars' lows (can't trail off a close you haven't seen yet).

Sizing: notional = risk_$ / stop_pct (stop_pct = stop_dist/entry). Capped by the
portfolio's remaining notional budget; risk scales down with the cap. Fixed-$ risk by
default (spec: $75/trade), so equity is start + cumulative leg PnL.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
import pandas as pd


@dataclass
class Costs:
    taker_pct: float = 0.0      # % per side on market/stop fills
    maker_pct: float = 0.0      # % per side on resting TP fills
    slippage_pct: float = 0.05  # % adverse slippage on market/stop fills
    funding_pct_per_8h: float = 0.01


@dataclass
class Cfg:
    don_entry: int = 20
    don_exit: int = 10
    ema_len: int = 100
    ema_slope_lb: int = 3        # "EMA rising/falling" measured over this many bars
    atr_len: int = 14
    atr_stop: float = 1.5        # initial stop = atr_stop * ATR
    atr_trail: float = 2.5       # runner trail = atr_trail * ATR(at entry)
    vol_mult: float = 1.2        # volume > vol_mult * SMA20(volume); 0 => no filter
    vol_sma: int = 20
    atr_min_pct: float = 0.6     # ATR% must exceed this (in %), e.g. 0.6 = 0.6%
    tp1_r: float = 1.5
    tp2_r: float = 3.0
    tp1_frac: float = 0.30
    tp2_frac: float = 0.30       # runner frac = 1 - tp1 - tp2
    risk_usd: float = 75.0
    start_equity: float = 3000.0
    leverage: float = 10.0
    max_positions: int = 2
    max_total_notional: float = 18000.0
    max_daily_loss: float = 225.0
    max_trades_day: int = 4
    stop_cap_pct: dict = field(default_factory=dict)  # coin -> max stop %, e.g. {'BTC':1.2}
    default_stop_cap: float = 2.2
    tf_minutes: int = 60


@dataclass
class Trade:
    coin: str; side: int
    entry_time: pd.Timestamp; entry_price: float
    exit_time: pd.Timestamp; exit_price: float
    notional: float; risk_usd: float
    pnl_usd: float; r_multiple: float; bars_held: int
    reasons: str; equity_after: float


def prepare(df: pd.DataFrame, cfg: Cfg) -> pd.DataFrame:
    """Add all indicators, each shifted so a row only sees PAST bars (no lookahead)."""
    d = df.copy()
    h, l, c = d["High"], d["Low"], d["Close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1.0 / cfg.atr_len, adjust=False, min_periods=cfg.atr_len).mean()
    d["atr_pct"] = d["atr"] / c * 100.0
    d["ema"] = c.ewm(span=cfg.ema_len, adjust=False, min_periods=cfg.ema_len).mean()
    d["ema_rising"] = d["ema"] > d["ema"].shift(cfg.ema_slope_lb)
    d["ema_falling"] = d["ema"] < d["ema"].shift(cfg.ema_slope_lb)
    d["don_hi"] = h.rolling(cfg.don_entry).max().shift(1)   # prior-N high (excl. current bar)
    d["don_lo"] = l.rolling(cfg.don_entry).min().shift(1)
    d["exit_hi"] = h.rolling(cfg.don_exit).max().shift(1)   # opposite-channel for emergency exit
    d["exit_lo"] = l.rolling(cfg.don_exit).min().shift(1)
    d["volsma"] = d["Volume"].rolling(cfg.vol_sma).mean()
    d["ret24"] = c / c.shift(24) - 1.0                       # 24h relative strength (ranking)
    return d


def _signal(row, cfg: Cfg):
    """Return (+1/-1/0, strength_dict) for the bar's CLOSE. Long/short breakout w/ filters."""
    if not np.isfinite(row.atr) or row.atr <= 0 or not np.isfinite(row.ema):
        return 0, None
    if not np.isfinite(row.don_hi) or not np.isfinite(row.volsma) or row.volsma <= 0:
        return 0, None
    if row.atr_pct <= cfg.atr_min_pct:
        return 0, None
    if cfg.vol_mult > 0 and row.Volume <= cfg.vol_mult * row.volsma:
        return 0, None
    side = 0
    if row.Close > row.don_hi and row.Close > row.ema and row.ema_rising:
        side = +1
    elif row.Close < row.don_lo and row.Close < row.ema and row.ema_falling:
        side = -1
    if side == 0:
        return 0, None
    # ranking strengths
    strength = dict(
        vol_exp=row.Volume / row.volsma,
        ema_dist=abs(row.Close - row.ema) / row.atr,
        rs=abs(row.ret24) if np.isfinite(row.ret24) else 0.0,
        body=abs(row.Close - row.Open) / row.atr,
    )
    return side, strength


def _rank_key(cand):
    s = cand["strength"]
    # primary vol expansion, then EMA distance, then 24h RS, then body
    return (s["vol_exp"], s["ema_dist"], s["rs"], s["body"])


class _Pos:
    __slots__ = ("coin", "side", "entry_i", "entry_time", "entry_price", "atr0", "stop_dist",
                 "R", "tp1", "tp2", "qty", "notional", "risk_usd", "rem", "peak", "trail",
                 "tp1_done", "tp2_done", "gross", "fees", "reasons")

    def __init__(self, coin, side, i, t, px, atr0, stop_dist, qty, notional, risk_usd, cfg):
        self.coin, self.side, self.entry_i, self.entry_time, self.entry_price = coin, side, i, t, px
        self.atr0, self.stop_dist, self.R = atr0, stop_dist, stop_dist
        self.tp1 = px + side * cfg.tp1_r * stop_dist
        self.tp2 = px + side * cfg.tp2_r * stop_dist
        self.qty, self.notional, self.risk_usd = qty, notional, risk_usd
        self.rem = 1.0
        self.peak = px
        self.trail = px - side * stop_dist        # initial hard stop level
        self.tp1_done = self.tp2_done = False
        self.gross = 0.0; self.fees = 0.0; self.reasons = []


def simulate(coins_data: dict, cfg: Cfg, costs: Costs):
    """coins_data: {coin -> prepared df on a 1H index}. Returns (trades, equity_curve)."""
    slip = costs.slippage_pct / 100.0
    taker = costs.taker_pct / 100.0
    maker = costs.maker_pct / 100.0
    times = sorted(set().union(*[set(df.index) for df in coins_data.values()]))
    rows = {c: {t: r for t, r in zip(df.index, df.itertuples())} for c, df in coins_data.items()}

    equity = cfg.start_equity
    positions: dict[str, _Pos] = {}
    pending: list[dict] = []        # decided at prev close, fill at this open
    day_loss: dict = {}             # date -> realized pnl (negative = loss)
    day_trades: dict = {}
    trades: list[Trade] = []
    curve = [(times[0], equity)] if times else []

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
        entry_fee = pos.notional * taker
        pnl = pos.gross - pos.fees - entry_fee - funding
        equity += pnl
        d = t.date()
        day_loss[d] = day_loss.get(d, 0.0) + pnl
        bars = int(round(hours / (cfg.tf_minutes / 60.0)))
        trades.append(Trade(pos.coin, pos.side, pos.entry_time, pos.entry_price, t, last_price,
                            pos.notional, pos.risk_usd, pnl, pnl / pos.risk_usd if pos.risk_usd else 0.0,
                            bars, "+".join(pos.reasons), equity))

    for t in times:
        # ---------- A) FILL pending entries at this bar's OPEN ----------
        still = []
        for e in pending:
            c = e["coin"]
            if c in positions or len(positions) >= cfg.max_positions:
                continue                      # slot gone
            d = t.date()
            if day_loss.get(d, 0.0) <= -cfg.max_daily_loss:
                continue
            if day_trades.get(d, 0) >= cfg.max_trades_day:
                continue
            r = rows[c].get(t)
            if r is None or not np.isfinite(r.atr) or r.atr <= 0:
                continue
            side = e["side"]
            raw = r.Open
            entry_px = raw * (1 + slip) if side > 0 else raw * (1 - slip)
            # stop: 1.5*ATR capped by per-coin max %
            cap = cfg.stop_cap_pct.get(c, cfg.default_stop_cap) / 100.0
            stop_dist = min(cfg.atr_stop * r.atr, cap * entry_px)
            if stop_dist <= 0:
                continue
            stop_pct = stop_dist / entry_px
            notional = cfg.risk_usd / stop_pct
            open_notional = sum(p.notional for p in positions.values())
            room = cfg.max_total_notional - open_notional
            if room <= 0:
                continue
            if notional > room:
                notional = room                       # clip; risk shrinks with it
            risk_usd = notional * stop_pct
            qty = notional / entry_px
            positions[c] = _Pos(c, side, e["i"], t, entry_px, r.atr, stop_dist, qty,
                                notional, risk_usd, cfg)
            day_trades[d] = day_trades.get(d, 0) + 1
        pending = still

        # ---------- B) PROCESS exits for open positions on this bar ----------
        for c in list(positions.keys()):
            pos = positions[c]
            r = rows[c].get(t)
            if r is None:
                continue
            hi, lo, cl = r.High, r.Low, r.Close
            side = pos.side
            done = False
            # 1) hard/trail stop (conservative: checked before TPs on a both-hit bar)
            hit_stop = (lo <= pos.trail) if side > 0 else (hi >= pos.trail)
            if hit_stop:
                px = pos.trail * (1 - slip) if side > 0 else pos.trail * (1 + slip)
                reason = "trail" if (pos.tp1_done or abs(pos.trail - (pos.entry_price - side * pos.stop_dist)) > 1e-9) else "stop"
                close_leg(pos, pos.rem, px, taker, reason)
                finalize(pos, t, px); del positions[c]; continue
            # 2) TP1
            if not pos.tp1_done:
                hit = (hi >= pos.tp1) if side > 0 else (lo <= pos.tp1)
                if hit:
                    close_leg(pos, cfg.tp1_frac, pos.tp1, maker, "tp1"); pos.tp1_done = True
            # 3) TP2
            if pos.tp1_done and not pos.tp2_done:
                hit = (hi >= pos.tp2) if side > 0 else (lo <= pos.tp2)
                if hit:
                    close_leg(pos, cfg.tp2_frac, pos.tp2, maker, "tp2"); pos.tp2_done = True
            # 4) emergency: close beyond opposite Donchian-10
            opp = r.exit_lo if side > 0 else r.exit_hi
            if np.isfinite(opp):
                breached = (cl < opp) if side > 0 else (cl > opp)
                if breached and pos.rem > 0:
                    px = cl * (1 - slip) if side > 0 else cl * (1 + slip)
                    close_leg(pos, pos.rem, px, taker, "donch"); finalize(pos, t, px); del positions[c]; done = True
            if done:
                continue
            # 5) ratchet the close-based trail for next bars
            if side > 0:
                pos.peak = max(pos.peak, cl)
                pos.trail = max(pos.trail, pos.peak - cfg.atr_trail * pos.atr0)
            else:
                pos.peak = min(pos.peak, cl)
                pos.trail = min(pos.trail, pos.peak + cfg.atr_trail * pos.atr0)
            if pos.rem <= 1e-9:                 # both TPs filled, no runner frac left
                finalize(pos, t, pos.tp2); del positions[c]

        # ---------- C) at CLOSE, scan + rank new signals, queue for next bar ----------
        slots = cfg.max_positions - len(positions)
        d = t.date()
        blocked = (day_loss.get(d, 0.0) <= -cfg.max_daily_loss) or (day_trades.get(d, 0) >= cfg.max_trades_day)
        if slots > 0 and not blocked:
            cands = []
            for c, df_rows in rows.items():
                if c in positions:
                    continue
                r = df_rows.get(t)
                if r is None:
                    continue
                side, strength = _signal(r, cfg)
                if side != 0:
                    cands.append(dict(coin=c, side=side, strength=strength, i=0, t=t))
            cands.sort(key=_rank_key, reverse=True)
            pending = cands[:slots]
        else:
            pending = []
        curve.append((t, equity))

    return trades, curve
