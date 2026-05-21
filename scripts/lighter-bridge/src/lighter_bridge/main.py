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
from .signals import (
    check_retest, compute_ema_and_slope, generate_v3_signals,
    passes_entry_filters, prepare,
)
from .state_machine import step as state_step


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
        log.info("Exits: SL=$%.0f BE=$%.0f lock_act=$%.0f trail_act=$%.0f trail_dist=$%.0f",
                 self.cfg.exits.sl_loss_usdt, self.cfg.exits.breakeven_usdt,
                 self.cfg.exits.lock_profit_activate_usdt,
                 self.cfg.exits.trail_activate_usdt, self.cfg.exits.trail_distance_usdt)

        # Lighter client + paper client
        self.api = lighter.ApiClient(configuration=lighter.Configuration(host=self.cfg.host))
        self.paper = lighter.PaperClient(self.api,
                                         initial_collateral_usdc=self.cfg.initial_collateral_usdc)

        # Subscribe to live order book for each enabled symbol
        enabled = {n: s for n, s in self.cfg.symbols.items() if s.enabled}
        for name, s in enabled.items():
            log.info("%s: subscribing to live order book (market_id=%d)", name, s.market_id)
            await self.paper.track_market(market_id=s.market_id)
            self.pending[name] = []

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
                resolution=self.cfg.entry.timeframe,
                poll_interval_s=self.cfg.loop.bar_poll_interval_s,
            )
            self.feeds[name] = BarFeed(self.api, feed_cfg)

        # Wait briefly so the order book has data before signaling
        await asyncio.sleep(2)

        # Telegram startup ping
        await notify.notify_startup(self.cfg)

        # Kick off async tasks
        tasks = []
        for name, feed in self.feeds.items():
            tasks.append(asyncio.create_task(feed.run_loop(self.on_new_bar)))
        tasks.append(asyncio.create_task(self.position_check_loop()))
        tasks.append(asyncio.create_task(self.heartbeat_loop()))
        tasks.append(asyncio.create_task(self.daily_summary_loop()))
        log.info("Bridge running. %d bar-feed tasks + position checker + heartbeat + daily summary.",
                 len(self.feeds))

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

        # NEW signal on the just-closed bar?
        if bool(last["buy_sig"]):
            self.pending[symbol].append(PendingSignal(symbol, "long", last_ts, len(enriched) - 1))
            log.info("%s: NEW BUY signal @ %s  (close=%.4f)", symbol, last_ts, last["Close"])
            self.db.log_signal(symbol=symbol, side="long",
                               bar_time=str(last_ts), outcome="detected",
                               ema9=float(last["ema9"]), slope_pct=float(last["slope_pct"]),
                               body_atr_ratio=float(last["body_atr_ratio"]),
                               detected_at=datetime.now(timezone.utc).isoformat())
        if bool(last["sell_sig"]):
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
            asyncio.create_task(notify.notify_open(pos))
            self.db.log_signal(symbol=symbol, side=sig.side, bar_time=str(sig.detected_at_bar_ts),
                               outcome="fired", ema9=ema_v, slope_pct=slope_v,
                               body_atr_ratio=body_v,
                               detected_at=datetime.now(timezone.utc).isoformat())
            # Drop other same-side pendings; opposite-side stays (matches live)
            new_pending = [p for p in new_pending if p.side != sig.side]
        self.pending[symbol] = new_pending

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
                        ))
            await asyncio.sleep(self.cfg.loop.position_check_interval_s)

    async def heartbeat_loop(self):
        """Every 5 minutes, snapshot account state to DB and log a summary line."""
        last_seen_at = time.time()
        while not self._stopped:
            await asyncio.sleep(300)
            if self.executor is None:
                continue
            try:
                s = self.executor.account_summary()
                cum_pnl = s["portfolio_value"] - self.cfg.initial_collateral_usdc
                self.db.snapshot_account(s["collateral"], s["portfolio_value"],
                                         s["open_positions"], cum_pnl)
                log.info("HEARTBEAT  portfolio=$%.2f  cum_pnl=$%+.2f  open=%d",
                         s["portfolio_value"], cum_pnl, s["open_positions"])
                last_seen_at = time.time()
            except Exception as exc:
                # Don't crash the heartbeat — alert if we go silent for > 30 min
                log.error("Heartbeat error: %s", exc, exc_info=True)
                if time.time() - last_seen_at > 1800:
                    asyncio.create_task(notify.notify_error(
                        f"Heartbeat silent for >30m: {exc}"
                    ))

    async def daily_summary_loop(self):
        """Send one Telegram daily summary every 24h (anchored to first call)."""
        # Initial wait: 60 min after startup, then every 24h.
        # Keeps the timing stable across restarts.
        await asyncio.sleep(3600)
        while not self._stopped:
            try:
                stats = self.db.summary()
                if self.executor is not None:
                    s = self.executor.account_summary()
                    stats["portfolio_value"] = s["portfolio_value"]
                await notify.notify_daily(stats)
            except Exception as exc:
                log.error("Daily summary error: %s", exc, exc_info=True)
            await asyncio.sleep(86400)


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
