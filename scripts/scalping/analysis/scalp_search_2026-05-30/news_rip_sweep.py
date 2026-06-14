"""News-rip mitigation sweep for regime_mr. Faithful clone of strat_lib.regime_mr
+ 4 optional levers, run through the EXACT basket_validate methodology
(per-coin -> pool by time -> OOS 70/30 -> 4 walk-forward folds -> hi-slip ->
fixed-notional honest liquidation). Baseline (all levers off) reproduces the
validated pooled PF 1.49 / WR 89%.

Levers:
  z_rollover    : only fade once z is turning back toward VWAP (z[i] vs z[i-1])
  z_max         : skip entries when |z| > z_max (extreme = breakout, not fade)
  min_slope_pct : require |EMA(trend_len) slope| / price * 100 >= floor (kill flat-regime fades)
  cooldown(K,m) : after K consecutive POOLED losses, suppress entries for m minutes (live /kill model)

Pure numbers out. No filtering of results.
"""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
import common as K
from btengine import simulate
import strat_lib as SL

BASE = dict(trend_len=200, z_period=30, z_entry=1.5, sl_atr=2.0,
            tp_frac=0.3, max_bars=12, limit_atr=0.25)
NREF, LREF = 200, 10          # fixed-notional reference: $200 @ 10x (validated sweet spot)


def regime_mr_x(df, side="both", trend_len=200, slope_lb=20, z_period=30, z_entry=1.5,
                sl_atr=2.0, tp_frac=0.3, max_bars=12, limit_atr=0.25, atr_p=14,
                z_max=float("inf"), min_slope_pct=0.0, z_rollover=False):
    """Exact regime_mr + per-signal levers (z_max / min_slope_pct / z_rollover)."""
    C = df["Close"]; a = SL.atr(df, atr_p)
    e = SL.ema(C, trend_len); slope = e - e.shift(slope_lb)
    vwap = SL.session_vwap(df)
    z = SL.rolling_zscore(C - vwap, z_period)
    cv, av, vv, zv, slv = C.values, a.values, vwap.values, z.values, slope.values
    sigs = []
    for i in range(len(df)):
        if np.isnan(zv[i]) or np.isnan(av[i]) or av[i] <= 0 or np.isnan(slv[i]) or np.isnan(vv[i]):
            continue
        up = slv[i] > 0
        side_val = 1 if (zv[i] <= -z_entry and up) else (-1 if (zv[i] >= z_entry and not up) else 0)
        if side_val == 0 or not SL._allow(side_val, side):
            continue
        # --- levers ---
        if abs(zv[i]) > z_max:
            continue
        if min_slope_pct > 0.0 and abs(slv[i]) / cv[i] * 100.0 < min_slope_pct:
            continue
        if z_rollover:
            if i == 0 or np.isnan(zv[i - 1]):
                continue
            if side_val < 0 and not (zv[i] < zv[i - 1]):   # short: z must be falling
                continue
            if side_val > 0 and not (zv[i] > zv[i - 1]):   # long: z must be rising toward 0
                continue
        # --- identical signal construction ---
        exp_entry = cv[i] - limit_atr * av[i] if side_val > 0 else cv[i] + limit_atr * av[i]
        tp_dist = abs(vv[i] - exp_entry) * tp_frac
        if tp_dist <= 0:
            continue
        sigs.append(SL.Signal(i=i, side=side_val, sl_dist=sl_atr * av[i], tp_dist=tp_dist,
                              entry_style="limit" if limit_atr > 0 else "market",
                              limit_dist=limit_atr * av[i], max_bars=max_bars))
    return sigs


def coin_trades(coin, params, costs):
    df = K.load(coin, "15m")
    tr = simulate(df, regime_mr_x(df, side="both", **params), costs, K.RISK, K.TF_MIN["15m"])
    rows = []
    for t in tr:
        r = t.side * (t.exit_price - t.entry_price) / t.entry_price
        rows.append(dict(et=t.entry_time, xt=t.exit_time, r=r, mae=t.mae_frac,
                         bars=t.bars_held, coin=coin, side=t.side))
    return rows


def apply_cooldown(rows, k_losses, cd_min):
    """rows sorted by entry_time. After k consecutive losses, suppress entries for cd_min minutes."""
    if not k_losses:
        return rows
    cd = pd.Timedelta(minutes=cd_min)
    out = []; consec = 0; until = None
    for row in rows:
        if until is not None and row["et"] < until:
            continue                       # suppressed (flat)
        out.append(row)
        if row["r"] < 0:
            consec += 1
            if consec >= k_losses:
                until = row["xt"] + cd; consec = 0
        else:
            consec = 0
    return out


def edge(rows, weeks):
    if not rows:
        return None
    rets = np.array([r["r"] for r in rows]); mae = np.array([r["mae"] for r in rows])
    w = rets[rets > 0]; l = rets[rets < 0]
    pf = w.sum() / -l.sum() if l.sum() < 0 else float("inf")
    streak = mx = 0
    for r in rets:
        if r < 0:
            streak += 1; mx = max(mx, streak)
        else:
            streak = 0
    liq = (1.0 / LREF) * (1 - 0.005)
    pnl = np.where(mae >= liq, -NREF / LREF, rets * NREF)
    eq = np.cumsum(np.concatenate([[0.0], pnl])); peak = np.maximum.accumulate(eq)
    dd = (peak - eq).max()
    return dict(n=len(rets), tpw=len(rets) / weeks if weeks else 0,
                pf=pf, wr=100 * len(w) / len(rets), exp=100 * rets.mean(),
                streak=mx, net=pnl.sum(), dd=dd, liqs=int((mae >= liq).sum()))


def build_pool(params, costs, coins, cooldown):
    pooled = []
    per_coin = {}
    for c in coins:
        rows = coin_trades(c, params, costs)
        per_coin[c] = rows
        pooled += rows
    pooled.sort(key=lambda r: r["et"])
    if cooldown:
        pooled = apply_cooldown(pooled, cooldown[0], cooldown[1])
    return pooled, per_coin


def pf_str(pf):
    return "inf" if pf == float("inf") else f"{pf:.2f}"


def run_config(label, params, cooldown, coins, weeks):
    pooled, per_coin = build_pool(params, K.LIGHTER, coins, cooldown)
    e = edge(pooled, weeks)
    if e is None:
        print(f"{label:26}  (no trades)"); return None
    # OOS 70/30 on pooled-by-time
    times = [r["et"] for r in pooled]
    cut = times[int(len(times) * 0.70)]
    oos = edge([r for r in pooled if r["et"] >= cut], weeks * 0.3)
    # walk-forward 4 folds
    t0, t1 = pooled[0]["et"], pooled[-1]["et"]
    bounds = pd.date_range(t0, t1, periods=5)
    wf = [edge([r for r in pooled if bounds[k] <= r["et"] < bounds[k + 1]], weeks / 4) for k in range(4)]
    wf_pf = [f["pf"] for f in wf if f]
    wfmin = min(wf_pf) if wf_pf else 0.0
    # hi-slip
    pooled_hi, _ = build_pool(params, K.LIGHTER_HISLIP, coins, cooldown)
    ehi = edge(pooled_hi, weeks)
    print(f"{label:26}{e['n']:>6}{e['tpw']:>7.1f}{pf_str(e['pf']):>6}{e['wr']:>5.0f}"
          f"{e['exp']:>+8.3f}{pf_str(oos['pf']):>7}{oos['wr']:>5.0f}{pf_str(wfmin):>7}"
          f"{pf_str(ehi['pf']):>8}{e['streak']:>7}{e['net']:>8.0f}{e['dd']:>8.0f}{e['liqs']:>5}")
    return per_coin, pooled


def per_coin_line(label, per_coin, coins, weeks):
    parts = []
    for c in coins:
        e = edge(per_coin[c], weeks)
        parts.append(f"{c}:{pf_str(e['pf'])}/{e['wr']:.0f}%/{e['n']}" if e else f"{c}:-")
    print(f"  {label:24} " + "  ".join(parts))


def main():
    coins = K.COINS
    weeks = K.weeks_of(K.load(coins[0], "15m"), "15m")
    print(f"DATA {coins} 15m  ~{weeks:.1f}wk  (fixed-notional ${NREF}@{LREF}x)")
    print("levers off = validated baseline. exp%=mean per-trade return. streak=worst consec losses.")
    print(f"\n{'config':26}{'n':>6}{'t/wk':>7}{'PF':>6}{'WR':>5}{'exp%':>8}"
          f"{'oPF':>7}{'oWR':>5}{'wfPF':>7}{'hsPF':>8}{'strk':>7}{'net$':>8}{'DD$':>8}{'liq':>5}")

    configs = []
    configs.append(("baseline", dict(BASE), None))
    # lever 1: z-rollover
    configs.append(("z_rollover", {**BASE, "z_rollover": True}, None))
    # lever 2: z_max cap
    for zm in (4.0, 3.5, 3.0, 2.5):
        configs.append((f"z_max={zm}", {**BASE, "z_max": zm}, None))
    # lever 3: min slope floor (% of price)
    for ms in (0.01, 0.02, 0.05, 0.10):
        configs.append((f"min_slope={ms}%", {**BASE, "min_slope_pct": ms}, None))
    # lever 4: cooldown (K consecutive losses, minutes)  [15m bar => 180=12b/3h, 360=24b/6h, 720=48b]
    for kk, mm in ((2, 180), (3, 180), (2, 360), (3, 360), (4, 360), (3, 720)):
        configs.append((f"cooldown({kk},{mm}m)", dict(BASE), (kk, mm)))
    # combos
    configs.append(("roll+zmax3.0", {**BASE, "z_rollover": True, "z_max": 3.0}, None))
    configs.append(("roll+cd(3,360)", {**BASE, "z_rollover": True}, (3, 360)))
    configs.append(("zmax3+slope.02+cd(3,360)",
                    {**BASE, "z_max": 3.0, "min_slope_pct": 0.02}, (3, 360)))
    configs.append(("ALL(roll+zmax3+sl.02+cd)",
                    {**BASE, "z_rollover": True, "z_max": 3.0, "min_slope_pct": 0.02}, (3, 360)))

    keep = {}
    for label, params, cd in configs:
        res = run_config(label, params, cd, coins, weeks)
        if res:
            keep[label] = res[0]

    print("\nPER-COIN PF/WR/n (generalization check):")
    for label in ("baseline", "z_rollover", "z_max=3.0", "min_slope=0.02%",
                  "roll+zmax3.0", "ALL(roll+zmax3+sl.02+cd)"):
        if label in keep:
            per_coin_line(label, keep[label], coins, weeks)


if __name__ == "__main__":
    main()
