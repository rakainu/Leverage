"""Timeframe survival probe — test if the locked config survives 3m and 15m.

Locked config (Batch 2 interior winner):
  Entry: slope >= 0.12, body band (0.3, 0.6), block Sunday
  Exits: SL=$80, BE=$15, lock_act=$25, lock_amt=$20,
         trail_act=$40, trail_start=$45, trail_dist=$15

Survival = still profitable, PF >= 1.5, and DD reasonable. If it survives,
the edge is TF-robust. If it dies on 3m or 15m, edge is 5m-specific.

Output:
  runs/tf_probe_2026-05-20.html  — visual report
  runs/tf_probe_summary.csv      — numeric summary
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

# Force UTF-8 stdout on Windows so unicode chars (HTML lives there too) don't crash console prints
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from engine import load_symbol
from strategy import TrailParams, EntryFilters, prepare_dataframe, run_backtest, kpis
from sweep import RUNS_DIR, FEE_PROFILES


LOCKED_CFG = {
    "min_abs_slope_pct": 0.12,
    "block_body_band": (0.3, 0.6),
    "block_sunday": True,
    "sl_loss_usdt": 80.0,
    "breakeven_usdt": 15.0,
    "lock_profit_activate_usdt": 25.0,
    "lock_profit_usdt": 20.0,
    "trail_activate_usdt": 40.0,
    "trail_start_usdt": 45.0,
    "trail_distance_usdt": 15.0,
}

SYMBOLS = ["ZEC", "SOL"]
TIMEFRAMES = ["3m", "5m", "15m"]


def build_params(fee_pct: float) -> tuple[TrailParams, EntryFilters]:
    p = TrailParams(
        sl_loss_usdt=LOCKED_CFG["sl_loss_usdt"],
        breakeven_usdt=LOCKED_CFG["breakeven_usdt"],
        lock_profit_activate_usdt=LOCKED_CFG["lock_profit_activate_usdt"],
        lock_profit_usdt=LOCKED_CFG["lock_profit_usdt"],
        trail_activate_usdt=LOCKED_CFG["trail_activate_usdt"],
        trail_start_usdt=LOCKED_CFG["trail_start_usdt"],
        trail_distance_usdt=LOCKED_CFG["trail_distance_usdt"],
        commission_pct=fee_pct,
    )
    f = EntryFilters(
        block_weekdays={6},
        min_abs_slope_pct=LOCKED_CFG["min_abs_slope_pct"],
        block_body_band=LOCKED_CFG["block_body_band"],
    )
    return p, f


def main():
    print("=" * 80)
    print("TIMEFRAME SURVIVAL PROBE — locked config across 3m / 5m / 15m")
    print("=" * 80)
    print(f"Locked config: {LOCKED_CFG}")
    print()

    rows = []
    equity_curves = {}   # (symbol, tf, fee) -> DataFrame

    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            print(f"\n>>> {sym} {tf}")
            df = load_symbol(sym, tf, days_back=180)
            df = prepare_dataframe(df)
            print(f"   bars={len(df)}  buy_sig={int(df['buy_sig'].sum())}  sell_sig={int(df['sell_sig'].sum())}")

            for fee_name, fee in FEE_PROFILES.items():
                p, filt = build_params(fee["commission_pct"])
                _, tdf = run_backtest(df, p, filters=filt)
                k = kpis(tdf)
                survived = k["net_pnl"] > 0 and k["profit_factor"] >= 1.5
                rows.append({
                    "symbol": sym, "timeframe": tf, "fee_profile": fee_name,
                    "n": k["n"],
                    "net_pnl": k["net_pnl"],
                    "profit_factor": k["profit_factor"],
                    "max_dd": k["max_dd"],
                    "win_rate": k["win_rate"],
                    "avg_trade": k["avg_trade"],
                    "largest_loss_streak": k["largest_loss_streak"],
                    "largest_loss_run_usdt": k["largest_loss_run_usdt"],
                    "survived": survived,
                })
                print(f"   {fee_name:>8}: n={k['n']:>4}  net=${k['net_pnl']:>+10,.0f}  PF={k['profit_factor']:>5.2f}  "
                      f"DD=${k['max_dd']:>+8,.0f}  WR={k['win_rate']*100:>5.1f}%  "
                      f"{'SURVIVED' if survived else 'FAILED'}")

                # Save equity curve for plot
                if not tdf.empty:
                    tdf = tdf.sort_values("entry_ts").reset_index(drop=True)
                    tdf["cum_pnl"] = tdf["pnl_net"].cumsum()
                    equity_curves[(sym, tf, fee_name)] = tdf[["entry_ts", "cum_pnl"]]

    df_out = pd.DataFrame(rows)
    out_csv = RUNS_DIR / "tf_probe_summary.csv"
    df_out.to_csv(out_csv, index=False)
    print(f"\nWrote {out_csv}")

    # Survival matrix
    print("\n" + "=" * 80)
    print("SURVIVAL MATRIX  (Lighter Net PnL / PF)")
    print("=" * 80)
    pivot = df_out[df_out["fee_profile"] == "lighter"].pivot_table(
        index="symbol", columns="timeframe", values="net_pnl"
    )
    print("\nLighter Net PnL:")
    print(pivot.to_string())
    pivot_pf = df_out[df_out["fee_profile"] == "lighter"].pivot_table(
        index="symbol", columns="timeframe", values="profit_factor"
    )
    print("\nLighter Profit Factor:")
    print(pivot_pf.to_string())

    # Build HTML
    style = """
    <style>
      body { background: #0a0e1a; color: #d4dde8; font-family: -apple-system, system-ui, sans-serif;
             padding: 24px; max-width: 1400px; margin: 0 auto; }
      h1 { color: #4fc3f7; margin-bottom: 4px; }
      h2 { color: #81c784; margin-top: 32px; border-bottom: 1px solid #1f2a3a; padding-bottom: 8px; }
      .verdict { padding: 20px; border-radius: 8px; margin: 16px 0; font-size: 18px; }
      .pass { background: #1a3a2a; border-left: 4px solid #81c784; color: #b9e4c2; }
      .fail { background: #3a1a1a; border-left: 4px solid #e57373; color: #ffb59c; }
      .mixed { background: #3a311a; border-left: 4px solid #ffd966; color: #ffe6a0; }
      table.t { border-collapse: collapse; width: 100%; font-size: 13px; margin: 12px 0; }
      table.t th, table.t td { padding: 8px 12px; border-bottom: 1px solid #1f2a3a; text-align: right; }
      table.t th { background: #1a2638; color: #4fc3f7; }
      table.t td.good { color: #81c784; }
      table.t td.bad { color: #e57373; }
      .config-card { background: #102030; padding: 16px; border-radius: 8px; margin: 12px 0; }
      .config-card code { color: #ffd966; }
      .note { color: #8893a3; font-size: 13px; margin: 8px 0; }
    </style>
    """

    # Survival verdict
    n_total = len(df_out[df_out["fee_profile"] == "lighter"])
    n_survived = int(df_out[df_out["fee_profile"] == "lighter"]["survived"].sum())
    if n_survived == n_total:
        verdict_cls = "pass"
        verdict_txt = (f"✓ <b>FULL SURVIVAL</b> — locked config is profitable with PF ≥ 1.5 "
                       f"across all {n_total} symbol × TF combos. Edge is timeframe-robust.")
    elif n_survived == 0:
        verdict_cls = "fail"
        verdict_txt = (f"✗ <b>FAILED ALL TFs</b> — locked config does not survive any TF. "
                       f"Something is wrong with the probe or the original sweep was overfit.")
    else:
        verdict_cls = "mixed"
        verdict_txt = (f"⚠ <b>PARTIAL SURVIVAL</b> — locked config survived {n_survived}/{n_total} TF probes. "
                       f"Edge is TF-dependent; check which TFs hold.")

    # Survival table
    rows_h = []
    for _, r in df_out.iterrows():
        cls = "good" if r["survived"] else "bad"
        rows_h.append(
            f"<tr><td>{r['symbol']}</td><td>{r['timeframe']}</td>"
            f"<td>{r['fee_profile']}</td><td>{int(r['n'])}</td>"
            f"<td class='{cls}'>${r['net_pnl']:+,.0f}</td>"
            f"<td class='{cls}'>{r['profit_factor']:.2f}</td>"
            f"<td>${r['max_dd']:+,.0f}</td>"
            f"<td>{r['win_rate']*100:.1f}%</td>"
            f"<td>{int(r['largest_loss_streak'])}</td>"
            f"<td>${r['largest_loss_run_usdt']:+,.0f}</td>"
            f"<td>{'✓' if r['survived'] else '✗'}</td></tr>"
        )
    table_html = (
        "<table class='t'><thead><tr>"
        "<th>Symbol</th><th>TF</th><th>Fee</th><th>n</th><th>Net</th><th>PF</th>"
        "<th>DD</th><th>WR</th><th>LossStreak</th><th>LossRun$</th><th>Survived</th>"
        "</tr></thead><tbody>" + "".join(rows_h) + "</tbody></table>"
    )

    # Equity curves by symbol (Lighter only — that's the deployment target)
    plots_html = []
    for sym in SYMBOLS:
        fig = go.Figure()
        for tf in TIMEFRAMES:
            key = (sym, tf, "lighter")
            if key not in equity_curves:
                continue
            eq = equity_curves[key]
            fig.add_trace(go.Scatter(x=eq["entry_ts"], y=eq["cum_pnl"],
                                     mode="lines", name=f"{tf}"))
        fig.update_layout(title=f"{sym} — equity curves by TF (Lighter)",
                          xaxis_title="Date", yaxis_title="Cumulative Net P&L ($)",
                          height=400, template="plotly_dark",
                          legend=dict(orientation="h", y=-0.2))
        fig.add_hline(y=0, line_color="gray", line_dash="dot")
        first = len(plots_html) == 0
        plots_html.append(fig.to_html(include_plotlyjs="cdn" if first else False,
                                      full_html=False, default_height="400px"))

    cfg_pretty = ", ".join(f"{k}={v}" for k, v in LOCKED_CFG.items())
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>TF Survival Probe</title>{style}</head><body>
<h1>Timeframe Survival Probe — Pro V3 Locked Config</h1>
<div class="note">Generated 2026-05-20. Tests whether the locked config survives 3m / 5m / 15m on both ZEC and SOL.</div>

<div class="config-card">
<b>Locked config (from Batch 2 interior winner):</b><br><code>{cfg_pretty}</code>
</div>

<div class="verdict {verdict_cls}">{verdict_txt}</div>

<h2>Survival matrix</h2>
{table_html}
<p class="note">Survival = net_pnl > 0 AND PF ≥ 1.5. "Lighter" = 0% fees (deployment target). "BloFin" = 0.06%/side taker.</p>

<h2>Equity curves by symbol</h2>
{plots_html[0]}
{plots_html[1]}

</body></html>
"""
    out_html = RUNS_DIR / "tf_probe_2026-05-20.html"
    out_html.write_text(html, encoding="utf-8")
    print(f"\nWrote {out_html}")
    print(f"Open: file:///{out_html.as_posix()}")


if __name__ == "__main__":
    main()
