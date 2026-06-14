"""Patch: add config-gated basket-wide news-rip cooldown breaker to the Scalper
bridge. After N consecutive losing closes, block ALL entries for M minutes, then
auto-resume. Off by default; the Scalper yaml opts in. Revert = enabled:false.

Edits config.py (CooldownConfig + parse) and main.py (state, gate, register,
status, resume-notify). Idempotent guard via marker. Verifies with py_compile."""
import py_compile

CFG = "/docker/scalper-paper/src/lighter_bridge/config.py"
MAIN = "/docker/scalper-paper/src/lighter_bridge/main.py"


def patch(path, edits):
    s = open(path, encoding="utf-8").read()
    if "cooldown" in s and "_register_close" in s and path == MAIN:
        print(f"{path}: already patched, skip"); return
    for old, new in edits:
        assert old in s, f"ANCHOR NOT FOUND in {path}:\n{old[:80]}"
        assert s.count(old) == 1, f"ANCHOR NOT UNIQUE in {path}:\n{old[:80]}"
        s = s.replace(old, new)
    open(path, "w", encoding="utf-8").write(s)
    py_compile.compile(path, doraise=True)
    print(f"{path}: PATCHED + COMPILE OK")


# ---------- config.py ----------
cfg_edits = [
    # 1) new dataclass after ControlConfig
    ('''@dataclass
class BridgeConfig:''',
     '''@dataclass
class CooldownConfig:
    """Basket-wide news-rip circuit breaker. After `consec_losses` losing closes
    in a row (across all coins), block ALL new entries for `minutes`, then
    auto-resume. Off by default; revert = set enabled:false."""
    enabled: bool = False
    consec_losses: int = 2
    minutes: int = 360


@dataclass
class BridgeConfig:'''),
    # 2) field on BridgeConfig
    ('''    control: ControlConfig = field(default_factory=ControlConfig)''',
     '''    control: ControlConfig = field(default_factory=ControlConfig)
    cooldown: CooldownConfig = field(default_factory=CooldownConfig)'''),
    # 3) parse
    ('''    control = ControlConfig(**raw.get("control", {}))''',
     '''    control = ControlConfig(**raw.get("control", {}))
    cooldown = CooldownConfig(**raw.get("cooldown", {}))'''),
    # 4) pass to constructor
    ('''        notify=notify,
        control=control,
    )''',
     '''        notify=notify,
        control=control,
        cooldown=cooldown,
    )'''),
]

# ---------- main.py ----------
main_edits = [
    # 1) init state
    ('''        self.control = None                            # TelegramControl task holder''',
     '''        self.control = None                            # TelegramControl task holder
        # --- news-rip cooldown circuit breaker (config-gated; off => inert) ---
        self._cd_consec = 0          # consecutive losing closes (basket-wide)
        self._cd_until = 0.0         # epoch secs until which entries are blocked
        self._cd_armed = False       # cooldown window active (drives resume-notify)'''),
    # 2) startup log
    ('''        # Lighter client + paper client''',
     '''        if self.cfg.cooldown.enabled:
            log.info("Cooldown breaker: %d consec losses -> block entries %dm (auto-resume)",
                     self.cfg.cooldown.consec_losses, self.cfg.cooldown.minutes)

        # Lighter client + paper client'''),
    # 3) entry gate + new methods (replace the gate; append methods before on_set_switch)
    ('''    def _entries_allowed(self, symbol: str) -> bool:
        """Gate checked at EVERY entry-decision point. Missing = ON (default)."""
        from .telegram_control import entries_allowed
        return entries_allowed(self.entries_enabled, symbol)''',
     '''    def _entries_allowed(self, symbol: str) -> bool:
        """Gate checked at EVERY entry-decision point. Missing = ON (default).
        A live cooldown blocks every symbol regardless of its per-ticker switch."""
        if self._cooldown_active():
            return False
        from .telegram_control import entries_allowed
        return entries_allowed(self.entries_enabled, symbol)

    def _cooldown_active(self) -> bool:
        if not self.cfg.cooldown.enabled:
            return False
        return self._cd_until > 0 and time.time() < self._cd_until

    def _register_close(self, reason: str, pnl: float):
        """Feed each booked regime close to the basket-wide cooldown breaker.
        After `consec_losses` losing closes in a row, block ALL entries for
        `minutes`, then auto-resume. Manual/kill closes do not count."""
        cd = self.cfg.cooldown
        if not cd.enabled or reason == "manual":
            return
        if pnl < 0:
            self._cd_consec += 1
            if self._cd_consec >= cd.consec_losses and not self._cooldown_active():
                self._cd_until = time.time() + cd.minutes * 60
                self._cd_consec = 0
                self._cd_armed = True
                log.warning("COOLDOWN armed: %d consec losses -> all entries blocked %dm",
                            cd.consec_losses, cd.minutes)
                if self.cfg.notify.close:
                    asyncio.create_task(notify.send(
                        f"\\U0001f9ca COOLDOWN \\u2014 {cd.consec_losses} losing closes in a row. "
                        f"All entries paused {cd.minutes}m (auto-resume)."))
        else:
            self._cd_consec = 0

    def _maybe_notify_cooldown_resume(self):
        if self._cd_armed and not self._cooldown_active():
            self._cd_armed = False
            log.info("COOLDOWN lifted - entries resume.")
            if self.cfg.notify.close:
                asyncio.create_task(notify.send("\\u2705 Cooldown lifted \\u2014 entries resume."))'''),
    # 4) book the close into the breaker
    ('''        log.info("%s: REGIME CLOSED %s @ %.4f pnl=$%+.2f (%s, %d bars)",
                 symbol, pos.side.upper(), exit_p, pnl, reason, pos.bars_held)''',
     '''        log.info("%s: REGIME CLOSED %s @ %.4f pnl=$%+.2f (%s, %d bars)",
                 symbol, pos.side.upper(), exit_p, pnl, reason, pos.bars_held)
        self._register_close(reason, pnl)'''),
    # 5) status banner
    ('''        lines = ["\U0001f4cb <b>Scalper status</b>"]''',
     '''        lines = ["\U0001f4cb <b>Scalper status</b>"]
        if self._cooldown_active():
            mins = int((self._cd_until - time.time()) / 60) + 1
            lines.append(f"\U0001f9ca COOLDOWN active \\u2014 entries blocked ~{mins}m more")'''),
    # 6) auto-resume notify on the heartbeat
    ('''        while not self._stopped:
            await asyncio.sleep(300)
            if self.executor is None:
                continue''',
     '''        while not self._stopped:
            await asyncio.sleep(300)
            self._maybe_notify_cooldown_resume()
            if self.executor is None:
                continue'''),
]

patch(CFG, cfg_edits)
patch(MAIN, main_edits)
print("ALL PATCHES APPLIED")
