"""CAMPAIGN STEP 2 — ATR/volatility-scaled exits vs the fixed-$ ladder.
Hypothesis: a $60 stop is tight on a high-vol coin and loose on a low-vol one.
Sizing each rung to the coin's own ATR (in $ at the 250@30x notional) should fit
the payoff better than flat dollars. Test on the fixed sandbox {XRP,ZEC,DOGE}.

Fixed-$ baseline = current winner SL60/BE25/act25/dist8.
ATR ladder       = each rung = k * coin_ATR$ (per-coin), sweep the k multipliers.

Entry B, 0.06% slip, 300d, pooled, with IS/OOS. Run: ../venv/Scripts/python.exe step2_atr.py
"""
import numpy as np, pandas as pd
import entry_grid as G
import entry_v2_search as EV
from engine import fetch_ohlcv
from zec_v3_realistic import generate_v3_signals, apply_entry_filter
from exit_apex_wide import make_p

DAYS = 300
SANDBOX = ["XRP-USDT", "ZEC-USDT", "DOGE-USDT"]
FILT = G.mk_filter(True, True)
GAP = 0.0005
ENT = ("B", 0.10, 9, 0.08)
SLIP = 0.0006
NOTIONAL = 250 * 30
FIXED = dict(sl=60, be=25, act=25, lock=20, dist=8, tp=2.0)


def atr_usd(df, period=14):
    """median ATR as $ at the trade notional (ATR% of price * notional)."""
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([(h-l), (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    return float((atr / c).median() * NOTIONAL)


def trades(df, p):
    G.set_globals(ENT[1], ENT[2], ENT[3])
    t = EV.run_reclaim_gap(df, p, FILT, gap_cap=GAP)
    return t if (t is not None and not t.empty) else pd.DataFrame()


def main():
    dfs, atrs = {}, {}
    for c in SANDBOX:
        df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                         days_back=DAYS, exchange="blofin", cache=True, verbose=False)
        df = apply_entry_filter(generate_v3_signals(df.copy()))
        dfs[c] = df; atrs[c] = atr_usd(df)
    print("per-coin median ATR$ at 250@30x notional:")
    for c in SANDBOX:
        print(f"  {c:10} ATR$ = {atrs[c]:6.1f}  (fixed $60 SL = {60/atrs[c]:.1f}x ATR)")

    H = DAYS/2.0

    def pool(make_p_for_coin, lo=None, hi=None):
        parts = []
        for c in SANDBOX:
            t = trades(dfs[c], make_p_for_coin(c))
            if lo is not None and not t.empty:
                ts = pd.to_datetime(t["entry_ts"], utc=True)
                t = t[(ts >= lo) & (ts < hi)]
            if not t.empty:
                parts.append(t)
        return G.kpis(pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(),
                      DAYS if lo is None else H)

    # data range for IS/OOS
    t0 = max(dfs[c].index[0] for c in SANDBOX)
    t1 = min(dfs[c].index[-1] for c in SANDBOX)
    mid = t0 + (t1 - t0)/2

    print(f"\n{'='*86}\nSTEP 2: ATR-SCALED vs FIXED-$ (pooled XRP+ZEC+DOGE, 0.06% slip, {DAYS}d)\n{'='*86}")
    print(f"{'config':30} {'n':>5} {'net$':>8} {'PF':>6} {'WR':>5} {'maxDD':>7} {'t/wk':>5} | "
          f"{'IS_PF':>6} {'OOS_PF':>6}")

    # fixed-$ baseline
    fx = lambda c: make_p(**FIXED, slip=SLIP)
    kf = pool(fx); ki = pool(fx, t0, mid); ko = pool(fx, mid, t1)
    print(f"{'FIXED $ (SL60/BE25/d8)':30} {kf['n']:>5d} {kf['net']:>8.0f} {kf['pf']:>6.2f} "
          f"{kf['wr']:>5.1f} {kf['dd']:>7.0f} {kf['tpw']:>5.1f} | {ki['pf']:>6.2f} {ko['pf']:>6.2f}")
    print("-"*86)

    # ATR sweep
    best = None
    for k_sl in [3, 4, 5, 6]:
        for k_dist in [0.4, 0.6, 0.8, 1.0]:
            for k_act in [1.5, 2.0]:
                def mk(c, k_sl=k_sl, k_dist=k_dist, k_act=k_act):
                    a = atrs[c]
                    return make_p(sl=k_sl*a, be=k_act*a, act=k_act*a,
                                  lock=k_act*a, dist=k_dist*a, tp=2.0, slip=SLIP)
                k = pool(mk)
                rec = (k["pf"], k["net"])
                if best is None or (k["pf"] >= 1.10 and k["net"] > best[1]["net"]):
                    best = ((k_sl, k_dist, k_act), k)
    # print top ATR config + a few rows around it
    print("ATR sweep (rungs = k*ATR$ per coin) — best by net w/ PF>=1.10:")
    for k_sl in [3, 4, 5, 6]:
        for k_dist in [0.6, 1.0]:
            k_act = 2.0
            def mk(c, k_sl=k_sl, k_dist=k_dist, k_act=k_act):
                a = atrs[c]
                return make_p(sl=k_sl*a, be=k_act*a, act=k_act*a, lock=k_act*a,
                              dist=k_dist*a, tp=2.0, slip=SLIP)
            k = pool(mk); ki = pool(mk, t0, mid); ko = pool(mk, mid, t1)
            star = " <-BEST" if best and best[0] == (k_sl, k_dist, k_act) else ""
            print(f"{'ATR sl%dx be/act2x d%.1fx'%(k_sl,k_dist):30} {k['n']:>5d} {k['net']:>8.0f} "
                  f"{k['pf']:>6.2f} {k['wr']:>5.1f} {k['dd']:>7.0f} {k['tpw']:>5.1f} | "
                  f"{ki['pf']:>6.2f} {ko['pf']:>6.2f}{star}")

    bk, bm = best
    print(f"\nBEST ATR config: sl={bk[0]}x be/act={bk[2]}x dist={bk[1]}x ATR$  -> "
          f"net {bm['net']:.0f} / PF {bm['pf']:.2f} / DD {bm['dd']:.0f} / {bm['tpw']:.1f} t/wk")
    print(f"FIXED-$ baseline                                  -> "
          f"net {kf['net']:.0f} / PF {kf['pf']:.2f} / DD {kf['dd']:.0f} / {kf['tpw']:.1f} t/wk")
    print("\nverdict: adopt ATR only if it beats fixed-$ on net/PF AND holds IS/OOS.")


if __name__ == "__main__":
    main()
