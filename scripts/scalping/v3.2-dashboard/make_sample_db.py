"""Generate a sample scalping-v3.2 bridge.db for previewing the dashboard
populated + 'on track' vs the engine. Deterministic (no RNG)."""
import sqlite3, sys
from datetime import datetime, timedelta, timezone

out = sys.argv[1] if len(sys.argv) > 1 else "sample_ontrack.db"
con = sqlite3.connect(out)
con.executescript("""
DROP TABLE IF EXISTS trade_log; DROP TABLE IF EXISTS pending_signals;
CREATE TABLE trade_log(id INTEGER PRIMARY KEY AUTOINCREMENT,position_id INTEGER,symbol TEXT,side TEXT,
 entry_price REAL,exit_price REAL,margin_usdt REAL,leverage REAL,initial_sl REAL,tp_ceiling REAL,
 trail_activated INTEGER,trail_high_price REAL,exit_reason TEXT,pnl_usdt REAL,pnl_pct REAL,
 opened_at TEXT,closed_at TEXT,duration_secs INTEGER);
CREATE TABLE pending_signals(id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT,action TEXT,signal_price REAL,
 created_at TEXT,expires_at TEXT,status TEXT,filled_at TEXT,fill_price REAL);
""")

# 40 trades, ~71% WR: trail wins ~+40, BE ~0, hard SL -82.5. Both sides.
# pattern repeats; net clearly positive, tracking the engine.
pat = [("short","trail_sl",44),("long","trail_sl",38),("short","sl_be",-2),("long","trail_sl",61),
       ("short","trail_sl",33),("long","sl",-82.5),("short","trail_sl",52),("long","trail_sl",29),
       ("short","sl_be",3),("long","trail_sl",47),("short","trail_sl",71),("long","sl",-82.5),
       ("short","trail_sl",36),("long","trail_sl",40),("short","trail_sl",55),("long","sl_be",-1),
       ("short","trail_sl",28),("long","trail_sl",49),("short","sl","-82.5"),("long","trail_sl",63)]
pat = pat*2  # 40
t0 = datetime(2026,6,11,2,0,tzinfo=timezone.utc)
px = 540.0
for i,(side,reason,pnl) in enumerate(pat):
    pnl=float(pnl)
    op=t0+timedelta(hours=i*3+ (i%2))
    cl=op+timedelta(minutes=12+(i%5)*4)
    entry=px+(i%7)*0.6 - (i%3)*0.4
    exitp=entry*(1+ (pnl/7500)*(1 if side=="long" else -1))
    con.execute("INSERT INTO trade_log(position_id,symbol,side,entry_price,exit_price,margin_usdt,leverage,"
        "initial_sl,tp_ceiling,trail_activated,trail_high_price,exit_reason,pnl_usdt,pnl_pct,opened_at,closed_at,duration_secs)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (i+1,"ZEC-USDT",side,round(entry,2),round(exitp,2),250,30,
         round(entry*(1-0.011 if side=="long" else 1+0.011),2),None,1 if reason=="trail_sl" else 0,
         round(exitp,2),reason,pnl,round(pnl/250*100,2),op.isoformat(),cl.isoformat(),int((cl-op).total_seconds())))

now=datetime.now(timezone.utc)
for j,(act,p) in enumerate([("buy",548.3),("sell",551.1)]):
    con.execute("INSERT INTO pending_signals(symbol,action,signal_price,created_at,expires_at,status) VALUES(?,?,?,?,?,?)",
        ("ZEC-USDT",act,p,(now-timedelta(minutes=4+j)).isoformat(),(now+timedelta(minutes=26)).isoformat(),"pending"))
con.commit(); con.close()
print("wrote",out)
