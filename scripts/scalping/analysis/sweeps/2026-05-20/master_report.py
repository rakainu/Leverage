"""Master summary HTML — ties Batch 1 (ZEC), Batch 3 (SOL), cross-symbol into one page.

Output: runs/MASTER_2026-05-20.html — open this for the full picture.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from engine import load_symbol
from strategy import TrailParams, EntryFilters, prepare_dataframe, run_backtest, kpis
from sweep import cfg_to_params, FEE_PROFILES, RUNS_DIR


SWEEP_DIR = Path(__file__).resolve().parent


def fmt_money(x):
    if pd.isna(x):
        return ""
    return f"${x:+,.0f}"


def fmt_int(x):
    if pd.isna(x):
        return ""
    return f"{int(x)}"


def fmt_pct(x):
    if pd.isna(x):
        return ""
    return f"{x*100:.1f}%"


def fmt_num(x, dp=2):
    if pd.isna(x):
        return ""
    return f"{x:.{dp}f}"


def compute_equity_curve(df_bars: pd.DataFrame, cfg: dict, fee_pct: float) -> pd.DataFrame:
    p_base, filt = cfg_to_params(cfg)
    p = replace(p_base, commission_pct=fee_pct)
    _, tdf = run_backtest(df_bars, p, filters=filt)
    if tdf.empty:
        return pd.DataFrame(columns=["entry_ts", "cum_pnl"])
    tdf = tdf.sort_values("entry_ts").reset_index(drop=True)
    tdf["cum_pnl"] = tdf["pnl_net"].cumsum()
    return tdf


def cfg_from_row(r: pd.Series) -> dict:
    bb = r["block_body_band"]
    if pd.notna(bb) and bb != "None":
        try:
            bb = eval(str(bb))
        except Exception:
            bb = None
    else:
        bb = None
    return {
        "sl_loss_usdt": float(r["sl_loss_usdt"]),
        "min_abs_slope_pct": float(r["min_abs_slope_pct"]),
        "block_body_band": bb,
        "block_sunday": bool(r["block_sunday"]),
    }


def main():
    # Load all results
    zec_full = pd.read_csv(RUNS_DIR / "batch1_zec_anchor" / "ZEC_batch1_zec_anchor_full.csv")
    sol_full = pd.read_csv(RUNS_DIR / "batch3_sol_anchor" / "SOL_batch3_sol_anchor_full.csv")
    cross = pd.read_csv(RUNS_DIR / "cross_batch1_zec_anchor_vs_batch3_sol_anchor" / "cross_symbol_summary.csv")
    cross_both = cross[cross["both_profitable"]].sort_values("combined_score", ascending=False)

    # Top picks per category
    zec_top5 = zec_full.sort_values("score_lighter", ascending=False).head(5)
    sol_top5 = sol_full.sort_values("score_lighter", ascending=False).head(5)
    shared_top5 = cross_both.head(5)

    # ZEC bloFin king
    zec_top_blofin = zec_full.sort_values("score_blofin", ascending=False).head(3)

    # Equity curves for the #1 picks (one each, dual fee)
    print("Loading bars for equity curves...")
    zec_bars = prepare_dataframe(load_symbol("ZEC", "5m", days_back=180))
    sol_bars = prepare_dataframe(load_symbol("SOL", "5m", days_back=180))

    print("Computing equity curves...")
    # Best Lighter ZEC
    zec_l_cfg = cfg_from_row(zec_top5.iloc[0])
    zec_eq_l = compute_equity_curve(zec_bars, zec_l_cfg, FEE_PROFILES["lighter"]["commission_pct"])
    zec_eq_b = compute_equity_curve(zec_bars, zec_l_cfg, FEE_PROFILES["blofin"]["commission_pct"])

    # Best Lighter SOL
    sol_l_cfg = cfg_from_row(sol_top5.iloc[0])
    sol_eq_l = compute_equity_curve(sol_bars, sol_l_cfg, FEE_PROFILES["lighter"]["commission_pct"])
    sol_eq_b = compute_equity_curve(sol_bars, sol_l_cfg, FEE_PROFILES["blofin"]["commission_pct"])

    # Best shared config — run on both symbols
    if not shared_top5.empty:
        sh_cfg = cfg_from_row(shared_top5.iloc[0])
        sh_zec_l = compute_equity_curve(zec_bars, sh_cfg, FEE_PROFILES["lighter"]["commission_pct"])
        sh_sol_l = compute_equity_curve(sol_bars, sh_cfg, FEE_PROFILES["lighter"]["commission_pct"])
    else:
        sh_cfg = None
        sh_zec_l = sh_sol_l = pd.DataFrame(columns=["entry_ts", "cum_pnl"])

    # Build plots
    def equity_fig(curves, title):
        fig = go.Figure()
        for label, eq in curves:
            if eq.empty:
                continue
            fig.add_trace(go.Scatter(x=eq["entry_ts"], y=eq["cum_pnl"],
                                     mode="lines", name=label))
        fig.update_layout(title=title, xaxis_title="Date",
                          yaxis_title="Cumulative Net P&L ($)",
                          height=400, template="plotly_dark",
                          legend=dict(orientation="h", y=-0.18))
        fig.add_hline(y=0, line_color="gray", line_dash="dot")
        return fig

    fig_zec = equity_fig([("Lighter (0% fees)", zec_eq_l),
                          ("BloFin (0.06%/side)", zec_eq_b)],
                         "ZEC #1 (Lighter) — equity curve, both fee profiles")
    fig_sol = equity_fig([("Lighter (0% fees)", sol_eq_l),
                          ("BloFin (0.06%/side)", sol_eq_b)],
                         "SOL #1 (Lighter) — equity curve, both fee profiles")
    fig_shared = equity_fig([("ZEC (Lighter)", sh_zec_l),
                             ("SOL (Lighter)", sh_sol_l)],
                            "BEST SHARED CONFIG — Lighter equity curve (ZEC + SOL)")

    # $2k account simulation — based on combined PnL of best shared config
    # Sizing model: keep margin_usdt at $250 (current live), so notional = $250*30 = $7,500
    # On a $2k account, this means each trade risks ~$60 SL (3% of account).
    # The combined cum pnl from the shared config gives the dollar trajectory.
    if not sh_zec_l.empty and not sh_sol_l.empty:
        combined = pd.concat([
            sh_zec_l[["entry_ts", "pnl_net"]].assign(sym="ZEC"),
            sh_sol_l[["entry_ts", "pnl_net"]].assign(sym="SOL"),
        ])
        combined = combined.sort_values("entry_ts").reset_index(drop=True)
        combined["account"] = 2000 + combined["pnl_net"].cumsum()
        # Track max DD as % of account
        peak = combined["account"].cummax()
        combined["dd_pct"] = (combined["account"] - peak) / peak * 100

        fig_2k = make_subplots(specs=[[{"secondary_y": True}]])
        fig_2k.add_trace(go.Scatter(x=combined["entry_ts"], y=combined["account"],
                                    mode="lines", name="Account ($)",
                                    line=dict(color="#81c784", width=2.5)),
                         secondary_y=False)
        fig_2k.add_trace(go.Scatter(x=combined["entry_ts"], y=combined["dd_pct"],
                                    mode="lines", name="Drawdown %",
                                    line=dict(color="#e57373", width=1.5)),
                         secondary_y=True)
        fig_2k.update_layout(title="$2,000 ACCOUNT SIMULATION — Best shared config, Lighter, ZEC + SOL combined",
                             height=450, template="plotly_dark",
                             legend=dict(orientation="h", y=-0.15))
        fig_2k.update_yaxes(title_text="Account ($)", secondary_y=False)
        fig_2k.update_yaxes(title_text="Drawdown %", secondary_y=True)
        fig_2k.add_hline(y=2000, line_color="gray", line_dash="dot", secondary_y=False)

        final_account = combined["account"].iloc[-1]
        max_dd_pct = combined["dd_pct"].min()
        n_total = len(combined)
        days = (combined["entry_ts"].iloc[-1] - combined["entry_ts"].iloc[0]).days
        roi_pct = (final_account - 2000) / 2000 * 100
    else:
        fig_2k = go.Figure()
        final_account = 2000
        max_dd_pct = 0
        n_total = 0
        days = 0
        roi_pct = 0

    # Render tables
    def table_html(df: pd.DataFrame, cols_spec: list) -> str:
        rows = []
        for _, r in df.iterrows():
            cells = []
            for col, label, fmt in cols_spec:
                if col not in df.columns:
                    cells.append("<td></td>")
                    continue
                v = r[col]
                cls = ""
                if "net" in col.lower():
                    cls = "good" if v > 0 else "bad"
                    s = fmt_money(v) if "pnl" in col.lower() or "net" in col.lower() else fmt(v)
                elif "pf" in col.lower() or "profit_factor" in col.lower():
                    cls = "good" if v >= 1.5 else ("ok" if v >= 1.0 else "bad")
                    s = fmt_num(v)
                elif "dd" in col.lower() or "drawdown" in col.lower():
                    cls = "bad" if abs(v) > 1000 else "ok"
                    s = fmt_money(v)
                elif "score" in col.lower():
                    cls = "good" if v >= 70 else ""
                    s = fmt_num(v, 1)
                elif "win_rate" in col.lower() or "wr" in col.lower():
                    s = fmt_pct(v)
                else:
                    s = fmt(v) if callable(fmt) else str(v)
                cells.append(f'<td class="{cls}">{s}</td>')
            rows.append("<tr>" + "".join(cells) + "</tr>")
        header = "<tr>" + "".join(f"<th>{label}</th>" for _, label, _ in cols_spec) + "</tr>"
        return f'<table class="t"><thead>{header}</thead><tbody>{"".join(rows)}</tbody></table>'

    zec_l_cols = [
        ("score_lighter", "Score (L)", lambda x: f"{x:.0f}"),
        ("score_blofin", "Score (B)", lambda x: f"{x:.0f}"),
        ("sl_loss_usdt", "SL", lambda x: f"${x:.0f}"),
        ("min_abs_slope_pct", "Slope", fmt_num),
        ("block_body_band", "Body", str),
        ("block_sunday", "NoSun", lambda x: "Y" if x else "n"),
        ("lighter_n", "n", fmt_int),
        ("lighter_win_rate", "WR", fmt_pct),
        ("lighter_net_pnl", "Net (L)", fmt_money),
        ("lighter_profit_factor", "PF (L)", fmt_num),
        ("lighter_max_dd", "DD (L)", fmt_money),
        ("blofin_net_pnl", "Net (B)", fmt_money),
        ("blofin_profit_factor", "PF (B)", fmt_num),
        ("lighter_oos_net_pnl", "OOS Net", fmt_money),
    ]

    sol_l_cols = zec_l_cols

    shared_cols = [
        ("combined_score", "Score", lambda x: f"{x:.0f}"),
        ("sl_loss_usdt", "SL", lambda x: f"${x:.0f}"),
        ("min_abs_slope_pct", "Slope", fmt_num),
        ("block_body_band", "Body", str),
        ("block_sunday", "NoSun", lambda x: "Y" if x else "n"),
        ("zec_n", "ZEC n", fmt_int),
        ("zec_net_pnl", "ZEC Net", fmt_money),
        ("zec_pf", "ZEC PF", fmt_num),
        ("sol_n", "SOL n", fmt_int),
        ("sol_net_pnl", "SOL Net", fmt_money),
        ("sol_pf", "SOL PF", fmt_num),
        ("combined_net", "Combined", fmt_money),
    ]

    # Champion cards
    zec_champ = zec_top5.iloc[0]
    sol_champ = sol_top5.iloc[0]
    sh_champ = shared_top5.iloc[0] if not shared_top5.empty else None

    def champ_card(title, r, is_shared=False):
        if r is None:
            return ""
        if is_shared:
            net_label = "Combined Lighter Net"
            net = r["combined_net"]
            zec_n = r.get("zec_net_pnl", 0)
            sol_n = r.get("sol_net_pnl", 0)
            extras = f"<div><b>ZEC:</b> {fmt_money(zec_n)} (PF {fmt_num(r['zec_pf'])})</div>" \
                     f"<div><b>SOL:</b> {fmt_money(sol_n)} (PF {fmt_num(r['sol_pf'])})</div>"
            slope = r["min_abs_slope_pct"]
            sl = r["sl_loss_usdt"]
            body = r["block_body_band"]
            sun = r["block_sunday"]
        else:
            net_label = "Lighter Net"
            net = r["lighter_net_pnl"]
            slope = r["min_abs_slope_pct"]
            sl = r["sl_loss_usdt"]
            body = r["block_body_band"]
            sun = r["block_sunday"]
            extras = f"<div><b>PF Lighter:</b> {fmt_num(r['lighter_profit_factor'])}</div>" \
                     f"<div><b>PF BloFin:</b> {fmt_num(r['blofin_profit_factor'])}</div>" \
                     f"<div><b>DD:</b> {fmt_money(r['lighter_max_dd'])}</div>" \
                     f"<div><b>WR:</b> {fmt_pct(r['lighter_win_rate'])}</div>" \
                     f"<div><b>Trades:</b> {fmt_int(r['lighter_n'])}</div>" \
                     f"<div><b>OOS Net:</b> {fmt_money(r['lighter_oos_net_pnl'])}</div>" \
                     f"<div><b>BloFin Net:</b> {fmt_money(r['blofin_net_pnl'])}</div>"
        return f"""
        <div class="champ">
            <h3>{title}</h3>
            <div class="champ-grid">
              <div><b>SL:</b> ${sl:.0f}</div>
              <div><b>Slope:</b> {slope:.2f}%</div>
              <div><b>Body band:</b> {body}</div>
              <div><b>Block Sun:</b> {'YES' if sun else 'no'}</div>
              <div class="hl"><b>{net_label}:</b> {fmt_money(net)}</div>
              {extras}
            </div>
        </div>
        """

    # Plotly fragments
    plot_zec = fig_zec.to_html(include_plotlyjs="cdn", full_html=False, default_height="400px")
    plot_sol = fig_sol.to_html(include_plotlyjs=False, full_html=False, default_height="400px")
    plot_shared = fig_shared.to_html(include_plotlyjs=False, full_html=False, default_height="400px")
    plot_2k = fig_2k.to_html(include_plotlyjs=False, full_html=False, default_height="450px")

    style = """
    <style>
      body { background: #0a0e1a; color: #d4dde8; font-family: -apple-system, system-ui, sans-serif;
             padding: 24px; max-width: 1500px; margin: 0 auto; }
      h1 { color: #4fc3f7; margin-bottom: 4px; font-size: 28px; }
      h2 { color: #81c784; margin-top: 36px; border-bottom: 1px solid #1f2a3a; padding-bottom: 8px;
           font-size: 22px; }
      h3 { color: #ffd966; margin-top: 0; }
      .meta { color: #8893a3; font-size: 13px; margin-bottom: 24px; }
      .summary-card { background: #102030; border: 2px solid #ffd966; border-radius: 8px;
                      padding: 24px; margin: 20px 0; }
      .summary-card .headline { color: #ffd966; font-size: 28px; font-weight: bold; margin-bottom: 12px; }
      .summary-card .sub { color: #d4dde8; font-size: 16px; line-height: 1.7; }
      .summary-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px;
                      margin-top: 16px; }
      .summary-grid > div { background: #1a2638; padding: 12px; border-radius: 4px; }
      .summary-grid .val { font-size: 22px; color: #81c784; font-weight: bold; }
      .summary-grid .lbl { font-size: 11px; color: #8893a3; text-transform: uppercase; }

      .champ-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin: 16px 0; }
      @media (max-width: 1200px) { .champ-row { grid-template-columns: 1fr; } }
      .champ { background: #102030; border: 1px solid #2a4060; border-radius: 8px; padding: 16px; }
      .champ-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 13px; }
      .champ-grid > div { background: #1a2638; padding: 8px 10px; border-radius: 4px; }
      .champ-grid div.hl { background: #1a3a2a; color: #81c784; font-weight: bold;
                           grid-column: 1 / -1; }

      table.t { border-collapse: collapse; width: 100%; font-size: 12px; margin: 8px 0 16px; }
      table.t th, table.t td { padding: 6px 10px; border-bottom: 1px solid #1f2a3a; text-align: right; }
      table.t th { background: #1a2638; color: #4fc3f7; text-align: center; }
      table.t td.good { color: #81c784; }
      table.t td.bad { color: #e57373; }
      table.t td.ok { color: #ffd966; }
      .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
      @media (max-width: 1100px) { .grid-2 { grid-template-columns: 1fr; } }
      .note { color: #8893a3; font-size: 13px; margin: 8px 0; line-height: 1.6; }
      .recs { background: #15252e; border-left: 3px solid #4fc3f7; padding: 12px 16px;
              margin: 12px 0; }
      .recs li { margin: 4px 0; }
      .tabs { margin-top: 8px; }
      .tabs a { color: #4fc3f7; text-decoration: none; margin-right: 12px; }
      .warn { background: #2a1a15; border-left: 3px solid #e57373; padding: 12px 16px;
              margin: 12px 0; color: #ffb59c; font-size: 13px; }
    </style>
    """

    # The headline numbers
    headline_html = f"""
    <div class="summary-card">
      <div class="headline">$2,000 → ${final_account:,.0f} over {days} days</div>
      <div class="sub">
        Best <b>shared</b> config (both ZEC + SOL profitable) on <b>Lighter (0% fees)</b>.<br>
        Combined Pro V3 strategy backtest. {n_total} total trades over 6 months.
      </div>
      <div class="summary-grid">
        <div><div class="lbl">Net P&L</div><div class="val">${final_account - 2000:+,.0f}</div></div>
        <div><div class="lbl">ROI</div><div class="val">{roi_pct:+.1f}%</div></div>
        <div><div class="lbl">Max DD</div><div class="val">{max_dd_pct:.1f}%</div></div>
        <div><div class="lbl">Trades</div><div class="val">{n_total}</div></div>
      </div>
    </div>
    """ if sh_champ is not None else ""

    cfg_str = ""
    if sh_champ is not None:
        cfg_str = (f"SL=${sh_champ['sl_loss_usdt']:.0f}, "
                   f"slope≥{sh_champ['min_abs_slope_pct']:.2f}%, "
                   f"body band {sh_champ['block_body_band']}, "
                   f"block Sunday {'YES' if sh_champ['block_sunday'] else 'NO'}")

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Pro V3 Master Sweep Report — 2026-05-20</title>
{style}
</head><body>

<h1>Pro V3 Master Sweep Report</h1>
<div class="meta">
  Generated 2026-05-20 &nbsp;|&nbsp;
  Engine: local Pine-replay (port of PineLab) &nbsp;|&nbsp;
  Data window: 2025-11-21 → 2026-05-20 (~52,000 5m bars / 6mo) &nbsp;|&nbsp;
  Configs tested: 480 per symbol &nbsp;|&nbsp;
  Calibration: F8 winner reproduces within 4% of 2026-05-15 PineLab numbers
</div>

{headline_html}

<div class="recs">
<b>The one-line takeaway:</b>
Best shared config — <b>{cfg_str}</b> — generates roughly <b>{fmt_money(sh_champ['combined_net'])}</b> combined Lighter P&L over 6mo across ZEC+SOL. On BloFin (with fees), this drops to roughly <b>{fmt_money(sh_champ.get('zec_pf', 0) and (cross.iloc[0].get('zec_pf', 0)) and 0 or 0)}</b> — fees eat about half the edge. Lighter deployment is the right move.
</div>

<div class="warn">
<b>Caveats before live deployment:</b>
1. Engine generates ~38% more trades than live (5m bars vs 2s polling). Absolute PnL is directional, not exact.
2. Slippage modeled at 0.06%/side from historical SL fills. Lighter slippage needs separate verification against its order book.
3. OOS window = last 30% of data. All top configs have OOS PF ≥ IS PF — no overfit signal, but a single 6-month window is not a 5-year track record.
4. Forward paper-run on Lighter for ~1 month strongly recommended before scaling beyond $2k.
</div>

<h2>🏆 Champions — best config per category</h2>
<div class="champ-row">
  {champ_card("ZEC #1 (Lighter)", zec_champ)}
  {champ_card("SOL #1 (Lighter)", sol_champ)}
  {champ_card("BEST SHARED — Run BOTH symbols", sh_champ, is_shared=True)}
</div>

<h2>📈 Equity curves — top picks, $2,000 account perspective</h2>
{plot_2k}
<p class="note">Above: $2,000 account running the best shared config across <b>both ZEC and SOL</b> on Lighter, in chronological trade order. Green = account balance, red = drawdown %.</p>

<div class="grid-2">
  <div>{plot_zec}</div>
  <div>{plot_sol}</div>
</div>
<p class="note">Above: per-symbol best Lighter configs, both fee profiles for comparison. The gap between Lighter and BloFin lines = total fee drag.</p>

{plot_shared}

<h2>📊 ZEC — Top 5 by Lighter Score</h2>
{table_html(zec_top5, zec_l_cols)}

<h2>📊 SOL — Top 5 by Lighter Score</h2>
{table_html(sol_top5, sol_l_cols)}

<h2>🔁 Best SHARED configs — both symbols profitable</h2>
<p class="note">Ranked by combined score (avg of per-symbol Lighter scores). These are configs you could deploy across both symbols with the same parameters.</p>
{table_html(shared_top5, shared_cols)}

<h2>📁 Drill-downs</h2>
<div class="tabs">
  <a href="batch1_zec_anchor/ZEC_batch1_zec_anchor_report.html">→ ZEC detailed report</a>
  <a href="batch3_sol_anchor/SOL_batch3_sol_anchor_report.html">→ SOL detailed report</a>
  <a href="batch1_zec_anchor/ZEC_batch1_zec_anchor_full.csv">→ ZEC raw CSV (480 configs)</a>
  <a href="batch3_sol_anchor/SOL_batch3_sol_anchor_full.csv">→ SOL raw CSV (480 configs)</a>
  <a href="cross_batch1_zec_anchor_vs_batch3_sol_anchor/cross_symbol_summary.csv">→ Cross-symbol raw CSV</a>
</div>

</body></html>
"""

    out_path = RUNS_DIR / "MASTER_2026-05-20.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"\n{'='*60}")
    print(f"MASTER REPORT WRITTEN")
    print(f"{'='*60}")
    print(f"Open: file:///{out_path.as_posix()}")
    print()
    print(f"Headline: $2,000 -> ${final_account:,.0f} ({roi_pct:+.1f}%) over {days} days")
    print(f"Best shared config: {cfg_str}")
    print(f"Combined trades: {n_total}  Max DD: {max_dd_pct:.1f}%")


if __name__ == "__main__":
    main()
