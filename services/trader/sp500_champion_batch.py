#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.trader.champion_prophet import (
    EXPORT_DIR,
    train_or_export_champion_from_raw_df,
)
from services.trader.main import ensure_raw_df

DEFAULT_CLOSE_MATRIX = ROOT_DIR / "data" / "sp500" / "sp500_close_daily.csv"
DEFAULT_RULES = ("20D", "5D", "1D")
DEFAULT_TASKS = ("direction", "low", "high")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train or export champion Prophet models for the full S&P500 close matrix."
    )
    parser.add_argument(
        "--close-matrix",
        default=str(DEFAULT_CLOSE_MATRIX),
        help="Path to the cached S&P500 close matrix CSV.",
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        help="Optional subset of symbols to train. Defaults to every ticker in the close matrix.",
    )
    parser.add_argument(
        "--tasks",
        nargs="*",
        default=list(DEFAULT_TASKS),
        choices=DEFAULT_TASKS,
        help="Champion tasks to train for each symbol.",
    )
    parser.add_argument(
        "--rules",
        nargs="*",
        default=list(DEFAULT_RULES),
        choices=DEFAULT_RULES,
        help="Daily cadence rules to train for each symbol.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional cap on how many symbols to process.",
    )
    parser.add_argument(
        "--min-rows",
        type=int,
        default=220,
        help="Minimum number of non-null rows required for a symbol to be trained.",
    )
    parser.add_argument(
        "--folds",
        type=int,
        default=3,
        help="Rolling validation folds for each champion search.",
    )
    parser.add_argument(
        "--force-retrain",
        action="store_true",
        help="Ignore existing metadata and retrain every requested champion.",
    )
    parser.add_argument(
        "--export-only",
        action="store_true",
        help="Only export existing champion artifacts instead of retraining.",
    )
    parser.add_argument(
        "--report-path",
        help="Optional explicit path for the batch report JSON.",
    )
    return parser.parse_args()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_close_matrix(path: str) -> pd.DataFrame:
    matrix_path = Path(path).expanduser().resolve()
    if not matrix_path.exists():
        raise FileNotFoundError(
            f"S&P500 close matrix was not found at {matrix_path}. "
            "Run services/trader/sp500_ingest.py first."
        )
    close_matrix = pd.read_csv(matrix_path)
    if "ds" not in close_matrix.columns:
        raise ValueError("S&P500 close matrix must contain a 'ds' column.")
    return close_matrix


def build_symbol_raw_df(close_matrix: pd.DataFrame, symbol: str, min_rows: int) -> pd.DataFrame:
    normalized_symbol = (symbol or "").strip().upper().replace(".", "-")
    if normalized_symbol not in close_matrix.columns:
        raise KeyError(f"{normalized_symbol} is not present in the S&P500 close matrix.")

    raw_df = close_matrix[["ds", normalized_symbol]].rename(
        columns={normalized_symbol: "close"}
    )
    raw_df["close"] = pd.to_numeric(raw_df["close"], errors="coerce")
    raw_df = raw_df.dropna(subset=["ds", "close"]).reset_index(drop=True)
    if len(raw_df) < max(100, min_rows):
        raise ValueError(
            f"{normalized_symbol} has only {len(raw_df)} valid rows; need at least {max(100, min_rows)}."
        )
    raw_df["open"] = raw_df["close"]
    raw_df["high"] = raw_df["close"]
    raw_df["low"] = raw_df["close"]
    return ensure_raw_df(raw_df)


def resolve_symbols(close_matrix: pd.DataFrame, requested_symbols: Iterable[str], limit: int) -> List[str]:
    available = [column for column in close_matrix.columns if column != "ds"]
    if requested_symbols:
        requested = [(symbol or "").strip().upper().replace(".", "-") for symbol in requested_symbols]
        symbols = [symbol for symbol in requested if symbol in available]
    else:
        symbols = available

    if limit > 0:
        symbols = symbols[:limit]
    return symbols


def default_report_path(explicit_path: str | None) -> Path:
    if explicit_path:
        return Path(explicit_path).expanduser().resolve()
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return EXPORT_DIR / f"sp500_champion_batch_{timestamp}.json"


def main() -> None:
    args = parse_args()
    close_matrix_path = Path(args.close_matrix).expanduser().resolve()
    close_matrix = load_close_matrix(str(close_matrix_path))
    symbols = resolve_symbols(close_matrix, args.symbols or [], int(args.limit or 0))
    report_path = default_report_path(args.report_path)

    report: Dict[str, object] = {
        "ok": True,
        "generatedAt": utc_now_iso(),
        "closeMatrixPath": str(close_matrix_path),
        "symbolsRequested": len(symbols),
        "tasks": list(args.tasks),
        "rules": list(args.rules),
        "forceRetrain": bool(args.force_retrain),
        "exportOnly": bool(args.export_only),
        "successes": [],
        "failures": [],
        "skipped": [],
    }

    successes: List[Dict[str, object]] = []
    failures: List[Dict[str, object]] = []
    skipped: List[Dict[str, object]] = []

    for symbol in symbols:
        try:
            raw_df = build_symbol_raw_df(close_matrix, symbol, int(args.min_rows))
        except Exception as exc:
            skipped.append(
                {
                    "symbol": symbol,
                    "reason": str(exc),
                }
            )
            continue

        dataset_label = f"sp500-close-matrix::{close_matrix_path.name}::{symbol}"

        for task in args.tasks:
            for rule in args.rules:
                try:
                    result = train_or_export_champion_from_raw_df(
                        symbol=symbol,
                        raw_df=raw_df,
                        dataset_label=dataset_label,
                        task=task,
                        rule=rule,
                        folds=max(1, int(args.folds)),
                        force_retrain=bool(args.force_retrain),
                        export_only=bool(args.export_only),
                    )
                    successes.append(
                        {
                            "symbol": symbol,
                            "task": task,
                            "rule": rule,
                            "winner": (result.get("winner") or {}).get("name"),
                            "createdAt": result.get("created_at"),
                            "exportBundlePath": result.get("export_bundle_path"),
                            "reusedExisting": bool(result.get("reused_existing")),
                        }
                    )
                except Exception as exc:
                    failures.append(
                        {
                            "symbol": symbol,
                            "task": task,
                            "rule": rule,
                            "error": str(exc),
                        }
                    )

    report["successes"] = successes
    report["failures"] = failures
    report["skipped"] = skipped
    report["successCount"] = len(successes)
    report["failureCount"] = len(failures)
    report["skippedCount"] = len(skipped)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**report, "reportPath": str(report_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
