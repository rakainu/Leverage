# backtest/tests/test_portfolio_sim.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pandas as pd
from engine import Trade, RiskCfg
import portfolio_sim
def _t(coin, t0, t1, r):
    ts=pd.Timestamp(t0, tz="UTC"); te=pd.Timestamp(t1, tz="UTC")
    return Trade(side=1, entry_i=0, entry_time=ts, entry_price=100, exit_i=1, exit_time=te,
        exit_price=100, exit_reason="x", notional=1000, qty=10, risk_usd=30, fees_usd=0,
        funding_usd=0, pnl_usd=0, r_multiple=r, equity_after=0, bars_held=1, liq_price=0,
        eff_leverage=5, mae_frac=0)
def test_compounding_grows_risk():
    R=RiskCfg(starting_equity=3000, risk_frac=0.10, compounding=True)
    intents={"BTC":[_t("BTC","2026-01-01","2026-01-02",1.0), _t("BTC","2026-01-03","2026-01-04",1.0)]}
    out=portfolio_sim.simulate(intents, R, max_positions=5, max_total_notional=1e9)
    # +10% then +10% compounded on $3000 = 3000*1.1*1.1 = 3630
    assert abs(out["final_equity"] - 3630) < 1
def test_position_cap_skips_overlap():
    R=RiskCfg(starting_equity=3000, risk_frac=0.10, compounding=False)
    # three fully-overlapping trades, cap=2 -> only 2 taken
    intents={c:[_t(c,"2026-01-01","2026-01-10",1.0)] for c in ["BTC","ETH","SOL"]}
    out=portfolio_sim.simulate(intents, R, max_positions=2, max_total_notional=1e9)
    assert len(out["trades"]) == 2
