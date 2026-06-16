"""Driver: staged search -> champion -> full report -> validation -> verdict."""
from __future__ import annotations
import itertools
import search_v2 as S

S.load(S.COINS)

BASE = dict(entry_mode="pullback", exit_model="D", don_entry=20, don_exit=10, ema_len=100,
            atr_stop=1.5, atr_trail=2.5, vol_mult=1.2, atr_min_pct=0.6, adx_min=0,
            rs_long=0, rs_short=0, risk_mode="risk", risk_usd=75)

STAGES = [
    ("entry_mode", [dict(entry_mode=v) for v in ("breakout", "pullback")]),
    ("exit_model", [dict(exit_model=v) for v in ("A", "B", "C", "D", "E")]),
    ("don_entry", [dict(don_entry=v) for v in (15, 20, 30, 40)]),
    ("don_exit", [dict(don_exit=v) for v in (5, 10, 15, 20)]),
    ("ema_len", [dict(ema_len=v) for v in (50, 100, 200)]),
    ("atr_stop", [dict(atr_stop=v) for v in (1.2, 1.5, 2.0, 2.5)]),
    ("atr_trail", [dict(atr_trail=v) for v in (2.0, 2.5, 3.0, 3.5)]),
    ("vol_mult", [dict(vol_mult=v) for v in (0.0, 1.1, 1.2, 1.5)]),
    ("atr_min_pct", [dict(atr_min_pct=v) for v in (0.4, 0.6, 0.8, 1.0)]),
    ("adx_min", [dict(adx_min=v) for v in (0, 15, 20, 25)]),
    ("rs", [dict(rs_long=0, rs_short=0), dict(rs_long=2, rs_short=2), dict(rs_long=3, rs_short=3)]),
    ("risk", [dict(risk_mode="risk", risk_usd=r) for r in (45, 60, 75, 100)] +
             [dict(risk_mode="notional", notional_usd=n) for n in (5000, 7500, 10000, 12000)]),
]


def pick_best(results):
    """DD-disciplined: among configs with enough trades and DD<=35%, take the highest
    net. Drawdown is a hard constraint (Rich's reject bar is 30%), not a tiebreaker —
    we never chase net into a blown account."""
    valid = [(o, m) for o, m in results if m and m["n"] >= 80]
    capped = [(o, m) for o, m in valid if m["maxdd_pct"] <= 35 and m["net"] > 0]
    if capped:
        return max(capped, key=lambda x: x[1]["net"])[0]
    if valid:                      # nothing under the DD cap -> take the lowest-DD positive, else lowest DD
        pos = [(o, m) for o, m in valid if m["net"] > 0]
        pool = pos if pos else valid
        return min(pool, key=lambda x: x[1]["maxdd_pct"])[0]
    return {}


def main():
    print("=" * 100 + "\nSTAGE 1 — staged greedy search (full universe, Lighter 0-fee)\n" + "=" * 100)
    champ = dict(BASE)
    for knob, variants in STAGES:
        print(f"\n-- {knob} (base so far carried) --")
        results = []
        for v in variants:
            over = {**champ, **v}
            m, _ = S.m_of(S.COINS, over)
            results.append((v, m))
            tag = ",".join(f"{k}={vv}" for k, vv in v.items())
            S.line(tag, m)
        best = pick_best(results)
        champ.update(best)
    print("\n>>> CHAMPION:", champ)

    S.report(S.COINS, champ, "CHAMPION (full universe)")
    print("\n  STARTER-5 only:")
    S.line("starter-5", S.m_of(S.STARTER, champ)[0])

    print("\n" + "=" * 100 + "\nSTAGE 4 — VALIDATION\n" + "=" * 100)
    mi, mo = S.is_oos(S.COINS, champ, 0.70)
    print("  70/30 split:"); S.line("in-sample 70%", mi); S.line("out-sample 30%", mo)

    print("\n  walk-forward (90d train / 30d test, roll 30d) over a focused grid:")
    wf_grid = [dict(champ, **dict(zip(("exit_model", "atr_stop", "atr_min_pct", "adx_min"), v)))
               for v in itertools.product(("B", "C", "D"), (1.5, 2.0), (0.6, 0.8), (0, 20))]
    mwf, picks = S.walk_forward(S.COINS, wf_grid)
    S.line("WALK-FORWARD (stitched OOS)", mwf)
    print("    picks/window:", [{k: o[k] for k in ("exit_model", "atr_stop", "atr_min_pct", "adx_min")} for o in picks])

    print("\n  slippage (champion, full window):")
    for sp in (0.02, 0.05, 0.10):
        S.line(f"slip {sp}%", S.m_of(S.COINS, champ, S.E.Costs(slippage_pct=sp))[0])
    S.line("BloFin fees", S.m_of(S.COINS, champ, S.BLOFIN)[0])

    print("\n  rejection checks (champion):")
    full_tr = S.sim(S.COINS, champ)[0]
    checks = S.rejection_checks(S.COINS, champ, mo, full_tr)
    for name, (passed, detail) in checks.items():
        print(f"      [{'PASS' if passed else 'FAIL'}] {name:<24} {detail}")
    # $100/day plausibility
    if mo:
        print(f"\n  $100/day check: OOS $/day=${mo['avg_daily']:.1f}, WF $/day=${mwf['avg_daily']:.1f} "
              f"(target $100). Plausible: {'MAYBE' if mwf['avg_daily'] > 30 else 'NO'}")


if __name__ == "__main__":
    main()
