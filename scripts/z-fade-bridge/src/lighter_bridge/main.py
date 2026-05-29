"""Lighter paper bridge — main orchestrator.

Per symbol:
  - BarFeed pulls 5m bars (REST poll every 30s)
  - On new closed bar: regenerate V3 Pine signals
  - On fresh buy/sell signal: add to pending queue
  - Pending queue (max 6 bars old) — on each new bar, check EMA(9) retest
    + slope gate + entry filters; if all pass, fire entry via PaperExecutor
  - State machine ticks every 5s while a position is open

Logs both fills and signals to SQLite (data/lighter_paper.db).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import lighter
import pandas as pd

from .bar_feed import BarFeed, BarFeedConfig
from .config import BridgeConfig, load_config
from .db import TradeLogDB
from .executor import OpenPosition, PaperExecutor
from . import notify
from .signals import prepare, evaluate_entry
from .indicators import calc_atr
from .state_machine import step as state_step
from .sdk_patches import apply_order_book_patches

# Install the efficient order-book merge before any PaperClient is created.
# Fixes the SDK order-book consumer monopolising the event loop (>20s) and
# tripping the WS keepalive timeout — the 2026-05-28/29 reconnect storm.
apply_order_book_patches()


# UTF-8 stdout for Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


log = logging.getLogger("bridge")


class Bridge:
    def __init__(self, cfg: BridgeConfig):
        self.cfg = cfg
        self.api: lighter.ApiClient | None = None
        self.paper: lighter.PaperClient | None = None
        self.executor: PaperExecutor | None = None
        self.feeds: dict[str, BarFeed] = {}
        self.bars: dict[str, pd.DataFrame] = {}    # latest enriched DF per symbol
        self.last_entry_idx: dict[str, int] = {}   # symbol -> bar index of last entry (cooldown)
        self.trade_ids: dict[str, int] = {}        # symbol -> DB row id while open
        self.db = TradeLogDB(cfg.log.db_path)
        self._stopped = False

    async def start(self):
        st = self.cfg.strat
        log.info("=" * 70)
        log.info("Z-FADE PAPER BRIDGE — z-score mean-reversion fade")
        log.info("=" * 70)
        log.info("Host: %s", self.cfg.host)
        log.info("Paper collateral: $%.0f", self.cfg.initial_collateral_usdc)
        log.info("Symbols: %s", list(self.cfg.symbols.keys()))
        log.info("Entry: fade |z|>=%.1f (win %d) | RSI %s(%g/%g) | BB-width>%.3f %s | ADX<=%.0f %s | EMA-filter %s",
                 st.z_thresh, st.window, "on" if st.use_rsi else "off", st.rsi_os, st.rsi_ob,
                 st.bb_width_min, "on" if st.use_bb else "off", st.adx_max,
                 "on" if st.use_adx else "off", "on" if st.use_ema else "off")
        log.info("Exits: ATR stop %.2fx / target %.2fx (ATR len %d), cooldown %d bars",
                 self.cfg.exits.sl_atr, self.cfg.exits.tp_atr, st.atr_len, st.cooldown_bars)

        # Lighter client + paper client
        self.api = lighter.ApiClient(configuration=lighter.Configuration(host=self.cfg.host))
        self.paper = lighter.PaperClient(self.api,
                                         initial_collateral_usdc=self.cfg.initial_collateral_usdc)

        # Subscribe to live order book for each enabled symbol
        enabled = {n: s for n, s in self.cfg.symbols.items() if s.enabled}
        for name, s in enabled.items():
            log.info("%s: subscribing to live order book (market_id=%d)", name, s.market_id)
            await self.paper.track_market(market_id=s.market_id)
            self._arm_death_log(name, s.market_id)
            self.last_entry_idx[name] = -10**9

        # Build the executor with sizing config
        exec_symbols = {
            name: {
                "market_id": s.market_id,
                "margin_usdt": s.margin_usdt,
                "leverage": s.leverage,
            }
            for name, s in enabled.items()
        }
        self.executor = PaperExecutor(self.paper, exec_symbols)

        # Bar feeds (REST polling)
        for name, s in enabled.items():
            feed_cfg = BarFeedConfig(
                market_id=s.market_id,
                symbol=name,
                resolution=self.cfg.strat.timeframe,
                poll_interval_s=self.cfg.loop.bar_poll_interval_s,
            )
            self.feeds[name] = BarFeed(self.api, feed_cfg)

        # Wait briefly so the order book has data before signaling
        await asyncio.sleep(2)

        # Verify the live mark feed is delivering for every enabled symbol
        # before we touch trade restoration or open the loops. A bridge that
        # starts without a working WS is exactly the failure mode that left
        # position #16 stuck on 2026-05-23 — refuse to run blind.
        await self._verify_mark_feed_live(enabled, deadline_s=30)

        # Restore any open positions from prior run (orphans of a crash/restart)
        restored = await self.restore_open_positions()

        # Telegram startup ping (includes restored positions if any)
        await notify.notify_startup(self.cfg, restored=restored or None)

        # Kick off async tasks
        tasks = []
        for name, feed in self.feeds.items():
            tasks.append(asyncio.create_task(feed.run_loop(self.on_new_bar)))
        tasks.append(asyncio.create_task(self.position_check_loop()))
        tasks.append(asyncio.create_task(self.heartbeat_loop()))
        tasks.append(asyncio.create_task(self.daily_summary_loop()))
        tasks.append(asyncio.create_task(self.mark_freshness_loop()))
        tasks.append(asyncio.create_task(self.loop_lag_loop()))
        log.info(
            "Bridge running. %d bar-feed tasks + position checker + heartbeat + "
            "daily summary + mark-feed watchdog.", len(self.feeds),
        )

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        log.info("Stopping bridge...")
        self._stopped = True
        for f in self.feeds.values():
            f.stop()
        if self.paper is not None:
            try:
                await self.paper.close()
            except Exception:
                pass
        if self.api is not None:
            try:
                await self.api.close()
            except Exception:
                pass
        self.db.close()
        log.info("Bridge stopped.")

    # ------- Event handlers -------

    async def on_new_bar(self, symbol: str, df: pd.DataFrame):
        """Called by BarFeed every time a new closed bar lands."""
        is_bootstrap = symbol not in self.bars

        # Enrich with Z-Fade indicators (z-score, RSI, BB-width, ADX, ATR).
        # Runs ~1.2s on the bootstrap window and far less on incremental bars;
        # the WS reconnect storm was NOT this (ruled out via instrumentation) —
        # it was the SDK order-book merge (see sdk_patches.py).
        enriched = prepare(df, self.cfg.strat)
        self.bars[symbol] = enriched

        last = enriched.iloc[-1]
        last_ts = enriched.index[-1]

        last_idx = len(enriched) - 1

        if is_bootstrap:
            log.info("%s: bootstrap — %d bars (most recent %s, z=%.2f adx=%.0f)",
                     symbol, len(enriched), last_ts,
                     float(last.get("zscore", float("nan"))), float(last.get("adx", float("nan"))))
            return  # only NEW bars trigger entries

        # One position per symbol; exits handled by the position checker.
        if self.executor.is_open(symbol):
            return

        # Cooldown between entries
        if last_idx - self.last_entry_idx.get(symbol, -10**9) < self.cfg.strat.cooldown_bars:
            return

        side = evaluate_entry(last, self.cfg.strat)
        if side is None:
            return

        atr_v = float(last["atr"]); z_v = float(last["zscore"])
        log.info("%s: Z-FADE %s @ %s  z=%.2f rsi=%.0f adx=%.0f close=%.4f",
                 symbol, side.upper(), last_ts, z_v, float(last["rsi"]),
                 float(last["adx"]), float(last["Close"]))

        pos = await self.executor.open_position(symbol, side)
        if pos is None:
            return
        # Arm the fixed ATR stop / target off the actual fill price.
        e = pos.entry_price
        if side == "long":
            pos.sl_price = e - atr_v * self.cfg.exits.sl_atr
            pos.tp_price = e + atr_v * self.cfg.exits.tp_atr
        else:
            pos.sl_price = e + atr_v * self.cfg.exits.sl_atr
            pos.tp_price = e - atr_v * self.cfg.exits.tp_atr
        pos.atr_at_entry = atr_v
        self.last_entry_idx[symbol] = last_idx
        log.info("%s: ARMED sl=$%.4f tp=$%.4f (atr=%.4f)", symbol, pos.sl_price, pos.tp_price, atr_v)

        # Persist (reuse generic columns: slope_pct slot holds z, body_atr_ratio holds bb_width)
        trade_id = self.db.log_trade(
            symbol=symbol, side=side,
            entry_price=pos.entry_price,
            margin_usdt=pos.margin_usdt, leverage=pos.leverage,
            base_amount=pos.base_amount, notional=pos.notional,
            opened_at=datetime.now(timezone.utc).isoformat(),
            bar_time_open=str(last_ts),
            slope_pct=z_v, body_atr_ratio=float(last["bb_width"]),
            adx_at_entry=float(last["adx"]),
        )
        self.trade_ids[symbol] = trade_id
        asyncio.create_task(notify.notify_open(pos))
        self.db.log_signal(symbol=symbol, side=side, bar_time=str(last_ts), outcome="fired",
                           ema9=float(last["ema"]), slope_pct=z_v,
                           body_atr_ratio=float(last["bb_width"]),
                           detected_at=datetime.now(timezone.utc).isoformat())

    async def position_check_loop(self):
        """Tick every N seconds — run state machine on each open position."""
        log.info("Position checker started (every %ds)", self.cfg.loop.position_check_interval_s)
        while not self._stopped:
            for symbol, pos in list(self.executor.positions.items()):
                mark = self.executor.get_mark_price(symbol)
                if mark is None:
                    continue
                decision = state_step(pos, mark, self.cfg.exits)
                if decision.close:
                    result = await self.executor.close_position(symbol, decision.reason)
                    if result is not None and symbol in self.trade_ids:
                        exit_p = result.avg_price
                        if pos.side == "long":
                            pnl = (exit_p - pos.entry_price) * pos.base_amount
                        else:
                            pnl = (pos.entry_price - exit_p) * pos.base_amount
                        duration = int(time.time() - pos.opened_at)
                        pnl_pct = pnl / self.cfg.initial_collateral_usdc * 100
                        self.db.update_trade_close(
                            self.trade_ids[symbol],
                            exit_price=exit_p,
                            initial_sl=pos.sl_price if pos.max_state == 0 else None,
                            exit_reason=decision.reason,
                            pnl_usdt=pnl,
                            pnl_pct_account=pnl_pct,
                            duration_secs=duration,
                            max_state=pos.max_state,
                            closed_at=datetime.now(timezone.utc).isoformat(),
                        )
                        del self.trade_ids[symbol]
                        # Telegram close alert
                        asyncio.create_task(notify.notify_close(
                            symbol, pos.side, pos.entry_price, exit_p,
                            pnl, decision.reason, duration, pos.max_state,
                            starting_collateral=self.cfg.initial_collateral_usdc,
                        ))
            await asyncio.sleep(self.cfg.loop.position_check_interval_s)

    def _equity_breakdown(self) -> tuple[float, float, float]:
        """Return (realized_pnl, unrealized_pnl, total_equity).

        realized_pnl  = sum of closed-trade PnL from DB
        unrealized_pnl = sum of pnl_at_mark over open positions
        total_equity   = initial_collateral + realized + unrealized
        """
        stats = self.db.summary()
        realized = float(stats.get("net_pnl") or 0.0)
        unrealized = 0.0
        if self.executor is not None:
            for sym in list(self.executor.positions.keys()):
                v = self.executor.pnl_at_mark(sym)
                if v is not None:
                    unrealized += float(v)
        equity = float(self.cfg.initial_collateral_usdc) + realized + unrealized
        return realized, unrealized, equity

    async def heartbeat_loop(self):
        """Every 5 minutes, snapshot total equity (collateral + realized + unrealized)."""
        last_seen_at = time.time()
        while not self._stopped:
            await asyncio.sleep(300)
            if self.executor is None:
                continue
            try:
                realized, unrealized, equity = self._equity_breakdown()
                total_pnl = equity - self.cfg.initial_collateral_usdc
                free_basis = self.cfg.initial_collateral_usdc + realized
                n_open = len(self.executor.positions)
                self.db.snapshot_account(free_basis, equity, n_open, total_pnl)
                # Per-symbol mark-feed freshness, for at-a-glance WS health.
                mark_parts: list[str] = []
                for name in self.executor.symbols:
                    mark = self.executor.get_mark_price(name)
                    age = self.executor.mark_age_seconds(name)
                    if mark is not None and age is not None:
                        mark_parts.append(f"{name}=${mark:.2f}({age:.0f}s)")
                    elif mark is None:
                        mark_parts.append(f"{name}=NO_MARK")
                marks_str = "  " + " ".join(mark_parts) if mark_parts else ""
                log.info(
                    "HEARTBEAT  equity=$%.2f  realized=$%+.2f  unrealized=$%+.2f  open=%d%s",
                    equity, realized, unrealized, n_open, marks_str,
                )
                last_seen_at = time.time()
            except Exception as exc:
                log.error("Heartbeat error: %s", exc, exc_info=True)
                if time.time() - last_seen_at > 1800:
                    asyncio.create_task(notify.notify_error(
                        f"Heartbeat silent for >30m: {exc}"
                    ))

    async def loop_lag_loop(self):
        """DIAGNOSTIC (pure measurement, no behavior change): sleep a fixed 1.0s
        and log whenever the loop wakes late. A late wake = the event loop was
        blocked by synchronous work for that long; a block >= ~20s is exactly
        what trips the websockets keepalive ping timeout that kills the
        order-book listeners. Correlate LOOP-LAG spikes with LISTENER-DEATH
        timestamps to confirm (or rule out) loop starvation as the cause of the
        2026-05-28 reconnect storm.
        """
        interval = 1.0
        worst = 0.0
        while not self._stopped:
            t0 = time.monotonic()
            await asyncio.sleep(interval)
            lag = (time.monotonic() - t0) - interval
            if lag > worst:
                worst = lag
            if lag > 2.0:
                log.warning("LOOP-LAG event loop blocked for %.1fs (worst so far %.1fs)",
                            lag, worst)

    async def _verify_mark_feed_live(self, enabled: dict, deadline_s: int):
        """Block until every enabled symbol has reported a non-zero mark price,
        or fail fast. Refusing to start blind is intentional — see incident
        2026-05-23 (position #16 stuck on a never-updating WS).
        """
        log.info("Verifying live mark feed for %d symbol(s) (deadline %ds)...",
                 len(enabled), deadline_s)
        deadline = time.monotonic() + deadline_s
        not_ready: list[str] = list(enabled.keys())
        while not_ready and time.monotonic() < deadline:
            still = []
            for name in not_ready:
                mark = self.executor.get_mark_price(name)
                if mark is None or mark <= 0:
                    still.append(name)
                else:
                    log.info("  %s mark live @ $%.4f", name, mark)
            not_ready = still
            if not_ready:
                await asyncio.sleep(1)
        if not_ready:
            log.error("Mark feed not live for %s after %ds — refusing to start.",
                      not_ready, deadline_s)
            try:
                await notify.notify_error(
                    f"Bridge startup aborted: WS mark feed silent for {not_ready}"
                )
            except Exception:
                pass
            raise RuntimeError(f"WS mark feed dead for {not_ready}")
        log.info("Mark feed verified live for all symbols.")

    def _arm_death_log(self, name: str, market_id: int) -> None:
        """DIAGNOSTIC (pure logging, no behavior change): when this market's
        order-book listener task ends, log WHY — clean server close (no
        exception), an exception (e.g. keepalive ping timeout), or a cancel
        from our own stop_tracking — plus a snapshot of every listener's
        alive/dead state at that instant, to tell a common-cause drop apart
        from a reconnect-induced one. Investigating the 2026-05-28 reconnect
        storm: stable sockets live for >22m, but the bridge's die every ~6-7m.
        """
        if self.paper is None:
            return
        listener = self.paper._live_listeners.get(market_id)
        task = getattr(listener, "_task", None) if listener else None
        if task is None:
            return

        def _cb(t) -> None:
            try:
                if t.cancelled():
                    cause = "CANCELLED (our stop_tracking)"
                else:
                    exc = t.exception()
                    cause = "CLEAN_CLOSE (no exception)" if exc is None else f"{type(exc).__name__}: {exc!r}"
            except Exception as e:  # noqa: BLE001
                cause = f"EXC_READ_FAIL: {e!r}"
            snapshot = {
                n: ("dead" if self._listener_dead(fcfg.cfg.market_id) else "alive")
                for n, fcfg in self.feeds.items()
            }
            log.warning("LISTENER-DEATH %s(mkt%d) cause=%s all_listeners=%s",
                        name, market_id, cause, snapshot)

        task.add_done_callback(_cb)

    def _listener_dead(self, market_id: int) -> bool:
        """True if the SDK's per-market order-book WS listener is gone or its
        task has finished.

        Lighter's PaperOrderBookListener opens one socket per market with no
        reconnect (live.py): on a dropped connection the task ends and the
        exception is swallowed by its done-callback, leaving the listener
        registered but inert. A finished/absent task is the deterministic
        signal that the socket is dead and the mark will freeze — caught
        faster than waiting for the value to age out.
        """
        if self.paper is None:
            return False
        listener = self.paper._live_listeners.get(market_id)
        if listener is None:
            return True  # not tracked at all → needs (re)subscribe
        task = getattr(listener, "_task", None)
        return task is None or task.done()

    @staticmethod
    def _watchdog_action(age: float | None, dead: bool,
                         reconnect_s: int, fatal_s: int) -> str:
        """Pure decision for the mark-feed watchdog. Returns one of:
          "fatal"     — mark aged past fatal_s; reconnect has failed, give up.
          "reconnect" — listener task dead, or mark stale past reconnect_s.
          "ok"        — feed healthy.

        Fatal takes priority: by the time age >= fatal_s the in-process
        reconnect has been retried every tick from reconnect_s onward and
        still hasn't produced a fresh mark, so a clean process restart is the
        correct last resort.
        """
        if age is not None and age >= fatal_s:
            return "fatal"
        if dead or age is None or age >= reconnect_s:
            return "reconnect"
        return "ok"

    async def _reconnect_market(self, name: str, market_id: int,
                                retries: int = 2) -> bool:
        """Rebuild a single symbol's order-book WS in place.

        stop_tracking() pops + stops the dead listener (without this, the
        SDK's track_market() early-returns as a no-op because the dead
        listener is still registered — the latent bug that made the old
        warn-level re-subscribe do nothing). track_market() then opens a
        fresh socket and re-snapshots the book. The existing order_books /
        market_configs entries survive the swap; the new initial snapshot
        replaces the stale book cleanly. Other symbols are untouched.
        """
        for attempt in range(1, retries + 1):
            try:
                await self.paper.stop_tracking(market_id)
            except Exception as exc:
                log.warning("%s: stop_tracking failed (attempt %d): %s",
                            name, attempt, exc)
            try:
                await self.paper.track_market(market_id=market_id)
                self._arm_death_log(name, market_id)
                return True
            except Exception as exc:
                # repr + traceback: bare str(exc) is empty for some SDK errors
                # (e.g. the market_id=0 / ETH reconnect failure, 2026-05-27).
                log.error("%s: track_market failed (attempt %d/%d): %r",
                          name, attempt, retries, exc, exc_info=True)
                await asyncio.sleep(min(2 ** attempt, 5))
        return False

    async def mark_freshness_loop(self):
        """Watchdog + self-healing supervisor for the Lighter mark feed.

        Pure infrastructure. Does NOT block, modify, gate, or filter any
        trades — the state machine keeps ticking on whatever get_mark_price
        returns. Per check tick, for each symbol:
          - reconnect JUST that symbol's WS in place when its listener task is
            dead, or its mark has been frozen past reconnect_s (heals in
            ~under a minute, without touching the other symbol);
          - as a last resort, exit the process when the mark ages past fatal_s
            (only reached if in-process reconnect keeps failing), so Docker
            rebuilds everything cleanly.
        """
        reconnect_s = self.cfg.loop.mark_reconnect_s
        warn_s = self.cfg.loop.mark_stale_warn_s
        fatal_s = self.cfg.loop.mark_stale_fatal_s
        check_s = self.cfg.loop.mark_watchdog_interval_s
        log.info(
            "Mark-feed watchdog started (reconnect=%ds, warn=%ds, fatal=%ds, "
            "check every %ds)", reconnect_s, warn_s, fatal_s, check_s,
        )
        last_reconnect: dict[str, float] = {}
        while not self._stopped:
            await asyncio.sleep(check_s)
            if self.executor is None or self.paper is None:
                continue
            for name in list(self.executor.symbols.keys()):
                market_id = self.executor.symbols[name]["market_id"]
                age = self.executor.mark_age_seconds(name)
                dead = self._listener_dead(market_id)
                action = self._watchdog_action(age, dead, reconnect_s, fatal_s)

                if action == "reconnect":
                    now_mono = time.monotonic()
                    if now_mono - last_reconnect.get(name, 0.0) >= 25:
                        if dead:
                            reason = "WS listener task dead"
                        elif age is None:
                            reason = "mark never recorded since last reconnect"
                        else:
                            reason = f"mark stale {age:.0f}s (>= {reconnect_s}s)"
                        log.warning("%s: %s — reconnecting market_id=%d",
                                    name, reason, market_id)
                        ok = await self._reconnect_market(name, market_id)
                        last_reconnect[name] = now_mono
                        if ok:
                            log.info("%s: WS reconnect OK", name)
                        else:
                            log.error(
                                "%s: WS reconnect failed; fatal restart will "
                                "fire if mark stays stale past %ds.",
                                name, fatal_s,
                            )
                    # Re-evaluate next tick; don't fall through to fatal on the
                    # same tick we just rebuilt the socket.
                    continue

                if action == "fatal":
                    msg = (f"Mark for {name} (market_id={market_id}) stale "
                           f"for {age:.0f}s (>= fatal {fatal_s}s) despite "
                           f"in-process reconnect. Exiting for Docker to "
                           f"restart with fresh WS.")
                    log.error(msg)
                    try:
                        await notify.notify_error(msg)
                    except Exception:
                        pass
                    await asyncio.sleep(2)
                    sys.exit(1)

    async def daily_summary_loop(self):
        """Send one Telegram daily summary every 24h (anchored to first call)."""
        await asyncio.sleep(3600)
        while not self._stopped:
            try:
                stats = self.db.summary()
                _, _, equity = self._equity_breakdown()
                stats["portfolio_value"] = equity
                await notify.notify_daily(stats, starting_collateral=self.cfg.initial_collateral_usdc)
            except Exception as exc:
                log.error("Daily summary error: %s", exc, exc_info=True)
            await asyncio.sleep(86400)

    async def restore_open_positions(self) -> list[OpenPosition]:
        """Reconstruct any orphaned open positions from the DB after a restart.

        For each trade_log row with closed_at IS NULL:
          - Replay 5m candles from opened_at to now to derive trail_high
            (lowest low for shorts, highest high for longs).
          - Submit a matching paper order so PaperClient holds the same
            position and a future close order can flatten it cleanly.
            (PaperClient's own PnL accounting is sidestepped — we track
            realized/unrealized via the DB and OpenPosition entry price.)
          - Inject the OpenPosition into self.executor.positions and register
            its trade_id so the state machine + close hook work normally.

        Returns the list of restored positions.
        """
        if self.executor is None or self.paper is None or self.api is None:
            return []
        open_rows = self.db.get_open_trades()
        if not open_rows:
            return []

        log.info("Restoring %d open position(s) from trade_log...", len(open_rows))
        candle_api = lighter.CandlestickApi(self.api)
        restored: list[OpenPosition] = []

        for row in open_rows:
            symbol = row["symbol"]
            if symbol not in self.executor.symbols:
                log.warning(
                    "  skipping orphan trade #%d (%s): symbol not enabled in config",
                    row["id"], symbol,
                )
                continue
            sym_cfg = self.executor.symbols[symbol]
            side = row["side"]
            opened_at_iso = row["opened_at"]
            try:
                opened_at_unix = datetime.fromisoformat(opened_at_iso).timestamp()
            except Exception:
                log.warning("  trade #%d has unparseable opened_at=%r, skipping", row["id"], opened_at_iso)
                continue

            now_unix = int(time.time())
            try:
                resp = await candle_api.candles(
                    market_id=sym_cfg["market_id"],
                    resolution="5m",
                    start_timestamp=int(opened_at_unix),
                    end_timestamp=now_unix,
                    count_back=500,
                )
                cd = resp.to_dict()
                candles = cd.get("c") or []
            except Exception as exc:
                log.error(
                    "  %s: candle replay failed for trade #%d (%s) — using entry as trail_high",
                    symbol, row["id"], exc,
                )
                candles = []

            entry_price = float(row["entry_price"])
            # Rebuild the fixed ATR stop/target. Use the earliest valid ATR from the
            # replayed candles (closest to the entry bar). Fallback to 1%/2% of entry
            # so an orphan is always bounded, never held with no exit.
            atr_entry = None
            if candles:
                try:
                    cdf = pd.DataFrame([{
                        "Open": float(c["o"]), "High": float(c["h"]),
                        "Low": float(c["l"]), "Close": float(c["c"]),
                    } for c in candles])
                    valid = calc_atr(cdf, self.cfg.strat.atr_len).dropna()
                    atr_entry = float(valid.iloc[0]) if len(valid) else None
                except Exception as exc:
                    log.warning("  %s: ATR recompute failed for #%d (%s)", symbol, row["id"], exc)
            sl_m, tp_m = self.cfg.exits.sl_atr, self.cfg.exits.tp_atr
            if atr_entry is None or atr_entry <= 0:
                log.warning("  %s: no ATR for #%d — 1%%/2%% fallback stops", symbol, row["id"])
                atr_entry = entry_price * 0.01 / max(sl_m, 1e-9)
            if side == "long":
                sl_price = entry_price - atr_entry * sl_m
                tp_price = entry_price + atr_entry * tp_m
            else:
                sl_price = entry_price + atr_entry * sl_m
                tp_price = entry_price - atr_entry * tp_m

            order_side = (
                lighter.PaperOrderSide.BUY if side == "long" else lighter.PaperOrderSide.SELL
            )
            try:
                result = await self.paper.create_paper_order(lighter.PaperOrderRequest(
                    market_id=sym_cfg["market_id"],
                    side=order_side,
                    base_amount=float(row["base_amount"]),
                ))
                rehydration_price = float(result.avg_price)
            except Exception as exc:
                log.error(
                    "  %s: rehydration paper-open failed for trade #%d (%s) — skipping",
                    symbol, row["id"], exc,
                )
                continue

            pos = OpenPosition(
                symbol=symbol,
                market_id=sym_cfg["market_id"],
                side=side,
                entry_price=entry_price,
                base_amount=float(row["base_amount"]),
                margin_usdt=float(row["margin_usdt"]),
                leverage=float(row["leverage"]),
                opened_at=opened_at_unix,
                notional=float(row["notional"]),
                sl_price=sl_price,
                tp_price=tp_price,
                atr_at_entry=atr_entry,
            )
            self.executor.positions[symbol] = pos
            self.trade_ids[symbol] = int(row["id"])
            restored.append(pos)
            log.info(
                "  restored #%d %s %s entry=$%.4f size=%g sl=$%.4f tp=$%.4f rehydrated@$%.4f",
                row["id"], symbol, side.upper(), entry_price, pos.base_amount,
                sl_price, tp_price, rehydration_price,
            )

        return restored


async def amain(config_path: str):
    cfg = load_config(config_path)
    logging.basicConfig(
        level=cfg.log.level,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    bridge = Bridge(cfg)

    # Graceful shutdown on Ctrl+C / SIGTERM
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def shutdown_handler(*_):
        log.info("Shutdown signal received")
        stop_event.set()

    # Windows doesn't support add_signal_handler for SIGINT in asyncio; rely on KeyboardInterrupt
    try:
        for sig in (signal.SIGTERM,):
            loop.add_signal_handler(sig, shutdown_handler)
    except (NotImplementedError, AttributeError):
        pass

    bridge_task = asyncio.create_task(bridge.start())
    try:
        await asyncio.wait([bridge_task, asyncio.create_task(stop_event.wait())],
                          return_when=asyncio.FIRST_COMPLETED)
    except KeyboardInterrupt:
        pass
    finally:
        await bridge.stop()
        bridge_task.cancel()
        try:
            await bridge_task
        except (asyncio.CancelledError, Exception):
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()
    config_path = Path(args.config).resolve()
    if not config_path.exists():
        # Try relative to the bridge root (parent of src/)
        config_path = Path(__file__).resolve().parents[2] / args.config
    asyncio.run(amain(str(config_path)))


if __name__ == "__main__":
    main()
