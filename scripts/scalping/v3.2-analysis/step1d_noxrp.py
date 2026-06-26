"""Step 1 redo — XRP excluded (Rich's call), frequency front and center.
PF is ~flat across coin counts, so the choice is really frequency vs drawdown.
Show several non-XRP sets so Rich picks the count. Entry B + wide ladder, 0.06% slip, 300d."""
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


def trades(df):
    G.set_globals(ENT[1], ENT[2], ENT[3])
    t = EV.run_reclaim_gap(df, make_p(**LADDER, slip=SLIP), FILT, gap_cap=GAP)
    if t is None or t.empty:
        return pd.DataFrame()
    t = t.copy(); t["entry_ts"] = pd.to_datetime(t["entry_ts"], utc=True)
    return t


def main():
    tr, rng = {}, []
    for c in COINS:
        df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                         days_back=DAYS, exchange="blofin", cache=True, verbose=False)
        df = apply_entry_filter(generate_v3_signals(df.copy()))
        tr[c] = trades(df); rng.append((df.index[0], df.index[-1]))
    t0, t1 = max(r[0] for r in rng), min(r[1] for r in rng)
    rec0 = t1 - pd.Timedelta(days=WIN)

    def keeppct(tdf):
        s, k, v = t0, 0, 0
        while s + pd.Timedelta(days=WIN) <= t1 + pd.Timedelta(days=1):
            sub = tdf[(tdf["entry_ts"] >= s) & (tdf["entry_ts"] < s + pd.Timedelta(days=WIN))]
            if len(sub) >= 10:
                p = sub["pnl_net"].values
                pf = p[p > 0].sum()/-p[p < 0].sum() if (p < 0).any() else 9.9
                v += 1; k += int(p.sum() > 0 and pf >= 1.10)
            s += pd.Timedelta(days=STEP)
        return 100*k/v if v else 0

    def show(label, coins):
        full = pd.concat([tr[c] for c in coins], ignore_index=True)
        kf = G.kpis(full, DAYS)
        rec = G.kpis(full[full["entry_ts"] >= rec0], WIN)
        print(f"{label:26} {kf['tpw']:>5.1f} {kf['n']:>5d} {kf['net']:>8.0f} {kf['pf']:>6.2f} "
              f"{kf['dd']:>7.0f} | {keeppct(full):>5.0f}% | {rec['tpw']:>5.1f} {rec['net']:>7.0f} {rec['pf']:>6.2f}")

    NX = ["BNB-USDT", "BTC-USDT", "DOGE-USDT", "HYPE-USDT", "SOL-USDT", "ZEC-USDT"]  # all non-XRP
    print(f"\n{'='*98}\nNON-XRP COIN SETS (Entry B, wide ladder, 0.06% slip, {DAYS}d) — freq first\n"
          f"range {t0.date()}->{t1.date()}\n{'='*98}")
    print(f"{'set':26} {'t/wk':>5} {'n':>5} {'net$':>8} {'PF':>6} {'maxDD':>7} | "
          f"{'keep%':>6} | {'rec/wk':>6} {'rec_net':>7} {'rec_PF':>6}")
    show("ALL 6 non-XRP", NX)
    print("-"*98)
    show("ZEC+DOGE", ["ZEC-USDT", "DOGE-USDT"])
    show("ZEC+DOGE+BTC", ["ZEC-USDT", "DOGE-USDT", "BTC-USDT"])
    show("ZEC+DOGE+HYPE", ["ZEC-USDT", "DOGE-USDT", "HYPE-USDT"])
    show("ZEC+DOGE+SOL", ["ZEC-USDT", "DOGE-USDT", "SOL-USDT"])
    show("ZEC+DOGE+BTC+HYPE", ["ZEC-USDT", "DOGE-USDT", "BTC-USDT", "HYPE-USDT"])
    show("ZEC+DOGE+BTC+SOL+HYPE", ["ZEC-USDT", "DOGE-USDT", "BTC-USDT", "SOL-USDT", "HYPE-USDT"])
    print("\ncols: t/wk + n + net + PF + maxDD over full 300d | keep% = 90d-window stability |")
    print("rec = last 90d (forward-relevant). PF ~flat across sets -> pick = frequency vs drawdown.")


if __name__ == "__main__":
    main()
