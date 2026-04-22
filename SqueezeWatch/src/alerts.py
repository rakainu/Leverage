"""Format scored data + diffs into the daily Markdown digest.

Output sections:
  1. Top N (configurable, default 15)
  2. Near Misses (next 5)
  3. Higher-Risk Meme-Style (5 picks outside the top 20, non-major, high OI+funding)
  4. Changes vs yesterday (new entries, risers, graduations)
  5. Triggered Alerts (the four Phase 1 conditions)
"""
from __future__ import annotations


TRIGGER_LABELS = {
    "new_top_15": "new top-15",
    "score_crossed_8": "crossed 8.0",
    "score_jump_2": "+2.0 jump",
    "combo_coil_tightening": "coil tightening",
}


def format_daily_digest(
    scored: list,
    diff_data: dict,
    triggered: list,
    date_str: str,
    config: dict,
    universe_size: int,
    errors: list,
) -> str:
    top_n = config["scanner"].get("top_n_digest", 15)
    majors = set(config.get("bias", {}).get("majors", []))

    lines: list = []
    lines.append(f"# SqueezeWatch — {date_str}")
    lines.append(
        f"_Universe: {universe_size} symbols total · "
        f"{len(scored)} scored · {len(errors)} errors_"
    )
    lines.append("")

    # 1. Top N
    lines.append(f"## Top {top_n}")
    lines.append("")
    lines.append(
        "| # | Symbol | Score | Price | 30d% | Fund14d | OI 7d | Vol 24h | Why |"
    )
    lines.append(
        "|---|---|---|---|---|---|---|---|---|"
    )
    for s in scored[:top_n]:
        lines.append(_row_full(s))
    lines.append("")

    # 2. Near misses (next 5)
    near = scored[top_n:top_n + 5]
    if near:
        lines.append("## Near Misses (ranks {}–{})".format(top_n + 1, top_n + len(near)))
        lines.append("")
        lines.append("| # | Symbol | Score | 30d% | Fund14d | OI 7d | Why |")
        lines.append("|---|---|---|---|---|---|---|")
        for s in near:
            lines.append(_row_slim(s))
        lines.append("")

    # 3. Higher-risk meme-style
    spicy = _spicy_picks(scored, top_n + 5, majors, n=5)
    if spicy:
        lines.append("## Higher-Risk Meme-Style (outside top 20, non-major, strong OI+funding)")
        lines.append("")
        lines.append(
            "_Proxy: \"non-major\" = base asset not in the configured `bias.majors` "
            "list. CoinGecko meme-tagging is Phase 2._"
        )
        lines.append("")
        lines.append("| # | Symbol | Score | Age | 30d% | Fund14d | OI 7d | Vol 24h | Why |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for s in spicy:
            lines.append(_row_spicy(s))
        lines.append("")

    # 4. Changes
    if diff_data.get("has_yesterday"):
        if diff_data["new_entries"]:
            lines.append("## New top-15 entries vs yesterday")
            lines.append("")
            for e in diff_data["new_entries"]:
                lines.append(f"- **{e['symbol']}** — rank {e['rank']}, score {e['score']}")
            lines.append("")
        if diff_data["risers"]:
            lines.append("## Biggest risers")
            lines.append("")
            for r in diff_data["risers"][:10]:
                lines.append(
                    f"- **{r['symbol']}** — score {r['score']} (Δ +{r['delta']}), rank {r['rank']}"
                )
            lines.append("")
        if diff_data["graduations"]:
            lines.append("## Graduations (were top-20, ran)")
            lines.append("")
            for g in diff_data["graduations"]:
                lines.append(
                    f"- **{g['symbol']}** — was #{g['yesterday_rank']}, "
                    f"+{g['today_return_7d']}% last 7d"
                )
            lines.append("")
    else:
        lines.append("_First run — no prior snapshot to compare against. "
                     "Day-over-day alerts are skipped this run._")
        lines.append("")

    # 5. Triggered alerts
    if triggered:
        lines.append("## Triggered Alerts")
        lines.append("")
        for t in sorted(triggered, key=lambda x: x["rank"]):
            tags = ", ".join(TRIGGER_LABELS.get(tr, tr) for tr in t["triggers"])
            lines.append(
                f"- **{t['symbol']}** (#{t['rank']}, score {t['score']}) → {tags}"
            )
        lines.append("")

    if errors:
        lines.append(f"<details><summary>{len(errors)} symbol errors</summary>")
        lines.append("")
        for e in errors[:50]:
            lines.append(f"- `{e['symbol']}`: {e['reason']}")
        if len(errors) > 50:
            lines.append(f"- ...and {len(errors) - 50} more")
        lines.append("</details>")
        lines.append("")

    lines.append("---")
    lines.append(
        "_Data: Binance USDT-M futures public REST. Scores are heuristics, "
        "not signals. Watchlist only — not financial advice._"
    )

    return "\n".join(lines) + "\n"


def _spicy_picks(scored: list, start_idx: int, majors: set, n: int) -> list:
    rest = [s for s in scored[start_idx:] if s["symbol"] not in majors]

    def spice(s):
        cs = s.get("component_scores", {})
        return (cs.get("oi_growth") or 0) + (cs.get("funding") or 0)

    rest.sort(key=lambda s: (-spice(s), -s["squeeze_score_100"]))
    return rest[:n]


def _explain(s: dict) -> str:
    """One-line concise reason."""
    parts = []
    cs = s.get("component_scores", {})
    fl, fu, oi, npump = cs.get("flatness"), cs.get("funding"), cs.get("oi_growth"), cs.get("non_pumped")

    if fl is not None and fl >= 70:
        parts.append("tight range")
    if fu is not None and fu >= 70:
        parts.append(f"funding {s['funding_avg_14d']*100:.3f}%")
    oi7 = s.get("oi_growth_7d")
    if oi7 is None:
        parts.append("OI n/a")
    elif oi7 > 0.10:
        parts.append(f"OI +{oi7*100:.0f}% 7d")
    if npump is not None and npump >= 70:
        parts.append("pre-move")
    elif s.get("return_30d", 0) > 0.30:
        parts.append(f"+{s['return_30d']*100:.0f}% 30d already")
    return ", ".join(parts) if parts else "coiling"


def _fund_str(rate: float) -> str:
    return f"{rate * 100:+.4f}%"


def _oi7_str(s: dict) -> str:
    oi7 = s.get("oi_growth_7d")
    return f"{oi7*100:+.1f}%" if oi7 is not None else "n/a"


def _vol_str(v: float) -> str:
    return f"${v/1e6:.1f}M"


def _price_str(p: float) -> str:
    if p == 0:
        return "0"
    if p >= 1:
        return f"{p:,.4f}"
    return f"{p:.6g}"


def _row_full(s: dict) -> str:
    return (
        f"| {s['rank']} | **{s['symbol']}** | {s['squeeze_score']} | "
        f"{_price_str(s['price_last'])} | {s['return_30d']*100:+.1f}% | "
        f"{_fund_str(s['funding_avg_14d'])} | {_oi7_str(s)} | "
        f"{_vol_str(s['quote_volume_24h'])} | {_explain(s)} |"
    )


def _row_slim(s: dict) -> str:
    return (
        f"| {s['rank']} | **{s['symbol']}** | {s['squeeze_score']} | "
        f"{s['return_30d']*100:+.1f}% | {_fund_str(s['funding_avg_14d'])} | "
        f"{_oi7_str(s)} | {_explain(s)} |"
    )


def _row_spicy(s: dict) -> str:
    age = s.get("age_days")
    age_str = f"{age}d" if age is not None else "n/a"
    return (
        f"| {s['rank']} | **{s['symbol']}** | {s['squeeze_score']} | {age_str} | "
        f"{s['return_30d']*100:+.1f}% | {_fund_str(s['funding_avg_14d'])} | "
        f"{_oi7_str(s)} | {_vol_str(s['quote_volume_24h'])} | {_explain(s)} |"
    )
