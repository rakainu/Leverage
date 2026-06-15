"""Full robustness on the one improvement that earns its place: min_squeeze 10->12
(tighter compression => cleaner release). Compare BASE vs IMPROVED, and 4-coin vs
3-coin (drop SOL, the only net-negative coin on fresh data)."""
from __future__ import annotations
import os, sys
import pandas as pd
HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from revalidate_squeeze import (load_1h, portfolio, show, COINS, LIGHTER, BLOFIN, bt)  # noqa


def suite(tag, coins, variant):
    full = {c: load_1h(c) for c in coins}
    JUN = pd.Timestamp("2026-06-01", tz="UTC")
    jun = {c: full[c][full[c].index >= JUN] for c in coins}
    isd = {c: bt.split_is_oos(full[c], 0.70)[0] for c in coins}
    oosd = {c: bt.split_is_oos(full[c], 0.70)[1] for c in coins}
    print(f"\n### {tag}  coins={coins}  variant={variant}")
    show("Lighter full", portfolio(full, LIGHTER, variant))
    show("BloFin full", portfolio(full, BLOFIN, variant))
    show("IS 70%", portfolio(isd, LIGHTER, variant))
    show("OOS 30%", portfolio(oosd, LIGHTER, variant))
    show("June-forward", portfolio(jun, LIGHTER, variant))


def main():
    suite("BASE 4-coin", COINS, "base")
    suite("IMPROVED (sq12) 4-coin", COINS, "sq12")
    suite("IMPROVED (sq12) 3-coin no-SOL", ["ETH", "ZEC", "HYPE"], "sq12")


if __name__ == "__main__":
    main()
