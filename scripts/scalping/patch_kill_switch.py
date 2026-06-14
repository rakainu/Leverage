"""One-shot patch: add /kill (full stop) + ALL wildcard to the Scalper
telegram_control.py. Idempotent-ish; run once. Verifies with py_compile."""
import py_compile
import sys

P = "/docker/scalper-paper/src/lighter_bridge/telegram_control.py"
s = open(P, encoding="utf-8").read()

# 1) register /kill as a no-symbol action
a = '_ACTIONS_NO_SYMBOL = {"status", "help"}'
b = '_ACTIONS_NO_SYMBOL = {"status", "help", "kill"}'
assert a in s, "actions set not found"
s = s.replace(a, b)

# 2) accept ALL wildcard for off/on/close before known-symbol validation
a = '    symbol = parts[1].upper()\n    if symbol not in known_symbols:'
b = ('    symbol = parts[1].upper()\n'
     '    if symbol == "ALL":\n'
     '        return ParsedCommand(action=head, symbol="ALL", error=None)\n'
     '    if symbol not in known_symbols:')
assert a in s, "parse symbol block not found"
s = s.replace(a, b)

# 3) dispatch: add /kill + ALL handling (off/on/close block has no escapes)
a = ('            elif cmd.action == "off":\n'
     '                await self._reply(await self.on_set_switch(cmd.symbol, False))\n'
     '            elif cmd.action == "on":\n'
     '                await self._reply(await self.on_set_switch(cmd.symbol, True))\n'
     '            elif cmd.action == "close":\n'
     '                await self._reply(await self.on_force_close(cmd.symbol))')
b = ('            elif cmd.action == "kill":\n'
     '                await self._reply(await self._kill_all())\n'
     '            elif cmd.action in ("off", "on"):\n'
     '                enabled = cmd.action == "on"\n'
     '                if cmd.symbol == "ALL":\n'
     '                    await self._reply(await self._switch_all(enabled))\n'
     '                else:\n'
     '                    await self._reply(await self.on_set_switch(cmd.symbol, enabled))\n'
     '            elif cmd.action == "close":\n'
     '                if cmd.symbol == "ALL":\n'
     '                    await self._reply(await self._close_all())\n'
     '                else:\n'
     '                    await self._reply(await self.on_force_close(cmd.symbol))')
assert a in s, "dispatch block not found"
s = s.replace(a, b)

# 4) refresh /help text to advertise /kill + ALL
a = ('                await self._reply(\n'
     '                    "Scalper control:\\n"\n'
     '                    "/off SYM — block new entries (open trade still managed)\\n"\n'
     '                    "/on SYM — re-enable entries\\n"\n'
     '                    "/close SYM — force-close open position now\\n"\n'
     '                    "/status — show on/off + open positions"\n'
     '                )')
b = ('                await self._reply(\n'
     '                    "Scalper control:\\n"\n'
     '                    "/kill — STOP ALL: close every open position + block all entries\\n"\n'
     '                    "/off SYM|ALL — block new entries (open trade still managed)\\n"\n'
     '                    "/on SYM|ALL — re-enable entries\\n"\n'
     '                    "/close SYM|ALL — force-close open position(s) now\\n"\n'
     '                    "/status — show on/off + open positions"\n'
     '                )')
assert a in s, "help text not found"
s = s.replace(a, b)

# 5) append helper methods (class methods; _dispatch is the last method in file)
helpers = '''

    async def _switch_all(self, enabled: bool) -> str:
        for sym in sorted(self.known):
            await self.on_set_switch(sym, enabled)
        state = "\U0001f7e2 ON" if enabled else "⛔ OFF"
        return f"ALL entries {state} ({len(self.known)} symbols)"

    async def _close_all(self) -> str:
        closed = []
        for sym in sorted(self.known):
            r = await self.on_force_close(sym)
            if "no open position" not in r:
                closed.append(r)
        return "\\n".join(closed) if closed else "No open positions to close."

    async def _kill_all(self) -> str:
        for sym in sorted(self.known):
            await self.on_set_switch(sym, False)
        closed = []
        for sym in sorted(self.known):
            r = await self.on_force_close(sym)
            if "no open position" not in r:
                closed.append(r)
        head = f"\U0001f6d1 KILL — all entries OFF ({len(self.known)} symbols)"
        return head + (" · flattened:\\n" + "\\n".join(closed) if closed
                       else " · no open positions")
'''
s = s.rstrip() + "\n" + helpers
open(P, "w", encoding="utf-8").write(s)

py_compile.compile(P, doraise=True)
print("PATCHED + COMPILE OK")
