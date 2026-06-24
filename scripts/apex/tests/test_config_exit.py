from pathlib import Path

from apex_bridge.config import load_config

CONFIG = Path(__file__).resolve().parents[1] / "config.apex.yaml"


def test_apex_exit_config_has_exactly_three_stage_fields():
    cfg = load_config(CONFIG)
    e = cfg.exits
    assert (e.sl_loss_usdt, e.breakeven_usdt, e.trail_activate_usdt,
            e.trail_distance_usdt, e.tp_ceiling_pct) == (30.0, 20.0, 35.0, 15.0, 2.0)
    assert not hasattr(e, "lock_profit_usdt")


def test_apex_entry_sizing_cooldown_control_loaded():
    cfg = load_config(CONFIG)
    assert cfg.signal_source == "webhook"
    assert list(cfg.symbols) == ["SOL", "HYPE", "ZEC"]
    assert cfg.symbols["SOL"].margin_usdt == 250 and cfg.symbols["SOL"].leverage == 30
    assert cfg.entry.require_retest is True
    assert cfg.entry.require_reclaim is False
    assert cfg.entry.max_gap_pct == 0.0
    assert cfg.entry.min_abs_slope_pct == 0.15
    assert cfg.entry.block_body_band == (0.3, 0.5)
    assert cfg.entry.block_weekdays == []
    assert cfg.cooldown.enabled and cfg.cooldown.consec_losses == 3 and cfg.cooldown.minutes == 60
    assert cfg.control.telegram_enabled is True
    assert cfg.webhook.enabled and cfg.webhook.path == "/webhook/apex"
