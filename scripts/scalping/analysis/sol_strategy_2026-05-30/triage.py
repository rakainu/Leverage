"""Triage scan: run each strategy family with sensible defaults across SOL 5m/15m/1h
on the FULL sample, after BloFin fees+slippage. Goal: reject dead families fast,
keep families that show any positive edge for the IS/OOS sweep stage.
"""
from __future__ import annotations
import itertools
from btengine import load_sol, simulate, metrics, fmt, Costs, RiskCfg
import strategies as S

COSTS = Costs()           # BloFin defaults: taker .06, maker .02, slip .05, funding .01/8h
RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=30, compounding=True)
TF_MIN = {"5m": 5, "15m": 15, "1h": 60}

# A few default param sets per family (NOT tuned — just to detect a pulse)
DEFAULTS = {
    "donchian": [
        dict(channel=20, sl_atr=2.0, tp_atr=3.0),
        dict(channel=20, sl_atr=2.0, trail=True, tp_atr=3.0),
        dict(channel=50, sl_atr=2.5, tp_atr=4.0),
    ],
    "zfade": [
        dict(z_period=20, z_entry=2.5, sl_atr=2.0, tp_mode="mean"),
        dict(z_period=20, z_entry=3.0, sl_atr=2.5, tp_mode="mean"),
        dict(z_period=30, z_entry=2.5, sl_atr=2.0, tp_mode="atr", tp_atr=2.0),
    ],
    "ema_pullback": [
        dict(fast=50, slow=200, sl_atr=2.0, tp_atr=3.0),
        dict(fast=50, slow=200, sl_atr=2.0, trail=True, tp_atr=3.0),
        dict(fast=21, slow=50, sl_atr=2.0, tp_atr=3.0),
    ],
    "adx_breakout": [
        dict(channel=20, adx_min=20, sl_atr=2.0, tp_atr=3.0),
        dict(channel=20, adx_min=25, sl_atr=2.0, trail=True, tp_atr=3.0),
        dict(channel=50, adx_min=20, sl_atr=2.5, tp_atr=4.0),
    ],
}

def main():
    for tf in ["5m", "15m", "1h"]:
        df = load_sol(tf)
        print(f"\n{'='*108}\nSOL {tf}  ({len(df)} bars  {df.index[0]} -> {df.index[-1]})\n{'='*108}")
        for fam, fn in S.REGISTRY.items():
            for params in DEFAULTS[fam]:
                sigs = fn(df, **params)
                trades = simulate(df, sigs, COSTS, RISK, TF_MIN[tf])
                m = metrics(trades, RISK.starting_equity)
                ps = ",".join(f"{k}={v}" for k, v in params.items())
                print(f"  {fam:13} [{ps:42}] {fmt(m)}")

if __name__ == "__main__":
    main()
