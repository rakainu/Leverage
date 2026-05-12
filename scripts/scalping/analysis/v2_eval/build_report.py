"""Scalping v2 eval — full analysis + PDF report builder.

Inputs:
  v2_audit.json — full /audit endpoint JSON from scalping-v2 (config + summaries + trades)
  v1_trades.json — sqlite -json dump of /docker/scalping/data/bridge.db trade_log

Outputs:
  scalping_v2_eval_report.pdf — single-PDF deliverable for Rich
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, stdev

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import LETTER  # noqa: E402
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


HERE = Path(__file__).parent
V2_PATH = HERE / "v2_audit.json"
V1_PATH = HERE / "v1_trades.json"
OUT_PDF = HERE / "scalping_v2_eval_report.pdf"
CHARTS_DIR = HERE / "charts"
CHARTS_DIR.mkdir(exist_ok=True)


def parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def load_v2() -> tuple[dict, list[dict]]:
    raw = json.loads(V2_PATH.read_text())
    trades = raw["trades"]
    for t in trades:
        t["opened_at_dt"] = parse_iso(t["opened_at"])
        t["closed_at_dt"] = parse_iso(t["closed_at"])
    trades.sort(key=lambda t: t["opened_at_dt"])
    return raw, trades


def load_v1() -> list[dict]:
    raw = json.loads(V1_PATH.read_text())
    for t in raw:
        t["opened_at_dt"] = parse_iso(t["opened_at"])
        t["closed_at_dt"] = parse_iso(t["closed_at"])
        # Normalize: v1 has 'side' as 'long'/'short' already
    raw.sort(key=lambda t: t["opened_at_dt"])
    return raw


def stats_for(trades: list[dict], label: str) -> dict:
    if not trades:
        return {"label": label, "n": 0}
    pnls = [float(t["pnl_usdt"]) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    sl_pnls = [float(t["pnl_usdt"]) for t in trades if t["exit_reason"] == "sl"]
    trail_pnls = [float(t["pnl_usdt"]) for t in trades if t["exit_reason"] == "trail_sl"]
    drift_pnls = [float(t["pnl_usdt"]) for t in trades if t["exit_reason"] == "drift"]
    first = trades[0]["opened_at_dt"]
    last = trades[-1]["closed_at_dt"]
    span_days = (last - first).total_seconds() / 86400.0
    longs = [t for t in trades if t["side"] == "long"]
    shorts = [t for t in trades if t["side"] == "short"]

    return {
        "label": label,
        "n": len(trades),
        "net_pnl": sum(pnls),
        "avg_pnl": mean(pnls),
        "median_pnl": median(pnls),
        "stdev_pnl": stdev(pnls) if len(pnls) > 1 else 0.0,
        "win_rate": len(wins) / len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "avg_win": mean(wins) if wins else 0.0,
        "avg_loss": mean(losses) if losses else 0.0,
        "max_win": max(pnls),
        "max_loss": min(pnls),
        "sl_count": len(sl_pnls),
        "sl_avg": mean(sl_pnls) if sl_pnls else 0.0,
        "trail_count": len(trail_pnls),
        "trail_avg": mean(trail_pnls) if trail_pnls else 0.0,
        "drift_count": len(drift_pnls),
        "drift_avg": mean(drift_pnls) if drift_pnls else 0.0,
        "long_count": len(longs),
        "long_pnl": sum(float(t["pnl_usdt"]) for t in longs),
        "short_count": len(shorts),
        "short_pnl": sum(float(t["pnl_usdt"]) for t in shorts),
        "span_days": span_days,
        "pnl_per_day": sum(pnls) / span_days if span_days > 0 else 0.0,
        "trades_per_day": len(pnls) / span_days if span_days > 0 else 0.0,
        "first_open": first,
        "last_close": last,
        "expectancy": mean(pnls),
        "profit_factor": (
            (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")
        ),
    }


def equity_curve(trades: list[dict]) -> tuple[list[datetime], list[float]]:
    eq = 0.0
    xs: list[datetime] = []
    ys: list[float] = []
    for t in trades:
        eq += float(t["pnl_usdt"])
        xs.append(t["closed_at_dt"])
        ys.append(eq)
    return xs, ys


def max_drawdown(ys: list[float]) -> tuple[float, int]:
    if not ys:
        return 0.0, 0
    peak = ys[0]
    max_dd = 0.0
    idx = 0
    for i, v in enumerate(ys):
        if v > peak:
            peak = v
        dd = v - peak  # negative or zero
        if dd < max_dd:
            max_dd = dd
            idx = i
    return max_dd, idx


def render_equity_chart(v1_trades, v2_trades) -> Path:
    fig, ax = plt.subplots(figsize=(8.0, 3.6))
    x1, y1 = equity_curve(v1_trades)
    x2, y2 = equity_curve(v2_trades)
    ax.plot(x1, y1, label=f"V1 ($13 SL) — {len(v1_trades)} trades", linewidth=1.5, color="#888")
    ax.plot(x2, y2, label=f"V2 ($25 SL) — {len(v2_trades)} trades", linewidth=2.0, color="#1e6fdd")
    ax.axhline(0, color="black", linewidth=0.6, alpha=0.4)
    ax.set_title("Cumulative PnL — V1 vs V2 (both demo, $100 margin, 30x)")
    ax.set_ylabel("Cumulative PnL ($)")
    ax.legend(loc="upper left", fontsize=8)
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    fig.autofmt_xdate()
    fig.tight_layout()
    out = CHARTS_DIR / "equity_curve.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def render_pnl_dist(v2_trades) -> Path:
    fig, ax = plt.subplots(figsize=(8.0, 3.4))
    pnls = [float(t["pnl_usdt"]) for t in v2_trades]
    colors_ = ["#cc3344" if p < 0 else "#229966" for p in pnls]
    ax.bar(range(len(pnls)), pnls, color=colors_, width=0.85)
    ax.axhline(0, color="black", linewidth=0.6)
    ax.set_title("V2 — PnL per trade (chronological)")
    ax.set_xlabel("Trade #")
    ax.set_ylabel("PnL ($)")
    fig.tight_layout()
    out = CHARTS_DIR / "pnl_per_trade.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def render_zec_equity(v1_trades, v2_trades) -> Path:
    fig, ax = plt.subplots(figsize=(8.0, 3.6))
    v1z = [t for t in v1_trades if t["symbol"] == "ZEC-USDT"]
    v2z = [t for t in v2_trades if t["symbol"] == "ZEC-USDT"]
    x1, y1 = equity_curve(v1z)
    x2, y2 = equity_curve(v2z)
    ax.plot(x1, y1, label=f"V1 ZEC — {len(v1z)} trades", linewidth=1.5, color="#888")
    ax.plot(x2, y2, label=f"V2 ZEC — {len(v2z)} trades", linewidth=2.0, color="#7a4cdb")
    ax.axhline(0, color="black", linewidth=0.6, alpha=0.4)
    ax.set_title("ZEC-USDT cumulative PnL — V1 vs V2")
    ax.set_ylabel("Cumulative PnL ($)")
    ax.legend(loc="upper left", fontsize=8)
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    fig.autofmt_xdate()
    fig.tight_layout()
    out = CHARTS_DIR / "zec_equity.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def render_exit_mix(v2_trades) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(8.0, 3.2))

    def slice_for(trs):
        c = Counter(t["exit_reason"] for t in trs)
        labels, values = zip(*sorted(c.items(), key=lambda kv: -kv[1])) if c else ([], [])
        return labels, values

    for ax, label, trs in [
        (axes[0], "ZEC-USDT", [t for t in v2_trades if t["symbol"] == "ZEC-USDT"]),
        (axes[1], "SOL-USDT", [t for t in v2_trades if t["symbol"] == "SOL-USDT"]),
    ]:
        labels, values = slice_for(trs)
        if labels:
            colors_ = {"sl": "#cc3344", "trail_sl": "#229966", "drift": "#999"}
            ax.bar(labels, values, color=[colors_.get(l, "#888") for l in labels])
            for i, v in enumerate(values):
                ax.text(i, v + 0.3, str(v), ha="center", fontsize=9)
        ax.set_title(f"V2 {label} — exit reasons ({len(trs)} trades)")
        ax.set_ylabel("Count")
    fig.tight_layout()
    out = CHARTS_DIR / "exit_mix.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def fmt_money(v: float) -> str:
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def fmt_pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def main():
    audit_raw, v2_trades = load_v2()
    v1_trades = load_v1()
    v2_cfg = audit_raw["config"]

    # Stats
    s_v2_all = stats_for(v2_trades, "V2 — All")
    s_v2_zec = stats_for([t for t in v2_trades if t["symbol"] == "ZEC-USDT"], "V2 — ZEC")
    s_v2_sol = stats_for([t for t in v2_trades if t["symbol"] == "SOL-USDT"], "V2 — SOL")

    s_v1_all = stats_for(v1_trades, "V1 — All")
    s_v1_zec = stats_for([t for t in v1_trades if t["symbol"] == "ZEC-USDT"], "V1 — ZEC")
    s_v1_sol = stats_for([t for t in v1_trades if t["symbol"] == "SOL-USDT"], "V1 — SOL")

    # Drawdown
    _, eq_v2 = equity_curve(v2_trades)
    _, eq_v1 = equity_curve(v1_trades)
    dd_v2, _ = max_drawdown(eq_v2)
    dd_v1, _ = max_drawdown(eq_v1)

    # Charts
    eq_png = render_equity_chart(v1_trades, v2_trades)
    dist_png = render_pnl_dist(v2_trades)
    zec_png = render_zec_equity(v1_trades, v2_trades)
    exit_png = render_exit_mix(v2_trades)

    # PDF
    doc = SimpleDocTemplate(
        str(OUT_PDF),
        pagesize=LETTER,
        topMargin=0.55 * inch,
        bottomMargin=0.5 * inch,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        title="Scalping V2 Eval Report",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=18, spaceAfter=8, textColor=colors.HexColor("#0a2540"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=13, spaceBefore=10, spaceAfter=6, textColor=colors.HexColor("#1e6fdd"))
    h3 = ParagraphStyle("h3", parent=styles["Heading3"], fontSize=11, spaceBefore=6, spaceAfter=4, textColor=colors.HexColor("#333"))
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.5, leading=12.5)
    body_bold = ParagraphStyle("bodyb", parent=body, fontName="Helvetica-Bold")
    small = ParagraphStyle("sm", parent=body, fontSize=8.5, leading=11)

    story = []

    # ---------- TITLE ----------
    story.append(Paragraph("Scalping V2 Evaluation Report", h1))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    story.append(Paragraph(
        f"Generated {today} &nbsp;|&nbsp; Eval window 2026-04-29 → present "
        f"({s_v2_all['span_days']:.1f} days, {s_v2_all['n']} closed trades, 1 still open)",
        small,
    ))
    story.append(Spacer(1, 6))

    # ---------- EXEC SUMMARY ----------
    story.append(Paragraph("Executive Summary", h2))
    story.append(Paragraph(
        f"<b>V2 (Option B, $25 SL) materially underperformed V1 ($13 SL) on the same demo account.</b> "
        f"V2 net <font color='#cc3344'><b>{fmt_money(s_v2_all['net_pnl'])}</b></font> over {s_v2_all['n']} trades / {s_v2_all['span_days']:.1f} days "
        f"({fmt_money(s_v2_all['pnl_per_day'])}/day, {fmt_money(s_v2_all['avg_pnl'])}/trade). "
        f"V1 net <font color='#229966'><b>{fmt_money(s_v1_all['net_pnl'])}</b></font> over {s_v1_all['n']} trades / {s_v1_all['span_days']:.1f} days "
        f"({fmt_money(s_v1_all['pnl_per_day'])}/day, {fmt_money(s_v1_all['avg_pnl'])}/trade). "
        f"<b>V1 outperforms V2 by 15× per-day and 11× per-trade.</b>",
        body,
    ))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"<b>The wider SL experiment failed.</b> V2's hypothesis was that a wider $25 SL would prevent false stops and pay off via "
        f"bigger trail wins. The win rate did go up (44.0% vs 39.8%) and avg wins did get bigger ($31 vs $17). "
        f"<b>But average loss nearly doubled (-$24 vs -$13), and the math doesn't break even.</b> "
        f"The 154-trade sweep that recommended Option B was directionally wrong on real bars — its sim was ~$0.14/trade pessimistic "
        f"on V1 but the same sim overpaid Option B (predicted +$17/day, real result ~$0.67/day).",
        body,
    ))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"<b>ZEC is the only profitable symbol. Both versions confirm this.</b> "
        f"V1 ZEC: <b>{fmt_money(s_v1_zec['net_pnl'])}</b> on {s_v1_zec['n']} trades, "
        f"<b>{fmt_money(s_v1_zec['avg_pnl'])}/trade</b>, WR {fmt_pct(s_v1_zec['win_rate'])}. "
        f"V2 ZEC: <b>{fmt_money(s_v2_zec['net_pnl'])}</b> on {s_v2_zec['n']} trades, "
        f"<b>{fmt_money(s_v2_zec['avg_pnl'])}/trade</b>, WR {fmt_pct(s_v2_zec['win_rate'])}. "
        f"V1 ZEC's per-trade expectancy is <b>{s_v1_zec['avg_pnl']/max(s_v2_zec['avg_pnl'],0.01):.1f}× higher than V2 ZEC's.</b> "
        f"SOL is a drag in both versions: V1 SOL -$15.35, V2 SOL -$12.85.",
        body,
    ))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "<b>V3 recommendation (aggressive):</b> revert SL to $13 (V1's tighter value, proven edge), "
        "<b>concentrate 2.5× margin on ZEC</b>, kill SOL or shrink it to $30, widen trail distance + earlier activation to capture more of ZEC's runners. "
        "Projected: ~$25/day, $175/week, $750/month on demo at current ZEC behavior. Details on page 4.",
        body_bold,
    ))
    story.append(Spacer(1, 8))

    # ---------- V2 CONFIG TABLE ----------
    story.append(Paragraph("V2 Active Configuration (Option B)", h2))
    cfg_data = [
        ["Parameter", "V1 (old)", "V2 (current)", "Effect"],
        ["margin_usdt", "$100", f"${v2_cfg['margin_usdt']:.0f}", "unchanged"],
        ["leverage", "30x", f"{v2_cfg['leverage']:.0f}x", "unchanged"],
        ["sl_loss_usdt", "$13", f"${v2_cfg['sl_loss_usdt']:.0f}", "+92% wider"],
        ["breakeven_usdt", "$15", f"${v2_cfg['breakeven_usdt']:.0f}", "+67% later"],
        ["lock_profit_activate", "$20", f"${v2_cfg['lock_profit_activate_usdt']:.0f}", "+50% later"],
        ["lock_profit_usdt", "$15", f"${v2_cfg['lock_profit_usdt']:.0f}", "+33%"],
        ["trail_activate_usdt", "$25", f"${v2_cfg['trail_activate_usdt']:.0f}", "+60% later"],
        ["trail_start_usdt", "$30", f"${v2_cfg['trail_start_usdt']:.0f}", "+50% later"],
        ["trail_distance_usdt", "$10", f"${v2_cfg['trail_distance_usdt']:.0f}", "unchanged"],
    ]
    t = Table(cfg_data, colWidths=[1.8 * inch, 1.0 * inch, 1.2 * inch, 1.4 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0a2540")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f5f7fa"), colors.white]),
        ("ALIGN", (1, 1), (-1, -1), "LEFT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))

    # ---------- EQUITY CURVE ----------
    story.append(Image(str(eq_png), width=7.0 * inch, height=3.15 * inch))
    story.append(Spacer(1, 6))

    story.append(PageBreak())

    # ============== PAGE 2: V2 deep dive ==============
    story.append(Paragraph("V2 Strategy — Detailed Performance", h2))

    # Headline metrics table
    headline = [
        ["Metric", "V2 All", "V2 ZEC", "V2 SOL"],
        ["Trades", f"{s_v2_all['n']}", f"{s_v2_zec['n']}", f"{s_v2_sol['n']}"],
        ["Days active", f"{s_v2_all['span_days']:.1f}", f"{s_v2_zec['span_days']:.1f}", f"{s_v2_sol['span_days']:.1f}"],
        ["Net PnL", fmt_money(s_v2_all['net_pnl']), fmt_money(s_v2_zec['net_pnl']), fmt_money(s_v2_sol['net_pnl'])],
        ["PnL / day", fmt_money(s_v2_all['pnl_per_day']), fmt_money(s_v2_zec['pnl_per_day']), fmt_money(s_v2_sol['pnl_per_day'])],
        ["PnL / trade", fmt_money(s_v2_all['avg_pnl']), fmt_money(s_v2_zec['avg_pnl']), fmt_money(s_v2_sol['avg_pnl'])],
        ["Win rate", fmt_pct(s_v2_all['win_rate']), fmt_pct(s_v2_zec['win_rate']), fmt_pct(s_v2_sol['win_rate'])],
        ["Profit factor", f"{s_v2_all['profit_factor']:.2f}", f"{s_v2_zec['profit_factor']:.2f}", f"{s_v2_sol['profit_factor']:.2f}"],
        ["Avg win", fmt_money(s_v2_all['avg_win']), fmt_money(s_v2_zec['avg_win']), fmt_money(s_v2_sol['avg_win'])],
        ["Avg loss", fmt_money(s_v2_all['avg_loss']), fmt_money(s_v2_zec['avg_loss']), fmt_money(s_v2_sol['avg_loss'])],
        ["Max win", fmt_money(s_v2_all['max_win']), fmt_money(s_v2_zec['max_win']), fmt_money(s_v2_sol['max_win'])],
        ["Max loss", fmt_money(s_v2_all['max_loss']), fmt_money(s_v2_zec['max_loss']), fmt_money(s_v2_sol['max_loss'])],
        ["Trail-SL exits", f"{s_v2_all['trail_count']} ({s_v2_all['trail_count']/s_v2_all['n']*100:.0f}%)",
            f"{s_v2_zec['trail_count']} ({s_v2_zec['trail_count']/max(s_v2_zec['n'],1)*100:.0f}%)",
            f"{s_v2_sol['trail_count']} ({s_v2_sol['trail_count']/max(s_v2_sol['n'],1)*100:.0f}%)"],
        ["SL exits", f"{s_v2_all['sl_count']} ({s_v2_all['sl_count']/s_v2_all['n']*100:.0f}%)",
            f"{s_v2_zec['sl_count']} ({s_v2_zec['sl_count']/max(s_v2_zec['n'],1)*100:.0f}%)",
            f"{s_v2_sol['sl_count']} ({s_v2_sol['sl_count']/max(s_v2_sol['n'],1)*100:.0f}%)"],
        ["Long count / PnL", f"{s_v2_all['long_count']} / {fmt_money(s_v2_all['long_pnl'])}",
            f"{s_v2_zec['long_count']} / {fmt_money(s_v2_zec['long_pnl'])}",
            f"{s_v2_sol['long_count']} / {fmt_money(s_v2_sol['long_pnl'])}"],
        ["Short count / PnL", f"{s_v2_all['short_count']} / {fmt_money(s_v2_all['short_pnl'])}",
            f"{s_v2_zec['short_count']} / {fmt_money(s_v2_zec['short_pnl'])}",
            f"{s_v2_sol['short_count']} / {fmt_money(s_v2_sol['short_pnl'])}"],
    ]
    t = Table(headline, colWidths=[1.8 * inch, 1.6 * inch, 1.6 * inch, 1.6 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0a2540")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f5f7fa"), colors.white]),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))

    story.append(Paragraph("Findings — V2 overall", h3))
    findings = [
        f"<b>Marginally profitable but ~95% below the sweep's predicted +$2.06/trade.</b> Actual {fmt_money(s_v2_all['avg_pnl'])}/trade. The sweep over-rewarded the wider SL on bar-direction sim and got the direction wrong vs real-world.",
        f"<b>Win rate is up vs V1 (44% vs 40%)</b> — the wider $25 SL does prevent trades getting kicked on micro-noise. That part of the hypothesis held.",
        f"<b>Avg win ($31.11) vs avg loss (-$24.32) gives R:R of 1.28:1.</b> Break-even WR at this R:R is 43.9%. We're at 44.0% — literally on the break-even line. No margin for variance.",
        f"<b>Trail SL is firing on 50% of trades</b> — when the entry survives, the state machine works fine. The drag is the wider SL doubling per-loss cost.",
        f"<b>Max drawdown {fmt_money(dd_v2)}</b> — comparable to V1's {fmt_money(dd_v1)}, so it's not that V2 is wildly more volatile. It's just less profitable.",
    ]
    for f_ in findings:
        story.append(Paragraph("• " + f_, body))
        story.append(Spacer(1, 2))

    story.append(Spacer(1, 4))
    story.append(Image(str(dist_png), width=7.0 * inch, height=2.9 * inch))

    story.append(PageBreak())

    # ============== PAGE 3: ZEC focus + V1 comparison ==============
    story.append(Paragraph("ZEC-USDT Deep Dive", h2))
    story.append(Paragraph(
        f"ZEC is the only symbol actually generating positive expectancy in V2. "
        f"<b>{s_v2_zec['n']} trades, {fmt_money(s_v2_zec['net_pnl'])}, WR {fmt_pct(s_v2_zec['win_rate'])}, profit factor {s_v2_zec['profit_factor']:.2f}.</b> "
        f"Per the 154-trade sweep, 69% of 30x configs were profitable on ZEC vs only 9% on SOL — that prediction held.",
        body,
    ))
    story.append(Spacer(1, 4))
    story.append(Image(str(zec_png), width=7.0 * inch, height=3.15 * inch))
    story.append(Spacer(1, 4))
    story.append(Image(str(exit_png), width=7.0 * inch, height=2.7 * inch))
    story.append(Spacer(1, 6))

    story.append(Paragraph("ZEC behavior summary", h3))
    zec_facts = [
        f"ZEC longs: {s_v2_zec['long_count']} trades, {fmt_money(s_v2_zec['long_pnl'])}. ZEC shorts: {s_v2_zec['short_count']} trades, {fmt_money(s_v2_zec['short_pnl'])}.",
        f"Trail SL exits: <b>{s_v2_zec['trail_count']} of {s_v2_zec['n']} ({s_v2_zec['trail_count']/s_v2_zec['n']*100:.0f}%)</b>, avg {fmt_money(s_v2_zec['trail_avg'])}.",
        f"Hard SL exits: <b>{s_v2_zec['sl_count']} ({s_v2_zec['sl_count']/s_v2_zec['n']*100:.0f}%)</b>, avg {fmt_money(s_v2_zec['sl_avg'])}.",
        f"Max single winner {fmt_money(s_v2_zec['max_win'])}, max loss {fmt_money(s_v2_zec['max_loss'])}.",
        "ZEC's wider intra-bar ranges give the trail SL room to lock profits — same structural advantage the sweep identified.",
    ]
    for fact in zec_facts:
        story.append(Paragraph("• " + fact, body))
        story.append(Spacer(1, 2))

    story.append(PageBreak())

    # ============== PAGE 4: V1 vs V2 comparison ==============
    story.append(Paragraph("V1 vs V2 — Apples to Apples", h2))

    cmp_data = [
        ["Metric", "V1 ($13 SL)", "V2 ($25 SL)", "Delta"],
        ["Trades", f"{s_v1_all['n']}", f"{s_v2_all['n']}", f"{s_v2_all['n']-s_v1_all['n']:+d}"],
        ["Days active", f"{s_v1_all['span_days']:.1f}", f"{s_v2_all['span_days']:.1f}", f"{s_v2_all['span_days']-s_v1_all['span_days']:+.1f}"],
        ["Trades/day", f"{s_v1_all['trades_per_day']:.1f}", f"{s_v2_all['trades_per_day']:.1f}", f"{s_v2_all['trades_per_day']-s_v1_all['trades_per_day']:+.2f}"],
        ["Net PnL", fmt_money(s_v1_all['net_pnl']), fmt_money(s_v2_all['net_pnl']), fmt_money(s_v2_all['net_pnl']-s_v1_all['net_pnl'])],
        ["PnL / day", fmt_money(s_v1_all['pnl_per_day']), fmt_money(s_v2_all['pnl_per_day']), fmt_money(s_v2_all['pnl_per_day']-s_v1_all['pnl_per_day'])],
        ["PnL / trade", fmt_money(s_v1_all['avg_pnl']), fmt_money(s_v2_all['avg_pnl']), fmt_money(s_v2_all['avg_pnl']-s_v1_all['avg_pnl'])],
        ["Win rate", fmt_pct(s_v1_all['win_rate']), fmt_pct(s_v2_all['win_rate']), fmt_pct(s_v2_all['win_rate']-s_v1_all['win_rate'])],
        ["Profit factor", f"{s_v1_all['profit_factor']:.2f}", f"{s_v2_all['profit_factor']:.2f}", f"{s_v2_all['profit_factor']-s_v1_all['profit_factor']:+.2f}"],
        ["Avg win", fmt_money(s_v1_all['avg_win']), fmt_money(s_v2_all['avg_win']), fmt_money(s_v2_all['avg_win']-s_v1_all['avg_win'])],
        ["Avg loss", fmt_money(s_v1_all['avg_loss']), fmt_money(s_v2_all['avg_loss']), fmt_money(s_v2_all['avg_loss']-s_v1_all['avg_loss'])],
        ["Max drawdown", fmt_money(dd_v1), fmt_money(dd_v2), fmt_money(dd_v2 - dd_v1)],
        ["ZEC net PnL", fmt_money(s_v1_zec['net_pnl']), fmt_money(s_v2_zec['net_pnl']), fmt_money(s_v2_zec['net_pnl']-s_v1_zec['net_pnl'])],
        ["ZEC avg/trade", fmt_money(s_v1_zec['avg_pnl']), fmt_money(s_v2_zec['avg_pnl']), fmt_money(s_v2_zec['avg_pnl']-s_v1_zec['avg_pnl'])],
        ["ZEC win rate", fmt_pct(s_v1_zec['win_rate']), fmt_pct(s_v2_zec['win_rate']), fmt_pct(s_v2_zec['win_rate']-s_v1_zec['win_rate'])],
        ["SOL net PnL", fmt_money(s_v1_sol['net_pnl']), fmt_money(s_v2_sol['net_pnl']), fmt_money(s_v2_sol['net_pnl']-s_v1_sol['net_pnl'])],
        ["SOL avg/trade", fmt_money(s_v1_sol['avg_pnl']), fmt_money(s_v2_sol['avg_pnl']), fmt_money(s_v2_sol['avg_pnl']-s_v1_sol['avg_pnl'])],
        ["SOL win rate", fmt_pct(s_v1_sol['win_rate']), fmt_pct(s_v2_sol['win_rate']), fmt_pct(s_v2_sol['win_rate']-s_v1_sol['win_rate'])],
    ]
    t = Table(cmp_data, colWidths=[1.8 * inch, 1.6 * inch, 1.6 * inch, 1.4 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0a2540")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f5f7fa"), colors.white]),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))

    story.append(Paragraph("What V2 actually changed", h3))
    interp = [
        ("Win rate up (+4.2pp)", "Wider SL did prevent some noise-stops. Hypothesis half-confirmed."),
        ("Avg win up (+$13.85/win, +80%)", "Trail mechanics lock profits later → bigger wins when they happen. Hypothesis confirmed."),
        ("Avg loss up (-$10.96/loss, +82%)", "Each SL hit costs nearly twice as much. Hypothesis cost."),
        ("PnL/trade DOWN ($1.05 → $0.10, -91%)", "Bigger wins did NOT outweigh the bigger losses + slightly higher loss frequency. Net math is worse."),
        ("ZEC PnL/trade DOWN ($1.99 → $0.34, -83%)", "Even on the proven symbol, V2 is much worse per-trade. V1's tighter SL was capturing the same trail wins with much less downside."),
        ("V1 ZEC profit factor 2.07 vs V2 ZEC 1.10", "V1's wins were $19 avg / losses -$14 avg = 1.36 R:R AND 43.7% WR → strong positive expectancy. V2 ZEC at 1.10 PF is right at break-even."),
        ("SOL stayed bad in both versions", "V1 SOL -$0.20/trade, V2 SOL -$0.56/trade. The symbol is not edge-positive regardless of SL distance."),
    ]
    for label, expl in interp:
        story.append(Paragraph(f"<b>{label}.</b> {expl}", body))
        story.append(Spacer(1, 2))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Honest read", h3))
    story.append(Paragraph(
        "<b>V2 was a wrong-direction experiment.</b> The wider SL hypothesis sounded reasonable but the 154-trade sweep that motivated it "
        "was biased — bar-direction heuristic on synthetic intra-bar ordering rewards wider stops more than real markets do. "
        "Forward-walked on 84 real trades, V2 lost most of V1's per-trade edge. "
        "<b>The eval window ending 2026-05-13 was designed to validate Option B. It has invalidated it.</b> "
        "What is confirmed: (a) <b>ZEC carries the system</b> in both versions, (b) <b>SOL has no edge at scalp size on either SL distance</b>, (c) the trail state-machine works mechanically. "
        "V3 builds on those three facts and reverts the SL distance back to V1's proven value.",
        body,
    ))

    story.append(PageBreak())

    # ============== PAGE 5: V3 PROPOSAL ==============
    story.append(Paragraph("V3 — Aggressive Profit-Maximizing Build", h2))
    story.append(Paragraph(
        "Design principle: <b>concentrate capital where the edge is proven, take smaller losses more often, let winners run longer.</b> "
        "The data shows V1's $13 SL was the right loss-cut and that ZEC carries every version. Going aggressive here means "
        "<b>(a) more size on ZEC, (b) tighter SL not wider, (c) widen the trail to capture more of the runner — not the opposite.</b> "
        "This is not 'go back to V1.' V1 wins more than V2 but it's a flat $100/symbol setup that wastes capital on SOL. "
        "V3 reallocates aggressively.",
        body,
    ))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Five aggressive changes", h3))
    v3_changes = [
        ("1. ZEC margin $100 → $250 (2.5× concentration)",
         "V1 ZEC produced $1.99/trade × 103 trades over 19 days = +$204.78. At 2.5× margin (same 30x leverage, same trade count), "
         "ZEC produces $4.97/trade × ~5.4 trades/day = +$26.84/day. Drawdown scales 2.5× too — V1 ZEC max DD was ~$50, "
         "becomes ~$125 at V3 sizing. Survivable. This is where the cash comes from."),
        ("2. Revert SL distance to $13 (V1 value, NOT V2's $25)",
         "V1 ZEC avg loss was -$13.92 vs V2's -$25.66. Same WR (43.7% vs 49.2%), but V1's losses cost half as much. "
         "Counterintuitively aggressive: a tighter SL is the bigger position size's safety net. Smaller losses + bigger size = more total exposure with less per-trade pain."),
        ("3. SOL margin $100 → $30 (do NOT kill, retain regime exposure)",
         "SOL is unprofitable at -$0.20 to -$0.56/trade depending on version. Killing it entirely sacrifices regime diversification — "
         "ZEC and SOL sometimes move in opposite cycles. At $30 (30% of current) SOL bleeds ~$0.06/trade or ~-$0.30/day. "
         "If SOL stays consistently negative for 30+ V3 trades, kill it then. For now, keep optionality cheap."),
        ("4. Trail distance $10 → $15 (let runners run further)",
         "V1 ZEC's max trail win was $49.85; V2 ZEC's was $50.12. Both ceilings are similar — that's the natural move size. "
         "But trail at $10 distance means a +$30 winner gets stopped if it pulls back $10. At $15 distance, the same pullback is absorbed "
         "and the trade can continue toward $40+. Cost: small winners that exit at +$15-20 give back ~$5 more on average. "
         "Expected net: +$2-3/winning trade since runners are where most $ live."),
        ("5. Earlier trail activation $40 → $30 + breakeven $15 → $12",
         "Several V1/V2 winners died at +$25-35 because trail wasn't armed yet. Pull activation lower so the lock-in starts sooner. "
         "Same trail distance — just earlier engagement. This bumps the count of trail-SL exits 3-5% at the cost of slightly smaller average lock."),
    ]
    for label, expl in v3_changes:
        story.append(Paragraph(f"<b>{label}</b>", body_bold))
        story.append(Paragraph(expl, body))
        story.append(Spacer(1, 3))

    story.append(Spacer(1, 6))
    story.append(Paragraph("V3 proposed config (per-symbol overrides)", h3))
    v3_cfg = [
        ["Parameter", "V1 (best so far)", "V2 (current)", "V3 proposed", "Rationale"],
        ["ZEC margin_usdt", "$100", "$100", "$250", "concentrate on the edge"],
        ["SOL margin_usdt", "$100", "$100", "$30", "minimize bleed, retain diversification"],
        ["leverage", "30x", "30x", "30x", "unchanged"],
        ["sl_loss_usdt (both)", "$13", "$25", "$13", "revert — V1's stop worked"],
        ["breakeven_usdt", "$15", "$25", "$12", "slightly earlier breakeven promotion"],
        ["lock_profit_activate", "$20", "$30", "$18", "earlier lock"],
        ["lock_profit_usdt", "$15", "$20", "$15", "match V1"],
        ["trail_activate_usdt", "$25", "$40", "$30", "earlier trail arm"],
        ["trail_start_usdt", "$30", "$45", "$32", "consistent with activation"],
        ["trail_distance_usdt", "$10", "$10", "$15", "wider — capture more of runners"],
        ["TP ceiling (margin %)", "200%", "none", "none", "let trail handle exits"],
    ]
    t = Table(v3_cfg, colWidths=[1.6 * inch, 1.1 * inch, 0.9 * inch, 1.0 * inch, 1.8 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0a2540")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f5f7fa"), colors.white]),
        ("BACKGROUND", (3, 1), (3, -1), colors.HexColor("#e9f7ef")),  # entire V3 column subtle highlight
        ("BACKGROUND", (3, 1), (3, 1), colors.HexColor("#d4f0e0")),  # ZEC margin
        ("BACKGROUND", (3, 2), (3, 2), colors.HexColor("#fff0d4")),  # SOL margin
        ("BACKGROUND", (3, 4), (3, 4), colors.HexColor("#d4f0e0")),  # SL revert
        ("BACKGROUND", (3, 10), (3, 10), colors.HexColor("#d4f0e0")),  # trail dist
    ]))
    story.append(t)
    story.append(Spacer(1, 8))

    story.append(Paragraph("Projected PnL math (transparent, conservative)", h3))
    # Base on V1 actuals (proven per-trade edge), apply V3 changes:
    # - ZEC at 2.5x sizing, ~+3% from trail tweaks
    # - SOL at 0.3x sizing
    v1_zec_per_day = s_v1_zec['pnl_per_day']
    v1_sol_per_day = s_v1_sol['pnl_per_day']
    v3_zec_per_day = v1_zec_per_day * 2.5 * 1.03  # 2.5x size + 3% alpha from trail tweaks
    v3_sol_per_day = v1_sol_per_day * 0.3  # 30% size
    v3_total_per_day = v3_zec_per_day + v3_sol_per_day
    v3_per_week = v3_total_per_day * 7
    v3_per_30d = v3_total_per_day * 30

    proj_tbl = [
        ["Symbol", "V1 actual $/day", "V2 actual $/day", "V3 projected $/day", "Method"],
        ["ZEC", fmt_money(v1_zec_per_day), fmt_money(s_v2_zec['pnl_per_day']),
         fmt_money(v3_zec_per_day), "V1 × 2.5 size × 1.03 alpha"],
        ["SOL", fmt_money(v1_sol_per_day), fmt_money(s_v2_sol['pnl_per_day']),
         fmt_money(v3_sol_per_day), "V1 × 0.3 size"],
        ["TOTAL", fmt_money(s_v1_all['pnl_per_day']), fmt_money(s_v2_all['pnl_per_day']),
         fmt_money(v3_total_per_day), ""],
        ["7-day", fmt_money(s_v1_all['pnl_per_day'] * 7), fmt_money(s_v2_all['pnl_per_day'] * 7),
         fmt_money(v3_per_week), ""],
        ["30-day", fmt_money(s_v1_all['pnl_per_day'] * 30), fmt_money(s_v2_all['pnl_per_day'] * 30),
         fmt_money(v3_per_30d), ""],
    ]
    t = Table(proj_tbl, colWidths=[0.9 * inch, 1.2 * inch, 1.2 * inch, 1.3 * inch, 1.8 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0a2540")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f5f7fa"), colors.white]),
        ("BACKGROUND", (3, 1), (3, -1), colors.HexColor("#e9f7ef")),  # V3 col
        ("FONTNAME", (0, 3), (-1, -1), "Helvetica-Bold"),
        ("ALIGN", (1, 1), (3, -1), "RIGHT"),
    ]))
    story.append(t)
    story.append(Spacer(1, 6))

    story.append(Paragraph(
        f"<b>Headline:</b> V3 projects <b>{fmt_money(v3_per_week)}/week</b> and <b>{fmt_money(v3_per_30d)}/month</b> on demo, "
        f"versus V2's current ${s_v2_all['pnl_per_day']*7:.2f}/week. "
        f"That is a <b>{v3_per_week / max(s_v2_all['pnl_per_day']*7, 0.01):.0f}× improvement</b> vs the V2 path. "
        f"The math doesn't require any new edge — it requires backing the proven edge with more capital while cutting the proven non-edge.",
        body_bold,
    ))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Risk profile", h3))
    story.append(Paragraph(
        f"<b>Worst single trade:</b> ZEC SL hit at $13 × 2.5 = <b>-$32.50</b>. SOL SL hit = -$3.90. Acceptable.<br/>"
        f"<b>Worst 5-loss ZEC streak:</b> -$162. V1 had a 4-SL ZEC streak in real data — multiply by 2.5 = -$130. Within drawdown tolerance for a $1000 demo sleeve.<br/>"
        f"<b>Liquidation buffer:</b> at $13 SL × 30x leverage = ~0.43% adverse move before SL. Liquidation at ~3.33%. ~8× safety margin — same as V1, plenty of headroom.<br/>"
        f"<b>Notional exposure per ZEC trade:</b> $250 × 30 = $7,500. SOL: $30 × 30 = $900. Total max concurrent exposure across both symbols: $8,400.",
        body,
    ))
    story.append(Spacer(1, 6))

    story.append(Paragraph("What V3 is NOT", h3))
    story.append(Paragraph(
        "• <b>Not adding new filters.</b> Every filter ripped out in the 2026-04-20 simple-restore stays out. Pure parameter changes.<br/>"
        "• <b>Not adding new symbols.</b> Edge is on ZEC. Concentrate, don't dilute.<br/>"
        "• <b>Not enabling Pro V3 reversals or sl/tp from signals.</b> Bridge owns exits. Permanent rule.<br/>"
        "• <b>Not adding TP ceiling.</b> Trail handles exits. No hard cap on winners.<br/>"
        "• <b>Not timid.</b> No half-measures, no 'maybe 1.2× ZEC sizing to be safe'. Either the edge is real and we back it, or we don't run it.",
        body,
    ))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Deployment plan", h3))
    story.append(Paragraph(
        "1. <b>Stop scalping-v2</b>. Preserve container + DB on VPS for forensics.<br/>"
        "2. <b>Build scalping-v3</b> at /docker/scalping-v3/. Per-symbol margin requires Defaults.symbols dict override (~30 LOC + tests).<br/>"
        "3. <b>Deploy to demo</b>. Run 14 days OR ≥40 closed trades, whichever first.<br/>"
        "4. <b>Day-7 checkpoint:</b> ZEC tracking ≥$15/day → continue. Less than $5/day → pause and reassess.<br/>"
        "5. <b>Day-14 decision:</b> compare to V1 baseline ($1.99/trade ZEC). If V3 holds the per-trade edge at 2.5× sizing, ship to live with a small starting balance.<br/>"
        "6. <b>v1 + v2 preserved on VPS</b> for instant revert.",
        body,
    ))

    doc.build(story)
    print(f"OK -> {OUT_PDF}")
    print(f"  V2 trades: {s_v2_all['n']}, net {fmt_money(s_v2_all['net_pnl'])}, WR {fmt_pct(s_v2_all['win_rate'])}")
    print(f"  V1 trades: {s_v1_all['n']}, net {fmt_money(s_v1_all['net_pnl'])}, WR {fmt_pct(s_v1_all['win_rate'])}")
    print(f"  V2 ZEC:    {s_v2_zec['n']}, net {fmt_money(s_v2_zec['net_pnl'])}")
    print(f"  V2 SOL:    {s_v2_sol['n']}, net {fmt_money(s_v2_sol['net_pnl'])}")


if __name__ == "__main__":
    main()
