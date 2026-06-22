import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "analysis", "strategy_hunt_2026-06-22")))
from donchian_millerrh import simulate_donchian as simulate  # long-only breakout + channel trail
