#!/usr/bin/env python3

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import logging
logging.getLogger("prophet").setLevel(logging.CRITICAL)
logging.getLogger("prophet.plot").setLevel(logging.CRITICAL)
logging.getLogger("cmdstanpy").setLevel(logging.CRITICAL)

import argparse
import contextlib
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.trader import main as trader_main
from services.trader.champion_prophet import load_runtime_champion_config
from services.trader.map_store import (
    DEFAULT_TRAJECTORY_LOOKBACK_DAYS,
    compute_symbol_trajectory_metrics,
    dated_map_snapshot_path,
    load_recent_symbol_information_map_rows,
    persist_information_map_history,
    today_market_date,
    today_market_timestamp_iso,
)
from services.trader.fmkorea_stock import (
    build_fmkorea_stock_snapshot,
    fmkorea_symbol_heat,
)
from services.trader.human_bias import (
    load_market_interest_overview,
    load_symbol_interest_map,
)
from services.trader.sp500_ingest import (
    CLOSE_MATRIX_PATH,
    CONSTITUENTS_PATH,
    download_sp500_close_matrix,
    expected_equity_session_date,
    fetch_sp500_constituents,
    save_sp500_dataset,
)
from services.trader.feature_selection_benchmark import (
    load_or_run_feature_selection_benchmark,
    public_feature_selection_benchmark_state,
)
from services.trader.tail_diagnostics import build_tail_diagnostics
from services.trader.web_signal_nn import score_information_map_items_with_web_nn

DEFAULT_CACHE_MAX_AGE_HOURS = float(os.getenv("SP500_MAP_CACHE_MAX_AGE_HOURS", "6"))
DEFAULT_HISTORY_ROWS = int(os.getenv("SP500_MAP_HISTORY_ROWS", "520"))
DEFAULT_LIMIT = int(os.getenv("SP500_MAP_DEFAULT_LIMIT", "505"))
DEFAULT_TRAJECTORY_LOOKBACK = int(
    os.getenv("SP500_MAP_TRAJECTORY_LOOKBACK_DAYS", str(DEFAULT_TRAJECTORY_LOOKBACK_DAYS))
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an S&P500 information map from Prophet moments."
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore a fresh cache and rebuild the map.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help="Maximum number of symbols to evaluate from the matrix.",
    )
    parser.add_argument(
        "--cache-max-age-hours",
        type=float,
        default=DEFAULT_CACHE_MAX_AGE_HOURS,
        help="How long a saved information map stays fresh.",
    )
    return parser.parse_args()


def utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def safe_float(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(numeric):
        return None
    return numeric


def parse_cached_timestamp(payload: Dict[str, Any]) -> Optional[datetime]:
    generated_at = payload.get("generatedAt")
    if not generated_at:
        return None
    normalized = str(generated_at).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def relabel_coordinate_axes(space: Optional[Dict[str, Any]], ordinal: str) -> Dict[str, Any]:
    normalized_space = dict(space or {})
    if ordinal == "first":
        label = str(normalized_space.get("label") or "")
        x_axis = str(normalized_space.get("xAxis") or "")
        y_axis = str(normalized_space.get("yAxis") or "")
        if label in {"m-coordinate map", "Coordinate map"} or not label:
            normalized_space["label"] = "1st coordinate map"
        if x_axis in {"m-coordinate x", ""}:
            normalized_space["xAxis"] = "1st coordinate x"
        if y_axis in {"m-coordinate y", ""}:
            normalized_space["yAxis"] = "1st coordinate y"
        return normalized_space

    label = str(normalized_space.get("label") or "")
    x_axis = str(normalized_space.get("xAxis") or "")
    y_axis = str(normalized_space.get("yAxis") or "")
    if label in {"e-coordinate map", "Coordinate map"} or not label:
        normalized_space["label"] = "2nd coordinate map"
    if x_axis in {"e-coordinate x", ""}:
        normalized_space["xAxis"] = "2nd coordinate x"
    if y_axis in {"e-coordinate y", ""}:
        normalized_space["yAxis"] = "2nd coordinate y"
    return normalized_space


def normalize_information_map_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(payload)
    raw_spaces = normalized.get("mapSpaces") or normalized.get("coordinateModes") or {}
    normalized["mapSpaces"] = {
        "firstCoordinate": relabel_coordinate_axes(
            raw_spaces.get("firstCoordinate")
            or raw_spaces.get("momentum")
            or raw_spaces.get("m")
            or {
                "label": "1st coordinate map",
                "xAxis": "1st coordinate x",
                "yAxis": "1st coordinate y",
            },
            "first",
        ),
        "secondCoordinate": relabel_coordinate_axes(
            raw_spaces.get("secondCoordinate")
            or raw_spaces.get("conviction")
            or raw_spaces.get("e")
            or {
                "label": "2nd coordinate map",
                "xAxis": "2nd coordinate x",
                "yAxis": "2nd coordinate y",
            },
            "second",
        ),
    }

    normalized_points = []
    for point in normalized.get("points") or []:
        next_point = dict(point)
        next_point["firstCoordinateSpace"] = (
            next_point.get("firstCoordinateSpace")
            or next_point.get("momentumSpace")
            or next_point.get("mCoordinate")
        )
        next_point["secondCoordinateSpace"] = (
            next_point.get("secondCoordinateSpace")
            or next_point.get("convictionSpace")
            or next_point.get("eCoordinate")
        )
        normalized_points.append(next_point)
    normalized["points"] = normalized_points

    normalized_top_picks = []
    for point in normalized.get("topPicks") or []:
        next_point = dict(point)
        next_point["firstCoordinateSpace"] = (
            next_point.get("firstCoordinateSpace")
            or next_point.get("momentumSpace")
            or next_point.get("mCoordinate")
        )
        next_point["secondCoordinateSpace"] = (
            next_point.get("secondCoordinateSpace")
            or next_point.get("convictionSpace")
            or next_point.get("eCoordinate")
        )
        normalized_top_picks.append(next_point)
    normalized["topPicks"] = normalized_top_picks
    return normalized


def load_cached_information_map(max_age_hours: float) -> Optional[Dict[str, Any]]:
    map_date = today_market_date()
    snapshot_path = dated_map_snapshot_path(map_date)
    if not snapshot_path.exists():
        return None

    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    payload = normalize_information_map_payload(payload)

    generated_at = parse_cached_timestamp(payload)
    if generated_at is None:
        return None

    if str(payload.get("mapDate") or "") != map_date:
        return None

    age_seconds = (
        datetime.fromisoformat(utc_now_iso()) - generated_at.astimezone()
    ).total_seconds()
    if age_seconds > max(0.0, max_age_hours) * 3600:
        return None

    payload["cache"] = {
        "used": True,
        "ageSeconds": age_seconds,
        "path": str(snapshot_path),
    }
    return payload


def persist_information_map(payload: Dict[str, Any]) -> None:
    snapshot_paths = persist_information_map_history(payload)
    payload["history"] = {
        "datedPath": snapshot_paths["datedPath"],
        "latestPath": snapshot_paths["latestPath"],
    }


def ensure_sp500_matrix() -> tuple[pd.DataFrame, pd.DataFrame]:
    close_matrix = None
    constituents = None

    if CLOSE_MATRIX_PATH.exists():
        try:
            close_matrix = pd.read_csv(CLOSE_MATRIX_PATH)
            constituents = (
                pd.read_csv(CONSTITUENTS_PATH)
                if CONSTITUENTS_PATH.exists()
                else None
            )
        except Exception as e:
            print(f"Warning: Failed to load cached S&P 500 files: {e}", file=sys.stderr)

    # If cached data is already fresh, return it directly
    if close_matrix is not None and constituents is not None:
        latest_cached_date = None
        if "ds" in close_matrix.columns and not close_matrix.empty:
            latest_cached_date = pd.Timestamp(close_matrix["ds"].iloc[-1]).date().isoformat()
        if latest_cached_date and latest_cached_date >= expected_equity_session_date():
            return constituents, close_matrix

    # Attempt to download the latest data
    try:
        updated_constituents = fetch_sp500_constituents()
        updated_close_matrix = download_sp500_close_matrix(
            updated_constituents,
            start_date="2018-01-01",
            end_date=today_market_date(),
        )
        save_sp500_dataset(
            updated_constituents,
            updated_close_matrix,
            output_dir=CLOSE_MATRIX_PATH.parent,
            start_date="2018-01-01",
            end_date=today_market_date(),
        )
        return updated_constituents, updated_close_matrix
    except Exception as e:
        print(f"Warning: Failed to download latest S&P 500 data ({e}).", file=sys.stderr)
        # Fallback to cache if available
        if close_matrix is not None:
            if constituents is None:
                try:
                    constituents = fetch_sp500_constituents()
                except Exception:
                    symbols = [col for col in close_matrix.columns if col != "ds"]
                    constituents = pd.DataFrame({
                        "Symbol": symbols,
                        "YahooSymbol": symbols,
                        "Security": symbols,
                        "GICS Sector": ["Financials" for _ in symbols]
                    })
            print("Falling back to cached S&P 500 close matrix and constituents.", file=sys.stderr)
            return constituents, close_matrix
        else:
            raise e



def build_symbol_metadata(constituents: pd.DataFrame) -> Dict[str, Dict[str, str]]:
    metadata: Dict[str, Dict[str, str]] = {}
    for _, row in constituents.iterrows():
        yahoo_symbol = str(row.get("YahooSymbol") or "").strip().upper()
        if not yahoo_symbol:
            continue
        metadata[yahoo_symbol] = {
            "name": str(row.get("Security") or yahoo_symbol),
            "sector": str(row.get("GICS Sector") or ""),
        }
    return metadata


def build_raw_df_from_matrix(close_matrix: pd.DataFrame, symbol: str) -> Optional[pd.DataFrame]:
    normalized_symbol = (symbol or "").strip().upper().replace(".", "-")
    if "ds" not in close_matrix.columns or normalized_symbol not in close_matrix.columns:
        return None

    raw_df = close_matrix[["ds", normalized_symbol]].rename(
        columns={normalized_symbol: "close"}
    )
    raw_df["close"] = pd.to_numeric(raw_df["close"], errors="coerce")
    raw_df = raw_df.dropna(subset=["ds", "close"]).copy()
    if raw_df.empty:
        return None

    raw_df["y"] = raw_df["close"]
    raw_df = raw_df.tail(DEFAULT_HISTORY_ROWS).reset_index(drop=True)
    return raw_df


def build_screening_config(symbol: str, rule: str, horizon_steps: int):
    champion_config = load_runtime_champion_config(
        symbol=symbol,
        task="direction",
        rule=rule,
        default_horizon_steps=horizon_steps,
    )
    if champion_config is not None:
        return trader_main.apply_uncertainty_profile(champion_config, batch_mode=True)

    return trader_main.apply_uncertainty_profile(trader_main.BaseProphetConfig(
        name=f"screen_direction_{symbol.lower()}_{rule.lower()}",
        rule=rule,
        horizon_steps=horizon_steps,
        weight=1.0,
        changepoint_prior_scale=0.03,
    ), batch_mode=True)


def classify_quadrant(first_moment: Optional[float], second_moment: Optional[float]) -> str:
    if first_moment is None or second_moment is None:
        return "unknown"
    if first_moment >= 0 and second_moment >= 0:
        return "breakout acceleration"
    if first_moment >= 0 and second_moment < 0:
        return "uptrend cooling"
    if first_moment < 0 and second_moment >= 0:
        return "recovery setup"
    return "selloff acceleration"


def build_momentum_space(
    first_moment_pct_per_day: Optional[float],
    second_moment_bp_per_day2: Optional[float],
) -> Dict[str, Any]:
    transformed_first = None
    transformed_second = None
    if first_moment_pct_per_day is not None:
        transformed_first = np.sign(first_moment_pct_per_day) * np.log1p(
            abs(first_moment_pct_per_day) * 10.0
        )
    if second_moment_bp_per_day2 is not None:
        transformed_second = np.sign(second_moment_bp_per_day2) * np.log1p(
            abs(second_moment_bp_per_day2) / 5.0
        )
    return {
        "x": safe_float(transformed_first),
        "y": safe_float(transformed_second),
        "xLabel": "1st coordinate x",
        "yLabel": "1st coordinate y",
    }


def build_conviction_space(
    first_moment_pct_per_day: Optional[float],
    second_moment_bp_per_day2: Optional[float],
    uncertainty_ratio: Optional[float],
) -> Dict[str, Any]:
    uncertainty_scale = max(0.01, safe_float(uncertainty_ratio) or 0.01)
    return {
        "x": safe_float(first_moment_pct_per_day / uncertainty_scale)
        if first_moment_pct_per_day is not None
        else None,
        "y": safe_float(second_moment_bp_per_day2 / uncertainty_scale)
        if second_moment_bp_per_day2 is not None
        else None,
        "uncertaintyScale": uncertainty_scale,
        "xLabel": "2nd coordinate x",
        "yLabel": "2nd coordinate y",
    }


def screen_symbol(
    symbol: str,
    raw_df: pd.DataFrame,
    metadata: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    normalized_df = trader_main.ensure_raw_df(raw_df)
    cadence_profile = trader_main.resolve_cadence_profile(normalized_df)
    rule = "1D" if "1D" in cadence_profile["rules"] else cadence_profile["rules"][-1]
    horizon_steps = cadence_profile["horizon_steps"][rule]
    training_views = trader_main.build_training_views_for_rule(normalized_df, rule)
    training_df = training_views["direction_df"]
    if len(training_df) < 180:
        return None

    config = build_screening_config(symbol, rule, horizon_steps)
    agent = trader_main.DirectionAgentWrapper(config)
    with contextlib.redirect_stdout(io.StringIO()):
        agent.fit(training_df)
    decision = agent.decision()
    forecast = agent.full_curve()
    if forecast.empty:
        return None

    current_price = safe_float(normalized_df["close"].iloc[-1])
    if current_price is None or current_price <= 0:
        return None

    curve_moments = trader_main.compute_curve_moments(
        forecast,
        "yhat",
        reference_price=current_price,
    )
    drawdown_metrics = trader_main.compute_drawdown_linger(
        forecast,
        "yhat",
        reference_price=current_price,
    )
    spike_metrics = trader_main.compute_upside_spike_sustain(
        forecast,
        "yhat",
        reference_price=current_price,
    )
    buy_row = forecast.loc[forecast["yhat"].idxmin()]
    sell_row = forecast.loc[forecast["yhat"].idxmax()]
    forecast_end_price = safe_float(forecast.iloc[-1]["yhat"])
    max_forecast_price = safe_float(sell_row["yhat"])
    min_forecast_price = safe_float(buy_row["yhat"])

    first_moment_pct_per_hour = safe_float(curve_moments.get("first_moment_pct_per_hour"))
    second_moment_pct_per_hour2 = safe_float(
        curve_moments.get("second_moment_pct_per_hour2")
    )
    first_moment_pct_per_day = (
        first_moment_pct_per_hour * 24 if first_moment_pct_per_hour is not None else None
    )
    second_moment_bp_per_day2 = (
        second_moment_pct_per_hour2 * 10_000 * 24 * 24
        if second_moment_pct_per_hour2 is not None
        else None
    )
    optimal_buy_timestamp = pd.Timestamp(buy_row["ds"]).isoformat()
    optimal_sell_timestamp = pd.Timestamp(sell_row["ds"]).isoformat()
    current_timestamp = pd.Timestamp(normalized_df["ds"].iloc[-1])
    time_to_optimal_buy_seconds = trader_main.safe_duration_seconds(
        current_timestamp, pd.Timestamp(buy_row["ds"])
    )
    time_to_optimal_sell_seconds = trader_main.safe_duration_seconds(
        current_timestamp, pd.Timestamp(sell_row["ds"])
    )

    expected_return_pct = (
        (forecast_end_price / current_price) - 1.0
        if forecast_end_price is not None and current_price
        else None
    )
    max_upside_pct = (
        (max_forecast_price / current_price) - 1.0
        if max_forecast_price is not None and current_price
        else None
    )
    drawdown_to_buy_pct = (
        (min_forecast_price / current_price) - 1.0
        if min_forecast_price is not None and current_price
        else None
    )
    uncertainty_ratio = safe_float(decision.get("uncertainty_ratio"))
    tail_diagnostics = build_tail_diagnostics(normalized_df)
    momentum_space = build_momentum_space(
        first_moment_pct_per_day, second_moment_bp_per_day2
    )
    conviction_space = build_conviction_space(
        first_moment_pct_per_day,
        second_moment_bp_per_day2,
        uncertainty_ratio,
    )

    is_multi = getattr(agent.engine.model, "seasonality_mode", "multiplicative") == "multiplicative"
    prophet_trend = safe_float(forecast["trend"].iloc[0]) if "trend" in forecast.columns and not forecast.empty else 0.0
    prophet_trend_slope = safe_float((forecast["trend"].iloc[-1] - forecast["trend"].iloc[0]) / len(forecast)) if "trend" in forecast.columns and len(forecast) > 0 else 0.0
    
    rule_upper = str(rule or "").upper()
    has_valid_season = True
    if rule_upper.endswith("D") and rule_upper[:-1].isdigit() and int(rule_upper[:-1]) > 1:
        has_valid_season = False
    
    prophet_weekly = 0.0
    if has_valid_season and "weekly" in forecast.columns and not forecast.empty:
        w_val = float(forecast["weekly"].iloc[0])
        w_usd = w_val * prophet_trend if is_multi else w_val
        y_val = float(forecast["yhat"].iloc[0])
        prophet_weekly = (w_usd / y_val) if y_val else 0.0
        
    prophet_monthly = 0.0
    if has_valid_season and "monthly" in forecast.columns and not forecast.empty:
        m_val = float(forecast["monthly"].iloc[0])
        m_usd = m_val * prophet_trend if is_multi else m_val
        y_val = float(forecast["yhat"].iloc[0])
        prophet_monthly = (m_usd / y_val) if y_val else 0.0

    return {
        "symbol": symbol,
        "analysisDate": today_market_date(),
        "analysisTimestampLocal": today_market_timestamp_iso(),
        "name": metadata.get("name") or symbol,
        "sector": metadata.get("sector") or "",
        "cadenceProfile": cadence_profile["name"],
        "screeningRule": rule,
        "currentPrice": current_price,
        "lastClosePrice": current_price,
        "finalAction": decision["action"],
        "directionScore": safe_float(decision.get("score")),
        "uncertaintyRatio": uncertainty_ratio,
        "prophetTrend": prophet_trend,
        "prophetTrendSlope": prophet_trend_slope,
        "prophetWeekly": prophet_weekly,
        "prophetMonthly": prophet_monthly,
        "firstMomentPctPerHour": first_moment_pct_per_hour,
        "secondMomentPctPerHour2": second_moment_pct_per_hour2,
        "firstMomentPctPerDay": first_moment_pct_per_day,
        "secondMomentBpPerDay2": second_moment_bp_per_day2,
        "firstCoordinateSpace": momentum_space,
        "secondCoordinateSpace": conviction_space,
        "optimalBuyTimestamp": optimal_buy_timestamp,
        "optimalBuyPrice": min_forecast_price,
        "optimalSellTimestamp": optimal_sell_timestamp,
        "optimalSellPrice": max_forecast_price,
        "timeToOptimalBuySeconds": safe_float(time_to_optimal_buy_seconds),
        "timeToOptimalSellSeconds": safe_float(time_to_optimal_sell_seconds),
        "drawdownStartTimestamp": (
            pd.Timestamp(drawdown_metrics["drawdown_start_timestamp"]).isoformat()
            if drawdown_metrics.get("drawdown_start_timestamp") is not None
            else None
        ),
        "drawdownRecoveryTimestamp": (
            pd.Timestamp(drawdown_metrics["drawdown_recovery_timestamp"]).isoformat()
            if drawdown_metrics.get("drawdown_recovery_timestamp") is not None
            else None
        ),
        "drawdownTroughTimestamp": (
            pd.Timestamp(drawdown_metrics["drawdown_trough_timestamp"]).isoformat()
            if drawdown_metrics.get("drawdown_trough_timestamp") is not None
            else None
        ),
        "drawdownTroughPrice": safe_float(drawdown_metrics.get("drawdown_trough_price")),
        "drawdownLingerSeconds": safe_float(drawdown_metrics.get("drawdown_linger_seconds")),
        "drawdownRecoveryInHorizon": bool(drawdown_metrics.get("drawdown_recovery_in_horizon")),
        "troughToRecoverySeconds": safe_float(drawdown_metrics.get("trough_to_recovery_seconds")),
        "maxDrawdownPct": safe_float(drawdown_metrics.get("max_drawdown_pct")),
        "spikeStartTimestamp": (
            pd.Timestamp(spike_metrics["spike_start_timestamp"]).isoformat()
            if spike_metrics.get("spike_start_timestamp") is not None
            else None
        ),
        "spikePeakTimestamp": (
            pd.Timestamp(spike_metrics["spike_peak_timestamp"]).isoformat()
            if spike_metrics.get("spike_peak_timestamp") is not None
            else None
        ),
        "spikePeakPrice": safe_float(spike_metrics.get("spike_peak_price")),
        "spikeSustainSeconds": safe_float(spike_metrics.get("spike_sustain_seconds")),
        "spikeFadeTimestamp": (
            pd.Timestamp(spike_metrics["spike_fade_timestamp"]).isoformat()
            if spike_metrics.get("spike_fade_timestamp") is not None
            else None
        ),
        "spikeFadeInHorizon": spike_metrics.get("spike_fade_in_horizon"),
        "peakToFadeSeconds": safe_float(spike_metrics.get("peak_to_fade_seconds")),
        "maxSpikePct": safe_float(spike_metrics.get("max_spike_pct")),
        "expectedReturnPct": expected_return_pct,
        "maxUpsidePct": max_upside_pct,
        "drawdownToBuyPct": drawdown_to_buy_pct,
        "quadrant": classify_quadrant(first_moment_pct_per_day, second_moment_bp_per_day2),
        "tailDiagnostics": tail_diagnostics,
        "longTailScore": safe_float(
            (tail_diagnostics.get("longTailScore") or 0.0) * 100.0
        ),
        "heavyTailStatScore": safe_float(
            (tail_diagnostics.get("heavyTailScore") or 0.0) * 100.0
        ),
        "leftTailRiskScore": safe_float(
            (tail_diagnostics.get("leftTailRiskScore") or 0.0) * 100.0
        ),
        "tailRegimeLabel": tail_diagnostics.get("regimeLabel"),
        "tailRationale": tail_diagnostics.get("rationale"),
    }


def compute_standard_scores(values: List[Optional[float]]) -> List[float]:
    numeric = np.array(
        [value if value is not None and np.isfinite(value) else np.nan for value in values],
        dtype=float,
    )
    valid = numeric[~np.isnan(numeric)]
    if valid.size == 0:
        return [0.0] * len(values)
    mean = float(valid.mean())
    std = float(valid.std())
    if std <= 1e-12:
        return [0.0] * len(values)
    normalized = np.nan_to_num((numeric - mean) / std, nan=0.0)
    clipped = np.clip(normalized, -3.0, 3.0)
    return clipped.astype(float).tolist()


def enrich_with_fmkorea_surge_context(
    items: List[Dict[str, Any]],
    snapshot: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not snapshot:
        return items
    enriched: List[Dict[str, Any]] = []
    for item in items:
        symbol_heat = fmkorea_symbol_heat(snapshot, str(item.get("symbol") or ""))
        score = safe_float(symbol_heat.get("score")) or 0.0
        mentions = int(symbol_heat.get("mentions") or 0)
        enriched.append(
            {
                **item,
                "fmkoreaSurgeScore": score,
                "fmkoreaMentionCount": mentions,
                "fmkoreaSurgeLabel": symbol_heat.get("label"),
                "fmkoreaSurgeContext": {
                    "source": "fmkorea",
                    "board": "stock",
                    "score": score,
                    "mentions": mentions,
                    "label": symbol_heat.get("label"),
                    "heatScore": safe_float(snapshot.get("heatScore")),
                    "regime": snapshot.get("regime"),
                },
            }
        )
    return enriched


def logistic_score(value: float) -> float:
    clipped = float(np.clip(value, -8.0, 8.0))
    return float(100.0 / (1.0 + np.exp(-clipped)))


def clip01(value: float) -> float:
    return float(np.clip(value, 0.0, 1.0))


def belief_probability(score: float, *, bias: float = 0.0, temperature: float = 1.0) -> float:
    safe_temperature = max(0.4, float(temperature))
    shifted = (float(score) + float(bias)) / safe_temperature
    return clip01(1.0 / (1.0 + np.exp(-np.clip(shifted, -8.0, 8.0))))


def belief_label(score: Optional[float]) -> str:
    numeric = safe_float(score) or 0.0
    if numeric >= 82.0:
        return "anchored belief"
    if numeric >= 72.0:
        return "constructive belief"
    if numeric >= 60.0:
        return "exploratory belief"
    if numeric >= 48.0:
        return "tentative belief"
    return "fragile belief"


def build_belief_rationale(item: Dict[str, Any], score: Optional[float]) -> str:
    uncertainty = safe_float(item.get("uncertaintyRatio"))
    upside = safe_float(item.get("maxUpsidePct"))
    trajectory = item.get("trajectory") or {}
    persistence = safe_float(trajectory.get("persistenceScore"))
    regime_risk = safe_float(trajectory.get("regimeShiftRisk"))
    web_neural_confidence = safe_float(item.get("webNeuralConfidence"))
    belief_network = item.get("beliefNetwork") or {}
    private_signal = safe_float(belief_network.get("privateSignalPct"))
    crowd_belief = safe_float(belief_network.get("crowdBeliefPct"))
    agreement_ratio = safe_float(belief_network.get("agreementRatio"))
    human_bias = item.get("humanBias") or {}
    human_bias_score = safe_float(human_bias.get("score"))
    human_bias_label_text = str(human_bias.get("label") or "attention diffuse")
    human_bias_short_count = safe_float(human_bias.get("shortCount"))

    parts = [
        f"belief stays {belief_label(score)}",
        f"upside {upside * 100:.1f}%" if upside is not None else "upside unknown",
        (
            f"persistence {persistence * 100:.0f}%"
            if persistence is not None
            else "persistence unknown"
        ),
        (
            f"uncertainty {uncertainty * 100:.1f}%"
            if uncertainty is not None
            else "uncertainty unknown"
        ),
        (
            f"regime risk {regime_risk * 100:.0f}%"
            if regime_risk is not None
            else "regime risk unknown"
        ),
    ]
    if web_neural_confidence is not None:
        parts.append(f"web confidence {web_neural_confidence * 100:.0f}%")
    if private_signal is not None and crowd_belief is not None:
        parts.append(
            f"private {private_signal:.0f} vs crowd {crowd_belief:.0f}"
        )
    if human_bias_score is not None:
        if human_bias_short_count is not None:
            parts.append(
                f"user attention {human_bias_score:.0f} ({human_bias_label_text}, {human_bias_short_count:.0f} recent taps)"
            )
        else:
            parts.append(f"user attention {human_bias_score:.0f} ({human_bias_label_text})")
    if agreement_ratio is not None:
        parts.append(f"agreement {agreement_ratio * 100:.0f}%")
    return ", ".join(parts)


def build_parallel_belief_network(
    item: Dict[str, Any],
    *,
    standardized: Dict[str, float],
    action_bonus: float,
    uncertainty_ratio: float,
) -> Dict[str, Any]:
    human_bias_snapshot = item.get("humanBias") or {}
    human_bias_score = max(
        0.0,
        min(1.0, (safe_float(human_bias_snapshot.get("score")) or 0.0) / 100.0),
    )
    human_bias_intensity = max(
        0.0,
        min(1.0, (safe_float(human_bias_snapshot.get("intensityPct")) or 0.0) / 100.0),
    )
    human_bias_share = max(
        0.0,
        min(1.0, (safe_float(human_bias_snapshot.get("shortSharePct")) or 0.0) / 100.0),
    )
    human_bias_trend = max(
        0.0,
        min(1.0, safe_float(human_bias_snapshot.get("trendScore")) or 0.0),
    )
    private_signal_raw = (
        standardized.get("upside", 0.0) * 0.24
        + standardized.get("direction", 0.0) * 0.18
        + standardized.get("mx", 0.0) * 0.16
        + standardized.get("cx", 0.0) * 0.14
        + standardized.get("persistence", 0.0) * 0.14
        + standardized.get("webScore", 0.0) * 0.08
        - standardized.get("regime", 0.0) * 0.16
        - max(0.0, uncertainty_ratio - 0.035) * 2.6
        + action_bonus * 0.7
    )
    private_signal = belief_probability(private_signal_raw, temperature=0.92)

    agent_specs = [
        {
            "name": "optimistic_scout",
            "label": "optimistic scout",
            "weight": 0.17,
            "bias_label": "slightly optimistic",
            "score": (
                standardized.get("upside", 0.0) * 0.34
                + standardized.get("direction", 0.0) * 0.18
                + standardized.get("mx", 0.0) * 0.15
                + standardized.get("webScore", 0.0) * 0.11
                - standardized.get("regime", 0.0) * 0.10
            ),
            "bias": 0.22,
            "temperature": 0.88,
            "threshold": 0.56,
        },
        {
            "name": "conservative_verifier",
            "label": "conservative verifier",
            "weight": 0.21,
            "bias_label": "cautious",
            "score": (
                standardized.get("persistence", 0.0) * 0.24
                + standardized.get("stability", 0.0) * 0.16
                + standardized.get("cx", 0.0) * 0.12
                - standardized.get("regime", 0.0) * 0.24
                - max(0.0, uncertainty_ratio - 0.03) * 2.8
            ),
            "bias": -0.16,
            "temperature": 0.94,
            "threshold": 0.54,
        },
        {
            "name": "trajectory_watcher",
            "label": "trajectory watcher",
            "weight": 0.22,
            "bias_label": "path dependent",
            "score": (
                standardized.get("persistence", 0.0) * 0.30
                + standardized.get("continuation", 0.0) * 0.18
                + standardized.get("my", 0.0) * 0.08
                + standardized.get("cy", 0.0) * 0.10
                - standardized.get("regime", 0.0) * 0.18
            ),
            "bias": 0.04,
            "temperature": 0.9,
            "threshold": 0.53,
        },
        {
            "name": "geometry_filter",
            "label": "geometry filter",
            "weight": 0.18,
            "bias_label": "geometry anchored",
            "score": (
                standardized.get("cx", 0.0) * 0.24
                + standardized.get("cy", 0.0) * 0.18
                + standardized.get("mx", 0.0) * 0.10
                - standardized.get("regime", 0.0) * 0.14
            ),
            "bias": -0.04,
            "temperature": 1.02,
            "threshold": 0.52,
        },
        {
            "name": "web_prophet_scout",
            "label": "web-prophet scout",
            "weight": 0.22,
            "bias_label": "model driven",
            "score": (
                standardized.get("webScore", 0.0) * 0.22
                + standardized.get("webConfidence", 0.0) * 0.14
                + standardized.get("fmkoreaSurge", 0.0) * 0.08
                + standardized.get("direction", 0.0) * 0.10
                + standardized.get("upside", 0.0) * 0.12
                - standardized.get("regime", 0.0) * 0.14
            ),
            "bias": 0.08,
            "temperature": 0.86,
            "threshold": 0.55,
        },
        {
            "name": "human_attention_echo",
            "label": "human attention echo",
            "weight": 0.16,
            "bias_label": "crowd attentive",
            "score": (
                standardized.get("humanBias", 0.0) * 0.24
                + standardized.get("humanBiasTrend", 0.0) * 0.18
                + human_bias_intensity * 0.16
                + human_bias_share * 0.16
                + standardized.get("webScore", 0.0) * 0.06
                - standardized.get("regime", 0.0) * 0.08
            ),
            "bias": 0.06,
            "temperature": 0.94,
            "threshold": 0.53,
        },
    ]

    distributed_agents: List[Dict[str, Any]] = []
    weighted_belief_sum = 0.0
    weighted_vote_sum = 0.0
    votes: List[float] = []
    belief_values: List[float] = []
    total_agent_weight = sum(float(spec["weight"]) for spec in agent_specs) or 1.0

    for spec in agent_specs:
        normalized_weight = float(spec["weight"]) / total_agent_weight
        probability = belief_probability(
            spec["score"],
            bias=spec["bias"],
            temperature=spec["temperature"],
        )
        supportive = probability >= float(spec["threshold"])
        vote = 1.0 if supportive else 0.0
        distributed_agents.append(
            {
                "name": spec["name"],
                "label": spec["label"],
                "weight": normalized_weight,
                "biasLabel": spec["bias_label"],
                "beliefPct": probability * 100.0,
                "stance": "support" if supportive else "hesitate",
            }
        )
        weighted_belief_sum += probability * normalized_weight
        weighted_vote_sum += vote * normalized_weight
        votes.append(vote)
        belief_values.append(probability)

    crowd_belief = clip01(weighted_belief_sum)
    agreement_ratio = clip01(weighted_vote_sum)
    polarization = float(np.std(np.asarray(belief_values, dtype=float))) if belief_values else 0.0

    central_logit = (
        np.log(np.clip(private_signal, 1e-6, 1 - 1e-6) / np.clip(1 - private_signal, 1e-6, 1 - 1e-6)) * 0.62
        + np.log(np.clip(crowd_belief, 1e-6, 1 - 1e-6) / np.clip(1 - crowd_belief, 1e-6, 1 - 1e-6)) * 0.38
        + (agreement_ratio - 0.5) * 0.75
        - polarization * 0.9
    )
    central_belief = belief_probability(float(central_logit), temperature=1.0)
    central_belief_pct = central_belief * 100.0

    if central_belief >= 0.62 and agreement_ratio >= 0.55:
        consensus_action = "support"
    elif central_belief <= 0.42:
        consensus_action = "hesitate"
    else:
        consensus_action = "mixed"

    return {
        "model": "parallel social learning with beliefs",
        "privateSignalPct": private_signal * 100.0,
        "crowdBeliefPct": crowd_belief * 100.0,
        "humanBiasPct": human_bias_score * 100.0,
        "humanBiasLabel": human_bias_snapshot.get("label"),
        "attentionCountShort": int(human_bias_snapshot.get("shortCount") or 0),
        "attentionCountLong": int(human_bias_snapshot.get("longCount") or 0),
        "attentionIntensityPct": human_bias_intensity * 100.0,
        "attentionSharePct": human_bias_share * 100.0,
        "attentionTrendScore": human_bias_trend,
        "centralBeliefPct": central_belief_pct,
        "agreementRatio": agreement_ratio,
        "polarizationScore": polarization,
        "consensusAction": consensus_action,
        "agentCount": len(distributed_agents),
        "distributedAgents": distributed_agents,
    }


def dark_horse_label(score: Optional[float]) -> str:
    numeric = safe_float(score) or 0.0
    if numeric >= 78.0:
        return "high-conviction dark horse"
    if numeric >= 68.0:
        return "emerging dark horse"
    if numeric >= 58.0:
        return "watchlist symmetry candidate"
    return "symmetry-neutral"


def heavy_tail_label(score: Optional[float]) -> str:
    numeric = safe_float(score) or 0.0
    if numeric >= 78.0:
        return "high heavy-tail small-cap proxy"
    if numeric >= 66.0:
        return "emerging heavy-tail proxy"
    if numeric >= 56.0:
        return "watchlist tail proxy"
    return "tail-neutral"


def enrich_with_small_cap_heavy_tail(
    ranked_items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not ranked_items:
        return ranked_items

    proxy_small_cap_scores = compute_standard_scores(
        [
            (
                (safe_float(item.get("darkHorseScore")) or 0.0) * 0.45
                + (safe_float(item.get("webNeuralNovelty")) or 0.0) * 18.0
                + max(0.0, safe_float(item.get("uncertaintyRatio")) or 0.0) * 5.0
            )
            for item in ranked_items
        ]
    )
    spike_scores = compute_standard_scores(
        [safe_float(item.get("maxSpikePct")) for item in ranked_items]
    )
    sustain_scores = compute_standard_scores(
        [
            safe_float(item.get("spikeSustainSeconds")) / 86_400.0
            if safe_float(item.get("spikeSustainSeconds")) is not None
            else None
            for item in ranked_items
        ]
    )
    downside_scores = compute_standard_scores(
        [
            abs(min(0.0, safe_float(item.get("maxDrawdownPct")) or 0.0))
            for item in ranked_items
        ]
    )
    persistence_scores = compute_standard_scores(
        [
            safe_float((item.get("trajectory") or {}).get("persistenceScore"))
            for item in ranked_items
        ]
    )
    actual_heavy_tail_scores = compute_standard_scores(
        [
            safe_float(((item.get("tailDiagnostics") or {}).get("heavyTailScore")))
            for item in ranked_items
        ]
    )
    actual_long_tail_scores = compute_standard_scores(
        [
            safe_float(((item.get("tailDiagnostics") or {}).get("longTailScore")))
            for item in ranked_items
        ]
    )
    actual_left_tail_scores = compute_standard_scores(
        [
            safe_float(((item.get("tailDiagnostics") or {}).get("leftTailRiskScore")))
            for item in ranked_items
        ]
    )

    for index, item in enumerate(ranked_items):
        proxy_small_cap_score = logistic_score(
            proxy_small_cap_scores[index] * 0.92
            + spike_scores[index] * 0.34
            + sustain_scores[index] * 0.24
            + persistence_scores[index] * 0.18
            + actual_long_tail_scores[index] * 0.26
        )
        heavy_tail_score = logistic_score(
            proxy_small_cap_scores[index] * 0.42
            + spike_scores[index] * 0.78
            + sustain_scores[index] * 0.52
            + persistence_scores[index] * 0.26
            + actual_heavy_tail_scores[index] * 0.62
            + actual_long_tail_scores[index] * 0.22
            - actual_left_tail_scores[index] * 0.20
            - downside_scores[index] * 0.38
        )
        tail_diagnostics = item.get("tailDiagnostics") or {}
        skewness = safe_float(tail_diagnostics.get("skewness")) or 0.0
        excess_kurtosis = safe_float(tail_diagnostics.get("excessKurtosis")) or 0.0
        tail_regime_label = tail_diagnostics.get("regimeLabel") or heavy_tail_label(
            heavy_tail_score
        )
        tail_label = heavy_tail_label(heavy_tail_score)
        rationale = (
            f"{item.get('symbol')} shows a small-cap tail proxy score of {proxy_small_cap_score:.1f} "
            f"and heavy-tail score of {heavy_tail_score:.1f}, with statistical tail regime {tail_regime_label}, "
            f"skew {skewness:.2f}, excess kurtosis {excess_kurtosis:.2f}, spike sustain, web-model novelty, "
            f"symmetry asymmetry, and persistence relative to its downside profile."
        )
        item["smallCapTailProxyScore"] = safe_float(proxy_small_cap_score)
        item["heavyTailProxyScore"] = safe_float(heavy_tail_score)
        item["heavyTailLabel"] = tail_label
        item["heavyTailRationale"] = rationale
    return ranked_items


def enrich_with_symmetry_dark_horses(
    ranked_items: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if len(ranked_items) <= 1:
        for item in ranked_items:
            item["symmetry"] = {
                "counterpartSymbol": None,
                "counterpartAction": None,
                "counterpartQuadrant": None,
                "residualScore": None,
                "qualityScore": None,
                "underfollowedScore": 0.0,
                "recoveryBiasScore": None,
                "mirrorStressScore": None,
                "darkHorseScore": None,
                "label": "symmetry-neutral",
                "rationale": None,
            }
            item["darkHorseScore"] = None
            item["darkHorseLabel"] = "symmetry-neutral"
            item["darkHorseRationale"] = None
            item["darkHorseRank"] = None
        return ranked_items, []

    def state_vector(item: Dict[str, Any]) -> np.ndarray:
        state = item.get("_symmetryState") or {}
        return np.asarray(
            [
                safe_float(state.get("mx")) or 0.0,
                safe_float(state.get("my")) or 0.0,
                safe_float(state.get("cx")) or 0.0,
                safe_float(state.get("cy")) or 0.0,
            ],
            dtype=float,
        )

    vectors = [state_vector(item) for item in ranked_items]

    for index, item in enumerate(ranked_items):
        vector = vectors[index]
        best_counterpart_index: Optional[int] = None
        best_residual: Optional[float] = None

        for candidate_index, candidate_vector in enumerate(vectors):
            if candidate_index == index:
                continue
            residual = float(np.linalg.norm(candidate_vector + vector) / np.sqrt(len(vector)))
            if best_residual is None or residual < best_residual:
                best_residual = residual
                best_counterpart_index = candidate_index

        counterpart = (
            ranked_items[best_counterpart_index]
            if best_counterpart_index is not None
            else None
        )
        counterpart_state = (counterpart or {}).get("_symmetryState") or {}
        current_state = item.get("_symmetryState") or {}

        symmetry_quality = (
            safe_float(1.0 / (1.0 + max(0.0, best_residual or 0.0)))
            if best_residual is not None
            else None
        )
        rank_ratio = index / max(1, len(ranked_items) - 1)
        underfollowed_score = float(np.clip((rank_ratio - 0.08) / 0.92, 0.0, 1.0))
        recovery_bias = (
            max(0.0, safe_float(current_state.get("mx")) or 0.0) * 0.18
            + max(0.0, safe_float(current_state.get("my")) or 0.0) * 0.08
            + max(0.0, safe_float(current_state.get("cx")) or 0.0) * 0.16
            + max(0.0, safe_float(current_state.get("cy")) or 0.0) * 0.08
            + max(0.0, safe_float(current_state.get("upside")) or 0.0) * 0.16
            + max(0.0, safe_float(current_state.get("persistence")) or 0.0) * 0.18
            + max(0.0, safe_float(current_state.get("continuation")) or 0.0) * 0.10
            + max(0.0, safe_float(current_state.get("stability")) or 0.0) * 0.06
        )
        mirror_stress = (
            max(0.0, -(safe_float(counterpart_state.get("mx")) or 0.0)) * 0.18
            + max(0.0, -(safe_float(counterpart_state.get("upside")) or 0.0)) * 0.18
            + max(0.0, safe_float(counterpart_state.get("regime")) or 0.0) * 0.16
            + max(0.0, -(safe_float(counterpart_state.get("direction")) or 0.0)) * 0.10
        )
        uncertainty_penalty = max(
            0.0,
            (safe_float(item.get("uncertaintyRatio")) or 0.0) - 0.06,
        ) * 4.0
        regime_penalty = max(
            0.0,
            safe_float((item.get("trajectory") or {}).get("regimeShiftRisk")) or 0.0,
        ) * 0.60
        action = str(item.get("finalAction") or "HOLD")
        action_bonus = 0.14 if action == "BUY" else 0.05 if action == "HOLD" else -0.25
        raw_dark_horse = (
            (symmetry_quality or 0.0) * 1.20
            + recovery_bias
            + mirror_stress * 0.80
            + underfollowed_score * 0.90
            + action_bonus
            - uncertainty_penalty
            - regime_penalty
        )
        dark_horse_score = logistic_score(raw_dark_horse)
        label = dark_horse_label(dark_horse_score)
        rationale = (
            f"{item.get('symbol')} sits in a symmetry recovery pocket against "
            f"{counterpart.get('symbol') if counterpart else 'the market mirror'}, "
            f"with residual {best_residual:.2f}, underfollowed score {underfollowed_score * 100:.0f}%, "
            f"persistence {((item.get('trajectory') or {}).get('persistenceScore') or 0.0) * 100:.0f}%, "
            f"and upside {(safe_float(item.get('maxUpsidePct')) or 0.0) * 100:.1f}%."
            if best_residual is not None
            else None
        )

        symmetry_payload = {
            "counterpartSymbol": counterpart.get("symbol") if counterpart else None,
            "counterpartAction": counterpart.get("finalAction") if counterpart else None,
            "counterpartQuadrant": counterpart.get("quadrant") if counterpart else None,
            "residualScore": safe_float(best_residual),
            "qualityScore": symmetry_quality,
            "underfollowedScore": safe_float(underfollowed_score),
            "recoveryBiasScore": safe_float(recovery_bias),
            "mirrorStressScore": safe_float(mirror_stress),
            "darkHorseScore": safe_float(dark_horse_score),
            "label": label,
            "rationale": rationale,
        }
        item["symmetry"] = symmetry_payload
        item["darkHorseScore"] = safe_float(dark_horse_score)
        item["darkHorseLabel"] = label
        item["darkHorseRationale"] = rationale

    dark_horse_candidates = [
        candidate
        for candidate in sorted(
            ranked_items,
            key=lambda entry: safe_float(entry.get("darkHorseScore")) or -999.0,
            reverse=True,
        )
        if str(candidate.get("finalAction") or "HOLD") != "SELL"
    ]

    dark_horse_picks: List[Dict[str, Any]] = []
    seen_symbols = set()
    MAX_LINGER_SECONDS = 15.0 * 86400.0
    for item in dark_horse_candidates:
        linger = safe_float(item.get("drawdownLingerSeconds"))
        if linger is not None and linger > MAX_LINGER_SECONDS:
            continue
        symmetry = item.get("symmetry") or {}
        if (
            (safe_float(item.get("darkHorseScore")) or 0.0) < 58.0
            or (safe_float(symmetry.get("underfollowedScore")) or 0.0) < 0.12
        ):
            continue
        dark_horse_picks.append(item)
        seen_symbols.add(item.get("symbol"))
        if len(dark_horse_picks) >= 10:
            break

    if len(dark_horse_picks) < 10:
        for item in dark_horse_candidates:
            if item.get("symbol") in seen_symbols:
                continue
            linger = safe_float(item.get("drawdownLingerSeconds"))
            if linger is not None and linger > MAX_LINGER_SECONDS:
                continue
            dark_horse_picks.append(item)
            seen_symbols.add(item.get("symbol"))
            if len(dark_horse_picks) >= 10:
                break

    rank_lookup = {
        str(item.get("symbol")): index + 1 for index, item in enumerate(dark_horse_picks)
    }
    for item in ranked_items:
        item["darkHorseRank"] = rank_lookup.get(str(item.get("symbol")))
        item.pop("_symmetryState", None)

    return ranked_items, dark_horse_picks


def enrich_with_trajectory_context(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not items:
        return items

    history_rows = load_recent_symbol_information_map_rows(
        [item["symbol"] for item in items],
        lookback_days=DEFAULT_TRAJECTORY_LOOKBACK,
    )

    enriched_items: List[Dict[str, Any]] = []
    for item in items:
        trajectory = compute_symbol_trajectory_metrics(
            history_rows.get(item["symbol"], []),
            current_snapshot=item,
        )
        enriched_items.append(
            {
                **item,
                "trajectory": trajectory,
            }
        )

    return enriched_items


def enrich_with_human_bias_context(
    items: List[Dict[str, Any]],
    *,
    market_mode: str = "sp500",
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not items:
        return items, load_market_interest_overview(market_mode=market_mode)

    attention_map = load_symbol_interest_map(
        [str(item.get("symbol") or "") for item in items],
        market_mode=market_mode,
    )
    market_overview = load_market_interest_overview(market_mode=market_mode)

    enriched_items: List[Dict[str, Any]] = []
    for item in items:
        symbol = str(item.get("symbol") or "")
        attention = attention_map.get(symbol) or {}
        enriched_items.append(
            {
                **item,
                "humanBias": attention,
                "humanBiasScore": safe_float(attention.get("score")),
                "humanBiasLabel": attention.get("label"),
                "humanBiasRationale": attention.get("rationale"),
            }
        )

    return enriched_items, market_overview


def optimize_information_map(
    items: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    momentum_x_scores = compute_standard_scores(
        [
            (
                item.get("firstCoordinateSpace")
                or item.get("momentumSpace")
                or {}
            ).get("x")
            for item in items
        ]
    )
    momentum_y_scores = compute_standard_scores(
        [
            (
                item.get("firstCoordinateSpace")
                or item.get("momentumSpace")
                or {}
            ).get("y")
            for item in items
        ]
    )
    conviction_x_scores = compute_standard_scores(
        [
            (
                item.get("secondCoordinateSpace")
                or item.get("convictionSpace")
                or {}
            ).get("x")
            for item in items
        ]
    )
    conviction_y_scores = compute_standard_scores(
        [
            (
                item.get("secondCoordinateSpace")
                or item.get("convictionSpace")
                or {}
            ).get("y")
            for item in items
        ]
    )
    upside_scores = compute_standard_scores([item.get("maxUpsidePct") for item in items])
    direction_scores = compute_standard_scores([item.get("directionScore") for item in items])
    persistence_scores = compute_standard_scores(
        [(item.get("trajectory") or {}).get("persistenceScore") for item in items]
    )
    stability_scores = compute_standard_scores(
        [(item.get("trajectory") or {}).get("stabilityScore") for item in items]
    )
    regime_risk_scores = compute_standard_scores(
        [(item.get("trajectory") or {}).get("regimeShiftRisk") for item in items]
    )
    continuation_scores = compute_standard_scores(
        [(item.get("trajectory") or {}).get("continuationBias") for item in items]
    )
    web_neural_scores = compute_standard_scores([item.get("webNeuralScore") for item in items])
    web_neural_confidences = compute_standard_scores(
        [item.get("webNeuralConfidence") for item in items]
    )
    fmkorea_surge_scores = compute_standard_scores([item.get("fmkoreaSurgeScore") for item in items])
    human_bias_scores = compute_standard_scores([item.get("humanBiasScore") for item in items])
    human_bias_trend_scores = compute_standard_scores(
        [
            ((item.get("humanBias") or {}).get("trendScore"))
            for item in items
        ]
    )

    ranked_items: List[Dict[str, Any]] = []
    for index, item in enumerate(items):
        action = item.get("finalAction")
        action_bonus = 0.35 if action == "BUY" else 0.05 if action == "HOLD" else -0.45
        negative_penalty = 0.0
        uncertainty_ratio = max(0.0, safe_float(item.get("uncertaintyRatio")) or 0.0)
        if (item.get("firstMomentPctPerDay") or 0.0) < 0:
            negative_penalty += 0.18
        if (item.get("maxUpsidePct") or 0.0) < 0:
            negative_penalty += 0.22
        if uncertainty_ratio > 0.08:
            negative_penalty += 0.12
        if ((item.get("trajectory") or {}).get("regimeShiftRisk") or 0.0) > 0.72:
            negative_penalty += 0.18

        optimization_score = (
            momentum_x_scores[index] * 0.26
            + momentum_y_scores[index] * 0.14
            + conviction_x_scores[index] * 0.18
            + conviction_y_scores[index] * 0.12
            + upside_scores[index] * 0.18
            + direction_scores[index] * 0.12
            + persistence_scores[index] * 0.18
            + stability_scores[index] * 0.10
            + continuation_scores[index] * 0.08
            + web_neural_scores[index] * 0.10
            + web_neural_confidences[index] * 0.04
            + fmkorea_surge_scores[index] * 0.05
            + human_bias_scores[index] * 0.06
            + human_bias_trend_scores[index] * 0.03
            - regime_risk_scores[index] * 0.18
            + action_bonus
            - negative_penalty
        )
        belief_penalty = max(0.0, uncertainty_ratio - 0.03) * 5.2
        belief_raw = (
            momentum_x_scores[index] * 0.20
            + momentum_y_scores[index] * 0.10
            + conviction_x_scores[index] * 0.16
            + conviction_y_scores[index] * 0.10
            + upside_scores[index] * 0.16
            + persistence_scores[index] * 0.18
            + stability_scores[index] * 0.08
            + continuation_scores[index] * 0.08
            + web_neural_scores[index] * 0.10
            + web_neural_confidences[index] * 0.04
            + fmkorea_surge_scores[index] * 0.04
            + human_bias_scores[index] * 0.08
            + human_bias_trend_scores[index] * 0.04
            - regime_risk_scores[index] * 0.18
            + action_bonus * 0.8
            - belief_penalty
            - negative_penalty * 0.65
        )
        belief_network = build_parallel_belief_network(
            item,
            standardized={
                "mx": momentum_x_scores[index],
                "my": momentum_y_scores[index],
                "cx": conviction_x_scores[index],
                "cy": conviction_y_scores[index],
                "upside": upside_scores[index],
                "direction": direction_scores[index],
                "persistence": persistence_scores[index],
                "stability": stability_scores[index],
                "regime": regime_risk_scores[index],
                "continuation": continuation_scores[index],
                "webScore": web_neural_scores[index],
                "webConfidence": web_neural_confidences[index],
                "fmkoreaSurge": fmkorea_surge_scores[index],
                "humanBias": human_bias_scores[index],
                "humanBiasTrend": human_bias_trend_scores[index],
            },
            action_bonus=action_bonus,
            uncertainty_ratio=uncertainty_ratio,
        )
        belief_score = safe_float(belief_network.get("centralBeliefPct")) or logistic_score(belief_raw)

        ranked_item = {
            **item,
            "optimizationScore": float(optimization_score),
            "beliefScore": float(belief_score),
            "beliefLabel": belief_label(belief_score),
            "beliefNetwork": belief_network,
            "_symmetryState": {
                "mx": momentum_x_scores[index],
                "my": momentum_y_scores[index],
                "cx": conviction_x_scores[index],
                "cy": conviction_y_scores[index],
                "upside": upside_scores[index],
                "direction": direction_scores[index],
                "persistence": persistence_scores[index],
                "stability": stability_scores[index],
                "regime": regime_risk_scores[index],
                "continuation": continuation_scores[index],
                "webScore": web_neural_scores[index],
                "webConfidence": web_neural_confidences[index],
                "humanBias": human_bias_scores[index],
                "humanBiasTrend": human_bias_trend_scores[index],
            },
        }
        ranked_item["beliefRationale"] = build_belief_rationale(ranked_item, belief_score)
        ranked_items.append(ranked_item)

    ranked_items.sort(key=lambda item: item["optimizationScore"], reverse=True)
    ranked_items, dark_horse_picks = enrich_with_symmetry_dark_horses(ranked_items)
    ranked_items = enrich_with_small_cap_heavy_tail(ranked_items)

    recommended = []
    MAX_LINGER_SECONDS = 15.0 * 86400.0
    for item in ranked_items:
        action = item.get("finalAction")
        score = item.get("optimizationScore", -999.0)
        linger = safe_float(item.get("drawdownLingerSeconds"))
        
        if action != "SELL" and score > -0.5:
            if linger is not None and linger > MAX_LINGER_SECONDS:
                continue
            recommended.append(item)
            
    top_picks = recommended[:10]
    if len(top_picks) < 10:
        seen = {item["symbol"] for item in top_picks}
        for item in ranked_items:
            if item["symbol"] in seen:
                continue
            linger = safe_float(item.get("drawdownLingerSeconds"))
            if linger is not None and linger > MAX_LINGER_SECONDS:
                continue
            top_picks.append(item)
            seen.add(item["symbol"])
            if len(top_picks) >= 10:
                break

    # Compile drawdown exclusions with reasons
    drawdown_exclusions = []
    seen_exclusions = set()
    for item in ranked_items[:25]:
        linger = safe_float(item.get("drawdownLingerSeconds"))
        if linger is not None and linger > MAX_LINGER_SECONDS:
            symbol = item.get("symbol")
            if symbol not in seen_exclusions:
                linger_days = linger / 86400.0
                drawdown_exclusions.append({
                    "symbol": symbol,
                    "name": item.get("name", "N/A"),
                    "lingerDays": float(linger_days),
                    "reason": f"예상 하락 회복 소요 기간 {linger_days:.1f}일 (기준 15.0일 초과)"
                })
                seen_exclusions.add(symbol)
                
    dark_horse_candidates = [
        candidate
        for candidate in sorted(
            ranked_items,
            key=lambda entry: safe_float(entry.get("darkHorseScore")) or -999.0,
            reverse=True,
        )
        if str(candidate.get("finalAction") or "HOLD") != "SELL"
    ]
    for item in dark_horse_candidates[:15]:
        linger = safe_float(item.get("drawdownLingerSeconds"))
        if linger is not None and linger > MAX_LINGER_SECONDS:
            symbol = item.get("symbol")
            if symbol not in seen_exclusions:
                linger_days = linger / 86400.0
                drawdown_exclusions.append({
                    "symbol": symbol,
                    "name": item.get("name", "N/A"),
                    "lingerDays": float(linger_days),
                    "reason": f"예상 하락 회복 소요 기간 {linger_days:.1f}일 (기준 15.0일 초과)"
                })
                seen_exclusions.add(symbol)

    return ranked_items, top_picks, dark_horse_picks, drawdown_exclusions


def build_sp500_information_map(
    *,
    force_refresh: bool = False,
    limit: int = DEFAULT_LIMIT,
    cache_max_age_hours: float = DEFAULT_CACHE_MAX_AGE_HOURS,
) -> Dict[str, Any]:
    try:
        from services.trader.reinforcement import maybe_run_daily_reinforcement_once

        reinforcement_status = maybe_run_daily_reinforcement_once()
    except Exception as exc:  # pragma: no cover - defensive fallback
        reinforcement_status = {
            "runDate": today_market_date(),
            "cached": False,
            "error": str(exc),
        }

    if not force_refresh:
        cached_payload = load_cached_information_map(cache_max_age_hours)
        if cached_payload is not None:
            cached_points = cached_payload.get("points") or []
            scored_cached_points, web_neural_model = score_information_map_items_with_web_nn(
                cached_points,
                force_refresh=False,
            )
            feature_benchmark = public_feature_selection_benchmark_state(
                load_or_run_feature_selection_benchmark(force_refresh=False)
            )
            fmkorea_stock = build_fmkorea_stock_snapshot(force_refresh=False)
            fmkorea_enriched_points = enrich_with_fmkorea_surge_context(
                scored_cached_points,
                fmkorea_stock,
            )
            human_bias_enriched_points, human_bias_market = enrich_with_human_bias_context(
                fmkorea_enriched_points,
                market_mode="sp500",
            )
            ranked_items, top_picks, dark_horse_picks, drawdown_exclusions = optimize_information_map(
                human_bias_enriched_points
            )
            cached_payload["points"] = ranked_items
            cached_payload["topPicks"] = top_picks
            cached_payload["darkHorsePicks"] = dark_horse_picks
            cached_payload["drawdownExclusions"] = drawdown_exclusions
            cached_payload["webNeuralModel"] = web_neural_model
            cached_payload["featureBenchmark"] = feature_benchmark
            cached_payload["fmkoreaStock"] = fmkorea_stock
            cached_payload["humanBiasMarket"] = human_bias_market
            cached_payload["reinforcement"] = reinforcement_status
            return cached_payload

    constituents, close_matrix = ensure_sp500_matrix()
    symbol_metadata = build_symbol_metadata(constituents)
    available_symbols = [
        column
        for column in close_matrix.columns
        if column != "ds" and column in symbol_metadata
    ]
    if limit > 0:
        available_symbols = available_symbols[:limit]

    results: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for symbol in available_symbols:
        raw_df = build_raw_df_from_matrix(close_matrix, symbol)
        if raw_df is None:
            failures.append({"symbol": symbol, "reason": "Missing close history"})
            continue
        try:
            result = screen_symbol(symbol, raw_df, symbol_metadata.get(symbol, {}))
        except Exception as exc:  # pragma: no cover - defensive batch capture
            failures.append({"symbol": symbol, "reason": str(exc)})
            continue
        if result is None:
            failures.append({"symbol": symbol, "reason": "Insufficient data"})
            continue
        results.append(result)

    trajectory_enriched_results = enrich_with_trajectory_context(results)
    web_scored_results, web_neural_model = score_information_map_items_with_web_nn(
        trajectory_enriched_results,
        force_refresh=force_refresh,
    )
    feature_benchmark = public_feature_selection_benchmark_state(
        load_or_run_feature_selection_benchmark(force_refresh=force_refresh)
    )
    fmkorea_stock = build_fmkorea_stock_snapshot(force_refresh=force_refresh)
    fmkorea_enriched_results = enrich_with_fmkorea_surge_context(
        web_scored_results,
        fmkorea_stock,
    )
    human_bias_enriched_results, human_bias_market = enrich_with_human_bias_context(
        fmkorea_enriched_results,
        market_mode="sp500",
    )
    ranked_items, top_picks, dark_horse_picks, drawdown_exclusions = optimize_information_map(human_bias_enriched_results)
    map_date = today_market_date()
    payload = {
        "ok": True,
        "generatedAt": utc_now_iso(),
        "mapDate": map_date,
        "cache": {
            "used": False,
            "path": str(dated_map_snapshot_path(map_date)),
        },
        "reinforcement": reinforcement_status,
        "universe": {
            "evaluatedSymbols": len(results),
            "failedSymbols": len(failures),
            "limit": limit,
        },
        "optimization": {
            "xAxis": "Raw 1st moment (%/day)",
            "yAxis": "Raw 2nd moment (bp/day²)",
            "method": (
                "0.26*z(momentum x) + 0.14*z(momentum y) + "
                "0.18*z(conviction x) + 0.12*z(conviction y) + "
                "0.18*z(max upside) + 0.12*z(direction score) + "
                "0.18*z(persistence) + 0.10*z(stability) + 0.08*z(continuation) + "
                "0.10*z(website neural score) + 0.04*z(website neural confidence) + "
                "0.05*z(Korean retail surge pulse) + 0.06*z(aggregate human symbol bias) + "
                "0.03*z(human bias trend) - "
                "0.18*z(regime risk) + action bonus - penalties"
            ),
            "darkHorseMethod": (
                "Mirror each stock across the information-map symmetry center, find the nearest reflected counterpart, "
                "and reward low symmetry residual, improving recovery bias, bearish mirror contrast, and underfollowed rank."
            ),
            "heavyTailMethod": (
                "Estimate a small-cap heavy-tail proxy from symmetry asymmetry, web-model novelty, spike sustain, "
                "persistence, and downside balance, then pass that proxy into the portfolio construction stage."
            ),
        },
        "webNeuralModel": web_neural_model,
        "featureBenchmark": feature_benchmark,
        "fmkoreaStock": fmkorea_stock,
        "humanBiasMarket": human_bias_market,
        "mapSpaces": {
            "firstCoordinate": {
                "label": "1st coordinate map",
                "xAxis": "1st coordinate x",
                "yAxis": "1st coordinate y",
            },
            "secondCoordinate": {
                "label": "2nd coordinate map",
                "xAxis": "2nd coordinate x",
                "yAxis": "2nd coordinate y",
            },
        },
        "points": ranked_items,
        "topPicks": top_picks,
        "darkHorsePicks": dark_horse_picks,
        "drawdownExclusions": drawdown_exclusions,
        "failures": failures[:50],
    }
    persist_information_map(payload)
    return payload


def main() -> None:
    args = parse_args()
    payload = build_sp500_information_map(
        force_refresh=bool(args.force_refresh),
        limit=max(0, int(args.limit)),
        cache_max_age_hours=max(0.0, float(args.cache_max_age_hours)),
    )
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
