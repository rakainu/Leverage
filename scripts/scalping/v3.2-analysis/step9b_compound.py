"""Compounding replay of the FINAL 5-coin config. Fixed sizing = $250 margin/coin
flat. Compound = margin scales with live equity (base_equity 3600 -> base margin),
capped at base x cap_mult, floored at 0 on drawdown — the Reclaim/scalper sizing
rule. Replay the time-ordered realized pnl both ways. 0.06% slip, EMA12, 300d."""
import dataclasses
import pandas as pd
import entry_grid as G
import entry_v2_search as EV
import zec_v3_realistic as Z
from engine import fetch_ohlcv
from v3_2_lab import base_params

DAYS = 300
KEEP = ["BNB-USDT", "BTC-USDT", "DOGE-USDT", "SOL-USDT", "ZEC-USDT"]
FILT = G.mk_filter(True, True)
GAP = 0.0005; SLIP = 0.0006
LAD = dict(sl=60, be=25, act=25, lock=20, dist=8, tp=2.0)
BLOCK = [3, 4, 5, 6]
BASE_EQ = 3600.0


def make_p(slip):
    p = base_params(sl=LAD["sl"], fee=0.0, slip=slip)
    return dataclasses.replace(p, margin_usdt=250.0, leverage=30.0,
        sl_loss_usdt=LAD["sl"], breakeven_usdt=LAD["be"], lock_profit_activate_usdt=LAD["act"],
        lock_profit_usdt=LAD["lock"], trail_activate_usdt=LAD["act"], trail_start_usdt=LAD["act"],
        trail_distance_usdt=LAD["dist"], tp_ceiling_pct=LAD["tp"], commission_pct=0.0, sl_slippage_pct=slip)


def replay(pnls, mode, cap=3.0):
    eq = BASE_EQ; peak = eq
    for x in pnls:
        scale = 1.0 if mode == "fixed" else max(0.0, min(eq/BASE_EQ, cap))
        eq += x * scale
        peak = max(peak, eq)
    return eq


def main():
    Z.EMA_PERIOD = 12
    p = make_p(SLIP)
    parts = []
    for c in KEEP:
        df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                         days_back=DAYS, exchange="blofin", cache=True, verbose=False)
        sig = Z.apply_entry_filter(Z.generate_v3_signals(df.copy()))
        G.set_globals(0.10, 9, 0.08)
        t = EV.run_reclaim_gap(sig, p, FILT, gap_cap=GAP)
        if t is None or t.empty:
            continue
        t = t.copy(); t["entry_ts"] = pd.to_datetime(t["entry_ts"], utc=True)
        t = t[~t["entry_ts"].dt.hour.isin(BLOCK)]
        parts.append(t)
    T = pd.concat(parts, ignore_index=True).sort_values("entry_ts")
    pnls = T["pnl_net"].values
    days = (T["entry_ts"].iloc[-1] - T["entry_ts"].iloc[0]).days
    months = days/30.0

    print(f"\nFINAL 5-coin config | {len(pnls)} trades over {days}d | base_equity ${BASE_EQ:.0f}\n{'='*64}")
    print(f"{'mode':16} {'end equity':>11} {'net$':>9} {'total%':>8} {'%/mo':>7} {'$/mo':>8}")
    for mode, cap, lbl in [("fixed", 0, "FIXED $250"), ("compound", 3.0, "COMPOUND 3x cap"),
                           ("compound", 5.0, "COMPOUND 5x cap")]:
        end = replay(pnls, mode, cap)
        net = end - BASE_EQ
        totpct = 100*net/BASE_EQ
        mopct = ((end/BASE_EQ)**(1/months) - 1)*100
        print(f"{lbl:16} {end:>11.0f} {net:>9.0f} {totpct:>7.0f}% {mopct:>6.1f}% {net/months:>8.0f}")
    print(f"\nnote: compound scales position with equity (cap x base), reinvesting gains.")
    print("per-month under compounding is the GEOMETRIC rate (it accelerates as equity grows).")
    Z.EMA_PERIOD = 9


if __name__ == "__main__":
    main()
