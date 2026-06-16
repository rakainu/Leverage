# Scalping V3.2 — self-generated HA-V3 signal (deployed mirror)

V3.2 keeps the entire V3.1 strategy (EMA9-retest entry, slope/Sunday/body
filters, 5-stage trailing exit, ZEC SL $82.50, 30×) but **replaces the
TradingView "Pro V3" webhook with an in-process signal generator.**

## Why

The 2026-06-16 audit (133 live demo trades + 52k-bar engine + a same-window
replay) found:

- Live demo V3.1: **−$192, PF 0.93**, longs −$382 / shorts +$190.
- The engine on the *same* ZEC month: **+$6,157, PF 3.53**, longs the best side.
- Replaying the 103 real filled entries through a perfect exit: longs lose
  **−$217 even idealized** → the entries were bad, the exit was fine.
- Only **36%** of real Pro V3 alerts filled (123/283 expired); the real signal
  fired ~5× sparser than the engine's HA model.

Conclusion: exits/stops/filters/execution were sound; the **deployed entry
signal was the failure.** The engine's HA-V3 signal (HA-smoothed trend cross +
fakeout + ADX-range) is what produces +$19k / PF 2.80 over 181 days. V3.2
generates *that* signal in-process, removing the TV alert-expiration gap.

## What changed vs V3.1 (code)

| File | Change |
|---|---|
| `src/blofin_bridge/signals.py` | **NEW.** Pure-Python HA-V3 signal. Parity-tested bar-for-bar vs the backtest engine (`tests/test_signals.py`). |
| `src/blofin_bridge/signal_engine.py` | **NEW.** Runtime loop: fetch 5m bars → drop forming candle → `latest_signal` on last closed bar → queue pending (what `router.dispatch` did for a webhook). De-dups per bar; optional `min_adx` gate. Tested (`tests/test_signal_engine.py`). |
| `config.py` | Added `signal_source` (`webhook`\|`ha_v3`) + `signal_*` params + `signal_min_adx`. |
| `main.py` | Starts `SignalEngine` in the lifespan when `signal_source == ha_v3`. |
| `poller.py` | Unchanged from V3.1 (retest + slope/Sunday/body gates + trail). |

Everything else — exits, stops, sizing, leverage — is byte-for-byte V3.1.

## Config decisions

- **ZEC only.** SOL dropped (live −$10/30 trades of noise, no 5m backtest data).
- **`signal_min_adx: 0.0`** (off). Engine showed `min_adx=18` lifts PF 2.80→2.90
  and cuts maxDD ~30% but costs ~25% of net — exposed as a knob, not baked in,
  per Rich's "bigger profit by default" preference.
- Sizing is **fixed** (V3.1). Compounding is deferred until the live signal is
  confirmed to track the engine (then backtest the compounding curve first).

## Deploy / revert

```bash
# V3.2 lives at /docker/scalping-v3.2 on srv1370094, container scalping-v3.2,
# host port 127.0.0.1:8789, BLOFIN_ENV=demo. No webhook/Traefik route needed.

# Data dir must be world-writable (container runs as uid 1000):
chmod 0777 /docker/scalping-v3.2/data

# Revert to V3.1 (both trade the same demo ZEC account — mutually exclusive):
cd /docker/scalping-v3.2 && docker compose stop
cd /docker/scalping-v3.1 && docker compose up -d

# V3.1 pre-cutover tarball: srv1370094:/root/scalping-v3.1-presave-2026-06-16.tar.gz
```

## Validation status (2026-06-16)

Deployed to demo, healthy. Signal verified computing on live bars. Now in the
~2-week / ≥30-trade demo window to confirm it tracks the engine before it earns
trust. The `signals.py` ↔ engine parity is proven; the open question is purely
live fill quality vs the idealized model (entry-slip test: edge survives to
0.20% overshoot).
