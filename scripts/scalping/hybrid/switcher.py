"""Regime -> specialist routing for one coin.

Classify market regime on the 1h context TF (regime.classify), then run each
specialist on its execution TF gated to only the bars whose ffill'd regime
matches that specialist's role:
  Up    -> long_momo  on 15m   (Donchian breakout long)
  Down  -> short_momo on 15m   (Donchian breakdown short)
  Range -> range_rsi2 on 5m    (Connors RSI-2 mean reversion)

The 1h regime is reindexed onto the exec TF with forward-fill; because
regime.classify is causal (uses only confirmed prior 1h bars), a regime value
timestamped T is known by time T, so gating an exec entry at time >= T has no
lookahead. The union of the three trade streams is this coin's intent list.
"""
from __future__ import annotations
import os, sys
import pandas as pd

# insert hybrid dir LAST so it lands at sys.path[0] and `import data` resolves to
# hybrid/data.py, not the unrelated backtest/data.py (ccxt loader) on the path too.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "strategies"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backtest")))
sys.path.insert(0, os.path.dirname(__file__))
import data
from regime import classify
import long_momo, short_momo, range_rsi2
from engine import simulate as sig_sim


def _gate(reg_1h: pd.Series, exec_index, want: int):
    """Bool array aligned to exec_index: True where the ffill'd 1h regime == want."""
    g = (reg_1h == want).reindex(exec_index, method="ffill").fillna(False)
    return g.values.astype(bool)


def coin_intents(coin, costs, risk, params, *, regime_cfg=None):
    regime_cfg = regime_cfg or {}
    reg = classify(data.load(coin, "1h"), **regime_cfg)
    df15 = data.load(coin, "15m")
    df5 = data.load(coin, "5m")

    longs = long_momo.simulate(df15, costs, risk, 15,
                               entry_gate=_gate(reg, df15.index, 1), **params.get("long", {}))
    shorts = short_momo.simulate(df15, costs, risk, 15,
                                 entry_gate=_gate(reg, df15.index, -1), **params.get("short", {}))
    # the Signal-based engine has no entry_gate hook -> filter range signals by regime first
    rg = _gate(reg, df5.index, 0)
    sigs = [s for s in range_rsi2.signals(df5, **params.get("range", {})) if rg[s.i]]
    ranges = sig_sim(df5, sigs, costs, risk, 5)
    return longs + shorts + ranges
