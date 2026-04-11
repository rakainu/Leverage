"""Schema for filter_results and runner_scores tables."""
import pytest

from runner.db.database import Database


@pytest.mark.asyncio
async def test_filter_results_table_exists_with_correct_columns(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    async with db.conn.execute("PRAGMA table_info(filter_results)") as cur:
        rows = await cur.fetchall()
    columns = {r[1]: r[2] for r in rows}  # name -> type

    assert "id" in columns
    assert "token_mint" in columns
    assert "filter_name" in columns
    assert "passed" in columns
    assert "hard_fail_reason" in columns
    assert "sub_scores_json" in columns
    assert "evidence_json" in columns
    assert "cluster_signal_id" in columns
    assert "created_at" in columns

    await db.close()


@pytest.mark.asyncio
async def test_runner_scores_table_exists_with_correct_columns(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    async with db.conn.execute("PRAGMA table_info(runner_scores)") as cur:
        rows = await cur.fetchall()
    columns = {r[1]: r[2] for r in rows}

    assert "id" in columns
    assert "token_mint" in columns
    assert "cluster_signal_id" in columns
    assert "runner_score" in columns
    assert "verdict" in columns
    assert "sub_scores_json" in columns
    assert "explanation_json" in columns
    assert "created_at" in columns

    await db.close()


@pytest.mark.asyncio
async def test_can_insert_and_query_filter_result(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    await db.conn.execute(
        """
        INSERT INTO filter_results
        (token_mint, filter_name, passed, hard_fail_reason,
         sub_scores_json, evidence_json, cluster_signal_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("MINT1", "rug_gate", 1, None, '{"rug_risk": 88}', '{}', 42),
    )
    await db.conn.commit()

    async with db.conn.execute(
        "SELECT token_mint, filter_name, passed FROM filter_results"
    ) as cur:
        rows = await cur.fetchall()
    assert rows == [("MINT1", "rug_gate", 1)]

    await db.close()
