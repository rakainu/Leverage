"""Lighter paper bridge — main orchestrator.

Per symbol:
  - BarFeed pulls 5m bars (REST poll every 30s)
  - On new closed bar: regenerate V3 Pine signals
  - On fresh buy/sell signal: add to pending queue
  - Pending queue (max 6 bars old) — on each new bar, check EMA(9) retest
    + slope gate + entry filters; if all pass, fire entry via PaperExecutor
  - State machine ticks every 5s while a position is open

Logs both fills and signals to SQLite (data/apex.db).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
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
from . import sizing
from .signals import (
    check_retest, compute_ema_and_slope,
    generate_v3_signals, passes_entry_filters, prepare,
)
from .state_machine import step as state_step
# NOTE: webhook.py (fastapi/uvicorn) is imported lazily in start() only when
# webhook mode is enabled, so the trail/replica bridge never needs those deps.


# UTF-8 stdout for Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


log = logging.getLogger("bridge")


@dataclass
class PendingSignal:
    """A V3 signal awaiting EMA(9) retest confirmation."""
    symbol: str
    side: str               # "long" or "short"
    detected_at_bar_ts: pd.Timestamp
    detected_at_bar_idx: int


class Bridge:
    def __init__(self, cfg: BridgeConfig):
        self.cfg = cfg
        self.api: lighter.ApiClient | None = None
        self.paper: lighter.PaperClient | None = None
        self.executor: PaperExecutor | None = None
        self.feeds: dict[str, BarFeed] = {}
        self.bars: dict[str, pd.DataFrame] = {}    # latest enriched DF per symbol
        self.pending: dict[str, list[PendingSignal]] = {}  # one list per symbol
        self.trade_ids: dict[str, int] = {}        # symbol -> DB row id while open
        self.db = TradeLogDB(cfg.log.db_path)
        self._stopped = False
        # --- inbound webhook signal queue (Pro V3 buy/sell -> entry pipeline) ---
        self.signal_queue: "asyncio.Queue[InboundSignal]" = asyncio.Queue()
        # --- per-ticker entry switch (Telegram control) ---
        self.entries_enabled: dict[str, bool] = {}     # explicit overrides; missing = ON
        self.control = None                            # TelegramControl task holder
        # --- news-rip cooldown circuit breaker (config-gated; off => inert) ---
        self._cd_consec = 0          # consecutive losing closes (basket-wide)
        self._cd_until = 0.0         # epoch secs until which entries are blocked
        self._cd_armed = False       # cooldown window active (drives resume-notify)

    async def start(self):
        log.info("=" * 70)
        log.info("APEX PAPER BRIDGE — SMRT Pro V3 webhook · EMA9 retest · 3-stage trail exit")
        log.info("=" * 70)
        log.info("Host: %s", self.cfg.host)
        log.info("Paper collateral: $%.0f", self.cfg.initial_collateral_usdc)
        log.info("Symbols: %s", list(self.cfg.symbols.keys()))
        log.info("Entry: slope>=%.2f%%  body_band=%s  block_weekdays=%s",
                 self.cfg.entry.min_abs_slope_pct, self.cfg.entry.block_body_band,
                 self.cfg.entry.block_weekdays)
        log.info("Signal source: %s | Exit model: %s", self.cfg.signal_source, self.cfg.exit_model)
        log.info("Entry: EMA9 retest=%s%s", self.cfg.entry.require_retest,
                 "" if self.cfg.entry.min_abs_slope_pct or self.cfg.entry.block_body_band
                 or self.cfg.entry.block_weekdays else " (no extra filters)")
        log.info("Exits: SL=$%.0f BE=$%.0f trail_act=$%.0f trail_dist=$%.0f tp_ceiling=%.1fx",
                 self.cfg.exits.sl_loss_usdt, self.cfg.exits.breakeven_usdt,
                 self.cfg.exits.trail_activate_usdt, self.cfg.exits.trail_distance_usdt,
                 self.cfg.exits.tp_ceiling_pct)

        if self.cfg.cooldown.enabled:
            log.info("Cooldown breaker: %d consec losses -> block entries %dm (auto-resume)",
                     self.cfg.cooldown.consec_losses, self.cfg.cooldown.minutes)
        if self.cfg.sizing.mode == "compound":
            sz = self.cfg.sizing
            log.info("Sizing: COMPOUND off equity (base $%.0f -> base margin), cap %gx "
                     "(max margin = base x %g)", sz.base_equity, sz.cap_mult, sz.cap_mult)
        if self.cfg.withdrawal.enabled:
            wd = self.cfg.withdrawal
            log.info("Withdrawal: %s skim of realized equity above %gx ($%.0f); "
                     "total taken so far $%.2f", wd.cadence, wd.target_mult,
                     self.cfg.sizing.base_equity * wd.target_mult, self.db.withdrawn_total())

        # Lighter client + paper client
        self.api = lighter.ApiClient(configuration=lighter.Configuration(host=self.cfg.host))
        self.paper = lighter.PaperClient(self.api,
                                         initial_collateral_usdc=self.cfg.initial_collateral_usdc)

        # Load each enabled market's order book via a REST snapshot (NOT a WS
        # delta subscription — see LoopConfig for why). track_market_snapshot
        # both loads the market_config (sizing depends on this) and pulls the
        # first book snapshot. The mark is then kept fresh by mark_poll_loop().
        # A single market whose snapshot fails (e.g. BTC market_id=1) must NOT
        # kill the whole multi-coin bridge: skip it and trade the rest.
        configured = {n: s for n, s in self.cfg.symbols.items() if s.enabled}
        enabled: dict = {}
        for name, s in configured.items():
            log.info("%s: loading order-book snapshot (market_id=%d)", name, s.market_id)
            ok = await self._snapshot_with_retry(name, s.market_id, retries=5)
            if not ok:
                log.error("%s: order-book snapshot FAILED after retries (market_id=%d) — skipping",
                          name, s.market_id)
                continue
            enabled[name] = s
            self.pending[name] = []
        if not enabled:
            try:
                await notify.notify_error("Apex startup aborted: no market order books came up")
            except Exception:
                pass
            raise RuntimeError("no markets subscribed — cannot run")
        if len(enabled) < len(configured):
            dropped = sorted(set(configured) - set(enabled))
            log.warning("Running with %d/%d markets (dropped: %s)",
                        len(enabled), len(configured), dropped)

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

        # Restore per-ticker entry switches from a prior run (persisted overrides).
        self.entries_enabled = self.db.get_switches()
        _off = sorted(s for s, on in self.entries_enabled.items() if not on)
        if _off:
            log.info("Entry switch: NEW entries OFF for %s (restored from DB; "
                     "open positions still managed to exit)", _off)

        # Bar feeds (REST polling)
        for name, s in enabled.items():
            feed_cfg = BarFeedConfig(
                market_id=s.market_id,
                symbol=name,
                resolution=self.cfg.entry.timeframe,
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
        if self.cfg.notify.startup:
            await notify.notify_startup(self.cfg, restored=restored or None)

        # Kick off async tasks. Stagger the bar feeds across the poll interval so
        # the N coins never hit /candlesticks in one synchronized burst (that burst
        # tripped Lighter's WAF on 2026-06-04). With 10 coins @ 90s that's a candle
        # request ~every 9s — smooth and well under the WAF rate threshold.
        tasks = []
        n_feeds = len(self.feeds)
        stagger = (self.cfg.loop.bar_poll_interval_s / n_feeds) if n_feeds else 0.0
        for i, (name, feed) in enumerate(self.feeds.items()):
            tasks.append(asyncio.create_task(
                feed.run_loop(self.on_new_bar, start_delay_s=i * stagger)))
        tasks.append(asyncio.create_task(self.position_check_loop()))
        tasks.append(asyncio.create_task(self.heartbeat_loop()))
        tasks.append(asyncio.create_task(self.daily_summary_loop()))
        tasks.append(asyncio.create_task(self.mark_poll_loop(enabled)))
        # Webhook signal source (real Pro V3) — listener + queue consumer.
        if self.cfg.webhook.enabled:
            from .webhook import build_app, run_server   # lazy: fastapi/uvicorn only in webhook mode
            known = {n for n, s in enabled.items()}
            app = build_app(self.signal_queue, self.cfg.webhook.secret, known,
                            self.cfg.webhook.path)
            tasks.append(asyncio.create_task(
                run_server(app, self.cfg.webhook.host, self.cfg.webhook.port)))
            tasks.append(asyncio.create_task(self.webhook_consumer_loop()))
            log.info("Webhook listener on %s:%d%s (symbols=%s)",
                     self.cfg.webhook.host, self.cfg.webhook.port,
                     self.cfg.webhook.path, sorted(known))

        # Telegram control listener (per-ticker entry switch) — opt-in.
        if self.cfg.control.telegram_enabled:
            from .telegram_control import TelegramControl
            self.control = TelegramControl(
                token=os.environ.get("TELEGRAM_BOT_TOKEN"),
                chat_id=os.environ.get("TELEGRAM_CHAT_ID"),
                known_symbols=list(enabled.keys()),
                on_set_switch=self.on_set_switch,
                on_force_close=self.force_close,
                on_status=self.on_status,
            )
            tasks.append(asyncio.create_task(self.control.run_loop()))
        log.info(
            "Bridge running. %d bar-feed tasks + position checker + heartbeat + "
            "daily summary + mark poller (REST snapshot every %ds).",
            len(self.feeds), self.cfg.loop.mark_poll_interval_s,
        )

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        log.info("Stopping bridge...")
        self._stopped = True
        if self.control is not None:
            self.control.stop()
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

        # Enrich with V3 signals + ema/slope
        enriched = prepare(
            df,
            sensitivity=self.cfg.pine.sensitivity,
            noise=self.cfg.pine.noise,
            fakeout=self.cfg.pine.fakeout,
            range_filt=self.cfg.pine.range_filter,
            ema_period=self.cfg.entry.ema_period,
            slope_lookback=self.cfg.entry.slope_lookback_bars,
        )
        self.bars[symbol] = enriched

        last = enriched.iloc[-1]
        last_ts = enriched.index[-1]

        if is_bootstrap:
            n_buy = int(enriched["buy_sig"].sum())
            n_sell = int(enriched["sell_sig"].sum())
            log.info("%s: bootstrap signal scan — %d buy + %d sell over %d bars (most recent %s)",
                     symbol, n_buy, n_sell, len(enriched), last_ts)
            return  # don't fire on historical signals — only NEW bars trigger entries

        # NEW signal on the just-closed bar? (replica source only; webhook mode
        # gets its signals from the inbound Pro V3 queue, not the HA-flip replica)
        if self.cfg.signal_source != "webhook" and bool(last["buy_sig"]):
            self.pending[symbol].append(PendingSignal(symbol, "long", last_ts, len(enriched) - 1))
            log.info("%s: NEW BUY signal @ %s  (close=%.4f)", symbol, last_ts, last["Close"])
            self.db.log_signal(symbol=symbol, side="long",
                               bar_time=str(last_ts), outcome="detected",
                               ema9=float(last["ema9"]), slope_pct=float(last["slope_pct"]),
                               body_atr_ratio=float(last["body_atr_ratio"]),
                               detected_at=datetime.now(timezone.utc).isoformat())
        if self.cfg.signal_source != "webhook" and bool(last["sell_sig"]):
            self.pending[symbol].append(PendingSignal(symbol, "short", last_ts, len(enriched) - 1))
            log.info("%s: NEW SELL signal @ %s  (close=%.4f)", symbol, last_ts, last["Close"])
            self.db.log_signal(symbol=symbol, side="short",
                               bar_time=str(last_ts), outcome="detected",
                               ema9=float(last["ema9"]), slope_pct=float(last["slope_pct"]),
                               body_atr_ratio=float(last["body_atr_ratio"]),
                               detected_at=datetime.now(timezone.utc).isoformat())

        # Process pending queue using the latest bar as the retest candidate
        await self.process_pending(symbol, enriched)

    async def process_pending(self, symbol: str, enriched: pd.DataFrame):
        """For each pending signal, check if the latest bar confirms entry."""
        if not self.pending[symbol]:
            return
        last_idx = len(enriched) - 1
        last = enriched.iloc[-1]
        last_ts = enriched.index[-1]
        ema_v = float(last["ema9"])
        slope_v = float(last["slope_pct"])
        body_v = float(last["body_atr_ratio"])
        bar_low = float(last["Low"])
        bar_high = float(last["High"])
        new_pending: list[PendingSignal] = []

        for sig in self.pending[symbol]:
            age = last_idx - sig.detected_at_bar_idx
            if age > self.cfg.entry.retest_timeout_bars:
                log.info("%s: pending %s expired (%d bars old)", symbol, sig.side, age)
                self.db.log_signal(symbol=symbol, side=sig.side, bar_time=str(sig.detected_at_bar_ts),
                                   outcome="expired",
                                   detected_at=datetime.now(timezone.utc).isoformat())
                continue

            # EMA(9) retest
            if not check_retest(sig.side, ema_v, bar_low, bar_high,
                                self.cfg.entry.retest_overshoot_pct):
                new_pending.append(sig)
                continue

            # Base slope gate (matches sweep behavior — keep pending alive if slope too flat)
            if abs(slope_v) < 0.03:
                new_pending.append(sig)
                continue

            # Position lock
            if self.executor.is_open(symbol):
                new_pending.append(sig)
                continue

            # Per-ticker entry switch (keep pending so it can fire once re-enabled)
            if not self._entries_allowed(symbol):
                new_pending.append(sig)
                continue

            # Locked-config entry filters (slope >= 0.12, body band, weekday)
            if not passes_entry_filters(last_ts, slope_v, body_v,
                                        self.cfg.entry.block_weekdays,
                                        self.cfg.entry.min_abs_slope_pct,
                                        self.cfg.entry.block_body_band):
                log.info("%s: %s blocked by entry filters (slope=%.3f body=%.3f weekday=%d)",
                         symbol, sig.side, slope_v, body_v, last_ts.weekday())
                self.db.log_signal(symbol=symbol, side=sig.side, bar_time=str(sig.detected_at_bar_ts),
                                   outcome="blocked_filter",
                                   detected_at=datetime.now(timezone.utc).isoformat())
                continue

            # Fire entry
            pos = await self.executor.open_position(symbol, sig.side)
            if pos is None:
                new_pending.append(sig)
                continue
            # Persist trade
            trade_id = self.db.log_trade(
                symbol=symbol, side=sig.side,
                entry_price=pos.entry_price,
                margin_usdt=pos.margin_usdt, leverage=pos.leverage,
                base_amount=pos.base_amount, notional=pos.notional,
                opened_at=datetime.now(timezone.utc).isoformat(),
                bar_time_open=str(last_ts),
                slope_pct=slope_v, body_atr_ratio=body_v,
                adx_at_entry=float(last.get("adx", 0)),
            )
            self.trade_ids[symbol] = trade_id
            # Telegram alert (fire-and-forget)
            if self.cfg.notify.open:
                asyncio.create_task(notify.notify_open(pos))
            self.db.log_signal(symbol=symbol, side=sig.side, bar_time=str(sig.detected_at_bar_ts),
                               outcome="fired", ema9=ema_v, slope_pct=slope_v,
                               body_atr_ratio=body_v,
                               detected_at=datetime.now(timezone.utc).isoformat())
            # Drop other same-side pendings; opposite-side stays (matches live)
            new_pending = [p for p in new_pending if p.side != sig.side]
        self.pending[symbol] = new_pending

    async def webhook_consumer_loop(self):
        """Drain inbound Pro V3 webhooks. buy/sell -> EMA9-retest entry pipeline."""
        log.info("Webhook consumer started")
        while not self._stopped:
            sig = await self.signal_queue.get()
            symbol, action = sig.symbol_key, sig.action
            if symbol not in self.pending:
                log.warning("webhook: %s not enabled — dropping %s", symbol, action)
                continue

            # ----- ENTRY events (buy/sell) -----
            side = "long" if action == "buy" else "short"
            df = self.bars.get(symbol)
            if df is None or len(df) == 0:
                log.warning("webhook: %s has no bars yet — dropping %s", symbol, action)
                continue
            ts = df.index[-1]
            self.pending[symbol].append(PendingSignal(symbol, side, ts, len(df) - 1))
            log.info("%s: PRO V3 %s webhook -> pending (anchor bar %s)", symbol, side, ts)
            self.db.log_signal(symbol=symbol, side=side, bar_time=str(ts),
                               outcome="detected",
                               detected_at=datetime.now(timezone.utc).isoformat())
            await self.process_pending(symbol, df)

    # ------- Per-ticker entry switch (Telegram control) -------

    def _entries_allowed(self, symbol: str) -> bool:
        """Gate checked at EVERY entry-decision point. Missing = ON (default).
        A live cooldown blocks every symbol regardless of its per-ticker switch."""
        if self._cooldown_active():
            return False
        from .telegram_control import entries_allowed
        return entries_allowed(self.entries_enabled, symbol)

    def _cooldown_active(self) -> bool:
        if not self.cfg.cooldown.enabled:
            return False
        return self._cd_until > 0 and time.time() < self._cd_until

    def _register_close(self, reason: str, pnl: float):
        """Feed each booked close to the basket-wide cooldown breaker.
        After `consec_losses` losing closes in a row, block ALL entries for
        `minutes`, then auto-resume. Manual/kill closes do not count."""
        cd = self.cfg.cooldown
        if not cd.enabled or reason == "manual":
            return
        if pnl < 0:
            self._cd_consec += 1
            if self._cd_consec >= cd.consec_losses and not self._cooldown_active():
                self._cd_until = time.time() + cd.minutes * 60
                self._cd_consec = 0
                self._cd_armed = True
                log.warning("COOLDOWN armed: %d consec losses -> all entries blocked %dm",
                            cd.consec_losses, cd.minutes)
                if self.cfg.notify.close:
                    asyncio.create_task(notify.send(
                        f"\U0001f9ca COOLDOWN \u2014 {cd.consec_losses} losing closes in a row. "
                        f"All entries paused {cd.minutes}m (auto-resume)."))
        else:
            self._cd_consec = 0

    def _maybe_notify_cooldown_resume(self):
        if self._cd_armed and not self._cooldown_active():
            self._cd_armed = False
            log.info("COOLDOWN lifted - entries resume.")
            if self.cfg.notify.close:
                asyncio.create_task(notify.send("\u2705 Cooldown lifted \u2014 entries resume."))

    async def on_set_switch(self, symbol: str, enabled: bool) -> str:
        """Telegram /on|/off callback. Persists, then (on OFF) drops any
        not-yet-fired pending signal so no NEW position opens. Open positions
        are deliberately left running so the bridge babysits them to exit."""
        self.entries_enabled[symbol] = enabled
        self.db.set_switch(symbol, enabled)
        note = ""
        if not enabled:
            if self.pending.get(symbol):
                self.pending[symbol] = []
                note = " · cleared pending signals"
            if self.executor is not None and self.executor.is_open(symbol):
                note += " · open position still managed to exit"
        state = "🟢 ON" if enabled else "⛔ OFF"
        log.info("Entry switch: %s entries %s%s", symbol, "ON" if enabled else "OFF", note)
        return f"{symbol} entries {state}{note}"

    async def force_close(self, symbol: str) -> str:
        """Telegram /close callback. Flatten any open position at the live mark."""
        pos = self.executor.positions.get(symbol) if self.executor else None
        if pos is None:
            return f"{symbol}: no open position"
        await self._close_generic(symbol, pos, "manual")
        return f"🔻 {symbol}: force-closed {pos.side} @ entry ${pos.entry_price:.4f} (manual)"

    async def _close_generic(self, symbol: str, pos, reason: str):
        """Close + book a trail/replica-mode position (fallback for /close)."""
        result = await self.executor.close_position(symbol, reason)
        if result is None:
            return
        exit_p = result.avg_price
        pnl = ((exit_p - pos.entry_price) if pos.side == "long"
               else (pos.entry_price - exit_p)) * pos.base_amount
        duration = int(time.time() - pos.opened_at)
        tid = self.trade_ids.get(symbol)
        if tid is not None:
            self.db.update_trade_close(
                tid, exit_price=exit_p, exit_reason=reason, pnl_usdt=pnl,
                pnl_pct_account=pnl / self.cfg.initial_collateral_usdc * 100,
                duration_secs=duration, max_state=pos.max_state,
                closed_at=datetime.now(timezone.utc).isoformat())
            del self.trade_ids[symbol]
        if self.cfg.notify.close:
            asyncio.create_task(notify.notify_close(
                symbol, pos.side, pos.entry_price, exit_p, pnl, reason, duration,
                pos.max_state, starting_collateral=self.cfg.initial_collateral_usdc))

    async def on_status(self) -> str:
        """Telegram /status callback — per-symbol entry switch + position state."""
        if self.executor is None:
            return "Apex starting up…"
        lines = ["📋 <b>Apex status</b>"]
        if self._cooldown_active():
            mins = int((self._cd_until - time.time()) / 60) + 1
            lines.append(f"🧊 COOLDOWN active \u2014 entries blocked ~{mins}m more")
        if self.cfg.sizing.mode == "compound":
            eq = self._equity_breakdown()[2]
            mult = (eq / self.cfg.sizing.base_equity) if self.cfg.sizing.base_equity else 1.0
            lines.append(f"\U0001f4c8 Sizing: compound {min(mult, self.cfg.sizing.cap_mult):.2f}x "
                         f"(cap {self.cfg.sizing.cap_mult:g}x) · equity ${eq:,.0f}")
        _wtot = self.db.withdrawn_total()
        if _wtot > 0:
            lines.append(f"\U0001f4b5 Withdrawn to date: ${_wtot:,.2f}")
        for sym in sorted(self.executor.symbols):
            on = self._entries_allowed(sym)
            pos = self.executor.positions.get(sym)
            if pos is not None:
                where = f"{pos.side} open @ ${pos.entry_price:.4f}"
            elif self.pending.get(sym):
                where = "pending signal"
            else:
                where = "flat"
            lines.append(f"{'🟢' if on else '⛔'} {sym}: entries "
                         f"{'on' if on else 'OFF'} · {where}")
        return "\n".join(lines)

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
                        # Feed the 3-loss cooldown breaker (trail mode).
                        self._register_close(decision.reason, pnl)
                        # Telegram close alert
                        if self.cfg.notify.close:
                            asyncio.create_task(notify.notify_close(
                                symbol, pos.side, pos.entry_price, exit_p,
                                pnl, decision.reason, duration, pos.max_state,
                                starting_collateral=self.cfg.initial_collateral_usdc,
                            ))
            await asyncio.sleep(self.cfg.loop.position_check_interval_s)

    def _equity_breakdown(self) -> tuple[float, float, float]:
        """Return (realized_pnl, unrealized_pnl, total_equity).

        realized_pnl  = sum of closed-trade PnL from DB (gross, pre-withdrawal)
        unrealized_pnl = sum of pnl_at_mark over open positions
        total_equity   = initial_collateral + realized + unrealized - withdrawn
        (withdrawn = profit already skimmed off the account; see withdrawals ledger)
        """
        stats = self.db.summary()
        realized = float(stats.get("net_pnl") or 0.0)
        unrealized = 0.0
        if self.executor is not None:
            for sym in list(self.executor.positions.keys()):
                v = self.executor.pnl_at_mark(sym)
                if v is not None:
                    unrealized += float(v)
        equity = (float(self.cfg.initial_collateral_usdc) + realized + unrealized
                  - self.db.withdrawn_total())
        return realized, unrealized, equity

    # ---- compounding sizing + profit withdrawal (config-gated; see sizing.py) ----
    def _entry_margin(self, symbol: str) -> float:
        """Margin to post for a NEW entry. Compounding (capped) when enabled, else
        the symbol's configured fixed margin. Sizes off live account equity, so it
        scales up as the account grows and down on drawdown."""
        base = self.cfg.symbols[symbol].margin_usdt
        sz = self.cfg.sizing
        if sz.mode != "compound":
            return base
        return sizing.compound_margin(base, self._equity_breakdown()[2], sz.base_equity, sz.cap_mult)

    def _realized_equity(self) -> float:
        """Realized-only account equity for the withdrawal decision — never skims
        open (unrealized) P&L. = initial_collateral + realized - withdrawn."""
        realized = float(self.db.summary().get("net_pnl") or 0.0)
        return float(self.cfg.initial_collateral_usdc) + realized - self.db.withdrawn_total()

    def _same_withdrawal_period(self, last_iso: str, cadence: str) -> bool:
        """True if `last_iso` falls in the current cadence period (so we don't
        withdraw twice in one ISO week / day). Survives restarts via the ledger."""
        try:
            last = datetime.fromisoformat(last_iso)
        except (ValueError, TypeError):
            return False
        now = datetime.now(timezone.utc)
        if cadence == "daily":
            return last.date() == now.date()
        return last.isocalendar()[:2] == now.isocalendar()[:2]   # weekly (ISO year, week)

    def _maybe_withdraw(self):
        """Once per cadence period, skim realized equity above target to the ledger."""
        wd = self.cfg.withdrawal
        if not wd.enabled:
            return
        last = self.db.last_withdrawal_ts()
        if last is not None and self._same_withdrawal_period(last, wd.cadence):
            return
        realized_eq = self._realized_equity()
        surplus = sizing.withdrawal_surplus(realized_eq, self.cfg.sizing.base_equity, wd.target_mult)
        if surplus <= 0:
            return
        after = realized_eq - surplus
        self.db.record_withdrawal(surplus, realized_eq, after,
                                  note=f"{wd.cadence} skim > {wd.target_mult:g}x")
        total = self.db.withdrawn_total()
        log.warning("WITHDRAWAL: skimmed $%.2f (realized equity $%.2f -> $%.2f); total taken $%.2f",
                    surplus, realized_eq, after, total)
        asyncio.create_task(notify.send(
            f"\U0001f4b5 Withdrawal: skimmed ${surplus:,.2f} profit — account back to "
            f"${after:,.0f} (target {wd.target_mult:g}x). Total taken to date: ${total:,.2f}."))

    async def heartbeat_loop(self):
        """Every 5 minutes, snapshot total equity (collateral + realized + unrealized)."""
        last_seen_at = time.time()
        while not self._stopped:
            self._maybe_notify_cooldown_resume()
            if self.executor is None:
                await asyncio.sleep(5)
                continue
            try:
                self._maybe_withdraw()                      # weekly profit skim (config-gated)
                realized, unrealized, equity = self._equity_breakdown()   # equity = net (post-withdrawal)
                withdrawn = self.db.withdrawn_total()
                # The curve tracks GROSS trading value so weekly skims don't read as
                # drawdowns; the live "equity" balance is the net (post-withdrawal) figure.
                gross_equity = equity + withdrawn
                total_pnl = gross_equity - self.cfg.initial_collateral_usdc
                free_basis = self.cfg.initial_collateral_usdc + realized
                n_open = len(self.executor.positions)
                self.db.snapshot_account(free_basis, gross_equity, n_open, total_pnl)
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
                wd_str = f"  withdrawn=${withdrawn:,.2f}" if withdrawn > 0 else ""
                log.info(
                    "HEARTBEAT  equity=$%.2f  realized=$%+.2f  unrealized=$%+.2f  open=%d%s%s",
                    equity, realized, unrealized, n_open, wd_str, marks_str,
                )
                last_seen_at = time.time()
            except Exception as exc:
                log.error("Heartbeat error: %s", exc, exc_info=True)
                if time.time() - last_seen_at > 1800:
                    asyncio.create_task(notify.notify_error(
                        f"Heartbeat silent for >30m: {exc}"
                    ))
            await asyncio.sleep(300)

    async def _verify_mark_feed_live(self, enabled: dict, deadline_s: int):
        """Block until every enabled symbol has reported a non-zero mark price,
        or fail fast. Refusing to start blind is intentional — see incident
        2026-05-23 (position #16 stuck on a never-updating feed).
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
                    f"Bridge startup aborted: mark feed silent for {not_ready}"
                )
            except Exception:
                pass
            raise RuntimeError(f"mark feed dead for {not_ready}")
        log.info("Mark feed verified live for all symbols.")

    async def _snapshot_with_retry(self, name: str, market_id: int,
                                   retries: int = 5) -> bool:
        """Load a market's order book via a REST snapshot, with retries.

        track_market_snapshot() loads the market_config (sizing depends on it)
        AND pulls the first book snapshot — no WebSocket is opened. The mark is
        kept fresh thereafter by mark_poll_loop(). Returns True once the book
        has a usable mid, False if all attempts fail.
        """
        for attempt in range(1, retries + 1):
            try:
                await self.paper.track_market_snapshot(market_id=market_id)
                book = self.paper.order_books.get(market_id)
                if book is not None and book.mid_price:
                    if attempt > 1:
                        log.info("%s: snapshot OK on attempt %d", name, attempt)
                    return True
                raise RuntimeError("snapshot returned an empty/zero book")
            except Exception as exc:
                log.warning("%s: snapshot attempt %d/%d failed (market_id=%d): %r",
                            name, attempt, retries, market_id, exc)
                await asyncio.sleep(min(2 * attempt, 8))
        return False

    async def mark_poll_loop(self, enabled: dict):
        """Keep every market's mark fresh by polling a REST order-book snapshot.

        Pure infrastructure — does NOT gate, block, or filter any trade; the
        state machine keeps ticking on whatever get_mark_price returns. This
        REPLACES the old WS delta subscription + reconnect watchdog: the SDK's
        WS path re-sorted the entire book on every delta message, pegging a CPU
        core and starving the event loop (incident 2026-05-31). A small periodic
        REST snapshot yields an identical mid at ~zero CPU and removes the whole
        WS-lifecycle failure class (keepalive drops, reconnect churn, subscribe
        flakiness).

        Markets are refreshed ROUND-ROBIN — one order-book request every `gap`
        seconds — NOT all-at-once. Bursting N concurrent requests every few
        seconds is what tripped Lighter's WAF on the order-book path with the
        10-coin basket (incident 2026-06-04, same class as the candle-feed burst):
        each market is polled once per `mark_poll_interval_s`, but the requests are
        spread evenly so the steady rate (~N / interval per sec) stays at the
        proven-safe ~1.3 req/s the 4-coin basket ran at for days. We track each
        market's last SUCCESSFUL poll; if a market with an OPEN position has had no
        successful poll past fatal_s — so its SL/TP can't be checked on a fresh
        mark — exit so Docker restarts with a clean client. A FLAT market that goes
        dark is logged but never restarts the whole bridge.
        """
        period_s = self.cfg.loop.mark_poll_interval_s   # per-market refresh period
        warn_s = self.cfg.loop.mark_stale_warn_s
        fatal_s = self.cfg.loop.mark_stale_fatal_s
        names = list(self.executor.symbols.keys())
        n = max(1, len(names))
        gap = max(0.2, period_s / n)                     # time between single requests
        log.info("Mark poller started (round-robin: 1 req/%.1fs, each market every "
                 "%ds; warn=%ds fatal=%ds)", gap, period_s, warn_s, fatal_s)
        last_ok: dict[str, float] = {name: time.monotonic() for name in names}
        last_warn: dict[str, float] = {}
        idx = 0
        while not self._stopped:
            await asyncio.sleep(gap)
            if self.executor is None or self.paper is None:
                continue
            name = names[idx % n]
            idx += 1
            mid = self.executor.symbols[name]["market_id"]
            try:
                # per-request timeout so one hung call can't stall the round-robin
                await asyncio.wait_for(self.paper.refresh_order_book(mid), timeout=5.0)
                last_ok[name] = time.monotonic()
            except Exception as exc:
                now_mono = time.monotonic()
                stale = now_mono - last_ok.get(name, now_mono)
                if stale >= warn_s and now_mono - last_warn.get(name, 0.0) >= 60:
                    log.warning("%s: order-book snapshot failing for %.0fs: %r",
                                name, stale, exc)
                    last_warn[name] = now_mono
                if stale >= fatal_s and self.executor.is_open(name):
                    msg = (f"Mark for {name} stale for {stale:.0f}s (>= fatal "
                           f"{fatal_s}s) with an OPEN position — REST snapshot "
                           f"polling keeps failing. Exiting for a clean restart.")
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
                if self.cfg.notify.daily:
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
            if side == "short":
                extremes = [float(c["l"]) for c in candles]
                trail_high = min(extremes) if extremes else entry_price
            else:
                extremes = [float(c["h"]) for c in candles]
                trail_high = max(extremes) if extremes else entry_price

            order_side = (
                lighter.PaperOrderSide.BUY if side == "long" else lighter.PaperOrderSide.SELL
            )
            base_amount = self.executor.quantize_size(
                sym_cfg["market_id"], float(row["base_amount"]))
            try:
                result = await self.paper.create_paper_order(lighter.PaperOrderRequest(
                    market_id=sym_cfg["market_id"],
                    side=order_side,
                    base_amount=base_amount,
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
                base_amount=base_amount,
                margin_usdt=float(row["margin_usdt"]),
                leverage=float(row["leverage"]),
                opened_at=opened_at_unix,
                notional=float(row["notional"]),
                trail_high=trail_high,
            )
            self.executor.positions[symbol] = pos
            self.trade_ids[symbol] = int(row["id"])
            restored.append(pos)
            log.info(
                "  restored #%d %s %s entry=$%.4f size=%g trail_high=$%.4f rehydrated@$%.4f",
                row["id"], symbol, side.upper(), entry_price, pos.base_amount,
                trail_high, rehydration_price,
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
