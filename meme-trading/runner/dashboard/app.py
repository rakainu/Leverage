"""FastAPI dashboard — read-only API routes + static file serve."""
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from runner.dashboard.queries import (
    get_health,
    get_positions,
    get_score_detail,
    get_scores,
    get_stats,
)
from runner.db.database import Database

_STATIC = Path(__file__).parent / "static"


def create_app(db: Database) -> FastAPI:
    app = FastAPI(title="Runner Intel Dashboard", docs_url=None, redoc_url=None)

    @app.get("/api/health")
    async def health():
        return await get_health(db)

    @app.get("/api/stats")
    async def stats():
        return await get_stats(db)

    @app.get("/api/scores")
    async def scores(limit: int = Query(50, ge=1, le=200)):
        return await get_scores(db, limit=limit)

    @app.get("/api/scores/{score_id}")
    async def score_detail(score_id: int):
        result = await get_score_detail(db, score_id)
        if result is None:
            return JSONResponse(status_code=404, content={"error": "score not found"})
        return result

    @app.get("/api/positions")
    async def positions(limit: int = Query(50, ge=1, le=200)):
        return await get_positions(db, limit=limit)

    @app.get("/")
    async def index():
        return FileResponse(_STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    return app
