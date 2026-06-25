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
    title: str = "Lighter"             # dashboard heading; per-book override (default preserves legacy)
    subtitle: str = "paper bridge"     # heading sub-label (Booster = "testnet · real orders")
    footer: str = "regime_mr · accel 3.0 · trend-gate 0.08 · cooldown 3/180"  # foot strategy descriptor; per-book override
    show_fill_quality: bool = False    # maker-order books (scalper) show the fill-quality panel; market-entry books hide it
    show_withdrawals: bool = True      # compounding/skim books show the withdrawals panel; off-books (Apex) hide it
    bt_wr: float = 88.0                # backtest WR baseline the live edge is measured against (per-book)
    bt_pf: float = 1.5                 # backtest PF baseline (per-book)
    protections: list | None = None    # per-strategy guard chips (None -> app default)


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
        title=str(data.get("title", "Lighter")),
        subtitle=str(data.get("subtitle", "paper bridge")),
        footer=str(data.get("footer", "regime_mr · accel 3.0 · trend-gate 0.08 · cooldown 3/180")),
        show_fill_quality=bool(data.get("show_fill_quality", False)),
        show_withdrawals=bool(data.get("show_withdrawals", True)),
        bt_wr=float(data.get("bt_wr", 88.0)),
        bt_pf=float(data.get("bt_pf", 1.5)),
        protections=data.get("protections"),
    )
