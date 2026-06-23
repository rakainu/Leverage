import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backtest")))
from engine import Costs, RiskCfg
import switcher


def test_coin_intents_runs_and_returns_trades():
    costs = Costs(0, 0, 0.05, 0.01)
    risk = RiskCfg(starting_equity=3000, risk_frac=0.02, max_leverage=20, compounding=True)
    params = {"long": {}, "short": {}, "range": {}}
    trades = switcher.coin_intents("BTC", costs, risk, params, regime_cfg={})
    assert isinstance(trades, list)
    assert all(t.side in (1, -1) for t in trades)
    # the three specialists should collectively produce some trades over 3y of BTC
    assert len(trades) > 0
