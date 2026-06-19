"""Generate a sample scalping-v3.2 bridge.db for previewing the dashboard:
7-coin closed trades + open positions + pending signals. Deterministic."""
import sqlite3, sys
from datetime import datetime, timedelta, timezone

out = sys.argv[1] if len(sys.argv) > 1 else "sample_ontrack.db"
con = sqlite3.connect(out)
con.executescript("""
DROP TABLE IF EXISTS trade_log; DROP TABLE IF EXISTS pending_signals; DROP TABLE IF EXISTS positions;
CREATE TABLE positions(id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT,side TEXT,entry_price REAL,
 initial_size REAL,current_size REAL,margin_usdt REAL,leverage REAL,tp_stage INTEGER,tp1_fill_price REAL,
 tp2_fill_price REAL,sl_order_id TEXT,tp1_order_id TEXT,tp2_order_id TEXT,tp3_order_id TEXT,sl_distance REAL,
 atr_value REAL,trail_high_price REAL,trail_active INTEGER,sl_policy TEXT,opened_at TEXT,closed_at TEXT,
 realized_pnl REAL,source TEXT);
CREATE TABLE trade_log(id INTEGER PRIMARY KEY AUTOINCREMENT,position_id INTEGER,symbol TEXT,side TEXT,
 entry_price REAL,exit_price REAL,margin_usdt REAL,leverage REAL,initial_sl REAL,tp_ceiling REAL,
 trail_activated INTEGER,trail_high_price REAL,exit_reason TEXT,pnl_usdt REAL,fee_usdt REAL DEFAULT 0,
 pnl_pct REAL,opened_at TEXT,closed_at TEXT,duration_secs INTEGER);
CREATE TABLE pending_signals(id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT,action TEXT,signal_price REAL,
 created_at TEXT,expires_at TEXT,status TEXT,filled_at TEXT,fill_price REAL);
""")

COINS=["ZEC-USDT","XRP-USDT","DOGE-USDT","SOL-USDT","BTC-USDT","BNB-USDT","HYPE-USDT"]
PX={"ZEC-USDT":505,"XRP-USDT":2.4,"DOGE-USDT":0.21,"SOL-USDT":168,"BTC-USDT":98000,"BNB-USDT":690,"HYPE-USDT":34}
pat=[("short","trail_sl",44),("long","trail_sl",38),("short","sl_be",-2),("long","trail_sl",61),
     ("short","trail_sl",33),("long","sl",-82.5),("short","trail_sl",52),("long","trail_sl",29),
     ("short","sl_be",3),("long","trail_sl",47),("short","trail_sl",71),("long","sl",-82.5),
     ("short","trail_sl",36),("long","trail_sl",40),("short","trail_sl",55),("long","sl_be",-1)]
t0=datetime(2026,6,11,2,0,tzinfo=timezone.utc)
for i in range(40):
    side,reason,pnl=pat[i%len(pat)]; pnl=float(pnl); sym=COINS[i%len(COINS)]; p=PX[sym]
    op=t0+timedelta(hours=i*3+(i%2)); cl=op+timedelta(minutes=12+(i%5)*4)
    entry=p*(1+(i%7)*0.001); exitp=entry*(1+(pnl/7500)*(1 if side=="long" else -1))
    fee=-8.98  # ~BloFin taker round-trip on $7.5k notional; 0 on a zero-fee venue
    con.execute("INSERT INTO trade_log(position_id,symbol,side,entry_price,exit_price,margin_usdt,leverage,"
      "initial_sl,tp_ceiling,trail_activated,trail_high_price,exit_reason,pnl_usdt,fee_usdt,pnl_pct,opened_at,closed_at,duration_secs)"
      " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
      (i+1,sym,side,round(entry,4),round(exitp,4),250,30,round(entry*(1-0.011),4),None,
       1 if reason=="trail_sl" else 0,round(exitp,4),reason,pnl,fee,round(pnl/250*100,2),
       op.isoformat(),cl.isoformat(),int((cl-op).total_seconds())))

# 2 open positions (current trades)
now=datetime.now(timezone.utc)
for sym,side,tr in [("ZEC-USDT","long",4),("SOL-USDT","short",1)]:
    e=PX[sym]*(0.997 if side=="long" else 1.003)
    con.execute("INSERT INTO positions(symbol,side,entry_price,initial_size,current_size,margin_usdt,leverage,"
      "tp_stage,trail_active,sl_policy,opened_at,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
      (sym,side,round(e,4),1,1,250,30,0,tr,"p2_step_stop",(now-timedelta(minutes=18)).isoformat(),"ha_v3"))

for act,sym in [("buy","XRP-USDT"),("sell","DOGE-USDT")]:
    p=PX[sym]
    con.execute("INSERT INTO pending_signals(symbol,action,signal_price,created_at,expires_at,status) VALUES(?,?,?,?,?,?)",
      (sym,act,p,(now-timedelta(minutes=3)).isoformat(),(now+timedelta(minutes=27)).isoformat(),"pending"))
con.commit(); con.close(); print("wrote",out)
