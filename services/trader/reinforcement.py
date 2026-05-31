from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from services.trader.map_store import MAP_HISTORY_DB_PATH, MODEL_CACHE_DIR, today_market_date
from services.trader.portfolio_manifold import PORTFOLIO_HISTORY_DB_PATH

REINFORCEMENT_DB_PATH = MODEL_CACHE_DIR / "reinforcement_learning.sqlite3"
INVESTOR_LENS_STATE_PATH = MODEL_CACHE_DIR / "investor_lens_state.json"
MACBOOK_AGENT_STATE_PATH = MODEL_CACHE_DIR / "macbook_agent_state.json"
SPIKE_SUSTAIN_STATE_PATH = MODEL_CACHE_DIR / "spike_sustain_state.json"
LENS_NAMES: Tuple[str, ...] = ("buffett", "druckenmiller", "lynch", "dalio")
DEFAULT_LENS_WEIGHTS = {lens: 1.0 for lens in LENS_NAMES}
DEFAULT_CHAMPION_PREFERRED_CPS = 0.03
SPIKE_MODEL_NAMES: Tuple[str, ...] = ("prophet", "timesfm")
MIN_LENS_WEIGHT = 0.35
MAX_LENS_WEIGHT = 3.5
LENS_LEARNING_RATE = 0.18
CHAMPION_REWARD_LEARNING_RATE = 0.22
SPIKE_MODEL_LEARNING_RATE = 0.22
MACBOOK_AGENT_NAME = "Macbook"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper().replace(".", "-")


def _safe_float(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _ensure_table_columns(
    conn: sqlite3.Connection,
    table: str,
    columns: Dict[str, str],
) -> None:
    existing = {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for column_name, column_spec in columns.items():
        if column_name in existing:
            continue
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {column_name} {column_spec}"
        )


def ensure_reinforcement_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS reinforcement_runs (
            run_date TEXT PRIMARY KEY,
            processed_events INTEGER NOT NULL DEFAULT 0,
            updated_symbols INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS daily_reward_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            reference_date TEXT NOT NULL,
            realized_date TEXT NOT NULL,
            reference_price REAL NOT NULL,
            realized_price REAL NOT NULL,
            realized_return_pct REAL NOT NULL,
            predicted_action TEXT NOT NULL,
            direction_score REAL,
            uncertainty_ratio REAL,
            persistence_score REAL,
            regime_shift_risk REAL,
            champion_reward REAL NOT NULL,
            lens_rewards_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(symbol, reference_date, realized_date)
        );
        CREATE INDEX IF NOT EXISTS idx_daily_reward_events_symbol
            ON daily_reward_events(symbol, reference_date DESC);

        CREATE TABLE IF NOT EXISTS investor_lens_states (
            lens TEXT PRIMARY KEY,
            weight REAL NOT NULL,
            avg_reward REAL NOT NULL DEFAULT 0,
            reward_count INTEGER NOT NULL DEFAULT 0,
            last_reward REAL,
            last_updated_date TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS champion_reinforcement_states (
            symbol TEXT NOT NULL,
            task TEXT NOT NULL,
            rule TEXT NOT NULL,
            preferred_changepoint_scale REAL NOT NULL,
            avg_reward REAL NOT NULL DEFAULT 0,
            reward_count INTEGER NOT NULL DEFAULT 0,
            last_reward REAL,
            last_reference_date TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(symbol, task, rule)
        );

        CREATE TABLE IF NOT EXISTS daily_portfolio_feedback_events (
            reference_date TEXT PRIMARY KEY,
            realized_date TEXT NOT NULL,
            reference_payload_path TEXT NOT NULL,
            champion_profile TEXT,
            holdings_count INTEGER NOT NULL DEFAULT 0,
            coverage_ratio REAL NOT NULL DEFAULT 0,
            realized_return_pct REAL NOT NULL,
            predicted_upside_pct REAL,
            weighted_uncertainty_pct REAL,
            weighted_drawdown_linger_days REAL,
            continuity_score REAL,
            target_distance REAL,
            reward REAL NOT NULL,
            hit INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS macbook_agent_states (
            agent TEXT PRIMARY KEY,
            weight REAL NOT NULL,
            avg_reward REAL NOT NULL DEFAULT 0,
            reward_count INTEGER NOT NULL DEFAULT 0,
            hit_count INTEGER NOT NULL DEFAULT 0,
            hit_rate REAL NOT NULL DEFAULT 0,
            last_reward REAL,
            last_reference_date TEXT,
            last_realized_date TEXT,
            last_realized_return_pct REAL,
            last_coverage_ratio REAL,
            champion_avg_reward REAL,
            champion_alignment_score REAL,
            champion_reward_count REAL,
            champion_preferred_cps REAL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS spike_sustain_prediction_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            reference_date TEXT NOT NULL,
            reference_timestamp TEXT,
            reference_price REAL NOT NULL,
            predicted_spike_sustain_seconds REAL,
            predicted_peak_to_fade_seconds REAL,
            predicted_max_spike_pct REAL,
            predicted_spike_fade_in_horizon INTEGER,
            timesfm_predicted_spike_sustain_seconds REAL,
            timesfm_predicted_peak_to_fade_seconds REAL,
            timesfm_predicted_max_spike_pct REAL,
            timesfm_predicted_spike_fade_in_horizon INTEGER,
            realized_spike_sustain_seconds REAL,
            realized_peak_to_fade_seconds REAL,
            realized_max_spike_pct REAL,
            realized_spike_fade_in_horizon INTEGER,
            prophet_reward REAL,
            timesfm_reward REAL,
            resolved_at TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(symbol, reference_date)
        );
        CREATE INDEX IF NOT EXISTS idx_spike_sustain_prediction_events_symbol
            ON spike_sustain_prediction_events(symbol, reference_date DESC);

        CREATE TABLE IF NOT EXISTS spike_sustain_model_states (
            model TEXT PRIMARY KEY,
            weight REAL NOT NULL,
            avg_reward REAL NOT NULL DEFAULT 0,
            reward_count INTEGER NOT NULL DEFAULT 0,
            hit_count INTEGER NOT NULL DEFAULT 0,
            hit_rate REAL NOT NULL DEFAULT 0,
            last_reward REAL,
            last_reference_date TEXT,
            last_realized_date TEXT,
            last_realized_spike_sustain_seconds REAL,
            last_realized_max_spike_pct REAL,
            updated_at TEXT NOT NULL
        );
        """
    )
    _ensure_table_columns(
        conn,
        "macbook_agent_states",
        {
            "champion_avg_reward": "REAL",
            "champion_alignment_score": "REAL",
            "champion_reward_count": "REAL",
            "champion_preferred_cps": "REAL",
        },
    )


def _load_symbol_rows() -> Dict[str, List[Dict[str, Any]]]:
    if not MAP_HISTORY_DB_PATH.exists():
        return {}

    with sqlite3.connect(MAP_HISTORY_DB_PATH) as map_conn:
        rows = map_conn.execute(
            """
            SELECT
                symbol,
                map_date,
                current_price,
                final_action,
                direction_score,
                uncertainty_ratio,
                first_moment_pct_per_day,
                second_moment_bp_per_day2,
                optimization_score,
                persistence_score,
                stability_score,
                regime_shift_risk,
                first_velocity_pct_per_day,
                second_velocity_bp_per_day2_per_day
            FROM map_symbol_snapshots
            WHERE current_price IS NOT NULL
            ORDER BY symbol ASC, map_date ASC
            """
        ).fetchall()

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        symbol = normalize_symbol(row[0])
        grouped.setdefault(symbol, []).append(
            {
                "symbol": symbol,
                "map_date": row[1],
                "current_price": _safe_float(row[2]),
                "final_action": str(row[3] or "HOLD").upper(),
                "direction_score": _safe_float(row[4]),
                "uncertainty_ratio": _safe_float(row[5]),
                "first_moment_pct_per_day": _safe_float(row[6]),
                "second_moment_bp_per_day2": _safe_float(row[7]),
                "optimization_score": _safe_float(row[8]),
                "persistence_score": _safe_float(row[9]),
                "stability_score": _safe_float(row[10]),
                "regime_shift_risk": _safe_float(row[11]),
                "first_velocity_pct_per_day": _safe_float(row[12]),
                "second_velocity_bp_per_day2_per_day": _safe_float(row[13]),
            }
        )
    return grouped


def _load_portfolio_runs() -> List[Dict[str, Any]]:
    if not PORTFOLIO_HISTORY_DB_PATH.exists():
        return []

    with sqlite3.connect(PORTFOLIO_HISTORY_DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT map_date, generated_at, payload_path, profile_name, champion_score
            FROM portfolio_runs
            ORDER BY map_date ASC
            """
        ).fetchall()

    history: List[Dict[str, Any]] = []
    for row in rows:
        history.append(
            {
                "mapDate": row[0],
                "generatedAt": row[1],
                "payloadPath": row[2],
                "profileName": row[3],
                "championScore": _safe_float(row[4]),
            }
        )
    return history


def _load_portfolio_payload(payload_path: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(Path(payload_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _next_trading_date(close_matrix: pd.DataFrame, reference_date: str) -> Optional[str]:
    if "ds" not in close_matrix.columns or not reference_date:
        return None

    available_dates = sorted({str(value)[:10] for value in close_matrix["ds"].dropna().tolist()})
    for candidate in available_dates:
        if candidate > reference_date:
            return candidate
    return None


def _normalize_price_history(raw_df: pd.DataFrame) -> pd.DataFrame:
    if raw_df is None or raw_df.empty or "ds" not in raw_df.columns:
        return pd.DataFrame(columns=["ds", "close"])
    ordered = raw_df[["ds", "close"]].copy()
    ordered["ds"] = pd.to_datetime(ordered["ds"], errors="coerce")
    ordered["close"] = pd.to_numeric(ordered["close"], errors="coerce")
    return ordered.dropna(subset=["ds", "close"]).sort_values("ds").reset_index(drop=True)


def _compute_realized_spike_profile(
    raw_df: pd.DataFrame,
    *,
    reference_price: float,
) -> Dict[str, Any]:
    default = {
        "spike_start_timestamp": None,
        "spike_peak_timestamp": None,
        "spike_peak_price": None,
        "spike_sustain_seconds": 0.0,
        "spike_fade_timestamp": None,
        "spike_fade_in_horizon": True,
        "peak_to_fade_seconds": 0.0,
        "max_spike_pct": 0.0,
    }
    if reference_price <= 0:
        return default

    ordered = _normalize_price_history(raw_df)
    if len(ordered) < 2:
        return default

    above_df = ordered[ordered["close"] > float(reference_price)].copy()
    if above_df.empty:
        return default

    spike_start_ts = pd.Timestamp(above_df.iloc[0]["ds"])
    spike_start_idx = int(above_df.index[0])
    post_spike = ordered.iloc[spike_start_idx:].copy()
    if post_spike.empty:
        return default

    peak_idx = int(post_spike["close"].idxmax())
    peak_row = ordered.loc[peak_idx]
    spike_peak_ts = pd.Timestamp(peak_row["ds"])
    spike_peak_price = _safe_float(peak_row["close"])
    if spike_peak_price is None or spike_peak_price <= float(reference_price):
        return default

    amplitude = spike_peak_price - float(reference_price)
    fade_threshold = float(reference_price) + amplitude * 0.4
    fade_candidates = ordered[
        (ordered.index > peak_idx) & (ordered["close"] <= fade_threshold)
    ].copy()
    fade_ts = pd.Timestamp(fade_candidates.iloc[0]["ds"]) if not fade_candidates.empty else None

    horizon_end_ts = pd.Timestamp(ordered.iloc[-1]["ds"])
    sustain_end_ts = fade_ts or horizon_end_ts
    spike_sustain_seconds = safe_duration_seconds(spike_start_ts, sustain_end_ts) or 0.0
    peak_to_fade_seconds = safe_duration_seconds(spike_peak_ts, sustain_end_ts) or 0.0
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


def _compute_spike_prediction_reward(
    *,
    predicted_sustain_seconds: Optional[float],
    predicted_peak_to_fade_seconds: Optional[float],
    predicted_max_spike_pct: Optional[float],
    predicted_spike_fade_in_horizon: Optional[bool],
    realized_profile: Dict[str, Any],
) -> Tuple[float, bool]:
    realized_sustain = _safe_float(realized_profile.get("spike_sustain_seconds")) or 0.0
    realized_peak_to_fade = _safe_float(realized_profile.get("peak_to_fade_seconds")) or 0.0
    realized_max_spike = max(0.0, _safe_float(realized_profile.get("max_spike_pct")) or 0.0)
    realized_fade_in_horizon = realized_profile.get("spike_fade_in_horizon")

    predicted_sustain = max(0.0, predicted_sustain_seconds or 0.0)
    predicted_peak_to_fade = max(0.0, predicted_peak_to_fade_seconds or 0.0)
    predicted_max_spike = max(0.0, predicted_max_spike_pct or 0.0)

    sustain_scale = max(realized_sustain, 86_400.0)
    fade_scale = max(realized_peak_to_fade, 86_400.0)
    amplitude_scale = max(realized_max_spike, 0.01)

    sustain_error = abs(predicted_sustain - realized_sustain) / sustain_scale
    fade_error = abs(predicted_peak_to_fade - realized_peak_to_fade) / fade_scale
    amplitude_error = abs(predicted_max_spike - realized_max_spike) / amplitude_scale

    fade_match = (
        predicted_spike_fade_in_horizon is not None
        and realized_fade_in_horizon is not None
        and bool(predicted_spike_fade_in_horizon) == bool(realized_fade_in_horizon)
    )

    reward = (
        1.0
        - min(1.6, sustain_error) * 0.45
        - min(1.6, fade_error) * 0.20
        - min(1.6, amplitude_error) * 0.35
        + (0.15 if fade_match else (-0.12 if predicted_spike_fade_in_horizon is not None else 0.0))
    )
    reward = float(max(-1.0, min(1.0, reward)))
    hit = sustain_error <= 0.55 and amplitude_error <= 0.55 and (
        predicted_spike_fade_in_horizon is None or fade_match
    )
    return reward, hit


def _portfolio_reference_price(holding: Dict[str, Any]) -> Optional[float]:
    for key in ("lastClosePrice", "currentPrice", "livePrice"):
        value = _safe_float(holding.get(key))
        if value is not None and value > 0:
            return value
    return None


def _compute_virtual_portfolio_feedback(
    payload: Dict[str, Any],
    close_matrix: pd.DataFrame,
    realized_date: str,
) -> Optional[Dict[str, Any]]:
    holdings = payload.get("holdings") or []
    if not holdings or "ds" not in close_matrix.columns:
        return None

    realized_rows = close_matrix.loc[close_matrix["ds"].astype(str).str[:10] == realized_date]
    if realized_rows.empty:
        return None

    realized_row = realized_rows.iloc[-1]
    weighted_sum = 0.0
    used_weight = 0.0
    holdings_count = 0

    for holding in holdings:
        symbol = normalize_symbol(str(holding.get("symbol") or ""))
        if not symbol or symbol not in close_matrix.columns:
            continue

        reference_price = _portfolio_reference_price(holding)
        realized_price = _safe_float(realized_row.get(symbol))
        portfolio_weight = (
            _safe_float(holding.get("portfolioWeightPct"))
            or _safe_float(holding.get("weightPct"))
            or ((_safe_float(holding.get("weight")) or 0.0) * 100.0)
        )
        if not reference_price or not realized_price or reference_price <= 0 or not portfolio_weight:
            continue

        weighted_sum += (portfolio_weight / 100.0) * (realized_price / reference_price - 1.0)
        used_weight += portfolio_weight / 100.0
        holdings_count += 1

    if used_weight <= 0:
        return None

    normalized_return = weighted_sum / used_weight
    summary = payload.get("summary") or {}
    champion = payload.get("championAgent") or {}
    manifold = payload.get("manifold") or {}
    hit = normalized_return > 0
    predicted_upside = _safe_float(summary.get("weightedUpsidePct")) or 0.0
    uncertainty = _safe_float(summary.get("weightedUncertaintyPct")) or 0.0
    linger = _safe_float(summary.get("weightedDrawdownLingerDays")) or 0.0
    champion_avg_reward = _safe_float(summary.get("weightedChampionProphetAvgReward")) or 0.0
    champion_alignment = _safe_float(summary.get("weightedChampionProphetAlignmentScore")) or 0.0
    champion_reward_count = _safe_float(summary.get("weightedChampionProphetRewardCount")) or 0.0
    champion_preferred_cps = (
        _safe_float(summary.get("weightedChampionProphetPreferredCps"))
        or DEFAULT_CHAMPION_PREFERRED_CPS
    )
    continuity = _safe_float(champion.get("continuityScore"))
    if continuity is None:
        continuity = _safe_float(manifold.get("continuityScore")) or 0.0
    target_distance = _safe_float(champion.get("targetDistance"))
    if target_distance is None:
        target_distance = _safe_float(manifold.get("targetDistance")) or 0.0

    reward = (
        math.tanh(normalized_return / 0.02)
        + max(-0.15, min(0.15, predicted_upside * 0.6))
        + continuity * 0.2
        + champion_avg_reward * 0.55
        + champion_alignment * 0.35
        + min(0.08, champion_reward_count * 0.0025)
        - uncertainty * 1.4
        - linger * 0.025
        - target_distance * 0.08
        - abs(champion_preferred_cps - DEFAULT_CHAMPION_PREFERRED_CPS) * 0.25
    )
    reward = float(max(-1.5, min(1.5, reward)))

    return {
        "realizedReturnPct": normalized_return,
        "coverageRatio": float(max(0.0, min(1.0, used_weight))),
        "holdingsCount": holdings_count,
        "predictedUpsidePct": predicted_upside,
        "weightedUncertaintyPct": uncertainty,
        "weightedDrawdownLingerDays": linger,
        "continuityScore": continuity,
        "targetDistance": target_distance,
        "championAvgReward": champion_avg_reward,
        "championAlignmentScore": champion_alignment,
        "championRewardCount": champion_reward_count,
        "championPreferredCps": champion_preferred_cps,
        "reward": reward,
        "hit": hit,
        "championProfile": champion.get("selectedProfile") or payload.get("profile"),
    }


def _action_reward(realized_return_pct: float, action: str) -> float:
    scaled = math.tanh(realized_return_pct / 0.03)
    action = (action or "HOLD").upper()
    if action == "BUY":
        return scaled
    if action == "SELL":
        return -scaled
    return 0.25 - abs(scaled)


def _infer_lens_action(snapshot: Dict[str, Any], lens: str) -> str:
    direction = _safe_float(snapshot.get("direction_score")) or 0.0
    uncertainty = _safe_float(snapshot.get("uncertainty_ratio")) or 0.05
    persistence = _safe_float(snapshot.get("persistence_score")) or 0.5
    regime_risk = _safe_float(snapshot.get("regime_shift_risk")) or 0.5
    first_moment = _safe_float(snapshot.get("first_moment_pct_per_day")) or 0.0
    second_moment = _safe_float(snapshot.get("second_moment_bp_per_day2")) or 0.0
    optimization = _safe_float(snapshot.get("optimization_score")) or 0.0

    if lens == "buffett":
        if direction >= 0.2 and uncertainty <= 0.04 and regime_risk <= 0.55:
            return "BUY"
        if direction <= -0.25 and first_moment < 0:
            return "SELL"
        return "HOLD"

    if lens == "druckenmiller":
        if direction >= 0.15 and first_moment > 0 and second_moment >= 0 and persistence >= 0.55:
            return "BUY"
        if direction <= -0.15 and first_moment < 0 and second_moment <= 0:
            return "SELL"
        return "HOLD"

    if lens == "lynch":
        if optimization >= 0.1 and direction >= 0.05 and uncertainty <= 0.06:
            return "BUY"
        if direction <= -0.2 and regime_risk >= 0.65:
            return "SELL"
        return "HOLD"

    if lens == "dalio":
        if persistence >= 0.65 and regime_risk <= 0.45 and uncertainty <= 0.045 and direction >= 0.08:
            return "BUY"
        if regime_risk >= 0.72 or (direction <= -0.12 and uncertainty >= 0.05):
            return "SELL"
        return "HOLD"

    return "HOLD"


def _preferred_changepoint_scale(snapshot: Dict[str, Any], realized_return_pct: float) -> float:
    persistence = _safe_float(snapshot.get("persistence_score")) or 0.5
    stability = _safe_float(snapshot.get("stability_score")) or 0.5
    regime_risk = _safe_float(snapshot.get("regime_shift_risk")) or 0.5
    uncertainty = _safe_float(snapshot.get("uncertainty_ratio")) or 0.04
    second_moment = abs(_safe_float(snapshot.get("second_moment_bp_per_day2")) or 0.0)
    realized_move = abs(realized_return_pct)

    target = (
        0.011
        + regime_risk * 0.022
        + uncertainty * 0.12
        + min(0.02, second_moment / 700.0)
        + min(0.02, realized_move * 0.7)
        - persistence * 0.006
        - stability * 0.005
    )
    return float(min(0.06, max(0.008, target)))


def _load_current_lens_states(conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    rows = conn.execute("SELECT lens, weight, avg_reward, reward_count, last_reward, last_updated_date, updated_at FROM investor_lens_states").fetchall()
    states: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        states[str(row[0])] = {
            "weight": _safe_float(row[1]) or 1.0,
            "avgReward": _safe_float(row[2]) or 0.0,
            "rewardCount": int(row[3] or 0),
            "lastReward": _safe_float(row[4]),
            "lastUpdatedDate": row[5],
            "updatedAt": row[6],
        }
    for lens in LENS_NAMES:
        states.setdefault(
            lens,
            {
                "weight": 1.0,
                "avgReward": 0.0,
                "rewardCount": 0,
                "lastReward": None,
                "lastUpdatedDate": None,
                "updatedAt": None,
            },
        )
    return states


def _update_lens_state(conn: sqlite3.Connection, lens: str, reward: float, realized_date: str) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT weight, avg_reward, reward_count FROM investor_lens_states WHERE lens = ?",
        (lens,),
    ).fetchone()
    previous_weight = _safe_float(row[0]) if row else 1.0
    avg_reward = _safe_float(row[1]) if row else 0.0
    reward_count = int(row[2] or 0) if row else 0

    next_weight = max(
        MIN_LENS_WEIGHT,
        min(MAX_LENS_WEIGHT, float(previous_weight or 1.0) * math.exp(LENS_LEARNING_RATE * reward)),
    )
    next_count = reward_count + 1
    next_avg_reward = (
        ((avg_reward or 0.0) * reward_count + reward) / next_count if next_count > 0 else reward
    )
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO investor_lens_states (
            lens, weight, avg_reward, reward_count, last_reward, last_updated_date, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(lens) DO UPDATE SET
            weight=excluded.weight,
            avg_reward=excluded.avg_reward,
            reward_count=excluded.reward_count,
            last_reward=excluded.last_reward,
            last_updated_date=excluded.last_updated_date,
            updated_at=excluded.updated_at
        """,
        (lens, next_weight, next_avg_reward, next_count, reward, realized_date, now),
    )
    return {
        "weight": next_weight,
        "avgReward": next_avg_reward,
        "rewardCount": next_count,
        "lastReward": reward,
        "lastUpdatedDate": realized_date,
        "updatedAt": now,
    }


def _update_macbook_state(
    conn: sqlite3.Connection,
    *,
    reward: float,
    hit: bool,
    reference_date: str,
    realized_date: str,
    realized_return_pct: float,
    coverage_ratio: float,
    champion_avg_reward: float,
    champion_alignment_score: float,
    champion_reward_count: float,
    champion_preferred_cps: float,
) -> Dict[str, Any]:
    row = conn.execute(
        """
        SELECT weight, avg_reward, reward_count, hit_count
        FROM macbook_agent_states
        WHERE agent = ?
        """,
        (MACBOOK_AGENT_NAME,),
    ).fetchone()

    previous_weight = _safe_float(row[0]) if row else 1.0
    avg_reward = _safe_float(row[1]) if row else 0.0
    reward_count = int(row[2] or 0) if row else 0
    hit_count = int(row[3] or 0) if row else 0

    next_weight = max(
        MIN_LENS_WEIGHT,
        min(MAX_LENS_WEIGHT, float(previous_weight or 1.0) * math.exp(LENS_LEARNING_RATE * reward)),
    )
    next_count = reward_count + 1
    next_hit_count = hit_count + (1 if hit else 0)
    next_hit_rate = next_hit_count / next_count if next_count > 0 else 0.0
    next_avg_reward = (((avg_reward or 0.0) * reward_count) + reward) / next_count
    now = utc_now_iso()

    conn.execute(
        """
        INSERT INTO macbook_agent_states (
            agent, weight, avg_reward, reward_count, hit_count, hit_rate,
            last_reward, last_reference_date, last_realized_date,
            last_realized_return_pct, last_coverage_ratio,
            champion_avg_reward, champion_alignment_score,
            champion_reward_count, champion_preferred_cps, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(agent) DO UPDATE SET
            weight=excluded.weight,
            avg_reward=excluded.avg_reward,
            reward_count=excluded.reward_count,
            hit_count=excluded.hit_count,
            hit_rate=excluded.hit_rate,
            last_reward=excluded.last_reward,
            last_reference_date=excluded.last_reference_date,
            last_realized_date=excluded.last_realized_date,
            last_realized_return_pct=excluded.last_realized_return_pct,
            last_coverage_ratio=excluded.last_coverage_ratio,
            champion_avg_reward=excluded.champion_avg_reward,
            champion_alignment_score=excluded.champion_alignment_score,
            champion_reward_count=excluded.champion_reward_count,
            champion_preferred_cps=excluded.champion_preferred_cps,
            updated_at=excluded.updated_at
        """,
        (
            MACBOOK_AGENT_NAME,
            next_weight,
            next_avg_reward,
            next_count,
            next_hit_count,
            next_hit_rate,
            reward,
            reference_date,
            realized_date,
            realized_return_pct,
            coverage_ratio,
            champion_avg_reward,
            champion_alignment_score,
            champion_reward_count,
            champion_preferred_cps,
            now,
        ),
    )

    return {
        "name": MACBOOK_AGENT_NAME,
        "weight": next_weight,
        "avgReward": next_avg_reward,
        "rewardCount": next_count,
        "hitCount": next_hit_count,
        "hitRate": next_hit_rate,
        "lastReward": reward,
        "lastReferenceDate": reference_date,
        "lastRealizedDate": realized_date,
        "lastRealizedReturnPct": realized_return_pct,
        "lastCoverageRatio": coverage_ratio,
        "championAvgReward": champion_avg_reward,
        "championAlignmentScore": champion_alignment_score,
        "championRewardCount": champion_reward_count,
        "championPreferredCps": champion_preferred_cps,
        "updatedAt": now,
    }


def _update_spike_model_state(
    conn: sqlite3.Connection,
    *,
    model: str,
    reward: float,
    hit: bool,
    reference_date: str,
    realized_date: str,
    realized_spike_sustain_seconds: float,
    realized_max_spike_pct: float,
) -> Dict[str, Any]:
    row = conn.execute(
        """
        SELECT weight, avg_reward, reward_count, hit_count
        FROM spike_sustain_model_states
        WHERE model = ?
        """,
        (model,),
    ).fetchone()

    previous_weight = _safe_float(row[0]) if row else 1.0
    avg_reward = _safe_float(row[1]) if row else 0.0
    reward_count = int(row[2] or 0) if row else 0
    hit_count = int(row[3] or 0) if row else 0

    next_weight = max(
        MIN_LENS_WEIGHT,
        min(MAX_LENS_WEIGHT, float(previous_weight or 1.0) * math.exp(SPIKE_MODEL_LEARNING_RATE * reward)),
    )
    next_count = reward_count + 1
    next_hit_count = hit_count + (1 if hit else 0)
    next_hit_rate = next_hit_count / next_count if next_count > 0 else 0.0
    next_avg_reward = (((avg_reward or 0.0) * reward_count) + reward) / next_count
    now = utc_now_iso()

    conn.execute(
        """
        INSERT INTO spike_sustain_model_states (
            model, weight, avg_reward, reward_count, hit_count, hit_rate, last_reward,
            last_reference_date, last_realized_date, last_realized_spike_sustain_seconds,
            last_realized_max_spike_pct, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(model) DO UPDATE SET
            weight=excluded.weight,
            avg_reward=excluded.avg_reward,
            reward_count=excluded.reward_count,
            hit_count=excluded.hit_count,
            hit_rate=excluded.hit_rate,
            last_reward=excluded.last_reward,
            last_reference_date=excluded.last_reference_date,
            last_realized_date=excluded.last_realized_date,
            last_realized_spike_sustain_seconds=excluded.last_realized_spike_sustain_seconds,
            last_realized_max_spike_pct=excluded.last_realized_max_spike_pct,
            updated_at=excluded.updated_at
        """,
        (
            model,
            next_weight,
            next_avg_reward,
            next_count,
            next_hit_count,
            next_hit_rate,
            reward,
            reference_date,
            realized_date,
            realized_spike_sustain_seconds,
            realized_max_spike_pct,
            now,
        ),
    )

    return {
        "model": model,
        "weight": next_weight,
        "avgReward": next_avg_reward,
        "rewardCount": next_count,
        "hitCount": next_hit_count,
        "hitRate": next_hit_rate,
        "lastReward": reward,
        "lastReferenceDate": reference_date,
        "lastRealizedDate": realized_date,
        "lastRealizedSpikeSustainSeconds": realized_spike_sustain_seconds,
        "lastRealizedMaxSpikePct": realized_max_spike_pct,
        "updatedAt": now,
    }


def _update_champion_state(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    task: str,
    rule: str,
    reward: float,
    reference_date: str,
    preferred_changepoint_scale: float,
) -> Dict[str, Any]:
    row = conn.execute(
        """
        SELECT preferred_changepoint_scale, avg_reward, reward_count
        FROM champion_reinforcement_states
        WHERE symbol = ? AND task = ? AND rule = ?
        """,
        (symbol, task, rule),
    ).fetchone()
    previous_cps = _safe_float(row[0]) if row else DEFAULT_CHAMPION_PREFERRED_CPS
    avg_reward = _safe_float(row[1]) if row else 0.0
    reward_count = int(row[2] or 0) if row else 0

    blended_cps = float(previous_cps or DEFAULT_CHAMPION_PREFERRED_CPS) * (
        1.0 - CHAMPION_REWARD_LEARNING_RATE
    ) + preferred_changepoint_scale * CHAMPION_REWARD_LEARNING_RATE
    if reward < 0:
        blended_cps = blended_cps * 0.85 + preferred_changepoint_scale * 0.15
    blended_cps = float(min(0.06, max(0.008, blended_cps)))

    next_count = reward_count + 1
    next_avg_reward = (
        ((avg_reward or 0.0) * reward_count + reward) / next_count if next_count > 0 else reward
    )
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO champion_reinforcement_states (
            symbol, task, rule, preferred_changepoint_scale, avg_reward,
            reward_count, last_reward, last_reference_date, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, task, rule) DO UPDATE SET
            preferred_changepoint_scale=excluded.preferred_changepoint_scale,
            avg_reward=excluded.avg_reward,
            reward_count=excluded.reward_count,
            last_reward=excluded.last_reward,
            last_reference_date=excluded.last_reference_date,
            updated_at=excluded.updated_at
        """,
        (
            symbol,
            task,
            rule,
            blended_cps,
            next_avg_reward,
            next_count,
            reward,
            reference_date,
            now,
        ),
    )
    return {
        "preferredChangepointScale": blended_cps,
        "avgReward": next_avg_reward,
        "rewardCount": next_count,
        "lastReward": reward,
        "lastReferenceDate": reference_date,
        "updatedAt": now,
    }


def export_investor_lens_state(conn: sqlite3.Connection) -> Dict[str, Any]:
    states = _load_current_lens_states(conn)
    ordered = [
        {"lens": lens, **states[lens]}
        for lens in sorted(states.keys(), key=lambda item: states[item]["weight"], reverse=True)
    ]
    leader = ordered[0]["lens"] if ordered else "buffett"
    payload = {
        "generatedAt": utc_now_iso(),
        "leader": leader,
        "lenses": ordered,
    }
    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    INVESTOR_LENS_STATE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def export_macbook_agent_state(conn: sqlite3.Connection) -> Dict[str, Any]:
    row = conn.execute(
        """
        SELECT weight, avg_reward, reward_count, hit_count, hit_rate, last_reward,
               last_reference_date, last_realized_date, last_realized_return_pct,
               last_coverage_ratio, champion_avg_reward, champion_alignment_score,
               champion_reward_count, champion_preferred_cps, updated_at
        FROM macbook_agent_states
        WHERE agent = ?
        """,
        (MACBOOK_AGENT_NAME,),
    ).fetchone()
    payload = {
        "generatedAt": utc_now_iso(),
        "name": MACBOOK_AGENT_NAME,
        "weight": _safe_float(row[0]) if row else 1.0,
        "avgReward": _safe_float(row[1]) if row else 0.0,
        "rewardCount": int(row[2] or 0) if row else 0,
        "hitCount": int(row[3] or 0) if row else 0,
        "hitRate": _safe_float(row[4]) if row else 0.0,
        "lastReward": _safe_float(row[5]) if row else None,
        "lastReferenceDate": row[6] if row else None,
        "lastRealizedDate": row[7] if row else None,
        "lastRealizedReturnPct": _safe_float(row[8]) if row else None,
        "lastCoverageRatio": _safe_float(row[9]) if row else None,
        "championAvgReward": _safe_float(row[10]) if row else None,
        "championAlignmentScore": _safe_float(row[11]) if row else None,
        "championRewardCount": _safe_float(row[12]) if row else None,
        "championPreferredCps": _safe_float(row[13]) if row else None,
        "updatedAt": row[14] if row else None,
    }
    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    MACBOOK_AGENT_STATE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def export_spike_sustain_state(conn: sqlite3.Connection) -> Dict[str, Any]:
    rows = conn.execute(
        """
        SELECT model, weight, avg_reward, reward_count, hit_count, hit_rate, last_reward,
               last_reference_date, last_realized_date, last_realized_spike_sustain_seconds,
               last_realized_max_spike_pct, updated_at
        FROM spike_sustain_model_states
        ORDER BY weight DESC, model ASC
        """
    ).fetchall()

    models: List[Dict[str, Any]] = []
    for model in SPIKE_MODEL_NAMES:
        row = next((item for item in rows if str(item[0]) == model), None)
        models.append(
            {
                "model": model,
                "weight": _safe_float(row[1]) if row else 1.0,
                "avgReward": _safe_float(row[2]) if row else 0.0,
                "rewardCount": int(row[3] or 0) if row else 0,
                "hitCount": int(row[4] or 0) if row else 0,
                "hitRate": _safe_float(row[5]) if row else 0.0,
                "lastReward": _safe_float(row[6]) if row else None,
                "lastReferenceDate": row[7] if row else None,
                "lastRealizedDate": row[8] if row else None,
                "lastRealizedSpikeSustainSeconds": _safe_float(row[9]) if row else None,
                "lastRealizedMaxSpikePct": _safe_float(row[10]) if row else None,
                "updatedAt": row[11] if row else None,
            }
        )

    leader = max(models, key=lambda item: item["weight"])["model"] if models else "prophet"
    payload = {
        "generatedAt": utc_now_iso(),
        "leader": leader,
        "models": models,
    }
    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SPIKE_SUSTAIN_STATE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def _read_snapshot_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _lens_snapshot_has_learning(snapshot: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(snapshot, dict):
        return False
    lenses = snapshot.get("lenses")
    if not isinstance(lenses, list):
        return False
    return any(int(item.get("rewardCount") or 0) > 0 for item in lenses if isinstance(item, dict))


def _macbook_snapshot_has_learning(snapshot: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(snapshot, dict):
        return False
    return int(snapshot.get("rewardCount") or 0) > 0


def _spike_snapshot_has_learning(snapshot: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(snapshot, dict):
        return False
    models = snapshot.get("models")
    if not isinstance(models, list):
        return False
    return any(int(item.get("rewardCount") or 0) > 0 for item in models if isinstance(item, dict))


def _table_row_count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(row[0] or 0) if row else 0


def _table_reward_sum(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COALESCE(SUM(reward_count), 0) FROM {table}").fetchone()
    return int(row[0] or 0) if row else 0


def _reset_reinforcement_state_tables(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM investor_lens_states")
    conn.execute("DELETE FROM champion_reinforcement_states")
    conn.execute("DELETE FROM macbook_agent_states")


def _rebuild_investor_lens_states_from_events(conn: sqlite3.Connection) -> Dict[str, Any]:
    conn.execute("DELETE FROM investor_lens_states")
    rows = conn.execute(
        """
        SELECT realized_date, lens_rewards_json
        FROM daily_reward_events
        ORDER BY realized_date ASC, reference_date ASC, id ASC
        """
    ).fetchall()

    for row in rows:
        realized_date = str(row[0] or "")
        raw_rewards = str(row[1] or "{}")
        try:
            lens_rewards = json.loads(raw_rewards)
        except json.JSONDecodeError:
            lens_rewards = {}
        if not isinstance(lens_rewards, dict):
            continue
        for lens in LENS_NAMES:
            reward = _safe_float(lens_rewards.get(lens))
            if reward is None:
                continue
            _update_lens_state(conn, lens, reward, realized_date)

    return export_investor_lens_state(conn)


def _rebuild_macbook_state_from_events(conn: sqlite3.Connection) -> Dict[str, Any]:
    conn.execute("DELETE FROM macbook_agent_states")
    rows = conn.execute(
        """
        SELECT reference_date, realized_date, reference_payload_path,
               reward, hit, realized_return_pct, coverage_ratio
        FROM daily_portfolio_feedback_events
        ORDER BY reference_date ASC
        """
    ).fetchall()

    for row in rows:
        payload = _load_portfolio_payload(str(row[2] or "")) or {}
        summary = payload.get("summary") or {}
        _update_macbook_state(
            conn,
            reward=_safe_float(row[3]) or 0.0,
            hit=bool(row[4]),
            reference_date=str(row[0] or ""),
            realized_date=str(row[1] or ""),
            realized_return_pct=_safe_float(row[5]) or 0.0,
            coverage_ratio=_safe_float(row[6]) or 0.0,
            champion_avg_reward=_safe_float(summary.get("weightedChampionProphetAvgReward")) or 0.0,
            champion_alignment_score=(
                _safe_float(summary.get("weightedChampionProphetAlignmentScore")) or 0.0
            ),
            champion_reward_count=(
                _safe_float(summary.get("weightedChampionProphetRewardCount")) or 0.0
            ),
            champion_preferred_cps=(
                _safe_float(summary.get("weightedChampionProphetPreferredCps"))
                or DEFAULT_CHAMPION_PREFERRED_CPS
            ),
        )

    return export_macbook_agent_state(conn)


def _rebuild_spike_sustain_states_from_events(conn: sqlite3.Connection) -> Dict[str, Any]:
    conn.execute("DELETE FROM spike_sustain_model_states")
    rows = conn.execute(
        """
        SELECT reference_date, resolved_at, realized_spike_sustain_seconds,
               realized_max_spike_pct, prophet_reward, timesfm_reward
        FROM spike_sustain_prediction_events
        WHERE resolved_at IS NOT NULL
          AND (
              prophet_reward IS NOT NULL
              OR timesfm_reward IS NOT NULL
          )
        ORDER BY reference_date ASC, id ASC
        """
    ).fetchall()

    for row in rows:
        reference_date = str(row[0] or "")
        resolved_at = str(row[1] or "")
        realized_date = resolved_at[:10] if resolved_at else reference_date
        realized_spike_sustain_seconds = _safe_float(row[2]) or 0.0
        realized_max_spike_pct = _safe_float(row[3]) or 0.0
        prophet_reward = _safe_float(row[4])
        timesfm_reward = _safe_float(row[5])

        if prophet_reward is not None:
            _update_spike_model_state(
                conn,
                model="prophet",
                reward=prophet_reward,
                hit=prophet_reward >= 0,
                reference_date=reference_date,
                realized_date=realized_date,
                realized_spike_sustain_seconds=realized_spike_sustain_seconds,
                realized_max_spike_pct=realized_max_spike_pct,
            )
        if timesfm_reward is not None:
            _update_spike_model_state(
                conn,
                model="timesfm",
                reward=timesfm_reward,
                hit=timesfm_reward >= 0,
                reference_date=reference_date,
                realized_date=realized_date,
                realized_spike_sustain_seconds=realized_spike_sustain_seconds,
                realized_max_spike_pct=realized_max_spike_pct,
            )

    return export_spike_sustain_state(conn)


def ensure_reinforcement_warm_start() -> Dict[str, Any]:
    investor_snapshot = _read_snapshot_json(INVESTOR_LENS_STATE_PATH)
    macbook_snapshot = _read_snapshot_json(MACBOOK_AGENT_STATE_PATH)
    spike_snapshot = _read_snapshot_json(SPIKE_SUSTAIN_STATE_PATH)

    investor_ready = _lens_snapshot_has_learning(investor_snapshot)
    macbook_ready = _macbook_snapshot_has_learning(macbook_snapshot)
    spike_ready = _spike_snapshot_has_learning(spike_snapshot)

    if investor_ready and macbook_ready and (spike_ready or spike_snapshot is not None):
        return {
            "investorLens": investor_snapshot,
            "macbookAgent": macbook_snapshot,
            "spikeSustainAgent": spike_snapshot,
            "historicalWarmStart": False,
            "warmStartSource": "snapshot",
        }

    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(REINFORCEMENT_DB_PATH) as conn:
        ensure_reinforcement_schema(conn)
        investor_state_reward_sum = _table_reward_sum(conn, "investor_lens_states")
        macbook_state_reward_sum = _table_reward_sum(conn, "macbook_agent_states")
        spike_state_reward_sum = _table_reward_sum(conn, "spike_sustain_model_states")
        daily_reward_events = _table_row_count(conn, "daily_reward_events")
        portfolio_feedback_events = _table_row_count(conn, "daily_portfolio_feedback_events")
        resolved_spike_events_row = conn.execute(
            """
            SELECT COUNT(*)
            FROM spike_sustain_prediction_events
            WHERE resolved_at IS NOT NULL
              AND (
                  prophet_reward IS NOT NULL
                  OR timesfm_reward IS NOT NULL
              )
            """
        ).fetchone()
        resolved_spike_events = int(resolved_spike_events_row[0] or 0) if resolved_spike_events_row else 0

        if not investor_ready and investor_state_reward_sum > 0:
            investor_snapshot = export_investor_lens_state(conn)
            investor_ready = True
        if not macbook_ready and macbook_state_reward_sum > 0:
            macbook_snapshot = export_macbook_agent_state(conn)
            macbook_ready = True
        if not spike_ready and spike_state_reward_sum > 0:
            spike_snapshot = export_spike_sustain_state(conn)
            spike_ready = True
        conn.commit()

    historical_sources_available = MAP_HISTORY_DB_PATH.exists() or PORTFOLIO_HISTORY_DB_PATH.exists()
    if historical_sources_available and (not investor_ready or not macbook_ready):
        report = run_daily_reinforcement(rebuild_states=True)
        investor_snapshot = report.get("investorLensSnapshot") or _read_snapshot_json(
            INVESTOR_LENS_STATE_PATH
        )
        macbook_snapshot = report.get("macbookAgentSnapshot") or _read_snapshot_json(
            MACBOOK_AGENT_STATE_PATH
        )
        investor_ready = _lens_snapshot_has_learning(investor_snapshot)
        macbook_ready = _macbook_snapshot_has_learning(macbook_snapshot)
    elif daily_reward_events > 0 or portfolio_feedback_events > 0:
        with sqlite3.connect(REINFORCEMENT_DB_PATH) as conn:
            ensure_reinforcement_schema(conn)
            if not investor_ready and daily_reward_events > 0:
                investor_snapshot = _rebuild_investor_lens_states_from_events(conn)
                investor_ready = _lens_snapshot_has_learning(investor_snapshot)
            if not macbook_ready and portfolio_feedback_events > 0:
                macbook_snapshot = _rebuild_macbook_state_from_events(conn)
                macbook_ready = _macbook_snapshot_has_learning(macbook_snapshot)
            conn.commit()

    if not spike_ready and resolved_spike_events > 0:
        with sqlite3.connect(REINFORCEMENT_DB_PATH) as conn:
            ensure_reinforcement_schema(conn)
            spike_snapshot = _rebuild_spike_sustain_states_from_events(conn)
            conn.commit()
        spike_ready = _spike_snapshot_has_learning(spike_snapshot)

    return {
        "investorLens": investor_snapshot,
        "macbookAgent": macbook_snapshot,
        "spikeSustainAgent": spike_snapshot,
        "historicalWarmStart": investor_ready or macbook_ready or spike_ready,
        "warmStartSource": (
            "history-replay"
            if historical_sources_available and (investor_ready or macbook_ready)
            else "db-rebuild"
            if spike_ready or investor_ready or macbook_ready
            else "default"
        ),
    }


def load_investor_lens_snapshot() -> Dict[str, Any]:
    snapshot = _read_snapshot_json(INVESTOR_LENS_STATE_PATH)
    if _lens_snapshot_has_learning(snapshot):
        return snapshot

    warmed = ensure_reinforcement_warm_start().get("investorLens")
    if _lens_snapshot_has_learning(warmed):
        return warmed

    return {
        "generatedAt": None,
        "leader": "buffett",
        "lenses": [
            {
                "lens": lens,
                "weight": DEFAULT_LENS_WEIGHTS[lens],
                "avgReward": 0.0,
                "rewardCount": 0,
                "lastReward": None,
                "lastUpdatedDate": None,
                "updatedAt": None,
            }
            for lens in LENS_NAMES
        ],
    }


def load_macbook_agent_snapshot() -> Dict[str, Any]:
    snapshot = _read_snapshot_json(MACBOOK_AGENT_STATE_PATH)
    if _macbook_snapshot_has_learning(snapshot):
        return snapshot

    warmed = ensure_reinforcement_warm_start().get("macbookAgent")
    if _macbook_snapshot_has_learning(warmed):
        return warmed

    return {
        "generatedAt": None,
        "name": MACBOOK_AGENT_NAME,
        "weight": 1.0,
        "avgReward": 0.0,
        "rewardCount": 0,
        "hitCount": 0,
        "hitRate": 0.0,
        "lastReward": None,
        "lastReferenceDate": None,
        "lastRealizedDate": None,
        "lastRealizedReturnPct": None,
        "lastCoverageRatio": None,
        "championAvgReward": None,
        "championAlignmentScore": None,
        "championRewardCount": None,
        "championPreferredCps": None,
        "updatedAt": None,
    }


def load_spike_sustain_snapshot() -> Dict[str, Any]:
    snapshot = _read_snapshot_json(SPIKE_SUSTAIN_STATE_PATH)
    if _spike_snapshot_has_learning(snapshot):
        return snapshot

    warmed = ensure_reinforcement_warm_start().get("spikeSustainAgent")
    if _spike_snapshot_has_learning(warmed):
        return warmed

    return {
        "generatedAt": None,
        "leader": "prophet",
        "models": [
            {
                "model": model,
                "weight": 1.0,
                "avgReward": 0.0,
                "rewardCount": 0,
                "hitCount": 0,
                "hitRate": 0.0,
                "lastReward": None,
                "lastReferenceDate": None,
                "lastRealizedDate": None,
                "lastRealizedSpikeSustainSeconds": None,
                "lastRealizedMaxSpikePct": None,
                "updatedAt": None,
            }
            for model in SPIKE_MODEL_NAMES
        ],
    }


def sync_spike_feedback_loop(
    *,
    symbol: str,
    price_history_df: pd.DataFrame,
    reference_price: Optional[float],
    reference_timestamp: Optional[Any],
    prophet_profile: Optional[Dict[str, Any]],
    timesfm_profile: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    normalized_symbol = normalize_symbol(symbol)
    resolved_reference_price = _safe_float(reference_price)
    ordered = _normalize_price_history(price_history_df)
    if not normalized_symbol or resolved_reference_price is None or resolved_reference_price <= 0 or ordered.empty:
        return {
            "referenceDate": None,
            "processedEvents": 0,
            "snapshot": load_spike_sustain_snapshot(),
            "dbPath": str(REINFORCEMENT_DB_PATH),
        }

    reference_ts = pd.Timestamp(reference_timestamp or ordered.iloc[-1]["ds"])
    if pd.isna(reference_ts):
        reference_ts = pd.Timestamp(ordered.iloc[-1]["ds"])
    reference_date = str(reference_ts.date())

    prophet_profile = prophet_profile or {}
    timesfm_profile = timesfm_profile or {}
    processed_events = 0

    with sqlite3.connect(REINFORCEMENT_DB_PATH) as conn:
        ensure_reinforcement_schema(conn)
        pending_rows = conn.execute(
            """
            SELECT id, reference_date, reference_price,
                   predicted_spike_sustain_seconds, predicted_peak_to_fade_seconds,
                   predicted_max_spike_pct, predicted_spike_fade_in_horizon,
                   timesfm_predicted_spike_sustain_seconds, timesfm_predicted_peak_to_fade_seconds,
                   timesfm_predicted_max_spike_pct, timesfm_predicted_spike_fade_in_horizon
            FROM spike_sustain_prediction_events
            WHERE symbol = ? AND resolved_at IS NULL
            ORDER BY reference_date ASC
            """,
            (normalized_symbol,),
        ).fetchall()

        for row in pending_rows:
            event_id = int(row[0])
            event_reference_date = str(row[1] or "")
            event_reference_price = _safe_float(row[2])
            if not event_reference_date or not event_reference_price or event_reference_date >= reference_date:
                continue

            realized_slice = ordered.loc[
                ordered["ds"].dt.strftime("%Y-%m-%d") >= event_reference_date
            ].copy()
            if len(realized_slice) < 2:
                continue

            realized_profile = _compute_realized_spike_profile(
                realized_slice,
                reference_price=event_reference_price,
            )
            realized_sustain_seconds = _safe_float(realized_profile.get("spike_sustain_seconds")) or 0.0
            realized_peak_to_fade_seconds = _safe_float(realized_profile.get("peak_to_fade_seconds")) or 0.0
            realized_max_spike_pct = _safe_float(realized_profile.get("max_spike_pct")) or 0.0
            realized_fade_in_horizon = bool(realized_profile.get("spike_fade_in_horizon"))

            prophet_reward, prophet_hit = _compute_spike_prediction_reward(
                predicted_sustain_seconds=_safe_float(row[3]),
                predicted_peak_to_fade_seconds=_safe_float(row[4]),
                predicted_max_spike_pct=_safe_float(row[5]),
                predicted_spike_fade_in_horizon=bool(row[6]) if row[6] is not None else None,
                realized_profile=realized_profile,
            )
            timesfm_reward, timesfm_hit = _compute_spike_prediction_reward(
                predicted_sustain_seconds=_safe_float(row[7]),
                predicted_peak_to_fade_seconds=_safe_float(row[8]),
                predicted_max_spike_pct=_safe_float(row[9]),
                predicted_spike_fade_in_horizon=bool(row[10]) if row[10] is not None else None,
                realized_profile=realized_profile,
            )

            _update_spike_model_state(
                conn,
                model="prophet",
                reward=prophet_reward,
                hit=prophet_hit,
                reference_date=event_reference_date,
                realized_date=reference_date,
                realized_spike_sustain_seconds=realized_sustain_seconds,
                realized_max_spike_pct=realized_max_spike_pct,
            )
            _update_spike_model_state(
                conn,
                model="timesfm",
                reward=timesfm_reward,
                hit=timesfm_hit,
                reference_date=event_reference_date,
                realized_date=reference_date,
                realized_spike_sustain_seconds=realized_sustain_seconds,
                realized_max_spike_pct=realized_max_spike_pct,
            )

            conn.execute(
                """
                UPDATE spike_sustain_prediction_events
                SET realized_spike_sustain_seconds = ?,
                    realized_peak_to_fade_seconds = ?,
                    realized_max_spike_pct = ?,
                    realized_spike_fade_in_horizon = ?,
                    prophet_reward = ?,
                    timesfm_reward = ?,
                    resolved_at = ?
                WHERE id = ?
                """,
                (
                    realized_sustain_seconds,
                    realized_peak_to_fade_seconds,
                    realized_max_spike_pct,
                    1 if realized_fade_in_horizon else 0,
                    prophet_reward,
                    timesfm_reward,
                    utc_now_iso(),
                    event_id,
                ),
            )
            processed_events += 1

        conn.execute(
            """
            INSERT INTO spike_sustain_prediction_events (
                symbol, reference_date, reference_timestamp, reference_price,
                predicted_spike_sustain_seconds, predicted_peak_to_fade_seconds,
                predicted_max_spike_pct, predicted_spike_fade_in_horizon,
                timesfm_predicted_spike_sustain_seconds, timesfm_predicted_peak_to_fade_seconds,
                timesfm_predicted_max_spike_pct, timesfm_predicted_spike_fade_in_horizon,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, reference_date) DO UPDATE SET
                reference_timestamp=excluded.reference_timestamp,
                reference_price=excluded.reference_price,
                predicted_spike_sustain_seconds=excluded.predicted_spike_sustain_seconds,
                predicted_peak_to_fade_seconds=excluded.predicted_peak_to_fade_seconds,
                predicted_max_spike_pct=excluded.predicted_max_spike_pct,
                predicted_spike_fade_in_horizon=excluded.predicted_spike_fade_in_horizon,
                timesfm_predicted_spike_sustain_seconds=excluded.timesfm_predicted_spike_sustain_seconds,
                timesfm_predicted_peak_to_fade_seconds=excluded.timesfm_predicted_peak_to_fade_seconds,
                timesfm_predicted_max_spike_pct=excluded.timesfm_predicted_max_spike_pct,
                timesfm_predicted_spike_fade_in_horizon=excluded.timesfm_predicted_spike_fade_in_horizon
            """,
            (
                normalized_symbol,
                reference_date,
                reference_ts.isoformat(),
                resolved_reference_price,
                _safe_float(prophet_profile.get("spike_sustain_seconds")),
                _safe_float(prophet_profile.get("peak_to_fade_seconds")),
                _safe_float(prophet_profile.get("max_spike_pct")),
                (
                    1 if bool(prophet_profile.get("spike_fade_in_horizon"))
                    else 0 if prophet_profile.get("spike_fade_in_horizon") is not None
                    else None
                ),
                _safe_float(timesfm_profile.get("timesfm_spike_sustain_seconds")),
                _safe_float(timesfm_profile.get("timesfm_peak_to_fade_seconds")),
                _safe_float(timesfm_profile.get("timesfm_max_spike_pct")),
                (
                    1 if bool(timesfm_profile.get("timesfm_spike_fade_in_horizon"))
                    else 0 if timesfm_profile.get("timesfm_spike_fade_in_horizon") is not None
                    else None
                ),
                utc_now_iso(),
            ),
        )

        snapshot = export_spike_sustain_state(conn)
        conn.commit()

    return {
        "referenceDate": reference_date,
        "processedEvents": processed_events,
        "snapshot": snapshot,
        "dbPath": str(REINFORCEMENT_DB_PATH),
    }


def load_champion_reinforcement_state(symbol: str, task: str, rule: str) -> Optional[Dict[str, Any]]:
    if not REINFORCEMENT_DB_PATH.exists():
        return None
    with sqlite3.connect(REINFORCEMENT_DB_PATH) as conn:
        ensure_reinforcement_schema(conn)
        row = conn.execute(
            """
            SELECT preferred_changepoint_scale, avg_reward, reward_count, last_reward, last_reference_date, updated_at
            FROM champion_reinforcement_states
            WHERE symbol = ? AND task = ? AND rule = ?
            """,
            (normalize_symbol(symbol), task, rule),
        ).fetchone()
        if not row:
            return None
        return {
            "preferredChangepointScale": _safe_float(row[0]) or DEFAULT_CHAMPION_PREFERRED_CPS,
            "avgReward": _safe_float(row[1]) or 0.0,
            "rewardCount": int(row[2] or 0),
            "lastReward": _safe_float(row[3]),
            "lastReferenceDate": row[4],
            "updatedAt": row[5],
        }


def compute_champion_reinforcement_prior(config: Any, rl_state: Optional[Dict[str, Any]]) -> float:
    if not rl_state:
        return 0.0
    preferred_cps = _safe_float(rl_state.get("preferredChangepointScale"))
    avg_reward = _safe_float(rl_state.get("avgReward")) or 0.0
    reward_count = int(rl_state.get("rewardCount") or 0)
    if preferred_cps is None:
        return 0.0

    cps_gap_penalty = abs(float(getattr(config, "changepoint_prior_scale", 0.03)) - preferred_cps) * 0.9
    reward_bonus = -avg_reward * min(0.06, 0.01 + reward_count * 0.0015)
    return float(cps_gap_penalty + reward_bonus)


def run_daily_reinforcement(
    *,
    cadence_rules: Iterable[str] = ("20D", "5D", "1D"),
    champion_tasks: Iterable[str] = ("direction", "low", "high"),
    rebuild_states: bool = False,
) -> Dict[str, Any]:
    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    processed_events = 0
    updated_symbols: set[str] = set()

    with sqlite3.connect(REINFORCEMENT_DB_PATH) as conn:
        ensure_reinforcement_schema(conn)
        if rebuild_states:
            _reset_reinforcement_state_tables(conn)
        symbol_rows = _load_symbol_rows()
        portfolio_feedback_events = 0

        for symbol, rows in symbol_rows.items():
            if len(rows) < 2:
                continue

            for previous, current in zip(rows[:-1], rows[1:]):
                reference_price = _safe_float(previous.get("current_price"))
                realized_price = _safe_float(current.get("current_price"))
                if not reference_price or not realized_price or reference_price <= 0:
                    continue

                reference_date = str(previous.get("map_date") or "")
                realized_date = str(current.get("map_date") or "")
                if not reference_date or not realized_date:
                    continue

                exists = conn.execute(
                    """
                    SELECT 1 FROM daily_reward_events
                    WHERE symbol = ? AND reference_date = ? AND realized_date = ?
                    """,
                    (symbol, reference_date, realized_date),
                ).fetchone()
                if exists and not rebuild_states:
                    continue

                realized_return_pct = realized_price / reference_price - 1.0
                predicted_action = str(previous.get("final_action") or "HOLD").upper()
                champion_reward = _action_reward(realized_return_pct, predicted_action)

                lens_rewards: Dict[str, float] = {}
                for lens in LENS_NAMES:
                    lens_action = _infer_lens_action(previous, lens)
                    lens_rewards[lens] = _action_reward(realized_return_pct, lens_action)
                    _update_lens_state(conn, lens, lens_rewards[lens], realized_date)

                preferred_cps = _preferred_changepoint_scale(previous, realized_return_pct)
                for task in champion_tasks:
                    for rule in cadence_rules:
                        _update_champion_state(
                            conn,
                            symbol=symbol,
                            task=str(task),
                            rule=str(rule),
                            reward=champion_reward,
                            reference_date=reference_date,
                            preferred_changepoint_scale=preferred_cps,
                        )

                if not exists:
                    conn.execute(
                        """
                        INSERT INTO daily_reward_events (
                            symbol, reference_date, realized_date, reference_price, realized_price,
                            realized_return_pct, predicted_action, direction_score, uncertainty_ratio,
                            persistence_score, regime_shift_risk, champion_reward, lens_rewards_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            symbol,
                            reference_date,
                            realized_date,
                            reference_price,
                            realized_price,
                            realized_return_pct,
                            predicted_action,
                            _safe_float(previous.get("direction_score")),
                            _safe_float(previous.get("uncertainty_ratio")),
                            _safe_float(previous.get("persistence_score")),
                            _safe_float(previous.get("regime_shift_risk")),
                            champion_reward,
                            json.dumps(lens_rewards, ensure_ascii=False),
                            utc_now_iso(),
                        ),
                    )
                    processed_events += 1
                updated_symbols.add(symbol)

        portfolio_runs = _load_portfolio_runs()
        if portfolio_runs:
            try:
                from services.trader.sp500_information_map import ensure_sp500_matrix

                _, close_matrix = ensure_sp500_matrix()
            except Exception:
                close_matrix = pd.DataFrame()

            for run in portfolio_runs:
                reference_date = str(run.get("mapDate") or "")
                payload_path = str(run.get("payloadPath") or "")
                if not reference_date or not payload_path or close_matrix.empty:
                    continue

                exists = conn.execute(
                    "SELECT 1 FROM daily_portfolio_feedback_events WHERE reference_date = ?",
                    (reference_date,),
                ).fetchone()
                if exists and not rebuild_states:
                    continue

                realized_date = _next_trading_date(close_matrix, reference_date)
                if not realized_date:
                    continue

                payload = _load_portfolio_payload(payload_path)
                if not payload:
                    continue

                feedback = _compute_virtual_portfolio_feedback(payload, close_matrix, realized_date)
                if not feedback:
                    continue

                if not exists:
                    conn.execute(
                        """
                        INSERT INTO daily_portfolio_feedback_events (
                            reference_date, realized_date, reference_payload_path, champion_profile,
                            holdings_count, coverage_ratio, realized_return_pct, predicted_upside_pct,
                            weighted_uncertainty_pct, weighted_drawdown_linger_days,
                            continuity_score, target_distance, reward, hit, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            reference_date,
                            realized_date,
                            payload_path,
                            feedback.get("championProfile"),
                            int(feedback.get("holdingsCount") or 0),
                            float(feedback.get("coverageRatio") or 0.0),
                            float(feedback.get("realizedReturnPct") or 0.0),
                            _safe_float(feedback.get("predictedUpsidePct")),
                            _safe_float(feedback.get("weightedUncertaintyPct")),
                            _safe_float(feedback.get("weightedDrawdownLingerDays")),
                            _safe_float(feedback.get("continuityScore")),
                            _safe_float(feedback.get("targetDistance")),
                            float(feedback.get("reward") or 0.0),
                            1 if feedback.get("hit") else 0,
                            utc_now_iso(),
                        ),
                    )
                _update_macbook_state(
                    conn,
                    reward=float(feedback.get("reward") or 0.0),
                    hit=bool(feedback.get("hit")),
                    reference_date=reference_date,
                    realized_date=realized_date,
                    realized_return_pct=float(feedback.get("realizedReturnPct") or 0.0),
                    coverage_ratio=float(feedback.get("coverageRatio") or 0.0),
                    champion_avg_reward=float(feedback.get("championAvgReward") or 0.0),
                    champion_alignment_score=float(
                        feedback.get("championAlignmentScore") or 0.0
                    ),
                    champion_reward_count=float(feedback.get("championRewardCount") or 0.0),
                    champion_preferred_cps=float(
                        feedback.get("championPreferredCps")
                        or DEFAULT_CHAMPION_PREFERRED_CPS
                    ),
                )
                portfolio_feedback_events += 1

        today = today_market_date()
        now = utc_now_iso()
        conn.execute(
            """
            INSERT INTO reinforcement_runs (run_date, processed_events, updated_symbols, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_date) DO UPDATE SET
                processed_events=excluded.processed_events,
                updated_symbols=excluded.updated_symbols,
                updated_at=excluded.updated_at
            """,
            (today, processed_events, len(updated_symbols), now, now),
        )
        snapshot = export_investor_lens_state(conn)
        macbook_snapshot = export_macbook_agent_state(conn)
        conn.commit()

    return {
        "runDate": today_market_date(),
        "processedEvents": processed_events,
        "portfolioFeedbackEvents": portfolio_feedback_events,
        "updatedSymbols": sorted(updated_symbols),
        "investorLensSnapshot": snapshot,
        "macbookAgentSnapshot": macbook_snapshot,
        "dbPath": str(REINFORCEMENT_DB_PATH),
    }


def maybe_run_daily_reinforcement_once() -> Dict[str, Any]:
    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    today = today_market_date()
    investor_snapshot = _read_snapshot_json(INVESTOR_LENS_STATE_PATH)
    macbook_snapshot = _read_snapshot_json(MACBOOK_AGENT_STATE_PATH)
    artifacts_ready = _lens_snapshot_has_learning(investor_snapshot) and _macbook_snapshot_has_learning(
        macbook_snapshot
    )
    with sqlite3.connect(REINFORCEMENT_DB_PATH) as conn:
        ensure_reinforcement_schema(conn)
        existing = conn.execute(
            "SELECT processed_events, updated_symbols, updated_at FROM reinforcement_runs WHERE run_date = ?",
            (today,),
        ).fetchone()
        state_tables_ready = (
            _table_reward_sum(conn, "investor_lens_states") > 0
            and _table_reward_sum(conn, "macbook_agent_states") > 0
        )
        if existing and not artifacts_ready and state_tables_ready:
            export_investor_lens_state(conn)
            export_macbook_agent_state(conn)
            conn.commit()
            artifacts_ready = True
        elif existing and not artifacts_ready:
            warmed = ensure_reinforcement_warm_start()
            artifacts_ready = _lens_snapshot_has_learning(warmed.get("investorLens")) and (
                _macbook_snapshot_has_learning(warmed.get("macbookAgent"))
            )
        if existing and artifacts_ready:
            return {
                "runDate": today,
                "processedEvents": int(existing[0] or 0),
                "updatedSymbolsCount": int(existing[1] or 0),
                "updatedAt": existing[2],
                "cached": True,
            }
    rebuild_states = MAP_HISTORY_DB_PATH.exists() or PORTFOLIO_HISTORY_DB_PATH.exists()
    report = run_daily_reinforcement(rebuild_states=rebuild_states)
    report["cached"] = False
    return report
