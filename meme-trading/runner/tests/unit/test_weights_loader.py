"""Weights loader reads YAML and hot-reloads on mtime change."""
import time
from pathlib import Path

import pytest

from runner.config.weights_loader import WeightsLoader


@pytest.fixture
def yaml_file(tmp_path: Path) -> Path:
    p = tmp_path / "weights.yaml"
    p.write_text(
        """
cluster:
  min_wallets: 3
  window_minutes: 30
weights:
  wallet_quality: 0.20
  rug_risk: 0.15
verdict_thresholds:
  watch: 40
  strong_candidate: 60
"""
    )
    return p


def test_loads_initial_values(yaml_file: Path):
    loader = WeightsLoader(yaml_file)

    assert loader.get("cluster.min_wallets") == 3
    assert loader.get("cluster.window_minutes") == 30
    assert loader.get("weights.wallet_quality") == 0.20
    assert loader.get("verdict_thresholds.watch") == 40


def test_get_with_default(yaml_file: Path):
    loader = WeightsLoader(yaml_file)

    assert loader.get("weights.doesnotexist", default=99) == 99
    assert loader.get("missing.key") is None


def test_reloads_on_mtime_change(yaml_file: Path):
    loader = WeightsLoader(yaml_file)
    assert loader.get("verdict_thresholds.watch") == 40

    time.sleep(0.01)  # ensure mtime changes
    yaml_file.write_text(
        """
verdict_thresholds:
  watch: 50
"""
    )
    # Force mtime bump in case FS resolution is low
    yaml_file.touch()

    loader.check_and_reload()
    assert loader.get("verdict_thresholds.watch") == 50


def test_reload_is_noop_when_mtime_unchanged(yaml_file: Path):
    loader = WeightsLoader(yaml_file)
    before = loader.last_loaded_mtime

    loader.check_and_reload()

    assert loader.last_loaded_mtime == before


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        WeightsLoader(tmp_path / "nope.yaml")
