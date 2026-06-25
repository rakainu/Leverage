"""Targeted IS/OOS confirmation of the only promising entry leads from the grid:
the overshoot0.10 / timeout9 tweak (lifted full-window PF 1.14->1.25) and a
looser-slope frequency play. Robust = positive + PF holds in BOTH halves.
Reuses entry_grid's machinery; fetch is cached so this is fast."""
import entry_grid as G

dfs = []
print(f"loading {G.DAYS}d cached...")
from engine import fetch_ohlcv
from zec_v3_realistic import generate_v3_signals, apply_entry_filter
for c in G.COINS:
    df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                     days_back=G.DAYS, exchange="blofin", cache=True, verbose=False)
    dfs.append(apply_entry_filter(generate_v3_signals(df.copy())))
dis = [d.iloc[:len(d)//2] for d in dfs]
doos = [d.iloc[len(d)//2:] for d in dfs]
H = G.DAYS / 2.0

# (gap, over, timeout, slope, sunday, body, label)
CFG = [
    (0.0005, 0.20, 6, 0.15, True, True, "LIVE (over.20 to6 slope.15)"),
    (0.0005, 0.10, 6, 0.15, True, True, "over.10 to6"),
    (0.0005, 0.10, 9, 0.15, True, True, "over.10 to9  <-Stage-B best"),
    (0.0005, 0.20, 9, 0.15, True, True, "over.20 to9"),
    (0.0005, 0.10, 12, 0.15, True, True, "over.10 to12"),
    (0.0005, 0.10, 9, 0.08, True, True, "over.10 to9 slope.08 (freq)"),
    (0.0005, 0.20, 6, 0.03, True, True, "slope.03 (freq play)"),
]
print(f"\n{'config':32s} {'IS_n':>5s} {'IS_net':>7s} {'IS_PF':>6s} {'IS_wr':>5s} | "
      f"{'OOS_n':>5s} {'OOS_net':>7s} {'OOS_PF':>6s} {'OOS_wr':>5s}  verdict")
for gap, over, to, slope, sun, body, name in CFG:
    G.set_globals(over, to, slope)
    f = G.mk_filter(sun, body)
    ki = G.run_model(dis, "M13", gap=gap, filt=f, days=H)
    ko = G.run_model(doos, "M13", gap=gap, filt=f, days=H)
    ok = ki["net"] > 0 and ko["net"] > 0 and ki["pf"] >= 1.10 and ko["pf"] >= 1.10
    v = "ROBUST" if ok else ("oos-fade" if ki["net"] > 0 >= ko["net"] else "weak")
    print(f"{name:32s} {ki['n']:>5d} {ki['net']:>7.0f} {ki['pf']:>6.2f} {ki['wr']:>5.1f} | "
          f"{ko['n']:>5d} {ko['net']:>7.0f} {ko['pf']:>6.2f} {ko['wr']:>5.1f}  {v}")
G.set_globals(G._LIVE_OVER, G._LIVE_TIMEOUT, G._LIVE_SLOPE)
