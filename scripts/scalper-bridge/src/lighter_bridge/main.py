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
from .signals import (
    check_retest, compute_ema_and_slope, generate_v3_signals,
    passes_entry_filters, prepare,
)
from .state_machine import step as state_step
from . import scaleout as so
from .squeeze import prepare_squeeze
from .regime import prepare_regime, entry_levels
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
        # --- scale-out / webhook mode state (unused in legacy trail/replica mode) ---
        self.signal_queue: "asyncio.Queue[InboundSignal]" = asyncio.Queue()
        self.scale: dict[str, so.ScaleOutState] = {}   # symbol -> exit state
        self.orig_base: dict[str, float] = {}          # symbol -> original base_amount
        self.realized: dict[str, float] = {}           # symbol -> realized pnl so far
        self.legs: dict[str, list] = {}                # symbol -> [(frac, exit_px, reason)]
        self.tp_seen: dict[str, set] = {}              # symbol -> {tp levels already closed} (pro_v3 mode)
        self.pending_entry: dict[str, dict] = {}       # symbol -> resting maker-limit entry (regime mode)
        # --- per-ticker entry switch (Telegram control) ---
        self.entries_enabled: dict[str, bool] = {}     # explicit overrides; missing = ON
        self.control = None                            # TelegramControl task holder
        # --- news-rip cooldown circuit breaker (config-gated; off => inert) ---
        self._cd_consec = 0          # consecutive losing closes (basket-wide)
        self._cd_until = 0.0         # epoch secs until which entries are blocked
        self._cd_armed = False       # cooldown window active (drives resume-notify)

    async def start(self):
        log.info("=" * 70)
        log.info("LIGHTER PAPER BRIDGE — Pro V3 locked config")
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
        if self.cfg.exit_model == "scaleout":
            sc = self.cfg.scaleout
            log.info("Scale-out: SL=%.2f×ATR TP=%s×ATR ratios=%s BE-after-TP1=%s",
                     sc.sl_atr, sc.tp_atr, sc.ratios, sc.be_after_tp1)
        elif self.cfg.exit_model == "pro_v3":
            log.info("Exits: Pro V3 TP1/2/3 + SL (verbatim), close %s of original",
                     tuple(self.cfg.scaleout.ratios))
        elif self.cfg.exit_model == "atr_trail":
            sq = self.cfg.squeeze
            log.info("Squeeze: BB(%d,%.1f)-in-KC(%.1f) >=%d bars | SL=%.2f×ATR trail=%.2f×ATR "
                     "max_bars=%d | risk=%.2f%%/trade lev<=%.0f×",
                     sq.bb_len, sq.bb_mult, sq.kc_mult, sq.min_squeeze, sq.sl_atr,
                     sq.trail_atr, sq.max_bars, sq.risk_frac * 100, sq.max_leverage)
        elif self.cfg.exit_model == "regime":
            rg = self.cfg.regime
            log.info("Regime-MR: EMA(%d) slope-gate | z(%d) fade |z|>=%.2f | maker-limit %.2f×ATR "
                     "| SL=%.2f×ATR TP=%.2f×dist-to-VWAP time=%d bars",
                     rg.trend_len, rg.z_period, rg.z_entry, rg.limit_atr,
                     rg.sl_atr, rg.tp_frac, rg.max_bars)
        else:
            log.info("Exits: SL=$%.0f BE=$%.0f lock_act=$%.0f trail_act=$%.0f trail_dist=$%.0f",
                     self.cfg.exits.sl_loss_usdt, self.cfg.exits.breakeven_usdt,
                     self.cfg.exits.lock_profit_activate_usdt,
                     self.cfg.exits.trail_activate_usdt, self.cfg.exits.trail_distance_usdt)

        if self.cfg.cooldown.enabled:
            log.info("Cooldown breaker: %d consec losses -> block entries %dm (auto-resume)",
                     self.cfg.cooldown.consec_losses, self.cfg.cooldown.minutes)

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
                await notify.notify_error("Scalper startup aborted: no market order books came up")
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
        if self.cfg.signal_source == "squeeze":
            await self._on_new_bar_squeeze(symbol, df)
            return
        if self.cfg.signal_source == "regime":
            await self._on_new_bar_regime(symbol, df)
            return

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
            # Arm exit tracking (no-op in trail mode)
            if self.cfg.exit_model in ("scaleout", "pro_v3"):
                self.orig_base[symbol] = pos.base_amount
                self.realized[symbol] = 0.0
                self.legs[symbol] = []
                self.tp_seen[symbol] = set()
                if self.cfg.exit_model == "scaleout":
                    atr_v = float(last.get("atr14", 0.0) or 0.0)
                    sc = self.cfg.scaleout
                    params = so.ScaleOutParams(sl_atr=sc.sl_atr, tp_atr=tuple(sc.tp_atr),
                                               ratios=tuple(sc.ratios), be_after_tp1=sc.be_after_tp1)
                    self.scale[symbol] = so.init_levels(sig.side, pos.entry_price, atr_v, params)
                    stx = self.scale[symbol]
                    log.info("%s: scale-out armed entry=%.4f ATR=%.4f SL=%.4f TP=%s",
                             symbol, pos.entry_price, atr_v, stx.sl_price,
                             [round(x, 4) for x in stx.tp_px])
                else:  # pro_v3 — exits come from Pro V3's TP1/2/3 + SL webhooks
                    log.info("%s: Pro V3 exits armed entry=%.4f — awaiting TP1/2/3 + SL "
                             "(close %s of original)", symbol, pos.entry_price,
                             tuple(self.cfg.scaleout.ratios))
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
        """Drain inbound Pro V3 webhooks. buy/sell -> entry pipeline;
        tp1/tp2/tp3/sl -> Pro V3 exit handler (event-driven, verbatim)."""
        log.info("Webhook consumer started")
        while not self._stopped:
            sig = await self.signal_queue.get()
            symbol, action = sig.symbol_key, sig.action
            if symbol not in self.pending:
                log.warning("webhook: %s not enabled — dropping %s", symbol, action)
                continue

            # ----- Pro V3 EXIT events -----
            if action in ("tp1", "tp2", "tp3", "sl"):
                await self._handle_exit_action(symbol, action)
                continue
            if action in ("reversal_buy", "reversal_sell"):
                # Defensive: a reversal closes the current position; the matching
                # buy/sell alert opens the new one through the normal entry gate.
                await self._handle_exit_action(symbol, "sl")
                continue

            # ----- ENTRY events (buy/sell) -----
            side = "long" if action == "buy" else "short"
            df = self.bars.get(symbol)
            if df is None or len(df) == 0:
                log.warning("webhook: %s has no bars yet — dropping %s", symbol, action)
                continue
            if not self.cfg.entry.require_retest:
                await self._open_on_signal(symbol, side, df)
                continue
            ts = df.index[-1]
            self.pending[symbol].append(PendingSignal(symbol, side, ts, len(df) - 1))
            log.info("%s: PRO V3 %s webhook -> pending (anchor bar %s)", symbol, side, ts)
            self.db.log_signal(symbol=symbol, side=side, bar_time=str(ts),
                               outcome="detected",
                               detected_at=datetime.now(timezone.utc).isoformat())
            await self.process_pending(symbol, df)

    async def _handle_exit_action(self, symbol: str, action: str):
        """Execute a Pro V3 exit webhook (tp1/tp2/tp3/sl) on the open position.

        Scaling is the configured per-TP fraction of the ORIGINAL size
        (scaleout.ratios, e.g. 50/25/25). tp3 and sl always flatten whatever
        remains. No bridge-computed levels — Pro V3 owns the exit prices.
        """
        pos = self.executor.positions.get(symbol)
        if pos is None:
            log.info("%s: Pro V3 %s but no open position — ignoring", symbol, action)
            return
        seen = self.tp_seen.setdefault(symbol, set())
        if action in seen:
            log.info("%s: Pro V3 %s already processed — ignoring duplicate", symbol, action)
            return
        seen.add(action)

        # tp3 / sl flatten the remainder; tp1/tp2 close their fraction of original.
        if action in ("tp3", "sl"):
            res = await self.executor.reduce_position(symbol, pos.base_amount, action)
            if res is not None:
                self._book_leg(symbol, pos, res, action)
            self._finalize_scaleout(symbol, pos)
            return

        ratios = {"tp1": self.cfg.scaleout.ratios[0], "tp2": self.cfg.scaleout.ratios[1]}
        res = await self.executor.reduce_position(symbol, ratios[action] * self.orig_base[symbol], action)
        if res is not None:
            self._book_leg(symbol, pos, res, action)
        if not self.executor.is_open(symbol):
            self._finalize_scaleout(symbol, pos)

    def _book_leg(self, symbol: str, pos, res, reason: str):
        exit_p, filled = res.avg_price, res.filled_size
        leg_pnl = ((exit_p - pos.entry_price) if pos.side == "long"
                   else (pos.entry_price - exit_p)) * filled
        self.realized[symbol] = self.realized.get(symbol, 0.0) + leg_pnl
        frac = round(filled / self.orig_base.get(symbol, filled), 4) if self.orig_base.get(symbol) else 1.0
        self.legs.setdefault(symbol, []).append((frac, exit_p, reason))
        log.info("%s: %s leg %.4f @ $%.4f  pnl=$%+.2f", symbol, reason, filled, exit_p, leg_pnl)

    async def _open_on_signal(self, symbol: str, side: str, df: pd.DataFrame):
        """Open a position immediately on a raw Pro V3 webhook (no retest/filters)."""
        last = df.iloc[-1]
        last_ts = df.index[-1]
        now = datetime.now(timezone.utc).isoformat()
        if self.executor.is_open(symbol):
            log.info("%s: PRO V3 %s — position already open, skipping (one at a time)", symbol, side)
            self.db.log_signal(symbol=symbol, side=side, bar_time=str(last_ts),
                               outcome="skipped_open", detected_at=now)
            return
        if not self._entries_allowed(symbol):
            log.info("%s: entries OFF — dropping PRO V3 %s", symbol, side)
            self.db.log_signal(symbol=symbol, side=side, bar_time=str(last_ts),
                               outcome="blocked_switch", detected_at=now)
            return
        log.info("%s: PRO V3 %s webhook -> ENTER (raw signal)", symbol, side)
        pos = await self.executor.open_position(symbol, side)
        if pos is None:
            log.error("%s: open_position failed for %s", symbol, side)
            return
        trade_id = self.db.log_trade(
            symbol=symbol, side=side, entry_price=pos.entry_price,
            margin_usdt=pos.margin_usdt, leverage=pos.leverage,
            base_amount=pos.base_amount, notional=pos.notional,
            opened_at=now, bar_time_open=str(last_ts),
            slope_pct=float(last.get("slope_pct", 0)),
            body_atr_ratio=float(last.get("body_atr_ratio", 0)),
            adx_at_entry=float(last.get("adx", 0)),
        )
        self.trade_ids[symbol] = trade_id
        atr_v = float(last.get("atr14", 0.0) or 0.0)
        sc = self.cfg.scaleout
        params = so.ScaleOutParams(sl_atr=sc.sl_atr, tp_atr=tuple(sc.tp_atr),
                                   ratios=tuple(sc.ratios), be_after_tp1=sc.be_after_tp1)
        self.scale[symbol] = so.init_levels(side, pos.entry_price, atr_v, params)
        self.orig_base[symbol] = pos.base_amount
        self.realized[symbol] = 0.0
        self.legs[symbol] = []
        stx = self.scale[symbol]
        log.info("%s: scale-out armed entry=%.4f ATR=%.4f SL=%.4f TP=%s",
                 symbol, pos.entry_price, atr_v, stx.sl_price,
                 [round(x, 4) for x in stx.tp_px])
        self.db.log_signal(symbol=symbol, side=side, bar_time=str(last_ts),
                           outcome="fired", ema9=float(last.get("ema9", 0)),
                           slope_pct=float(last.get("slope_pct", 0)),
                           body_atr_ratio=float(last.get("body_atr_ratio", 0)),
                           detected_at=now)
        if self.cfg.notify.open:
            asyncio.create_task(notify.notify_open(pos))

    async def _scaleout_tick(self, symbol: str, pos, mark: float):
        """One scale-out evaluation for an open position; executes partial closes."""
        st = self.scale.get(symbol)
        if st is None:
            return
        sc = self.cfg.scaleout
        params = so.ScaleOutParams(sl_atr=sc.sl_atr, tp_atr=tuple(sc.tp_atr),
                                   ratios=tuple(sc.ratios), be_after_tp1=sc.be_after_tp1)
        decision = so.step(st, mark, params)
        if decision.be_moved:
            log.info("%s: TP1 hit -> stop moved to breakeven $%.4f", symbol, st.entry_price)
        for frac, reason in decision.closes:
            res = await self.executor.reduce_position(symbol, frac * self.orig_base[symbol], reason)
            if res is None:
                continue
            exit_p, filled = res.avg_price, res.filled_size
            leg_pnl = ((exit_p - pos.entry_price) if pos.side == "long"
                       else (pos.entry_price - exit_p)) * filled
            self.realized[symbol] = self.realized.get(symbol, 0.0) + leg_pnl
            self.legs[symbol].append((frac, exit_p, reason))
            log.info("%s: %s leg %.4f @ $%.4f  pnl=$%+.2f", symbol, reason, filled, exit_p, leg_pnl)
        if decision.done or not self.executor.is_open(symbol):
            self._finalize_scaleout(symbol, pos)

    def _finalize_scaleout(self, symbol: str, pos):
        legs = self.legs.get(symbol, [])
        total_pnl = self.realized.get(symbol, 0.0)
        if legs:
            tot = sum(f for f, _, _ in legs)
            wavg = sum(f * px for f, px, _ in legs) / tot if tot > 0 else pos.entry_price
            last_reason = legs[-1][2]
        else:
            wavg, last_reason = pos.entry_price, "unknown"
        max_tp = (self.scale[symbol].max_tp_reached if symbol in self.scale
                  else sum(1 for _, _, r in legs if r.startswith("tp")))
        reason = f"{self.cfg.exit_model}:{last_reason}"
        tid = self.trade_ids.get(symbol)
        if tid is not None:
            self.db.update_trade_close(
                tid, exit_price=wavg, initial_sl=None,
                exit_reason=reason, pnl_usdt=total_pnl,
                pnl_pct_account=total_pnl / self.cfg.initial_collateral_usdc * 100,
                duration_secs=int(time.time() - pos.opened_at), max_state=max_tp,
                closed_at=datetime.now(timezone.utc).isoformat())
            del self.trade_ids[symbol]
        log.info("%s: CLOSED (%s) total_pnl=$%+.2f legs=%d avg_exit=$%.4f max_tp=%d",
                 symbol, reason, total_pnl, len(legs), wavg, max_tp)
        if self.cfg.notify.close:
            asyncio.create_task(notify.notify_close(
                symbol, pos.side, pos.entry_price, wavg, total_pnl,
                reason, int(time.time() - pos.opened_at), max_tp,
                starting_collateral=self.cfg.initial_collateral_usdc))
        for d in (self.scale, self.orig_base, self.realized, self.legs, self.tp_seen):
            d.pop(symbol, None)

    # ------- Squeeze (compression->expansion) strategy -------

    async def _on_new_bar_squeeze(self, symbol: str, df: pd.DataFrame):
        """Native squeeze handler. On each new closed 1h bar: ratchet the trail of
        any open position (and time-stop it), else fire an entry on a release."""
        sq = self.cfg.squeeze
        enriched = prepare_squeeze(df, bb_len=sq.bb_len, bb_mult=sq.bb_mult,
                                   kc_mult=sq.kc_mult, min_squeeze=sq.min_squeeze,
                                   atr_period=sq.atr_period)
        is_bootstrap = symbol not in self.bars
        self.bars[symbol] = enriched
        last = enriched.iloc[-1]
        last_ts = enriched.index[-1]

        if is_bootstrap:
            nl = int(enriched["sq_long"].sum()); ns = int(enriched["sq_short"].sum())
            log.info("%s: squeeze bootstrap scan — %d long + %d short releases over %d bars "
                     "(most recent %s)", symbol, nl, ns, len(enriched), last_ts)
            return

        # 1) manage an open position on this freshly-closed bar
        if self.executor.is_open(symbol):
            await self._squeeze_trail_update(symbol, enriched)
            return  # one position at a time — no new entry while in a trade

        # 2) entry on a release bar (the just-closed bar)
        side = "long" if bool(last["sq_long"]) else ("short" if bool(last["sq_short"]) else None)
        if side is None:
            return
        if not self._entries_allowed(symbol):
            return  # entries OFF for this ticker
        await self._open_squeeze(symbol, side, enriched)

    async def _open_squeeze(self, symbol: str, side: str, enriched: pd.DataFrame):
        last = enriched.iloc[-1]
        last_ts = enriched.index[-1]
        now = datetime.now(timezone.utc).isoformat()
        sq = self.cfg.squeeze
        atr_v = float(last.get("atr14", 0.0) or 0.0)
        ref = self.executor.get_mark_price(symbol)
        if atr_v <= 0 or ref is None or ref <= 0:
            log.warning("%s: squeeze entry skipped (atr=%.4f mark=%s)", symbol, atr_v, ref)
            return
        # risk-based sizing: notional so the sl_atr*ATR stop risks risk_frac of equity
        _, _, equity = self._equity_breakdown()
        if equity <= 0:
            equity = self.cfg.initial_collateral_usdc
        stop_dist = sq.sl_atr * atr_v
        stop_frac = stop_dist / ref
        if stop_frac <= 0:
            return
        notional = min((sq.risk_frac * equity) / stop_frac, equity * sq.max_leverage)
        base = notional / ref
        pos = await self.executor.open_position(symbol, side, base_amount=base)
        if pos is None:
            return
        # arm the ATR trailing exit state
        pos.atr_entry = atr_v
        pos.best_close = pos.entry_price
        pos.bars_held = 0
        pos.sl_price = (pos.entry_price - stop_dist) if side == "long" else (pos.entry_price + stop_dist)
        eff_lev = pos.notional / equity
        pos.leverage = round(eff_lev, 2)
        pos.margin_usdt = round(pos.notional / sq.max_leverage, 2)
        trade_id = self.db.log_trade(
            symbol=symbol, side=side, entry_price=pos.entry_price,
            margin_usdt=pos.margin_usdt, leverage=pos.leverage,
            base_amount=pos.base_amount, notional=pos.notional,
            initial_sl=pos.sl_price, opened_at=now, bar_time_open=str(last_ts),
            adx_at_entry=0.0)
        self.trade_ids[symbol] = trade_id
        log.info("%s: SQUEEZE %s entry=%.4f ATR=%.4f SL=%.4f notional=$%.0f (eff_lev=%.1f×) risk=$%.2f",
                 symbol, side.upper(), pos.entry_price, atr_v, pos.sl_price, pos.notional,
                 eff_lev, sq.risk_frac * equity)
        self.db.log_signal(symbol=symbol, side=side, bar_time=str(last_ts), outcome="fired",
                           detected_at=now)
        if self.cfg.notify.open:
            asyncio.create_task(notify.notify_open(pos))

    async def _squeeze_trail_update(self, symbol: str, enriched: pd.DataFrame):
        """On each new closed bar, ratchet the trailing stop off the bar CLOSE
        (matches the backtest: trail armed on close, stop checked intrabar on the
        5s mark). Time-stop the position at max_bars."""
        pos = self.executor.positions.get(symbol)
        if pos is None:
            return
        sq = self.cfg.squeeze
        close = float(enriched.iloc[-1]["Close"])
        trail_dist = sq.trail_atr * pos.atr_entry
        pos.bars_held += 1
        if pos.side == "long":
            pos.best_close = max(pos.best_close, close)
            pos.sl_price = max(pos.sl_price, pos.best_close - trail_dist)
        else:
            pos.best_close = min(pos.best_close, close)
            pos.sl_price = min(pos.sl_price, pos.best_close + trail_dist)
        if pos.bars_held >= sq.max_bars:
            log.info("%s: squeeze time stop (%d bars) — closing", symbol, pos.bars_held)
            await self._close_squeeze(symbol, pos, forced_reason="time")

    async def _close_squeeze(self, symbol: str, pos, forced_reason: Optional[str] = None):
        """Close a squeeze position at the live mark and book the trade."""
        result = await self.executor.close_position(symbol, "atr_trail")
        if result is None:
            return
        exit_p = result.avg_price
        pnl = ((exit_p - pos.entry_price) if pos.side == "long"
               else (pos.entry_price - exit_p)) * pos.base_amount
        if forced_reason:
            reason = forced_reason
        else:
            moved = (pos.sl_price > pos.entry_price) if pos.side == "long" else (pos.sl_price < pos.entry_price)
            reason = "trail_sl" if moved else "sl"
        duration = int(time.time() - pos.opened_at)
        tid = self.trade_ids.get(symbol)
        if tid is not None:
            self.db.update_trade_close(
                tid, exit_price=exit_p, exit_reason=reason, pnl_usdt=pnl,
                pnl_pct_account=pnl / self.cfg.initial_collateral_usdc * 100,
                duration_secs=duration, max_state=(1 if reason == "trail_sl" else 0),
                closed_at=datetime.now(timezone.utc).isoformat())
            del self.trade_ids[symbol]
        log.info("%s: SQUEEZE CLOSED %s @ %.4f pnl=$%+.2f (%s, %d bars)",
                 symbol, pos.side.upper(), exit_p, pnl, reason, pos.bars_held)
        if self.cfg.notify.close:
            asyncio.create_task(notify.notify_close(
                symbol, pos.side, pos.entry_price, exit_p, pnl, reason, duration,
                pos.max_state, starting_collateral=self.cfg.initial_collateral_usdc))

    # ------- Regime-gated VWAP mean-reversion (regime_mr) strategy — Scalper -------

    async def _on_new_bar_regime(self, symbol: str, df: pd.DataFrame):
        """On each new closed 15m bar:
          1) if a position is open: tick the time stop (SL/TP run on the 5s mark);
          2) elif a resting maker-limit entry exists: try to fill it on THIS bar's
             low/high (exactly the backtest rule), or expire it after entry_valid_bars;
          3) else: evaluate a fresh regime_mr signal and arm a resting maker limit.
        One position OR one pending entry per symbol at a time (matches the backtest)."""
        rg = self.cfg.regime
        enriched = prepare_regime(df, trend_len=rg.trend_len, slope_lb=rg.slope_lb,
                                  z_period=rg.z_period, z_entry=rg.z_entry,
                                  atr_period=rg.atr_period)
        is_bootstrap = symbol not in self.bars
        self.bars[symbol] = enriched
        last = enriched.iloc[-1]
        last_ts = enriched.index[-1]

        if is_bootstrap:
            nl = int(enriched["reg_long"].sum()); ns = int(enriched["reg_short"].sum())
            log.info("%s: regime bootstrap scan — %d long + %d short signals over %d bars "
                     "(most recent %s)", symbol, nl, ns, len(enriched), last_ts)
            return

        # 1) manage an open position (time stop on bar close; SL/TP on the mark)
        if self.executor.is_open(symbol):
            pos = self.executor.positions[symbol]
            pos.bars_held += 1
            if pos.bars_held >= rg.max_bars:
                log.info("%s: regime time stop (%d bars) — closing", symbol, pos.bars_held)
                await self._close_regime(symbol, pos, "time")
            return

        # 2) try to fill a resting maker-limit entry against this just-closed bar
        pe = self.pending_entry.get(symbol)
        if pe is not None:
            # entries switched OFF since this limit was armed -> cancel it (an
            # unfilled limit is a not-yet-open NEW entry).
            if not self._entries_allowed(symbol):
                log.info("%s: entries OFF — cancelling resting maker-limit @ %.4f",
                         symbol, pe["limit_px"])
                self.pending_entry.pop(symbol, None)
                return
            pe["bars"] += 1
            lo, hi = float(last["Low"]), float(last["High"])
            filled = (lo <= pe["limit_px"]) if pe["side"] == "long" else (hi >= pe["limit_px"])
            if filled:
                await self._open_regime(symbol, pe, last_ts)
                self.pending_entry.pop(symbol, None)
            elif pe["bars"] >= rg.entry_valid_bars:
                log.info("%s: regime %s limit @ %.4f unfilled after %d bars — cancel",
                         symbol, pe["side"], pe["limit_px"], pe["bars"])
                self.db.log_signal(symbol=symbol, side=pe["side"], bar_time=str(pe["signal_ts"]),
                                   outcome="entry_unfilled",
                                   detected_at=datetime.now(timezone.utc).isoformat())
                self.pending_entry.pop(symbol, None)
            return

        # 3) flat & no pending: evaluate a fresh signal on the just-closed bar
        if not self._entries_allowed(symbol):
            return  # entries OFF for this ticker — don't arm a new maker limit
        side = "long" if bool(last["reg_long"]) else ("short" if bool(last["reg_short"]) else None)
        if side is None:
            return
        atr_v = float(last.get("atr14", 0.0) or 0.0)
        vwap_v = float(last.get("vwap", 0.0) or 0.0)
        close_v = float(last["Close"])
        if atr_v <= 0 or vwap_v <= 0:
            return
        lv = entry_levels(side, close_v, atr_v, vwap_v, rg.limit_atr, rg.sl_atr, rg.tp_frac)
        if lv["tp_dist"] <= 0:
            return
        self.pending_entry[symbol] = {
            "side": side, "limit_px": lv["limit_px"], "sl_dist": lv["sl_dist"],
            "tp_dist": lv["tp_dist"], "bars": 0, "signal_ts": last_ts,
        }
        log.info("%s: REGIME %s signal @ %s (close=%.4f z=%.2f slope=%.4f) -> maker limit %.4f "
                 "SL_dist=%.4f TP_dist=%.4f", symbol, side.upper(), last_ts, close_v,
                 float(last["zscore"]), float(last["slope"]), lv["limit_px"],
                 lv["sl_dist"], lv["tp_dist"])
        self.db.log_signal(symbol=symbol, side=side, bar_time=str(last_ts), outcome="detected",
                           slope_pct=float(last["slope"]),
                           detected_at=datetime.now(timezone.utc).isoformat())

    async def _open_regime(self, symbol: str, pe: dict, bar_ts):
        """Fill the resting maker limit: open the paper position, then book the
        entry at the LIMIT price (maker fill, no slippage — matches the backtest)."""
        side = pe["side"]
        now = datetime.now(timezone.utc).isoformat()
        pos = await self.executor.open_position(symbol, side)
        if pos is None:
            return
        # Override to the maker limit price (executor filled at the live mark).
        pos.entry_price = pe["limit_px"]
        pos.notional = pos.entry_price * pos.base_amount
        pos.bars_held = 0
        if side == "long":
            pos.sl_price = pos.entry_price - pe["sl_dist"]
            pos.tp_price = pos.entry_price + pe["tp_dist"]
        else:
            pos.sl_price = pos.entry_price + pe["sl_dist"]
            pos.tp_price = pos.entry_price - pe["tp_dist"]
        trade_id = self.db.log_trade(
            symbol=symbol, side=side, entry_price=pos.entry_price,
            margin_usdt=pos.margin_usdt, leverage=pos.leverage,
            base_amount=pos.base_amount, notional=pos.notional,
            initial_sl=pos.sl_price, initial_tp=pos.tp_price,
            opened_at=now, bar_time_open=str(bar_ts), adx_at_entry=0.0)
        self.trade_ids[symbol] = trade_id
        log.info("%s: REGIME %s FILLED entry=%.4f SL=%.4f TP=%.4f notional=$%.0f",
                 symbol, side.upper(), pos.entry_price, pos.sl_price, pos.tp_price, pos.notional)
        self.db.log_signal(symbol=symbol, side=side, bar_time=str(bar_ts), outcome="fired",
                           detected_at=now)
        if self.cfg.notify.open:
            asyncio.create_task(notify.notify_open(pos))

    async def _close_regime(self, symbol: str, pos, reason: str):
        """Close a regime position at the live mark and book the trade."""
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
                duration_secs=duration, max_state=(1 if reason == "tp" else 0),
                closed_at=datetime.now(timezone.utc).isoformat())
            del self.trade_ids[symbol]
        log.info("%s: REGIME CLOSED %s @ %.4f pnl=$%+.2f (%s, %d bars)",
                 symbol, pos.side.upper(), exit_p, pnl, reason, pos.bars_held)
        self._register_close(reason, pnl)
        if self.cfg.notify.close:
            asyncio.create_task(notify.notify_close(
                symbol, pos.side, pos.entry_price, exit_p, pnl, reason, duration,
                pos.max_state, starting_collateral=self.cfg.initial_collateral_usdc))

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
        """Feed each booked regime close to the basket-wide cooldown breaker.
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
        """Telegram /on|/off callback. Persists, then (on OFF) cancels any
        not-yet-filled resting entry so no NEW position opens. Open positions
        are deliberately left running so the bridge babysits them to exit."""
        self.entries_enabled[symbol] = enabled
        self.db.set_switch(symbol, enabled)
        note = ""
        if not enabled:
            if self.pending_entry.pop(symbol, None) is not None:
                note = " · cancelled resting limit"
            if self.pending.get(symbol):
                self.pending[symbol] = []
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
        em = self.cfg.exit_model
        if em == "regime":
            await self._close_regime(symbol, pos, "manual")
        elif em == "atr_trail":
            await self._close_squeeze(symbol, pos, forced_reason="manual")
        elif em in ("scaleout", "pro_v3"):
            res = await self.executor.reduce_position(symbol, pos.base_amount, "manual")
            if res is not None:
                self._book_leg(symbol, pos, res, "manual")
            self._finalize_scaleout(symbol, pos)
        else:
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
            return "Scalper starting up…"
        lines = ["📋 <b>Scalper status</b>"]
        if self._cooldown_active():
            mins = int((self._cd_until - time.time()) / 60) + 1
            lines.append(f"🧊 COOLDOWN active \u2014 entries blocked ~{mins}m more")
        for sym in sorted(self.executor.symbols):
            on = self._entries_allowed(sym)
            pos = self.executor.positions.get(sym)
            if pos is not None:
                where = f"{pos.side} open @ ${pos.entry_price:.4f}"
            elif self.pending_entry.get(sym) is not None:
                where = "pending entry"
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
                if self.cfg.exit_model == "scaleout":
                    await self._scaleout_tick(symbol, pos, mark)
                    continue
                if self.cfg.exit_model == "atr_trail":
                    # trail level is ratcheted on bar close in _squeeze_trail_update;
                    # here we only check whether the live mark has hit the stop.
                    hit = (mark <= pos.sl_price) if pos.side == "long" else (mark >= pos.sl_price)
                    if hit:
                        await self._close_squeeze(symbol, pos, forced_reason=None)
                    continue
                if self.cfg.exit_model == "regime":
                    # fixed SL + TP checked on the live mark; STOP WINS on a tie
                    # (conservative, matches the backtest's both-hit rule).
                    if pos.side == "long":
                        hit_sl = mark <= pos.sl_price
                        hit_tp = mark >= pos.tp_price
                    else:
                        hit_sl = mark >= pos.sl_price
                        hit_tp = mark <= pos.tp_price
                    if hit_sl:
                        await self._close_regime(symbol, pos, "sl")
                    elif hit_tp:
                        await self._close_regime(symbol, pos, "tp")
                    continue
                if self.cfg.exit_model == "pro_v3":
                    continue  # exits are driven by Pro V3 TP/SL webhooks, not ticked
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
                        if self.cfg.notify.close:
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
            self._maybe_notify_cooldown_resume()
            if self.executor is None:
                await asyncio.sleep(5)
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
            # Squeeze (atr_trail) re-arm: restore a valid protective stop. We
            # re-derive ATR(entry) from the stored initial_sl and re-arm the
            # INITIAL stop, letting the trail re-ratchet from entry on the next
            # bars (gives back any locked profit since entry, but is always
            # protective — never leaves a position without a valid stop).
            if self.cfg.exit_model == "atr_trail":
                sq = self.cfg.squeeze
                init_sl = row.get("initial_sl")
                if init_sl:
                    pos.atr_entry = abs(entry_price - float(init_sl)) / sq.sl_atr if sq.sl_atr else 0.0
                    pos.sl_price = float(init_sl)
                else:
                    pos.sl_price = entry_price - 0.0  # no stored stop -> next bar re-arms
                pos.best_close = entry_price
                pos.bars_held = int(max(0, (time.time() - opened_at_unix) // 3600))
                log.info("  %s: squeeze trail re-armed sl=%.4f atr_entry=%.4f bars_held=%d",
                         symbol, pos.sl_price, pos.atr_entry, pos.bars_held)

            # Regime-MR re-arm: restore the fixed SL + TP and the bar count for the
            # time stop. SL/TP are stored at entry (initial_sl/initial_tp) so no
            # replay is needed — the mark loop resumes guarding the position.
            if self.cfg.exit_model == "regime":
                init_sl = row.get("initial_sl")
                init_tp = row.get("initial_tp")
                if init_sl:
                    pos.sl_price = float(init_sl)
                if init_tp:
                    pos.tp_price = float(init_tp)
                pos.bars_held = int(max(0, (time.time() - opened_at_unix) // 900))  # 15m bars
                log.info("  %s: regime re-armed sl=%.4f tp=%.4f bars_held=%d",
                         symbol, pos.sl_price, pos.tp_price, pos.bars_held)

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
