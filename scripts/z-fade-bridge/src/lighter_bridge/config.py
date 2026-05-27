"""Config loader. Parses config.yaml into typed Z-Fade dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class SymbolConfig:
    market_id: int
    enabled: bool
    margin_usdt: float
    leverage: float


@dataclass
class ZFadeConfig:
    """Entry/indicator params — mirrors sweeps strat_zscore.ZParams (validated config)."""
    timeframe: str = "5m"
    window: int = 80              # z-score rolling window
    z_thresh: float = 3.0         # fade |z| >= this
    rsi_len: int = 14
    rsi_os: float = 30
    rsi_ob: float = 70
    use_rsi: bool = True
    bb_len: int = 20
    bb_mult: float = 2.0
    bb_width_min: float = 0.02    # volatility floor
    use_bb: bool = True
    ema_len: int = 200
    use_ema: bool = False         # trend filter OFF (pure fade validated better)
    adx_len: int = 14
    adx_max: float = 40.0         # regime gate: only fade when ranging (ADX <= this)
    use_adx: bool = True
    atr_len: int = 14
    cooldown_bars: int = 5        # bars between entries per symbol


@dataclass
class ZFadeExits:
    sl_atr: float = 1.5           # stop = entry -/+ sl_atr * ATR
    tp_atr: float = 3.0           # target = entry +/- tp_atr * ATR


@dataclass
class LoopConfig:
    bar_poll_interval_s: int = 30
    position_check_interval_s: int = 5
    # WS mark-feed watchdog thresholds (see lighter-bridge incidents 2026-05-23/25).
    mark_reconnect_s: int = 60
    mark_stale_warn_s: int = 180
    mark_stale_fatal_s: int = 300
    mark_watchdog_interval_s: int = 30


@dataclass
class LogConfig:
    level: str = "INFO"
    db_path: str = "data/zfade_paper.db"


@dataclass
class BridgeConfig:
    host: str
    initial_collateral_usdc: float
    symbols: dict[str, SymbolConfig]
    strat: ZFadeConfig = field(default_factory=ZFadeConfig)
    exits: ZFadeExits = field(default_factory=ZFadeExits)
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

    strat = ZFadeConfig(**raw.get("strategy", {}))
    exits = ZFadeExits(**raw.get("exits", {}))
    loop = LoopConfig(**raw.get("loop", {}))
    log_cfg = LogConfig(**raw.get("log", {}))

    return BridgeConfig(
        host=raw["connection"]["host"],
        initial_collateral_usdc=float(raw["connection"]["initial_collateral_usdc"]),
        symbols=symbols,
        strat=strat,
        exits=exits,
        loop=loop,
        log=log_cfg,
    )
