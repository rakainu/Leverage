"""Task 7 — per-regime optimize + walk-forward of each specialist, then freeze.

Each of the three specialists is optimized ONLY on the bars belonging to its
regime (long->Up, short->Down, range->Range), pooled across the 10-coin basket,
on Lighter costs. Objective mirrors stage8_fair_retest: maximize basket Calmar,
hard-kill any liquidation, penalize drawdown over the 25% ceiling, floor on trade
count. Each role is then 6-fold walk-forward-validated (re-optimized per fold,
each test fold labeled by its market regime) and the IS-best params are frozen to
frozen_params.json for the switcher / sweep / end-to-end run.

Regime definition (regime.classify) is held FIXED here; only specialist params are
searched (searching the regime gate too would overfit the validation).
"""
from __future__ import annotations
import os, sys, json
import numpy as np
import optuna

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "strategies"))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "backtest")))
sys.path.insert(0, HERE)  # hybrid -> data (front of path, beats backtest/data.py)
import data
from regime import classify
from engine import Costs, RiskCfg, simulate as sig_sim
from optimizer import _suggest
from metrics import extended_metrics
import long_momo, short_momo, range_rsi2

optuna.logging.set_verbosity(optuna.logging.WARNING)

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=20, liq_buffer=2.5, compounding=False)
HARD_KILL = -1e9
fin = lambda x: 999.0 if (x == float("inf") or (isinstance(x, float) and x != x)) else x

SPACES = {
    "long":  {"dc_high": ("int", 10, 60), "dc_low": ("int", 5, 30), "dc_stop": ("int", 3, 15), "use_tight_stop": ("cat", [False, True])},
    "short": {"dc_high": ("int", 10, 60), "dc_low": ("int", 5, 30), "dc_stop": ("int", 3, 15), "use_tight_stop": ("cat", [False, True])},
    "range": {"lo": ("int", 2, 15), "hi": ("int", 85, 98), "rsi_len": ("cat", [2, 3]), "sma_len": ("cat", [5, 10]), "sl_atr": ("float", 0.8, 2.5), "max_bars": ("int", 12, 48)},
}
ROLE_TF = {"long": "15m", "short": "15m", "range": "5m"}
ROLE_WANT = {"long": 1, "short": -1, "range": 0}
TFM = {"15m": 15, "5m": 5}
IS_TRIALS, FOLD_TRIALS, NF = 40, 20, 6


def _gate(reg, idx, want):
    return (reg == want).reindex(idx, method="ffill").fillna(False).values.astype(bool)


def preload(coins):
    store = {}
    for c in coins:
        reg = classify(data.load(c, "1h"))
        df15, df5 = data.load(c, "15m"), data.load(c, "5m")
        store[c] = {
            "15m": df15, "5m": df5,
            "g_long": _gate(reg, df15.index, 1),
            "g_short": _gate(reg, df15.index, -1),
            "g_range": _gate(reg, df5.index, 0),
        }
    return store


def role_trades(role, store, params, costs, coins, lo=0.0, hi=1.0):
    tfm = TFM[ROLE_TF[role]]
    tbc = {}
    for c in coins:
        s = store[c]
        if role == "range":
            df, g = s["5m"], s["g_range"]
        else:
            df, g = s["15m"], (s["g_long"] if role == "long" else s["g_short"])
        a, b = int(lo * len(df)), int(hi * len(df))
        d, gg = df.iloc[a:b], g[a:b]
        if role == "long":
            tr = long_momo.simulate(d, costs, RISK, tfm, entry_gate=gg, **params)
        elif role == "short":
            tr = short_momo.simulate(d, costs, RISK, tfm, entry_gate=gg, **params)
        else:
            sigs = [sg for sg in range_rsi2.signals(d, **params) if gg[sg.i]]
            tr = sig_sim(d, sigs, costs, RISK, tfm)
        tbc[c] = tr
    return tbc


def basket_metrics(tbc, se=RISK.starting_equity):
    allt = sorted([t for tr in tbc.values() for t in tr], key=lambda t: t.exit_time)
    if not allt:
        return None
    pnls = np.array([t.pnl_usd for t in allt])
    eq = np.concatenate([[se], se + np.cumsum(pnls)])
    peak = np.maximum.accumulate(eq); maxdd = float(((peak - eq) / peak).max() * 100)
    w = pnls[pnls > 0]; l = -pnls[pnls < 0]
    pf = w.sum() / l.sum() if l.sum() > 0 else float("inf")
    payoff = (w.mean() / l.mean()) if (len(w) and len(l)) else float("inf")
    span = max((allt[-1].exit_time - allt[0].entry_time).total_seconds() / 86400, 1e-9)
    net = pnls.sum() / se * 100
    cagr = net * (365 / span)
    rets = pnls / se; sd = rets.std(ddof=1) if len(rets) > 1 else 0
    sharpe = rets.mean() / sd * np.sqrt(365 * len(allt) / span) if sd > 0 else 0
    return dict(n=len(allt), pf=pf, wr=len(w) / len(allt) * 100, payoff=payoff, net_pct=net,
                cagr=cagr, sharpe=sharpe, calmar=(cagr / maxdd if maxdd > 0 else float("inf")), maxdd=maxdd)


def basket_liq(tbc):
    return sum(extended_metrics(tr, RISK.starting_equity, compounding=False)["liq_hits"] for tr in tbc.values())


def basket_optimize(store, role, coins, lo, hi, n_trials, min_trades, seed=42):
    space = SPACES[role]

    def obj(trial):
        p = {k: _suggest(trial, k, v) for k, v in space.items()}
        try:
            tbc = role_trades(role, store, p, LIGHTER, coins, lo, hi)
        except Exception:
            return HARD_KILL
        if basket_liq(tbc) > 0:
            return HARD_KILL
        m = basket_metrics(tbc)
        if m is None or m["n"] < min_trades:
            return HARD_KILL + (0 if m is None else m["n"])
        v = m["calmar"] if m["calmar"] != float("inf") else 1e6
        over = m["maxdd"] - 25.0
        if over > 0:
            v -= over * abs(v or 1) * 0.1 + over
        return float(v)

    st = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed))
    st.optimize(obj, n_trials=n_trials)
    fixed = {k: v for k, v in space.items() if not isinstance(v, tuple)}
    return {**fixed, **st.best_params}


def fold_regime(role, store, coins, lo, hi):
    tf = ROLE_TF[role]
    rs = []
    for c in coins:
        d = store[c][tf]
        seg = d.iloc[int(lo * len(d)):int(hi * len(d))]
        if len(seg) > 1:
            rs.append((seg["Close"].iloc[-1] / seg["Close"].iloc[0] - 1) * 100)
    return float(np.mean(rs)) if rs else 0.0


def optimize_role(role, store, coins):
    print(f"\n{'='*70}\nROLE: {role}  (regime={ROLE_WANT[role]}, TF={ROLE_TF[role]})\n{'='*70}")
    best = basket_optimize(store, role, coins, 0.0, 0.70, IS_TRIALS, min_trades=80)
    oos = basket_metrics(role_trades(role, store, best, LIGHTER, coins, 0.70, 1.0))
    print(f"best: {best}")
    if oos:
        print(f"OOS(last30%): n={oos['n']} PF={fin(oos['pf']):.2f} payoff={fin(oos['payoff']):.2f} "
              f"net={oos['net_pct']:+.0f}% DD={oos['maxdd']:.0f}% sharpe={oos['sharpe']:.2f}")
    print(f"Walk-forward {NF} folds (re-optimized; regime = mean buy&hold of test window):")
    passed = 0
    for k in range(NF):
        f0, f1 = k / NF, (k + 1) / NF
        cut = f0 + 0.70 * (f1 - f0)
        bp = basket_optimize(store, role, coins, f0, cut, FOLD_TRIALS, min_trades=20, seed=42 + k)
        fm = basket_metrics(role_trades(role, store, bp, LIGHTER, coins, cut, f1))
        reg = fold_regime(role, store, coins, cut, f1)
        if fm is None:
            print(f"  fold{k} regime={reg:>+4.0f}% no trades"); continue
        ok = (fm["pf"] >= 1.2 and fm["payoff"] >= 1.0 and fm["maxdd"] <= 30 and fm["net_pct"] > 0)
        passed += ok
        print(f"  fold{k} regime={reg:>+4.0f}% | n={fm['n']:>3} PF={fin(fm['pf']):.2f} "
              f"payoff={fin(fm['payoff']):.2f} net={fm['net_pct']:>+5.0f}% DD={fm['maxdd']:>3.0f}% "
              f"{'PASS' if ok else 'fail'}")
    print(f"VERDICT {role}: {passed}/{NF} folds -> {'ROBUST' if passed >= int(np.ceil(0.75 * NF)) else 'FRAGILE'}")
    return best


def main():
    coins = data.COINS
    print(f"Optimizing 3 specialists on {len(coins)} coins, 3y, Lighter costs (per-regime bars)")
    store = preload(coins)
    frozen = {role: optimize_role(role, store, coins) for role in ("long", "short", "range")}
    out = os.path.join(HERE, "frozen_params.json")
    json.dump(frozen, open(out, "w"), indent=2, default=lambda o: bool(o) if isinstance(o, np.bool_) else float(o))
    print(f"\nFROZEN -> {out}\n{json.dumps(frozen, indent=2)}")


if __name__ == "__main__":
    main()
