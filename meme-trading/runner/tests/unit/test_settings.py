"""Settings loads env vars with RUNNER_ prefix."""
import pytest

from runner.config.settings import Settings


def test_settings_loads_required_fields(monkeypatch):
    monkeypatch.setenv("RUNNER_HELIUS_API_KEY", "test-key-123")
    monkeypatch.setenv("RUNNER_HELIUS_WS_URL", "wss://example.test/ws")
    monkeypatch.setenv("RUNNER_HELIUS_RPC_URL", "https://example.test/rpc")
    monkeypatch.setenv("RUNNER_WALLETS_JSON_PATH", "/tmp/wallets.json")
    monkeypatch.setenv("RUNNER_WEIGHTS_YAML_PATH", "/tmp/weights.yaml")
    monkeypatch.setenv("RUNNER_DB_PATH", "/tmp/runner.db")

    s = Settings()

    assert s.helius_api_key == "test-key-123"
    assert s.helius_ws_url == "wss://example.test/ws"
    assert s.helius_rpc_url == "https://example.test/rpc"
    assert s.db_path == "/tmp/runner.db"
    assert s.log_level == "INFO"           # default
    assert s.enable_executor is True       # default


def test_settings_respects_log_level_override(monkeypatch):
    monkeypatch.setenv("RUNNER_HELIUS_API_KEY", "k")
    monkeypatch.setenv("RUNNER_HELIUS_WS_URL", "wss://x")
    monkeypatch.setenv("RUNNER_HELIUS_RPC_URL", "https://x")
    monkeypatch.setenv("RUNNER_WALLETS_JSON_PATH", "/tmp/w.json")
    monkeypatch.setenv("RUNNER_WEIGHTS_YAML_PATH", "/tmp/w.yaml")
    monkeypatch.setenv("RUNNER_DB_PATH", "/tmp/r.db")
    monkeypatch.setenv("RUNNER_LOG_LEVEL", "DEBUG")

    s = Settings()
    assert s.log_level == "DEBUG"


def test_settings_missing_required_raises(monkeypatch):
    for var in [
        "RUNNER_HELIUS_API_KEY",
        "RUNNER_HELIUS_WS_URL",
        "RUNNER_HELIUS_RPC_URL",
        "RUNNER_WALLETS_JSON_PATH",
        "RUNNER_WEIGHTS_YAML_PATH",
        "RUNNER_DB_PATH",
    ]:
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(Exception):   # pydantic ValidationError
        Settings()
