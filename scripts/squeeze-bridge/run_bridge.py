"""Entry point — runs the Lighter paper bridge.

Usage:
    python run_bridge.py [--config config.yaml]
"""
import sys
from pathlib import Path

# Make `lighter_bridge` importable from src/
sys.path.insert(0, str(Path(__file__).parent / "src"))

from lighter_bridge.main import main

if __name__ == "__main__":
    main()
