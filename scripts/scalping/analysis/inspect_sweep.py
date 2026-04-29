"""Inspect /tmp/sweep_results.csv with multiple filtered views."""
import csv

with open('/tmp/sweep_results.csv') as f:
    rows = list(csv.DictReader(f))
print('Total rows:', len(rows))


def show(rs, label, top=8):
    rs = sorted(rs, key=lambda r: float(r['net_pnl']), reverse=True)
    print()
    print('=' * 110)
    print(label)
    print('=' * 110)
    print(f"{'rank':>4} {'net':>8} {'avg':>7} {'wr':>5} {'maxdd':>8} | "
          f"{'mar':>3} {'lev':>3} {'sl':>3} {'be':>3} {'la':>3} {'lm':>3} {'ta':>3} {'ts':>3} {'td':>3} | "
          f"{'n':>3} {'sl':>3} {'be':>3} {'tr':>3} {'ur':>3}")
    for i, r in enumerate(rs[:top], 1):
        print(
            f"{i:>4} ${float(r['net_pnl']):>+7.2f} ${float(r['avg_pnl']):>+6.3f} "
            f"{float(r['wr']):>5.3f} ${float(r['max_dd']):>+7.2f} | "
            f"{r['margin']:>3} {r['leverage']:>3} {r['sl']:>3} {r['be']:>3} "
            f"{r['lock_act']:>3} {r['lock_amt']:>3} {r['trail_act']:>3} "
            f"{r['trail_start']:>3} {r['trail_dist']:>3} | "
            f"{r['n']:>3} {r['sl_hits']:>3} {r['be_hits']:>3} "
            f"{r['trail_exits']:>3} {r['unresolved']:>3}"
        )


def resolved(r):
    n = int(r['n'])
    u = int(r['unresolved'])
    return (n - u) / n >= 0.70


# 30x leverage views (the user's stated target)
all30 = [r for r in rows if r['symbol'] == 'ALL' and r['leverage'] == '30' and resolved(r)]
sol30 = [r for r in rows if r['symbol'] == 'SOL-USDT' and r['leverage'] == '30' and resolved(r)]
zec30 = [r for r in rows if r['symbol'] == 'ZEC-USDT' and r['leverage'] == '30' and resolved(r)]

show(all30, 'TOP CONFIGS @ 30x — ALL symbols', 10)
show(sol30, 'TOP CONFIGS @ 30x — SOL-USDT only', 10)
show(zec30, 'TOP CONFIGS @ 30x — ZEC-USDT only', 10)

# Constrain to user's $80-$130 margin range and SL 18-25
sol30_user = [r for r in rows if r['symbol'] == 'SOL-USDT'
              and r['leverage'] == '30'
              and 80 <= int(r['margin']) <= 130
              and 18 <= int(r['sl']) <= 25
              and resolved(r)]
show(sol30_user, 'SOL-USDT @ 30x, margin 80-130, SL 18-25', 10)

# Tightest constraint: SOL only, 30x, 100 margin, SL 20
sol30_focused = [r for r in rows if r['symbol'] == 'SOL-USDT'
                 and r['leverage'] == '30'
                 and r['margin'] == '100'
                 and resolved(r)]
show(sol30_focused, 'SOL @ 30x, margin=100', 10)

# How does CURRENT live config score?
# margin=100, lev=30, sl=13, be=15, lock_act=20, lock_amt=15, trail_act=25, trail_start=30, trail_dist=10
# but our grid only has sl in {15,18,20,22,25} so the closest is sl=15
print()
print('=' * 110)
print('CURRENT LIVE CONFIG (closest in grid: sl=15 instead of 13)')
print('=' * 110)
for r in rows:
    if (r['symbol'] == 'ALL' and r['margin'] == '100' and r['leverage'] == '30'
        and r['sl'] == '15' and r['be'] == '15' and r['lock_act'] == '20'
        and r['lock_amt'] == '15' and r['trail_act'] == '25'
        and r['trail_start'] == '30' and r['trail_dist'] == '10'):
        print(f"net=${r['net_pnl']}, wr={r['wr']}, sl_hits={r['sl_hits']}, "
              f"be={r['be_hits']}, trail={r['trail_exits']}, unr={r['unresolved']}")

# Show the top 1 config for SOL @ 30x in detail
print()
print('=' * 110)
print('PER-SYMBOL OPTIMA @ 30x leverage (best for ALL+SOL+ZEC at 30x)')
print('=' * 110)
for label, rs in (('ALL', all30), ('SOL', sol30), ('ZEC', zec30)):
    rs = sorted(rs, key=lambda r: float(r['net_pnl']), reverse=True)
    if rs:
        r = rs[0]
        print(f"\n{label} optimum (n={r['n']}):")
        print(f"  margin=${r['margin']}, leverage={r['leverage']}x")
        print(f"  SL=${r['sl']}, BE @+${r['be']}")
        print(f"  Lock ${r['lock_amt']} at +${r['lock_act']}")
        print(f"  Trail jump at +${r['trail_act']} (locks ${int(r['trail_start'])-int(r['trail_dist'])})")
        print(f"  Trail starts at +${r['trail_start']}, $${r['trail_dist']} behind peak")
        print(f"  Net: ${r['net_pnl']} over {r['n']} trades, WR={r['wr']}, MaxDD=${r['max_dd']}")
        print(f"  Exit mix: SL={r['sl_hits']}, BE-exit={r['be_hits']}, Trail={r['trail_exits']}, "
              f"Ceiling={r['ceiling_hits']}, Unresolved={r['unresolved']}")
