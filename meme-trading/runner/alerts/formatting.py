"""Pure alert formatting helpers — no I/O, fully testable."""
import html as html_lib


def escape_html(text: str) -> str:
    """Escape <, >, & in user/token-derived text."""
    return html_lib.escape(str(text))


def mint_short(mint: str) -> str:
    """Shorten a mint address to 4...4 form."""
    if len(mint) <= 10:
        return mint
    return f"{mint[:4]}...{mint[-4:]}"


def _truncate_symbol(symbol: str | None) -> str:
    """Cap symbol at 12 chars, default to empty."""
    if not symbol:
        return ""
    return str(symbol)[:12]


def format_top_reasons(explanation: dict) -> list[tuple[str, float, float, float]]:
    """Top 3 dimensions by weighted contribution. Excludes placeholders.
    Returns [(name, score, weight, weighted), ...]."""
    dims = explanation.get("dimensions", {})
    candidates = []
    for name, info in dims.items():
        if info.get("detail", {}).get("placeholder"):
            continue
        candidates.append((
            name,
            float(info.get("score", 0)),
            float(info.get("weight", 0)),
            float(info.get("weighted", 0)),
        ))
    candidates.sort(key=lambda x: x[3], reverse=True)
    return candidates[:3]


def format_cautions(explanation: dict) -> list[str]:
    """Build caution strings. Returns ["No major cautions"] if nothing notable."""
    cautions: list[str] = []
    dims = explanation.get("dimensions", {})
    for name, info in dims.items():
        score = float(info.get("score", 100))
        if score < 40:
            label = name.replace("_", " ").title()
            cautions.append(f"{label} {score:.0f} — below threshold")
    if explanation.get("data_degraded"):
        missing = explanation.get("missing_subscores", [])
        cautions.append(f"Data degraded — missing {', '.join(missing)}")
    rug_detail = dims.get("rug_risk", {}).get("detail", {})
    if rug_detail.get("insider_capped"):
        cautions.append("Insider risk cap triggered")
    return cautions if cautions else ["No major cautions"]


def _format_verdict_label(verdict: str) -> str:
    return verdict.upper().replace("_", " ")


def _format_pnl(pnl: float | None) -> str:
    if pnl is None:
        return "N/A"
    sign = "+" if pnl >= 0 else ""
    return f"{sign}{pnl:.1f}%"


def format_entry_alert(alert: dict) -> str:
    """Format an entry alert dict as HTML for Telegram."""
    verdict_label = _format_verdict_label(alert["verdict"])
    score = alert["runner_score"]
    symbol = escape_html(_truncate_symbol(alert.get("symbol")))
    mint = alert["token_mint"]
    short = mint_short(mint)
    amount = alert["amount_sol"]
    price_usd = alert.get("entry_price_usd")
    price_str = f"${price_usd:.6g}" if price_usd else f"{alert['entry_price_sol']:.8g} SOL"

    cluster = alert.get("cluster_summary", {})
    wc = cluster.get("wallet_count", 0)
    tiers = cluster.get("tier_counts", {})
    tier_parts = [f"{v}{k}" for k, v in sorted(tiers.items())]
    tier_str = ", ".join(tier_parts) if tier_parts else ""
    conv_min = cluster.get("convergence_minutes", 0)

    reasons = format_top_reasons(alert.get("explanation", {}))
    cautions = format_cautions(alert.get("explanation", {}))

    lines = [
        f"<b>FROM: RUNNER • {verdict_label} ({score:.0f})</b>",
        "",
        f"<b>{symbol}</b> • <code>{short}</code>",
        f"Cluster: {wc} wallets ({tier_str}) in {conv_min:.0f} min",
        "",
        "<b>Why it scored well:</b>",
    ]
    for i, (name, s, w, wd) in enumerate(reasons, 1):
        label = name.replace("_", " ").title()
        lines.append(f"  {i}. {label}  {s:.0f} (x{w:.2f} = {wd:.1f})")
    lines.append("")
    lines.append("<b>Cautions:</b>")
    for c in cautions:
        lines.append(f"  {escape_html(c)}")
    lines.append("")
    lines.append(f"Paper entry: {amount} SOL @ {price_str}")
    lines.append("")
    lines.append(
        f'<a href="https://dexscreener.com/solana/{mint}">DexScreener</a>'
        f' | <a href="https://solscan.io/token/{mint}">Solscan</a>'
    )
    return "\n".join(lines)


def format_close_alert(alert: dict) -> str:
    """Format a close alert dict as HTML for Telegram."""
    verdict_label = _format_verdict_label(alert["verdict"])
    score = alert["runner_score"]
    symbol = escape_html(_truncate_symbol(alert.get("symbol")))
    entry_usd = alert.get("entry_price_usd")
    exit_sol = alert.get("exit_price_sol")
    entry_str = f"${entry_usd:.6g}" if entry_usd else f"{alert.get('entry_price_sol', 0):.8g} SOL"

    milestones = alert.get("milestones", {})
    final_pnl = milestones.get("24h")
    if final_pnl is None and exit_sol and alert.get("entry_price_sol"):
        entry_p = alert["entry_price_sol"]
        if entry_p > 0:
            final_pnl = (exit_sol - entry_p) / entry_p * 100.0

    mfe = alert.get("max_favorable_pct", 0)
    mae = alert.get("max_adverse_pct", 0)

    lines = [
        f"<b>FROM: RUNNER • CLOSED • {symbol} ({score:.0f} → {verdict_label})</b>",
        "",
        f"Final P&L: {_format_pnl(final_pnl)}",
    ]
    if exit_sol:
        lines.append(f"Entry: {entry_str} → Exit: ${exit_sol:.6g}")
    else:
        lines.append(f"Entry: {entry_str}")
    lines.append("")

    milestone_order = ["5m", "30m", "1h", "4h", "24h"]
    captured = [(k, milestones[k]) for k in milestone_order if milestones.get(k) is not None]
    if captured:
        lines.append("Milestones:")
        for label, pnl in captured:
            lines.append(f"  {label}:  {_format_pnl(pnl)}")
        lines.append("")

    lines.append(f"MFE: {_format_pnl(mfe)} | MAE: {_format_pnl(mae)}")
    return "\n".join(lines)
