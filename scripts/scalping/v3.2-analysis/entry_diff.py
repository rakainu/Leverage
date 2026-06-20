"""Trade-by-trade diff: engine-selected entries vs live-selected entries over
the live window — and WHY they differ. Decomposes the gap into:

  matched      : engine & live both took it (same side, ~same bar)
  engine-only  : engine took a trade live MISSED  -> split into:
                   * signal-not-filled : live HAD the signal, retest/fill never
                     fired (RETEST/TIMING gap)
                   * no-signal         : live never generated it (SIGNAL-GEN gap)
  live-only    : live took a trade the engine did NOT (extra, usually losers)

Run:
    PYTHONPATH="analysis;v3.1-drafts;analysis/sweeps/2026-05-20" \
        venv/Scripts/python.exe v3.2-analysis/entry_diff.py
"""
import pandas as pd

from engine import fetch_ohlcv
from zec_v3_realistic import generate_v3_signals, apply_entry_filter
from v3_2_lab import run_bt, ExitModel, F_LIVE, base_params

LIVE = pd.read_csv("data/v32_live_trades.csv")
LIVE["opened_at"] = pd.to_datetime(LIVE["opened_at"], utc=True)
SIGN = pd.read_csv("data/v32_pending.csv")
SIGN["created_at"] = pd.to_datetime(SIGN["created_at"], utc=True)
SIGN["side"] = SIGN["action"].map({"buy": "long", "sell": "short"})
W0, W1 = LIVE["opened_at"].min(), LIVE["opened_at"].max()
COINS = sorted(LIVE["symbol"].unique())

pB = base_params(sl=82.5, fee=0.0, slip=0.0006)
MATCH = pd.Timedelta(minutes=12)      # engine bar vs live fill tolerance (~2 bars)
SIGWIN = pd.Timedelta(minutes=75)     # how far back a live signal can precede entry

tot = dict(m=0, eo=0, lo=0, eo_sig=0, eo_nosig=0)
net = dict(eng_missed=0.0, live_extra=0.0)
print(f"window {W0:%m-%d %H:%M} -> {W1:%m-%d %H:%M}\n")
print(f"{'coin':9s} {'eng':>4s} {'live':>4s} {'match':>5s} | "
      f"{'eng-only':>8s} (sig/nosig) miss$ | {'live-only':>9s} extra$")
for c in COINS:
    sig = apply_entry_filter(generate_v3_signals(
        fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m", days_back=12,
                    exchange="blofin", cache=False, verbose=False).copy()))
    eng = run_bt(sig, pB, ExitModel("trail"), F_LIVE)
    eng["et"] = pd.to_datetime(eng["entry_ts"], utc=True)
    eng = eng[(eng.et >= W0 - MATCH) & (eng.et <= W1 + MATCH)].reset_index(drop=True)
    lv = LIVE[LIVE.symbol == c].reset_index(drop=True)
    lsig = SIGN[SIGN.symbol == c]

    live_used = [False] * len(lv)
    eo_sig = eo_nosig = matched = miss = 0.0
    for _, e in eng.iterrows():
        hit = None
        for j, l in lv.iterrows():
            if not live_used[j] and l.side == e.side and abs(l.opened_at - e.et) <= MATCH:
                hit = j; break
        if hit is not None:
            live_used[hit] = True; matched += 1
        else:
            miss += e.pnl_net
            had = lsig[(lsig.side == e.side) &
                       (lsig.created_at >= e.et - SIGWIN) &
                       (lsig.created_at <= e.et + MATCH)]
            if len(had):
                eo_sig += 1
            else:
                eo_nosig += 1
    lo = lv[[not u for u in live_used]]
    tot["m"] += int(matched); tot["eo"] += int(eo_sig + eo_nosig); tot["lo"] += len(lo)
    tot["eo_sig"] += int(eo_sig); tot["eo_nosig"] += int(eo_nosig)
    net["eng_missed"] += miss; net["live_extra"] += float(lo.pnl_usdt.sum())
    print(f"{c:9s} {len(eng):4d} {len(lv):4d} {int(matched):5d} | "
          f"{int(eo_sig+eo_nosig):8d} ({int(eo_sig)}/{int(eo_nosig)})  {miss:+6.0f} | "
          f"{len(lo):9d} {lo.pnl_usdt.sum():+6.0f}")

print(f"\nTOTAL  matched={tot['m']}  engine-only={tot['eo']} "
      f"(signal-not-filled={tot['eo_sig']}, no-signal={tot['eo_nosig']})  "
      f"live-only={tot['lo']}")
print(f"  $ engine MISSED (engine-only winners not taken): {net['eng_missed']:+.0f}")
print(f"  $ live EXTRA (live-only trades engine skipped):  {net['live_extra']:+.0f}")
print("\nread: engine-only=signal-not-filled -> RETEST/FILL timing is the leak; "
      "no-signal -> SIGNAL-GEN diverges; live-only losses -> live takes trades the "
      "engine's filters reject.")
