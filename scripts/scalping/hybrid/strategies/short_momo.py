from __future__ import annotations
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backtest")))
from engine import Costs, RiskCfg, Trade

def simulate(df, costs, risk, tf_minutes, *, dc_high=20, dc_low=10, dc_stop=8,
             use_tight_stop=False, entry_gate=None, **_ignored):
    O,H,L,C = (df[k].values for k in ("Open","High","Low","Close"))
    idx = df.index; n = len(df)
    dn_lvl = pd.Series(L).rolling(dc_high).min().shift(1).values     # breakdown level
    up_trail = pd.Series(H).rolling(dc_low).max().shift(1).values    # trailing stop (channel)
    tight = pd.Series(H).rolling(dc_stop).max().shift(1).values
    if entry_gate is not None:
        entry_gate = np.asarray(entry_gate, bool)
    slip = costs.slippage_pct/100; taker = costs.taker_pct/100
    trades=[]; equity=risk.starting_equity; i=0
    while i < n-1:
        if not np.isfinite(dn_lvl[i]) or not np.isfinite(up_trail[i]):
            i+=1; continue
        if entry_gate is not None and not entry_gate[i]:
            i+=1; continue
        if L[i] > dn_lvl[i]:
            i+=1; continue
        f=i+1; raw=min(dn_lvl[i], O[f]); entry=raw*(1-slip)
        init_stop = tight[i] if use_tight_stop else up_trail[i]
        if not np.isfinite(init_stop) or init_stop <= entry:
            i+=1; continue
        sdf=(init_stop-entry)/entry
        eq=equity if risk.compounding else risk.starting_equity
        risk_usd=eq*risk.risk_frac; notional=risk_usd/sdf
        if notional>eq*risk.max_leverage: notional=eq*risk.max_leverage; risk_usd=notional*sdf
        qty=notional/entry
        eff=min(risk.max_leverage, max(1.0, 1.0/(sdf*risk.liq_buffer)))
        liq=entry*(1+(1.0/eff)*(1-risk.maint_margin_rate))
        trail=init_stop; mae=0.0; exit_i=exit_px=reason=None; j=f
        while j<n:
            mae=max(mae,(H[j]-entry)/entry)
            if np.isfinite(up_trail[j]): trail=min(trail, up_trail[j])   # ratchet down
            if H[j]>=trail:
                exit_px=trail*(1+slip); reason="trail" if trail<init_stop else "stop"; exit_i=j; break
            j+=1
        if exit_i is None:
            exit_i=n-1; exit_px=C[exit_i]*(1+slip); reason="eod"
        bars=exit_i-f; hours=bars*tf_minutes/60
        fees=notional*taker+(qty*exit_px)*taker
        funding=notional*(costs.funding_pct_per_8h/100)*(hours/8)
        pnl=(entry-exit_px)*qty - fees - funding; equity+=pnl
        trades.append(Trade(side=-1, entry_i=f, entry_time=idx[f], entry_price=entry,
            exit_i=exit_i, exit_time=idx[exit_i], exit_price=exit_px, exit_reason=reason,
            notional=notional, qty=qty, risk_usd=risk_usd, fees_usd=fees, funding_usd=funding,
            pnl_usd=pnl, r_multiple=pnl/risk_usd if risk_usd>0 else 0, equity_after=equity,
            bars_held=bars, liq_price=liq, eff_leverage=eff, mae_frac=mae))
        i=exit_i+1
    return trades
