"""Final views: robustness, sample-size signals, $80 conservative option."""
import csv
from collections import defaultdict
from statistics import median, mean

with open('/tmp/sweep_results.csv') as f:
    rows = list(csv.DictReader(f))


def resolved(r):
    return (int(r['n']) - int(r['unresolved'])) / int(r['n']) >= 0.70


# Robustness: at 30x, what fraction of configs are profitable per symbol?
print('=' * 80)
print('ROBUSTNESS — % of 30x configs that are profitable')
print('=' * 80)
for sym in ('ALL', 'SOL-USDT', 'ZEC-USDT'):
    all_rs = [r for r in rows if r['symbol'] == sym and r['leverage'] == '30' and resolved(r)]
    pos = [r for r in all_rs if float(r['net_pnl']) > 0]
    if all_rs:
        nets = [float(r['net_pnl']) for r in all_rs]
        print(f"  {sym:>10}: {len(pos):>3}/{len(all_rs):>3} positive ({len(pos)/len(all_rs)*100:.0f}%) | "
              f"median net=${median(nets):+.2f} | best=${max(nets):+.2f} | worst=${min(nets):+.2f}")


# Conservative margin views ($80, low-DD focus)
print()
print('=' * 80)
print('CONSERVATIVE — $80 margin @ 30x')
print('=' * 80)
sol80_30 = [r for r in rows if r['symbol'] == 'SOL-USDT' and r['leverage'] == '30'
            and r['margin'] == '80' and resolved(r)]
zec80_30 = [r for r in rows if r['symbol'] == 'ZEC-USDT' and r['leverage'] == '30'
            and r['margin'] == '80' and resolved(r)]
all80_30 = [r for r in rows if r['symbol'] == 'ALL' and r['leverage'] == '30'
            and r['margin'] == '80' and resolved(r)]


def show_top(rs, label, top=5):
    rs = sorted(rs, key=lambda r: float(r['net_pnl']), reverse=True)
    print(f"\n  {label}")
    for r in rs[:top]:
        print(f"    net=${float(r['net_pnl']):+.2f}  wr={r['wr']}  "
              f"sl=${r['sl']} be=${r['be']} lock=${r['lock_amt']}@+${r['lock_act']} "
              f"trail_jump@+${r['trail_act']} (locks ${int(r['trail_start'])-int(r['trail_dist'])}) "
              f"trail_start=+${r['trail_start']} dist=${r['trail_dist']}  "
              f"DD=${r['max_dd']}")


show_top(all80_30, 'ALL @ 80mar/30x', 5)
show_top(sol80_30, 'SOL @ 80mar/30x', 5)
show_top(zec80_30, 'ZEC @ 80mar/30x', 5)


# Comparison: live config vs proposed
print()
print('=' * 80)
print('CURRENT vs RECOMMENDED — sim PnL over 154 trades')
print('=' * 80)


def find(margin, lev, sl, be, la, lm, ta, ts, td, sym='ALL'):
    for r in rows:
        if (r['symbol'] == sym and r['margin'] == str(margin) and r['leverage'] == str(lev)
            and r['sl'] == str(sl) and r['be'] == str(be) and r['lock_act'] == str(la)
            and r['lock_amt'] == str(lm) and r['trail_act'] == str(ta)
            and r['trail_start'] == str(ts) and r['trail_dist'] == str(td)):
            return r
    return None


# Live (closest grid): margin=100, lev=30, sl=15, be=15, la=20, lm=15, ta=25, ts=30, td=10
live_all = find(100, 30, 15, 15, 20, 15, 25, 30, 10, 'ALL')
live_sol = find(100, 30, 15, 15, 20, 15, 25, 30, 10, 'SOL-USDT')
live_zec = find(100, 30, 15, 15, 20, 15, 25, 30, 10, 'ZEC-USDT')

# Optimum ALL @ 30x: 100, 30, 25, 25, 30, 20, 40, 45, 10
opt_all = find(100, 30, 25, 25, 30, 20, 40, 45, 10, 'ALL')
opt_all_sol = find(100, 30, 25, 25, 30, 20, 40, 45, 10, 'SOL-USDT')
opt_all_zec = find(100, 30, 25, 25, 30, 20, 40, 45, 10, 'ZEC-USDT')

# Optimum SOL @ 30x: 130, 30, 15, 15, 20, 15, 25, 30, 10
opt_sol = find(130, 30, 15, 15, 20, 15, 25, 30, 10, 'SOL-USDT')
# Optimum ZEC @ 30x: 130, 30, 20, 25, 30, 25, 35, 40, 10
opt_zec = find(130, 30, 20, 25, 30, 25, 35, 40, 10, 'ZEC-USDT')


def fmt(r):
    if r is None:
        return "(not found in grid)"
    return f"${float(r['net_pnl']):+.2f} | wr={r['wr']} | DD=${r['max_dd']} | n={r['n']} | mix(sl/be/tr/un)={r['sl_hits']}/{r['be_hits']}/{r['trail_exits']}/{r['unresolved']}"


print('\n  -- Single config across both symbols --')
print(f'  LIVE (sl=15) ALL: {fmt(live_all)}')
print(f'  LIVE (sl=15) SOL: {fmt(live_sol)}')
print(f'  LIVE (sl=15) ZEC: {fmt(live_zec)}')
print()
print(f'  OPT-ALL (sl=25) ALL: {fmt(opt_all)}')
print(f'  OPT-ALL (sl=25) SOL: {fmt(opt_all_sol)}')
print(f'  OPT-ALL (sl=25) ZEC: {fmt(opt_all_zec)}')
print()
print('  -- Per-symbol custom --')
print(f'  OPT-SOL (sl=15) SOL: {fmt(opt_sol)}')
print(f'  OPT-ZEC (sl=20) ZEC: {fmt(opt_zec)}')

# Combined per-symbol total
if opt_sol and opt_zec:
    combined = float(opt_sol['net_pnl']) + float(opt_zec['net_pnl'])
    print(f'  TOTAL per-symbol custom: ${combined:+.2f}')

# Live actual reference
print()
print('  Reference: live ACTUAL = +$56.70 across 154 trades')


# Daily/weekly/monthly projection
print()
print('=' * 80)
print('PROJECTION — extrapolating over 19-day window (sim total / 19)')
print('=' * 80)
days = 19
print(f"\n  Live (actual):                  ${56.70/days:>+.2f}/day")
if live_all:
    print(f"  Live config (sim):              ${float(live_all['net_pnl'])/days:>+.2f}/day")
if opt_all:
    print(f"  Single ALL-optimum config (sim): ${float(opt_all['net_pnl'])/days:>+.2f}/day")
if opt_sol and opt_zec:
    print(f"  Per-symbol custom (sim):        ${(float(opt_sol['net_pnl'])+float(opt_zec['net_pnl']))/days:>+.2f}/day")
