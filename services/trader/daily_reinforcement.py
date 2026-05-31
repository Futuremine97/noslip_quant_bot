#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.trader.reinforcement import run_daily_reinforcement


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the daily reinforcement update for champion Prophet and investor LLM lenses."
    )
    return parser.parse_args()


def main() -> None:
    parse_args()
    report = run_daily_reinforcement()
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
