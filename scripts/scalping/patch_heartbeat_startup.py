"""Patch: write the equity snapshot immediately on startup instead of after the
first 300s sleep. Eliminates the ~5-min stale-pill window after every restart
(the dashboard flags stale at >420s heartbeat age). Moves sleep to loop end +
fast-retries while executor warms up. Verified with py_compile."""
import py_compile

P = "/docker/scalper-paper/src/lighter_bridge/main.py"
s = open(P, encoding="utf-8").read()

if "await asyncio.sleep(5)\n                continue" in s:
    print("already patched, skip")
else:
    # R1: move the 300s sleep off the top of the loop; fast-retry while warming up
    a = '''        last_seen_at = time.time()
        while not self._stopped:
            await asyncio.sleep(300)
            self._maybe_notify_cooldown_resume()
            if self.executor is None:
                continue
            try:'''
    b = '''        last_seen_at = time.time()
        while not self._stopped:
            self._maybe_notify_cooldown_resume()
            if self.executor is None:
                await asyncio.sleep(5)
                continue
            try:'''
    assert a in s and s.count(a) == 1, "R1 anchor"
    s = s.replace(a, b)

    # R2: add the 300s cadence at the END of the loop body (after the except)
    a = '''                if time.time() - last_seen_at > 1800:
                    asyncio.create_task(notify.notify_error(
                        f"Heartbeat silent for >30m: {exc}"
                    ))

    async def _verify_mark_feed_live(self, enabled: dict, deadline_s: int):'''
    b = '''                if time.time() - last_seen_at > 1800:
                    asyncio.create_task(notify.notify_error(
                        f"Heartbeat silent for >30m: {exc}"
                    ))
            await asyncio.sleep(300)

    async def _verify_mark_feed_live(self, enabled: dict, deadline_s: int):'''
    assert a in s and s.count(a) == 1, "R2 anchor"
    s = s.replace(a, b)

    open(P, "w", encoding="utf-8").write(s)
    py_compile.compile(P, doraise=True)
    print("PATCHED + COMPILE OK")
