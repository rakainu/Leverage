"""CAMPAIGN STEP 1b — coin-ranking STABILITY test.
Rich's challenge: every test picks different winning coins -> is the per-coin
prune real or just this-window noise? Slide a 90-day window across ~300d of
history and watch each coin's Entry-B PF. A coin we trust should stay in keep
territory (+EV, PF>=1.1) across MOST windows, not flip.

Entry B + wide ladder, 0.06% slip (the decision metric). One trade list per coin
(warmup preserved), bucketed into rolling windows -> per-window PF/net per coin.

Run: ../venv/Scripts/python.exe step1b_stability.py 300
"""
import sys
import pandas as pd
import entry_grid as G
import entry_v2_search as EV
from engine import fetch_ohlcv
from zec_v3_realistic import generate_v3_signals, apply_entry_filter
from exit_apex_wide import make_p

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 300
COINS = G.COINS
FILT = G.mk_filter(True, True)
GAP = 0.0005
ENT = ("B", 0.10, 9, 0.08)
LADDER = dict(sl=60, be=25, act=25, lock=20, dist=8, tp=2.0)
SLIP = 0.0006
WIN_DAYS = 90
STEP_DAYS = 30


def trades(df):
    G.set_globals(ENT[1], ENT[2], ENT[3])
    t = EV.run_reclaim_gap(df, make_p(**LADDER, slip=SLIP), FILT, gap_cap=GAP)
    return t if (t is not None and not t.empty) else pd.DataFrame()


def pf_net(tdf):
    if tdf is None or len(tdf) == 0:
        return None, 0.0, 0
    p = tdf["pnl_net"].values
    w, l = p[p > 0].sum(), p[p < 0].sum()
    pf = (w / -l) if l < 0 else float("inf")
    return pf, float(p.sum()), len(p)


def main():
    print(f"loading {DAYS}d, rolling {WIN_DAYS}d window step {STEP_DAYS}d, Entry B, {SLIP*100:.2f}% slip")
    tr = {}
    rng = []
    for c in COINS:
        df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                         days_back=DAYS, exchange="blofin", cache=True, verbose=False)
        df = apply_entry_filter(generate_v3_signals(df.copy()))
        t = trades(df)
        if not t.empty:
            t = t.copy(); t["entry_ts"] = pd.to_datetime(t["entry_ts"], utc=True)
        tr[c] = t
        rng.append((df.index[0], df.index[-1]))
    t0 = max(r[0] for r in rng); t1 = min(r[1] for r in rng)
    print(f"common range {t0.date()} -> {t1.date()} ({(t1-t0).days}d)")

    # rolling window starts
    starts = []
    s = t0
    while s + pd.Timedelta(days=WIN_DAYS) <= t1 + pd.Timedelta(days=1):
        starts.append(s); s = s + pd.Timedelta(days=STEP_DAYS)
    if not starts:
        starts = [t0]
    wlabels = [f"{st.date().strftime('%m-%d')}" for st in starts]

    print(f"\n{'='*100}\nPER-COIN PF ACROSS {len(starts)} ROLLING {WIN_DAYS}d WINDOWS (start dates)\n{'='*100}")
    print(f"{'coin':5} " + " ".join(f"{w:>8}" for w in wlabels) + f" | {'keep%':>6} {'verdict':>10}")
    summary = {}
    for c in COINS:
        cells, keeps, valid = [], 0, 0
        for st in starts:
            en = st + pd.Timedelta(days=WIN_DAYS)
            sub = tr[c][(tr[c]["entry_ts"] >= st) & (tr[c]["entry_ts"] < en)] if len(tr[c]) else tr[c]
            pf, net, n = pf_net(sub)
            if n < 8:
                cells.append(f"{'·':>8}")               # too few trades to judge
                continue
            valid += 1
            if net > 0 and pf is not None and pf >= 1.10:
                keeps += 1
            cells.append(f"{pf:>8.2f}" if pf is not None else f"{'-':>8}")
        keep_pct = (100 * keeps / valid) if valid else 0
        if valid == 0:
            verdict = "thin"
        elif keep_pct >= 70:
            verdict = "STABLE-KEEP"
        elif keep_pct <= 30:
            verdict = "STABLE-DRAG"
        else:
            verdict = "UNSTABLE"
        summary[c] = (keep_pct, verdict, valid)
        print(f"{c:5} " + " ".join(cells) + f" | {keep_pct:>5.0f}% {verdict:>10}")

    print(f"\n{'='*100}\nVERDICT (keep% = share of valid windows where coin was +EV & PF>=1.10)\n{'='*100}")
    prune4 = ["BTC-USDT", "DOGE-USDT", "XRP-USDT", "ZEC-USDT"]
    for c in sorted(COINS, key=lambda x: summary[x][0], reverse=True):
        kp, v, valid = summary[c]
        was = "prune-KEEP" if c in prune4 else "prune-CUT"
        print(f"  {c:10} {kp:>5.0f}%  {v:12}  (single-window call: {was}, {valid} valid windows)")
    print("\nread: if the 4 prune-KEEP coins are STABLE-KEEP and the 3 cut coins are")
    print("STABLE-DRAG/UNSTABLE across windows, the prune is REAL. If they flip, it's noise.")


if __name__ == "__main__":
    main()
