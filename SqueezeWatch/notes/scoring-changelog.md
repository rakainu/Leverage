# Scoring Changelog

Every change to weights, thresholds, or component formulas. One entry per change. Dated. Reason required.

Follow the **one filter change at a time** rule — don't stack changes between evaluation cycles.

## Format

```
## YYYY-MM-DD — [what changed]

**Change:** (before → after)
**Reason:** (why)
**Expected effect:** (what we think this does to rankings)
**Review date:** (when we'll evaluate)
```

## Entries

## 2026-04-21 — initial v0 formula

**Change:** n/a (baseline)
**Reason:** starting point based on theory in `notes/squeeze-theory.md`.
**Expected effect:** baseline — rankings should tilt toward small/mid caps with coiled price and negative funding.
**Review date:** 2026-05-21 (after ~30 days of snapshots).


## 2026-04-22 — non_pumped made symmetric (falling-knife fix)

**Change:** `non_pumped_score(return_7d, return_30d)`
  - before: `max_ret = max(return_7d, return_30d)`  (signed)
  - after:  `max_abs_ret = max(abs(return_7d), abs(return_30d))`  (magnitude)

Bands unchanged. Only the input to the piecewise table changed.

**Reason:** the first full-universe production scan on 2026-04-22 surfaced
8 out of 50 top-ranked symbols that were actively crashing, not coiling:

```
#  9 SAGAUSDT       7d=-31.6%  30d=-40.0%  non_pumped=100  (score 6.9)
# 11 IPUSDT         7d= +2.2%  30d=-20.3%  non_pumped=100  (score 6.9)
# 23 WUSDT          7d= +5.1%  30d=-22.2%  non_pumped= 80  (score 6.6)
# 24 AAVEUSDT       7d=-11.6%  30d=-16.9%  non_pumped=100  (score 6.5)
# 38 EDUUSDT        7d=+16.8%  30d=-25.2%  non_pumped= 50  (score 6.2)
# 46 INXUSDT        7d=-35.8%  30d=-35.1%  non_pumped=100  (score 6.1)
# 48 KERNELUSDT     7d=-20.1%  30d=-29.5%  non_pumped=100  (score 6.0)
# 49 DYMUSDT        7d= +1.6%  30d=-37.7%  non_pumped=100  (score 6.0)
```

The old `max(return_7d, return_30d)` treated a −40% crash as "flat enough"
because `max(−0.32, −0.40) = −0.32 ≤ 0.05 → score 100`. That rewards shorts
piling in on capitulation, which is the opposite of the squeeze thesis.

The thesis in `notes/squeeze-theory.md` explicitly requires flat/sideways
price. Symmetric absolute-magnitude penalty enforces that.

**Expected effect:** 8 of the top-50 ranks free up for real coils. Falling-knife
capitulation names should drop to composite range 50–55 depending on which
other components still fire.

**Related:** followed the "one filter change at a time" rule — no weight
changes or other component tweaks in this edit.

**Review date:** re-run 2026-04-22 full-universe scan immediately to verify
falling-knife names drop out and real coils take their place.
