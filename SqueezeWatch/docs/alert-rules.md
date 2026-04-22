# Alert Rules

When does a symbol earn a mention in the daily digest? What goes in each section?

**Phase 1 goal:** format only. No sending. Alerts are written to `outputs/daily/YYYY-MM-DD.md`.
**Phase 3:** wire to Telegram bot on the VPS.

**Score scale:** the digest displays `squeeze_score` on a **0–10 scale** (one decimal).
Internally everything is computed on 0–100; the display value is just the internal divided by 10.
All thresholds below are quoted on the 0–10 scale unless noted.

## Phase 1 trigger conditions

Four explicit alert conditions are evaluated by `src/compare.py::check_triggers()`:

| Trigger | Condition |
|---|---|
| `new_top_15` | Symbol is in today's top 15 AND was NOT in yesterday's top 15. Requires a yesterday snapshot. |
| `score_crossed_8` | Today's score ≥ 8.0 AND (yesterday's score < 8.0 OR no yesterday snapshot). |
| `score_jump_2` | Today's score − yesterday's score ≥ 2.0. Requires a yesterday snapshot. |
| `combo_coil_tightening` | Funding more negative than yesterday (Δ ≤ −0.005%) AND `oi_growth_7d` > 5% AND `abs(return_7d)` < 10%. |

The combo trigger encodes Rich's specific request:
*"funding more negative + OI rising + price still contained"*.

Triggered alerts are listed in their own section of the daily digest.

## Daily digest sections

### 1. Top 10 by Squeeze Score
Always show. Ranked, with component breakdown.

**Show per symbol:** rank, symbol, score, flatness, funding (actual rate), oi_growth_14d (%), 30d return, 24h volume ($M).

### 2. New entries to Top 30
Symbols that were NOT in yesterday's top 30 but ARE in today's. Prime "something woke up" signal.

**Gate:** score ≥ 60 to reduce noise. Symbols below 60 breaking into top 30 usually means the universe is weak that day, not that the symbol is interesting.

### 3. Big score risers
Symbols whose score increased by ≥ 15 points versus yesterday AND whose current score is ≥ 50.

Both conditions required — a 10→25 rise is noise; a 60→75 rise is a signal.

### 4. Graduations (exits)
Symbols that WERE in yesterday's top 30 but aren't today because `non_pumped_score` collapsed — i.e., they ran. Useful to know so we don't chase.

**Gate:** yesterday rank ≤ 20 AND today's `return_7d > 0.15`.

### 5. Coilers (watchlist)
Symbols scoring 55–70 for ≥ 5 consecutive days. These are the slow-burn setups. Bench them.

Requires 5-day history — section stays empty until then.

## Alert thresholds (Phase 3 push-alerts)

A symbol generates a **push** (Telegram DM) when any of these fire, not just digest mention:

| Trigger | Condition |
|---|---|
| Fresh top-10 | symbol entered top 10 today AND score ≥ 70 |
| Breakout-pre | score ≥ 80 AND OI growth 7d ≥ 30% AND funding < 0 |
| Score spike | day-over-day delta ≥ 20 AND current score ≥ 65 |

Cooldown: once a symbol triggers any alert, suppress re-triggers for 48h.

## Format constraints

- **Telegram:** 4096 char max per message. If digest exceeds, split on section boundaries.
- **Discord:** 2000 char max per message. Same split rule.
- Always include symbol, score, and the 1-line reason. Dump full details on demand (Phase 3: "/details BTCUSDT").

## What NOT to alert on

- Any symbol with `liquidity_score == 0` (untradable). Never mention.
- Symbols with score < 50 — don't pollute the signal.
- Already-pumped symbols (non_pumped_score < 20) unless they're a graduation callout.
- Symbols that already alerted in the last 48h on the same trigger type.

## Template locations

- `alerts/templates/telegram.md`
- `alerts/templates/discord.md`

Template variables available (see `src/alerts.py` for the complete list):
```
{date}              e.g. "2026-04-21"
{top_10}            rendered block
{new_entries}       rendered block (or empty string)
{score_risers}      rendered block
{graduations}       rendered block
{coilers}           rendered block
{universe_size}     int
{errors}            list of symbols the scanner failed on
```
