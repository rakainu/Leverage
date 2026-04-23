# Observation Window — 2026-04-23 onward

Short doc to track what we learn from letting the cron run untouched for 5–7 days.
No scoring changes, no alert-logic changes, no Telegram, no positioning endpoints
during this window. Each morning, append a daily entry below.

**Entry-point decision:** at the end of the window we tune thresholds or confirm
them. Don't tune before.

---

## How to run the daily eval (runbook)

Trigger: Rich says "daily eval" (or similar) after 06:30 UTC. Don't append
entries unprompted.

```bash
# 1. Confirm today's cron ran cleanly (replace YYYY-MM-DD with today UTC)
ssh root@46.202.146.30 "ls -la /root/SqueezeWatch/outputs/daily/ /root/SqueezeWatch/data/snapshots/ | tail -5"
ssh root@46.202.146.30 "grep -n 'SqueezeWatch scan @ YYYY-MM-DD' /var/log/squeezewatch.log; tail -5 /var/log/squeezewatch.log"

# 2. Pull TODAY + YESTERDAY snapshots fresh from VPS (never trust stale local files)
scp root@46.202.146.30:/root/SqueezeWatch/data/snapshots/YYYY-MM-DD.json     SqueezeWatch/data/snapshots/
scp root@46.202.146.30:/root/SqueezeWatch/data/snapshots/YESTERDAY.json       SqueezeWatch/data/snapshots/
scp root@46.202.146.30:/root/SqueezeWatch/outputs/daily/YYYY-MM-DD.md          SqueezeWatch/outputs/daily/
scp root@46.202.146.30:/root/SqueezeWatch/data/history/scores.csv              SqueezeWatch/data/history/

# 3. Compute day-over-day and append an entry below following the existing format.
#    Required fields per entry: universe size, top 15, top 8, max score,
#    top-15 churn (in/out), biggest risers (Δ ≥ +1.0), biggest fallers (Δ ≤ -1.5),
#    8.0+ crosses, alert volume by type, silent liquidity dropouts,
#    status of prior high-conviction names, compare-logic sanity check if anything surprises.
```

**Do not** commit snapshots or outputs — `data/` and `outputs/` are gitignored.
Commit the updated doc only.

If the cron header is missing, the "Done in XXs." closing line is absent, or
the digest file is missing: **stop and report to Rich immediately**. That's a
cron/data integrity issue and is the only reason to break observation-mode
rules before 2026-04-30.

---

## What we're watching

1. **Top-15 stability** — how much of the top 15 turns over day-over-day once
   baselines settle. Day-2 churn is meaningless (first day had no comparator);
   day 3+ is when the steady-state picture begins.
2. **Biggest day-over-day risers** — log every Δ ≥ +1.0 on the 0–10 scale, with
   the rank change, so we can see whether risers tend to squeeze or fade.
3. **Any 8.0+ score crosses** — currently the `score_crossed_8` alert has never
   fired. Track the max observed score per day. If two weeks pass without a
   cross, 8.0 is probably the wrong threshold and 7.5 is the real tier break.
4. **Yesterday's top-8 follow-through** — for each day's top 8, record their
   7d return one week later. If the high-ranked names rarely squeeze in 7d,
   the scoring is probably good at identifying coil but not timing entry.
5. **Alert volume / density** — raw count/day + which types dominate. If
   `new_top_15` routinely produces 5+ alerts/day, we need to gate it by score
   before wiring Telegram.
6. **Silent liquidity dropouts** — symbols that were ranked ≤ 20 yesterday but
   absent from today's scored set (filtered by the $1M 24h-volume floor).
   Today had one: DEGENUSDT fell from rank 5 to missing. The digest has no
   section for this — worth noticing in the log so we decide whether to
   surface it later.

---

## Daily entries

### 2026-04-22 — baseline (first cron + falling-knife-fix re-run)

- **Universe:** 472 scored, 2 errors.
- **Top 8:** TRUMPUSDT 7.6 · NEWTUSDT 7.4 · ASTERUSDT 7.4 · API3USDT 7.3 ·
  DEGENUSDT 7.2 · SYRUPUSDT 7.2 · BTCDOMUSDT 7.2 · ZETAUSDT 7.1
- **Max score:** 7.6 (TRUMPUSDT).
- **Alerts:** first run — day-over-day comparison skipped.
- **Carry-forward questions:** does DEGENUSDT's $1.1M 24h-vol (just above the
  floor) evaporate tomorrow? Do the top 8 squeeze within 7d?

### 2026-04-23 — day 2

- **Universe:** 455 scored, 3 errors (all "insufficient klines" — new listings
  `GENIUSUSDT`, `CHIPUSDT`, `OPGUSDT`).
- **Top 8:** XAUTUSDT 7.8 · ASTERUSDT 7.6 · ZETAUSDT 7.4 · 1000SHIBUSDT 7.4 ·
  NEWTUSDT 7.2 · 1000FLOKIUSDT 7.2 · TRUMPUSDT 7.2 · API3USDT 7.1
- **Max score:** 7.8 (XAUTUSDT — new all-time high for the scanner).
- **Top-15 churn:** 6 in / 6 out (40% turnover). Not interpretable yet — day 2
  has no prior stable base.
  - In: 1000FLOKIUSDT, RUNEUSDT, QTUMUSDT, FLUIDUSDT, AVNTUSDT, BBUSDT.
  - Out: DEGENUSDT (liquidity dropout), BTCDOMUSDT (→ 16), MOVEUSDT (→ 29),
    LSKUSDT (→ out of universe), YFIUSDT (→ 30), GMTUSDT (→ 36).
- **Biggest riser:** QUSDT Δ +2.4 (3.4 → 5.8, rank 373 → 58).
- **Other Δ ≥ +1.0:** 19 symbols. Notable: CETUSUSDT +1.9 (382 → 110),
  AVNTUSDT +1.8 (155 → 14), BBUSDT +1.7 (141 → 15), MOODENGUSDT +1.6 (384 → 177),
  SPXUSDT +1.5 (265 → 69), XAUTUSDT +1.0 (→ #1).
- **Biggest fallers (Δ ≤ −1.5):** UAIUSDT, WETUSDT, TOWNSUSDT (each −1.9);
  BIOUSDT −1.8; FIGHTUSDT −1.5; UMAUSDT −1.5 (6.4 → 4.9, rank 21 → 153).
- **8.0+ crosses:** 0. Still zero across the whole scanner history.
- **Alert volume:** 8 triggered = 6 `new_top_15` + 1 `score_jump_2` (QUSDT) +
  2 `combo_coil_tightening` (AVNTUSDT also in top 15, GUNUSDT at #40).
- **Silent dropouts:** DEGENUSDT (yesterday rank 5, 24h-vol $1.14M) dropped
  below the $1M liquidity floor — invisibly gone. LSKUSDT (yesterday rank 10)
  likely the same.
- **Yesterday top-8 decay:** TRUMPUSDT 7.6 → 7.2 (mild fade, 7d −6.4%);
  NEWTUSDT 7.4 → 7.2 (stable); ASTERUSDT 7.4 → 7.6 (strengthened, now #2);
  API3USDT 7.3 → 7.1 (stable); DEGENUSDT disappeared; SYRUPUSDT 7.2 → 6.9
  (mild fade); BTCDOMUSDT 7.2 → 6.6 (fell to near-miss); ZETAUSDT 7.1 → 7.4
  (strengthened, now #3). Net: 2 strengthened, 4 faded, 1 dropped out,
  1 lost to liquidity.
- **Compare-logic sanity check:** RUNEUSDT correctly flagged as `new_top_15`
  today (was rank 26 yesterday per VPS snapshot, now rank 9). Earlier concern
  about a false positive traced back to a stale local snapshot that diverged
  from VPS production state. `data/snapshots/` is gitignored, so local files
  and VPS files can drift — always trust VPS for eval. Regression test added
  (`test_trigger_new_top_15_not_fired_for_rank_shuffle_within_top_n`).

---

## Review checkpoint

Target: **2026-04-30** (7 days of data). At that point answer these questions
and decide whether to (a) tune `new_top_15` score gate, (b) lower
`score_crossed_8` to 7.5, (c) add a "liquidity dropout" digest section,
(d) wire Telegram, or (e) keep observing.
