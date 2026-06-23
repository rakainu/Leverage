import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backtest")))
import optimize_specialists as opt


def test_spaces_cover_all_three_roles():
    assert set(opt.SPACES) == {"long", "short", "range"}
    for role, space in opt.SPACES.items():
        assert space, f"{role} space empty"
        for k, v in space.items():
            assert isinstance(v, tuple) or not isinstance(v, (list, dict))
