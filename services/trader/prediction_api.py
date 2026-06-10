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

from datetime import datetime, timezone
from typing import List, Optional

import pandas as pd
from fastapi import FastAPI, Header, HTTPException, Query, Request, BackgroundTasks
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


# ----------------- Peer Hub (Claude Code plugin user community) -----------------
# Connects plugin users through a shared alpha-signal feed + presence registry.

PEER_HUB_DB_PATH = ROOT_DIR / "services" / "trader" / "model_cache" / "peer_hub.sqlite3"
PEER_ONLINE_WINDOW_MIN = 10  # minutes since last heartbeat to count as online


class PeerRegistration(BaseModel):
    peer_id: str
    nickname: str
    bio: Optional[str] = ""


class AlphaSignal(BaseModel):
    peer_id: str
    nickname: str
    symbol: str
    direction: str  # BUY / SELL / HOLD
    confidence: float  # 0~100
    thesis: Optional[str] = ""


def init_peer_hub_db():
    PEER_HUB_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3
    with sqlite3.connect(PEER_HUB_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS peers (
                peer_id TEXT PRIMARY KEY,
                nickname TEXT NOT NULL,
                bio TEXT,
                registered_at TEXT NOT NULL,
                last_seen TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alpha_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                peer_id TEXT NOT NULL,
                nickname TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                confidence REAL NOT NULL,
                thesis TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_created ON alpha_signals(created_at DESC)")
        conn.commit()


@app.post("/peers/register")
def register_peer(
    reg: PeerRegistration,
    authorization: Optional[str] = Header(default=None)
) -> dict:
    """Register (or heartbeat) a plugin user. Re-posting updates last_seen/nickname."""
    require_authorization(authorization)
    init_peer_hub_db()
    nickname = reg.nickname.strip()[:40]
    if not reg.peer_id.strip() or not nickname:
        raise HTTPException(status_code=400, detail="peer_id and nickname are required")
    now = datetime.now(timezone.utc).isoformat()
    import sqlite3
    with sqlite3.connect(PEER_HUB_DB_PATH) as conn:
        conn.execute("""
            INSERT INTO peers (peer_id, nickname, bio, registered_at, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(peer_id) DO UPDATE SET
                nickname = excluded.nickname,
                bio = excluded.bio,
                last_seen = excluded.last_seen
        """, (reg.peer_id.strip(), nickname, (reg.bio or "")[:200], now, now))
        conn.commit()
    return {"ok": True, "message": f"Peer '{nickname}' registered", "last_seen": now}


@app.get("/peers")
def list_peers(authorization: Optional[str] = Header(default=None)) -> dict:
    """List peers with online presence (last heartbeat within window) and ranking info."""
    require_authorization(authorization)
    init_peer_hub_db()
    init_leaderboard_db()
    import sqlite3
    with sqlite3.connect(PEER_HUB_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT peer_id, nickname, bio, registered_at, last_seen FROM peers ORDER BY last_seen DESC"
        ).fetchall()
    # Pull best composite score per bot from the prophet leaderboard for ranking flair
    best_scores = {}
    with sqlite3.connect(LEADERBOARD_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        for r in conn.execute(
            "SELECT bot_id, MIN(composite_score) AS best FROM prophet_leaderboard GROUP BY bot_id"
        ).fetchall():
            best_scores[r["bot_id"]] = r["best"]
    now = datetime.now(timezone.utc)
    peers = []
    for r in rows:
        try:
            seen = datetime.fromisoformat(r["last_seen"])
            online = (now - seen).total_seconds() <= PEER_ONLINE_WINDOW_MIN * 60
        except Exception:
            online = False
        peers.append({
            "peer_id": r["peer_id"],
            "nickname": r["nickname"],
            "bio": r["bio"],
            "online": online,
            "last_seen": r["last_seen"],
            "best_leaderboard_score": best_scores.get(r["peer_id"]),
        })
    return {"ok": True, "online_window_min": PEER_ONLINE_WINDOW_MIN, "peers": peers}


@app.post("/signals/share")
def share_alpha_signal(
    sig: AlphaSignal,
    authorization: Optional[str] = Header(default=None)
) -> dict:
    """Share an alpha signal with all connected plugin users."""
    require_authorization(authorization)
    init_peer_hub_db()
    direction = sig.direction.strip().upper()
    if direction not in ("BUY", "SELL", "HOLD"):
        raise HTTPException(status_code=400, detail="direction must be BUY, SELL, or HOLD")
    confidence = max(0.0, min(float(sig.confidence), 100.0))
    now = datetime.now(timezone.utc).isoformat()
    import sqlite3
    with sqlite3.connect(PEER_HUB_DB_PATH) as conn:
        conn.execute("""
            INSERT INTO alpha_signals (peer_id, nickname, symbol, direction, confidence, thesis, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (sig.peer_id.strip(), sig.nickname.strip()[:40],
              normalize_symbol(sig.symbol), direction, confidence,
              (sig.thesis or "")[:500], now))
        # touch presence
        conn.execute("UPDATE peers SET last_seen = ? WHERE peer_id = ?", (now, sig.peer_id.strip()))
        conn.commit()
    return {"ok": True, "message": f"Signal {direction} {normalize_symbol(sig.symbol)} shared", "created_at": now}


@app.get("/signals")
def get_alpha_signals(
    symbol: Optional[str] = None,
    limit: int = 20,
    authorization: Optional[str] = Header(default=None)
) -> dict:
    """Read the shared alpha-signal feed (optionally filtered by symbol)."""
    require_authorization(authorization)
    init_peer_hub_db()
    limit = clamp_int(limit, default=20, minimum=1, maximum=100)
    import sqlite3
    with sqlite3.connect(PEER_HUB_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if symbol:
            rows = conn.execute("""
                SELECT nickname, symbol, direction, confidence, thesis, created_at
                FROM alpha_signals WHERE symbol = ?
                ORDER BY created_at DESC LIMIT ?
            """, (normalize_symbol(symbol), limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT nickname, symbol, direction, confidence, thesis, created_at
                FROM alpha_signals ORDER BY created_at DESC LIMIT ?
            """, (limit,)).fetchall()
    signals = [dict(r) for r in rows]
    # Consensus summary per symbol across the returned window
    consensus: dict = {}
    for s in signals:
        c = consensus.setdefault(s["symbol"], {"BUY": 0, "SELL": 0, "HOLD": 0})
        c[s["direction"]] = c.get(s["direction"], 0) + 1
    return {"ok": True, "signals": signals, "consensus": consensus}


# ----------------- Personalized / Zero-shot Forecast Service -----------------
# Domain-agnostic time-series SaaS: finance, semiconductor process (yield/SPC),
# quantum error data (Stim logical error rates), or any generic series.

def _pfs():
    try:
        from services.trader import personal_forecast_service as m
    except ImportError:
        import personal_forecast_service as m
    return m


class DatasetUpload(BaseModel):
    user_id: str
    name: str
    domain: str = "generic"  # finance | semiconductor | quantum | generic
    description: Optional[str] = ""
    rows: List[dict]         # [{"ds": "...", "y": ...}] — ds may be date/round/shot


class TrainRequest(BaseModel):
    user_id: str
    name: str


class ZeroShotRequest(BaseModel):
    domain: str = "generic"
    days: int = 30
    title: Optional[str] = "zero-shot"
    rows: List[dict]


@app.post("/personal/datasets")
def upload_personal_dataset(
    req: DatasetUpload,
    authorization: Optional[str] = Header(default=None)
) -> dict:
    """Register a company/individual dataset for personalized model training."""
    require_authorization(authorization)
    try:
        meta = _pfs().register_dataset(req.user_id, req.name, rows=req.rows,
                                       domain=req.domain, description=req.description or "")
        return {"ok": True, "dataset": meta}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/personal/train")
def train_personal_dataset(
    req: TrainRequest,
    authorization: Optional[str] = Header(default=None)
) -> dict:
    """Hyperparameter-tuned Prophet training on the registered dataset."""
    require_authorization(authorization)
    try:
        meta = _pfs().train_personal_model(req.user_id, req.name)
        return {"ok": True, "model": meta}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/personal/forecast")
def get_personal_forecast_api(
    user_id: str,
    name: str,
    days: int = 30,
    authorization: Optional[str] = Header(default=None)
) -> dict:
    """Serve a forecast from the user's stored personalized model (+anomalies)."""
    require_authorization(authorization)
    try:
        result = _pfs().personal_forecast(user_id, name, days=clamp_int(days, default=30, minimum=1, maximum=365),
                                          with_chart=False)
        return {"ok": True, "forecast": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/personal/zero-shot")
def zero_shot_forecast_api(
    req: ZeroShotRequest,
    authorization: Optional[str] = Header(default=None)
) -> dict:
    """Instant forecast on arbitrary uploaded rows — no stored state."""
    require_authorization(authorization)
    try:
        result = _pfs().zero_shot_forecast(rows=req.rows, domain=req.domain,
                                           days=req.days, with_chart=False,
                                           title=req.title or "zero-shot")
        return {"ok": True, "forecast": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/personal/datasets")
def list_personal_datasets_api(
    user_id: str,
    authorization: Optional[str] = Header(default=None)
) -> dict:
    require_authorization(authorization)
    return {"ok": True, "datasets": _pfs().list_datasets(user_id)}


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


# ----------------- Instagram Direct Message (DM) Chatbot Integration -----------------

def clean_html_tags(text: str) -> str:
    """Removes HTML tags from a string to make it plain text."""
    if not text:
        return ""
    # Strip basic tags
    clean = re.sub(r"<[^>]+>", "", text)
    return clean


def send_instagram_dm(recipient_id: str, text: str):
    """Sends a direct message to a user on Instagram via Graph API."""
    import requests
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    if not access_token:
        print("⚠️ INSTAGRAM_ACCESS_TOKEN is missing. Cannot send DM response.")
        return
        
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={access_token}"
    
    # Instagram DM character limit is 1000 characters. We chunk it to 900.
    chunks = [text[i:i+900] for i in range(0, len(text), 900)]
    
    for chunk in chunks:
        payload = {
            "recipient": {
                "id": recipient_id
            },
            "message": {
                "text": chunk
            }
        }
        try:
            res = requests.post(url, json=payload, timeout=15)
            res.raise_for_status()
            print(f"✅ Sent Instagram message chunk to recipient {recipient_id}")
        except Exception as e:
            print(f"❌ Failed to send Instagram DM to {recipient_id}: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"Response: {e.response.text}")


def process_bot_query(user_id: str, text: str) -> str:
    """Processes a user message by mimicking the telegram interactive bot commands."""
    import time
    import json
    text = text.strip()
    
    # Import necessary functions from telegram_interactive_bot safely
    try:
        import services.trader.telegram_interactive_bot as tib
    except ImportError as e:
        print(f"❌ Failed to import telegram_interactive_bot: {e}")
        return "⚠️ 시스템 연동 에러가 발생했습니다. 잠시 후 다시 시도해 주세요."
    
    # Help / Info
    if text in ["/help", "help", "도움말", "도움"]:
        return (
            "🤖 No Slip AI 인스타그램 봇 도움말\n"
            "==============================\n"
            "아래 명령어들을 통해 AI 분석 및 전략 정보를 실시간으로 조회하실 수 있습니다:\n\n"
            "💼 /포트폴리오 : AI 추천 포트폴리오 요약 조회\n"
            "🏆 /챔피언 : S&P500 챔피언 AI 모델 분석 요약 조회\n"
            "🧭 /섹터 : GICS 섹터 상관관계 및 추천 섹터 조회\n"
            "📊 /정보맵 : S&P500 정보맵 4분면 종목 통계 조회\n"
            "🌀 /오빗 : 섹터 궤적(Orbit) 분석 요약 조회\n"
            "📺 /오선 : 오선 유튜브 시황 요약 리포트 조회\n"
            "🤖 /조언 : 연합 RL 에이전트들의 시장 분석 조언\n"
            "🔍 /분석 [종목명] : 개별 주식(예: AAPL) 또는 가상자산(예: BTC) AI 분석\n"
            "   (예: /분석 AAPL 또는 /분석 BTC)\n"
            "📈 /예측 [종목명] : 개별 자산의 30일 가격 예측 (Prophet)\n"
            "   (예: /예측 AAPL 또는 /예측 BTC-USD)\n"
            "💬 /토론 [종목명] : 개별 종목의 AI 위원회 토론방 생성\n"
            "   (예: /토론 NVDA)\n"
            "=============================="
        )
        
    # Portfolio Summary
    if tib.parse_portfolio_request(text):
        try:
            return clean_html_tags(tib.execute_portfolio_summary())
        except Exception as e:
            return f"⚠️ 포트폴리오 조회 중 오류가 발생했습니다: {e}"
            
    # Champion Summary
    if tib.parse_champion_request(text):
        try:
            return clean_html_tags(tib.execute_champion_summary())
        except Exception as e:
            return f"⚠️ 챔피언 모델 조회 중 오류가 발생했습니다: {e}"
            
    # Sector Recommendation
    if tib.parse_sector_request(text):
        try:
            from services.trader.sector_correlation import build_sector_report
            return clean_html_tags(build_sector_report())
        except Exception as e:
            return f"⚠️ 섹터 추천 산출 중 오류가 발생했습니다: {e}"
            
    # Information Map stats
    if tib.parse_infomap_request(text):
        try:
            latest_json_path = ROOT_DIR / "services" / "trader" / "model_cache" / "sp500_information_maps" / "latest.json"
            if latest_json_path.exists():
                with open(latest_json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                points = data.get("points", [])
                total_symbols = len(points)
                
                quadrant_counts = {
                    "breakout acceleration": 0,
                    "uptrend cooling": 0,
                    "recovery setup": 0,
                    "selloff acceleration": 0
                }
                for p in points:
                    q = p.get("quadrant")
                    if q in quadrant_counts:
                        quadrant_counts[q] += 1
                        
                return (
                    f"📊 S&P 500 Information Map ({data.get('mapDate', 'N/A')})\n"
                    f"==============================\n"
                    f"총 분석 종목 수: {total_symbols}개\n\n"
                    f"🟢 우상향 가속 (Breakout Accel): {quadrant_counts['breakout acceleration']}개\n"
                    f"🟣 회복 국면 (Recovery Setup): {quadrant_counts['recovery setup']}개\n"
                    f"🟡 상승 둔화 (Uptrend Cooling): {quadrant_counts['uptrend cooling']}개\n"
                    f"🔴 하락 가속 (Selloff Accel): {quadrant_counts['selloff acceleration']}개\n"
                    f"=============================="
                )
            else:
                return "⚠️ 정보맵 데이터를 찾을 수 없습니다."
        except Exception as e:
            return f"⚠️ 정보맵 조회 중 오류가 발생했습니다: {e}"
            
    # Sector Orbit
    if tib.parse_orbit_request(text):
        try:
            from services.trader.sector_orbit_learner import run_pipeline
            ranked, report = run_pipeline()
            return clean_html_tags(report)
        except Exception as e:
            return f"⚠️ GICS 오빗 분석 중 오류가 발생했습니다: {e}"
            
    # Oh-seon Summary
    if tib.parse_ohseon_request(text):
        try:
            from services.trader.ohseon_summary import run_ohseon_summary_pipeline
            return clean_html_tags(run_ohseon_summary_pipeline())
        except Exception as e:
            return f"⚠️ 시황 요약 생성 중 오류가 발생했습니다: {e}"
            
    # Agent Advice
    if tib.parse_advice_request(text):
        try:
            from services.trader.federated_rl_agent import FederatedRLAgent
            agent = FederatedRLAgent()
            return clean_html_tags(agent.get_agents_advice())
        except Exception as e:
            return f"⚠️ 에이전트 조언 생성 중 오류가 발생했습니다: {e}"
            
    # Gemini Chat
    gemini_query = tib.parse_gemini_request(text)
    if gemini_query:
        try:
            return clean_html_tags(tib.execute_gemini_chat(f"ig_{user_id}", gemini_query))
        except Exception as e:
            return f"⚠️ 제미나이 처리 중 오류가 발생했습니다: {e}"
            
    # Debate Initiation
    debate_query = tib.parse_debate_request(text)
    if debate_query:
        symbol = tib.normalize_symbol(debate_query)
        if not symbol:
            return f"⚠️ 올바르지 않은 심볼명입니다: {debate_query}"
        try:
            debate_intro = clean_html_tags(tib.execute_debate_initiation(symbol))
            state = tib.load_debate_state()
            state[f"ig_{user_id}"] = {
                "symbol": symbol,
                "timestamp": time.time()
            }
            tib.save_debate_state(state)
            return debate_intro + "\n\n💡 AI 에이전트들과 토론을 이어가시려면 메시지를 바로 입력해 주세요."
        except Exception as e:
            return f"⚠️ {symbol} 토론방 개설 중 오류가 발생했습니다: {e}"
            
    # Prophet Forecast (Instagram DM Text Version)
    prophet_query = tib.parse_prophet_request(text)
    if prophet_query is not None:
        if not prophet_query:
            return (
                "📊 [No Slip AI Prophet 30일 가격 예측]\n"
                "사용법: /예측 [종목명]\n"
                "(예: /예측 AAPL 또는 /예측 BTC-USD)"
            )
        symbol = tib.normalize_symbol(prophet_query)
        if not symbol:
            return f"⚠️ 올바르지 않은 심볼명입니다: {prophet_query}"
        try:
            from prophet import Prophet
            df = tib.fetch_ticker_data(symbol)
            if df.empty or len(df) < 30:
                return f"⚠️ {symbol} 종목의 데이터를 찾을 수 없거나 데이터 수가 너무 적습니다."
                
            df_p = pd.DataFrame()
            df_p['ds'] = df.index.tz_localize(None)
            df_p['y'] = df['Close'].values
            
            m = Prophet(changepoint_prior_scale=0.05, yearly_seasonality=False, weekly_seasonality=True, daily_seasonality=False)
            m.fit(df_p.tail(365))
            
            future = m.make_future_dataframe(periods=30, freq='D')
            forecast = m.predict(future)
            
            forecast_future = forecast[forecast['ds'] > df_p['ds'].max()]
            cur_price = df_p['y'].iloc[-1]
            proj_price = forecast_future['yhat'].iloc[-1]
            ret_pct = ((proj_price - cur_price) / cur_price) * 100
            lower = forecast_future['yhat_lower'].iloc[-1]
            upper = forecast_future['yhat_upper'].iloc[-1]
            
            return (
                f"📈 [No Slip AI] {symbol} Prophet 30일 예측 결과\n"
                f"==============================\n"
                f"• 현재 가격: ${cur_price:.2f}\n"
                f"• 30일 뒤 예측가: ${proj_price:.2f} ({ret_pct:+.2f}%)\n"
                f"• 예측 신뢰구간 (80%): ${lower:.2f} ~ ${upper:.2f}\n"
                f"==============================\n"
                f"※ 본 예측은 Facebook Prophet 시계열 분석 결과이며 투자 참고용입니다."
            )
        except Exception as e:
            return f"⚠️ {symbol} 예측 중 오류가 발생했습니다: {e}"
            
    # Standard Analysis
    analysis_query = tib.parse_analysis_request(text)
    if analysis_query:
        symbol = tib.normalize_symbol(analysis_query)
        if not symbol:
            return f"⚠️ 올바르지 않은 심볼명입니다: {analysis_query}"
        try:
            return clean_html_tags(tib.execute_analysis(symbol))
        except Exception as e:
            return f"⚠️ {symbol} 분석 중 오류가 발생했습니다: {e}"
            
    # Active debate replies (if user is in a debate session)
    state = tib.load_debate_state()
    chat_state = state.get(f"ig_{user_id}")
    if chat_state and (time.time() - chat_state["timestamp"] < 7200):
        symbol = chat_state["symbol"]
        try:
            reply_msg = clean_html_tags(tib.generate_agent_replies(symbol, text))
            chat_state["timestamp"] = time.time()
            tib.save_debate_state(state)
            return reply_msg
        except Exception as e:
            return f"⚠️ 토론 답변 처리 중 오류가 발생했습니다: {e}"
            
    # Default: Fallback to Gemini general chat if available
    api_key = os.getenv("GEMINI_API_KEY")
    if api_key and tib.HAS_GEMINI:
        try:
            return clean_html_tags(tib.execute_gemini_chat(f"ig_{user_id}", text))
        except Exception:
            pass
            
    # Ultimate rule-based fallback
    return (
        f"안녕하세요! No Slip AI 인스타그램 봇입니다. 🤖\n\n"
        f"입력하신 내용('{text}')에 대해 답변하기 어렵습니다. 전체 명령어 목록을 확인하려면 '/help'를 입력해주세요!"
    )


def handle_instagram_webhook_event(payload: dict):
    """Parses incoming Instagram webhook message payloads and triggers a response."""
    if payload.get("object") != "instagram":
        return
        
    for entry in payload.get("entry", []):
        for messaging_event in entry.get("messaging", []):
            sender_id = messaging_event.get("sender", {}).get("id")
            message = messaging_event.get("message", {})
            text = message.get("text")
            
            if not sender_id or not text:
                continue
                
            # Discard messages sent by the bot itself (preventing infinite loops)
            is_echo = message.get("is_echo", False)
            if is_echo:
                continue
                
            print(f"💬 Received Instagram DM from sender_id {sender_id}: {text}")
            
            # Process query and get reply
            reply_text = process_bot_query(sender_id, text)
            
            # Send DM response
            send_instagram_dm(sender_id, reply_text)


@app.get("/instagram/webhook")
def verify_instagram_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token")
):
    """Webhook verification endpoint called by Facebook Developer Settings."""
    from fastapi.responses import PlainTextResponse
    verify_token = os.getenv("INSTAGRAM_VERIFY_TOKEN", "noslip_verify_token_default")
    if hub_mode == "subscribe" and hub_verify_token == verify_token:
        print("Hub challenge verified successfully.")
        return PlainTextResponse(content=hub_challenge)
    else:
        print("Hub challenge verification failed.")
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Verification token mismatch")


@app.post("/instagram/webhook")
async def receive_instagram_webhook(request: Request, background_tasks: BackgroundTasks) -> dict:
    """Receives Instagram DMs and delegates processing to a background task."""
    try:
        payload = await request.json()
    except Exception as e:
        print(f"⚠️ Error parsing webhook json: {e}")
        return {"ok": False, "error": "Invalid JSON"}
        
    # Queue processing to background task to respond 200 OK immediately
    background_tasks.add_task(handle_instagram_webhook_event, payload)
    return {"ok": True}
