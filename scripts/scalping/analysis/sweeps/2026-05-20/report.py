"""HTML report builder for sweep results.

Generates a single self-contained HTML page per batch:
  - Top-N table (sortable by Lighter / BloFin score)
  - Equity curves for top-5 configs (both fee profiles)
  - Parameter heatmaps (SL x slope, etc.)
  - Neighborhood stability visualization
  - BloFin vs Lighter side-by-side rankings

Output: runs/<batch>/<symbol>_<batch>_report.html
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from engine import load_symbol
from strategy import (
    TrailParams, EntryFilters, prepare_dataframe, run_backtest, kpis,
)
from sweep import cfg_to_params, FEE_PROFILES, RUNS_DIR


# ---------- Helpers ----------

def fmt_money(x):
    if pd.isna(x): return ""
    return f"${x:+,.0f}"


def fmt_pct(x):
    if pd.isna(x): return ""
    return f"{x*100:.1f}%"


def neighbor_stability(df: pd.DataFrame, row: pd.Series, knobs: list[str],
                       fee_prefix: str = "lighter_") -> dict:
    """Find configs that are ±1 step away on each knob and compute their avg PF."""
    neighbors = []
    for _, r in df.iterrows():
        dist = 0
        ok = True
        for k in knobs:
            if k not in df.columns or k not in row.index:
                continue
            a, b = r[k], row[k]
            if pd.isna(a) or pd.isna(b):
                continue
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                if a != b:
                    dist += 1
            else:
                if str(a) != str(b):
                    dist += 1
        if dist == 1:
            neighbors.append(r)
    if not neighbors:
        return {"n_neighbors": 0, "neighbor_avg_pf": 0.0, "neighbor_avg_net": 0.0}
    pfs = [n[fee_prefix + "profit_factor"] for n in neighbors if not pd.isna(n[fee_prefix + "profit_factor"])]
    nets = [n[fee_prefix + "net_pnl"] for n in neighbors if not pd.isna(n[fee_prefix + "net_pnl"])]
    return {
        "n_neighbors": len(neighbors),
        "neighbor_avg_pf": round(float(np.mean(pfs)), 3) if pfs else 0,
        "neighbor_avg_net": round(float(np.mean(nets)), 2) if nets else 0,
    }


def compute_equity_curve(df_bars: pd.DataFrame, cfg: dict, fee_pct: float) -> pd.DataFrame:
    """Re-run a single config to extract trades, return cum equity time series."""
    p_base, filt = cfg_to_params(cfg)
    p = replace(p_base, commission_pct=fee_pct)
    _, tdf = run_backtest(df_bars, p, filters=filt)
    if tdf.empty:
        return pd.DataFrame(columns=["entry_ts", "cum_pnl"])
    tdf = tdf.sort_values("entry_ts")
    tdf["cum_pnl"] = tdf["pnl_net"].cumsum()
    return tdf[["entry_ts", "cum_pnl", "pnl_net", "side", "exit_reason"]]


# ---------- Report sections ----------

def top_table_html(df_top: pd.DataFrame) -> str:
    """Pretty top-N table with conditional formatting."""
    cols_show = [
        ("score_lighter", "Score (L)"),
        ("score_blofin", "Score (B)"),
        ("sl_loss_usdt", "SL$"),
        ("min_abs_slope_pct", "Slope"),
        ("block_body_band", "BodyBand"),
        ("block_sunday", "NoSun"),
        ("lighter_n", "n"),
        ("lighter_net_pnl", "Net (L)"),
        ("lighter_profit_factor", "PF (L)"),
        ("lighter_max_dd", "DD (L)"),
        ("lighter_win_rate", "WR (L)"),
        ("lighter_oos_net_pnl", "OOS Net"),
        ("lighter_oos_pf", "OOS PF"),
        ("blofin_net_pnl", "Net (B)"),
        ("blofin_profit_factor", "PF (B)"),
    ]
    cols = [c for c, _ in cols_show if c in df_top.columns]
    headers = [lbl for c, lbl in cols_show if c in df_top.columns]

    rows_html = []
    for _, r in df_top.iterrows():
        cells = []
        for c, lbl in cols_show:
            if c not in df_top.columns:
                continue
            v = r[c]
            cls = ""
            if c == "lighter_net_pnl" or c == "blofin_net_pnl" or c == "lighter_oos_net_pnl":
                cls = "good" if v > 0 else "bad"
                vs = fmt_money(v)
            elif c == "lighter_max_dd":
                cls = "bad" if abs(v) > 1000 else "ok"
                vs = fmt_money(v)
            elif c == "lighter_profit_factor" or c == "blofin_profit_factor" or c == "lighter_oos_pf":
                cls = "good" if v >= 1.5 else ("ok" if v >= 1.0 else "bad")
                vs = f"{v:.2f}" if not pd.isna(v) else ""
            elif c == "lighter_win_rate":
                vs = fmt_pct(v)
            elif c == "block_sunday":
                vs = "Y" if v else "N"
            elif c == "min_abs_slope_pct":
                vs = f"{v:.2f}"
            elif c == "sl_loss_usdt":
                vs = f"${v:.0f}"
            elif c in ("score_lighter", "score_blofin"):
                cls = "good" if v >= 70 else ("ok" if v >= 50 else "")
                vs = f"{v:.0f}"
            elif c == "lighter_n":
                vs = f"{int(v)}"
            else:
                vs = str(v)
            cells.append(f'<td class="{cls}">{vs}</td>')
        rows_html.append("<tr>" + "".join(cells) + "</tr>")

    header_html = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    return f'<table class="topt"><thead>{header_html}</thead><tbody>{"".join(rows_html)}</tbody></table>'


def heatmap_fig(df: pd.DataFrame, x: str, y: str, z: str = "score_lighter",
                title: str = "") -> go.Figure:
    pivot = df.pivot_table(index=y, columns=x, values=z, aggfunc="mean")
    fig = go.Figure(data=go.Heatmap(
        z=pivot.values, x=pivot.columns, y=pivot.index,
        colorscale="RdYlGn", colorbar=dict(title=z),
    ))
    fig.update_layout(
        title=title or f"{z} by {x} × {y}",
        xaxis_title=x, yaxis_title=y,
        height=400, template="plotly_dark",
    )
    return fig


def equity_curve_fig(curves: list[tuple[str, pd.DataFrame]], title: str) -> go.Figure:
    fig = go.Figure()
    for label, eq in curves:
        if eq.empty:
            continue
        fig.add_trace(go.Scatter(
            x=eq["entry_ts"], y=eq["cum_pnl"], mode="lines", name=label,
        ))
    fig.update_layout(
        title=title, xaxis_title="Date", yaxis_title="Cumulative Net P&L ($)",
        height=450, template="plotly_dark",
        legend=dict(orientation="h", y=-0.2),
    )
    fig.add_hline(y=0, line_color="gray", line_dash="dot")
    return fig


# ---------- Main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--days", type=int, default=180)
    args = ap.parse_args()

    out_dir = RUNS_DIR / args.batch
    full_csv = out_dir / f"{args.symbol}_{args.batch}_full.csv"
    if not full_csv.exists():
        raise SystemExit(f"missing {full_csv} — run sweep.py first")

    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    df = pd.read_csv(full_csv)
    print(f"Loaded {len(df)} configs from {full_csv.name}")

    # Top-25 by Lighter score
    df_top = df.sort_values("score_lighter", ascending=False).head(25)
    df_top10 = df_top.head(10)

    # Reload bars to compute equity curves for top-5
    print("Loading bars for equity curves...")
    bars = load_symbol(args.symbol, "5m", days_back=args.days)
    bars = prepare_dataframe(bars)

    print("Computing top-5 equity curves...")
    curves_l = []
    curves_b = []
    for i, (_, r) in enumerate(df_top10.head(5).iterrows()):
        cfg = {
            "sl_loss_usdt": r["sl_loss_usdt"],
            "min_abs_slope_pct": r["min_abs_slope_pct"],
            "block_body_band": eval(str(r["block_body_band"])) if pd.notna(r["block_body_band"]) and r["block_body_band"] != "None" else None,
            "block_sunday": bool(r["block_sunday"]),
        }
        label = (f"#{i+1}  SL${cfg['sl_loss_usdt']:.0f} slope{cfg['min_abs_slope_pct']:.2f}"
                 f" body{cfg['block_body_band']} {'noSun' if cfg['block_sunday'] else 'allDays'}")
        eq_l = compute_equity_curve(bars, cfg, FEE_PROFILES["lighter"]["commission_pct"])
        eq_b = compute_equity_curve(bars, cfg, FEE_PROFILES["blofin"]["commission_pct"])
        curves_l.append((label, eq_l))
        curves_b.append((label, eq_b))

    # Neighborhood stability for the #1 config
    knobs = ["sl_loss_usdt", "min_abs_slope_pct", "block_body_band", "block_sunday"]
    nb = neighbor_stability(df, df_top10.iloc[0], knobs, "lighter_")
    print(f"Neighborhood: {nb}")

    # Build heatmaps
    print("Building heatmaps...")
    hm_sl_slope = heatmap_fig(df, "sl_loss_usdt", "min_abs_slope_pct",
                              "score_lighter", "Score (Lighter) by SL × Slope gate")
    hm_sl_slope_pnl = heatmap_fig(df, "sl_loss_usdt", "min_abs_slope_pct",
                                  "lighter_net_pnl", "Net PnL (Lighter) by SL × Slope")

    # Build equity figures
    fig_eq_lighter = equity_curve_fig(curves_l, "Top-5 Equity Curves (Lighter, 0% fees)")
    fig_eq_blofin = equity_curve_fig(curves_b, "Top-5 Equity Curves (BloFin, 0.06%/side)")

    # Plotly HTML fragments
    def to_html(fig):
        return fig.to_html(include_plotlyjs="cdn" if not getattr(to_html, "loaded", False) else False,
                          full_html=False, default_height="450px")

    plot1 = fig_eq_lighter.to_html(include_plotlyjs="cdn", full_html=False, default_height="450px")
    plot2 = fig_eq_blofin.to_html(include_plotlyjs=False, full_html=False, default_height="450px")
    plot3 = hm_sl_slope.to_html(include_plotlyjs=False, full_html=False, default_height="400px")
    plot4 = hm_sl_slope_pnl.to_html(include_plotlyjs=False, full_html=False, default_height="400px")

    # Champion summary card
    champ = df_top10.iloc[0]
    champ_card = f"""
    <div class="champion">
        <h2>🏆 #1 — Lighter Score {champ['score_lighter']:.0f}</h2>
        <div class="champ-grid">
            <div><b>SL:</b> ${champ['sl_loss_usdt']:.0f}</div>
            <div><b>Slope gate:</b> {champ['min_abs_slope_pct']:.2f}%</div>
            <div><b>Body band:</b> {champ['block_body_band']}</div>
            <div><b>Block Sunday:</b> {'YES' if champ['block_sunday'] else 'no'}</div>
            <div><b>Trades:</b> {int(champ['lighter_n'])}</div>
            <div><b>Win rate:</b> {champ['lighter_win_rate']*100:.1f}%</div>
            <div><b>Net (Lighter):</b> ${champ['lighter_net_pnl']:+,.0f}</div>
            <div><b>Net (BloFin):</b> ${champ['blofin_net_pnl']:+,.0f}</div>
            <div><b>PF (Lighter):</b> {champ['lighter_profit_factor']:.2f}</div>
            <div><b>PF (BloFin):</b> {champ['blofin_profit_factor']:.2f}</div>
            <div><b>Max DD:</b> ${champ['lighter_max_dd']:+,.0f}</div>
            <div><b>OOS Net:</b> ${champ['lighter_oos_net_pnl']:+,.0f}</div>
        </div>
        <div class="champ-neighbors">
            Neighborhood (±1 step on any knob): {nb['n_neighbors']} configs, avg PF {nb['neighbor_avg_pf']}
        </div>
    </div>
    """

    # Build full HTML
    style = """
    <style>
      body { background: #0a0e1a; color: #d4dde8; font-family: -apple-system, system-ui, sans-serif;
             padding: 24px; max-width: 1400px; margin: 0 auto; }
      h1 { color: #4fc3f7; margin-bottom: 4px; }
      h2 { color: #81c784; margin-top: 32px; border-bottom: 1px solid #1f2a3a; padding-bottom: 8px; }
      .meta { color: #8893a3; font-size: 13px; margin-bottom: 24px; }
      .champion { background: #102030; border: 1px solid #2a4060; border-radius: 8px;
                  padding: 20px; margin: 16px 0; }
      .champion h2 { border: none; color: #ffd966; margin-top: 0; }
      .champ-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 16px 0; }
      .champ-grid div { background: #1a2638; padding: 10px; border-radius: 4px; }
      .champ-neighbors { color: #8893a3; font-size: 13px; margin-top: 16px; }
      table.topt { border-collapse: collapse; width: 100%; font-size: 12px; margin: 12px 0; }
      table.topt th, table.topt td { padding: 6px 10px; border-bottom: 1px solid #1f2a3a; text-align: right; }
      table.topt th { background: #1a2638; color: #4fc3f7; text-align: center; position: sticky; top: 0; }
      table.topt td.good { color: #81c784; }
      table.topt td.bad { color: #e57373; }
      table.topt td.ok { color: #ffd966; }
      .section { margin: 32px 0; }
      .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
      @media (max-width: 1100px) { .grid-2 { grid-template-columns: 1fr; } }
      .note { color: #8893a3; font-size: 13px; margin-top: 8px; }
    </style>
    """

    meta_html = f"""
    <div class="meta">
        Batch: <b>{args.batch}</b> &nbsp;|&nbsp;
        Symbol: <b>{args.symbol}</b> &nbsp;|&nbsp;
        Window: {manifest.get('data_window', ['', ''])[0][:10]} → {manifest.get('data_window', ['', ''])[1][:10]} &nbsp;|&nbsp;
        Bars: {manifest.get('bars', 'N/A')} &nbsp;|&nbsp;
        Configs: {manifest.get('n_configs', len(df))} &nbsp;|&nbsp;
        Runtime: {manifest.get('run_seconds', 'N/A')}s
    </div>
    """

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>{args.batch} — {args.symbol}</title>
{style}
</head><body>
<h1>Pro V3 Sweep Report — {args.symbol}</h1>
{meta_html}

{champ_card}

<div class="section">
<h2>Top 25 Configs (sorted by Lighter score)</h2>
<p class="note">Score is composite 0-100: profitability (25), PF (20), DD control (20), trade count (15), OOS survival (20). "(L)" = Lighter (0% fees); "(B)" = BloFin (0.06% taker).</p>
{top_table_html(df_top)}
</div>

<div class="section">
<h2>Equity Curves — Top 5</h2>
<div class="grid-2">
  <div>{plot1}</div>
  <div>{plot2}</div>
</div>
<p class="note">Same configs, two fee assumptions. Lighter (zero fees) is the deployment target.</p>
</div>

<div class="section">
<h2>Parameter Heatmaps — SL × Slope gate</h2>
<div class="grid-2">
  <div>{plot3}</div>
  <div>{plot4}</div>
</div>
<p class="note">Mean over body-band and Sunday-block variants. Hot zones = robust regions, not single-cell peaks.</p>
</div>

</body></html>
"""

    out_html = out_dir / f"{args.symbol}_{args.batch}_report.html"
    out_html.write_text(html, encoding="utf-8")
    print(f"\nWrote {out_html}")
    print(f"Open: file:///{out_html.as_posix()}")


if __name__ == "__main__":
    main()
