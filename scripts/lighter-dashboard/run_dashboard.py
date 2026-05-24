"""Uvicorn entrypoint. Usage: python run_dashboard.py --config config.yaml"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from lighter_dashboard.app import create_app          # noqa: E402
from lighter_dashboard.config import load_config       # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    cfg = load_config(args.config)
    app = create_app(cfg)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
