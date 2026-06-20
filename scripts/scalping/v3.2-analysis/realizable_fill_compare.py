"""Realizable-fill comparison — the core entry-parity experiment.

The engine (`run_bt` / `run_v3_backtest`) fills a retest at `ema[i]`: the EMA9
of the SAME bar whose wick it tests. That value isn't known until bar i closes,
so the idealized fill is mildly look-ahead-biased (the "phantom fill"). This
script holds the SIGNAL and the EXIT identical and swaps ONLY the entry-fill
mechanism, then measures each realizable mechanism against the idealized engine
(M0) on the live coin basket:

  M0  IDEAL    : engine as-is — fill at ema[i], overshoot-gated (the target).
  M1  REST     : resting limit at the LAST CLOSED bar's EMA9 (ema[i-1]); fills
                 when bar i's wick reaches it. No overshoot reject (a resting
                 limit fills on knives too). This is what the live poller does.
  M2  REST+GRD : M1 but skip the fill when the bar overshoots the limit by
                 >0.2% (a perfect-guard ceiling — measures the cost of knives).
  M3  MKT-RTST : when a CLOSED bar confirms the retest, market-enter at the next
                 bar's open. Fully realizable, no phantom, but no EMA9 price.

For each mechanism: trade count, overlap vs M0 (matched / extra / missed by
side+time), net P&L, and the mean entry-price gap vs M0. Exit = trail machine,
filters = F_LIVE, so only the ENTRY differs.

Run:
    PYTHONPATH="analysis;v3.1-drafts;analysis/sweeps/2026-05-20" \
        venv/Scripts/python.exe v3.2-analysis/realizable_fill_compare.py [days] [coins...]
"""
import sys
import numpy as np
import pandas as pd

from engine import fetch_ohlcv
from zec_v3_realistic import (
    generate_v3_signals, apply_entry_filter, simulate_trade,
    _check_retest, RETEST_TIMEOUT_BARS, MIN_SLOPE_PCT, RETEST_OVERSHOOT_PCT,
)
from v3_2_lab import (run_bt, ExitModel, F_LIVE, F_TREND, F_NOFILT,
                      base_params, _simulate, kpis as lab_kpis)
from zec_v3_realistic import EntryFilters
from dataclasses import replace as _replace

LIVE_COINS = ["BNB-USDT", "BTC-USDT", "DOGE-USDT", "HYPE-USDT",
              "SOL-USDT", "XRP-USDT", "ZEC-USDT"]
MATCH = pd.Timedelta(minutes=12)   # entry-time tolerance for matching to M0


def _fee_for(entry_price, exit_price, p):
    notional_in = p.margin_usdt * p.leverage
    notional_out = (exit_price / entry_price) * notional_in
    return (notional_in + notional_out) * p.commission_pct


def run_realizable(df, p, filters, fill_model: str, max_lookahead_bars=288):
    """Bar-walk with pending queue (parity with run_bt) but a swappable fill.

    fill_model:
      'rest'     -> limit at ema[i-1], fill if wick reaches it, no guard
      'rest_grd' -> as 'rest' but skip when bar overshoots limit by >0.2%
      'mkt'      -> on confirmed retest at bar i, enter at open[i+1]
      'limit_confirm' -> on confirmed retest at bar i, arm a limit at ema[i]
                         (known once bar i closed); fill at ema[i] if price
                         returns to it within LIMIT_LIFE bars. Hybrid: M3's
                         selection + M2's good price.
    """
    LIMIT_LIFE = 2
    buy_sig = df["buy_sig"].values
    sell_sig = df["sell_sig"].values
    opens = df["Open"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    closes = df["Close"].values.astype(float)
    ema = df["ema9"].values
    slope = df["slope_pct"].values
    adx = df["adx"].values
    body_a = df["body_atr_ratio"].values
    ts = df.index
    n = len(df)
    over = RETEST_OVERSHOOT_PCT / 100.0

    trades = []
    pending = []          # (signal_bar_idx, side)
    blocked_until = -1
    for i in range(n):
        new_pending = []
        for sig_i, side in pending:
            if i - sig_i > RETEST_TIMEOUT_BARS:
                continue
            if i < 1:
                new_pending.append((sig_i, side)); continue

            limit = ema[i - 1]                      # last CLOSED bar's EMA9
            if np.isnan(limit):
                new_pending.append((sig_i, side)); continue

            filled = False
            entry_price = None
            if fill_model in ("rest", "rest_grd", "rest_rguard"):
                # M7 realizable guard: only ARM the limit when the last CLOSED
                # bar hasn't already broken through EMA9 past the overshoot band
                # (reject breakdowns at bar boundaries — the only knife-rejection
                # a passive limit can actually do). Intrabar knives still slip
                # through; this measures how many are catchable realizably.
                if fill_model == "rest_rguard":
                    prev_c = closes[i - 1]
                    broke = (prev_c < limit * (1 - over)) if side == "long" \
                        else (prev_c > limit * (1 + over))
                    if broke:
                        new_pending.append((sig_i, side)); continue
                if side == "long":
                    touch = lows[i] <= limit
                    knife = lows[i] < limit * (1 - over)
                else:
                    touch = highs[i] >= limit
                    knife = highs[i] > limit * (1 + over)
                if touch:
                    if fill_model == "rest_grd" and knife:
                        # perfect-guard (ceiling, not realizable): skip the knife
                        new_pending.append((sig_i, side)); continue
                    filled = True
                    entry_price = limit            # resting limit fills at EMA9
            elif fill_model == "mkt":
                # confirm the retest on THIS closed bar (engine's _check_retest),
                # then market-enter at the next bar's open.
                if _check_retest(side, ema[i], lows[i], highs[i]) and i + 1 < n:
                    filled = True
                    entry_price = opens[i + 1]
            elif fill_model == "reclaim":
                # touch-and-reclaim: the bar wicked to EMA9 (within band) AND
                # closed back on the trade's side of EMA9 (a confirmed bounce,
                # not a breakdown). Market-enter at that close. Selects only the
                # bounces and fills just past EMA9 (small gap).
                touched = _check_retest(side, ema[i], lows[i], highs[i])
                reclaimed = (closes[i] > ema[i]) if side == "long" else (closes[i] < ema[i])
                if touched and reclaimed:
                    filled = True
                    entry_price = closes[i]
            else:
                raise ValueError(fill_model)

            if not filled:
                new_pending.append((sig_i, side)); continue
            # slope gate (same as engine, measured at bar i)
            if abs(slope[i]) < MIN_SLOPE_PCT:
                new_pending.append((sig_i, side)); continue
            if i <= blocked_until:
                new_pending.append((sig_i, side)); continue
            if filters is not None:
                _adx = float(adx[i]) if not np.isnan(adx[i]) else 0.0
                if not filters.passes(ts[i], float(slope[i]), float(body_a[i]), _adx):
                    continue

            # exit sim from the bar AFTER entry (mkt enters at open[i+1] so its
            # forward bars start at i+1 too; close enough for a fill-mechanism
            # comparison since the trail machine dominates the outcome).
            start = i + 1
            j_end = min(start + max_lookahead_bars, n)
            bars = [(int(ts[j].timestamp()), opens[j], highs[j], lows[j], closes[j])
                    for j in range(start, j_end)]
            res = simulate_trade(side, entry_price, bars, p, ordering="avg")
            fee = _fee_for(entry_price, res.exit_price, p)
            trades.append(dict(
                side=side, entry_ts=ts[i], entry_price=entry_price,
                pnl_usdt=res.pnl_usdt, pnl_net=res.pnl_usdt - fee,
                duration_bars=res.duration_bars,
            ))
            blocked_until = i + max(1, res.duration_bars)
            new_pending = [(s, sd) for (s, sd) in new_pending if sd != side]
        pending = new_pending
        if buy_sig[i]:
            pending.append((i, "long"))
        if sell_sig[i]:
            pending.append((i, "short"))
    return pd.DataFrame(trades)


def run_limit_confirm(df, p, filters, limit_life=2, market_fallback=False,
                      max_lookahead_bars=288):
    """Hybrid M4: select trades like M3 (engine retest confirmed on a CLOSED
    bar i), but instead of market-entering at open[i+1], ARM a limit at ema[i]
    — the engine's own fill price, now known because bar i has closed — and fill
    at ema[i] if price returns to it within `limit_life` bars. Captures the
    engine's good price without the phantom (we never fill in the past).

    market_fallback=True (M5): if the limit hasn't filled by the time it
    expires, market-enter at that bar's open instead of dropping — so the
    runners (price ran away from EMA9 = the winners a pure limit misses) are
    still caught, just at a worse price. Combines M4's price on stalls with
    M3's coverage on runners."""
    buy_sig = df["buy_sig"].values
    sell_sig = df["sell_sig"].values
    opens = df["Open"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    closes = df["Close"].values.astype(float)
    ema = df["ema9"].values
    slope = df["slope_pct"].values
    adx = df["adx"].values
    body_a = df["body_atr_ratio"].values
    ts = df.index
    n = len(df)

    trades = []
    pending = []            # (signal_bar_idx, side)
    armed = []              # (price, side, armed_at)
    blocked_until = -1
    for i in range(n):
        # 1. Try to fill any armed limits first.
        still_armed = []
        for price, side, armed_at in armed:
            expired = i - armed_at > limit_life
            if expired and not market_fallback:
                continue    # limit expired unfilled
            if i <= blocked_until:
                if not expired:
                    still_armed.append((price, side, armed_at))
                continue
            hit = (lows[i] <= price) if side == "long" else (highs[i] >= price)
            if expired and market_fallback:
                entry_price = opens[i]          # market in — catch the runner
            elif hit:
                entry_price = price             # limit filled at EMA9
            else:
                still_armed.append((price, side, armed_at)); continue
            start = i + 1
            j_end = min(start + max_lookahead_bars, n)
            bars = [(int(ts[j].timestamp()), opens[j], highs[j], lows[j], closes[j])
                    for j in range(start, j_end)]
            res = simulate_trade(side, entry_price, bars, p, ordering="avg")
            fee = _fee_for(entry_price, res.exit_price, p)
            trades.append(dict(
                side=side, entry_ts=ts[i], entry_price=entry_price,
                pnl_usdt=res.pnl_usdt, pnl_net=res.pnl_usdt - fee,
                duration_bars=res.duration_bars,
            ))
            blocked_until = i + max(1, res.duration_bars)
        armed = still_armed

        # 2. Confirm retests on this closed bar -> arm a limit at ema[i].
        new_pending = []
        for sig_i, side in pending:
            if i - sig_i > RETEST_TIMEOUT_BARS:
                continue
            if not _check_retest(side, ema[i], lows[i], highs[i]):
                new_pending.append((sig_i, side)); continue
            if abs(slope[i]) < MIN_SLOPE_PCT:
                new_pending.append((sig_i, side)); continue
            if filters is not None:
                _adx = float(adx[i]) if not np.isnan(adx[i]) else 0.0
                if not filters.passes(ts[i], float(slope[i]), float(body_a[i]), _adx):
                    continue
            armed.append((float(ema[i]), side, i))
            new_pending = [(s, sd) for (s, sd) in new_pending if sd != side]
        pending = new_pending
        if buy_sig[i]:
            pending.append((i, "long"))
        if sell_sig[i]:
            pending.append((i, "short"))
    return pd.DataFrame(trades)


def overlap(ref: pd.DataFrame, cand: pd.DataFrame):
    """Match cand trades to ref (M0) by side + entry-time tolerance."""
    if ref.empty:
        return 0, len(cand), 0, 0.0
    used = [False] * len(cand)
    matched = 0
    eprice_gap = []
    cand = cand.reset_index(drop=True)
    for _, r in ref.iterrows():
        for j, c in cand.iterrows():
            if not used[j] and c.side == r.side and abs(c.entry_ts - r.entry_ts) <= MATCH:
                used[j] = True
                matched += 1
                eprice_gap.append(abs(c.entry_price - r.entry_price) / r.entry_price * 100)
                break
    extra = sum(1 for u in used if not u)
    missed = len(ref) - matched
    gap = float(np.mean(eprice_gap)) if eprice_gap else 0.0
    return matched, extra, missed, gap


MODELS = ("M1_rest", "M2_rest_grd", "M3_mkt", "M4_limit_confirm",
          "M5_limit_fallback", "M6_reclaim", "M7_rest_rguard")


def _run_model(df, p, key):
    if key == "M1_rest":
        return run_realizable(df, p, F_LIVE, "rest")
    if key == "M2_rest_grd":
        return run_realizable(df, p, F_LIVE, "rest_grd")
    if key == "M3_mkt":
        return run_realizable(df, p, F_LIVE, "mkt")
    if key == "M4_limit_confirm":
        return run_limit_confirm(df, p, F_LIVE, limit_life=2)
    if key == "M5_limit_fallback":
        return run_limit_confirm(df, p, F_LIVE, limit_life=1, market_fallback=True)
    if key == "M6_reclaim":
        return run_realizable(df, p, F_LIVE, "reclaim")
    if key == "M7_rest_rguard":
        return run_realizable(df, p, F_LIVE, "rest_rguard")
    raise ValueError(key)


def _pass(days, coins, fee, slip, label):
    p = base_params(sl=82.5, fee=fee, slip=slip)
    em = ExitModel("trail")
    print(f"\n{'='*78}\n{label}  (window={days}d, exit=trail, filters=F_LIVE)\n{'='*78}")
    agg = {m: dict(n=0, net=0.0, matched=0, extra=0, missed=0, gaps=[]) for m in MODELS}
    m0_total = 0
    m0_net = 0.0
    print(f"{'coin':9s} {'M0n':>4s} {'M0net':>7s} | " + " | ".join(
        f"{m:>13s}" for m in ("M1 rest", "M2 grd", "M3 mkt", "M4 limit",
                              "M5 lim+fb", "M6 reclaim")))
    print(f"{'':9s} {'':>4s} {'':>7s} | " + " | ".join(
        f"{'n/mt/x/ms':>13s}" for _ in MODELS))
    for c in coins:
        df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                         days_back=days, exchange="blofin", cache=False, verbose=False)
        df = apply_entry_filter(generate_v3_signals(df.copy()))
        m0 = run_bt(df, p, em, F_LIVE)
        m0n = float(m0.pnl_net.sum()) if not m0.empty else 0.0
        m0_total += len(m0); m0_net += m0n
        cells = []
        for key in MODELS:
            t = _run_model(df, p, key)
            mt, x, ms, gap = overlap(m0, t)
            net = float(t.pnl_net.sum()) if not t.empty else 0.0
            a = agg[key]
            a["n"] += len(t); a["net"] += net
            a["matched"] += mt; a["extra"] += x; a["missed"] += ms
            if gap > 0:
                a["gaps"].append(gap)
            cells.append(f"{len(t):3d}/{mt:3d}/{x:2d}/{ms:2d}")
        print(f"{c:9s} {len(m0):4d} {m0n:7.0f} | " + " | ".join(f"{x:>13s}" for x in cells))

    print(f"\nM0 (engine ideal): {m0_total} trades, net ${m0_net:+.0f}\n")
    print(f"{'mech':17s} {'n':>4s} {'matched':>7s} {'extra':>5s} {'missed':>6s} "
          f"{'net$':>8s} {'%ofM0':>6s} {'eprice gap%':>11s}")
    for key in MODELS:
        a = agg[key]
        g = float(np.mean(a["gaps"])) if a["gaps"] else 0.0
        pct = (a["net"] / m0_net * 100) if m0_net else 0.0
        print(f"{key:17s} {a['n']:4d} {a['matched']:7d} {a['extra']:5d} "
              f"{a['missed']:6d} {a['net']:8.0f} {pct:5.0f}% {g:11.3f}")
    return m0_net, agg


def run_m3_exit(df, p, filters, em: ExitModel, max_lookahead_bars=288):
    """M3 entry (market on confirmed EMA9 retest, fill at open[i+1]) with a
    pluggable exit model — to test whether bigger-target exits rescue the
    realizable edge that the tight 5m trail leaves exposed to entry slippage."""
    buy_sig = df["buy_sig"].values; sell_sig = df["sell_sig"].values
    opens = df["Open"].values.astype(float); highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float); closes = df["Close"].values.astype(float)
    ema = df["ema9"].values; slope = df["slope_pct"].values
    adx = df["adx"].values; body_a = df["body_atr_ratio"].values
    ts = df.index; n = len(df)
    trades = []; pending = []; blocked_until = -1
    for i in range(n):
        new_pending = []
        for sig_i, side in pending:
            if i - sig_i > RETEST_TIMEOUT_BARS:
                continue
            if not (_check_retest(side, ema[i], lows[i], highs[i]) and i + 1 < n):
                new_pending.append((sig_i, side)); continue
            if abs(slope[i]) < MIN_SLOPE_PCT:
                new_pending.append((sig_i, side)); continue
            if i <= blocked_until:
                new_pending.append((sig_i, side)); continue
            if filters is not None:
                _adx = float(adx[i]) if not np.isnan(adx[i]) else 0.0
                if not filters.passes(ts[i], float(slope[i]), float(body_a[i]), _adx):
                    continue
            entry_price = opens[i + 1]
            j_end = min(i + 1 + max_lookahead_bars, n)
            bars = [(int(ts[j].timestamp()), opens[j], highs[j], lows[j], closes[j])
                    for j in range(i + 1, j_end)]
            pnl, _reason, dur = _simulate(side, entry_price, bars, p, em)
            fee = _fee_for(entry_price, entry_price * (1 + pnl / (p.margin_usdt * p.leverage)), p)
            trades.append(dict(side=side, entry_ts=ts[i], entry_price=entry_price,
                               pnl_usdt=pnl, pnl_net=pnl - fee, duration_bars=dur))
            blocked_until = i + max(1, dur)
            new_pending = [(s, sd) for (s, sd) in new_pending if sd != side]
        pending = new_pending
        if buy_sig[i]:
            pending.append((i, "long"))
        if sell_sig[i]:
            pending.append((i, "short"))
    return pd.DataFrame(trades)


def mode_exit_sensitivity(days, coins):
    """Does letting winners run rescue the realizable (M3) edge? Compare ideal
    (M0) vs realizable (M3) net under several exits, zero-fee."""
    p = base_params(sl=82.5, fee=0.0, slip=0.0)
    exits = [
        ("trail (V3.1)", ExitModel("trail")),
        ("fixedR 2.5R", ExitModel("fixedR", r_mult=2.5)),
        ("fixedR 3R +BE@1R", ExitModel("fixedR", r_mult=3.0, use_be=True, be_r=1.0)),
        ("fixedR 4R +BE@1R", ExitModel("fixedR", r_mult=4.0, use_be=True, be_r=1.0)),
        ("partial .5@1R run1.5R", ExitModel("partial", r1=1.0, partial_frac=0.5, runner_trail_r=1.5)),
    ]
    print(f"\n{'='*78}\nEXIT SENSITIVITY (zero-fee): does letting winners run beat "
          f"the ~$26/trade\nentry slippage? window={days}d, M3 entry vs M0 ideal\n{'='*78}")
    dfs = {}
    for c in coins:
        d = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m", days_back=days,
                        exchange="blofin", cache=False, verbose=False)
        dfs[c] = apply_entry_filter(generate_v3_signals(d.copy()))
    print(f"{'exit':24s} {'M0 net':>9s} {'M3 net':>9s} {'M3 %ofM0':>9s} {'slip$/t':>8s}")
    for lbl, em in exits:
        m0net = m3net = 0.0; m3n = 0
        for c in coins:
            m0 = run_bt(dfs[c], p, em, F_LIVE)
            m3 = run_m3_exit(dfs[c], p, F_LIVE, em)
            m0net += float(m0.pnl_net.sum()) if not m0.empty else 0.0
            m3net += float(m3.pnl_net.sum()) if not m3.empty else 0.0
            m3n += len(m3)
        pct = (m3net / m0net * 100) if m0net else 0.0
        slip = (m0net - m3net) / m3n if m3n else 0.0
        print(f"{lbl:24s} {m0net:9.0f} {m3net:9.0f} {pct:8.0f}% {slip:8.1f}")
    print("\nread: if a bigger-target exit lifts M3 %ofM0 toward 100%, the edge "
          "survives realistic fills there. if every exit stays low, the EMA9 "
          "phantom fill — not the exit — is the edge, and it's unrealizable.")


def _trail_net(df, p, filters, entry_mode, ordering, max_lookahead_bars=288):
    """Trail-exit net with a configurable intrabar ORDERING — to stress-test the
    high-leverage result against the pessimistic (stop-hits-first) path instead
    of the engine's optimistic avg. entry_mode: 'ideal' (M0, fill ema[i]) or
    'm3' (fill open[i+1] on confirmed retest)."""
    buy_sig = df["buy_sig"].values; sell_sig = df["sell_sig"].values
    opens = df["Open"].values.astype(float); highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float); closes = df["Close"].values.astype(float)
    ema = df["ema9"].values; slope = df["slope_pct"].values
    adx = df["adx"].values; body_a = df["body_atr_ratio"].values
    ts = df.index; n = len(df)
    net = 0.0; ntr = 0; pending = []; blocked_until = -1
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
            if entry_mode == "ideal":
                entry_price = float(ema[i]); start = i + 1
            else:
                if i + 1 >= n:
                    continue
                entry_price = opens[i + 1]; start = i + 1
            j_end = min(start + max_lookahead_bars, n)
            bars = [(int(ts[j].timestamp()), opens[j], highs[j], lows[j], closes[j])
                    for j in range(start, j_end)]
            res = simulate_trade(side, entry_price, bars, p, ordering=ordering)
            fee = _fee_for(entry_price, res.exit_price, p)
            net += res.pnl_usdt - fee; ntr += 1
            blocked_until = i + max(1, res.duration_bars)
            new_pending = [(s, sd) for (s, sd) in new_pending if sd != side]
        pending = new_pending
        if buy_sig[i]:
            pending.append((i, "long"))
        if sell_sig[i]:
            pending.append((i, "short"))
    return net, ntr


def mode_leverage_pessimistic(days, coins, slip=0.0006):
    """The decisive artifact test: redo the fixed-$ leverage sweep under the
    PESSIMISTIC intrabar ordering (adverse move hits first) with realistic stop
    slippage. If the high-leverage M3 edge survives this, it may be real."""
    from dataclasses import replace as _r
    base = base_params(sl=82.5, fee=0.0, slip=slip)
    levs = [30, 50, 75, 100, 150]
    dfs = {}
    for c in coins:
        d = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m", days_back=days,
                        exchange="blofin", cache=False, verbose=False)
        dfs[c] = apply_entry_filter(generate_v3_signals(d.copy()))
    print(f"\n{'='*78}\nLEVERAGE x ORDERING stress (fixed-$ stop, slip={slip}, zero-fee, "
          f"{days}d)\nM3 net under optimistic-avg vs pessimistic(adv-first) intrabar "
          f"path\n{'='*78}")
    print(f"{'lev':>4s} {'SL%':>6s} | {'M0 avg':>8s} {'M3 avg':>8s} | "
          f"{'M0 pess':>8s} {'M3 pess':>8s}  {'M3pess%':>7s}")
    for L in levs:
        p = _r(base, leverage=L)
        sl_pct = p.sl_loss_usdt / (p.margin_usdt * L) * 100
        m0a = m3a = m0p = m3p = 0.0; m3pn = 0
        for c in coins:
            a0, _ = _trail_net(dfs[c], p, F_LIVE, "ideal", "avg")
            a3, _ = _trail_net(dfs[c], p, F_LIVE, "m3", "avg")
            p0, _ = _trail_net(dfs[c], p, F_LIVE, "ideal", "adv_first")
            p3, n3 = _trail_net(dfs[c], p, F_LIVE, "m3", "adv_first")
            m0a += a0; m3a += a3; m0p += p0; m3p += p3; m3pn += n3
        pp = (m3p / m0p * 100) if m0p else 0.0
        print(f"{L:4d} {sl_pct:5.2f}% | {m0a:8.0f} {m3a:8.0f} | "
              f"{m0p:8.0f} {m3p:8.0f}  {pp:6.0f}%")
    print("\nread: M3 pess > 0 across leverage = the high-lev edge isn't just the "
          "optimistic-fill artifact. M3 pess << 0 = it was sub-bar fantasy.")


def _scale_params(p, leverage):
    """Constant %-risk: hold the SL/trail PRICE distances fixed as leverage
    changes by scaling every $-threshold proportionally with leverage."""
    from dataclasses import replace as _r
    k = leverage / p.leverage
    return _r(p, leverage=leverage,
              sl_loss_usdt=p.sl_loss_usdt * k,
              breakeven_usdt=p.breakeven_usdt * k,
              lock_profit_activate_usdt=p.lock_profit_activate_usdt * k,
              lock_profit_usdt=p.lock_profit_usdt * k,
              trail_activate_usdt=p.trail_activate_usdt * k,
              trail_start_usdt=p.trail_start_usdt * k,
              trail_distance_usdt=p.trail_distance_usdt * k)


def mode_leverage(days, coins, slip=0.0):
    """Does higher leverage help reach a profitable engine-replica? Sweep
    leverage two ways, zero-fee, M3 (best realizable entry) vs M0 ideal:
      A FIXED-$ stop  : keep $82.5 SL — stop tightens in % as leverage rises
                        (the live-config behavior).
      B %-CONSTANT    : scale $ thresholds with leverage — stop % held fixed.
    margin held at $250; notional = margin x leverage.

    `slip` = stop-fill slippage %. At slip=0 the high-leverage edge is sub-bar
    fantasy; rerun with slip~0.0006 to see if it survives realistic exits."""
    base = base_params(sl=82.5, fee=0.0, slip=slip)   # zero-fee Lighter endgame
    em = ExitModel("trail")
    levs = [10, 20, 30, 50, 75, 100, 150]
    dfs = {}
    for c in coins:
        d = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m", days_back=days,
                        exchange="blofin", cache=False, verbose=False)
        dfs[c] = apply_entry_filter(generate_v3_signals(d.copy()))

    print(f"\n{'='*78}\nLEVERAGE SWEEP (zero-fee, margin=$250, M3 entry vs M0 ideal, "
          f"window={days}d)\n{'='*78}")
    for label, scale in (("A  FIXED-$ stop ($82.5 SL, % tightens w/ leverage)", False),
                         ("B  %-CONSTANT stop ($ thresholds scaled w/ leverage)", True)):
        from dataclasses import replace as _r
        print(f"\n--- {label} ---")
        print(f"{'lev':>4s} {'notional':>9s} {'SL%':>6s} {'M0 net':>9s} "
              f"{'M3 net':>9s} {'M3 %ofM0':>9s} {'slip$/t':>8s}")
        for L in levs:
            p = _scale_params(base, L) if scale else _r(base, leverage=L)
            sl_pct = p.sl_loss_usdt / (p.margin_usdt * L) * 100
            m0net = m3net = 0.0; m3n = 0
            for c in coins:
                m0 = run_bt(dfs[c], p, em, F_LIVE)
                m3 = run_m3_exit(dfs[c], p, F_LIVE, em)
                m0net += float(m0.pnl_net.sum()) if not m0.empty else 0.0
                m3net += float(m3.pnl_net.sum()) if not m3.empty else 0.0
                m3n += len(m3)
            pct = (m3net / m0net * 100) if m0net else 0.0
            slip = (m0net - m3net) / m3n if m3n else 0.0
            print(f"{L:4d} {p.margin_usdt*L:9.0f} {sl_pct:5.2f}% {m0net:9.0f} "
                  f"{m3net:9.0f} {pct:8.0f}% {slip:8.1f}")
    print("\nread: if NO leverage makes M3 net positive, leverage scales edge AND "
          "slippage together — it can't manufacture an edge the fill destroys.")


def mode_honest_engine(days, coins, leverage=30, fee=0.0, slip=0.0006):
    """Build an engine whose fill is CAUSAL (M3: market on confirmed EMA9
    retest, fill at open[i+1]) and re-optimize the whole strategy on it. If a
    config is solidly +EV here, its trades are takeable live by construction.
    Sweeps SL x exit x sides x filter; ranks by realizable net."""
    from dataclasses import replace as _r
    base = _r(base_params(sl=82.5, fee=fee, slip=slip), leverage=leverage)
    dfs = {}
    for c in coins:
        d = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m", days_back=days,
                        exchange="blofin", cache=False, verbose=False)
        dfs[c] = apply_entry_filter(generate_v3_signals(d.copy()))

    sls = [50.0, 82.5, 120.0]
    exits = [("trail", ExitModel("trail")),
             ("2.5R", ExitModel("fixedR", r_mult=2.5)),
             ("3R+BE", ExitModel("fixedR", r_mult=3.0, use_be=True, be_r=1.0)),
             ("part.5@1R+run1.5", ExitModel("partial", r1=1.0, partial_frac=0.5, runner_trail_r=1.5))]
    sides = [("both", ("long", "short")), ("short", ("short",)), ("long", ("long",))]
    filts = [("Flive", F_LIVE), ("Ftrend", F_TREND), ("Fnone", F_NOFILT)]

    feelbl = "zero-fee" if fee == 0 else f"fee{fee}"
    print(f"\n{'='*86}\nHONEST ENGINE SWEEP (M3 causal fill, lev={leverage}x, {feelbl}, "
          f"slip={slip}, {days}d, {len(coins)} coins)\n{'='*86}")
    rows = []
    for sl in sls:
        p = _r(base, sl_loss_usdt=sl)
        for elbl, em in exits:
            for slbl, sd in sides:
                for flbl, fl in filts:
                    allt = []
                    for c in coins:
                        t = run_m3_exit(dfs[c], p, fl, em)
                        if not t.empty:
                            t = t[t.side.isin(sd)]
                            allt.append(t)
                    tdf = pd.concat(allt, ignore_index=True) if allt else pd.DataFrame()
                    k = lab_kpis(tdf, p, days)
                    if k.get("n", 0) == 0:
                        continue
                    rows.append({"sl": sl, "exit": elbl, "side": slbl, "filt": flbl,
                                 "n": k["n"], "net": k["net"], "PF": k["PF"],
                                 "WR": k["WR"], "maxDD": k["maxDD"],
                                 "net/day": k.get("net_per_day", 0)})
    out = pd.DataFrame(rows).sort_values("net", ascending=False)
    print(f"{'sl':>5s} {'exit':17s} {'side':5s} {'filt':6s} {'n':>4s} {'net':>8s} "
          f"{'PF':>5s} {'WR':>5s} {'maxDD':>7s} {'net/day':>7s}")
    for _, r in out.head(15).iterrows():
        print(f"{r.sl:5.0f} {r['exit']:17s} {r['side']:5s} {r.filt:6s} {int(r.n):4d} "
              f"{r.net:8.0f} {r.PF:5.2f} {r.WR:5.2f} {r.maxDD:7.0f} {r['net/day']:7.1f}")
    pos = out[out.net > 0]
    print(f"\n{len(pos)}/{len(out)} configs are +EV under the causal fill. "
          f"best PF={out.PF.max():.2f}, best net=${out.net.max():.0f}")
    print("read: a robust +EV config here (good PF, n, low DD, holds across "
          "sides/filters) is a strategy live can actually execute. If the best "
          "is thin/fragile, the signal has no causal edge at this leverage.")
    return out


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "honest":
        d = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 50
        cs = [a for a in sys.argv[2:] if a.endswith("-USDT")] or LIVE_COINS
        lev = next((int(a[3:]) for a in sys.argv[2:] if a.startswith("lev")), 30)
        fee = 0.0006 if "fee" in sys.argv[2:] else 0.0
        mode_honest_engine(d, cs, leverage=lev, fee=fee)
        return
    if len(sys.argv) > 1 and sys.argv[1] in ("leverage", "leverage_pess"):
        d = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 50
        cs = [a for a in sys.argv[2:] if a.endswith("-USDT")] or LIVE_COINS
        slip = next((float(a) for a in sys.argv[2:] if a.replace(".", "", 1).isdigit()
                     and "." in a), 0.0)
        if sys.argv[1] == "leverage_pess":
            mode_leverage_pessimistic(d, cs, slip=slip or 0.0006)
        else:
            mode_leverage(d, cs, slip=slip)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "exits":
        d = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 50
        cs = [a for a in sys.argv[2:] if a.endswith("-USDT")] or LIVE_COINS
        mode_exit_sensitivity(d, cs)
        return
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 45
    coins = [a for a in sys.argv[1:] if a.endswith("-USDT")] or LIVE_COINS
    _pass(days, coins, fee=0.0006, slip=0.0006, label="REALISTIC BloFin (fee 0.06%/side, slip 0.06%)")
    _pass(days, coins, fee=0.0, slip=0.0, label="ZERO-FEE Lighter endgame (fee 0, slip 0)")
    print("\nread: highest matched + lowest extra + net closest to M0 = best "
          "realizable replica. eprice gap% = how far each fill sits from the "
          "engine's idealized EMA9 fill.")


if __name__ == "__main__":
    main()
