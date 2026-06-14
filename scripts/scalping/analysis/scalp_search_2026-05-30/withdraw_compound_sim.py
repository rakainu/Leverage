"""Discussion sim (no deploy). Q1: daily $50/$100 withdrawals when profit is there.
Q2: does compounding matter at $3200/$3600 start?

Sizing = live config: each trade $500 margin x 10x = $5000 notional (FIXED = current
live behavior, no compounding). Cooldown(3,180m) stream, validated 5-coin set
(SOL/ETH/ZEC/HYPE/BTC) as proxy; live 6-coin basket (ZEC->BNB+XMR) trades ~20% more,
so $ figures run ~20% higher live. Per-trade liquidation modeled (mae>=liq => -margin)."""
import numpy as np
import pandas as pd
import common as K
import news_rip_sweep as N

NOTIONAL = 5000.0          # $500 margin x 10x
LEV = 10.0
LIQ = (1.0 / LEV) * (1 - 0.005)
MARGIN = NOTIONAL / LEV    # $500

pooled, _ = N.build_pool(dict(N.BASE), K.LIGHTER, K.COINS, (3, 180))   # cooldown(3,180m)
pooled = [t for t in pooled]
weeks = K.weeks_of(K.load("SOL", "15m"), "15m")
days_span = (pooled[-1]["et"].date() - pooled[0]["et"].date()).days + 1


def simulate(start_eq, compound=False, withdraw=0.0, protect_principal=True):
    eq = start_eq; pocket = 0.0; peak = start_eq; maxdd = 0.0
    liqs = 0; wdays = 0; skip = 0
    daily = {}
    last_date = pooled[0]["et"].date()

    def do_withdraw():
        nonlocal eq, pocket, wdays, skip
        if withdraw <= 0:
            return
        avail = eq - start_eq if protect_principal else eq
        if avail >= withdraw:
            eq -= withdraw; pocket += withdraw; wdays += 1
        else:
            skip += 1

    for t in pooled:
        d = t["et"].date()
        if d != last_date:
            do_withdraw()
            last_date = d
        notl = NOTIONAL * (eq / start_eq) if compound else NOTIONAL
        if t["mae"] >= LIQ:
            pnl = -notl / LEV; liqs += 1
        else:
            pnl = t["r"] * notl
        eq += pnl
        daily[d] = daily.get(d, 0.0) + pnl
        peak = max(peak, eq); maxdd = max(maxdd, peak - eq)
    do_withdraw()  # final day

    dv = np.array(list(daily.values()))
    return dict(end=eq, pocket=pocket, profit=eq - start_eq + pocket,
                maxdd=maxdd, liqs=liqs, wdays=wdays, skip=skip,
                day_avg=dv.mean(), day_med=float(np.median(dv)),
                day_pos=100 * (dv > 0).mean(), day_worst=dv.min(), day_best=dv.max(),
                ndays=len(dv))


def line(label, r):
    print(f"{label:30} end=${r['end']:>9,.0f}  pocket=${r['pocket']:>7,.0f}  "
          f"totalP=${r['profit']:>9,.0f}  maxDD=${r['maxdd']:>7,.0f}  liq={r['liqs']}")


print(f"cooldown(3,180m) | 5-coin proxy | ${NOTIONAL:.0f} notional/trade (${MARGIN:.0f}m x{LEV:.0f}) "
      f"| {len(pooled)} trades / {weeks:.1f}wk / {days_span} days")
b = simulate(3600, compound=False)
print(f"\nNO WITHDRAWAL, $3600 start, FIXED notional (current live):")
print(f"  net profit ${b['profit']:,.0f}  (+{100*b['profit']/3600:.0f}%)  end ${b['end']:,.0f}")
print(f"  per-week ${b['profit']/weeks:,.0f}   per-trading-day avg ${b['day_avg']:,.0f}  median ${b['day_med']:,.0f}")
print(f"  trading days {b['ndays']}  positive {b['day_pos']:.0f}%  best ${b['day_best']:,.0f}  worst ${b['day_worst']:,.0f}  maxDD ${b['maxdd']:,.0f}")

print(f"\nQ1 — DAILY WITHDRAWAL (only skim profit above the $3600 principal):")
line("no withdrawal", b)
for w in (50, 100):
    r = simulate(3600, compound=False, withdraw=w)
    line(f"withdraw ${w}/day", r)
    print(f"     -> withdrew on {r['wdays']}/{r['wdays']+r['skip']} days "
          f"(skipped {r['skip']} no-profit days), cash taken ${r['pocket']:,.0f}, "
          f"account left ${r['end']:,.0f}")

print(f"\nQ2 — COMPOUNDING vs FIXED notional (no withdrawal):")
for start in (3200, 3600):
    f = simulate(start, compound=False)
    c = simulate(start, compound=True)
    print(f"  start ${start}:  FIXED end ${f['end']:>10,.0f} (+{100*f['profit']/start:.0f}%)   "
          f"COMPOUND end ${c['end']:>13,.0f} (+{100*c['profit']/start:.0f}%)   "
          f"x{c['end']/f['end']:.1f} difference")

print(f"\nQ1+Q2 — compounding WITH $50/day withdrawal, $3600 start:")
r = simulate(3600, compound=True, withdraw=50)
print(f"  end ${r['end']:,.0f}  cash taken ${r['pocket']:,.0f}  total ${r['profit']:,.0f}  "
      f"withdrew {r['wdays']} days / skipped {r['skip']}")
