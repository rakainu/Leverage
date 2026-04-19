# HLSM — Hyperliquid Smart-Money Positioning Engine

**Status:** Parked. Not yet started. Saved for future work.
**Decided:** 2026-04-18
**Owner:** Rich

This folder holds the full spec, build plan, and rationale for HLSM, a project chosen as the next highest-leverage edge-building effort after a structured research/decision pass. Rich has other work in flight and parked this until capacity frees up. Do not begin implementation without Rich's explicit go-ahead.

## Files in this folder

- `SPEC.md` — what we're building, why, and how it makes money
- `PLAN.md` — MVP / V2 / V3 build order with concrete tasks
- `DECISIONS.md` — key decisions made during selection + open questions
- `PROFIT_MECHANICS.md` — three profit mechanisms explained in detail

## How to resume work

When ready to start:

1. Read all four files in this folder in order: README → SPEC → PROFIT_MECHANICS → DECISIONS → PLAN
2. Confirm assumptions in DECISIONS.md still hold (Hyperliquid API still public, no scope changes)
3. Start with the "First Build Task" in PLAN.md — nothing else first
4. Commit each working unit immediately, push to GitHub
5. Update PLAN.md checkboxes as tasks complete
6. Log any non-obvious choices in DECISIONS.md as they come up

## One-line summary

Build a ranked + monitored database of Hyperliquid's most-skilled perp traders using the venue's fully-transparent on-chain data, and use their positioning as (a) standalone trade signals, (b) aggregate regime signals, and (c) a confluence filter on the existing TV→BloFin Leverage bridge.
