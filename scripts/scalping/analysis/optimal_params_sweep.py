"""Counterfactual replay + parameter sweep for the scalping bridge trail-SL state machine.

Built 2026-04-29 to find optimal SL/BE/trail parameters using the actual 181-trade
history. Mirrors the production state machine in poller.py.

Run inside the scalping container:
  docker cp optimal_params_sweep.py scalping:/tmp/
  docker exec scalping python /tmp/optimal_params_sweep.py > /tmp/sweep_report.txt

Outputs:
  /tmp/sweep_report.txt      — human-readable summary
  /tmp/sweep_results.csv     — every (params x symbol) row, scorable
  /tmp/slippage_profile.csv  — slippage distribution per symbol
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import product
from statistics import mean, median, stdev
from typing import Iterable, Optional

import ccxt

DB_PATH = "/app/data/bridge.db"
OUT_REPORT = "/tmp/sweep_report.txt"
OUT_CSV = "/tmp/sweep_results.csv"
OUT_SLIP = "/tmp/slippage_profile.csv"

# Cache for OHLCV across replays — fetch once per (symbol, start_ms) bucket
_ohlcv_cache: dict[tuple[str, int], list[list[float]]] = {}


def to_ms(ts_str: str) -> int:
    return int(datetime.fromisoformat(ts_str).astimezone(timezone.utc).timestamp() * 1000)


def make_client() -> ccxt.Exchange:
    return ccxt.blofin({"options": {"defaultType": "swap"}, "enableRateLimit": True})


def fetch_bars(client, ccxt_sym: str, start_ms: int, end_ms: int) -> list[list[float]]:
    """Fetch 5m bars covering [start_ms, end_ms]. Cached.

    BloFin's ccxt fetchOHLCV uses `until` as a forward cursor. We pull a window
    that covers the trade duration plus 1 bar of slack on each side.
    """
    key = (ccxt_sym, start_ms - 5 * 60_000, end_ms + 10 * 60_000)
    if key in _ohlcv_cache:
        return _ohlcv_cache[key]
    bars: list[list[float]] = []
    cursor_ms = end_ms + 10 * 60_000
    while cursor_ms > start_ms - 5 * 60_000:
        try:
            chunk = client.fetch_ohlcv(
                ccxt_sym, timeframe="5m", limit=100,
                params={"until": cursor_ms},
            )
        except Exception as exc:
            print(f"    ohlcv fetch failed @{cursor_ms}: {exc}", file=sys.stderr)
            break
        if not chunk:
            break
        bars = chunk + bars
        # BloFin returns oldest-first; use the oldest bar's ts as next cursor
        cursor_ms = chunk[0][0] - 1
        if len(chunk) < 100:
            break
        time.sleep(0.05)  # gentle rate limit
    # Filter to window
    bars = [b for b in bars if start_ms - 5 * 60_000 <= b[0] <= end_ms + 10 * 60_000]
    bars.sort(key=lambda b: b[0])
    _ohlcv_cache[key] = bars
    return bars


# ------------- State machine simulator -------------

@dataclass
class TrailParams:
    margin_usdt: float = 100.0
    leverage: float = 30.0
    sl_loss_usdt: float = 13.0
    breakeven_usdt: float = 15.0
    lock_profit_activate_usdt: float = 20.0
    lock_profit_usdt: float = 15.0
    trail_activate_usdt: float = 25.0
    trail_start_usdt: float = 30.0
    trail_distance_usdt: float = 10.0
    tp_ceiling_pct: float = 2.0  # 200% of margin = $200 hard ceiling
    sl_slippage_pct: float = 0.0  # extra slippage beyond SL trigger as % of price


def dollars_to_price_distance(dollars: float, params: TrailParams, ref_price: float) -> float:
    notional = params.margin_usdt * params.leverage
    return (dollars / notional) * ref_price


def pnl_at_price(side: str, entry: float, price: float, params: TrailParams) -> float:
    notional = params.margin_usdt * params.leverage
    if side == "long":
        pct = (price - entry) / entry
    else:
        pct = (entry - price) / entry
    return pct * notional


@dataclass
class SimResult:
    pnl_usdt: float
    exit_reason: str  # 'sl', 'trail_sl', 'tp_ceiling', 'unresolved'
    exit_price: float
    final_state: int
    duration_bars: int


def simulate_trade(
    side: str,
    entry_price: float,
    bars: list[list[float]],
    params: TrailParams,
    ordering: str = "auto",     # "auto" (bar direction), "fav_first", "adv_first", "avg"
) -> SimResult:
    """Replay a single trade through the trail-SL state machine.

    Bars: list of [ts_ms, open, high, low, close, volume]. Bars must be ordered
    chronologically and start at the entry bar (or just after).

    Each bar is processed:
      1. Check if SL is hit using the bar's adverse extreme (long: low, short: high).
         If yes, exit at SL trigger + slippage (worse direction).
      2. Check if state advancement triggers using the bar's favorable extreme.
         (long: high, short: low). State machine advances at most one step per bar.
      3. If trailing (state 4), update SL based on the bar's favorable extreme.

    Note: in the live system, polling is every 2s so these state transitions
    happen smoothly. Bar-level approximation slightly over-counts trail wins
    (since we use bar high/low, not the actual sequence of ticks).
    """
    if not bars:
        return SimResult(0.0, "unresolved", entry_price, 0, 0)

    state = 0
    sl_dist0 = dollars_to_price_distance(params.sl_loss_usdt, params, entry_price)
    if side == "long":
        sl = entry_price - sl_dist0
    else:
        sl = entry_price + sl_dist0

    trail_high = entry_price  # best price reached so far (high for long, low for short)

    def advance_state(favorable_price: float, sl_in: float, state_in: int,
                      trail_high_in: float) -> tuple[float, int, float]:
        """Apply state-machine transitions using a single favorable price point."""
        sl_, state_, th_ = sl_in, state_in, trail_high_in
        peak_pnl = pnl_at_price(side, entry_price, favorable_price, params)
        if state_ == 0 and peak_pnl >= params.breakeven_usdt:
            sl_ = entry_price
            state_ = 1
        if state_ == 1 and peak_pnl >= params.lock_profit_activate_usdt:
            lock_dist = dollars_to_price_distance(params.lock_profit_usdt, params, entry_price)
            sl_ = (entry_price + lock_dist) if side == "long" else (entry_price - lock_dist)
            state_ = 2
        if state_ == 2 and peak_pnl >= params.trail_activate_usdt:
            jump_lock = params.trail_start_usdt - params.trail_distance_usdt
            jump_dist = dollars_to_price_distance(jump_lock, params, entry_price)
            sl_ = (entry_price + jump_dist) if side == "long" else (entry_price - jump_dist)
            state_ = 3
            th_ = favorable_price
        if state_ == 3 and peak_pnl >= params.trail_start_usdt:
            state_ = 4
            th_ = favorable_price
        if state_ == 4:
            better = (side == "long" and favorable_price > th_) or \
                     (side == "short" and favorable_price < th_)
            if better:
                th_ = favorable_price
            trail_dist_p = dollars_to_price_distance(
                params.trail_distance_usdt, params, th_,
            )
            new_sl = (th_ - trail_dist_p) if side == "long" else (th_ + trail_dist_p)
            sl_ = max(sl_, new_sl) if side == "long" else min(sl_, new_sl)
        return sl_, state_, th_

    def check_sl(price: float, sl_now: float, state_now: int) -> Optional[SimResult]:
        slip_dist = entry_price * params.sl_slippage_pct
        if side == "long":
            if price <= sl_now:
                exit_p = sl_now - slip_dist
                reason = "trail_sl" if state_now >= 2 else ("sl_be" if state_now == 1 else "sl")
                return SimResult(
                    pnl_at_price(side, entry_price, exit_p, params),
                    reason, exit_p, state_now, -1,
                )
        else:
            if price >= sl_now:
                exit_p = sl_now + slip_dist
                reason = "trail_sl" if state_now >= 2 else ("sl_be" if state_now == 1 else "sl")
                return SimResult(
                    pnl_at_price(side, entry_price, exit_p, params),
                    reason, exit_p, state_now, -1,
                )
        return None

    for i, bar in enumerate(bars):
        _, b_open, b_high, b_low, b_close, _ = bar
        adverse = b_low if side == "long" else b_high
        favorable = b_high if side == "long" else b_low

        # TP ceiling check (favorable-side, may fire any time the bar reaches it)
        peak_pnl = pnl_at_price(side, entry_price, favorable, params)
        if peak_pnl >= params.margin_usdt * params.tp_ceiling_pct:
            ceiling_dist = dollars_to_price_distance(
                params.margin_usdt * params.tp_ceiling_pct, params, entry_price,
            )
            ceiling_p = (entry_price + ceiling_dist) if side == "long" else (entry_price - ceiling_dist)
            return SimResult(
                pnl_at_price(side, entry_price, ceiling_p, params),
                "tp_ceiling", ceiling_p, state, i + 1,
            )

        # Determine intra-bar ordering of favorable vs adverse extreme
        if ordering == "fav_first":
            favorable_first = True
        elif ordering == "adv_first":
            favorable_first = False
        else:
            # Bar-direction heuristic: bullish bar reaches low first then high
            bullish = b_close >= b_open
            if side == "long":
                favorable_first = not bullish
            else:
                favorable_first = bullish

        if favorable_first:
            # 1. Advance state at favorable extreme
            sl, state, trail_high = advance_state(favorable, sl, state, trail_high)
            # 2. Check SL at adverse extreme (with UPDATED sl)
            res = check_sl(adverse, sl, state)
            if res:
                res.duration_bars = i + 1
                return res
        else:
            # 1. Check SL at adverse extreme using OLD sl
            res = check_sl(adverse, sl, state)
            if res:
                res.duration_bars = i + 1
                return res
            # 2. Then advance state at favorable extreme
            sl, state, trail_high = advance_state(favorable, sl, state, trail_high)

    # Ran out of bars without exiting — close at last bar's close
    last_close = bars[-1][4]
    return SimResult(
        pnl_at_price(side, entry_price, last_close, params),
        "unresolved", last_close, state, len(bars),
    )


# ------------- Data loading + slippage characterization -------------

def load_trades() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("""
        SELECT id, symbol, side, entry_price, exit_price, initial_sl,
               trail_activated, trail_high_price, exit_reason,
               pnl_usdt, opened_at, closed_at, duration_secs
        FROM trade_log
        ORDER BY id
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def slippage_profile(trades: list[dict]) -> dict[str, dict]:
    """For each symbol, how much do SL fills slip past the trigger?

    Returns {symbol: {pct_slip: [...], median_pct, p75_pct, p95_pct, mean_pct}}
    """
    by_sym: dict[str, list[float]] = {}
    for t in trades:
        if t["exit_reason"] != "sl":
            continue
        if not t["initial_sl"] or t["initial_sl"] == 0.0:
            continue
        if t["exit_price"] is None or t["entry_price"] is None:
            continue
        if t["side"] == "long":
            slip = t["initial_sl"] - t["exit_price"]   # exit below SL = positive slip
        else:
            slip = t["exit_price"] - t["initial_sl"]   # exit above SL = positive slip
        if slip < 0:
            continue   # exit was actually better than trigger; not a slippage event
        slip_pct = slip / t["entry_price"]
        by_sym.setdefault(t["symbol"], []).append(slip_pct)

    out = {}
    for sym, slips in by_sym.items():
        slips.sort()
        n = len(slips)
        out[sym] = {
            "n": n,
            "min_pct": slips[0],
            "max_pct": slips[-1],
            "mean_pct": mean(slips),
            "median_pct": median(slips),
            "p75_pct": slips[int(n * 0.75)] if n else 0,
            "p90_pct": slips[int(n * 0.90)] if n else 0,
            "p95_pct": slips[int(n * 0.95)] if n else 0,
            "samples": slips,
        }
    return out


# ------------- OHLCV fetch with retries + cache disk -------------

def gather_ohlcv(trades: list[dict], client) -> dict[int, list[list[float]]]:
    """Fetch bars for every trade. Returns {trade_id: [bars]}.

    Window: entry through max(actual close, entry + 24h). 24h gives wider-SL
    counterfactual configs enough room to resolve, while bounding the
    "next-signal-flip" effect that closes positions in real life.
    """
    out: dict[int, list[list[float]]] = {}
    for t in trades:
        sym = t["symbol"]
        ccxt_sym = sym.replace("-", "/") + ":USDT"
        start_ms = to_ms(t["opened_at"])
        end_ms = to_ms(t["closed_at"])
        max_window_end = max(end_ms, start_ms + 24 * 60 * 60_000)
        bars = fetch_bars(client, ccxt_sym, start_ms - 60_000, max_window_end + 60_000)
        bars = [b for b in bars if b[0] >= start_ms - 5 * 60_000
                                  and b[0] <= start_ms + 24 * 60 * 60_000]
        out[t["id"]] = bars
    return out


# ------------- Param sweep -------------

# Production live config (the thing we're trying to beat)
LIVE_PARAMS = TrailParams(
    margin_usdt=100, leverage=30,
    sl_loss_usdt=13, breakeven_usdt=15,
    lock_profit_activate_usdt=20, lock_profit_usdt=15,
    trail_activate_usdt=25, trail_start_usdt=30, trail_distance_usdt=10,
)


def sweep_grid() -> list[TrailParams]:
    """Generate the grid of (margin, leverage, sl, BE, lock, trail) configs."""
    grid = []
    for margin in (80, 100, 130):
        for leverage in (20, 25, 30):
            for sl in (15, 18, 20, 22, 25):
                for be in (15, 20, 25):
                    if be < sl:
                        continue  # BE before recouping SL distance is illogical
                    for lock_act, lock_amt in [
                        (be + 5, be), (be + 10, be + 5), (be + 5, be - 5),
                    ]:
                        if lock_amt <= 0:
                            continue
                        for trail_act, trail_start, trail_dist in [
                            (lock_act + 5, lock_act + 10, 10),
                            (lock_act + 5, lock_act + 10, 15),
                            (lock_act + 10, lock_act + 15, 10),
                        ]:
                            grid.append(TrailParams(
                                margin_usdt=margin, leverage=leverage,
                                sl_loss_usdt=sl, breakeven_usdt=be,
                                lock_profit_activate_usdt=lock_act,
                                lock_profit_usdt=lock_amt,
                                trail_activate_usdt=trail_act,
                                trail_start_usdt=trail_start,
                                trail_distance_usdt=trail_dist,
                            ))
    return grid


def run_sweep(trades: list[dict], bars_by_id: dict[int, list],
              slip_by_sym: dict[str, dict],
              params_grid: Iterable[TrailParams],
              symbol_filter: Optional[str] = None,
              ordering: str = "avg") -> list[dict]:
    """For each param set, simulate every trade. Return per-config summary rows.

    ordering="avg" runs both fav_first and adv_first and averages the P&L per trade.
    This is the most honest single-number estimate without tick data.
    """
    rows = []
    for params in params_grid:
        wins = 0
        losses = 0
        pnls = []
        sl_hits = 0
        be_hits = 0
        trail_exits = 0
        ceiling_hits = 0
        unresolved = 0
        for t in trades:
            if symbol_filter and t["symbol"] != symbol_filter:
                continue
            bars = bars_by_id.get(t["id"]) or []
            if not bars:
                continue
            slip_pct = slip_by_sym.get(t["symbol"], {}).get("median_pct", 0.0)
            run_params = TrailParams(**{**params.__dict__, "sl_slippage_pct": slip_pct})
            if ordering == "avg":
                a = simulate_trade(t["side"], t["entry_price"], bars, run_params, "fav_first")
                b = simulate_trade(t["side"], t["entry_price"], bars, run_params, "adv_first")
                avg_pnl = (a.pnl_usdt + b.pnl_usdt) / 2
                # Pick reason from worse-outcome ordering
                if a.pnl_usdt < b.pnl_usdt:
                    res = SimResult(avg_pnl, a.exit_reason, a.exit_price,
                                    a.final_state, max(a.duration_bars, b.duration_bars))
                else:
                    res = SimResult(avg_pnl, b.exit_reason, b.exit_price,
                                    b.final_state, max(a.duration_bars, b.duration_bars))
            else:
                res = simulate_trade(t["side"], t["entry_price"], bars, run_params, ordering)
            pnls.append(res.pnl_usdt)
            if res.pnl_usdt > 0:
                wins += 1
            else:
                losses += 1
            if res.exit_reason == "sl":
                sl_hits += 1
            elif res.exit_reason == "sl_be":
                be_hits += 1
            elif res.exit_reason == "trail_sl":
                trail_exits += 1
            elif res.exit_reason == "tp_ceiling":
                ceiling_hits += 1
            elif res.exit_reason == "unresolved":
                unresolved += 1
        n = len(pnls)
        if n == 0:
            continue
        net_pnl = sum(pnls)
        wr = wins / n
        # Drawdown: max negative running PnL
        running = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            running += p
            peak = max(peak, running)
            max_dd = min(max_dd, running - peak)
        rows.append({
            "margin": params.margin_usdt,
            "leverage": params.leverage,
            "sl": params.sl_loss_usdt,
            "be": params.breakeven_usdt,
            "lock_act": params.lock_profit_activate_usdt,
            "lock_amt": params.lock_profit_usdt,
            "trail_act": params.trail_activate_usdt,
            "trail_start": params.trail_start_usdt,
            "trail_dist": params.trail_distance_usdt,
            "n": n,
            "wins": wins,
            "losses": losses,
            "wr": round(wr, 3),
            "net_pnl": round(net_pnl, 2),
            "avg_pnl": round(net_pnl / n, 3),
            "median_pnl": round(median(pnls), 3),
            "sl_hits": sl_hits,
            "be_hits": be_hits,
            "trail_exits": trail_exits,
            "ceiling_hits": ceiling_hits,
            "unresolved": unresolved,
            "max_dd": round(max_dd, 2),
            "symbol": symbol_filter or "ALL",
        })
    return rows


# ------------- Main -------------

def main():
    print("=" * 72)
    print("Scalping bridge — trail-SL counterfactual sweep")
    print("Generated:", datetime.now(timezone.utc).isoformat())
    print("=" * 72)

    print("\n[1/5] Loading trades from DB...")
    trades = load_trades()
    # Drop trades with no initial_sl (early bridge versions)
    trades_clean = [t for t in trades if (t["initial_sl"] or 0) > 0 and t["entry_price"]]
    print(f"  total: {len(trades)}  usable (has initial_sl>0): {len(trades_clean)}")

    print("\n[2/5] Slippage profile per symbol...")
    slip = slippage_profile(trades_clean)
    with open(OUT_SLIP, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "n", "median_pct", "p75_pct", "p90_pct", "p95_pct", "mean_pct", "max_pct"])
        for sym, info in slip.items():
            print(
                f"  {sym}: n={info['n']:3d}  "
                f"median={info['median_pct']*100:.4f}%  "
                f"p75={info['p75_pct']*100:.4f}%  "
                f"p90={info['p90_pct']*100:.4f}%  "
                f"p95={info['p95_pct']*100:.4f}%  "
                f"max={info['max_pct']*100:.4f}%"
            )
            w.writerow([
                sym, info["n"], info["median_pct"], info["p75_pct"],
                info["p90_pct"], info["p95_pct"], info["mean_pct"], info["max_pct"],
            ])

    print("\n[3/5] Fetching 5m OHLCV for each trade...")
    client = make_client()
    bars_by_id = gather_ohlcv(trades_clean, client)
    n_with_bars = sum(1 for b in bars_by_id.values() if b)
    print(f"  fetched bars for {n_with_bars}/{len(trades_clean)} trades")

    print("\n[4/5] Validating sim against live with current params...")
    actual_pnl = sum(t["pnl_usdt"] or 0 for t in trades_clean)
    actual_wr = sum(1 for t in trades_clean if (t["pnl_usdt"] or 0) > 0) / len(trades_clean)
    print(f"  Live (actual):       net_pnl=${actual_pnl:+.2f}  wr={actual_wr:.3f}  n={len(trades_clean)}")
    for ord_label in ("fav_first", "adv_first", "avg"):
        rs = run_sweep(trades_clean, bars_by_id, slip, [LIVE_PARAMS], ordering=ord_label)
        if rs:
            r = rs[0]
            print(f"  Sim ({ord_label:>9}):    net_pnl=${r['net_pnl']:+.2f}  wr={r['wr']:.3f}  unresolved={r['unresolved']}")
    calibration_offset = actual_pnl - rs[0]["net_pnl"]
    print(f"  Calibration offset (live actual - sim_avg): ${calibration_offset:+.2f}  (~${calibration_offset/len(trades_clean):+.3f}/trade)")

    print("\n[5/5] Running param sweep...")
    grid = sweep_grid()
    print(f"  grid size: {len(grid)} configs")

    all_rows = []
    print("  ALL symbols ...")
    all_rows += run_sweep(trades_clean, bars_by_id, slip, grid, symbol_filter=None, ordering="auto")
    print("  SOL only ...")
    all_rows += run_sweep(trades_clean, bars_by_id, slip, grid, symbol_filter="SOL-USDT", ordering="auto")
    print("  ZEC only ...")
    all_rows += run_sweep(trades_clean, bars_by_id, slip, grid, symbol_filter="ZEC-USDT", ordering="auto")

    with open(OUT_CSV, "w", newline="") as f:
        if all_rows:
            w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            w.writeheader()
            w.writerows(all_rows)

    print(f"\n  wrote {len(all_rows)} rows to {OUT_CSV}")

    # Top N by net PnL per symbol filter, with leverage views
    print("\n" + "=" * 72)
    print("TOP CONFIGS by net_pnl  (auto-ordering = bar-direction heuristic)")
    print("=" * 72)
    for sym in ("ALL", "SOL-USDT", "ZEC-USDT"):
        for lev_filter in (None, 30):
            label = f"{sym} (any lev)" if lev_filter is None else f"{sym} @ {lev_filter}x ONLY"
            sym_rows = [r for r in all_rows if r["symbol"] == sym]
            if lev_filter:
                sym_rows = [r for r in sym_rows if r["leverage"] == lev_filter]
            # Require >= 70% resolved
            sym_rows = [r for r in sym_rows if (r["n"] - r["unresolved"]) / r["n"] >= 0.70]
            sym_rows.sort(key=lambda r: r["net_pnl"], reverse=True)
            if not sym_rows:
                continue
            print(f"\n--- {label} (n={sym_rows[0]['n']}) ---")
            print(f"{'rank':>4} {'net':>8} {'wr':>5} {'avg':>7} {'maxdd':>7} {'n_sl':>4} {'n_trl':>5} "
                  f"{'n_unr':>5} {'mar':>4} {'lev':>4} {'sl':>3} {'be':>3} "
                  f"{'la':>3} {'lm':>3} {'ta':>3} {'ts':>3} {'td':>3}")
            for i, r in enumerate(sym_rows[:10], 1):
                print(
                    f"{i:>4} ${r['net_pnl']:>+7.2f} {r['wr']:>5.3f} ${r['avg_pnl']:>+6.3f} "
                    f"${r['max_dd']:>+6.2f} {r['sl_hits']:>4d} {r['trail_exits']:>5d} "
                    f"{r['unresolved']:>5d} {r['margin']:>4} {r['leverage']:>4} "
                    f"{r['sl']:>3} {r['be']:>3} {r['lock_act']:>3} {r['lock_amt']:>3} "
                    f"{r['trail_act']:>3} {r['trail_start']:>3} {r['trail_dist']:>3}"
                )

    print("\n" + "=" * 72)
    print(f"Full results: {OUT_CSV}")
    print(f"Slippage:     {OUT_SLIP}")
    print("=" * 72)


if __name__ == "__main__":
    main()
