"""Round-robin RPC pool with health tracking."""
import pytest

from runner.ingest.rpc_pool import RpcPool


def test_pool_rotates_round_robin():
    pool = RpcPool(["a", "b", "c"])
    picks = [pool.next() for _ in range(6)]
    assert picks == ["a", "b", "c", "a", "b", "c"]


def test_marking_unhealthy_skips_url():
    pool = RpcPool(["a", "b", "c"])
    pool.mark_unhealthy("b")
    picks = [pool.next() for _ in range(4)]
    # Only a and c should rotate
    assert set(picks) == {"a", "c"}
    assert "b" not in picks


def test_marking_healthy_restores_url():
    pool = RpcPool(["a", "b"])
    pool.mark_unhealthy("a")
    assert pool.next() == "b"
    pool.mark_healthy("a")

    picks = [pool.next() for _ in range(4)]
    assert set(picks) == {"a", "b"}


def test_empty_pool_raises():
    with pytest.raises(ValueError):
        RpcPool([])


def test_all_unhealthy_falls_back_to_full_list():
    pool = RpcPool(["a", "b"])
    pool.mark_unhealthy("a")
    pool.mark_unhealthy("b")
    # Must still return something — do not deadlock.
    assert pool.next() in {"a", "b"}
