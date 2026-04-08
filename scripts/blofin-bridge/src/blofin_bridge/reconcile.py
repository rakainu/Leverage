"""Startup reconciliation between SQLite and BloFin."""
from __future__ import annotations
from dataclasses import dataclass, field

from .blofin_client import BloFinClient
from .state import Store


@dataclass
class ReconcileReport:
    frozen_symbols: list[str] = field(default_factory=list)
    drift_count: int = 0
    details: list[str] = field(default_factory=list)


def _extract_inst_id(pos: dict) -> str:
    info = pos.get("info") or {}
    return info.get("instId") or pos.get("symbol", "").replace("/", "-").split(":")[0]


def reconcile(*, store: Store, blofin: BloFinClient) -> ReconcileReport:
    report = ReconcileReport()

    sqlite_open = {p.symbol: p for p in store.list_open_positions()}
    blofin_raw = blofin.fetch_positions()
    blofin_open = {
        _extract_inst_id(p): p
        for p in blofin_raw
        if float(p.get("contracts") or 0) != 0
    }

    # SQLite says open, BloFin says flat -> drift
    for sym in sqlite_open:
        if sym not in blofin_open:
            report.frozen_symbols.append(sym)
            report.drift_count += 1
            report.details.append(
                f"{sym}: SQLite has open position, BloFin flat"
            )

    # BloFin says open, SQLite says flat -> drift
    for sym in blofin_open:
        if sym not in sqlite_open:
            report.frozen_symbols.append(sym)
            report.drift_count += 1
            report.details.append(
                f"{sym}: BloFin has open position, SQLite flat"
            )

    return report
