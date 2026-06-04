#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import logging
import os
import re
import secrets
import services.trader.machine_auth
from pathlib import Path

from typing import List, Optional

import pandas as pd
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from services.trader.predict_signal import (
    parse_route_symbols,
    safe_float,
    serialize_wrapper_result,
    summarize_per_rule,
)
from services.trader.pair_fallback import build_pair_raw_df
from services.trader.human_bias import (
    load_symbol_interest_snapshot,
    record_symbol_interest,
)
from services.trader.map_store import today_market_date, today_market_timestamp_iso
from services.trader.correlation_forecast import build_sp500_symbol_correlation_forecast
from services.trader.resource_moe import (
    build_moe_runtime,
    build_skipped_correlation_forecast,
    evaluate_correlation_gate,
)
from services.trader.sp500_information_map import build_sp500_information_map
from services.trader.sp500_portfolio import build_sp500_portfolio
from services.trader.tail_diagnostics import build_tail_diagnostics
from services.trader.reinforcement import (
    ensure_reinforcement_warm_start,
    load_investor_lens_snapshot,
    load_macbook_agent_snapshot,
    load_spike_sustain_snapshot,
)

try:
    from services.llm import WrapperConfig, run_wrapper_pipeline
    from services.llm.weight_store import (
        load_wrapper_weights,
        store_wrapper_prediction_snapshot,
    )
except ImportError:
    WrapperConfig = None
    run_wrapper_pipeline = None
    load_wrapper_weights = None
    store_wrapper_prediction_snapshot = None

logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

ROOT_DIR = Path(__file__).resolve().parents[2]
MPLCONFIGDIR = Path(os.environ.get("MPLCONFIGDIR", "/tmp/no-slip-matplotlib"))
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._/\- ]{0,79}$")
ROUTE_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._/\- ]{0,31}$")
MAX_SP500_MAP_LIMIT = 503
MAX_SP500_PORTFOLIO_HOLDINGS = 25


class ProphetRow(BaseModel):
    ds: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class PredictionRequest(BaseModel):
    symbol: str
    inputMint: Optional[str] = None
    outputMint: Optional[str] = None
    marketMode: Optional[str] = None
    trackHumanBias: bool = False
    humanBiasSource: Optional[str] = None
    data: List[ProphetRow] = []


class LeaderboardSubmission(BaseModel):
    bot_id: str
    symbol: str
    mae: float
    rmse: float
    directional_accuracy: float
    composite_score: float
    cv_folds: int
    updated_at: str


LEADERBOARD_DB_PATH = ROOT_DIR / "services" / "trader" / "model_cache" / "prophet_leaderboard.sqlite3"

def init_leaderboard_db():
    LEADERBOARD_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3
    with sqlite3.connect(LEADERBOARD_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prophet_leaderboard (
                bot_id TEXT,
                symbol TEXT,
                mae REAL,
                rmse REAL,
                directional_accuracy REAL,
                composite_score REAL,
                cv_folds INTEGER,
                updated_at TEXT,
                PRIMARY KEY (bot_id, symbol)
            )
        """)
        conn.commit()


app = FastAPI(title="No Slip Prediction API")


def normalize_symbol(raw_symbol: str) -> str:
    normalized = " ".join((raw_symbol or "").strip().upper().split())
    if not normalized:
        return ""

    if SYMBOL_RE.fullmatch(normalized):
        return normalized

    input_symbol, output_symbol = parse_route_symbols(normalized)
    if (
        input_symbol
        and output_symbol
        and ROUTE_SYMBOL_RE.fullmatch(input_symbol)
        and ROUTE_SYMBOL_RE.fullmatch(output_symbol)
    ):
        return f"{input_symbol}→{output_symbol}"

    return ""


def unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="Invalid prediction API token")


def has_valid_authorization(authorization: Optional[str]) -> bool:
    api_token = os.environ.get("PREDICTION_API_TOKEN", "").strip()
    if not api_token:
        return True

    expected_header = f"Bearer {api_token}"
    provided_header = authorization or ""
    return secrets.compare_digest(provided_header, expected_header)


def require_authorization(authorization: Optional[str]) -> None:
    if not has_valid_authorization(authorization):
        raise unauthorized()


def clamp_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, numeric))


def build_result(
    requested_symbol: str,
    raw_df: pd.DataFrame,
    decision: dict,
    wrapper_result: dict | None,
    *,
    live_price: float | None = None,
    last_close_price: float | None = None,
    human_bias_snapshot: dict | None = None,
    correlation_forecast: dict | None = None,
    tail_diagnostics: dict | None = None,
    moe_runtime: dict | None = None,
) -> dict:
    resolved_last_close_price = last_close_price if last_close_price is not None else safe_float(raw_df["close"].iloc[-1])
    resolved_current_price = safe_float(decision.get("current_price")) or resolved_last_close_price
    tail_diagnostics = tail_diagnostics or build_tail_diagnostics(raw_df)
    return {
        "supported": True,
        "requestedSymbol": requested_symbol,
        "resolvedSymbol": requested_symbol,
        "source": "prediction_api",
        "dataset": None,
        "analysisDate": today_market_date(),
        "analysisTimestampLocal": today_market_timestamp_iso(),
        "rows": int(len(raw_df)),
        "currentPrice": resolved_current_price,
        "livePrice": live_price if live_price is not None else resolved_current_price,
        "lastClosePrice": resolved_last_close_price,
        "finalAction": decision["final_action"],
        "directionVote": float(decision["direction_vote"]),
        "directionStrength": float(decision["direction_strength"]),
        "firstMomentPricePerHour": safe_float(decision.get("first_moment_price_per_hour")),
        "firstMomentPctPerHour": safe_float(decision.get("first_moment_pct_per_hour")),
        "secondMomentPricePerHour2": safe_float(decision.get("second_moment_price_per_hour2")),
        "secondMomentPctPerHour2": safe_float(decision.get("second_moment_pct_per_hour2")),
        "timeToBelowCurrentSeconds": safe_float(
            decision.get("time_to_below_current_seconds")
        ),
        "timeToOptimalBuySeconds": safe_float(decision.get("time_to_optimal_buy_seconds")),
        "timeToOptimalSellSeconds": safe_float(decision.get("time_to_optimal_sell_seconds")),
        "riseWindowSeconds": safe_float(decision.get("rise_window_seconds")),
        "dropWindowSeconds": safe_float(decision.get("drop_window_seconds")),
        "spikeStartTimestamp": (
            str(decision.get("spike_start_timestamp"))
            if decision.get("spike_start_timestamp") is not None
            else None
        ),
        "spikePeakTimestamp": (
            str(decision.get("spike_peak_timestamp"))
            if decision.get("spike_peak_timestamp") is not None
            else None
        ),
        "spikePeakPrice": safe_float(decision.get("spike_peak_price")),
        "spikeSustainSeconds": safe_float(decision.get("spike_sustain_seconds")),
        "spikeFadeTimestamp": (
            str(decision.get("spike_fade_timestamp"))
            if decision.get("spike_fade_timestamp") is not None
            else None
        ),
        "spikeFadeInHorizon": decision.get("spike_fade_in_horizon"),
        "peakToFadeSeconds": safe_float(decision.get("peak_to_fade_seconds")),
        "maxSpikePct": safe_float(decision.get("max_spike_pct")),
        "drawdownStartTimestamp": (
            str(decision.get("drawdown_start_timestamp"))
            if decision.get("drawdown_start_timestamp") is not None
            else None
        ),
        "drawdownRecoveryTimestamp": (
            str(decision.get("drawdown_recovery_timestamp"))
            if decision.get("drawdown_recovery_timestamp") is not None
            else None
        ),
        "drawdownTroughTimestamp": (
            str(decision.get("drawdown_trough_timestamp"))
            if decision.get("drawdown_trough_timestamp") is not None
            else None
        ),
        "drawdownTroughPrice": safe_float(decision.get("drawdown_trough_price")),
        "drawdownLingerSeconds": safe_float(decision.get("drawdown_linger_seconds")),
        "drawdownRecoveryInHorizon": decision.get("drawdown_recovery_in_horizon"),
        "troughToRecoverySeconds": safe_float(decision.get("trough_to_recovery_seconds")),
        "maxDrawdownPct": safe_float(decision.get("max_drawdown_pct")),
        "timesfmDrawdownStartTimestamp": (
            str(decision.get("timesfm_drawdown_start_timestamp"))
            if decision.get("timesfm_drawdown_start_timestamp") is not None
            else None
        ),
        "timesfmDrawdownRecoveryTimestamp": (
            str(decision.get("timesfm_drawdown_recovery_timestamp"))
            if decision.get("timesfm_drawdown_recovery_timestamp") is not None
            else None
        ),
        "timesfmDrawdownTroughTimestamp": (
            str(decision.get("timesfm_drawdown_trough_timestamp"))
            if decision.get("timesfm_drawdown_trough_timestamp") is not None
            else None
        ),
        "timesfmDrawdownTroughPrice": safe_float(decision.get("timesfm_drawdown_trough_price")),
        "timesfmDrawdownLingerSeconds": safe_float(decision.get("timesfm_drawdown_linger_seconds")),
        "timesfmDrawdownRecoveryInHorizon": decision.get("timesfm_drawdown_recovery_in_horizon"),
        "timesfmTroughToRecoverySeconds": safe_float(decision.get("timesfm_trough_to_recovery_seconds")),
        "timesfmMaxDrawdownPct": safe_float(decision.get("timesfm_max_drawdown_pct")),
        "timesfmQuantileBandPct": safe_float(decision.get("timesfm_quantile_band_pct")),
        "timesfmSpikeStartTimestamp": (
            str(decision.get("timesfm_spike_start_timestamp"))
            if decision.get("timesfm_spike_start_timestamp") is not None
            else None
        ),
        "timesfmSpikePeakTimestamp": (
            str(decision.get("timesfm_spike_peak_timestamp"))
            if decision.get("timesfm_spike_peak_timestamp") is not None
            else None
        ),
        "timesfmSpikePeakPrice": safe_float(decision.get("timesfm_spike_peak_price")),
        "timesfmSpikeSustainSeconds": safe_float(decision.get("timesfm_spike_sustain_seconds")),
        "timesfmSpikeFadeTimestamp": (
            str(decision.get("timesfm_spike_fade_timestamp"))
            if decision.get("timesfm_spike_fade_timestamp") is not None
            else None
        ),
        "timesfmSpikeFadeInHorizon": decision.get("timesfm_spike_fade_in_horizon"),
        "timesfmPeakToFadeSeconds": safe_float(decision.get("timesfm_peak_to_fade_seconds")),
        "timesfmMaxSpikePct": safe_float(decision.get("timesfm_max_spike_pct")),
        "timesfmStatus": decision.get("timesfm_status"),
        "timesfmError": decision.get("timesfm_error"),
        "timesfmUsed": bool(decision.get("timesfm_used")),
        "timesfmModelId": decision.get("timesfm_model_id"),
        "timesfmMoeGate": decision.get("timesfm_moe_gate") or None,
        "moeRuntime": moe_runtime or decision.get("moe_runtime") or None,
        "spikeSustainConsensusSeconds": safe_float(decision.get("spike_sustain_consensus_seconds")),
        "peakToFadeConsensusSeconds": safe_float(decision.get("peak_to_fade_consensus_seconds")),
        "spikeFadeConsensusInHorizon": decision.get("spike_fade_consensus_in_horizon"),
        "maxSpikeConsensusPct": safe_float(decision.get("max_spike_consensus_pct")),
        "spikeConsensusSource": decision.get("spike_consensus_source"),
        "prophetSpikeWeight": safe_float(decision.get("prophet_spike_weight")),
        "timesfmSpikeWeight": safe_float(decision.get("timesfm_spike_weight")),
        "drawdownLingerConsensusSeconds": safe_float(decision.get("drawdown_linger_consensus_seconds")),
        "troughToRecoveryConsensusSeconds": safe_float(decision.get("trough_to_recovery_consensus_seconds")),
        "drawdownRecoveryConsensusInHorizon": decision.get("drawdown_recovery_consensus_in_horizon"),
        "maxDrawdownConsensusPct": safe_float(decision.get("max_drawdown_consensus_pct")),
        "drawdownConsensusSource": decision.get("drawdown_consensus_source"),
        "trendCurve": decision.get("trend_curve") or [],
        "forecastPlot": decision.get("forecast_plot") or None,
        "trendComponent": decision.get("trend_component") or None,
        "seasonalityComponents": decision.get("seasonality_components") or {},
        "seasonalitySummary": decision.get("seasonality_summary") or {},
        "avgUncertaintyRatio": safe_float(decision.get("avg_uncertainty_ratio")),
        "geodesicState": decision.get("geodesic_state") or None,
        "geodesicAvailable": bool(decision.get("geodesic_available")),
        "geodesicLabel": decision.get("geodesic_label"),
        "geodesicActionBias": decision.get("geodesic_action_bias"),
        "geodesicHistoryCount": int(decision.get("geodesic_history_count") or 0),
        "geodesicPathLength": safe_float(decision.get("geodesic_path_length")),
        "geodesicCurvature": safe_float(decision.get("geodesic_curvature")),
        "geodesicAlignmentScore": safe_float(decision.get("geodesic_alignment_score")),
        "geodesicDeviationScore": safe_float(decision.get("geodesic_deviation_score")),
        "geodesicContinuationScore": safe_float(decision.get("geodesic_continuation_score")),
        "geodesicConfidence": safe_float(decision.get("geodesic_confidence")),
        "geodesicProjectedFirstCoordinateX": safe_float(
            decision.get("geodesic_projected_first_coordinate_x")
        ),
        "geodesicProjectedFirstCoordinateY": safe_float(
            decision.get("geodesic_projected_first_coordinate_y")
        ),
        "geodesicProjectedSecondCoordinateX": safe_float(
            decision.get("geodesic_projected_second_coordinate_x")
        ),
        "geodesicProjectedSecondCoordinateY": safe_float(
            decision.get("geodesic_projected_second_coordinate_y")
        ),
        "geodesicProjectedFirstCoordinateDrift": safe_float(
            decision.get("geodesic_projected_first_coordinate_drift")
        ),
        "geodesicProjectedSecondCoordinateDrift": safe_float(
            decision.get("geodesic_projected_second_coordinate_drift")
        ),
        "targetTimestamp": (
            str(decision["target_timestamp"])
            if decision["target_timestamp"] is not None
            else None
        ),
        "targetPrice": (
            float(decision["target_price"])
            if decision["target_price"] is not None
            else None
        ),
        "timingEnabled": bool(decision["timing_enabled"]),
        "timeToBelowCurrent": (
            float(decision["time_to_below_current_seconds"])
            if decision["time_to_below_current_seconds"] is not None
            else None
        ),
        "optimalBuyTimestamp": (
            str(decision.get("optimal_buy_timestamp"))
            if decision.get("optimal_buy_timestamp") is not None
            else None
        ),
        "optimalBuyPrice": safe_float(decision.get("optimal_buy_price")),
        "optimalSellTimestamp": (
            str(decision.get("optimal_sell_timestamp"))
            if decision.get("optimal_sell_timestamp") is not None
            else None
        ),
        "optimalSellPrice": safe_float(decision.get("optimal_sell_price")),
        "cadenceProfile": decision.get("cadence_profile"),
        "cadenceRules": decision.get("cadence_rules") or [],
        "uncertaintySettings": decision.get("uncertainty_settings") or {},
        "runtimeSymbol": decision.get("runtime_symbol"),
        "humanBias": human_bias_snapshot or None,
        "correlationForecast": correlation_forecast or None,
        "tailDiagnostics": tail_diagnostics,
        "championRefresh": decision.get("champion_refresh") or {},
        "perRuleSummary": summarize_per_rule(decision.get("per_rule")),
        "wrapper": serialize_wrapper_result(wrapper_result),
        "recommendation": {
            "shouldBuyWithSol": decision["final_action"] == "BUY",
            "tone": (
                "positive"
                if decision["final_action"] == "BUY"
                else "neutral"
                if decision["final_action"] == "HOLD"
                else "negative"
            ),
            "summary": (
                f"Model suggests buying {requested_symbol} with SOL."
                if decision["final_action"] == "BUY"
                else f"Model suggests waiting before buying {requested_symbol}."
                if decision["final_action"] == "HOLD"
                else f"Model is bearish on {requested_symbol}; avoid buying with SOL right now."
            ),
        },
    }


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/leaderboard/submit")
def submit_leaderboard_score(
    submission: LeaderboardSubmission,
    authorization: Optional[str] = Header(default=None)
) -> dict:
    require_authorization(authorization)
    init_leaderboard_db()
    
    import sqlite3
    with sqlite3.connect(LEADERBOARD_DB_PATH) as conn:
        conn.execute("""
            INSERT INTO prophet_leaderboard (bot_id, symbol, mae, rmse, directional_accuracy, composite_score, cv_folds, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bot_id, symbol) DO UPDATE SET
                mae = excluded.mae,
                rmse = excluded.rmse,
                directional_accuracy = excluded.directional_accuracy,
                composite_score = excluded.composite_score,
                cv_folds = excluded.cv_folds,
                updated_at = excluded.updated_at
        """, (
            submission.bot_id,
            submission.symbol.upper().strip(),
            submission.mae,
            submission.rmse,
            submission.directional_accuracy,
            submission.composite_score,
            submission.cv_folds,
            submission.updated_at
        ))
        conn.commit()
        
    return {"ok": True, "message": f"Successfully registered score for {submission.symbol} by {submission.bot_id}"}


@app.get("/leaderboard")
def get_leaderboard(
    symbol: Optional[str] = None,
    authorization: Optional[str] = Header(default=None)
) -> dict:
    require_authorization(authorization)
    init_leaderboard_db()
    
    import sqlite3
    with sqlite3.connect(LEADERBOARD_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if symbol:
            rows = conn.execute("""
                SELECT bot_id, symbol, mae, rmse, directional_accuracy, composite_score, cv_folds, updated_at
                FROM prophet_leaderboard
                WHERE symbol = ?
                ORDER BY composite_score ASC
            """, (symbol.upper().strip(),)).fetchall()
        else:
            rows = conn.execute("""
                SELECT bot_id, symbol, mae, rmse, directional_accuracy, composite_score, cv_folds, updated_at
                FROM prophet_leaderboard
                ORDER BY symbol ASC, composite_score ASC
            """).fetchall()
            
    leaderboard = []
    for r in rows:
        leaderboard.append({
            "bot_id": r["bot_id"],
            "symbol": r["symbol"],
            "mae": r["mae"],
            "rmse": r["rmse"],
            "directional_accuracy": r["directional_accuracy"],
            "composite_score": r["composite_score"],
            "cv_folds": r["cv_folds"],
            "updated_at": r["updated_at"]
        })
        
    return {"ok": True, "leaderboard": leaderboard}


@app.get("/reinforcement-state")
def reinforcement_state(authorization: Optional[str] = Header(default=None)) -> dict:
    require_authorization(authorization)
    warmed = ensure_reinforcement_warm_start()

    return {
        "ok": True,
        "source": "prediction_api",
        "historicalWarmStart": bool(warmed.get("historicalWarmStart")),
        "warmStartSource": warmed.get("warmStartSource"),
        "investorLens": warmed.get("investorLens") or load_investor_lens_snapshot(),
        "macbookAgent": warmed.get("macbookAgent") or load_macbook_agent_snapshot(),
        "spikeSustainAgent": warmed.get("spikeSustainAgent") or load_spike_sustain_snapshot(),
    }


@app.post("/sp500-map")
def sp500_map(
    payload: Optional[dict] = None,
    authorization: Optional[str] = Header(default=None),
) -> dict:
    require_authorization(authorization)

    payload = payload or {}
    force_refresh = bool(payload.get("forceRefresh"))
    limit = clamp_int(
        payload.get("limit"),
        default=0,
        minimum=0,
        maximum=MAX_SP500_MAP_LIMIT,
    )

    return build_sp500_information_map(
        force_refresh=force_refresh,
        limit=limit,
    )


@app.post("/sp500-portfolio")
def sp500_portfolio(
    payload: Optional[dict] = None,
    authorization: Optional[str] = Header(default=None),
) -> dict:
    require_authorization(authorization)

    payload = payload or {}
    force_refresh = bool(payload.get("forceRefresh"))
    holdings = clamp_int(
        payload.get("holdings"),
        default=10,
        minimum=1,
        maximum=MAX_SP500_PORTFOLIO_HOLDINGS,
    )

    return build_sp500_portfolio(
        force_refresh=force_refresh,
        holdings=holdings,
    )


@app.post("/predict-step")
def predict_step(
    request: PredictionRequest, authorization: Optional[str] = Header(default=None)
) -> dict:
    require_authorization(authorization)

    requested_symbol = normalize_symbol(request.symbol)
    if not requested_symbol:
        return {"supported": False, "reason": "Invalid or unsupported symbol format"}

    try:
        from services.trader import main as trader_main

        MPLCONFIGDIR.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", str(MPLCONFIGDIR))
        os.environ.setdefault("ALLOW_BOOTSTRAP_EXECUTION", "false")
        os.environ.setdefault("EXECUTE_TRADES", "false")
        os.environ.setdefault("WAIT_FOR_TARGET", "false")
        os.environ.setdefault("TARGET_COIN_SYMBOL", requested_symbol)
        human_bias_snapshot = load_symbol_interest_snapshot(
            requested_symbol,
            market_mode=request.marketMode or "sp500",
        )
        if request.trackHumanBias:
            human_bias_snapshot = record_symbol_interest(
                requested_symbol,
                market_mode=request.marketMode or "sp500",
                source=request.humanBiasSource or "predict_step",
            )

        input_symbol, output_symbol = parse_route_symbols(requested_symbol)

        if request.data and len(request.data) >= 100:
            raw_df = pd.DataFrame([row.model_dump() for row in request.data])
            raw_df = trader_main.ensure_raw_df(raw_df)
        elif input_symbol and output_symbol:
            raw_df = build_pair_raw_df(requested_symbol)
            raw_df = trader_main.ensure_raw_df(raw_df)
        else:
            raw_df = trader_main.fetch_fallback_data(
                requested_symbol,
                market_mode=request.marketMode,
            )
            raw_df = trader_main.ensure_raw_df(raw_df)

        last_close_price = safe_float(raw_df["close"].iloc[-1])
        live_snapshot = trader_main.resolve_reference_market_snapshot(
            raw_df,
            requested_symbol,
        )
        live_price = safe_float(live_snapshot.get("price")) or last_close_price

        with contextlib.redirect_stdout(io.StringIO()):
            runtime = trader_main.MultiResolutionRuntime(
                raw_df=raw_df,
                symbol=requested_symbol,
            ).bootstrap()
            decision = runtime.infer()
        tail_diagnostics = build_tail_diagnostics(raw_df)
        decision["tail_long_score"] = safe_float(tail_diagnostics.get("longTailScore"))
        decision["tail_heavy_score"] = safe_float(tail_diagnostics.get("heavyTailScore"))
        decision["tail_left_risk_score"] = safe_float(tail_diagnostics.get("leftTailRiskScore"))
        decision["tail_regime_label"] = tail_diagnostics.get("regimeLabel")
        decision["tail_skewness"] = safe_float(tail_diagnostics.get("skewness"))
        decision["tail_excess_kurtosis"] = safe_float(tail_diagnostics.get("excessKurtosis"))

        correlation_forecast = None
        correlation_moe_gate = None
        if (request.marketMode or "sp500").strip().lower() == "sp500" and not (
            input_symbol and output_symbol
        ):
            correlation_moe_gate = evaluate_correlation_gate(
                requested_symbol,
                raw_df,
                decision,
                tail_diagnostics,
            )
            if correlation_moe_gate.get("run"):
                try:
                    correlation_forecast = build_sp500_symbol_correlation_forecast(
                        requested_symbol
                    )
                    if isinstance(correlation_forecast, dict):
                        correlation_forecast["moeGate"] = correlation_moe_gate
                except Exception as exc:
                    correlation_forecast = {
                        "status": "unavailable",
                        "symbol": requested_symbol,
                        "reason": str(exc),
                        "moeGate": correlation_moe_gate,
                    }
            else:
                correlation_forecast = build_skipped_correlation_forecast(
                    requested_symbol,
                    correlation_moe_gate,
                )

        moe_runtime = build_moe_runtime(
            decision.get("timesfm_moe_gate"),
            correlation_moe_gate,
        )
        decision["moe_runtime"] = moe_runtime

        wrapper_result = None
        wrapper_config = WrapperConfig() if WrapperConfig is not None else None
        if (
            run_wrapper_pipeline is not None
            and WrapperConfig is not None
            and load_wrapper_weights is not None
            and store_wrapper_prediction_snapshot is not None
        ):
            current_price = safe_float(decision.get("current_price")) or float(raw_df["close"].iloc[-1])
            current_timestamp = decision.get("current_timestamp") or raw_df["ds"].iloc[-1]
            try:
                learned_weights, weight_metadata = load_wrapper_weights(
                    requested_symbol,
                    current_price=current_price,
                    current_timestamp=current_timestamp,
                    config=wrapper_config,
                )
            except Exception as exc:
                learned_weights = None
                weight_metadata = {
                    "source": "default",
                    "feedbackCount": 0,
                    "latestFeedback": None,
                    "weightError": str(exc),
                }
            wrapper_result = run_wrapper_pipeline(
                decision=decision,
                execution_context={
                    "slippageBps": 0.0,
                    "priceImpactPct": 0.0,
                    "totalTime": 0.0,
                },
                weight_state=learned_weights,
                config=wrapper_config,
            )
            wrapper_result["wrapper_weight_source"] = weight_metadata.get("source", "default")
            wrapper_result["wrapper_feedback"] = weight_metadata.get("latestFeedback")
            wrapper_result["wrapper_feedback_count"] = weight_metadata.get("feedbackCount", 0)
            if weight_metadata.get("weightError"):
                wrapper_result.setdefault("wrapper_rationale", []).append(
                    f"weight_store_unavailable={weight_metadata['weightError']}"
                )
            try:
                store_wrapper_prediction_snapshot(
                    requested_symbol,
                    reference_price=current_price,
                    reference_timestamp=current_timestamp,
                    wrapper_result=wrapper_result,
                    weights_used=learned_weights or {},
                )
            except Exception as exc:
                wrapper_result.setdefault("wrapper_rationale", []).append(
                    f"weight_snapshot_skipped={exc}"
                )

        return build_result(
            requested_symbol,
            raw_df,
            decision,
            wrapper_result,
            live_price=live_price,
            last_close_price=last_close_price,
            human_bias_snapshot=human_bias_snapshot,
            correlation_forecast=correlation_forecast,
            tail_diagnostics=tail_diagnostics,
            moe_runtime=moe_runtime,
        )
    except Exception as exc:
        return {
            "supported": False,
            "requestedSymbol": requested_symbol,
            "resolvedSymbol": requested_symbol,
            "source": "prediction_api",
            "dataset": None,
            "reason": str(exc),
        }
