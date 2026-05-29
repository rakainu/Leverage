"""LONG soak: definitively catch (or rule out) a server-side WS drop.

A bare 6-min soak is too short — the bridge's drop cadence is ~6-8 min, so one
6-min window can miss a drop by luck. This runs TWO concurrent bare listeners
(markets 1 & 2, ping_interval=20 == SDK default) for SOAK_SECS, printing a
liveness heartbeat every 60s and logging every CLOSE with lifetime + close
code/reason. If a bare socket dies at ~6-8 min like the bridge → server-side.
If both survive the whole window → the bridge's runtime is the cause.
Read-only.
"""
import asyncio
import json
import time

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:
    from websockets.client import connect as ws_connect

WS_URL = "wss://mainnet.zklighter.elliot.ai/stream?encoding=json"
MARKETS = [1, 2]      # BTC, SOL
SOAK_SECS = 1320      # 22 min
PING_INTERVAL = 20    # websockets default == SDK


async def listener(market_id, stop_at, state):
    gen = 0
    while time.monotonic() < stop_at:
        gen += 1
        t0 = time.monotonic()
        updates = 0
        try:
            async with ws_connect(WS_URL, ping_interval=PING_INTERVAL) as ws:
                state[market_id] = ("OPEN", t0, 0)
                while time.monotonic() < stop_at:
                    raw = await ws.recv()
                    msg = json.loads(raw)
                    mt = msg.get("type")
                    if mt == "connected":
                        await ws.send(json.dumps({"type": "subscribe",
                                                  "channel": f"order_book/{market_id}"}))
                    elif mt == "ping":
                        await ws.send(json.dumps({"type": "pong"}))
                    elif mt and mt.endswith("order_book"):
                        updates += 1
                        state[market_id] = ("OPEN", t0, updates)
        except Exception as exc:
            life = time.monotonic() - t0
            code = getattr(exc, "code", None)
            reason = getattr(exc, "reason", None)
            print(f"[{time.strftime('%H:%M:%S')}] mkt{market_id} gen{gen} CLOSED after "
                  f"{life:6.1f}s  type={type(exc).__name__} code={code} reason={reason!r} "
                  f"exc={exc!r} (updates={updates})", flush=True)
            state[market_id] = ("CLOSED", time.monotonic(), updates)
            await asyncio.sleep(0.5)


async def heartbeat(stop_at, state):
    t0 = time.monotonic()
    while time.monotonic() < stop_at:
        await asyncio.sleep(60)
        parts = []
        for mid in MARKETS:
            st = state.get(mid, ("?", t0, 0))
            parts.append(f"mkt{mid}={st[0]}(upd={st[2]})")
        print(f"[{time.strftime('%H:%M:%S')}] +{time.monotonic()-t0:5.0f}s  "
              + " ".join(parts), flush=True)


async def main():
    print(f"[{time.strftime('%H:%M:%S')}] LONG soak {SOAK_SECS}s markets={MARKETS} "
          f"ping_interval={PING_INTERVAL}", flush=True)
    stop_at = time.monotonic() + SOAK_SECS
    state = {}
    await asyncio.gather(
        heartbeat(stop_at, state),
        *(listener(m, stop_at, state) for m in MARKETS),
    )
    print(f"[{time.strftime('%H:%M:%S')}] soak complete", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
