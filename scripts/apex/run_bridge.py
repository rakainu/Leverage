"""Entry point — runs the Apex paper bridge.

Usage:
    python run_bridge.py [--config config.yaml]
"""
import sys
from pathlib import Path

# Make `apex_bridge` importable from src/
sys.path.insert(0, str(Path(__file__).parent / "src"))

from apex_bridge.main import main

if __name__ == "__main__":
    main()
