from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class DashboardConfig:
    db_path: str
    lighter_host: str
    initial_collateral_usdc: float
    symbols: dict[str, int]          # name -> market_id
    live_ms: int
    static_ms: int
    mark_cache_ttl_s: float


def load_config(path: str | Path) -> DashboardConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    refresh = data.get("refresh", {})
    return DashboardConfig(
        db_path=data["db_path"],
        lighter_host=data["lighter_host"],
        initial_collateral_usdc=float(data["initial_collateral_usdc"]),
        symbols={k: int(v) for k, v in data["symbols"].items()},
        live_ms=int(refresh.get("live_ms", 3000)),
        static_ms=int(refresh.get("static_ms", 15000)),
        mark_cache_ttl_s=float(data.get("mark_cache_ttl_s", 2.0)),
    )
