"""Shared test fixtures + path setup."""
import sys
from pathlib import Path

# Make the meme-trading directory importable so `runner.*` resolves
_MEME_TRADING = Path(__file__).resolve().parents[2]
if str(_MEME_TRADING) not in sys.path:
    sys.path.insert(0, str(_MEME_TRADING))
