import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import numpy as np, pandas as pd
from engine import Trade, RiskCfg, Costs
import portfolio_backtest as pb


def _trade(t0, t1, r):
    return Trade(side=1, entry_i=0, entry_time=pd.Timestamp(t0, tz="UTC"), entry_price=100,
                 exit_i=1, exit_time=pd.Timestamp(t1, tz="UTC"), exit_price=100, exit_reason="x",
                 notional=1000, qty=10, risk_usd=30, fees_usd=0, funding_usd=0, pnl_usd=0,
                 r_multiple=r, equity_after=0, bars_held=1, liq_price=0, eff_leverage=5, mae_frac=0)


def _df(n=10):
    idx = pd.date_range("2026-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1.0}, index=idx)


def test_run_merges_basket_into_one_account():
    # two coins, non-overlapping winners -> compounding account grows
    def strat(df, costs, risk, tfm, *, entry_gate=None, **p):
        return [_trade("2026-01-01", "2026-01-02", 1.0)]  # +1R each coin

    gen = RiskCfg(starting_equity=3000, risk_frac=0.10, max_leverage=10, compounding=False)
    acct = RiskCfg(starting_equity=3000, risk_frac=0.10, max_leverage=10, compounding=True)
    out = pb.run(strat, ["BTC", "ETH"], lambda c: _df(), 1440,
                 costs=Costs(0, 0, 0, 0), gen_risk=gen, acct_risk=acct, max_positions=5)
    # both +1R at 10% risk: depends on overlap handling, but account must end > start
    assert out["final_equity"] > 3000
    s = pb.summarize(out, 3000)
    assert s["total_return_pct"] > 0 and s["n_trades"] >= 1


def test_summarize_handles_empty():
    out = {"equity_curve": pd.Series(dtype=float), "trades": [], "liq": 0}
    s = pb.summarize(out, 3000)
    assert s["total_return_pct"] == 0.0 and s["n_trades"] == 0
