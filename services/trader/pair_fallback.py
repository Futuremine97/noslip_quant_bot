from __future__ import annotations

from pathlib import Path
import re
from typing import List, Optional, Tuple

import pandas as pd

from services.trader.main import ensure_raw_df, fetch_fallback_data

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data" / "historical"

STABLE_SYMBOLS = {
    "USDC",
    "USDT",
    "USDE",
    "USD",
    "PYUSD",
    "USDS",
    "USDF",
    "USDG",
    "USDTB",
    "USD0",
    "USDD",
    "USDY",
    "RLUSD",
}

LOCAL_DATASETS = {}

SYMBOL_ALIASES = {
    "SOL": "SOL",
    "ETH": "ETH",
    "WETH": "ETH",
    "BTC": "BTC",
    "WBTC": "BTC",
    "BNB": "BNB",
}
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._/\- ]{0,31}$")


def normalize_symbol(raw_symbol: str) -> str:
    normalized = " ".join((raw_symbol or "").strip().upper().split())
    if not normalized or not SYMBOL_RE.fullmatch(normalized):
        return ""
    return normalized


def parse_route_symbols(symbol_hint: str) -> Tuple[Optional[str], Optional[str]]:
    parts = [
        normalize_symbol(piece)
        for piece in pd.Series([symbol_hint]).str.split(r"[→>-]+").iloc[0]
        if piece and piece.strip()
    ]
    parts = [part for part in parts if part]
    if len(parts) < 2:
        return None, None
    return parts[0], parts[1]


def load_reference_df(symbol: str) -> pd.DataFrame:
    normalized = normalize_symbol(symbol)
    local_path = LOCAL_DATASETS.get(normalized)

    if local_path and local_path.exists():
        return ensure_raw_df(pd.read_csv(local_path))

    fallback_symbol = SYMBOL_ALIASES.get(normalized, normalized)
    raw_df = fetch_fallback_data(fallback_symbol)
    return ensure_raw_df(raw_df)


def _build_single_asset_series(df: pd.DataFrame) -> pd.DataFrame:
    base = df[["ds", "close"]].copy()
    base["close"] = pd.to_numeric(base["close"], errors="coerce")
    return base.dropna(subset=["ds", "close"]).reset_index(drop=True)


def _align_pair_series(
    input_series: pd.DataFrame, output_series: pd.DataFrame
) -> pd.DataFrame:
    merged = input_series.merge(
        output_series,
        on="ds",
        how="inner",
        suffixes=("_input", "_output"),
    )
    if not merged.empty:
        return merged

    paired_length = min(len(input_series), len(output_series))
    if paired_length == 0:
        return pd.DataFrame(columns=["ds", "close_input", "close_output"])

    tail_input = input_series.tail(paired_length).reset_index(drop=True)
    tail_output = output_series.tail(paired_length).reset_index(drop=True)
    return pd.DataFrame(
        {
            "ds": tail_input["ds"],
            "close_input": tail_input["close"],
            "close_output": tail_output["close"],
        }
    )


def _finalize_ratio_series(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna(subset=["ds", "close"]).reset_index(drop=True)
    out["open"] = out["close"]
    out["high"] = out["close"]
    out["low"] = out["close"]
    out["y"] = out["close"]
    return out[["ds", "open", "high", "low", "close", "y"]]


def build_pair_raw_df(symbol_hint: str, min_rows: int = 110) -> pd.DataFrame:
    input_symbol, output_symbol = parse_route_symbols(symbol_hint)
    if not input_symbol or not output_symbol:
        raise ValueError(f"Could not parse route symbols from '{symbol_hint}'")

    if input_symbol in STABLE_SYMBOLS and output_symbol in STABLE_SYMBOLS:
        raise ValueError("Stable-to-stable route does not have enough signal for Prophet fallback")

    if output_symbol in STABLE_SYMBOLS:
        input_df = _build_single_asset_series(load_reference_df(input_symbol))
        return _finalize_ratio_series(input_df.tail(max(min_rows, 120)))

    if input_symbol in STABLE_SYMBOLS:
        output_df = _build_single_asset_series(load_reference_df(output_symbol)).tail(max(min_rows, 120))
        output_df["close"] = output_df["close"].replace(0, pd.NA)
        output_df["close"] = 1 / output_df["close"]
        return _finalize_ratio_series(output_df)

    input_df = _build_single_asset_series(load_reference_df(input_symbol))
    output_df = _build_single_asset_series(load_reference_df(output_symbol))
    merged = _align_pair_series(input_df, output_df).tail(max(min_rows, 120)).copy()
    merged["close_output"] = pd.to_numeric(merged["close_output"], errors="coerce").replace(0, pd.NA)
    merged["close"] = pd.to_numeric(merged["close_input"], errors="coerce") / merged["close_output"]
    return _finalize_ratio_series(merged[["ds", "close"]])
