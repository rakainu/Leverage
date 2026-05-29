"""Soak #2 (fixed): does the SDK PaperClient path reproduce the death?

The bare 22-min soak proved the raw socket is stable, so the killer is in the
bridge runtime. This drives the REAL SDK PaperClient.track_market on both
markets (with snapshot-retry to dodge the transient startup timeout) and runs a
watchdog-like reconnect loop for SOAK_SECS, capturing the EXACT exception that
ends each listener task (the SDK swallows it via _consume_task_exception;
task.exception() is still readable from our own done-callback). Read-only — no
orders, no DB. Run ALONE (no other soaks) to avoid connection-count confounds.

If listeners die here at ~6-8 min like the bridge → cause is the SDK message
path (apply_delta + locks). If they survive 22 min → cause is the bridge's other
loops (bar feed pandas, heartbeat, the watchdog itself).
"""
import asyncio
import time

import lighter

HOST = "https://mainnet.zklighter.elliot.ai"
MARKETS = {"BTC": 1, "SOL": 2}
SOAK_SECS = 1320
CHECK_S = 20


async def track_with_retry(paper, name, mid, tries=6):
    for attempt in range(1, tries + 1):
        try:
            await paper.track_market(market_id=mid)
            return True
        except Exception as exc:
            print(f"  [{time.strftime('%H:%M:%S')}] {name} track attempt {attempt}/{tries} "
                  f"failed: {type(exc).__name__} {exc!r}", flush=True)
            await asyncio.sleep(2)
    return False


def arm_death_logger(paper, name, mid):
    listener = paper._live_listeners.get(mid)
    task = getattr(listener, "_task", None) if listener else None
    if task is None:
        return
    def _cb(t):
        if t.cancelled():
            cause, detail = "CANCELLED", None
        else:
            try:
                exc = t.exception()
                cause = type(exc).__name__ if exc else "NO_EXC"
                detail = repr(exc) if exc else None
            except Exception as e:
                cause, detail = "EXC_READ_FAIL", repr(e)
        print(f"  >> DEATH [{time.strftime('%H:%M:%S')}] {name}(mkt{mid}) "
              f"cause={cause} detail={detail}", flush=True)
    task.add_done_callback(_cb)


async def main():
    print(f"[{time.strftime('%H:%M:%S')}] SDK PaperClient soak {SOAK_SECS}s markets={MARKETS}", flush=True)
    api = lighter.ApiClient(configuration=lighter.Configuration(host=HOST))
    paper = lighter.PaperClient(api, initial_collateral_usdc=2000.0)

    for name, mid in MARKETS.items():
        ok = await track_with_retry(paper, name, mid)
        if ok:
            arm_death_logger(paper, name, mid)
            print(f"  [{time.strftime('%H:%M:%S')}] tracking {name} (mkt{mid})", flush=True)

    t0 = time.monotonic()
    last_mid = {n: None for n in MARKETS}
    last_change = {n: time.monotonic() for n in MARKETS}
    n_deaths = {n: 0 for n in MARKETS}
    while time.monotonic() - t0 < SOAK_SECS:
        await asyncio.sleep(CHECK_S)
        line = []
        for name, mid in MARKETS.items():
            listener = paper._live_listeners.get(mid)
            task = getattr(listener, "_task", None) if listener else None
            alive = task is not None and not task.done()
            book = paper.order_books.get(mid)
            midpx = book.mid_price if book else None
            if midpx != last_mid[name]:
                last_mid[name] = midpx
                last_change[name] = time.monotonic()
            stale = time.monotonic() - last_change[name]
            line.append(f"{name}:alive={alive} stale={stale:4.0f}s")
            # mimic the bridge watchdog: re-track a dead listener so we observe cadence
            if not alive:
                n_deaths[name] += 1
                await track_with_retry(paper, name, mid)
                arm_death_logger(paper, name, mid)
        print(f"[{time.strftime('%H:%M:%S')}] +{time.monotonic()-t0:5.0f}s  "
              + " | ".join(line) + f"  deaths={n_deaths}", flush=True)

    print(f"[{time.strftime('%H:%M:%S')}] soak complete. total_deaths={n_deaths}", flush=True)
    await paper.close()
    await api.close()


if __name__ == "__main__":
    asyncio.run(main())
