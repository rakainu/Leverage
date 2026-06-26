"""CAMPAIGN STEP 8 — maker-limit entry vs market-at-close.
Currently the reclaim fills market at close[i] (the backtest assumes zero entry
slip — optimistic; live takes a taker fill that slips). A maker limit posts BELOW
the close (long) and fills only on a pullback within `valid` bars: better entry
price when it fills, but misses setups that run away. This models the fill
tradeoff; the LIVE slippage saving (entry slip ~0.06% -> 0) is on top and not in
these numbers. Carry forward: EMA12, Entry B, fixed ladder, block 3-6, both, 30x.

Run: ../venv/Scripts/python.exe step8_maker.py
"""
import pandas as pd
import entry_grid as G
import entry_v2_search as EV
import zec_v3_realistic as Z
from engine import fetch_ohlcv
from exit_apex_wide import make_p

DAYS = 300
COINS = G.COINS
FILT = G.mk_filter(True, True)
GAP = 0.0005
ENT = ("B", 0.10, 9, 0.08)
SLIP = 0.0006
LAD = dict(sl=60, be=25, act=25, lock=20, dist=8, tp=2.0)
BLOCK = [3, 4, 5, 6]
EMA = 12


def run_maker(df, p, filters, gap_cap, off_pct, valid):
    """Reclaim signal -> post resting limit at close*(1-/+off); fill on pullback
    within `valid` bars (low<=lim long / high>=lim short)."""
    a = EV._arrays(df); n = len(df)
    trades = []; pending = []; resting = []; blocked = -1
    for i in range(n):
        # 1) try to fill resting limits
        still = []
        for lim, side, at in resting:
            if i - at > valid:
                continue
            if i <= blocked:
                still.append((lim, side, at)); continue
            hit = (a["l"][i] <= lim) if side == "long" else (a["h"][i] >= lim)
            if not hit:
                still.append((lim, side, at)); continue
            dur = EV._exit_and_record(a, side, lim, i + 1, p, trades, n)
            blocked = i + max(1, dur)
        resting = still
        # 2) confirm reclaims -> post a limit
        new_p = []
        for sig_i, side in pending:
            if i - sig_i > EV.RETEST_TIMEOUT_BARS:
                continue
            touched = EV._check_retest(side, a["ema"][i], a["l"][i], a["h"][i])
            recl = (a["c"][i] > a["ema"][i]) if side == "long" else (a["c"][i] < a["ema"][i])
            if not (touched and recl and i + 1 < n):
                new_p.append((sig_i, side)); continue
            g = EV._gate(a, i, side, blocked, filters)
            if g is None:
                new_p.append((sig_i, side)); continue
            if g is False:
                continue
            if gap_cap is not None and abs(a["c"][i] - a["ema"][i]) / a["ema"][i] > gap_cap:
                continue
            lim = a["c"][i] * (1 - off_pct) if side == "long" else a["c"][i] * (1 + off_pct)
            resting.append((lim, side, i))
            new_p = [(s, sd) for (s, sd) in new_p if sd != side]
        pending = new_p
        if a["buy"][i]:
            pending.append((i, "long"))
        if a["sell"][i]:
            pending.append((i, "short"))
    return pd.DataFrame(trades)


def main():
    Z.EMA_PERIOD = EMA
    p = make_p(**LAD, slip=SLIP)
    sigs, rng = {}, []
    for c in COINS:
        df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                         days_back=DAYS, exchange="blofin", cache=True, verbose=False)
        sigs[c] = Z.apply_entry_filter(Z.generate_v3_signals(df.copy())); rng.append((sigs[c].index[0], sigs[c].index[-1]))
    t0 = max(r[0] for r in rng); t1 = min(r[1] for r in rng); mid = t0 + (t1-t0)/2; H = DAYS/2.0

    def pool(runner, lo=None, hi=None, days=DAYS):
        parts = []
        for c in COINS:
            G.set_globals(ENT[1], ENT[2], ENT[3])
            t = runner(sigs[c])
            if t is None or t.empty:
                continue
            t = t.copy(); t["entry_ts"] = pd.to_datetime(t["entry_ts"], utc=True)
            t = t[~t["entry_ts"].dt.hour.isin(BLOCK)]
            if lo is not None:
                t = t[(t["entry_ts"] >= lo) & (t["entry_ts"] < hi)]
            if not t.empty:
                parts.append(t)
        return G.kpis(pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(), days)

    market = lambda df: EV.run_reclaim_gap(df, p, FILT, gap_cap=GAP)
    base = pool(market); base_o = pool(market, mid, t1, H)
    print(f"\n{'='*84}\nSTEP 8 — MAKER-LIMIT ENTRY vs market-at-close (EMA12, all 7, valid=3 bars)\n{'='*84}")
    print(f"{'entry':22} {'n':>5} {'fill%':>6} {'net$':>8} {'PF':>6} {'maxDD':>7} {'t/wk':>5} | {'OOS_PF':>6}")
    print(f"{'MARKET @ close (now)':22} {base['n']:>5d} {'100':>6} {base['net']:>8.0f} {base['pf']:>6.2f} "
          f"{base['dd']:>7.0f} {base['tpw']:>5.1f} | {base_o['pf']:>6.2f}")
    print("-"*84)
    for off in [0.0005, 0.0010, 0.0015, 0.0025]:
        for valid in [3, 6]:
            runner = lambda df, off=off, valid=valid: run_maker(df, p, FILT, GAP, off, valid)
            k = pool(runner); ko = pool(runner, mid, t1, H)
            fillpct = 100*k['n']/base['n'] if base['n'] else 0
            print(f"{'maker off%.2f%% v%d'%(off*100,valid):22} {k['n']:>5d} {fillpct:>5.0f}% "
                  f"{k['net']:>8.0f} {k['pf']:>6.2f} {k['dd']:>7.0f} {k['tpw']:>5.1f} | {ko['pf']:>6.2f}")
    Z.EMA_PERIOD = 9
    print("\nread: maker fills at a better price but misses runners. If maker net/PF holds")
    print("near market while fill%>~60%, it's worth it — live ALSO saves ~0.06% entry slip.")


if __name__ == "__main__":
    main()
