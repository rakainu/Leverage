"""CRITICAL: the engine enters at the exact EMA9 value (idealized, unfillable).
Test how much edge survives realistic entry slippage. This is the V3 phantom-fill
issue. Run on BOTH the 52k full sample and the fetched live window."""
import sys, json, time, urllib.request
import numpy as np, pandas as pd
sys.path.insert(0, 'strategies')
from zec_v3_realistic import (generate_v3_signals, apply_entry_filter, _check_retest,
    RETEST_TIMEOUT_BARS, MIN_SLOPE_PCT, simulate_trade, pnl_at_price)
from v3_2_lab import kpis, ExitModel, F_LIVE, base_params, span_days, load_and_signal, _simulate

def run_bt_slip(df, p, em, filters, entry_slip_pct=0.0, allowed_sides=("long","short"), max_la=288):
    buy=df["buy_sig"].values; sell=df["sell_sig"].values
    o=df["Open"].values.astype(float); h=df["High"].values.astype(float)
    l=df["Low"].values.astype(float); c=df["Close"].values.astype(float)
    ts=df.index; adx=df["adx"].values; ba=df["body_atr_ratio"].values
    slope=df["slope_pct"].values; ema=df["ema9"].values; n=len(df)
    trades=[]; pending=[]; blocked=-1
    for i in range(n):
        np_=[]
        for si,side in pending:
            if i-si>RETEST_TIMEOUT_BARS: continue
            if not _check_retest(side, ema[i], l[i], h[i]): np_.append((si,side)); continue
            if abs(slope[i])<MIN_SLOPE_PCT: np_.append((si,side)); continue
            if i<=blocked: np_.append((si,side)); continue
            if filters is not None:
                _a=float(adx[i]) if not np.isnan(adx[i]) else 0.0
                if not filters.passes(ts[i], float(slope[i]), float(ba[i]), _a): continue
            if side not in allowed_sides: continue
            # ENTRY SLIP: fill worse than the ideal EMA9 by entry_slip_pct
            base_e=float(ema[i])
            entry=base_e*(1+entry_slip_pct) if side=="long" else base_e*(1-entry_slip_pct)
            je=min(i+1+max_la,n)
            bars=[(int(ts[j].timestamp()),o[j],h[j],l[j],c[j]) for j in range(i+1,je)]
            pnl,reason,dur=_simulate(side,entry,bars,p,em)
            notl=p.margin_usdt*p.leverage; pct=pnl/notl
            ep=entry*(1+pct) if side=="long" else entry*(1-pct)
            fee=(notl+(ep/entry)*notl)*p.commission_pct
            trades.append(dict(idx=i,side=side,entry_ts=ts[i],entry_price=entry,
                exit_reason=reason,pnl_usdt=pnl,pnl_net=pnl-fee,duration_bars=dur,
                hour_utc=ts[i].hour,weekday=ts[i].weekday(),
                adx_at_entry=float(adx[i]) if not np.isnan(adx[i]) else 0.0,
                body_atr_ratio=float(ba[i]),slope_pct=float(slope[i])))
            blocked=i+max(1,dur)
            np_=[(s,sd) for (s,sd) in np_ if sd!=side]
        pending=np_
        if buy[i]: pending.append((i,"long"))
        if sell[i]: pending.append((i,"short"))
    return pd.DataFrame(trades)

def fetch_binance(symbol="ZECUSDT", interval="5m", start="2026-05-14", end="2026-06-17"):
    s=int(pd.Timestamp(start,tz="UTC").timestamp()*1000); e=int(pd.Timestamp(end,tz="UTC").timestamp()*1000)
    rows=[]; cur=s
    while cur<e:
        u=f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&startTime={cur}&limit=1000"
        with urllib.request.urlopen(u,timeout=30) as r: data=json.load(r)
        if not data: break
        rows+=data; cur=data[-1][0]+1
        if len(data)<1000: break
        time.sleep(0.2)
    df=pd.DataFrame(rows,columns=["t","Open","High","Low","Close","v","ct","q","n","tb","tq","ig"])
    df["dt"]=pd.to_datetime(df["t"],unit="ms",utc=True)
    df=df.set_index("dt")[["Open","High","Low","Close"]].astype(float)
    return df[~df.index.duplicated()].sort_index()

for label, dfgen in (("LIVE-WINDOW (May14-Jun16)", lambda: apply_entry_filter(generate_v3_signals(fetch_binance()))),
                     ("FULL 52k (Nov-May)", lambda: load_and_signal())):
    df=dfgen(); days=span_days(df)
    print(f"\n{'='*60}\n{label}: {len(df)} bars, {days:.0f} days\n{'='*60}")
    for slip in (0.0, 0.0005, 0.0010, 0.0015, 0.0020):
        p=base_params(sl=82.5, fee=0.0, slip=0.0006)
        t=run_bt_slip(df,p,ExitModel("trail"),F_LIVE,entry_slip_pct=slip)
        k=kpis(t,p,days)
        print(f"  entry_slip={slip*100:.2f}%  net=${k['net']:>8}  PF={k['PF']:<5} WR={k['WR']} n={k['n']} net/day=${k.get('net_per_day')}")
