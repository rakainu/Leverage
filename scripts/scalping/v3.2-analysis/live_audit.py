import sqlite3, pandas as pd, numpy as np
pd.set_option('display.width', 200); pd.set_option('display.max_columns', 50)
con = sqlite3.connect('/docker/scalping-v3.1/data/bridge.db')
df = pd.read_sql_query("SELECT * FROM trade_log ORDER BY id", con)
df['opened_at'] = pd.to_datetime(df['opened_at'])
df['closed_at'] = pd.to_datetime(df['closed_at'])
df = df.sort_values('opened_at').reset_index(drop=True)

# risk_usdt per trade from initial_sl distance
def risk(row):
    if pd.isna(row['initial_sl']) or row['entry_price']<=0: return np.nan
    notional = row['margin_usdt']*row['leverage']
    return abs(row['entry_price']-row['initial_sl'])/row['entry_price']*notional
df['risk_usdt'] = df.apply(risk, axis=1)
df['R'] = df['pnl_usdt']/df['risk_usdt']

def kpi(d, label):
    if len(d)==0: 
        print(f"\n## {label}: 0 trades"); return
    w = d[d.pnl_usdt>0]; l = d[d.pnl_usdt<=0]
    net = d.pnl_usdt.sum(); gw=w.pnl_usdt.sum(); gl=-l.pnl_usdt.sum()
    pf = gw/gl if gl>0 else float('inf')
    cum = d.sort_values('opened_at').pnl_usdt.cumsum().values
    dd = (cum-np.maximum.accumulate(cum)).min()
    # consec losses
    mc=c=0
    for p in d.sort_values('opened_at').pnl_usdt.values:
        if p<=0: c+=1; mc=max(mc,c)
        else: c=0
    print(f"\n## {label}")
    print(f"  n={len(d)}  net=${net:.2f}  WR={len(w)/len(d)*100:.1f}%  PF={pf:.2f}")
    print(f"  avg_win=${w.pnl_usdt.mean():.2f}  avg_loss=${l.pnl_usdt.mean():.2f}  avg_trade=${net/len(d):.2f}")
    print(f"  avg_R={d.R.mean():.2f}  median_R={d.R.median():.2f}  maxDD=${dd:.2f}  maxConsecLoss={mc}")
    print(f"  gross_win=${gw:.2f}  gross_loss=${gl:.2f}")

print("="*70)
print("LIVE BLOFIN DEMO V3.1 AUDIT")
print(f"Window: {df.opened_at.min()} -> {df.opened_at.max()}")
days = (df.opened_at.max()-df.opened_at.min()).total_seconds()/86400
print(f"Span: {days:.1f} days   trades/day={len(df)/days:.2f}")
print(f"avg hold: {df.duration_secs.mean()/60:.1f} min   median hold: {df.duration_secs.median()/60:.1f} min")

kpi(df, "ALL")
kpi(df[df.symbol=='ZEC-USDT'], "ZEC only")
kpi(df[df.symbol=='SOL-USDT'], "SOL only")
kpi(df[df.side=='long'], "LONG only")
kpi(df[df.side=='short'], "SHORT only")
kpi(df[(df.symbol=='ZEC-USDT')&(df.side=='long')], "ZEC long")
kpi(df[(df.symbol=='ZEC-USDT')&(df.side=='short')], "ZEC short")

print("\n## EXIT REASON breakdown")
g = df.groupby('exit_reason').agg(n=('pnl_usdt','size'),net=('pnl_usdt','sum'),
    avg=('pnl_usdt','mean'),wins=('pnl_usdt',lambda s:(s>0).sum())).round(2)
g['WR%']=(g.wins/g.n*100).round(1)
print(g.sort_values('net',ascending=False).to_string())

print("\n## EXIT REASON x SYMBOL")
g2 = df.groupby(['symbol','exit_reason']).agg(n=('pnl_usdt','size'),net=('pnl_usdt','sum')).round(2)
print(g2.to_string())

print("\n## BEST 5 trades")
print(df.nlargest(5,'pnl_usdt')[['id','symbol','side','exit_reason','pnl_usdt','R','opened_at']].to_string(index=False))
print("\n## WORST 5 trades")
print(df.nsmallest(5,'pnl_usdt')[['id','symbol','side','exit_reason','pnl_usdt','R','opened_at']].to_string(index=False))

print("\n## Remove BEST 3 trades")
kpi(df.drop(df.nlargest(3,'pnl_usdt').index), "ALL minus best 3")
print("\n## Remove BEST 3 (ZEC only)")
z=df[df.symbol=='ZEC-USDT']
kpi(z.drop(z.nlargest(3,'pnl_usdt').index), "ZEC minus best 3")

print("\n## BY HOUR UTC (ZEC)")
z=df[df.symbol=='ZEC-USDT'].copy(); z['h']=z.opened_at.dt.hour
gh=z.groupby('h').agg(n=('pnl_usdt','size'),net=('pnl_usdt','sum'),wins=('pnl_usdt',lambda s:(s>0).sum())).round(2)
gh['WR%']=(gh.wins/gh.n*100).round(0)
print(gh.to_string())

print("\n## BY WEEKDAY (0=Mon, ZEC)")
z['dow']=z.opened_at.dt.weekday
print(z.groupby('dow').agg(n=('pnl_usdt','size'),net=('pnl_usdt','sum')).round(2).to_string())

print("\n## Cumulative equity curve (ZEC, by trade)")
z=df[df.symbol=='ZEC-USDT'].sort_values('opened_at')
print("  running net after each ZEC trade:")
print("  ", np.round(z.pnl_usdt.cumsum().values,1).tolist())
