"""Put the backtest package dir on sys.path so tests import flat modules."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
