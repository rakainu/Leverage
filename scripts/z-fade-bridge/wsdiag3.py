"""Connection-limit probe: does the server evict when >N sockets are open?

2 stable connections survived 22 min (long soak). The bridge's deaths look like
eviction: reconnecting one market cleanly closes the other (NO_EXC). This holds
N STABLE connections (no churn) to distinct markets and watches whether the
server closes any. If opening the 3rd cleanly closes an earlier one → per-IP/
account connection limit confirmed (and the fix is: don't exceed it / don't
churn). Read-only.
"""
import asyncio
import json
import time

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:
    from websockets.client import connect as ws_connect

WS_URL = "wss://mainnet.zklighter.elliot.ai/stream?encoding=json"
MARKETS = [1, 2, 3]   # open 3 stable sockets, distinct markets
HOLD_SECS = 300
STAGGER_S = 8         # open them a few seconds apart to see eviction-on-open


async def conn(idx, market_id, stop_at, state):
    await asyncio.sleep(idx * STAGGER_S)   # stagger opens
    t0 = time.monotonic()
    updates = 0
    print(f"[{time.strftime('%H:%M:%S')}] conn{idx}(mkt{market_id}) opening...", flush=True)
    try:
        async with ws_connect(WS_URL, ping_interval=20) as ws:
            state[idx] = "OPEN"
            print(f"[{time.strftime('%H:%M:%S')}] conn{idx}(mkt{market_id}) OPEN", flush=True)
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
    except Exception as exc:
        life = time.monotonic() - t0
        code = getattr(exc, "code", None)
        reason = getattr(exc, "reason", None)
        state[idx] = "CLOSED"
        print(f"[{time.strftime('%H:%M:%S')}] conn{idx}(mkt{market_id}) CLOSED after {life:6.1f}s "
              f"type={type(exc).__name__} code={code} reason={reason!r} updates={updates}", flush=True)


async def heartbeat(stop_at, state):
    t0 = time.monotonic()
    while time.monotonic() < stop_at:
        await asyncio.sleep(30)
        print(f"[{time.strftime('%H:%M:%S')}] +{time.monotonic()-t0:4.0f}s state={state}", flush=True)


async def main():
    print(f"[{time.strftime('%H:%M:%S')}] conn-limit probe: {len(MARKETS)} sockets markets={MARKETS} "
          f"hold={HOLD_SECS}s", flush=True)
    stop_at = time.monotonic() + HOLD_SECS + len(MARKETS) * STAGGER_S
    state = {i: "init" for i in range(len(MARKETS))}
    await asyncio.gather(
        heartbeat(stop_at, state),
        *(conn(i, m, stop_at, state) for i, m in enumerate(MARKETS)),
    )
    print(f"[{time.strftime('%H:%M:%S')}] probe complete  final_state={state}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
