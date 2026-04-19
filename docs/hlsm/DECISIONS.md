# HLSM — Decision Log

Record non-obvious choices and their rationale. Update as new decisions are made during build.

---

## 2026-04-18 — Project selected

**Decision:** Build HLSM (Hyperliquid Smart-Money Positioning Engine) as the next edge-building project for the Leverage stack.

**Context:** Rich asked for a rigorous selection pass across crypto trading project categories. A 3-finalist comparison was run: HLSM, Strategy Intelligence Layer (SIL), Perp Positioning Dashboard.

**Rationale:**
- No overlap with existing systems (SMC Trading, Runner Intelligence, LP-Project, Leverage bridge)
- Public verifiable data (Hyperliquid L1 transparency)
- Compounding ranking database — time is an asset
- Starts producing intelligence on day one from 90d of historical API data
- Plugs cleanly into existing Leverage bridge as signal source / filter

**SIL was runner-up.** Scored marginally higher on criteria but requires months of live bridge data before filters become statistically meaningful. HLSM produces output immediately. SIL is the right V4 follow-on once HLSM stabilizes.

**Perp Positioning Dashboard dropped** on defensibility — Coinalyze/Coinglass/Laevitas commoditize it.

---

## 2026-04-18 — Project parked, not started

**Decision:** Save full spec + plan to `docs/hlsm/` and a memory index entry, but do not begin implementation.

**Context:** Rich has too much other work in flight. Wants to preserve the design work without committing build capacity now.

**How to resume:** Read README → SPEC → PROFIT_MECHANICS → DECISIONS → PLAN, confirm assumptions still hold, start with "First build task" in PLAN.md.

---

## 2026-04-18 — Bridge ownership boundary preserved

**Decision:** HLSM will never send execution commands directly. Bridge remains sole owner of order placement and exits.

**Context:** Rich's scalping bridge established a strict "bridge owns exits" rule on 2026-04-17 (removed `sl` + `reversal_*` actions, only `buy`/`sell` webhook actions). HLSM must respect this.

**Implementation:** HLSM exposes a queryable state endpoint. Bridge queries at alert-time and receives a verdict (`aligned | neutral | conflicting | strong_confluence`) plus optional size modifier hint. Bridge decides what to do with it. HLSM never calls BloFin.

---

## 2026-04-18 — Signals are price-agnostic

**Decision:** HLSM signal logic is triggered by state, not by price levels.

**Context:** An earlier framing used concrete price examples (e.g., "SOL at $184") which suggested price-based gating. Clarified: price is irrelevant. TV alerts fire whenever TV fires them at whatever price the market is at. HLSM maintains live positioning state independently. At TV alert time, bridge queries HLSM for current verdict on that asset.

---

## Open questions (resolve when building)

- **Composite scorer weights:** initial weights for Sharpe / DD / win rate / sample size / recency — TBD during MVP scoring work
- **Minimum wallet history:** 30 days + 50 trades is the starting floor; tune during V2
- **Top-N monitored set size:** starts at 100, may expand once WebSocket capacity validated
- **Leaderboard seed refresh frequency:** starts daily; may move to 6h if discovery is weak
- **Style classifier cutoffs:** hold-time boundaries between scalper/swing/positional TBD from empirical data
- **Signal decay measurement horizons:** 1h/4h/24h to start; adjust based on observed decay curves
