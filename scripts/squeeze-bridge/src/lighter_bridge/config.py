"""Config loader. Parses config.yaml into typed dataclasses."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class SymbolConfig:
    market_id: int
    enabled: bool
    margin_usdt: float
    leverage: float


@dataclass
class EntryConfig:
    timeframe: str
    min_abs_slope_pct: float
    block_body_band: Optional[tuple]
    block_weekdays: list[int]
    ema_period: int
    retest_overshoot_pct: float
    slope_lookback_bars: int
    retest_timeout_bars: int
    require_retest: bool = True   # False = take the raw Pro V3 webhook immediately (no retest/filters)


@dataclass
class ExitConfig:
    sl_loss_usdt: float
    breakeven_usdt: float
    lock_profit_activate_usdt: float
    lock_profit_usdt: float
    trail_activate_usdt: float
    trail_start_usdt: float
    trail_distance_usdt: float
    tp_ceiling_pct: float


@dataclass
class PineConfig:
    sensitivity: int = 8
    noise: float = 0.0
    fakeout: float = 0.2
    range_filter: float = 0.2


@dataclass
class ScaleOutConfig:
    """ATR scale-out exit params (validated Pro V3 SOL config, 2026-05-29)."""
    sl_atr: float = 3.5
    tp_atr: tuple = (1.0, 2.0, 3.0)
    ratios: tuple = (0.34, 0.33, 0.33)
    be_after_tp1: bool = True
    atr_period: int = 14


@dataclass
class SqueezeConfig:
    """Volatility compression->expansion (squeeze) params + risk-based sizing.
    Validated 4-coin 1h basket (scripts/scalping/analysis/lighter_strat_2026-05-30).
    Sizing is RISK-PER-TRADE (not fixed margin x leverage): each trade's notional
    is set so the initial sl_atr*ATR stop risks `risk_frac` of current equity,
    capped at `max_leverage` notional. Reproduces the backtested DD profile."""
    bb_len: int = 20
    bb_mult: float = 2.0
    kc_mult: float = 1.5
    min_squeeze: int = 10
    sl_atr: float = 1.5
    trail_atr: float = 3.0
    max_bars: int = 48
    atr_period: int = 14
    risk_frac: float = 0.0075        # 0.75% of equity to the hard stop per trade
    max_leverage: float = 20.0


@dataclass
class WebhookConfig:
    """Inbound Pro V3 webhook listener (signal_source == 'webhook')."""
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8080
    path: str = "/webhook/pro-v3"
    secret: str = ""


@dataclass
class LoopConfig:
    bar_poll_interval_s: int = 30
    position_check_interval_s: int = 5
    # WS mark-feed watchdog thresholds (see incidents 2026-05-23 / 2026-05-25).
    # The Lighter SDK's per-market order-book listener has no reconnect: when a
    # socket drops, its task dies silently and that symbol's mark freezes. The
    # watchdog heals it in-process:
    #   reconnect_s: rebuild JUST that symbol's WS (stop_tracking + track_market)
    #                — also triggered immediately if the listener task is dead.
    #   warn_s:      informational log threshold below reconnect.
    #   fatal_s:     last resort — exit so Docker restarts the whole process
    #                (only reached if in-process reconnect keeps failing).
    # ZEC/SOL trade ~14×/min, so a healthy WS ticks the mid every 5–10s.
    mark_reconnect_s: int = 60
    mark_stale_warn_s: int = 180
    mark_stale_fatal_s: int = 300
    mark_watchdog_interval_s: int = 30


@dataclass
class LogConfig:
    level: str = "INFO"
    db_path: str = "data/lighter_paper.db"


@dataclass
class NotifyConfig:
    """Per-type Telegram toggles. Default all-on = legacy behavior.
    Set a flag false to silence that message class for a given bridge.
    (Error/crash alerts are intentionally NOT toggleable — silent failures are
    worse than a ping.)"""
    startup: bool = True       # "Bridge UP" ping on every (re)start — noisy when iterating
    open: bool = True          # entry alerts
    close: bool = True         # exit alerts
    daily: bool = True         # daily KPI summary


@dataclass
class BridgeConfig:
    host: str
    initial_collateral_usdc: float
    symbols: dict[str, SymbolConfig]
    entry: EntryConfig
    exits: Optional[ExitConfig] = None         # required only for exit_model == "trail"
    pine: PineConfig = field(default_factory=PineConfig)
    loop: LoopConfig = field(default_factory=LoopConfig)
    log: LogConfig = field(default_factory=LogConfig)
    # Strategy mode switches (default = legacy replica + trail bridge behavior)
    signal_source: str = "replica"             # "replica" | "webhook"
    exit_model: str = "trail"                  # "trail" | "scaleout"
    scaleout: ScaleOutConfig = field(default_factory=ScaleOutConfig)
    squeeze: "SqueezeConfig" = field(default_factory=lambda: SqueezeConfig())
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)


def load_config(path: str | Path) -> BridgeConfig:
    raw = yaml.safe_load(Path(path).read_text())

    symbols = {}
    for name, s in raw["symbols"].items():
        symbols[name] = SymbolConfig(
            market_id=s["market_id"],
            enabled=s.get("enabled", True),
            margin_usdt=float(s["margin_usdt"]),
            leverage=float(s["leverage"]),
        )

    body_band = raw["entry"].get("block_body_band")
    if body_band is not None:
        body_band = tuple(body_band)

    entry = EntryConfig(
        timeframe=raw["entry"]["timeframe"],
        min_abs_slope_pct=float(raw["entry"]["min_abs_slope_pct"]),
        block_body_band=body_band,
        block_weekdays=list(raw["entry"].get("block_weekdays", [])),
        ema_period=int(raw["entry"].get("ema_period", 9)),
        retest_overshoot_pct=float(raw["entry"].get("retest_overshoot_pct", 0.2)),
        slope_lookback_bars=int(raw["entry"].get("slope_lookback_bars", 3)),
        retest_timeout_bars=int(raw["entry"].get("retest_timeout_bars", 6)),
        require_retest=bool(raw["entry"].get("require_retest", True)),
    )

    exits = ExitConfig(**{k: float(v) for k, v in raw["exits"].items()}) if raw.get("exits") else None
    pine = PineConfig(**raw.get("pine", {}))
    loop = LoopConfig(**raw.get("loop", {}))
    log_cfg = LogConfig(**raw.get("log", {}))

    signal_source = raw.get("signal_source", "replica")
    exit_model = raw.get("exit_model", "trail")

    so_raw = dict(raw.get("scaleout", {}))
    if "tp_atr" in so_raw:
        so_raw["tp_atr"] = tuple(so_raw["tp_atr"])
    if "ratios" in so_raw:
        so_raw["ratios"] = tuple(so_raw["ratios"])
    scaleout = ScaleOutConfig(**so_raw)
    squeeze = SqueezeConfig(**raw.get("squeeze", {}))

    webhook = WebhookConfig(**raw.get("webhook", {}))
    # BRIDGE_SECRET env overrides the yaml secret (so it's never committed).
    if os.environ.get("BRIDGE_SECRET"):
        webhook.secret = os.environ["BRIDGE_SECRET"]

    notify = NotifyConfig(**raw.get("notify", {}))

    if exit_model == "trail" and exits is None:
        raise ValueError("exit_model 'trail' requires an 'exits:' config block")
    if signal_source == "webhook" and not webhook.enabled:
        webhook.enabled = True

    return BridgeConfig(
        host=raw["connection"]["host"],
        initial_collateral_usdc=float(raw["connection"]["initial_collateral_usdc"]),
        symbols=symbols,
        entry=entry,
        exits=exits,
        pine=pine,
        loop=loop,
        log=log_cfg,
        signal_source=signal_source,
        exit_model=exit_model,
        scaleout=scaleout,
        squeeze=squeeze,
        webhook=webhook,
        notify=notify,
    )
