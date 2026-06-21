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
    # Reclaim entry (M13, the validated V3.2 honest twin). When require_reclaim is
    # True the EMA9 retest must also CLOSE BACK across EMA9 on the trade's side (a
    # confirmed bounce, not a breakdown) — entry then fires at that bar's close.
    # max_gap_pct caps how far the close may sit from EMA9: skip the entry when
    # |close-ema9|/ema9*100 > max_gap_pct (0 = no cap). 0.05 is the validated knee
    # (PF 1.27, OOS-stable, 1m-magnifier confirmed). See v3.2-analysis/entry_v2_search.py.
    require_reclaim: bool = False
    max_gap_pct: float = 0.0


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
class RegimeConfig:
    """Regime-gated VWAP mean-reversion ("regime_mr") — Scalper.
    Validated 5-coin 15m basket (scripts/scalping/analysis/scalp_search_2026-05-30):
    pooled PF 1.49, 89% WR, 192 trades/wk, OOS 1.42, walk-forward 4/4, 2x-slip 1.43.
    Maker LIMIT entry; FIXED notional sizing (per-symbol margin x leverage in the
    symbols block — $250 @ 10x = margin 25, leverage 10). Exits: hard sl_atr*ATR
    stop, take-profit at tp_frac*(entry distance to VWAP), time stop max_bars."""
    trend_len: int = 200       # EMA length for the higher-tf trend gate
    slope_lb: int = 20         # slope = EMA - EMA.shift(slope_lb); sign = regime
    z_period: int = 30         # z-score window for (Close - sessionVWAP)
    z_entry: float = 1.5       # |z| threshold to fade
    sl_atr: float = 2.0        # hard stop = entry -/+ sl_atr*ATR
    tp_frac: float = 0.3       # take-profit = tp_frac * |VWAP - limit entry|
    max_bars: int = 12         # time stop in bars (12 * 15m = 3h)
    limit_atr: float = 0.25    # maker limit offset beyond close, in ATR
    atr_period: int = 14
    entry_valid_bars: int = 3  # cancel the resting limit if unfilled after N bars
    accel_mult: float = 0.0    # acceleration guard: skip fading a signal bar whose
                               # range >= accel_mult*ATR (news-rip / climax bar).
                               # 0 = off. Validated 3.0: PF 1.46->1.54, lower DD.
    min_slope_pct: float = 0.0  # trend-clarity gate: require |EMA-slope|% >= this
                                # before fading against the trend (sign-only gate
                                # shorts a flat-but-rising tape). 0 = off. Validated
                                # 0.08: worst losing window -$926->-$126, total +20%.


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
    db_path: str = "data/reclaim.db"


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
    Reclaim config opts in. Authorizes against TELEGRAM_CHAT_ID (env)."""
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
    # Strategy mode switches (default = legacy replica + trail bridge behavior)
    signal_source: str = "replica"             # "replica" | "webhook"
    exit_model: str = "trail"                  # "trail" | "scaleout"
    scaleout: ScaleOutConfig = field(default_factory=ScaleOutConfig)
    squeeze: "SqueezeConfig" = field(default_factory=lambda: SqueezeConfig())
    regime: "RegimeConfig" = field(default_factory=lambda: RegimeConfig())
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
        require_reclaim=bool(raw["entry"].get("require_reclaim", False)),
        max_gap_pct=float(raw["entry"].get("max_gap_pct", 0.0)),
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

    so_raw = dict(raw.get("scaleout", {}))
    if "tp_atr" in so_raw:
        so_raw["tp_atr"] = tuple(so_raw["tp_atr"])
    if "ratios" in so_raw:
        so_raw["ratios"] = tuple(so_raw["ratios"])
    scaleout = ScaleOutConfig(**so_raw)
    squeeze = SqueezeConfig(**raw.get("squeeze", {}))
    regime = RegimeConfig(**raw.get("regime", {}))

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
        scaleout=scaleout,
        squeeze=squeeze,
        regime=regime,
        webhook=webhook,
        notify=notify,
        control=control,
        cooldown=cooldown,
        sizing=sizing,
        withdrawal=withdrawal,
    )
