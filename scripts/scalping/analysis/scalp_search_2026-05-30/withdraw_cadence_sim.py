"""Discussion sim (no deploy). Q1: $50/day vs $250/week — which nets more / handles
the up-and-down better. Q2: capped compounding — grow the account to a realistic
size, trade at that size, skim the surplus, keep trading.

Sizing = live: $5000 notional/trade ($500m x10). cooldown(3,180m), 5-coin proxy
(live 6 coins ~+20%). Per-trade liquidation modeled."""
import numpy as np
import common as K
import news_rip_sweep as N

NOTIONAL = 5000.0; LEV = 10.0; LIQ = (1.0 / LEV) * (1 - 0.005)
pooled, _ = N.build_pool(dict(N.BASE), K.LIGHTER, K.COINS, (3, 180))
weeks = K.weeks_of(K.load("SOL", "15m"), "15m")
START = 3600.0


def pnl_of(t, notl):
    return (-notl / LEV) if t["mae"] >= LIQ else t["r"] * notl


def fixed_withdraw(cadence, amount, start=START):
    """Fixed notional, skim `amount` each day/week only when equity is above principal."""
    eq = start; pocket = 0.0; peak = start; maxdd = 0.0; took = 0; skip = 0
    key = (lambda t: t["et"].date()) if cadence == "day" else (lambda t: t["et"].isocalendar()[:2])
    last = key(pooled[0])

    def maybe():
        nonlocal eq, pocket, took, skip
        if eq - start >= amount:
            eq -= amount; pocket += amount; took += 1
        else:
            skip += 1
    for t in pooled:
        k = key(t)
        if k != last:
            maybe(); last = k
        eq += pnl_of(t, NOTIONAL)
        peak = max(peak, eq); maxdd = max(maxdd, peak - eq)
    maybe()
    return dict(pocket=pocket, end=eq, total=pocket + (eq - start), maxdd=maxdd, took=took, skip=skip)


def capped_compound(cap_mult, start=START):
    """Notional scales with equity up to cap_mult x base, then surplus above the
    target equity (cap_mult x start) is skimmed continuously. Account holds at the
    target => strategy keeps trading full-size forever."""
    T = cap_mult * start
    eq = start; pocket = 0.0; reached = None; tdays = None
    d0 = pooled[0]["et"].date()
    for t in pooled:
        notl = min(NOTIONAL * (eq / start), NOTIONAL * cap_mult)
        eq += pnl_of(t, notl)
        if eq > T:
            if reached is None:
                reached = t["et"].date(); tdays = (reached - d0).days
            pocket += eq - T; eq = T
    # weekly income after reaching T = total skimmed / weeks-after-target
    wk_after = max(0.01, weeks - (tdays or 0) / 7)
    return dict(T=T, notl_cap=NOTIONAL * cap_mult, days_to_T=tdays, pocket=pocket,
                end=eq, wk_income=pocket / wk_after)


print(f"cooldown(3,180m) | $5000 notl/trade | {len(pooled)} trades / {weeks:.1f}wk | start ${START:.0f}")
print(f"strategy makes ~${619:.0f}/wk (~$89/day) at fixed size.\n")

print("Q1 — WITHDRAWAL CADENCE (fixed size, skim only real profit):")
d = fixed_withdraw("day", 50)
w = fixed_withdraw("week", 250)
w350 = fixed_withdraw("week", 350)
print(f"  $50/day  -> cash ${d['pocket']:,.0f}  account left ${d['end']:,.0f}  "
      f"(took {d['took']} days, skipped {d['skip']})  total value ${d['total']:,.0f}")
print(f"  $250/week-> cash ${w['pocket']:,.0f}  account left ${w['end']:,.0f}  "
      f"(took {w['took']} wks, skipped {w['skip']})  total value ${w['total']:,.0f}")
print(f"  $350/week-> cash ${w350['pocket']:,.0f}  account left ${w350['end']:,.0f}  "
      f"(took {w350['took']} wks, skipped {w350['skip']})   [= $50/day equivalent]")

print("\nQ2 — CAPPED COMPOUNDING (grow to a realistic size, then skim surplus, keep trading):")
for cap in (2, 3, 5):
    r = capped_compound(cap)
    print(f"  cap {cap}x (account grows ${START:.0f}->${r['T']:.0f}, trade notl ${r['notl_cap']:,.0f}): "
          f"reached in {r['days_to_T']}d, then skims ${r['wk_income']:,.0f}/wk -> "
          f"total cash ${r['pocket']:,.0f}, account holds ${r['end']:,.0f} (still trading)")
