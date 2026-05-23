"""Config loader. Parses config.yaml into typed dataclasses."""
from __future__ import annotations

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
class LoopConfig:
    bar_poll_interval_s: int = 30
    position_check_interval_s: int = 5
    # WS mark-feed watchdog thresholds (see incident 2026-05-23).
    # warn_s: log + try re-subscribe; fatal_s: exit so Docker restarts WS.
    # 180s warn is generous — ZEC/SOL trade ~14×/min, so a healthy WS
    # will tick the value through every 5–10s in practice.
    mark_stale_warn_s: int = 180
    mark_stale_fatal_s: int = 300
    mark_watchdog_interval_s: int = 30


@dataclass
class LogConfig:
    level: str = "INFO"
    db_path: str = "data/lighter_paper.db"


@dataclass
class BridgeConfig:
    host: str
    initial_collateral_usdc: float
    symbols: dict[str, SymbolConfig]
    entry: EntryConfig
    exits: ExitConfig
    pine: PineConfig = field(default_factory=PineConfig)
    loop: LoopConfig = field(default_factory=LoopConfig)
    log: LogConfig = field(default_factory=LogConfig)


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
    )

    exits = ExitConfig(**{k: float(v) for k, v in raw["exits"].items()})
    pine = PineConfig(**raw.get("pine", {}))
    loop = LoopConfig(**raw.get("loop", {}))
    log_cfg = LogConfig(**raw.get("log", {}))

    return BridgeConfig(
        host=raw["connection"]["host"],
        initial_collateral_usdc=float(raw["connection"]["initial_collateral_usdc"]),
        symbols=symbols,
        entry=entry,
        exits=exits,
        pine=pine,
        loop=loop,
        log=log_cfg,
    )
