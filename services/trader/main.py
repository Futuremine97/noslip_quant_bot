
# main_time_to_below_current_updated.py
# Updated from main_merged_fixed_integrated.py
# Key change:
# - output how long it takes until the forecast first goes below the current price
#
# pip install pandas numpy requests prophet solders yfinance scikit-learn

import os
import time
import math
import json
import base64
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
import requests
from sklearn.linear_model import LinearRegression
from services.trader.geodesic_state import estimate_symbol_geodesic_state
from services.trader.resource_moe import (
    build_moe_runtime,
    evaluate_timesfm_gate,
    heavy_expert_slot,
)
from services.trader.timesfm_drawdown import (
    build_timesfm_skipped_profile,
    compute_timesfm_drawdown_profile,
)

from prophet import Prophet
from prophet.serialize import model_to_json, model_from_json

try:
    from solders.keypair import Keypair
    from solders.message import to_bytes_versioned
    from solders.transaction import VersionedTransaction
    SOLDERS_AVAILABLE = True
except ImportError:
    Keypair = Any
    to_bytes_versioned = None
    VersionedTransaction = None
    SOLDERS_AVAILABLE = False


# =========================================================
# CONFIG
# =========================================================

SWAP_BASE_URL = "https://api.jup.ag/swap/v2"
ORDER_URL = f"{SWAP_BASE_URL}/order"
EXECUTE_URL = f"{SWAP_BASE_URL}/execute"

JUPITER_API_KEY = os.getenv("JUPITER_API_KEY", "")
SOLANA_PRIVATE_KEY_B58 = os.getenv("SOLANA_PRIVATE_KEY_B58", "")
PRICE_HISTORY_CSV = os.getenv("PRICE_HISTORY_CSV", "historical_price.csv")

EXECUTE_TRADES = os.getenv("EXECUTE_TRADES", "false").lower() == "true"
WAIT_FOR_TARGET = os.getenv("WAIT_FOR_TARGET", "false").lower() == "true"
ALLOW_BOOTSTRAP_EXECUTION = os.getenv("ALLOW_BOOTSTRAP_EXECUTION", "false").lower() == "true"
MIN_REALTIME_BARS_FOR_EXECUTION = int(os.getenv("MIN_REALTIME_BARS_FOR_EXECUTION", "100"))
INTRADAY_MAX_BAR_SECONDS = int(os.getenv("INTRADAY_MAX_BAR_SECONDS", "600"))  # 10 minutes

MODEL_CACHE_DIR = os.getenv("MODEL_CACHE_DIR", "model_cache")
FORCE_RETRAIN = os.getenv("FORCE_RETRAIN", "false").lower() == "true"
MODEL_ENGINE = os.getenv("MODEL_ENGINE", "PROPHET").upper()  # PROPHET or LR
TARGET_COIN_SYMBOL = os.getenv("TARGET_COIN_SYMBOL", "SOL")

INPUT_MINT_FOR_PRICE = os.getenv("INPUT_MINT_FOR_PRICE", "So11111111111111111111111111111111111111112")
OUTPUT_MINT_FOR_PRICE = os.getenv("OUTPUT_MINT_FOR_PRICE", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
INPUT_DECIMALS = int(os.getenv("INPUT_DECIMALS", "9"))
OUTPUT_DECIMALS = int(os.getenv("OUTPUT_DECIMALS", "6"))

QUOTE_AMOUNT_IN_SMALLEST_UNIT = int(os.getenv("QUOTE_AMOUNT_IN_SMALLEST_UNIT", "100000000"))  # 0.1 SOL

BUY_AMOUNT_USDC = int(os.getenv("BUY_AMOUNT_USDC", "10000000"))
SELL_AMOUNT_SOL = int(os.getenv("SELL_AMOUNT_SOL", "100000000"))

POLL_EVERY_SECONDS = float(os.getenv("POLL_EVERY_SECONDS", "5.0"))
QUOTE_BURST_COUNT = int(os.getenv("QUOTE_BURST_COUNT", "3"))
QUOTE_BURST_SLEEP_SECONDS = float(os.getenv("QUOTE_BURST_SLEEP_SECONDS", "1.0"))
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "10"))
TARGET_LEAD_SECONDS = int(os.getenv("TARGET_LEAD_SECONDS", "60"))

REQUIRE_ULTRA_FOR_TRAINING = os.getenv("REQUIRE_ULTRA_FOR_TRAINING", "true").lower() == "true"

INTRADAY_CADENCE_RULES = ("10min", "5min", "1min")
INTRADAY_CADENCE_WEIGHTS = {"10min": 0.45, "5min": 0.35, "1min": 0.20}
INTRADAY_HORIZON_STEPS = {
    "10min": int(os.getenv("HORIZON_STEPS_10MIN", "6")),
    "5min": int(os.getenv("HORIZON_STEPS_5MIN", "12")),
    "1min": int(os.getenv("HORIZON_STEPS_1MIN", "30")),
}

DAILY_CADENCE_RULES = ("20D", "5D", "1D")
DAILY_CADENCE_WEIGHTS = {"20D": 0.45, "5D": 0.35, "1D": 0.20}
DAILY_HORIZON_STEPS = {
    "20D": int(os.getenv("HORIZON_STEPS_20D", "6")),
    "5D": int(os.getenv("HORIZON_STEPS_5D", "12")),
    "1D": int(os.getenv("HORIZON_STEPS_1D", "30")),
}
HORIZON_STEPS = {**INTRADAY_HORIZON_STEPS, **DAILY_HORIZON_STEPS}
ENABLE_CHAMPION_RUNTIME = os.getenv("ENABLE_CHAMPION_RUNTIME", "true").lower() == "true"
CHAMPION_REFRESH_MAX_AGE_HOURS = float(os.getenv("CHAMPION_REFRESH_MAX_AGE_HOURS", "24"))
CHAMPION_REFRESH_FOLDS = int(os.getenv("CHAMPION_REFRESH_FOLDS", "3"))
CHAMPION_REFRESH_PROFILE = os.getenv("CHAMPION_REFRESH_PROFILE", "daily").strip().lower()

CADENCE_PROFILE_CONFIG = {
    "intraday": {
        "rules": INTRADAY_CADENCE_RULES,
        "weights": INTRADAY_CADENCE_WEIGHTS,
        "horizon_steps": INTRADAY_HORIZON_STEPS,
    },
    "daily": {
        "rules": DAILY_CADENCE_RULES,
        "weights": DAILY_CADENCE_WEIGHTS,
        "horizon_steps": DAILY_HORIZON_STEPS,
    },
}

BUY_THRESHOLD = float(os.getenv("BUY_THRESHOLD", "0.003"))
SELL_THRESHOLD = float(os.getenv("SELL_THRESHOLD", "-0.003"))
MAX_UNCERTAINTY_RATIO = float(os.getenv("MAX_UNCERTAINTY_RATIO", "0.03"))
PROPHET_INTERVAL_WIDTH = float(os.getenv("PROPHET_INTERVAL_WIDTH", "0.8"))
PROPHET_CHANGEPOINT_SIGNIFICANCE = float(
    os.getenv("PROPHET_CHANGEPOINT_SIGNIFICANCE", "0.01")
)
PROPHET_MIN_ROWS_FOR_MCMC = int(os.getenv("PROPHET_MIN_ROWS_FOR_MCMC", "240"))
PROPHET_MIN_RELATIVE_VOL_FOR_MCMC = float(
    os.getenv("PROPHET_MIN_RELATIVE_VOL_FOR_MCMC", "0.01")
)
PROPHET_MAX_REASONABLE_BAND_RATIO = float(
    os.getenv("PROPHET_MAX_REASONABLE_BAND_RATIO", "1.5")
)
PROPHET_UNCERTAINTY_SAMPLES = int(os.getenv("PROPHET_UNCERTAINTY_SAMPLES", "1000"))
PROPHET_BATCH_UNCERTAINTY_SAMPLES = int(
    os.getenv("PROPHET_BATCH_UNCERTAINTY_SAMPLES", "300")
)
PROPHET_INTRADAY_MCMC_SAMPLES = int(
    os.getenv("PROPHET_INTRADAY_MCMC_SAMPLES", "0")
)
PROPHET_DAILY_MCMC_SAMPLES = int(os.getenv("PROPHET_DAILY_MCMC_SAMPLES", "40"))
PROPHET_BATCH_MCMC_SAMPLES = int(os.getenv("PROPHET_BATCH_MCMC_SAMPLES", "0"))

ROOT_DIR = Path(__file__).resolve().parents[2]
SP500_CLOSE_MATRIX_PATH = ROOT_DIR / "data" / "sp500" / "sp500_close_daily.csv"


# =========================================================
# HELPERS
# =========================================================

def safe_float(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(numeric):
        return None
    return numeric


def require_api_key():
    if not JUPITER_API_KEY:
        raise ValueError("Missing JUPITER_API_KEY")


def get_wallet() -> Keypair:
    if not SOLDERS_AVAILABLE:
        raise ImportError("solders is required for transaction signing")
    if not SOLANA_PRIVATE_KEY_B58:
        raise ValueError("Missing SOLANA_PRIVATE_KEY_B58")
    return Keypair.from_base58_string(SOLANA_PRIVATE_KEY_B58)


def load_local_sp500_close_history(symbol: str) -> Optional[pd.DataFrame]:
    from services.trader.sp500_ingest import expected_equity_session_date

    normalized_symbol = (symbol or "").strip().upper().replace(".", "-")
    if not normalized_symbol or not SP500_CLOSE_MATRIX_PATH.exists():
        return None

    close_df = pd.read_csv(SP500_CLOSE_MATRIX_PATH)
    if "ds" not in close_df.columns or normalized_symbol not in close_df.columns:
        return None

    out = close_df[["ds", normalized_symbol]].rename(columns={normalized_symbol: "close"}).copy()
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna(subset=["ds", "close"]).reset_index(drop=True)
    if out.empty:
        return None

    latest_cached_date = pd.Timestamp(out["ds"].iloc[-1]).date().isoformat()
    expected_date = expected_equity_session_date()
    if latest_cached_date < expected_date:
        return None

    out["y"] = out["close"]
    return out


def fetch_fallback_data(
    symbol: str,
    start_date: str = "2023-01-01",
    market_mode: Optional[str] = None,
) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise ImportError(
            "yfinance is required only for fallback downloads. "
            "Install trader requirements or run predict_signal.py with --csv."
        ) from exc
    from services.trader.sp500_ingest import yfinance_inclusive_end_date

    local_sp500_df = load_local_sp500_close_history(symbol)
    if local_sp500_df is not None:
        print(f"[Data Fetcher] Using local S&P500 cache for {symbol} from {SP500_CLOSE_MATRIX_PATH}...")
        return local_sp500_df

    normalized_market_mode = (market_mode or "").strip().lower()
    if normalized_market_mode == "sp500":
        tickers_to_try = [symbol, f"{symbol}-USD"]
    elif normalized_market_mode == "crypto":
        tickers_to_try = [f"{symbol}-USD", symbol]
    else:
        tickers_to_try = [f"{symbol}-USD", symbol]
    df = pd.DataFrame()
    resolved_ticker = None

    for ticker in tickers_to_try:
        print(f"[Data Fetcher] Downloading fallback data for {ticker} from yfinance...")
        candidate_df = yf.download(
            ticker,
            start=start_date,
            end=yfinance_inclusive_end_date(pd.Timestamp.utcnow().date().isoformat()),
            auto_adjust=False,
            progress=False,
        )
        if not candidate_df.empty:
            df = candidate_df
            resolved_ticker = ticker
            break

    if df.empty or resolved_ticker is None:
        raise ValueError(f"No fallback data found for {symbol} (tried {', '.join(tickers_to_try)})")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.reset_index()
    if "Date" in df.columns:
        df = df.rename(columns={"Date": "ds"})
    elif "Datetime" in df.columns:
        df = df.rename(columns={"Datetime": "ds"})

    rename_map = {"Open": "open", "High": "high", "Low": "low", "Close": "close"}
    df = df.rename(columns=rename_map)
    df["y"] = df["close"]
    return df


def _to_naive_timestamp(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        return ts.tz_convert(None)
    return ts


def fetch_live_equity_snapshot(symbol: Optional[str]) -> Optional[Dict[str, Any]]:
    normalized_symbol = (symbol or "").strip().upper().replace(".", "-")
    if not normalized_symbol or any(sep in normalized_symbol for sep in ("→", "->", "/")):
        return None

    try:
        import yfinance as yf
    except ImportError:
        return None

    try:
        ticker = yf.Ticker(normalized_symbol)
        market_cap = None
        shares_outstanding = None
        fast_info = getattr(ticker, "fast_info", None)
        if fast_info is not None:
            for key in ("marketCap", "market_cap"):
                value = safe_float(getattr(fast_info, key, None))
                if value is None and hasattr(fast_info, "get"):
                    value = safe_float(fast_info.get(key))
                if value is not None:
                    market_cap = value
                    break
            for key in ("shares", "sharesOutstanding", "shares_outstanding"):
                value = safe_float(getattr(fast_info, key, None))
                if value is None and hasattr(fast_info, "get"):
                    value = safe_float(fast_info.get(key))
                if value is not None:
                    shares_outstanding = value
                    break
        intraday = ticker.history(period="5d", interval="1m", auto_adjust=False, prepost=True)
        if not intraday.empty and "Close" in intraday.columns:
            close_series = pd.to_numeric(intraday["Close"], errors="coerce").dropna()
            if not close_series.empty:
                return {
                    "price": float(close_series.iloc[-1]),
                    "timestamp": _to_naive_timestamp(close_series.index[-1]),
                    "source": "yfinance_intraday",
                    "marketCap": market_cap,
                    "sharesOutstanding": shares_outstanding,
                }

        if fast_info is not None:
            for key in ("lastPrice", "regularMarketPrice", "last_price", "regular_market_price"):
                value = safe_float(getattr(fast_info, key, None))
                if value is None and hasattr(fast_info, "get"):
                    value = safe_float(fast_info.get(key))
                if value is not None:
                    return {
                        "price": value,
                        "timestamp": _to_naive_timestamp(pd.Timestamp.utcnow()),
                        "source": f"yfinance_fast_info:{key}",
                        "marketCap": market_cap,
                        "sharesOutstanding": shares_outstanding,
                    }
    except Exception:
        return None

    return None


def resolve_reference_market_snapshot(raw_df: pd.DataFrame, symbol: Optional[str]) -> Dict[str, Any]:
    fallback_price = safe_float(raw_df["close"].iloc[-1]) if len(raw_df) else None
    fallback_timestamp = _to_naive_timestamp(raw_df["ds"].iloc[-1]) if len(raw_df) else _to_naive_timestamp(pd.Timestamp.utcnow())

    snapshot = {
        "price": fallback_price,
        "timestamp": fallback_timestamp,
        "source": "historical_close",
    }

    try:
        profile = resolve_cadence_profile(raw_df)
    except Exception:
        return snapshot

    if profile.get("name") != "daily":
        return snapshot

    live_snapshot = fetch_live_equity_snapshot(symbol)
    if live_snapshot is None or live_snapshot.get("price") is None:
        return snapshot

    return live_snapshot


def ensure_raw_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "ds" not in out.columns:
        raise ValueError("CSV must contain column 'ds'")

    out["ds"] = pd.to_datetime(out["ds"], utc=True, errors="coerce").dt.tz_convert(None)
    out = out.dropna(subset=["ds"]).sort_values("ds").reset_index(drop=True)

    if "close" not in out.columns and "y" not in out.columns:
        raise ValueError("CSV must contain 'close' or 'y'")

    if "close" not in out.columns and "y" in out.columns:
        out["close"] = pd.to_numeric(out["y"], errors="coerce")

    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out["open"] = pd.to_numeric(out.get("open", out["close"]), errors="coerce")
    out["high"] = pd.to_numeric(out.get("high", out["close"]), errors="coerce")
    out["low"] = pd.to_numeric(out.get("low", out["close"]), errors="coerce")

    out = out.dropna(subset=["ds", "open", "high", "low", "close"]).copy()
    if len(out) < 100:
        raise ValueError("Need at least ~100 rows of historical data")
    return out


def infer_median_bar_seconds(df: pd.DataFrame) -> Optional[float]:
    ds = pd.to_datetime(df["ds"], errors="coerce").dropna().sort_values()
    if len(ds) < 3:
        return None
    diffs = ds.diff().dropna().dt.total_seconds()
    if len(diffs) == 0:
        return None
    return float(diffs.median())


def is_intraday_df(df: pd.DataFrame, max_bar_seconds: int = INTRADAY_MAX_BAR_SECONDS) -> bool:
    median_sec = infer_median_bar_seconds(df)
    if median_sec is None:
        return False
    return median_sec <= max_bar_seconds


def format_cadence_rule_label(rule: str) -> str:
    normalized = str(rule or "").strip()
    if normalized.endswith("min"):
        return normalized.replace("min", "m")
    if normalized.endswith("D"):
        return normalized.lower()
    return normalized


def resolve_cadence_profile(raw_df: pd.DataFrame) -> Dict[str, Any]:
    profile_name = "intraday" if is_intraday_df(raw_df, max_bar_seconds=INTRADAY_MAX_BAR_SECONDS) else "daily"
    profile = CADENCE_PROFILE_CONFIG[profile_name]
    rules = tuple(profile["rules"])
    weights = dict(profile["weights"])
    horizon_steps = dict(profile["horizon_steps"])
    labels = {rule: format_cadence_rule_label(rule) for rule in rules}
    return {
        "name": profile_name,
        "rules": rules,
        "weights": weights,
        "horizon_steps": horizon_steps,
        "labels": labels,
    }


def resample_ohlc(raw_df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = ensure_raw_df(raw_df).set_index("ds").sort_index()
    return (
        out[["open", "high", "low", "close"]]
        .resample(rule)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
        .reset_index()
    )


def build_training_views_for_rule(raw_df: pd.DataFrame, rule: str) -> Dict[str, pd.DataFrame]:
    bars = resample_ohlc(raw_df, rule)
    return {
        "bars": bars,
        "direction_df": bars[["ds", "close"]].rename(columns={"close": "y"}).copy(),
        "low_df": bars[["ds", "low"]].rename(columns={"low": "y"}).copy(),
        "high_df": bars[["ds", "high"]].rename(columns={"high": "y"}).copy(),
    }


def build_future_grid(last_timestamp: pd.Timestamp, rule: str, steps: int) -> pd.DataFrame:
    last_timestamp = pd.Timestamp(last_timestamp)
    if last_timestamp.tzinfo is not None:
        last_timestamp = last_timestamp.tz_convert("UTC").tz_convert(None)
    future_ds = pd.date_range(start=last_timestamp + pd.Timedelta(rule), periods=steps, freq=rule)
    return pd.DataFrame({"ds": future_ds})


def weighted_timestamp(ts_weight_pairs: List[Tuple[pd.Timestamp, float]]) -> pd.Timestamp:
    total_w = sum(w for _, w in ts_weight_pairs) or 1.0
    avg_ns = int(sum(pd.Timestamp(ts).value * w for ts, w in ts_weight_pairs) / total_w)
    return pd.Timestamp(avg_ns, tz="UTC").tz_convert(None)


def safe_duration_seconds(start_ts: Optional[pd.Timestamp], end_ts: Optional[pd.Timestamp]) -> Optional[float]:
    if start_ts is None or end_ts is None:
        return None
    start = pd.Timestamp(start_ts)
    end = pd.Timestamp(end_ts)
    return float((end - start).total_seconds())


def serialize_curve_points(
    curve: Optional[pd.DataFrame],
    value_col: str,
    *,
    max_points: int = 72,
) -> List[Dict[str, Any]]:
    if curve is None or value_col not in curve.columns:
        return []

    ordered = curve[["ds", value_col]].copy()
    ordered["ds"] = pd.to_datetime(ordered["ds"], errors="coerce")
    ordered[value_col] = pd.to_numeric(ordered[value_col], errors="coerce")
    ordered = ordered.dropna(subset=["ds", value_col]).sort_values("ds").reset_index(drop=True)

    if ordered.empty:
        return []

    if len(ordered) > max_points:
        indices = np.linspace(0, len(ordered) - 1, num=max_points, dtype=int)
        ordered = ordered.iloc[indices].reset_index(drop=True)

    return [
        {
            "timestamp": pd.Timestamp(row["ds"]).isoformat(),
            "value": float(row[value_col]),
        }
        for _, row in ordered.iterrows()
    ]


def serialize_component_points(
    curve: Optional[pd.DataFrame],
    value_col: str,
    *,
    max_points: Optional[int] = None,
    label_builder=None,
) -> List[Dict[str, Any]]:
    if curve is None or value_col not in curve.columns:
        return []

    ordered = curve[["ds", value_col]].copy()
    ordered["ds"] = pd.to_datetime(ordered["ds"], errors="coerce")
    ordered[value_col] = pd.to_numeric(ordered[value_col], errors="coerce")
    ordered = ordered.dropna(subset=["ds", value_col]).sort_values("ds").reset_index(drop=True)

    if ordered.empty:
        return []

    if max_points is not None and len(ordered) > max_points:
        indices = np.linspace(0, len(ordered) - 1, num=max_points, dtype=int)
        ordered = ordered.iloc[indices].reset_index(drop=True)

    points: List[Dict[str, Any]] = []
    for index, row in ordered.iterrows():
        ts = pd.Timestamp(row["ds"])
        label = label_builder(ts, index) if callable(label_builder) else None
        points.append(
            {
                "timestamp": ts.isoformat(),
                "label": label,
                "value": float(row[value_col]),
            }
        )
    return points


def serialize_forecast_plot_points(
    forecast_df: Optional[pd.DataFrame],
    history_df: Optional[pd.DataFrame] = None,
    *,
    max_points: int = 240,
) -> List[Dict[str, Any]]:
    if forecast_df is None:
        return []

    required_cols = {"ds", "yhat", "yhat_lower", "yhat_upper", "trend"}
    if not required_cols.issubset(set(forecast_df.columns)):
        return []

    ordered = forecast_df[["ds", "yhat", "yhat_lower", "yhat_upper", "trend"]].copy()
    ordered["ds"] = pd.to_datetime(ordered["ds"], errors="coerce")
    ordered["yhat"] = pd.to_numeric(ordered["yhat"], errors="coerce")
    ordered["yhat_lower"] = pd.to_numeric(ordered["yhat_lower"], errors="coerce")
    ordered["yhat_upper"] = pd.to_numeric(ordered["yhat_upper"], errors="coerce")
    ordered["trend"] = pd.to_numeric(ordered["trend"], errors="coerce")
    ordered = ordered.dropna(subset=["ds", "yhat"]).sort_values("ds").reset_index(drop=True)
    if ordered.empty:
        return []

    if history_df is not None and {"ds", "y"}.issubset(set(history_df.columns)):
        actual = history_df[["ds", "y"]].copy()
        actual["ds"] = pd.to_datetime(actual["ds"], errors="coerce")
        actual["y"] = pd.to_numeric(actual["y"], errors="coerce")
        actual = actual.dropna(subset=["ds", "y"]).sort_values("ds").drop_duplicates(subset=["ds"])
        ordered = ordered.merge(actual, on="ds", how="left")
        history_end = pd.to_datetime(actual["ds"], errors="coerce").max() if not actual.empty else None
        if history_end is not None and pd.notna(history_end):
            ordered["is_history"] = ordered["ds"] <= history_end
        else:
            ordered["is_history"] = False
    else:
        ordered["y"] = np.nan
        ordered["is_history"] = False

    if len(ordered) > max_points:
        indices = np.linspace(0, len(ordered) - 1, num=max_points, dtype=int)
        ordered = ordered.iloc[indices].reset_index(drop=True)

    points: List[Dict[str, Any]] = []
    for _, row in ordered.iterrows():
        points.append(
            {
                "timestamp": pd.Timestamp(row["ds"]).isoformat(),
                "yhat": safe_float(row.get("yhat")),
                "yhatLower": safe_float(row.get("yhat_lower")),
                "yhatUpper": safe_float(row.get("yhat_upper")),
                "actual": safe_float(row.get("y")),
                "trend": safe_float(row.get("trend")),
                "isHistory": bool(row.get("is_history")),
            }
        )
    return points


def serialize_significant_changepoints(
    model: Optional[Prophet],
    forecast_df: Optional[pd.DataFrame],
    *,
    threshold: float = PROPHET_CHANGEPOINT_SIGNIFICANCE,
) -> List[Dict[str, Any]]:
    if model is None or forecast_df is None or "ds" not in forecast_df.columns:
        return []

    changepoints = getattr(model, "changepoints", None)
    params = getattr(model, "params", {}) or {}
    delta = params.get("delta")
    if changepoints is None or delta is None:
        return []

    cp_series = pd.Series(changepoints)
    if cp_series.empty:
        return []

    delta_values = np.asarray(delta)
    if delta_values.size == 0:
        return []

    mean_delta = np.abs(np.nanmean(delta_values, axis=0)).reshape(-1)
    forecast_lookup = forecast_df[["ds", "trend", "yhat"]].copy()
    forecast_lookup["ds"] = pd.to_datetime(forecast_lookup["ds"], errors="coerce")
    forecast_lookup = forecast_lookup.dropna(subset=["ds"]).sort_values("ds").reset_index(drop=True)
    if forecast_lookup.empty:
        return []

    significant: List[Dict[str, Any]] = []
    for index, cp in enumerate(cp_series):
        if index >= len(mean_delta) or mean_delta[index] < threshold:
            continue
        cp_ts = pd.Timestamp(cp)
        nearest_idx = (forecast_lookup["ds"] - cp_ts).abs().idxmin()
        nearest_row = forecast_lookup.loc[nearest_idx]
        significant.append(
            {
                "timestamp": cp_ts.isoformat(),
                "trend": safe_float(nearest_row.get("trend")),
                "forecast": safe_float(nearest_row.get("yhat")),
                "magnitude": safe_float(mean_delta[index]),
            }
        )
    return significant


def format_component_value(value: Optional[float], value_type: str) -> str:
    numeric = safe_float(value)
    if numeric is None:
        return "unknown"
    if value_type == "percent":
        return f"{numeric * 100:+.2f}%"
    return f"{numeric:,.2f}"


def summarize_component_points(
    title: str,
    points: List[Dict[str, Any]],
    *,
    value_type: str,
) -> Optional[Dict[str, Any]]:
    valid_points = [
        point
        for point in points
        if safe_float(point.get("value")) is not None
    ]
    if not valid_points:
        return None

    peak = max(valid_points, key=lambda point: float(point["value"]))
    trough = min(valid_points, key=lambda point: float(point["value"]))
    peak_label = peak.get("label") or peak.get("timestamp")
    trough_label = trough.get("label") or trough.get("timestamp")
    peak_value = safe_float(peak.get("value"))
    trough_value = safe_float(trough.get("value"))
    strength = max(abs(peak_value or 0.0), abs(trough_value or 0.0))

    return {
        "title": title,
        "peakLabel": peak_label,
        "peakValue": peak_value,
        "troughLabel": trough_label,
        "troughValue": trough_value,
        "strength": strength,
        "summary": (
            f"{title} peaks near {peak_label} ({format_component_value(peak_value, value_type)}) "
            f"and softens near {trough_label} ({format_component_value(trough_value, value_type)})."
        ),
    }


def build_prophet_component_bundle(
    agent: Optional["DirectionAgentWrapper"],
) -> Dict[str, Any]:
    default = {
        "forecast_plot": None,
        "trend_component": None,
        "seasonality_components": {},
        "seasonality_summary": {
            "sourceRule": None,
            "headline": None,
            "strongestComponent": None,
        },
    }
    if agent is None:
        return default

    engine = getattr(agent, "engine", None)
    model = getattr(engine, "model", None)
    train_df = getattr(engine, "train_df", None)
    if not isinstance(engine, ProphetEngineAgent) or model is None or train_df is None:
        return default
    if not uses_daily_rule(agent.config.rule):
        return default

    last_ts = pd.to_datetime(train_df["ds"], errors="coerce").max()
    history_plus_future = pd.concat(
        [
            train_df[["ds"]].copy(),
            build_future_grid(last_ts, agent.config.rule, agent.config.horizon_steps)[["ds"]].copy(),
        ],
        ignore_index=True,
    ).drop_duplicates(subset=["ds"]).sort_values("ds").reset_index(drop=True)

    try:
        trend_forecast = model.predict(history_plus_future)
    except Exception:
        return default

    trend_points = serialize_component_points(
        trend_forecast,
        "trend",
        max_points=120,
        label_builder=lambda ts, _: ts.strftime("%Y-%m-%d"),
    )

    trend_component = (
        {
            "title": "Trend",
            "xAxisLabel": "Date",
            "yAxisLabel": "Trend",
            "valueType": "price",
            "points": trend_points,
        }
        if trend_points
        else None
    )

    forecast_plot = {
        "title": "Prophet forecast",
        "xAxisLabel": "Date",
        "yAxisLabel": "Price forecast",
        "uncertaintyEnabled": bool(
            "yhat_lower" in trend_forecast.columns and "yhat_upper" in trend_forecast.columns
        ),
        "historyEndTimestamp": (
            pd.Timestamp(train_df["ds"].max()).isoformat()
            if not train_df.empty
            else None
        ),
        "points": serialize_forecast_plot_points(
            trend_forecast,
            train_df[["ds", "y"]].copy(),
            max_points=260,
        ),
        "changepoints": serialize_significant_changepoints(model, trend_forecast),
    }

    component_specs = [
        (
            "weekly",
            "Weekly seasonality",
            pd.date_range("2026-01-04", periods=7, freq="D"),
            "Day of week",
            "Weekly seasonality",
            "percent",
            lambda ts, _: ts.day_name(),
            None,
        ),
        (
            "yearly",
            "Yearly seasonality",
            pd.date_range("2026-01-01", periods=366, freq="D"),
            "Day of year",
            "Yearly seasonality",
            "percent",
            lambda ts, _: f"{ts.strftime('%b')} {ts.day}",
            180,
        ),
        (
            "monthly",
            "Monthly seasonality",
            pd.date_range("2026-01-01", periods=31, freq="D"),
            "Day of month",
            "Monthly seasonality",
            "percent",
            lambda ts, _: str(ts.day),
            None,
        ),
        (
            "quarterly",
            "Quarterly seasonality",
            pd.date_range("2026-01-01", periods=92, freq="D"),
            "Day of quarter",
            "Quarterly seasonality",
            "percent",
            lambda ts, _: str((ts - ts.to_period("Q").start_time).days + 1),
            None,
        ),
    ]

    seasonality_components: Dict[str, Any] = {}
    summaries: Dict[str, Any] = {}
    for (
        component_name,
        title,
        ds_values,
        x_axis_label,
        y_axis_label,
        value_type,
        label_builder,
        max_points,
    ) in component_specs:
        try:
            forecast = model.predict(pd.DataFrame({"ds": ds_values}))
        except Exception:
            continue
        if component_name not in forecast.columns:
            continue

        points = serialize_component_points(
            forecast,
            component_name,
            max_points=max_points,
            label_builder=label_builder,
        )
        if not points:
            continue

        seasonality_components[component_name] = {
            "title": title,
            "xAxisLabel": x_axis_label,
            "yAxisLabel": y_axis_label,
            "valueType": value_type,
            "points": points,
        }
        component_summary = summarize_component_points(
            title,
            points,
            value_type=value_type,
        )
        if component_summary is not None:
            summaries[component_name] = component_summary

    strongest_component = None
    headline = None
    if summaries:
        strongest_component = max(
            summaries.items(),
            key=lambda item: safe_float(item[1].get("strength")) or 0.0,
        )[0]
        strongest_summary = summaries.get(strongest_component) or {}
        headline = (
            f"{strongest_summary.get('title', 'Seasonality')} is the strongest recurring pattern, "
            f"with support near {strongest_summary.get('peakLabel') or 'unknown'} "
            f"and softness near {strongest_summary.get('troughLabel') or 'unknown'}."
        )

    seasonality_summary = {
        "sourceRule": format_cadence_rule_label(agent.config.rule),
        "headline": headline,
        "strongestComponent": strongest_component,
        "weekly": summaries.get("weekly"),
        "yearly": summaries.get("yearly"),
        "monthly": summaries.get("monthly"),
        "quarterly": summaries.get("quarterly"),
    }

    return {
        "forecast_plot": forecast_plot,
        "trend_component": trend_component,
        "seasonality_components": seasonality_components,
        "seasonality_summary": seasonality_summary,
    }


def compute_curve_moments(
    curve: Optional[pd.DataFrame],
    value_col: str,
    *,
    reference_price: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    default = {
        "first_moment_price_per_hour": None,
        "first_moment_pct_per_hour": None,
        "second_moment_price_per_hour2": None,
        "second_moment_pct_per_hour2": None,
    }
    if curve is None or value_col not in curve.columns:
        return default

    ordered = curve[["ds", value_col]].copy()
    ordered["ds"] = pd.to_datetime(ordered["ds"], errors="coerce")
    ordered[value_col] = pd.to_numeric(ordered[value_col], errors="coerce")
    ordered = ordered.dropna(subset=["ds", value_col]).sort_values("ds").reset_index(drop=True)
    if len(ordered) < 2:
        return default

    elapsed_hours = (
        ordered["ds"] - ordered["ds"].iloc[0]
    ).dt.total_seconds().astype(float).to_numpy() / 3600.0
    values = ordered[value_col].astype(float).to_numpy()

    unique_mask = np.insert(np.diff(elapsed_hours) > 0, 0, True)
    elapsed_hours = elapsed_hours[unique_mask]
    values = values[unique_mask]
    if len(values) < 2:
        return default

    first_gradient = np.gradient(values, elapsed_hours)
    second_gradient = np.gradient(first_gradient, elapsed_hours) if len(values) >= 3 else np.array([])

    first_moment = float(first_gradient[0])
    second_moment = float(second_gradient[0]) if len(second_gradient) else None
    normalized_ref = float(reference_price) if reference_price not in {None, 0} else None

    return {
        "first_moment_price_per_hour": first_moment,
        "first_moment_pct_per_hour": (
            first_moment / normalized_ref if normalized_ref not in {None, 0.0} else None
        ),
        "second_moment_price_per_hour2": second_moment,
        "second_moment_pct_per_hour2": (
            second_moment / normalized_ref
            if second_moment is not None and normalized_ref not in {None, 0.0}
            else None
        ),
    }


def compute_drawdown_linger(
    curve: Optional[pd.DataFrame],
    value_col: str,
    *,
    reference_price: Optional[float] = None,
) -> Dict[str, Any]:
    default = {
        "drawdown_start_timestamp": None,
        "drawdown_recovery_timestamp": None,
        "drawdown_trough_timestamp": None,
        "drawdown_trough_price": None,
        "drawdown_linger_seconds": None,
        "drawdown_recovery_in_horizon": None,
        "trough_to_recovery_seconds": None,
        "max_drawdown_pct": None,
    }
    if curve is None or value_col not in curve.columns or reference_price in {None, 0}:
        return default

    ordered = curve[["ds", value_col]].copy()
    ordered["ds"] = pd.to_datetime(ordered["ds"], errors="coerce")
    ordered[value_col] = pd.to_numeric(ordered[value_col], errors="coerce")
    ordered = ordered.dropna(subset=["ds", value_col]).sort_values("ds").reset_index(drop=True)
    if len(ordered) < 2:
        return default

    below_df = ordered[ordered[value_col] < float(reference_price)].copy()
    if below_df.empty:
        return default

    drawdown_start_ts = pd.Timestamp(below_df.iloc[0]["ds"])
    drawdown_start_idx = int(below_df.index[0])
    post_drawdown = ordered.iloc[drawdown_start_idx:].copy()
    if post_drawdown.empty:
        return default

    trough_idx = int(post_drawdown[value_col].idxmin())
    trough_row = ordered.loc[trough_idx]
    drawdown_trough_ts = pd.Timestamp(trough_row["ds"])
    drawdown_trough_price = safe_float(trough_row[value_col])

    recovery_candidates = ordered[
        (ordered.index > drawdown_start_idx) & (ordered[value_col] >= float(reference_price))
    ].copy()
    recovery_ts = (
        pd.Timestamp(recovery_candidates.iloc[0]["ds"])
        if not recovery_candidates.empty
        else None
    )

    horizon_end_ts = pd.Timestamp(ordered.iloc[-1]["ds"])
    linger_end_ts = recovery_ts or horizon_end_ts
    drawdown_linger_seconds = safe_duration_seconds(drawdown_start_ts, linger_end_ts)
    trough_to_recovery_seconds = safe_duration_seconds(drawdown_trough_ts, linger_end_ts)
    max_drawdown_pct = (
        (drawdown_trough_price / float(reference_price)) - 1.0
        if drawdown_trough_price is not None
        else None
    )

    return {
        "drawdown_start_timestamp": drawdown_start_ts,
        "drawdown_recovery_timestamp": recovery_ts,
        "drawdown_trough_timestamp": drawdown_trough_ts,
        "drawdown_trough_price": drawdown_trough_price,
        "drawdown_linger_seconds": drawdown_linger_seconds,
        "drawdown_recovery_in_horizon": recovery_ts is not None,
        "trough_to_recovery_seconds": trough_to_recovery_seconds,
        "max_drawdown_pct": max_drawdown_pct,
    }


def compute_upside_spike_sustain(
    curve: Optional[pd.DataFrame],
    value_col: str,
    *,
    reference_price: Optional[float] = None,
) -> Dict[str, Any]:
    default = {
        "spike_start_timestamp": None,
        "spike_peak_timestamp": None,
        "spike_peak_price": None,
        "spike_sustain_seconds": None,
        "spike_fade_timestamp": None,
        "spike_fade_in_horizon": None,
        "peak_to_fade_seconds": None,
        "max_spike_pct": None,
    }
    if curve is None or value_col not in curve.columns or reference_price in {None, 0}:
        return default

    ordered = curve[["ds", value_col]].copy()
    ordered["ds"] = pd.to_datetime(ordered["ds"], errors="coerce")
    ordered[value_col] = pd.to_numeric(ordered[value_col], errors="coerce")
    ordered = ordered.dropna(subset=["ds", value_col]).sort_values("ds").reset_index(drop=True)
    if len(ordered) < 2:
        return default

    above_df = ordered[ordered[value_col] > float(reference_price)].copy()
    if above_df.empty:
        return default

    spike_start_ts = pd.Timestamp(above_df.iloc[0]["ds"])
    spike_start_idx = int(above_df.index[0])
    post_spike = ordered.iloc[spike_start_idx:].copy()
    if post_spike.empty:
        return default

    peak_idx = int(post_spike[value_col].idxmax())
    peak_row = ordered.loc[peak_idx]
    spike_peak_ts = pd.Timestamp(peak_row["ds"])
    spike_peak_price = safe_float(peak_row[value_col])
    if spike_peak_price is None or spike_peak_price <= float(reference_price):
        return default

    amplitude = spike_peak_price - float(reference_price)
    fade_threshold = float(reference_price) + amplitude * 0.4
    fade_candidates = ordered[
        (ordered.index > peak_idx) & (ordered[value_col] <= fade_threshold)
    ].copy()
    fade_ts = pd.Timestamp(fade_candidates.iloc[0]["ds"]) if not fade_candidates.empty else None

    horizon_end_ts = pd.Timestamp(ordered.iloc[-1]["ds"])
    sustain_end_ts = fade_ts or horizon_end_ts
    spike_sustain_seconds = safe_duration_seconds(spike_start_ts, sustain_end_ts)
    peak_to_fade_seconds = safe_duration_seconds(spike_peak_ts, sustain_end_ts)
    max_spike_pct = spike_peak_price / float(reference_price) - 1.0

    return {
        "spike_start_timestamp": spike_start_ts,
        "spike_peak_timestamp": spike_peak_ts,
        "spike_peak_price": spike_peak_price,
        "spike_sustain_seconds": spike_sustain_seconds,
        "spike_fade_timestamp": fade_ts,
        "spike_fade_in_horizon": fade_ts is not None,
        "peak_to_fade_seconds": peak_to_fade_seconds,
        "max_spike_pct": max_spike_pct,
    }


def combine_drawdown_profiles(
    prophet_profile: Optional[Dict[str, Any]],
    timesfm_profile: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    prophet_profile = prophet_profile or {}
    timesfm_profile = timesfm_profile or {}

    prophet_linger = safe_float(prophet_profile.get("drawdown_linger_seconds"))
    timesfm_linger = safe_float(timesfm_profile.get("timesfm_drawdown_linger_seconds"))
    prophet_trough_to_recovery = safe_float(prophet_profile.get("trough_to_recovery_seconds"))
    timesfm_trough_to_recovery = safe_float(timesfm_profile.get("timesfm_trough_to_recovery_seconds"))
    prophet_max_drawdown = safe_float(prophet_profile.get("max_drawdown_pct"))
    timesfm_max_drawdown = safe_float(timesfm_profile.get("timesfm_max_drawdown_pct"))

    effective_linger_candidates = [value for value in [prophet_linger, timesfm_linger] if value is not None]
    effective_recovery_candidates = [
        value
        for value in [prophet_trough_to_recovery, timesfm_trough_to_recovery]
        if value is not None
    ]
    drawdown_recovery_flags = [
        flag
        for flag in [
            prophet_profile.get("drawdown_recovery_in_horizon"),
            timesfm_profile.get("timesfm_drawdown_recovery_in_horizon"),
        ]
        if flag is not None
    ]
    max_drawdown_candidates = [
        value
        for value in [prophet_max_drawdown, timesfm_max_drawdown]
        if value is not None
    ]

    if prophet_linger is not None and timesfm_linger is not None:
        source = "prophet+timesfm"
    elif timesfm_linger is not None:
        source = "timesfm"
    elif prophet_linger is not None:
        source = "prophet"
    else:
        source = "none"

    return {
        "drawdown_linger_consensus_seconds": (
            max(effective_linger_candidates) if effective_linger_candidates else None
        ),
        "trough_to_recovery_consensus_seconds": (
            max(effective_recovery_candidates) if effective_recovery_candidates else None
        ),
        "drawdown_recovery_consensus_in_horizon": (
            all(bool(flag) for flag in drawdown_recovery_flags)
            if drawdown_recovery_flags
            else None
        ),
        "max_drawdown_consensus_pct": (
            min(max_drawdown_candidates) if max_drawdown_candidates else None
        ),
        "drawdown_consensus_source": source,
    }


def combine_spike_profiles(
    prophet_profile: Optional[Dict[str, Any]],
    timesfm_profile: Optional[Dict[str, Any]],
    *,
    feedback_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    prophet_profile = prophet_profile or {}
    timesfm_profile = timesfm_profile or {}
    feedback_snapshot = feedback_snapshot or {}

    def model_weight(model_name: str) -> float:
        models = feedback_snapshot.get("models") or []
        for item in models:
            if str(item.get("model") or "").lower() == model_name:
                return max(0.2, safe_float(item.get("weight")) or 1.0)
        return 1.0

    prophet_sustain = safe_float(prophet_profile.get("spike_sustain_seconds"))
    timesfm_sustain = safe_float(timesfm_profile.get("timesfm_spike_sustain_seconds"))
    prophet_peak_to_fade = safe_float(prophet_profile.get("peak_to_fade_seconds"))
    timesfm_peak_to_fade = safe_float(timesfm_profile.get("timesfm_peak_to_fade_seconds"))
    prophet_max_spike = safe_float(prophet_profile.get("max_spike_pct"))
    timesfm_max_spike = safe_float(timesfm_profile.get("timesfm_max_spike_pct"))
    prophet_fade_flag = prophet_profile.get("spike_fade_in_horizon")
    timesfm_fade_flag = timesfm_profile.get("timesfm_spike_fade_in_horizon")

    prophet_weight = model_weight("prophet") if prophet_sustain is not None else 0.0
    timesfm_weight = model_weight("timesfm") if timesfm_sustain is not None else 0.0
    total_weight = prophet_weight + timesfm_weight

    if total_weight > 0:
        sustain_consensus = (
            ((prophet_sustain or 0.0) * prophet_weight + (timesfm_sustain or 0.0) * timesfm_weight)
            / total_weight
        )
        peak_to_fade_consensus = (
            (
                (prophet_peak_to_fade or 0.0) * prophet_weight
                + (timesfm_peak_to_fade or 0.0) * timesfm_weight
            )
            / total_weight
            if prophet_peak_to_fade is not None or timesfm_peak_to_fade is not None
            else None
        )
        max_spike_consensus = (
            ((prophet_max_spike or 0.0) * prophet_weight + (timesfm_max_spike or 0.0) * timesfm_weight)
            / total_weight
            if prophet_max_spike is not None or timesfm_max_spike is not None
            else None
        )
    else:
        sustain_consensus = None
        peak_to_fade_consensus = None
        max_spike_consensus = None

    fade_flags = [flag for flag in [prophet_fade_flag, timesfm_fade_flag] if flag is not None]
    if prophet_sustain is not None and timesfm_sustain is not None:
        source = "prophet+timesfm(loop)" if feedback_snapshot.get("generatedAt") else "prophet+timesfm"
    elif timesfm_sustain is not None:
        source = "timesfm"
    elif prophet_sustain is not None:
        source = "prophet"
    else:
        source = "none"

    return {
        "spike_sustain_consensus_seconds": sustain_consensus,
        "peak_to_fade_consensus_seconds": peak_to_fade_consensus,
        "spike_fade_consensus_in_horizon": (
            all(bool(flag) for flag in fade_flags) if fade_flags else None
        ),
        "max_spike_consensus_pct": max_spike_consensus,
        "spike_consensus_source": source,
        "prophet_spike_weight": prophet_weight if prophet_weight > 0 else None,
        "timesfm_spike_weight": timesfm_weight if timesfm_weight > 0 else None,
    }


def aggregate_timing_target(
    per_rule: Dict[str, Dict[str, Any]],
    cadence_rules: Tuple[str, ...],
    cadence_weights: Dict[str, float],
    key: str,
) -> Tuple[Optional[pd.Timestamp], Optional[float]]:
    pairs = []
    prices = []
    for rule in cadence_rules:
        payload = per_rule.get(rule, {})
        timing_payload = payload.get(key)
        if timing_payload is None:
            continue
        weight = cadence_weights.get(rule, 0.0)
        pairs.append((timing_payload["predicted_timestamp"], weight))
        prices.append((timing_payload["predicted_price"], weight))

    if not pairs:
        return None, None

    target_ts = weighted_timestamp(pairs)
    total_weight = sum(weight for _, weight in prices) or 1.0
    target_price = sum(price * weight for price, weight in prices) / total_weight
    return target_ts, target_price


def wait_until_target(target_ts: pd.Timestamp, lead_seconds: int = TARGET_LEAD_SECONDS):
    target_ts = pd.Timestamp(target_ts)
    if target_ts.tzinfo is None:
        target_ts = target_ts.tz_localize("UTC")
    else:
        target_ts = target_ts.tz_convert("UTC")
    delta = (target_ts - pd.Timestamp.now(tz="UTC")).total_seconds() - lead_seconds
    if delta > 0:
        print(f"Waiting {delta:.1f}s until target window...")
        time.sleep(delta)


def choose_best_candidate(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = []
    for c in candidates:
        rows.append({
            "requestId": c["requestId"],
            "outAmount": c.get("outAmount", 0) or 0,
            "priceImpact": c.get("priceImpact", 999999) or 999999,
            "totalTime": c.get("totalTime", 999999) or 999999,
            "slippageBps": c.get("slippageBps", 999999) or 999999,
        })
    sdf = pd.DataFrame(rows)
    sdf["final_score"] = (
        sdf["outAmount"].rank(ascending=False, method="average")
        + sdf["priceImpact"].rank(ascending=True, method="average")
        + sdf["totalTime"].rank(ascending=True, method="average")
        + sdf["slippageBps"].rank(ascending=True, method="average")
    )
    best_request_id = sdf.sort_values("final_score", ascending=True).iloc[0]["requestId"]
    return next(c for c in candidates if c["requestId"] == best_request_id)


# =========================================================
# ENGINE AGENTS
# =========================================================

@dataclass
class BaseProphetConfig:
    name: str
    rule: str
    horizon_steps: int
    seasonality_mode: str = "multiplicative"
    changepoint_prior_scale: float = 0.05
    yearly_seasonality: bool = False
    weekly_seasonality: bool = True
    daily_seasonality: bool = True
    growth: str = "linear"
    weight: float = 1.0
    interval_width: float = PROPHET_INTERVAL_WIDTH
    uncertainty_samples: int = PROPHET_UNCERTAINTY_SAMPLES
    mcmc_samples: int = 0


def uses_daily_rule(rule: str) -> bool:
    return str(rule or "").upper().endswith("D")


def uncertainty_profile_for_rule(rule: str, *, batch_mode: bool = False) -> Dict[str, Any]:
    if batch_mode:
        return {
            "interval_width": PROPHET_INTERVAL_WIDTH,
            "uncertainty_samples": max(0, PROPHET_BATCH_UNCERTAINTY_SAMPLES),
            "mcmc_samples": max(0, PROPHET_BATCH_MCMC_SAMPLES),
        }
    return {
        "interval_width": PROPHET_INTERVAL_WIDTH,
        "uncertainty_samples": max(0, PROPHET_UNCERTAINTY_SAMPLES),
        "mcmc_samples": max(
            0,
            PROPHET_DAILY_MCMC_SAMPLES if uses_daily_rule(rule) else PROPHET_INTRADAY_MCMC_SAMPLES,
        ),
    }


def apply_uncertainty_profile(
    config: BaseProphetConfig,
    *,
    batch_mode: bool = False,
) -> BaseProphetConfig:
    profile = uncertainty_profile_for_rule(config.rule, batch_mode=batch_mode)
    return BaseProphetConfig(
        name=config.name,
        rule=config.rule,
        horizon_steps=config.horizon_steps,
        seasonality_mode=config.seasonality_mode,
        changepoint_prior_scale=config.changepoint_prior_scale,
        yearly_seasonality=config.yearly_seasonality,
        weekly_seasonality=config.weekly_seasonality,
        daily_seasonality=config.daily_seasonality,
        growth=config.growth,
        weight=config.weight,
        interval_width=float(profile["interval_width"]),
        uncertainty_samples=int(profile["uncertainty_samples"]),
        mcmc_samples=int(profile["mcmc_samples"]),
    )


def serialize_uncertainty_settings(
    config: BaseProphetConfig,
    runtime: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    runtime = runtime or {}
    return {
        "intervalWidth": float(config.interval_width),
        "uncertaintySamples": int(config.uncertainty_samples),
        "mcmcSamples": int(config.mcmc_samples),
        "includesSeasonalityUncertainty": bool(config.mcmc_samples > 0),
        "trendFlexibility": float(config.changepoint_prior_scale),
        "effectiveMcmcSamples": int(runtime.get("effective_mcmc_samples", config.mcmc_samples)),
        "fitMode": runtime.get("fit_mode", "default"),
        "stabilityFallbackApplied": bool(runtime.get("fallback_applied", False)),
        "fallbackReason": runtime.get("fallback_reason"),
        "relativeVolatility": runtime.get("relative_volatility"),
        "bandRatio": runtime.get("band_ratio"),
    }


def relative_training_volatility(df: pd.DataFrame) -> Optional[float]:
    if "y" not in df.columns:
        return None
    series = pd.to_numeric(df["y"], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if len(series) < 10:
        return None
    mean_level = float(series.abs().mean())
    if mean_level <= 0:
        return None
    rel_vol = float(series.std(ddof=0) / mean_level)
    return rel_vol if np.isfinite(rel_vol) else None


def seasonality_defaults_for_rule(rule: str) -> Dict[str, bool]:
    if uses_daily_rule(rule):
        return {
            "yearly_seasonality": False,
            "weekly_seasonality": True,
            "daily_seasonality": False,
        }
    return {
        "yearly_seasonality": False,
        "weekly_seasonality": True,
        "daily_seasonality": True,
    }


class ProphetEngineAgent:
    def __init__(self, config: BaseProphetConfig):
        self.config = config
        self.model: Optional[Prophet] = None
        self.train_df: Optional[pd.DataFrame] = None
        self.fitted = False
        self.uncertainty_runtime: Dict[str, Any] = {
            "fit_mode": "default",
            "effective_mcmc_samples": int(config.mcmc_samples),
            "fallback_applied": False,
            "fallback_reason": None,
            "relative_volatility": None,
            "band_ratio": None,
        }

    def load_model(self, cache_dir: str):
        filepath = os.path.join(cache_dir, f"{self.config.name}.json")
        if not os.path.exists(filepath):
            return None
        try:
            with open(filepath, "r") as f:
                return model_from_json(json.load(f))
        except Exception:
            return None

    def save_model(self, cache_dir: str):
        if not self.fitted or self.model is None:
            return
        os.makedirs(cache_dir, exist_ok=True)
        try:
            with open(os.path.join(cache_dir, f"{self.config.name}.json"), "w") as f:
                json.dump(model_to_json(self.model), f)
        except Exception:
            pass

    def _warm_start_params(self, model) -> dict:
        def scalar_first(x):
            arr = np.asarray(x)
            return float(arr.reshape(-1)[0])
        return {
            "k": scalar_first(model.params["k"]),
            "m": scalar_first(model.params["m"]),
            "sigma_obs": scalar_first(model.params["sigma_obs"]),
            "delta": np.asarray(model.params["delta"])[0],
            "beta": np.asarray(model.params["beta"])[0],
        }

    def _build_model(
        self,
        *,
        mcmc_samples: Optional[int] = None,
        uncertainty_samples: Optional[int] = None,
    ) -> Prophet:
        model = Prophet(
            seasonality_mode=self.config.seasonality_mode,
            changepoint_prior_scale=self.config.changepoint_prior_scale,
            yearly_seasonality=self.config.yearly_seasonality,
            weekly_seasonality=self.config.weekly_seasonality,
            daily_seasonality=self.config.daily_seasonality,
            growth=self.config.growth,
            interval_width=self.config.interval_width,
            uncertainty_samples=(
                self.config.uncertainty_samples
                if uncertainty_samples is None
                else uncertainty_samples
            ),
            mcmc_samples=self.config.mcmc_samples if mcmc_samples is None else mcmc_samples,
        )
        if uses_daily_rule(self.config.rule):
            model.add_seasonality(
                name="monthly",
                period=30.5,
                fourier_order=10,
                prior_scale=0.10,
                mode=self.config.seasonality_mode,
            )
            model.add_seasonality(
                name="quarterly",
                period=92.25,
                fourier_order=15,
                prior_scale=0.10,
                mode=self.config.seasonality_mode,
            )
            model.add_seasonality(
                name="yearly",
                period=365.0,
                fourier_order=15,
                prior_scale=0.10,
                mode=self.config.seasonality_mode,
            )
        return model

    def _select_effective_mcmc_samples(self, df: pd.DataFrame) -> Tuple[int, Optional[str]]:
        requested = int(self.config.mcmc_samples)
        if requested <= 0:
            return 0, None
        if not uses_daily_rule(self.config.rule):
            return requested, None
        if self.config.growth == "flat":
            return 0, "flat_growth_daily"
        if len(df) < PROPHET_MIN_ROWS_FOR_MCMC:
            return 0, "insufficient_daily_history"
        relative_vol = relative_training_volatility(df)
        self.uncertainty_runtime["relative_volatility"] = relative_vol
        if relative_vol is not None and relative_vol < PROPHET_MIN_RELATIVE_VOL_FOR_MCMC:
            return 0, "low_relative_volatility"
        if str(self.config.rule).upper() in {"1D", "5D"}:
            return 0, "short_horizon_daily_rule"
        return requested, None

    def _forecast_band_ratio(self, model: Prophet, df: pd.DataFrame) -> Optional[float]:
        if df.empty:
            return None
        probe = df[["ds"]].tail(min(len(df), 90)).copy()
        try:
            forecast = model.predict(probe)
        except Exception:
            return None
        required = {"yhat", "yhat_lower", "yhat_upper"}
        if not required.issubset(set(forecast.columns)):
            return None
        band = pd.to_numeric(forecast["yhat_upper"], errors="coerce") - pd.to_numeric(
            forecast["yhat_lower"], errors="coerce"
        )
        center = pd.to_numeric(forecast["yhat"], errors="coerce").abs()
        valid = pd.DataFrame({"band": band, "center": center}).replace([np.inf, -np.inf], np.nan).dropna()
        if valid.empty:
            return None
        mean_center = float(valid["center"].mean())
        if mean_center <= 0:
            return None
        ratio = float(valid["band"].mean() / mean_center)
        return ratio if np.isfinite(ratio) else None

    def _fit_model(self, model: Prophet, df: pd.DataFrame, init: Optional[dict] = None):
        effective_mcmc = int(getattr(model, "mcmc_samples", 0) or 0)
        if effective_mcmc > 0:
            if init is not None:
                return model.fit(df, init=init, show_progress=False)
            return model.fit(df, show_progress=False)
        if init is not None:
            return model.fit(df, init=init)
        return model.fit(df)

    def fit(self, df: pd.DataFrame, prev_model=None, use_warm_start: bool = False):
        self.train_df = df.copy()
        self.uncertainty_runtime = {
            "fit_mode": "default",
            "effective_mcmc_samples": int(self.config.mcmc_samples),
            "fallback_applied": False,
            "fallback_reason": None,
            "relative_volatility": relative_training_volatility(df),
            "band_ratio": None,
        }
        effective_mcmc_samples, mcmc_reason = self._select_effective_mcmc_samples(df)
        self.model = self._build_model(mcmc_samples=effective_mcmc_samples)
        if mcmc_reason:
            self.uncertainty_runtime.update(
                {
                    "fit_mode": "stability_precheck_fallback",
                    "effective_mcmc_samples": int(effective_mcmc_samples),
                    "fallback_applied": True,
                    "fallback_reason": mcmc_reason,
                }
            )

        init = None
        if use_warm_start and prev_model is not None:
            try:
                if getattr(prev_model, "n_changepoints", None) == getattr(self.model, "n_changepoints", None):
                    init = self._warm_start_params(prev_model)
            except Exception:
                init = None

        try:
            self._fit_model(self.model, df, init=init)
        except Exception:
            self.model = self._build_model(mcmc_samples=0)
            self._fit_model(self.model, df)
            self.uncertainty_runtime.update(
                {
                    "fit_mode": "fit_exception_fallback",
                    "effective_mcmc_samples": 0,
                    "fallback_applied": True,
                    "fallback_reason": "fit_exception",
                }
            )

        band_ratio = self._forecast_band_ratio(self.model, df)
        self.uncertainty_runtime["band_ratio"] = band_ratio
        if (
            int(self.uncertainty_runtime.get("effective_mcmc_samples", 0)) > 0
            and (band_ratio is None or band_ratio > PROPHET_MAX_REASONABLE_BAND_RATIO)
        ):
            self.model = self._build_model(mcmc_samples=0)
            self._fit_model(self.model, df)
            self.uncertainty_runtime.update(
                {
                    "fit_mode": "band_ratio_fallback",
                    "effective_mcmc_samples": 0,
                    "fallback_applied": True,
                    "fallback_reason": (
                        "nonfinite_band_ratio" if band_ratio is None else "excessive_band_ratio"
                    ),
                }
            )
            self.uncertainty_runtime["band_ratio"] = self._forecast_band_ratio(self.model, df)

        self.fitted = True
        return self

    def uncertainty_settings(self) -> Dict[str, Any]:
        return serialize_uncertainty_settings(self.config, self.uncertainty_runtime)

    def next_horizon_forecast(self) -> pd.DataFrame:
        last_ts = pd.to_datetime(self.train_df["ds"], errors="coerce").max()
        future = build_future_grid(last_ts, self.config.rule, self.config.horizon_steps)
        pred = self.model.predict(future)
        cols = ["ds", "yhat", "yhat_lower", "yhat_upper"]
        for extra in ["trend", "weekly", "monthly", "yearly"]:
            if extra in pred.columns:
                cols.append(extra)
        return pred[cols].copy()


class LinearRegressionEngineAgent:
    def __init__(self, config: BaseProphetConfig):
        self.config = config
        self.model = LinearRegression()
        self.train_df: Optional[pd.DataFrame] = None
        self.fitted = False

    def load_model(self, cache_dir: str):
        return None

    def save_model(self, cache_dir: str):
        return None

    def fit(self, df: pd.DataFrame, prev_model=None, use_warm_start: bool = False):
        self.train_df = df.copy()
        X = np.arange(len(self.train_df)).reshape(-1, 1)
        self.model.fit(X, self.train_df["y"].values)
        self.fitted = True
        return self

    def next_horizon_forecast(self) -> pd.DataFrame:
        last_idx = len(self.train_df)
        X_future = np.arange(last_idx, last_idx + self.config.horizon_steps).reshape(-1, 1)
        preds = self.model.predict(X_future)
        last_ts = pd.to_datetime(self.train_df["ds"], errors="coerce").max()
        future = build_future_grid(last_ts, self.config.rule, self.config.horizon_steps)
        future["yhat"] = preds
        future["yhat_lower"] = preds * 0.99
        future["yhat_upper"] = preds * 1.01
        for extra in ["trend", "weekly", "monthly", "yearly"]:
            future[extra] = 0.0
        return future[["ds", "yhat", "yhat_lower", "yhat_upper", "trend", "weekly", "monthly", "yearly"]].copy()


def get_base_agent(config: BaseProphetConfig):
    if MODEL_ENGINE == "LR":
        return LinearRegressionEngineAgent(config)
    return ProphetEngineAgent(config)


class DirectionAgentWrapper:
    def __init__(self, config: BaseProphetConfig):
        self.config = config
        self.engine = get_base_agent(config)

    def load_model(self, cache_dir: str):
        return self.engine.load_model(cache_dir)

    def save_model(self, cache_dir: str):
        return self.engine.save_model(cache_dir)

    def fit(self, df: pd.DataFrame, prev_model=None, use_warm_start: bool = False):
        return self.engine.fit(df, prev_model=prev_model, use_warm_start=use_warm_start)

    def full_curve(self) -> pd.DataFrame:
        fcst = self.engine.next_horizon_forecast()
        fcst["agent"] = self.config.name
        fcst["weight"] = self.config.weight
        return fcst

    # def decision(self) -> Dict[str, Any]:
    #     fcst = self.engine.next_horizon_forecast()
    #     last_price = float(self.engine.train_df["y"].iloc[-1])

    #     first_price = float(fcst.iloc[0]["yhat"])
    #     last_future_price = float(fcst.iloc[-1]["yhat"])
    #     session_mean = float(fcst["yhat"].mean())

    #     open_ret = (first_price / last_price) - 1.0 if last_price else 0.0
    #     close_ret = (last_future_price / last_price) - 1.0 if last_price else 0.0
    #     mean_ret = (session_mean / last_price) - 1.0 if last_price else 0.0

    #     score = 0.25 * open_ret + 0.50 * close_ret + 0.25 * mean_ret
    #     avg_band = float((fcst["yhat_upper"] - fcst["yhat_lower"]).mean())
    #     uncertainty_ratio = avg_band / max(abs(session_mean), 1e-8)

    #     if score >= BUY_THRESHOLD and uncertainty_ratio < MAX_UNCERTAINTY_RATIO:
    #         action = "BUY"
    #     elif score <= SELL_THRESHOLD and uncertainty_ratio < MAX_UNCERTAINTY_RATIO:
    #         action = "SELL"
    #     else:
    #         action = "HOLD"

    #     return {
    #         "agent": self.config.name,
    #         "action": action,
    #         "score": score,
    #         "uncertainty_ratio": uncertainty_ratio,
    #         "weight": self.config.weight,
    #     }
    def decision(self) -> Dict[str, Any]:
        fcst = self.engine.next_horizon_forecast()
        last_price = float(self.engine.train_df["y"].iloc[-1])

        first_price = float(fcst.iloc[0]["yhat"])
        last_future_price = float(fcst.iloc[-1]["yhat"])
        session_mean = float(fcst["yhat"].mean())

        open_ret = (first_price / last_price) - 1.0 if last_price else 0.0
        close_ret = (last_future_price / last_price) - 1.0 if last_price else 0.0
        mean_ret = (session_mean / last_price) - 1.0 if last_price else 0.0

        score = 0.25 * open_ret + 0.50 * close_ret + 0.25 * mean_ret
        avg_band = float((fcst["yhat_upper"] - fcst["yhat_lower"]).mean())
        uncertainty_ratio = avg_band / max(abs(session_mean), 1e-8)

        if score >= BUY_THRESHOLD and uncertainty_ratio < MAX_UNCERTAINTY_RATIO:
            action = "BUY"
        elif score <= SELL_THRESHOLD and uncertainty_ratio < MAX_UNCERTAINTY_RATIO:
            action = "SELL"
        else:
            action = "HOLD"

        gold = last_price   # 현재 시점의 실제값을 gold로 사용

        return {
            "agent": self.config.name,
            "action": action,
            "score": score,
            "uncertainty_ratio": uncertainty_ratio,
            "weight": self.config.weight,
            "gold": gold,
        }


class TimingAgentWrapper:
    def __init__(self, config: BaseProphetConfig, mode: str = "low"):
        self.config = config
        self.engine = get_base_agent(config)
        self.mode = mode

    def load_model(self, cache_dir: str):
        return self.engine.load_model(cache_dir)

    def save_model(self, cache_dir: str):
        return self.engine.save_model(cache_dir)

    def fit(self, df: pd.DataFrame, prev_model=None, use_warm_start: bool = False):
        return self.engine.fit(df, prev_model=prev_model, use_warm_start=use_warm_start)

    def aggregate_point(self) -> Dict[str, Any]:
        fcst = self.engine.next_horizon_forecast()
        row = fcst.loc[fcst["yhat"].idxmin()] if self.mode == "low" else fcst.loc[fcst["yhat"].idxmax()]
        return {
            "agent": self.config.name,
            "predicted_timestamp": pd.Timestamp(row["ds"]),
            "predicted_price": float(row["yhat"]),
            "weight": self.config.weight,
        }

    def full_curve(self) -> pd.DataFrame:
        fcst = self.engine.next_horizon_forecast()
        fcst["agent"] = self.config.name
        fcst["weight"] = self.config.weight
        return fcst


# =========================================================
# COORDINATORS
# =========================================================

@dataclass
class DirectionCoordinator:
    agents: List[DirectionAgentWrapper] = field(default_factory=list)

    def fit_all(self, df: pd.DataFrame, prev_agents=None, cache_dir: str = None, force_retrain: bool = False):
        prev_agents = prev_agents or []
        for i, a in enumerate(self.agents):
            use_warm_start = False
            prev_model = None
            if i < len(prev_agents) and prev_agents[i] is not None:
                prev_model = getattr(prev_agents[i].engine, "model", None)
                use_warm_start = True
            elif cache_dir and not force_retrain:
                cached = a.load_model(cache_dir)
                if cached is not None:
                    prev_model = cached
                    use_warm_start = True
            a.fit(df, prev_model=prev_model, use_warm_start=use_warm_start)
            if cache_dir and (force_retrain or prev_model is None):
                a.save_model(cache_dir)
        return self

    def aggregate(self) -> Dict[str, Any]:
        details = pd.DataFrame([a.decision() for a in self.agents])
        total_weight = details["weight"].sum() or 1.0
        weighted_score = float((details["score"] * details["weight"]).sum() / total_weight)
        buy_weight = float(details.loc[details["action"] == "BUY", "weight"].sum())
        sell_weight = float(details.loc[details["action"] == "SELL", "weight"].sum())
        hold_weight = float(details.loc[details["action"] == "HOLD", "weight"].sum())

        if buy_weight > max(sell_weight, hold_weight) and weighted_score > 0:
            final_action = "BUY"
        elif sell_weight > max(buy_weight, hold_weight) and weighted_score < 0:
            final_action = "SELL"
        else:
            final_action = "HOLD"

        current_price = None
        current_timestamp = None
        first_below_current_timestamp = None
        time_to_below_current_seconds = None
        curve_moments = compute_curve_moments(None, "weighted_yhat")

        try:
            curves = []
            for a in self.agents:
                fcst = a.full_curve()[["ds", "yhat"]].rename(columns={"yhat": f"yhat__{a.config.name}"})
                curves.append(fcst)

            if curves:
                merged = curves[0]
                for c in curves[1:]:
                    merged = merged.merge(c, on="ds", how="inner")

                total_curve_weight = sum(a.config.weight for a in self.agents) or 1.0
                merged["weighted_yhat"] = sum(
                    merged[f"yhat__{a.config.name}"] * a.config.weight for a in self.agents
                ) / total_curve_weight

                first_agent_train_df = self.agents[0].engine.train_df
                current_price = float(first_agent_train_df["y"].iloc[-1])
                current_timestamp = pd.Timestamp(first_agent_train_df["ds"].iloc[-1])
                curve_moments = compute_curve_moments(
                    merged,
                    "weighted_yhat",
                    reference_price=current_price,
                )

                below_df = merged[merged["weighted_yhat"] < current_price].sort_values("ds")
                if not below_df.empty:
                    first_below_current_timestamp = pd.Timestamp(below_df.iloc[0]["ds"])
                    time_to_below_current_seconds = float(
                        (first_below_current_timestamp - current_timestamp).total_seconds()
                    )
        except Exception:
            pass

        return {
            "final_action": final_action,
            "weighted_score": weighted_score,
            "details": details,
            "uncertainty_settings": (
                self.agents[0].engine.uncertainty_settings()
                if self.agents and isinstance(self.agents[0].engine, ProphetEngineAgent)
                else serialize_uncertainty_settings(self.agents[0].config)
                if self.agents
                else None
            ),
            "current_price": current_price,
            "current_timestamp": current_timestamp,
            "first_below_current_timestamp": first_below_current_timestamp,
            "time_to_below_current_seconds": time_to_below_current_seconds,
            "first_moment_price_per_hour": curve_moments.get("first_moment_price_per_hour"),
            "first_moment_pct_per_hour": curve_moments.get("first_moment_pct_per_hour"),
            "second_moment_price_per_hour2": curve_moments.get("second_moment_price_per_hour2"),
            "second_moment_pct_per_hour2": curve_moments.get("second_moment_pct_per_hour2"),
        }


@dataclass
class TimingCoordinator:
    agents: List[TimingAgentWrapper] = field(default_factory=list)
    mode: str = "low"

    def fit_all(self, df: pd.DataFrame, prev_agents=None, cache_dir: str = None, force_retrain: bool = False):
        prev_agents = prev_agents or []
        for i, a in enumerate(self.agents):
            use_warm_start = False
            prev_model = None
            if i < len(prev_agents) and prev_agents[i] is not None:
                prev_model = getattr(prev_agents[i].engine, "model", None)
                use_warm_start = True
            elif cache_dir and not force_retrain:
                cached = a.load_model(cache_dir)
                if cached is not None:
                    prev_model = cached
                    use_warm_start = True
            a.fit(df, prev_model=prev_model, use_warm_start=use_warm_start)
            if cache_dir and (force_retrain or prev_model is None):
                a.save_model(cache_dir)
        return self

    def aggregate(self) -> Dict[str, Any]:
        curves = [a.full_curve()[["ds", "yhat"]].rename(columns={"yhat": f"yhat__{a.config.name}"}) for a in self.agents]
        merged = curves[0]
        for c in curves[1:]:
            merged = merged.merge(c, on="ds", how="inner")

        total_weight = sum(a.config.weight for a in self.agents) or 1.0
        merged["weighted_yhat"] = sum(merged[f"yhat__{a.config.name}"] * a.config.weight for a in self.agents) / total_weight
        row = merged.loc[merged["weighted_yhat"].idxmin()] if self.mode == "low" else merged.loc[merged["weighted_yhat"].idxmax()]
        return {
            "predicted_timestamp": pd.Timestamp(row["ds"]),
            "predicted_price": float(row["weighted_yhat"]),
        }


def make_cfgs(prefix: str, rule: str, hs: int, symbol: Optional[str] = None) -> List[BaseProphetConfig]:
    runtime_symbol = (symbol or "").strip().upper()
    task_lookup = {"dir": "direction", "low": "low", "high": "high"}
    champion_task = task_lookup.get(prefix)
    seasonality_kwargs = seasonality_defaults_for_rule(rule)
    changepoint_prior_scale = 0.003 if uses_daily_rule(rule) else 0.05

    if ENABLE_CHAMPION_RUNTIME and runtime_symbol and champion_task:
        try:
            from services.trader.champion_prophet import load_runtime_champion_config

            champion_cfg = load_runtime_champion_config(
                symbol=runtime_symbol,
                task=champion_task,
                rule=rule,
                default_horizon_steps=hs,
            )
            if champion_cfg is not None:
                return [apply_uncertainty_profile(champion_cfg, batch_mode=False)]
        except Exception:
            pass

    return [
        apply_uncertainty_profile(
            BaseProphetConfig(
                name=f"{prefix}_base_{rule}",
                rule=rule,
                horizon_steps=hs,
                changepoint_prior_scale=changepoint_prior_scale,
                weight=1.0,
                **seasonality_kwargs,
            ),
            batch_mode=False,
        ),
        apply_uncertainty_profile(
            BaseProphetConfig(
                name=f"{prefix}_flat_{rule}",
                rule=rule,
                horizon_steps=hs,
                growth="flat",
                changepoint_prior_scale=changepoint_prior_scale,
                weight=0.8,
                **seasonality_kwargs,
            ),
            batch_mode=False,
        ),
    ]


def build_direction_agents(rule: str, horizon_steps: int, symbol: Optional[str] = None) -> List[DirectionAgentWrapper]:
    return [DirectionAgentWrapper(c) for c in make_cfgs("dir", rule, horizon_steps, symbol=symbol)]


def build_low_agents(rule: str, horizon_steps: int, symbol: Optional[str] = None) -> List[TimingAgentWrapper]:
    return [TimingAgentWrapper(c, "low") for c in make_cfgs("low", rule, horizon_steps, symbol=symbol)]


def build_high_agents(rule: str, horizon_steps: int, symbol: Optional[str] = None) -> List[TimingAgentWrapper]:
    return [TimingAgentWrapper(c, "high") for c in make_cfgs("high", rule, horizon_steps, symbol=symbol)]


# =========================================================
# JUPITER CLIENTS
# =========================================================

class JupiterSwapClient:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Missing JUPITER_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({"x-api-key": api_key})

    def get_order(self, input_mint: str, output_mint: str, amount: int, taker: str) -> Dict[str, Any]:
        r = self.session.get(
            ORDER_URL,
            params={"inputMint": input_mint, "outputMint": output_mint, "amount": str(amount), "taker": taker},
        )
        r.raise_for_status()
        j = r.json()
        j["_fetched_at"] = pd.Timestamp.now(tz="UTC").isoformat()
        return j

    def collect_candidate_orders(self, input_mint: str, output_mint: str, amount: int, taker: str, rounds: int) -> List[Dict[str, Any]]:
        cands = []
        for _ in range(rounds):
            try:
                raw = self.get_order(input_mint, output_mint, amount, taker)
                cands.append({
                    "requestId": raw.get("requestId"),
                    "outAmount": float(raw.get("outAmount", 0) or 0),
                    "priceImpact": float(raw.get("priceImpact", 0) or 0),
                    "totalTime": float(raw.get("totalTime", 0) or 0),
                    "slippageBps": float(raw.get("slippageBps", 0) or 0),
                    "_raw_order": raw,
                })
            except Exception:
                pass
            time.sleep(QUOTE_BURST_SLEEP_SECONDS)
        return cands

    def sign_order_transaction(self, order_response: Dict[str, Any], wallet: Keypair) -> str:
        if not SOLDERS_AVAILABLE:
            raise ImportError("solders is required for transaction signing")
        tx_b64 = order_response.get("transaction")
        if not tx_b64:
            raise ValueError("Order response does not include transaction")
        tx_bytes = base64.b64decode(tx_b64)
        tx = VersionedTransaction.from_bytes(tx_bytes)
        required_signers = list(tx.message.account_keys[:tx.message.header.num_required_signatures])
        if wallet.pubkey() not in required_signers:
            raise ValueError(f"Wallet pubkey {wallet.pubkey()} is not in required signer set")
        signer_index = required_signers.index(wallet.pubkey())
        sigs = list(tx.signatures)
        sigs[signer_index] = wallet.sign_message(to_bytes_versioned(tx.message))
        signed_tx = VersionedTransaction.populate(tx.message, sigs)
        return base64.b64encode(bytes(signed_tx)).decode("utf-8")

    def execute_order(self, signed_tx_b64: str, request_id: str) -> Dict[str, Any]:
        r = self.session.post(EXECUTE_URL, json={"signedTransaction": signed_tx_b64, "requestId": request_id})
        r.raise_for_status()
        return r.json()


@dataclass
class JupiterQuotePoller:
    api_key: str
    input_mint: str
    output_mint: str
    input_decimals: int
    output_decimals: int
    amount_in_smallest_unit: int
    require_ultra: bool = True

    def get_snapshot(self) -> Optional[Dict[str, Any]]:
        try:
            r = requests.get(
                ORDER_URL,
                headers={"x-api-key": self.api_key},
                params={
                    "inputMint": self.input_mint,
                    "outputMint": self.output_mint,
                    "amount": str(self.amount_in_smallest_unit),
                },
            )
            if r.status_code != 200:
                return None
            j = r.json()
            if self.require_ultra and j.get("mode") != "ultra":
                return None
            in_amt = float(j.get("inAmount", 0) or 0)
            out_amt = float(j.get("outAmount", 0) or 0)
            if in_amt <= 0 or out_amt <= 0:
                return None
            price = (out_amt / (10 ** self.output_decimals)) / (in_amt / (10 ** self.input_decimals))
            return {
                "ds": pd.Timestamp.now(tz="UTC"),
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "y": price,
                "slippageBps": float(j.get("slippageBps", 0) or 0),
                "priceImpact": float(j.get("priceImpact", 0) or 0),
                "totalTime": float(j.get("totalTime", 0) or 0),
            }
        except Exception:
            return None


# =========================================================
# MULTI-RESOLUTION RUNTIME
# =========================================================

@dataclass
class MultiResolutionRuntime:
    raw_df: pd.DataFrame
    symbol: Optional[str] = None
    direction_states: Dict[str, DirectionCoordinator] = field(default_factory=dict)
    low_states: Dict[str, Optional[TimingCoordinator]] = field(default_factory=dict)
    high_states: Dict[str, Optional[TimingCoordinator]] = field(default_factory=dict)
    timing_enabled: bool = False
    cadence_profile: str = "intraday"
    cadence_rules: Tuple[str, ...] = field(default_factory=lambda: INTRADAY_CADENCE_RULES)
    cadence_weights: Dict[str, float] = field(default_factory=lambda: dict(INTRADAY_CADENCE_WEIGHTS))
    horizon_steps: Dict[str, int] = field(default_factory=lambda: dict(INTRADAY_HORIZON_STEPS))
    cadence_labels: Dict[str, str] = field(default_factory=lambda: {
        rule: format_cadence_rule_label(rule) for rule in INTRADAY_CADENCE_RULES
    })
    champion_refresh: Dict[str, Any] = field(default_factory=dict)

    def _update_timing_enabled(self):
        profile = resolve_cadence_profile(self.raw_df)
        self.cadence_profile = profile["name"]
        self.cadence_rules = profile["rules"]
        self.cadence_weights = profile["weights"]
        self.horizon_steps = profile["horizon_steps"]
        self.cadence_labels = profile["labels"]
        self.symbol = ((self.symbol or os.getenv("TARGET_COIN_SYMBOL", "")) or "").strip().upper() or None
        self.timing_enabled = True
        return self.timing_enabled

    def _refresh_champion_configs_if_needed(self):
        self.champion_refresh = {}
        if not ENABLE_CHAMPION_RUNTIME or not self.symbol:
            return
        if CHAMPION_REFRESH_PROFILE == "daily" and self.cadence_profile != "daily":
            return

        try:
            from services.trader.reinforcement import maybe_run_daily_reinforcement_once
            from services.trader.champion_prophet import ensure_symbol_champion_configs

            reinforcement_status = maybe_run_daily_reinforcement_once()
            self.champion_refresh = ensure_symbol_champion_configs(
                symbol=self.symbol,
                raw_df=self.raw_df,
                cadence_rules=self.cadence_rules,
                folds=CHAMPION_REFRESH_FOLDS,
                max_age_hours=CHAMPION_REFRESH_MAX_AGE_HOURS,
            )
            self.champion_refresh["reinforcement"] = reinforcement_status
        except Exception as exc:
            self.champion_refresh = {
                "symbol": self.symbol,
                "status": "error",
                "reason": str(exc),
            }

    def bootstrap(self):
        self.raw_df = ensure_raw_df(self.raw_df)
        self._update_timing_enabled()
        self._refresh_champion_configs_if_needed()
        print(
            f"\n=== BOOTSTRAP (ENGINE: {MODEL_ENGINE}, PROFILE: {self.cadence_profile}, TIMING ENABLED: {self.timing_enabled}) ==="
        )
        for r in self.cadence_rules:
            views = build_training_views_for_rule(self.raw_df, r)
            self.direction_states[r] = DirectionCoordinator(build_direction_agents(r, self.horizon_steps[r], symbol=self.symbol)).fit_all(
                views["direction_df"], cache_dir=MODEL_CACHE_DIR, force_retrain=FORCE_RETRAIN
            )
            if self.timing_enabled:
                self.low_states[r] = TimingCoordinator(build_low_agents(r, self.horizon_steps[r], symbol=self.symbol), "low").fit_all(
                    views["low_df"], cache_dir=MODEL_CACHE_DIR, force_retrain=FORCE_RETRAIN
                )
                self.high_states[r] = TimingCoordinator(build_high_agents(r, self.horizon_steps[r], symbol=self.symbol), "high").fit_all(
                    views["high_df"], cache_dir=MODEL_CACHE_DIR, force_retrain=FORCE_RETRAIN
                )
            else:
                self.low_states[r] = None
                self.high_states[r] = None
        return self

    def refit_after_update(self):
        self.raw_df = ensure_raw_df(self.raw_df)
        self._update_timing_enabled()
        self._refresh_champion_configs_if_needed()
        for r in self.cadence_rules:
            views = build_training_views_for_rule(self.raw_df, r)
            prev_dir = self.direction_states[r].agents if self.direction_states.get(r) is not None else None
            prev_low = self.low_states[r].agents if self.low_states.get(r) is not None else None
            prev_high = self.high_states[r].agents if self.high_states.get(r) is not None else None

            self.direction_states[r].fit_all(views["direction_df"], prev_agents=prev_dir)
            if self.timing_enabled:
                if self.low_states.get(r) is None:
                    self.low_states[r] = TimingCoordinator(build_low_agents(r, self.horizon_steps[r], symbol=self.symbol), "low")
                if self.high_states.get(r) is None:
                    self.high_states[r] = TimingCoordinator(build_high_agents(r, self.horizon_steps[r], symbol=self.symbol), "high")
                self.low_states[r].fit_all(views["low_df"], prev_agents=prev_low)
                self.high_states[r].fit_all(views["high_df"], prev_agents=prev_high)
            else:
                self.low_states[r] = None
                self.high_states[r] = None
        return self

    def infer(self) -> Dict[str, Any]:
        direction_vote = 0.0
        direction_strength = 0.0
        per_rule = {}
        action_map = {"BUY": 1.0, "HOLD": 0.0, "SELL": -1.0}
        avg_uncertainty_ratio = None

        current_price = None
        current_timestamp = None
        first_below_current_timestamp = None
        time_to_below_current_seconds = None
        first_moment_price_per_hour = None
        first_moment_pct_per_hour = None
        second_moment_price_per_hour2 = None
        second_moment_pct_per_hour2 = None
        time_to_optimal_buy_seconds = None
        time_to_optimal_sell_seconds = None
        rise_window_seconds = None
        drop_window_seconds = None
        spike_start_timestamp = None
        spike_peak_timestamp = None
        spike_peak_price = None
        spike_sustain_seconds = None
        spike_fade_timestamp = None
        spike_fade_in_horizon = None
        peak_to_fade_seconds = None
        max_spike_pct = None
        drawdown_start_timestamp = None
        drawdown_recovery_timestamp = None
        drawdown_trough_timestamp = None
        drawdown_trough_price = None
        drawdown_linger_seconds = None
        drawdown_recovery_in_horizon = None
        trough_to_recovery_seconds = None
        max_drawdown_pct = None
        timesfm_drawdown_start_timestamp = None
        timesfm_drawdown_recovery_timestamp = None
        timesfm_drawdown_trough_timestamp = None
        timesfm_drawdown_trough_price = None
        timesfm_drawdown_linger_seconds = None
        timesfm_drawdown_recovery_in_horizon = None
        timesfm_trough_to_recovery_seconds = None
        timesfm_max_drawdown_pct = None
        timesfm_quantile_band_pct = None
        timesfm_spike_start_timestamp = None
        timesfm_spike_peak_timestamp = None
        timesfm_spike_peak_price = None
        timesfm_spike_sustain_seconds = None
        timesfm_spike_fade_timestamp = None
        timesfm_spike_fade_in_horizon = None
        timesfm_peak_to_fade_seconds = None
        timesfm_max_spike_pct = None
        timesfm_status = "unavailable"
        timesfm_error = None
        timesfm_used = False
        timesfm_model_id = None
        timesfm_moe_gate = None
        moe_runtime = build_moe_runtime()
        spike_sustain_consensus_seconds = None
        peak_to_fade_consensus_seconds = None
        spike_fade_consensus_in_horizon = None
        max_spike_consensus_pct = None
        spike_consensus_source = "prophet"
        prophet_spike_weight = None
        timesfm_spike_weight = None
        drawdown_linger_consensus_seconds = None
        trough_to_recovery_consensus_seconds = None
        drawdown_recovery_consensus_in_horizon = None
        max_drawdown_consensus_pct = None
        drawdown_consensus_source = "prophet"
        spike_feedback_status = None
        trend_curve_points: List[Dict[str, Any]] = []
        forecast_plot = None
        trend_component = None
        seasonality_components: Dict[str, Any] = {}
        seasonality_summary: Dict[str, Any] = {
            "sourceRule": None,
            "headline": None,
            "strongestComponent": None,
        }
        geodesic_state: Dict[str, Any] = {
            "available": False,
            "label": "geodesic unavailable",
            "actionBias": "hold",
        }

        for r in self.cadence_rules:
            dir_result = self.direction_states[r].aggregate()
            low_result = self.low_states[r].aggregate() if self.timing_enabled and self.low_states.get(r) is not None else None
            high_result = self.high_states[r].aggregate() if self.timing_enabled and self.high_states.get(r) is not None else None
            w = self.cadence_weights.get(r, 0.0)
            direction_vote += w * action_map[dir_result["final_action"]]
            direction_strength += w * dir_result["weighted_score"]
            per_rule[r] = {"direction": dir_result, "low_timing": low_result, "high_timing": high_result}

        uncertainty_values: List[float] = []
        for payload in per_rule.values():
            direction_payload = payload.get("direction") if isinstance(payload, dict) else None
            details = direction_payload.get("details") if isinstance(direction_payload, dict) else None
            if isinstance(details, pd.DataFrame) and "uncertainty_ratio" in details.columns:
                uncertainty_values.extend(
                    float(value)
                    for value in details["uncertainty_ratio"].dropna().tolist()
                )
        if uncertainty_values:
            avg_uncertainty_ratio = float(sum(uncertainty_values) / len(uncertainty_values))

        if direction_vote > 0.20 and direction_strength > 0:
            final_action = "BUY"
        elif direction_vote < -0.20 and direction_strength < 0:
            final_action = "SELL"
        else:
            final_action = "HOLD"

        optimal_buy_ts, optimal_buy_price = (None, None)
        optimal_sell_ts, optimal_sell_price = (None, None)
        if self.timing_enabled:
            optimal_buy_ts, optimal_buy_price = aggregate_timing_target(
                per_rule, self.cadence_rules, self.cadence_weights, "low_timing"
            )
            optimal_sell_ts, optimal_sell_price = aggregate_timing_target(
                per_rule, self.cadence_rules, self.cadence_weights, "high_timing"
            )

        target_ts = None
        target_price = None
        if final_action == "BUY":
            target_ts = optimal_buy_ts
            target_price = optimal_buy_price
        elif final_action == "SELL":
            target_ts = optimal_sell_ts
            target_price = optimal_sell_price

        if current_timestamp is None and len(self.raw_df) > 0:
            current_timestamp = pd.Timestamp(self.raw_df["ds"].iloc[-1])

        time_to_optimal_buy_seconds = safe_duration_seconds(current_timestamp, optimal_buy_ts)
        time_to_optimal_sell_seconds = safe_duration_seconds(current_timestamp, optimal_sell_ts)

        rise_candidate = safe_duration_seconds(optimal_buy_ts, optimal_sell_ts)
        if rise_candidate is not None and rise_candidate >= 0:
            rise_window_seconds = rise_candidate

        drop_candidate = safe_duration_seconds(optimal_sell_ts, optimal_buy_ts)
        if drop_candidate is not None and drop_candidate >= 0:
            drop_window_seconds = drop_candidate

        # New output requested by user:
        # time length until the direction forecast first goes below the current price
        try:
            curve_pairs = []
            for r in self.cadence_rules:
                curves = []
                for agent in self.direction_states[r].agents:
                    fcst = agent.full_curve()[["ds", "yhat"]].rename(columns={"yhat": f"yhat__{agent.config.name}"})
                    curves.append(fcst)
                if not curves:
                    continue

                merged = curves[0]
                for c in curves[1:]:
                    merged = merged.merge(c, on="ds", how="inner")

                total_rule_weight = sum(agent.config.weight for agent in self.direction_states[r].agents) or 1.0
                merged["rule_weighted_yhat"] = sum(
                    merged[f"yhat__{agent.config.name}"] * agent.config.weight
                    for agent in self.direction_states[r].agents
                ) / total_rule_weight

                curve_pairs.append((r, merged))

            if curve_pairs:
                common_curve = None
                for rule, curve in curve_pairs:
                    col_name = f"rule_yhat__{rule}"
                    curve = curve[["ds", "rule_weighted_yhat"]].rename(columns={"rule_weighted_yhat": col_name})
                    if common_curve is None:
                        common_curve = curve
                    else:
                        common_curve = common_curve.merge(curve, on="ds", how="inner")

                if common_curve is not None and not common_curve.empty:
                    total_cadence_weight = sum(self.cadence_weights.get(r, 0.0) for r, _ in curve_pairs) or 1.0
                    common_curve["direction_weighted_yhat"] = sum(
                        common_curve[f"rule_yhat__{r}"] * self.cadence_weights.get(r, 0.0)
                        for r, _ in curve_pairs
                    ) / total_cadence_weight

                    current_price = float(self.raw_df["close"].iloc[-1])
                    current_timestamp = pd.Timestamp(self.raw_df["ds"].iloc[-1])
                    curve_moments = compute_curve_moments(
                        common_curve,
                        "direction_weighted_yhat",
                        reference_price=current_price,
                    )
                    first_moment_price_per_hour = curve_moments["first_moment_price_per_hour"]
                    first_moment_pct_per_hour = curve_moments["first_moment_pct_per_hour"]
                    second_moment_price_per_hour2 = curve_moments["second_moment_price_per_hour2"]
                    second_moment_pct_per_hour2 = curve_moments["second_moment_pct_per_hour2"]
                    spike_profile = compute_upside_spike_sustain(
                        common_curve,
                        "direction_weighted_yhat",
                        reference_price=current_price,
                    )
                    drawdown_profile = compute_drawdown_linger(
                        common_curve,
                        "direction_weighted_yhat",
                        reference_price=current_price,
                    )
                    timesfm_moe_gate = evaluate_timesfm_gate(
                        self.raw_df,
                        cadence_profile=self.cadence_profile,
                        current_price=current_price,
                        curve_moments=curve_moments,
                        spike_profile=spike_profile,
                        drawdown_profile=drawdown_profile,
                        symbol=self.symbol,
                    )
                    if timesfm_moe_gate.get("run"):
                        with heavy_expert_slot("timesfm") as has_slot:
                            if has_slot:
                                timesfm_drawdown_profile = compute_timesfm_drawdown_profile(
                                    self.raw_df,
                                    reference_price=current_price,
                                    cadence_profile=self.cadence_profile,
                                )
                                timesfm_drawdown_profile["timesfm_moe_gate"] = timesfm_moe_gate
                            else:
                                timesfm_moe_gate = {
                                    **timesfm_moe_gate,
                                    "run": False,
                                    "reason": "heavy expert concurrency limit reached",
                                }
                                timesfm_drawdown_profile = build_timesfm_skipped_profile(
                                    timesfm_moe_gate["reason"],
                                    gate=timesfm_moe_gate,
                                )
                    else:
                        timesfm_drawdown_profile = build_timesfm_skipped_profile(
                            timesfm_moe_gate.get("reason") or "skipped by resource MoE gate",
                            gate=timesfm_moe_gate,
                        )
                    moe_runtime = build_moe_runtime(timesfm_moe_gate)
                    try:
                        from services.trader.reinforcement import sync_spike_feedback_loop

                        if self.symbol:
                            spike_feedback_status = sync_spike_feedback_loop(
                                symbol=self.symbol,
                                price_history_df=self.raw_df,
                                reference_price=current_price,
                                reference_timestamp=current_timestamp,
                                prophet_profile=spike_profile,
                                timesfm_profile=timesfm_drawdown_profile,
                            )
                    except Exception:
                        spike_feedback_status = None
                    combined_spike_profile = combine_spike_profiles(
                        spike_profile,
                        timesfm_drawdown_profile,
                        feedback_snapshot=(spike_feedback_status or {}).get("snapshot"),
                    )
                    combined_drawdown_profile = combine_drawdown_profiles(
                        drawdown_profile,
                        timesfm_drawdown_profile,
                    )
                    spike_start_timestamp = spike_profile["spike_start_timestamp"]
                    spike_peak_timestamp = spike_profile["spike_peak_timestamp"]
                    spike_peak_price = spike_profile["spike_peak_price"]
                    spike_sustain_seconds = spike_profile["spike_sustain_seconds"]
                    spike_fade_timestamp = spike_profile["spike_fade_timestamp"]
                    spike_fade_in_horizon = spike_profile["spike_fade_in_horizon"]
                    peak_to_fade_seconds = spike_profile["peak_to_fade_seconds"]
                    max_spike_pct = spike_profile["max_spike_pct"]
                    drawdown_start_timestamp = drawdown_profile["drawdown_start_timestamp"]
                    drawdown_recovery_timestamp = drawdown_profile["drawdown_recovery_timestamp"]
                    drawdown_trough_timestamp = drawdown_profile["drawdown_trough_timestamp"]
                    drawdown_trough_price = drawdown_profile["drawdown_trough_price"]
                    drawdown_linger_seconds = drawdown_profile["drawdown_linger_seconds"]
                    drawdown_recovery_in_horizon = drawdown_profile["drawdown_recovery_in_horizon"]
                    trough_to_recovery_seconds = drawdown_profile["trough_to_recovery_seconds"]
                    max_drawdown_pct = drawdown_profile["max_drawdown_pct"]
                    timesfm_drawdown_start_timestamp = timesfm_drawdown_profile["timesfm_drawdown_start_timestamp"]
                    timesfm_drawdown_recovery_timestamp = timesfm_drawdown_profile["timesfm_drawdown_recovery_timestamp"]
                    timesfm_drawdown_trough_timestamp = timesfm_drawdown_profile["timesfm_drawdown_trough_timestamp"]
                    timesfm_drawdown_trough_price = timesfm_drawdown_profile["timesfm_drawdown_trough_price"]
                    timesfm_drawdown_linger_seconds = timesfm_drawdown_profile["timesfm_drawdown_linger_seconds"]
                    timesfm_drawdown_recovery_in_horizon = timesfm_drawdown_profile["timesfm_drawdown_recovery_in_horizon"]
                    timesfm_trough_to_recovery_seconds = timesfm_drawdown_profile["timesfm_trough_to_recovery_seconds"]
                    timesfm_max_drawdown_pct = timesfm_drawdown_profile["timesfm_max_drawdown_pct"]
                    timesfm_quantile_band_pct = timesfm_drawdown_profile["timesfm_quantile_band_pct"]
                    timesfm_spike_start_timestamp = timesfm_drawdown_profile["timesfm_spike_start_timestamp"]
                    timesfm_spike_peak_timestamp = timesfm_drawdown_profile["timesfm_spike_peak_timestamp"]
                    timesfm_spike_peak_price = timesfm_drawdown_profile["timesfm_spike_peak_price"]
                    timesfm_spike_sustain_seconds = timesfm_drawdown_profile["timesfm_spike_sustain_seconds"]
                    timesfm_spike_fade_timestamp = timesfm_drawdown_profile["timesfm_spike_fade_timestamp"]
                    timesfm_spike_fade_in_horizon = timesfm_drawdown_profile["timesfm_spike_fade_in_horizon"]
                    timesfm_peak_to_fade_seconds = timesfm_drawdown_profile["timesfm_peak_to_fade_seconds"]
                    timesfm_max_spike_pct = timesfm_drawdown_profile["timesfm_max_spike_pct"]
                    timesfm_status = timesfm_drawdown_profile["timesfm_status"]
                    timesfm_error = timesfm_drawdown_profile["timesfm_error"]
                    timesfm_used = bool(timesfm_drawdown_profile["timesfm_used"])
                    timesfm_model_id = timesfm_drawdown_profile["timesfm_model_id"]
                    timesfm_moe_gate = timesfm_drawdown_profile.get("timesfm_moe_gate") or timesfm_moe_gate
                    moe_runtime = build_moe_runtime(timesfm_moe_gate)
                    spike_sustain_consensus_seconds = combined_spike_profile["spike_sustain_consensus_seconds"]
                    peak_to_fade_consensus_seconds = combined_spike_profile["peak_to_fade_consensus_seconds"]
                    spike_fade_consensus_in_horizon = combined_spike_profile["spike_fade_consensus_in_horizon"]
                    max_spike_consensus_pct = combined_spike_profile["max_spike_consensus_pct"]
                    spike_consensus_source = combined_spike_profile["spike_consensus_source"]
                    prophet_spike_weight = combined_spike_profile["prophet_spike_weight"]
                    timesfm_spike_weight = combined_spike_profile["timesfm_spike_weight"]
                    drawdown_linger_consensus_seconds = combined_drawdown_profile["drawdown_linger_consensus_seconds"]
                    trough_to_recovery_consensus_seconds = combined_drawdown_profile["trough_to_recovery_consensus_seconds"]
                    drawdown_recovery_consensus_in_horizon = combined_drawdown_profile["drawdown_recovery_consensus_in_horizon"]
                    max_drawdown_consensus_pct = combined_drawdown_profile["max_drawdown_consensus_pct"]
                    drawdown_consensus_source = combined_drawdown_profile["drawdown_consensus_source"]
                    trend_curve_points = serialize_curve_points(
                        common_curve,
                        "direction_weighted_yhat",
                    )
                    component_agent = None
                    for candidate_rule in sorted(
                        self.cadence_rules,
                        key=lambda rule: self.cadence_weights.get(rule, 0.0),
                        reverse=True,
                    ):
                        coordinator = self.direction_states.get(candidate_rule)
                        if coordinator is None:
                            continue
                        for candidate_agent in coordinator.agents:
                            if (
                                isinstance(getattr(candidate_agent, "engine", None), ProphetEngineAgent)
                                and getattr(candidate_agent.engine, "model", None) is not None
                            ):
                                component_agent = candidate_agent
                                break
                        if component_agent is not None:
                            break
                    component_bundle = build_prophet_component_bundle(component_agent)
                    forecast_plot = component_bundle.get("forecast_plot")
                    trend_component = component_bundle.get("trend_component")
                    seasonality_components = component_bundle.get("seasonality_components") or {}
                    seasonality_summary = component_bundle.get("seasonality_summary") or seasonality_summary
                    geodesic_state = estimate_symbol_geodesic_state(
                        symbol=self.symbol,
                        first_moment_pct_per_hour=first_moment_pct_per_hour,
                        second_moment_pct_per_hour2=second_moment_pct_per_hour2,
                        uncertainty_ratio=avg_uncertainty_ratio,
                        map_date=str(current_timestamp.date()) if current_timestamp is not None else None,
                    )

                    below_df = common_curve[common_curve["direction_weighted_yhat"] < current_price].sort_values("ds")
                    if not below_df.empty:
                        first_below_current_timestamp = pd.Timestamp(below_df.iloc[0]["ds"])
                        time_to_below_current_seconds = float(
                            (first_below_current_timestamp - current_timestamp).total_seconds()
                        )
        except Exception:
            pass

        return {
            "final_action": final_action,
            "direction_vote": direction_vote,
            "direction_strength": direction_strength,
            "avg_uncertainty_ratio": avg_uncertainty_ratio,
            "target_timestamp": target_ts,
            "target_price": target_price,
            "timing_enabled": self.timing_enabled,
            "current_price": current_price,
            "current_timestamp": current_timestamp,
            "first_below_current_timestamp": first_below_current_timestamp,
            "time_to_below_current_seconds": time_to_below_current_seconds,
            "first_moment_price_per_hour": first_moment_price_per_hour,
            "first_moment_pct_per_hour": first_moment_pct_per_hour,
            "second_moment_price_per_hour2": second_moment_price_per_hour2,
            "second_moment_pct_per_hour2": second_moment_pct_per_hour2,
            "time_to_optimal_buy_seconds": time_to_optimal_buy_seconds,
            "time_to_optimal_sell_seconds": time_to_optimal_sell_seconds,
            "rise_window_seconds": rise_window_seconds,
            "drop_window_seconds": drop_window_seconds,
            "spike_start_timestamp": spike_start_timestamp,
            "spike_peak_timestamp": spike_peak_timestamp,
            "spike_peak_price": spike_peak_price,
            "spike_sustain_seconds": spike_sustain_seconds,
            "spike_fade_timestamp": spike_fade_timestamp,
            "spike_fade_in_horizon": spike_fade_in_horizon,
            "peak_to_fade_seconds": peak_to_fade_seconds,
            "max_spike_pct": max_spike_pct,
            "drawdown_start_timestamp": drawdown_start_timestamp,
            "drawdown_recovery_timestamp": drawdown_recovery_timestamp,
            "drawdown_trough_timestamp": drawdown_trough_timestamp,
            "drawdown_trough_price": drawdown_trough_price,
            "drawdown_linger_seconds": drawdown_linger_seconds,
            "drawdown_recovery_in_horizon": drawdown_recovery_in_horizon,
            "trough_to_recovery_seconds": trough_to_recovery_seconds,
            "max_drawdown_pct": max_drawdown_pct,
            "timesfm_drawdown_start_timestamp": timesfm_drawdown_start_timestamp,
            "timesfm_drawdown_recovery_timestamp": timesfm_drawdown_recovery_timestamp,
            "timesfm_drawdown_trough_timestamp": timesfm_drawdown_trough_timestamp,
            "timesfm_drawdown_trough_price": timesfm_drawdown_trough_price,
            "timesfm_drawdown_linger_seconds": timesfm_drawdown_linger_seconds,
            "timesfm_drawdown_recovery_in_horizon": timesfm_drawdown_recovery_in_horizon,
            "timesfm_trough_to_recovery_seconds": timesfm_trough_to_recovery_seconds,
            "timesfm_max_drawdown_pct": timesfm_max_drawdown_pct,
            "timesfm_quantile_band_pct": timesfm_quantile_band_pct,
            "timesfm_spike_start_timestamp": timesfm_spike_start_timestamp,
            "timesfm_spike_peak_timestamp": timesfm_spike_peak_timestamp,
            "timesfm_spike_peak_price": timesfm_spike_peak_price,
            "timesfm_spike_sustain_seconds": timesfm_spike_sustain_seconds,
            "timesfm_spike_fade_timestamp": timesfm_spike_fade_timestamp,
            "timesfm_spike_fade_in_horizon": timesfm_spike_fade_in_horizon,
            "timesfm_peak_to_fade_seconds": timesfm_peak_to_fade_seconds,
            "timesfm_max_spike_pct": timesfm_max_spike_pct,
            "timesfm_status": timesfm_status,
            "timesfm_error": timesfm_error,
            "timesfm_used": timesfm_used,
            "timesfm_model_id": timesfm_model_id,
            "timesfm_moe_gate": timesfm_moe_gate,
            "moe_runtime": moe_runtime,
            "spike_sustain_consensus_seconds": spike_sustain_consensus_seconds,
            "peak_to_fade_consensus_seconds": peak_to_fade_consensus_seconds,
            "spike_fade_consensus_in_horizon": spike_fade_consensus_in_horizon,
            "max_spike_consensus_pct": max_spike_consensus_pct,
            "spike_consensus_source": spike_consensus_source,
            "prophet_spike_weight": prophet_spike_weight,
            "timesfm_spike_weight": timesfm_spike_weight,
            "drawdown_linger_consensus_seconds": drawdown_linger_consensus_seconds,
            "trough_to_recovery_consensus_seconds": trough_to_recovery_consensus_seconds,
            "drawdown_recovery_consensus_in_horizon": drawdown_recovery_consensus_in_horizon,
            "max_drawdown_consensus_pct": max_drawdown_consensus_pct,
            "drawdown_consensus_source": drawdown_consensus_source,
            "spike_feedback_status": spike_feedback_status,
            "trend_curve": trend_curve_points,
            "forecast_plot": forecast_plot,
            "trend_component": trend_component,
            "seasonality_components": seasonality_components,
            "seasonality_summary": seasonality_summary,
            "geodesic_state": geodesic_state,
            "geodesic_available": geodesic_state.get("available"),
            "geodesic_label": geodesic_state.get("label"),
            "geodesic_action_bias": geodesic_state.get("actionBias"),
            "geodesic_history_count": geodesic_state.get("historyCount"),
            "geodesic_path_length": geodesic_state.get("pathLength"),
            "geodesic_curvature": geodesic_state.get("curvature"),
            "geodesic_alignment_score": geodesic_state.get("alignmentScore"),
            "geodesic_deviation_score": geodesic_state.get("deviationScore"),
            "geodesic_continuation_score": geodesic_state.get("continuationScore"),
            "geodesic_confidence": geodesic_state.get("confidence"),
            "geodesic_projected_first_coordinate_x": geodesic_state.get("projectedFirstCoordinateX"),
            "geodesic_projected_first_coordinate_y": geodesic_state.get("projectedFirstCoordinateY"),
            "geodesic_projected_second_coordinate_x": geodesic_state.get("projectedSecondCoordinateX"),
            "geodesic_projected_second_coordinate_y": geodesic_state.get("projectedSecondCoordinateY"),
            "geodesic_projected_first_coordinate_drift": geodesic_state.get("projectedFirstCoordinateDrift"),
            "geodesic_projected_second_coordinate_drift": geodesic_state.get("projectedSecondCoordinateDrift"),
            "geodesic_stability_score": geodesic_state.get("stabilityScore"),
            "geodesic_persistence_score": geodesic_state.get("persistenceScore"),
            "geodesic_regime_shift_risk": geodesic_state.get("regimeShiftRisk"),
            "optimal_buy_timestamp": optimal_buy_ts,
            "optimal_buy_price": optimal_buy_price,
            "optimal_sell_timestamp": optimal_sell_ts,
            "optimal_sell_price": optimal_sell_price,
            "cadence_profile": self.cadence_profile,
            "runtime_symbol": self.symbol,
            "champion_refresh": self.champion_refresh,
            "cadence_rules": [
                {
                    "rule": rule,
                    "label": self.cadence_labels.get(rule, rule),
                    "weight": self.cadence_weights.get(rule, 0.0),
                }
                for rule in self.cadence_rules
            ],
            "uncertainty_settings": {
                rule: (per_rule[rule].get("direction") or {}).get("uncertainty_settings")
                for rule in self.cadence_rules
            },
            "per_rule": per_rule,
        }


# =========================================================
# EXECUTION BRIDGE
# =========================================================

def signal_to_swap_params(final_action: str) -> Optional[Dict[str, Any]]:
    if final_action == "BUY":
        return {"input_mint": OUTPUT_MINT_FOR_PRICE, "output_mint": INPUT_MINT_FOR_PRICE, "amount": BUY_AMOUNT_USDC}
    if final_action == "SELL":
        return {"input_mint": INPUT_MINT_FOR_PRICE, "output_mint": OUTPUT_MINT_FOR_PRICE, "amount": SELL_AMOUNT_SOL}
    return None


def maybe_execute_with_jupiter(decision: Dict[str, Any], execute_trades: bool = False):
    if decision["final_action"] == "HOLD":
        print("No trade. HOLD.")
        return None

    swap_params = signal_to_swap_params(decision["final_action"])
    if swap_params is None:
        return None

    print("\n=== DECISION ===")
    print(json.dumps({
        "final_action": decision["final_action"],
        "direction_vote": decision["direction_vote"],
        "direction_strength": decision["direction_strength"],
        "target_timestamp": str(decision["target_timestamp"]),
        "target_price": decision["target_price"],
        "timing_enabled": decision.get("timing_enabled"),
        "current_price": decision.get("current_price"),
        "current_timestamp": str(decision.get("current_timestamp")),
        "first_below_current_timestamp": str(decision.get("first_below_current_timestamp")),
        "time_to_below_current_seconds": decision.get("time_to_below_current_seconds"),
    }, indent=2))

    wallet = get_wallet()
    taker = str(wallet.pubkey())
    client = JupiterSwapClient(JUPITER_API_KEY)

    if WAIT_FOR_TARGET and decision["target_timestamp"] is not None:
        wait_until_target(decision["target_timestamp"], lead_seconds=TARGET_LEAD_SECONDS)

    candidates = client.collect_candidate_orders(
        input_mint=swap_params["input_mint"],
        output_mint=swap_params["output_mint"],
        amount=swap_params["amount"],
        taker=taker,
        rounds=QUOTE_BURST_COUNT,
    )
    if not candidates:
        print("No execution candidates collected.")
        return None

    best = choose_best_candidate(candidates)
    print("\n=== BEST ORDER ===")
    print(json.dumps({
        "requestId": best["requestId"],
        "outAmount": best["outAmount"],
        "priceImpact": best["priceImpact"],
        "totalTime": best["totalTime"],
    }, indent=2))

    if not execute_trades:
        print("Dry run only. Set EXECUTE_TRADES=true to actually execute.")
        return {"decision": decision, "best_order": best}

    signed_tx_b64 = client.sign_order_transaction(best["_raw_order"], wallet)
    execute_result = client.execute_order(signed_tx_b64, best["requestId"])
    print("\n=== EXECUTE RESULT ===")
    print(json.dumps(execute_result, indent=2))
    return {"decision": decision, "best_order": best, "execute_result": execute_result}


# =========================================================
# REALTIME LOOP
# =========================================================

def run_realtime_loop_with_jupiter_quotes(
    historical_raw_df: pd.DataFrame,
    execute_trades: bool = False,
    poll_every_seconds: float = POLL_EVERY_SECONDS,
    quote_burst_count: int = QUOTE_BURST_COUNT,
    max_iterations: int = MAX_ITERATIONS,
):
    historical_raw_df = ensure_raw_df(historical_raw_df)
    runtime = MultiResolutionRuntime(raw_df=historical_raw_df).bootstrap()

    if not runtime.timing_enabled:
        median_sec = infer_median_bar_seconds(historical_raw_df)
        print(
            "Historical CSV is not intraday enough for timing agents. "
            f"Median bar seconds={median_sec}. Timing will stay disabled until realtime 1min bars accumulate."
        )

    poller = JupiterQuotePoller(
        api_key=JUPITER_API_KEY,
        input_mint=INPUT_MINT_FOR_PRICE,
        output_mint=OUTPUT_MINT_FOR_PRICE,
        input_decimals=INPUT_DECIMALS,
        output_decimals=OUTPUT_DECIMALS,
        amount_in_smallest_unit=QUOTE_AMOUNT_IN_SMALLEST_UNIT,
        require_ultra=REQUIRE_ULTRA_FOR_TRAINING,
    )

    buffer_df = historical_raw_df.copy()
    initial_decision = runtime.infer()
    print("\n=== INITIAL DECISION ===")
    print(json.dumps({
        "final_action": initial_decision["final_action"],
        "direction_vote": initial_decision["direction_vote"],
        "direction_strength": initial_decision["direction_strength"],
        "target_timestamp": str(initial_decision["target_timestamp"]),
        "target_price": initial_decision["target_price"],
        "timing_enabled": initial_decision["timing_enabled"],
        "current_price": initial_decision["current_price"],
        "current_timestamp": str(initial_decision["current_timestamp"]),
        "first_below_current_timestamp": str(initial_decision["first_below_current_timestamp"]),
        "time_to_below_current_seconds": initial_decision["time_to_below_current_seconds"],
    }, indent=2))

    initial_realtime_bars = 0
    allow_initial_execution = (
        ALLOW_BOOTSTRAP_EXECUTION
        and initial_decision["timing_enabled"]
        and initial_realtime_bars >= MIN_REALTIME_BARS_FOR_EXECUTION
    )
    if allow_initial_execution:
        maybe_execute_with_jupiter(initial_decision, execute_trades=execute_trades)
    else:
        print(
            "Skipping bootstrap execution. "
            f"ALLOW_BOOTSTRAP_EXECUTION={ALLOW_BOOTSTRAP_EXECUTION}, "
            f"timing_enabled={initial_decision['timing_enabled']}, "
            f"realtime_bars={initial_realtime_bars}/{MIN_REALTIME_BARS_FOR_EXECUTION}."
        )

    print(f"\n[START] {TARGET_COIN_SYMBOL} Trading Loop Started. (Engine: {MODEL_ENGINE})")

    for i in range(max_iterations):
        new_rows = [poller.get_snapshot() for _ in range(quote_burst_count)]
        new_rows = [r for r in new_rows if r is not None]

        if new_rows:
            buffer_df = pd.concat([buffer_df, pd.DataFrame(new_rows)], ignore_index=True)
            if len(buffer_df) > 5000:
                buffer_df = buffer_df.iloc[-5000:].reset_index(drop=True)

            runtime.raw_df = buffer_df
            realtime_bar_count = len(resample_ohlc(buffer_df, "1min")) if len(buffer_df) >= 100 else 0

            if realtime_bar_count >= MIN_REALTIME_BARS_FOR_EXECUTION:
                runtime.refit_after_update()
                decision = runtime.infer()
                print(
                    f"[Iter {i}] Action: {decision['final_action']} | "
                    f"Target TS: {decision['target_timestamp']} | "
                    f"Below Current In(sec): {decision['time_to_below_current_seconds']} | "
                    f"Timing Enabled: {decision['timing_enabled']}"
                )
                maybe_execute_with_jupiter(decision, execute_trades=execute_trades)
            else:
                print(
                    "Not enough realtime intraday bars yet for retraining/execution. "
                    f"Current 1min bars={realtime_bar_count}, required={MIN_REALTIME_BARS_FOR_EXECUTION}."
                )
        else:
            print(f"[Iter {i}] No new Jupiter quote rows.")

        time.sleep(poll_every_seconds)

    return runtime


# =========================================================
# MAIN
# =========================================================

def main():
    require_api_key()
    if os.path.exists(PRICE_HISTORY_CSV):
        historical_raw_df = pd.read_csv(PRICE_HISTORY_CSV)
    else:
        historical_raw_df = fetch_fallback_data(TARGET_COIN_SYMBOL)
        historical_raw_df.to_csv(PRICE_HISTORY_CSV, index=False)

    historical_raw_df = ensure_raw_df(historical_raw_df)

    run_realtime_loop_with_jupiter_quotes(
        historical_raw_df=historical_raw_df,
        execute_trades=EXECUTE_TRADES,
        poll_every_seconds=POLL_EVERY_SECONDS,
        quote_burst_count=QUOTE_BURST_COUNT,
        max_iterations=MAX_ITERATIONS,
    )


if __name__ == "__main__":
    main()
