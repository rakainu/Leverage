"""hlsm-bridge entrypoint. Boots dashboard + telegram bot + ingest + executor + exit loop.

Single asyncio process. FastAPI runs in-process via uvicorn. The Hyperliquid WS, the
exit-policy sweeper, the circuit-breaker watcher, and the heartbeat are async tasks.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import uvicorn
from sqlalchemy import select

from hlsm.config import get_settings, load_weights
from hlsm.convergence import ConvergenceDetector, ConvergenceEvent
from hlsm.convergence.detector import DetectorConfig
from hlsm.convergence.events import WalletCloseEvent, WalletOpenEvent
from hlsm.dashboard import create_app
from hlsm.db import PaperPosition, Signal, Wallet
from hlsm.db.session import get_engine, get_session
from hlsm.exchange import BloFinExchange, Exchange, LighterStub
from hlsm.executor import ExecutorConfig, ExitPolicy, ExitPolicyConfig, PaperExecutor
from hlsm.ingest import HistoricalIngestor, HyperliquidREST, HyperliquidWebSocket, LeaderboardCrawler
from hlsm.safety import WeightsWatcher
from hlsm.safety.circuit_breaker import CircuitBreaker
from hlsm.scoring import ScoringConfig, score_all
from hlsm.telegram import (
    AlertSender,
    TelegramBot,
    format_breaker_trip,
    format_convergence,
    format_heartbeat,
    format_position_close,
    format_position_open,
)
from hlsm.reconstruct import reconstruct_positions

log = logging.getLogger(__name__)


def configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )


def build_exchange(venue: str = "blofin") -> Exchange:
    if venue == "blofin":
        return BloFinExchange()
    if venue == "lighter":
        return LighterStub()
    raise ValueError(f"unknown venue: {venue}")


class Runtime:
    """Holds wired-up components + handles hot-reload of tunables."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.weights = load_weights()
        self.exchange = build_exchange("blofin")
        self.universe = frozenset(self.weights.get("universe", {}).get("memecoin_perps", []))
        self.detector = self._build_detector()
        self.executor = self._build_executor()
        self.exit_policy = self._build_exit_policy()
        self.alerts = AlertSender.from_settings()
        self.breaker = CircuitBreaker(
            threshold_usdt=Decimal(str(self.weights.get("safety", {}).get("daily_loss_pause_usdt", 100))),
            on_trip=self._on_breaker_trip,
        )

    def _build_detector(self) -> ConvergenceDetector:
        c = self.weights.get("convergence", {})
        return ConvergenceDetector(DetectorConfig(
            cluster_n=int(c.get("cluster_n", 3)),
            window_minutes=int(c.get("window_minutes", 45)),
            score_floor=float(c.get("score_floor", 75)),
            universe=self.universe or None,
        ))

    def _build_executor(self) -> PaperExecutor:
        e = self.weights.get("executor", {})
        return PaperExecutor(
            exchange=self.exchange,
            config=ExecutorConfig(
                per_trade_margin_usdt=Decimal(str(e.get("per_trade_margin_usdt", 50))),
                leverage=int(e.get("leverage", 10)),
                hard_sl_pct=Decimal(str(e.get("hard_sl_pct", 25))),
                tp_default_pct=Decimal(str(e.get("tp_default_pct", 30))),
                max_concurrent_positions=int(e.get("max_concurrent_positions", 5)),
                universe=self.universe,
                signal_cooldown_minutes=int(e.get("signal_cooldown_minutes", 60)),
            ),
            on_signal=self._on_signal,
        )

    def _build_exit_policy(self) -> ExitPolicy:
        e = self.weights.get("executor", {})
        return ExitPolicy(
            exchange=self.exchange,
            config=ExitPolicyConfig(
                exit_rule=str(e.get("exit_rule", "median")),
                hard_sl_pct=Decimal(str(e.get("hard_sl_pct", 25))),
                tp_default_pct=Decimal(str(e.get("tp_default_pct", 30))),
            ),
            on_close=self._on_close,
        )

    def reload_weights(self, new: dict) -> None:
        log.info("weights reloaded: convergence=%s executor=%s",
                 new.get("convergence"), new.get("executor"))
        self.weights = new
        self.universe = frozenset(new.get("universe", {}).get("memecoin_perps", []))
        self.detector = self._build_detector()
        self.executor = self._build_executor()
        self.exit_policy = self._build_exit_policy()
        self.breaker = CircuitBreaker(
            threshold_usdt=Decimal(str(new.get("safety", {}).get("daily_loss_pause_usdt", 100))),
            on_trip=self._on_breaker_trip,
        )

    # ---- callbacks wired into executor / exit_policy ----

    def _on_signal(self, signal: Signal, pp: PaperPosition | None, outcome) -> None:
        if signal.status == "filled" and pp is not None:
            self.alerts.send(format_position_open(signal, pp))
        elif signal.status == "error":
            self.alerts.send(
                f"<b>EXECUTION ERROR</b>  {signal.coin} {signal.side.upper()} signal #{signal.id}\n"
                f"<i>{(signal.reason or '')[:200]}</i>"
            )

    def _on_close(self, pp: PaperPosition, decision, pnl_usdt: Decimal) -> None:
        self.alerts.send(format_position_close(pp, decision, pnl_usdt))

    def _on_breaker_trip(self, day_pnl: Decimal) -> None:
        self.alerts.send(format_breaker_trip(day_pnl))

    # ---- WS handlers ----

    async def on_open(self, ev: WalletOpenEvent) -> None:
        # Record event row
        from hlsm.db import Event
        with get_session() as sess:
            sess.add(Event(
                wallet_address=ev.wallet_address,
                ts=ev.ts,
                coin=ev.coin,
                side=ev.side.value,
                kind="open",
                sz_after=Decimal("0"),
                px=ev.px if ev.px is not None else None,
            ))
        # Convergence check (in-memory)
        fired = self.detector.on_open(ev)
        if fired is None:
            return
        await self._handle_convergence(fired)

    async def on_close_event(self, ev: WalletCloseEvent) -> None:
        from hlsm.db import Event
        with get_session() as sess:
            sess.add(Event(
                wallet_address=ev.wallet_address,
                ts=ev.ts,
                coin=ev.coin,
                side=ev.side.value,
                kind="close",
                sz_after=Decimal("0"),
                px=ev.px if ev.px is not None else None,
            ))

    async def _handle_convergence(self, ev: ConvergenceEvent) -> None:
        self.alerts.send(format_convergence(ev))
        with get_session() as sess:
            self.executor.execute(sess, ev)


def _run_exit_sweep(runtime: Runtime) -> None:
    with get_session() as sess:
        runtime.exit_policy.sweep_open(sess)
        runtime.breaker.check(sess)


async def exit_sweep_loop(runtime: Runtime, *, interval_seconds: int = 30) -> None:
    """Exit sweep talks to the venue (synchronous ccxt) — runs in a thread."""
    while True:
        try:
            await asyncio.to_thread(_run_exit_sweep, runtime)
        except Exception:  # noqa: BLE001
            log.exception("exit sweep failed")
        await asyncio.sleep(interval_seconds)


async def heartbeat_loop(runtime: Runtime, *, interval_seconds: int = 3600) -> None:
    while True:
        try:
            with get_session() as sess:
                tracked = sess.execute(select(Wallet).where(Wallet.active.is_(True))).scalars().all()
                scored = [w for w in tracked if w.current_score is not None]
                opens = sess.execute(
                    select(PaperPosition).where(PaperPosition.status == "open")
                ).scalars().all()
                day_pnl = runtime.breaker.day_pnl_usdt(sess)
            runtime.alerts.send(format_heartbeat(
                tracked_wallets=len(tracked),
                scored_wallets=len(scored),
                open_positions=len(opens),
                day_pnl=day_pnl,
            ))
        except Exception:  # noqa: BLE001
            log.exception("heartbeat failed")
        await asyncio.sleep(interval_seconds)


def _run_refresh_cycle(runtime: Runtime, cfg: ScoringConfig) -> None:
    """One full refresh cycle. Synchronous; meant to be called inside asyncio.to_thread
    so the asyncio event loop (dashboard, telegram, WS, exit-sweep) stays responsive
    while the slow HL ingest runs."""
    rest = HyperliquidREST()
    crawler = LeaderboardCrawler(
        rest,
        top_n=int(runtime.weights.get("ingest", {}).get("top_n_wallets", 100)),
        seed_wallets=runtime.weights.get("ingest", {}).get("seed_wallets") or [],
    )
    ingestor = HistoricalIngestor(rest, days=int(runtime.weights.get("ingest", {}).get("historical_days", 90)))

    with get_session() as sess:
        added = crawler.refresh(sess)
        addresses = [w.address for w in sess.execute(select(Wallet).where(Wallet.active.is_(True))).scalars().all()]
    log.info("leaderboard refreshed: %d new wallets, %d total active", added, len(addresses))

    successes = 0
    for addr in addresses:
        try:
            with get_session() as sess:
                ingestor.ingest_wallet(sess, addr)
                reconstruct_positions(sess, addr)
            successes += 1
        except Exception:  # noqa: BLE001
            log.exception("ingest+reconstruct failed for %s", addr)
    log.info("ingest+reconstruct complete: %d/%d wallets succeeded", successes, len(addresses))

    with get_session() as sess:
        score_all(sess, config=cfg, addresses=addresses)
    log.info("scoring complete")


async def daily_refresh_loop(runtime: Runtime, *, interval_seconds: int = 21600) -> None:
    """Every 6h: refresh leaderboard, rebuild positions, recompute scores.
    The blocking work runs in a thread to keep the event loop responsive."""
    cfg = ScoringConfig(
        min_trades=int(runtime.weights.get("scoring", {}).get("min_trades", 50)),
        min_days_active=int(runtime.weights.get("scoring", {}).get("min_days_active", 30)),
        max_single_trade_pnl_pct=float(runtime.weights.get("scoring", {}).get("max_single_trade_pnl_pct", 50)),
        recency_half_life_days=int(runtime.weights.get("scoring", {}).get("recency_half_life_days", 30)),
        weights=runtime.weights.get("scoring", {}).get("weights"),
    )
    while True:
        try:
            await asyncio.to_thread(_run_refresh_cycle, runtime, cfg)
        except Exception:  # noqa: BLE001
            log.exception("daily_refresh_loop failed")
        await asyncio.sleep(interval_seconds)


async def ws_loop(runtime: Runtime) -> None:
    while True:
        try:
            with get_session() as sess:
                addresses = [
                    w.address for w in sess.execute(
                        select(Wallet).where(Wallet.active.is_(True), Wallet.current_score.is_not(None))
                    ).scalars().all()
                ]
            if not addresses:
                log.info("no scored wallets yet; sleeping 60s before retrying WS subscription")
                await asyncio.sleep(60)
                continue

            score_map: dict[str, float] = {}
            with get_session() as sess:
                for w in sess.execute(select(Wallet).where(Wallet.address.in_(addresses))).scalars().all():
                    if w.current_score is not None:
                        score_map[w.address.lower()] = float(w.current_score)

            ws = HyperliquidWebSocket(
                on_open=runtime.on_open,
                on_close=runtime.on_close_event,
                score_provider=lambda a, sm=score_map: sm.get(a.lower()),
            )
            await ws.run(addresses)
        except Exception:  # noqa: BLE001
            log.exception("ws_loop failed; restarting in 10s")
            await asyncio.sleep(10)


async def dashboard_server(host: str, port: int) -> None:
    app = create_app()
    config = uvicorn.Config(app, host=host, port=port, log_level="info", access_log=False)
    server = uvicorn.Server(config)
    await server.serve()


async def telegram_loop(runtime: Runtime) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        log.warning("telegram bot disabled (no TELEGRAM_BOT_TOKEN)")
        return

    def status_provider() -> dict:
        with get_session() as sess:
            opens = sess.execute(select(PaperPosition).where(PaperPosition.status == "open")).scalars().all()
            tracked = sess.execute(select(Wallet).where(Wallet.active.is_(True))).scalars().all()
            scored = [w for w in tracked if w.current_score is not None]
            day_pnl = runtime.breaker.day_pnl_usdt(sess)
        return {
            "tracked_wallets": len(tracked),
            "scored_wallets": len(scored),
            "open_positions": len(opens),
            "day_pnl_usdt": f"{day_pnl:.2f}",
        }

    bot = TelegramBot(status_provider=status_provider)
    try:
        await bot.run()
    except Exception:  # noqa: BLE001
        log.exception("telegram bot failed")


async def amain() -> None:
    configure_logging()
    settings = get_settings()
    # Ensure engine is bound
    _ = get_engine()

    runtime = Runtime()

    watcher = WeightsWatcher(Path(settings.hlsm_config), on_change=runtime.reload_weights)
    watcher.start()

    tasks = [
        asyncio.create_task(dashboard_server("0.0.0.0", settings.hlsm_api_port), name="dashboard"),
        asyncio.create_task(exit_sweep_loop(runtime), name="exit_sweep"),
        asyncio.create_task(heartbeat_loop(runtime), name="heartbeat"),
        asyncio.create_task(daily_refresh_loop(runtime), name="daily_refresh"),
        asyncio.create_task(ws_loop(runtime), name="ws"),
        asyncio.create_task(telegram_loop(runtime), name="telegram"),
    ]

    stop_event = asyncio.Event()

    def _handle_stop(*_args) -> None:
        log.info("shutdown requested")
        stop_event.set()

    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, _handle_stop)
        loop.add_signal_handler(signal.SIGINT, _handle_stop)
    except (NotImplementedError, AttributeError):
        pass  # Windows / non-asyncio host

    await stop_event.wait()
    log.info("cancelling background tasks")
    watcher.stop()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
