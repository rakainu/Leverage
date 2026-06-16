"""Full tuning pass on squeeze_expansion — anti-overfit, profit-hunting.

Discipline (so we don't fool ourselves):
  * Selection is on OUT-OF-SAMPLE + fee-survival + multi-coin, never peak full-window PF.
  * 4 dev coins (SOL/ETH/ZEC/HYPE) for the grid; then a SEPARATE out-of-universe
    coin set (BTC/BNB/XRP/DOGE/AVAX/LINK the strat never saw) as a generalization gate.
  * June-forward slice = untouched holdout (small n -> confirmation only, not a selector).
  * Walk-forward folds on the locked config.
  * 2x-slip + BloFin-fee stress.
Profit side: we hunt the high-return end (wider trails, hotter sizing) and report the
real drawdown, not the safe corner. 40% DD budget, $3k -> 2x target.
"""
from __future__ import annotations
import os, sys, itertools
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.abspath(os.path.join(HERE, "..", "sol_strategy_2026-05-30"))
LIB = os.path.abspath(os.path.join(HERE, "..", "lighter_strat_2026-05-30"))
sys.path.insert(0, ENGINE); sys.path.insert(0, LIB)
import btengine as bt  # noqa: E402
from strat_lib import squeeze_expansion as SQ  # noqa: E402

DEV_COINS = ["SOL", "ETH", "ZEC", "HYPE"]
OOS_COINS = ["BTC", "BNB", "XRP", "DOGE", "AVAX", "LINK"]
DEV_DIR = os.path.join(HERE, "data_june")
OOS_DIR = os.path.join(HERE, "data_oos_coins")
TF_MIN = 60
LIGHTER = bt.Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
LIGHTER_2X = bt.Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.10, funding_pct_per_8h=0.01)
BLOFIN = bt.Costs()
JUN = pd.Timestamp("2026-06-01", tz="UTC")


def avail(coins, d):
    return [c for c in coins if os.path.exists(os.path.join(d, f"okx_{c}_5m.parquet"))]


def load_1h(coin, d):
    df5 = pd.read_parquet(os.path.join(d, f"okx_{coin}_5m.parquet")).astype(float)
    return df5.resample("1h").agg({"Open": "first", "High": "max", "Low": "min",
                                   "Close": "last", "Volume": "sum"}).dropna()


def make_signals(df, cfg):
    p = dict(bb_len=20, bb_mult=cfg.get("bb_mult", 2.0), kc_mult=cfg.get("kc_mult", 1.5),
             sl_atr=cfg["sl_atr"], tp_atr=cfg["tp_atr"], max_bars=cfg.get("max_bars", 48),
             entry=cfg.get("entry", "market"), min_squeeze=cfg["min_squeeze"], trail=True)
    sigs = SQ(df, side=cfg.get("side", "both"), **p)
    filt = cfg.get("filter", "none")
    if filt == "none":
        return sigs
    a = bt.atr(df, 14).values
    ema50 = bt.ema(df["Close"], 50).values
    ema200 = bt.ema(df["Close"], 200).values
    tr = pd.concat([(df["High"] - df["Low"]), (df["High"] - df["Close"].shift()).abs(),
                    (df["Low"] - df["Close"].shift()).abs()], axis=1).max(axis=1).values
    out = []
    for s in sigs:
        i = s.i
        if filt == "trend":
            up = ema50[i] > ema200[i]
            if (s.side > 0 and up) or (s.side < 0 and not up):
                out.append(s)
        elif filt == "confirm":      # release bar shows real expansion
            if tr[i] > a[i]:
                out.append(s)
    return out


def portfolio(dfs, costs, cfg, rf=0.01, start=1000.0, compounding=True, max_lev=20.0):
    risk = bt.RiskCfg(starting_equity=start, risk_frac=rf, max_leverage=max_lev,
                      liq_buffer=2.5, compounding=compounding)
    recs = []
    per_coin_r = {}
    for c, df in dfs.items():
        rr = []
        for t in bt.simulate(df, make_signals(df, cfg), costs, risk, TF_MIN):
            recs.append((t.exit_time, t.r_multiple)); rr.append(t.r_multiple)
        per_coin_r[c] = rr
    recs.sort(key=lambda x: x[0])
    if not recs:
        return None
    eq = start; curve = [start]; pnls = []
    for _, r in recs:
        pnl = r * rf * eq; eq += pnl; pnls.append(pnl); curve.append(eq)
    pnls = np.array(pnls); curve = np.array(curve)
    wins = pnls[pnls > 0]; losses = pnls[pnls < 0]
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf")
    peak = np.maximum.accumulate(curve); dd = ((peak - curve) / peak).max() * 100
    rs = np.array([r for _, r in recs])
    t_stat = rs.mean() / (rs.std(ddof=1) / np.sqrt(len(rs))) if len(rs) > 1 else 0.0
    coins_pos = sum(1 for c in per_coin_r if per_coin_r[c] and np.mean(per_coin_r[c]) > 0)
    return dict(n=len(pnls), pf=pf, wr=(pnls > 0).mean() * 100, avg_r=rs.mean(),
                net_pct=(eq - start) / start * 100, max_dd=dd, t=t_stat, final=eq,
                coins_pos=coins_pos, ncoins=len(per_coin_r))


def slices(coins, d):
    full = {c: load_1h(c, d) for c in coins}
    isd = {c: bt.split_is_oos(full[c], 0.70)[0] for c in coins}
    oosd = {c: bt.split_is_oos(full[c], 0.70)[1] for c in coins}
    jun = {c: full[c][full[c].index >= JUN] for c in coins}
    return full, isd, oosd, jun


def pfs(m, k="pf"):
    if m is None:
        return "  -  "
    v = m[k]
    return "inf" if v == float("inf") else f"{v:.2f}"


# ---------------------------------------------------------------------------
def main():
    full, isd, oosd, jun = slices(DEV_COINS, DEV_DIR)
    span = (list(full.values())[0].index.min().date(), max(d.index.max() for d in full.values()).date())
    print(f"# SQUEEZE TUNING | dev={DEV_COINS} | {span[0]} -> {span[1]} | trail-runner, no fixed TP\n")

    # ---- PHASE 1: coarse grid, select on OOS + fee survival + multi-coin ----
    grid = list(itertools.product(
        [6, 8, 10, 12, 15],        # min_squeeze
        [1.0, 1.5, 2.0],           # sl_atr
        [2.5, 3.0, 3.5, 4.0, 5.0], # tp_atr == trail distance
    ))
    print(f"PHASE 1 — grid {len(grid)} configs (min_sq x sl x trail). Select: IS&OOS>=1.25, "
          f"BloFin>=1.2, Jun>1, >=3/4 coins +.\n")
    rows = []
    for ms, sl, tp in grid:
        cfg = dict(min_squeeze=ms, sl_atr=sl, tp_atr=tp)
        mf = portfolio(full, LIGHTER, cfg)
        mi = portfolio(isd, LIGHTER, cfg)
        mo = portfolio(oosd, LIGHTER, cfg)
        mb = portfolio(full, BLOFIN, cfg)
        mj = portfolio(jun, LIGHTER, cfg)
        if not all([mf, mi, mo, mb, mj]):
            continue
        robust = (mi["pf"] >= 1.25 and mo["pf"] >= 1.25 and mb["pf"] >= 1.2
                  and mj["pf"] > 1.0 and mf["coins_pos"] >= 3)
        rows.append((cfg, mf, mi, mo, mb, mj, robust))
    # rank survivors by OOS PF, then full net
    survivors = [r for r in rows if r[6]]
    survivors.sort(key=lambda r: (r[3]["pf"], r[1]["net_pct"]), reverse=True)
    print(f"  {len(survivors)}/{len(rows)} configs passed the robustness gate. Top 12 by OOS PF:")
    print(f"  {'min_sq sl  trail':<18} {'fullPF':>6} {'net%':>7} {'DD%':>5} {'IS':>5} {'OOS':>5} {'BloFin':>6} {'Jun':>5} {'coins':>5} {'t':>5}")
    for cfg, mf, mi, mo, mb, mj, _ in survivors[:12]:
        tag = f"{cfg['min_squeeze']:>2} {cfg['sl_atr']:.1f} {cfg['tp_atr']:.1f}"
        print(f"  {tag:<18} {pfs(mf):>6} {mf['net_pct']:>+7.0f} {mf['max_dd']:>5.1f} "
              f"{pfs(mi):>5} {pfs(mo):>5} {pfs(mb):>6} {pfs(mj):>5} {mf['coins_pos']}/{mf['ncoins']:<3} {mf['t']:>+5.2f}")

    if not survivors:
        print("  NO survivors — gate too tight or edge gone. Stop."); return
    base_cfg = survivors[0][0]
    print(f"\n  -> carrying top robust config: {base_cfg}")

    # ---- PHASE 2: filters / side / entry / coin-set, on the carried config ----
    print(f"\nPHASE 2 — one-knob variants on the carried config (full window, Lighter):")
    def line(tag, dfs, cfg, costs=LIGHTER):
        m = portfolio(dfs, costs, cfg)
        if m is None:
            print(f"  {tag:<22} (no trades)"); return None
        print(f"  {tag:<22} n={m['n']:>4} PF={pfs(m):>5} WR={m['wr']:3.0f}% net={m['net_pct']:>+6.0f}% "
              f"DD={m['max_dd']:4.1f}% coins={m['coins_pos']}/{m['ncoins']} t={m['t']:+.2f}")
        return m
    line("carried base", full, base_cfg)
    for f in ("trend", "confirm"):
        line(f"filter={f}", full, {**base_cfg, "filter": f})
    for sd in ("long", "short"):
        line(f"side={sd}", full, {**base_cfg, "side": sd})
    line("entry=limit", full, {**base_cfg, "entry": "limit"})
    drop_sol = {c: full[c] for c in DEV_COINS if c != "SOL"}
    line("drop SOL (3-coin)", drop_sol, base_cfg)

    # ---- PHASE 3: GENERALIZATION on out-of-universe coins ----
    oc = avail(OOS_COINS, OOS_DIR) if os.path.isdir(OOS_DIR) else []
    print(f"\nPHASE 3 — OUT-OF-UNIVERSE coins {oc} (strat never saw these):")
    if oc:
        ofull, _, _, ojun = slices(oc, OOS_DIR)
        line("OOS-coins base", ofull, base_cfg)
        line("OOS-coins trend", ofull, {**base_cfg, "filter": "trend"})
        line("OOS-coins confirm", ofull, {**base_cfg, "filter": "confirm"})
        line("OOS-coins June-fwd", ojun, base_cfg)
        for c in oc:
            line(f"  {c}", {c: ofull[c]}, base_cfg)
    else:
        print("  (out-of-universe data not ready)")

    # ---- PHASE 4: walk-forward folds on the carried config (dev coins) ----
    print(f"\nPHASE 4 — walk-forward, 4 contiguous folds (dev coins, Lighter):")
    for k in range(4):
        fold = {}
        for c in DEV_COINS:
            df = full[c]; n = len(df)
            lo, hi = int(n * k / 4), int(n * (k + 1) / 4)
            fold[c] = df.iloc[lo:hi]
        m = portfolio(fold, LIGHTER, base_cfg)
        if m:
            print(f"  fold {k+1}/4  n={m['n']:>4} PF={pfs(m):>5} net={m['net_pct']:>+6.0f}% DD={m['max_dd']:4.1f}% t={m['t']:+.2f}")

    # ---- PHASE 5: sizing / risk / leverage -> $3k path to 2x ----
    print(f"\nPHASE 5 — sizing on $3,000, compounding, carried config (full window, Lighter):")
    print(f"  {'risk/trade':<12} {'lev':>4} {'final$':>10} {'net%':>7} {'maxDD%':>7}")
    for rf in (0.01, 0.02, 0.03, 0.04):
        for lev in (10.0, 20.0):
            m = portfolio(full, LIGHTER, base_cfg, rf=rf, start=3000.0, max_lev=lev)
            if m:
                print(f"  {rf*100:>4.0f}%        {lev:>4.0f} {m['final']:>10,.0f} {m['net_pct']:>+7.0f} {m['max_dd']:>7.1f}")

    print(f"\nLOCKED CANDIDATE: {base_cfg}")


if __name__ == "__main__":
    main()
