"""Tune regime_mr for a THICKER per-trade edge (not a more conservative one).
Levers: z_entry (fade bigger extensions), tp_frac (ride further), limit_atr (0 =
MARKET entry, removes maker-fill dependency = the live risk), sl_atr, max_bars.

Honest dump: every config's per-trade thickness AND frequency AND robustness, so the
frequency/thickness tradeoff is visible. No conservative filtering — ranked, baseline
flagged. 'cushion' = actual WR minus breakeven WR (how far above water per trade)."""
from __future__ import annotations
import os, sys, itertools
import numpy as np
from common import load, portfolio, weeks_span, LIGHTER, bt, TF_MIN
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scalp_search_2026-05-30")))
from strat_lib import regime_mr  # noqa: E402

BASKET = ["SOL", "ETH", "ZEC", "HYPE", "BTC"]
BASE = dict(trend_len=200, slope_lb=20, z_period=30, z_entry=1.5, sl_atr=2.0,
            tp_frac=0.3, max_bars=12, limit_atr=0.25)
GRID = dict(z_entry=[1.5, 2.0, 2.5, 3.0], tp_frac=[0.3, 0.5, 0.7, 1.0],
            limit_atr=[0.0, 0.25], sl_atr=[2.0, 3.0], max_bars=[12, 24])


def rich_stats(per):
    rs = np.concatenate([[t.r_multiple for t in ts] for ts in per.values() if ts]) if any(per.values()) else np.array([])
    if len(rs) < 20:
        return None
    w = rs[rs > 0]; l = rs[rs < 0]
    aw = w.mean() if len(w) else 0.0
    al = l.mean() if len(l) else 0.0
    wr = len(w) / len(rs)
    be_wr = (-al) / (aw - al) if (aw - al) != 0 else 1.0   # breakeven win rate
    return dict(n=len(rs), wr=wr * 100, avg_r=rs.mean(), avg_win=aw, avg_loss=al,
                be_wr=be_wr * 100, cushion=(wr - be_wr) * 100,
                t=rs.mean() / (rs.std(ddof=1) / np.sqrt(len(rs))))


def main():
    dfs = {c: load(c) for c in BASKET}
    wk = weeks_span(dfs)
    print(f"# regime_mr THICKNESS tune | {BASKET} | ~{wk:.0f}wk | 15m Lighter 0-fee\n")
    keys = list(GRID)
    rows = []
    for vals in itertools.product(*[GRID[k] for k in keys]):
        cfg = dict(BASE); cfg.update(dict(zip(keys, vals)))
        per = {c: bt.simulate(df, regime_mr(df, side="both", **cfg), LIGHTER,
               bt.RiskCfg(1000.0, 0.01, max_leverage=10, liq_buffer=2.5, compounding=True),
               TF_MIN) for c, df in dfs.items()}
        st = rich_stats(per)
        if st is None:
            continue
        pm = portfolio(per)
        rows.append((cfg, st, pm))

    base_key = {k: BASE[k] for k in keys}
    def is_base(cfg):
        return all(cfg[k] == BASE[k] for k in keys)

    rows.sort(key=lambda r: r[1]["avg_r"], reverse=True)
    print("RANKED BY avgR (per-trade thickness). 'cush' = WR above breakeven. tpw = trades/wk")
    print(f"{'z':>4} {'tp':>4} {'lim':>4} {'sl':>4} {'mb':>3} | {'avgR':>7} {'PF':>5} {'WR':>4} "
          f"{'BE%':>4} {'cush':>5} {'t':>5} {'tpw':>6} {'net%':>7} {'maxDD':>6}")
    for cfg, st, pm in rows[:22]:
        tag = "  <BASE" if is_base(cfg) else ""
        print(f"{cfg['z_entry']:>4} {cfg['tp_frac']:>4} {cfg['limit_atr']:>4} {cfg['sl_atr']:>4} "
              f"{cfg['max_bars']:>3} | {st['avg_r']:+.4f} {pm['pf']:5.2f} {st['wr']:3.0f}% "
              f"{st['be_wr']:3.0f}% {st['cushion']:+4.0f} {st['t']:+.1f} {st['n']/wk:6.1f} "
              f"{pm['net_pct']:+7.0f} {pm['max_dd']:5.0f}%{tag}")

    print("\n--- BASELINE for reference ---")
    for cfg, st, pm in rows:
        if is_base(cfg):
            print(f"z={cfg['z_entry']} tp={cfg['tp_frac']} lim={cfg['limit_atr']} sl={cfg['sl_atr']} "
                  f"mb={cfg['max_bars']} | avgR={st['avg_r']:+.4f} PF={pm['pf']:.2f} WR={st['wr']:.0f}% "
                  f"BE={st['be_wr']:.0f}% cushion={st['cushion']:+.0f} t={st['t']:+.1f} "
                  f"tpw={st['n']/wk:.0f} net={pm['net_pct']:+.0f}% maxDD={pm['max_dd']:.0f}%")
            break


if __name__ == "__main__":
    main()
