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
    trail_activate_usdt: float
    trail_distance_usdt: float
    tp_ceiling_pct: float


@dataclass
class PineConfig:
    sensitivity: int = 8
    noise: float = 0.0
    fakeout: float = 0.2
    range_filter: float = 0.2


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
    # Mark price feed (REST order-book snapshot polling — NOT the WS delta
    # stream). The Lighter SDK's WS order-book listener applies deltas by
    # re-sorting the whole book on every message, which pegged a CPU core and
    # starved the event loop (incident 2026-05-31). This strategy only samples
    # the mid every position_check_interval_s, so a small periodic REST snapshot
    # gives identical execution at ~zero CPU and removes the whole WS-lifecycle
    # failure class (keepalive drops, reconnect churn, subscribe flakiness).
    #   mark_poll_interval_s: how often to refresh each market's book snapshot.
    #   mark_stale_warn_s:    informational log threshold (no successful poll).
    #   mark_stale_fatal_s:   last resort — if a symbol with an OPEN position has
    #                         had no successful poll past this, exit so Docker
    #                         restarts the process with a clean client.
    mark_poll_interval_s: int = 3
    mark_stale_warn_s: int = 180
    mark_stale_fatal_s: int = 300


@dataclass
class LogConfig:
    level: str = "INFO"
    db_path: str = "data/apex.db"


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
class ControlConfig:
    """Inbound Telegram control listener (per-ticker entry switch).
    Off by default so other bridges sharing this code are unaffected; the
    Apex config opts in. Authorizes against TELEGRAM_CHAT_ID (env)."""
    telegram_enabled: bool = False


@dataclass
class CooldownConfig:
    """Basket-wide news-rip circuit breaker. After `consec_losses` losing closes
    in a row (across all coins), block ALL new entries for `minutes`, then
    auto-resume. Off by default; revert = set enabled:false."""
    enabled: bool = False
    consec_losses: int = 2
    minutes: int = 360


@dataclass
class SizingConfig:
    """Position sizing mode. `fixed` (default) posts each symbol's configured
    margin. `compound` scales margin with live account equity (base_equity ->
    base margin), capped at base * cap_mult and floored at 0 on drawdown.
    See sizing.compound_margin — identical math for paper and live."""
    mode: str = "fixed"            # "fixed" | "compound"
    base_equity: float = 0.0       # equity at which a symbol trades its base margin; 0 => initial_collateral
    cap_mult: float = 3.0          # margin never exceeds base_margin * cap_mult


@dataclass
class WithdrawalConfig:
    """Periodic profit withdrawal. Skims REALIZED equity above
    base_equity * target_mult, at most once per cadence period. Records to the
    `withdrawals` ledger and reduces the equity base used for sizing. Off by
    default; revert = set enabled:false."""
    enabled: bool = False
    cadence: str = "weekly"        # "weekly" (ISO week) | "daily"
    target_mult: float = 3.0       # skim realized equity above base_equity * target_mult


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
    # Strategy mode switches (Apex: webhook signal source + 3-stage trail exit)
    signal_source: str = "replica"             # "replica" | "webhook"
    exit_model: str = "trail"                  # "trail"
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    cooldown: CooldownConfig = field(default_factory=CooldownConfig)
    sizing: SizingConfig = field(default_factory=SizingConfig)
    withdrawal: WithdrawalConfig = field(default_factory=WithdrawalConfig)


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
    # Filter to known fields so a stale config key (e.g. a renamed/removed
    # watchdog knob left in an old yaml) never breaks startup.
    _loop_keys = set(LoopConfig.__dataclass_fields__)
    loop = LoopConfig(**{k: v for k, v in raw.get("loop", {}).items() if k in _loop_keys})
    log_cfg = LogConfig(**raw.get("log", {}))

    signal_source = raw.get("signal_source", "replica")
    exit_model = raw.get("exit_model", "trail")

    webhook = WebhookConfig(**raw.get("webhook", {}))
    # BRIDGE_SECRET env overrides the yaml secret (so it's never committed).
    if os.environ.get("BRIDGE_SECRET"):
        webhook.secret = os.environ["BRIDGE_SECRET"]

    notify = NotifyConfig(**raw.get("notify", {}))
    control = ControlConfig(**raw.get("control", {}))
    cooldown = CooldownConfig(**raw.get("cooldown", {}))
    sizing = SizingConfig(**raw.get("sizing", {}))
    withdrawal = WithdrawalConfig(**raw.get("withdrawal", {}))
    # base_equity defaults to the starting collateral if left at 0.
    if sizing.base_equity <= 0:
        sizing.base_equity = float(raw["connection"]["initial_collateral_usdc"])

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
        webhook=webhook,
        notify=notify,
        control=control,
        cooldown=cooldown,
        sizing=sizing,
        withdrawal=withdrawal,
    )
