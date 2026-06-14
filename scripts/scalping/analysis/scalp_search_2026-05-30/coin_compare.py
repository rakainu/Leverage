"""4-coin (liquid ETH/BTC/SOL/HYPE) vs 5-coin proxy, with capped compounding.
The real choice: 4 liquid coins -> higher notional cap (5x+); 6 coins (incl thin
BNB/XMR) -> more trades but cap ~3x on the thin books. Which pays more after the
grow-then-skim structure?  cooldown(3,180m), $500m x10 base = $5000 notional."""
import numpy as np
import common as K
import news_rip_sweep as N

NOTIONAL = 5000.0; LEV = 10.0; LIQ = (1.0 / LEV) * (1 - 0.005); START = 3600.0
FOUR = ["ETH", "BTC", "SOL", "HYPE"]
FIVE = K.COINS  # SOL ETH ZEC HYPE BTC (proxy for a 6-coin diversified book)
weeks = K.weeks_of(K.load("SOL", "15m"), "15m")


def pool(coins):
    p, _ = N.build_pool(dict(N.BASE), K.LIGHTER, coins, (3, 180))
    return p


def basket_edge(coins):
    p = pool(coins)
    e = N.edge(p, weeks)
    return p, e


def capped(p, cap_mult, start=START):
    T = cap_mult * start; eq = start; pocket = 0.0; tdays = None
    d0 = p[0]["et"].date()
    for t in p:
        notl = min(NOTIONAL * (eq / start), NOTIONAL * cap_mult)
        eq += (-notl / LEV) if t["mae"] >= LIQ else t["r"] * notl
        if eq > T:
            if tdays is None:
                tdays = (t["et"].date() - d0).days
            pocket += eq - T; eq = T
    wk_after = max(0.01, weeks - (tdays or 0) / 7)
    return dict(T=T, cap_notl=NOTIONAL * cap_mult, days_to_T=tdays,
                pocket=pocket, wk_income=pocket / wk_after, end=eq)


print(f"cooldown(3,180m) | base $5000 notional | {weeks:.1f}wk | start ${START:.0f}\n")
print("BASKET EDGE:")
for name, coins in (("4-coin liquid", FOUR), ("5-coin proxy~6", FIVE)):
    p, e = basket_edge(coins)
    print(f"  {name:16} {coins}")
    print(f"     n={e['n']}  t/wk={e['tpw']:.1f}  PF={e['pf']:.2f}  WR={e['wr']:.0f}%  "
          f"exp={e['exp']:+.3f}%  net$@200={e['net']:.0f}  maxDD$@200={e['dd']:.0f}")

print("\nCAPPED COMPOUNDING (grow to cap, skim surplus, keep trading):")
scen = [("4-coin @ 3x", FOUR, 3), ("4-coin @ 5x", FOUR, 5),
        ("5-coin~6 @ 3x", FIVE, 3), ("5-coin~6 @ 5x", FIVE, 5)]
P = {tuple(c): pool(c) for c in (FOUR, FIVE)}
for label, coins, cap in scen:
    r = capped(P[tuple(coins)], cap)
    print(f"  {label:16} cap${r['cap_notl']:,.0f} notl, acct->${r['T']:,.0f}: "
          f"reach {r['days_to_T']}d -> ${r['wk_income']:,.0f}/wk, total cash ${r['pocket']:,.0f}")

print("\nNOTE: live 6-coin = these 4 + BNB + XMR. Live paper so far: XMR +$330 (3rd best),"
      "\nBNB +$80 (weakest). BNB/XMR are the thin books (~$4-8M vol) that cap notional ~3x.")
