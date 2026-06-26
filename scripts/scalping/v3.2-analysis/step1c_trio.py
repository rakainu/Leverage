"""CAMPAIGN STEP 1 (final) — best THREE coins. Stability test showed only XRP+ZEC
are regime-stable. Fix that pair, test each other coin as the 3rd, rank by
pooled robustness (full 300d PF + rolling keep%) AND recency (last 90d), so the
3rd pick isn't just this-window noise. Entry B + wide ladder, 0.06% slip."""
import pandas as pd
import entry_grid as G
import entry_v2_search as EV
from engine import fetch_ohlcv
from zec_v3_realistic import generate_v3_signals, apply_entry_filter
from exit_apex_wide import make_p

DAYS = 300
COINS = G.COINS
FILT = G.mk_filter(True, True)
GAP = 0.0005
ENT = ("B", 0.10, 9, 0.08)
LADDER = dict(sl=60, be=25, act=25, lock=20, dist=8, tp=2.0)
SLIP, WIN, STEP = 0.0006, 90, 30
PAIR = ["XRP-USDT", "ZEC-USDT"]
THIRDS = ["BTC-USDT", "DOGE-USDT", "SOL-USDT", "HYPE-USDT", "BNB-USDT"]


def trades(df):
    G.set_globals(ENT[1], ENT[2], ENT[3])
    t = EV.run_reclaim_gap(df, make_p(**LADDER, slip=SLIP), FILT, gap_cap=GAP)
    if t is None or t.empty:
        return pd.DataFrame()
    t = t.copy(); t["entry_ts"] = pd.to_datetime(t["entry_ts"], utc=True)
    return t


def roll_keep(tdf, t0, t1):
    """share of 90d windows where pooled set is +EV & PF>=1.10"""
    s, keeps, valid = t0, 0, 0
    while s + pd.Timedelta(days=WIN) <= t1 + pd.Timedelta(days=1):
        sub = tdf[(tdf["entry_ts"] >= s) & (tdf["entry_ts"] < s + pd.Timedelta(days=WIN))]
        if len(sub) >= 10:
            p = sub["pnl_net"].values
            pf = p[p > 0].sum() / -p[p < 0].sum() if (p < 0).any() else 9.9
            valid += 1; keeps += int(p.sum() > 0 and pf >= 1.10)
        s += pd.Timedelta(days=STEP)
    return (100*keeps/valid) if valid else 0


def main():
    print(f"loading {DAYS}d, Entry B + wide ladder, {SLIP*100:.2f}% slip")
    tr, rng = {}, []
    for c in COINS:
        df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                         days_back=DAYS, exchange="blofin", cache=True, verbose=False)
        df = apply_entry_filter(generate_v3_signals(df.copy()))
        tr[c] = trades(df); rng.append((df.index[0], df.index[-1]))
    t0, t1 = max(r[0] for r in rng), min(r[1] for r in rng)
    rec0 = t1 - pd.Timedelta(days=WIN)            # last 90d = recency

    def pooled(coins):
        return pd.concat([tr[c] for c in coins], ignore_index=True)

    def kpi(tdf, lo=None, hi=None):
        d = tdf
        if lo is not None:
            d = d[(d["entry_ts"] >= lo) & (d["entry_ts"] < (hi or t1+pd.Timedelta(days=1)))]
        return G.kpis(d, (DAYS if lo is None else WIN))

    def show(label, coins):
        full = pooled(coins)
        kf = kpi(full); kr = kpi(full, rec0)
        keep = roll_keep(full, t0, t1)
        print(f"{label:22} {kf['n']:>5d} {kf['net']:>8.0f} {kf['pf']:>6.2f} {kf['dd']:>7.0f} "
              f"{kf['tpw']:>5.1f} | {keep:>5.0f}% | {kr['n']:>4d} {kr['net']:>7.0f} {kr['pf']:>6.2f}")

    print(f"\n{'='*92}\nBEST-3 SEARCH: XRP+ZEC (stable core) + each candidate 3rd\n"
          f"common range {t0.date()}->{t1.date()}\n{'='*92}")
    print(f"{'set':22} {'n':>5} {'full_net':>8} {'PF':>6} {'maxDD':>7} {'t/wk':>5} | "
          f"{'keep%':>6} | {'rec_n':>5} {'rec_net':>7} {'rec_PF':>6}")
    show("XRP+ZEC (pair only)", PAIR)
    print("-"*92)
    for third in THIRDS:
        show(f"XRP+ZEC+{third.split('-')[0]}", PAIR + [third])
    print("\ncols: full = whole 300d | keep% = share of 90d windows pooled set holds (+EV,PF>=1.10)")
    print("rec = last 90d (recency / forward-relevant). Best 3rd = high keep% AND strong recent.")


if __name__ == "__main__":
    main()
