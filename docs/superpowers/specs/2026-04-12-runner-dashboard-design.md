# Runner-Intel Dashboard (Phase 1) — Design Spec

**Status:** Approved
**Author:** Claude (with Rich)
**Date:** 2026-04-12
**Depends on:** Plans 1-3 complete, VPS deployment working

---

## 1. Purpose

Build a compact, data-dense dashboard for observing the runner intelligence pipeline in real-time. Reads from the existing `runner.db` — no new tables needed. Optimized for clarity and useful data density, not visual polish. Easy to redesign later.

## 2. Scope

**In scope:**
- FastAPI backend with 4 API routes returning UI-friendly JSON
- Static frontend (index.html + app.js) with Tailwind CDN
- Summary cards, scored candidates table, paper positions table, inline detail panel
- 15-second polling refresh
- Server-side extraction of top_reason, top_caution, dimensions from JSON blobs
- Private-only access via SSH tunnel (port 8421)

**Out of scope:**
- WebSockets (add later if needed)
- Public Traefik route (add when dashboard is proven)
- `/api/activity` route (optional, not in Phase 1)
- Fancy styling, animations, charts

## 3. Decisions (locked)

| Decision | Choice | Rationale |
|---|---|---|
| Stack | FastAPI + static HTML/JS + Tailwind CDN | Zero build step, proven pattern from smc-trading, easy to swap frontend later |
| Data refresh | 15s polling, no WebSockets | Simpler, fewer moving parts, enough for Phase 1 |
| JSON parsing | Server-side in queries.py | Frontend stays a thin renderer, no giant parsing engine |
| Process model | Same process as runner pipeline (uvicorn task in main.py) | Simplest. Dashboard module is isolated for future split. |
| Access | `127.0.0.1:8421` + SSH tunnel | Private only. No Traefik/Cloudflare yet. |

## 4. File structure

```
meme-trading/runner/
  dashboard/
    __init__.py
    app.py              # FastAPI app factory, route handlers, static file serve
    queries.py          # All SQL queries as named async functions
    static/
      index.html        # Page structure, Tailwind layout
      app.js            # Fetch, render, polling logic
```

4 new files. `queries.py` is the clean backend/UI boundary — all SQL there, routes call named functions, frontend calls routes.

## 5. API routes

### `GET /api/stats`

Summary numbers for the top cards.

```json
{
  "total_scored": 142,
  "by_verdict": {
    "ignore": 98,
    "watch": 22,
    "strong_candidate": 16,
    "probable_runner": 6
  },
  "avg_score_eligible": 68.4,
  "open_positions": 3,
  "closed_positions": 11,
  "avg_pnl_closed": 12.7
}
```

`avg_score_eligible` = average runner_score WHERE verdict NOT IN ('ignore'). `avg_pnl_closed` = average pnl_24h_pct WHERE status='closed' AND pnl_24h_pct IS NOT NULL. Returns `null` if no data.

### `GET /api/scores?limit=50`

Recent scored candidates with pre-extracted summary fields.

```json
{
  "scores": [
    {
      "id": 42,
      "token_mint": "5HpYvuTgQoG8TePPBQ4Cfbfmrd9RVBz1v5aodqLkabc1",
      "short_token": "5HpY...abc1",
      "runner_score": 72.3,
      "verdict": "strong_candidate",
      "short_circuited": false,
      "top_reason": "Wallet Quality 87 (x0.20 = 17.4)",
      "top_caution": "Holder Quality 38 — below threshold",
      "has_position": true,
      "created_at": "2026-04-12T14:30:00Z"
    }
  ]
}
```

**Server-side computed fields:**
- `short_token` — `mint[:4]...{mint[-4:]}`
- `top_reason` — highest `weighted` from `explanation_json.dimensions`, excluding narrative placeholder. Format: `"{Name} {score} (x{weight} = {weighted})"`
- `top_caution` — first dimension with score < 40 as `"{Name} {score} — below threshold"`, or data_degraded note, or insider cap note, or `"None"`
- `has_position` — SQL EXISTS subquery against paper_positions

### `GET /api/positions?limit=50`

Paper positions with milestone P&L columns.

```json
{
  "positions": [
    {
      "id": 1,
      "token_mint": "5HpYvuTgQoG8TePPBQ4Cfbfmrd9RVBz1v5aodqLkabc1",
      "short_token": "5HpY...abc1",
      "symbol": "$WIFHAT",
      "verdict": "strong_candidate",
      "runner_score": 72.3,
      "entry_price_usd": 0.00042,
      "amount_sol": 0.25,
      "pnl_5m": 8.1,
      "pnl_30m": 22.4,
      "pnl_1h": 45.2,
      "pnl_4h": 31.0,
      "pnl_24h": 18.3,
      "mfe": 52.1,
      "mae": -3.2,
      "status": "closed",
      "close_reason": "completed",
      "signal_time": "2026-04-12T14:30:00Z",
      "opened_at": "2026-04-12T14:30:05Z",
      "closed_at": "2026-04-13T14:30:00Z"
    }
  ]
}
```

Milestone P&L values are `null` if not yet captured. Frontend renders null as "—".

### `GET /api/scores/{id}`

Full detail for the inline expand panel.

```json
{
  "id": 42,
  "token_mint": "5HpYvuTgQoG8TePPBQ4Cfbfmrd9RVBz1v5aodqLkabc1",
  "short_token": "5HpY...abc1",
  "runner_score": 72.3,
  "verdict": "strong_candidate",
  "short_circuited": false,
  "created_at": "2026-04-12T14:30:00Z",
  "dimensions": {
    "wallet_quality": {"score": 87, "weight": 0.20, "weighted": 17.4},
    "cluster_quality": {"score": 70, "weight": 0.15, "weighted": 10.5},
    "entry_quality": {"score": 75, "weight": 0.15, "weighted": 11.25},
    "holder_quality": {"score": 38, "weight": 0.15, "weighted": 5.7},
    "rug_risk": {"score": 77, "weight": 0.15, "weighted": 11.55},
    "follow_through": {"score": 60, "weight": 0.15, "weighted": 9.0},
    "narrative": {"score": 50, "weight": 0.05, "weighted": 2.5}
  },
  "top_reasons": [
    {"name": "Wallet Quality", "score": 87, "weight": 0.20, "weighted": 17.4},
    {"name": "Rug/Risk", "score": 77, "weight": 0.15, "weighted": 11.55},
    {"name": "Entry Quality", "score": 75, "weight": 0.15, "weighted": 11.25}
  ],
  "cautions": ["Holder Quality 38 — below threshold"],
  "raw_rug_risk": 85.0,
  "raw_insider_risk": 50.0,
  "cluster": {
    "wallet_count": 4,
    "tier_counts": {"A": 2, "B": 1, "U": 1},
    "convergence_minutes": 14.0
  },
  "position": {
    "id": 1,
    "entry_price_usd": 0.00042,
    "amount_sol": 0.25,
    "pnl_5m": 8.1,
    "pnl_30m": 22.4,
    "pnl_1h": 45.2,
    "pnl_4h": 31.0,
    "pnl_24h": 18.3,
    "mfe": 52.1,
    "mae": -3.2,
    "status": "closed"
  },
  "links": {
    "dexscreener": "https://dexscreener.com/solana/5HpYvuTgQoG8TePPBQ4Cfbfmrd9RVBz1v5aodqLkabc1",
    "solscan": "https://solscan.io/token/5HpYvuTgQoG8TePPBQ4Cfbfmrd9RVBz1v5aodqLkabc1"
  }
}
```

`position` is `null` if no paper position exists. All heavy parsing done server-side.

## 6. Server-side vs client-side computation

| Field | Where | Why |
|-------|-------|-----|
| `short_token` | Server | Trivial, keeps frontend clean |
| `top_reason` | Server | Parses explanation_json once |
| `top_caution` | Server | Parses explanation_json once |
| `has_position` | Server | SQL EXISTS subquery |
| `dimensions`, `top_reasons`, `cautions` | Server (detail only) | Extracted from JSON blobs |
| `raw_rug_risk`, `raw_insider_risk` | Server (detail only) | From sub_scores_json |
| `cluster` | Server (detail only) | From explanation_json |
| Verdict badge CSS class | Client | Map verdict string to color class |
| P&L green/red coloring | Client | Sign check in render function |
| Null → "—" display | Client | Simple ternary |

## 7. Frontend layout

Single page, dark background, 4 sections stacked vertically.

### A. Summary cards (row of 6)

| Card | Value |
|------|-------|
| Scored Candidates | `total_scored` |
| Strong Candidates | `by_verdict.strong_candidate` |
| Probable Runners | `by_verdict.probable_runner` |
| Open Positions | `open_positions` |
| Closed Positions | `closed_positions` |
| Avg P&L (closed) | `avg_pnl_closed` with +/- sign, green/red |

### B. Recent scored candidates (table, 50 rows)

Columns: Time | Token | Score | Verdict | Top Reason | Top Caution | Position | Short-circuited

- Score colored by verdict tier (gray/yellow/green/blue)
- Verdict as colored badge
- Clicking a row expands the inline detail panel below it

### C. Paper positions (table, 50 rows)

Columns: Token | Symbol | Opened | Verdict | Entry $ | 5m | 30m | 1h | 4h | 24h | MFE | MAE | Status

- All P&L columns green/red by sign, "—" for null
- Monospace for numbers

### D. Inline detail panel (accordion on score row click)

Expands below the clicked scores table row. Shows:
- 7-dimension score breakdown with simple bar visualization (CSS width % of 100)
- Top 3 reasons list
- Cautions list
- Raw rug + insider scores
- Cluster info (wallet count, tiers, convergence time)
- Milestones (if position exists)
- DexScreener + Solscan links

Fetches from `/api/scores/{id}` on click (not pre-loaded).

## 8. Polling

```javascript
const POLL_INTERVAL = 15_000;
async function refreshAll() {
    const [stats, scores, positions] = await Promise.all([
        fetchJSON('/api/stats'),
        fetchJSON('/api/scores?limit=50'),
        fetchJSON('/api/positions?limit=50'),
    ]);
    renderStats(stats);
    renderScores(scores.scores);
    renderPositions(positions.positions);
}
refreshAll();
setInterval(refreshAll, POLL_INTERVAL);
```

Detail panel is fetched on-demand, not polled.

## 9. Process model

Dashboard runs as a supervised uvicorn task inside `main.py`:

```python
from runner.dashboard.app import create_app

dashboard_app = create_app(db)

async def _run_dashboard():
    import uvicorn
    config = uvicorn.Config(dashboard_app, host="0.0.0.0", port=8421, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()

# In asyncio.gather:
_supervise(_run_dashboard, "dashboard", logger),
```

The dashboard module is isolated — `create_app(db)` takes only a Database reference. It can be split into its own process/container later by running `uvicorn runner.dashboard.app:create_app(...)` standalone.

## 10. Docker compose change

Add port binding to `docker-compose.runner.yml`:

```yaml
    ports:
      - "127.0.0.1:8421:8421"
```

Local-only binding on VPS. Access via SSH tunnel: `ssh -L 8421:localhost:8421 root@46.202.146.30`, then open `http://localhost:8421`.

## 11. Dependencies to add

```
fastapi==0.115.0
uvicorn[standard]==0.32.0
```

## 12. UX guidelines

- Dark background (#0f172a or similar slate-900)
- Light text, monospace for numbers and addresses
- Verdict badges: gray=ignore, yellow=watch, green=strong_candidate, blue=probable_runner
- P&L: green for positive, red for negative, white for zero/null
- Compact rows — maximize data density
- No animations, no hover effects beyond cursor change
- Mobile: readable but not optimized (desktop-first for this tool)

## 13. Test strategy

### Backend tests (queries.py + app.py)

- `test_get_stats` — verify counts and averages against known DB state
- `test_get_scores` — verify top_reason and top_caution extraction
- `test_get_positions` — verify milestone columns returned correctly
- `test_get_score_detail` — verify full dimension breakdown + position join
- `test_get_score_detail_no_position` — position is null when none exists

### Frontend testing

Manual — open in browser, verify tables render, polling works, detail panel expands.

---

**End of spec.**
