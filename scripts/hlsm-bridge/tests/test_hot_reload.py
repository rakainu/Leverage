"""Hot-reload watcher tests. Uses a real temp file + manual poll."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

from hlsm.safety.hot_reload import WeightsWatcher


def test_watcher_fires_on_initial_read(tmp_path: Path):
    f = tmp_path / "weights.yaml"
    f.write_text(yaml.safe_dump({"convergence": {"cluster_n": 3}}))

    seen: list[dict] = []
    w = WeightsWatcher(f, on_change=lambda d: seen.append(d), poll_interval_seconds=0.05)
    # Trigger initial read via poll_once
    w.poll_once()
    assert len(seen) == 1
    assert seen[0]["convergence"]["cluster_n"] == 3


def test_watcher_fires_on_file_change(tmp_path: Path):
    f = tmp_path / "weights.yaml"
    f.write_text(yaml.safe_dump({"convergence": {"cluster_n": 3}}))
    seen: list[dict] = []
    w = WeightsWatcher(f, on_change=lambda d: seen.append(d), poll_interval_seconds=0.05)
    w.poll_once()
    assert len(seen) == 1

    # Change content + bump mtime
    time.sleep(0.1)
    f.write_text(yaml.safe_dump({"convergence": {"cluster_n": 4}}))
    # Force mtime advance on Windows
    import os
    new_time = time.time()
    os.utime(f, (new_time, new_time))

    fired = w.poll_once()
    assert fired
    assert seen[-1]["convergence"]["cluster_n"] == 4


def test_watcher_does_not_fire_when_unchanged(tmp_path: Path):
    f = tmp_path / "weights.yaml"
    f.write_text(yaml.safe_dump({"convergence": {"cluster_n": 3}}))
    seen: list[dict] = []
    w = WeightsWatcher(f, on_change=lambda d: seen.append(d), poll_interval_seconds=0.05)
    w.poll_once()
    fired = w.poll_once()
    assert fired is False
    assert len(seen) == 1
