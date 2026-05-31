from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from .config import WrapperConfig, default_weight_state
from .pipeline import update_wrapper_weights

ROOT_DIR = Path(__file__).resolve().parents[2]
MODEL_CACHE_DIR = ROOT_DIR / "services" / "trader" / "model_cache"
WRAPPER_STATE_DB_PATH = MODEL_CACHE_DIR / "wrapper_weights.sqlite3"
MAX_PENDING_SNAPSHOTS_PER_SYMBOL = 32


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def _json_safe(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(inner) for inner in value]
    return value


def ensure_wrapper_state_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS wrapper_weight_states (
            symbol TEXT PRIMARY KEY,
            weights_json TEXT NOT NULL,
            feedback_count INTEGER NOT NULL DEFAULT 0,
            update_count INTEGER NOT NULL DEFAULT 0,
            last_realized_action TEXT,
            last_realized_return_pct REAL,
            last_reference_price REAL,
            last_reference_timestamp TEXT,
            last_feedback_timestamp TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS wrapper_pending_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            reference_price REAL NOT NULL,
            reference_timestamp TEXT NOT NULL,
            wrapper_result_json TEXT NOT NULL,
            weights_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_wrapper_pending_predictions_symbol
            ON wrapper_pending_predictions(symbol, reference_timestamp, id);
        CREATE TABLE IF NOT EXISTS wrapper_feedback_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            reference_timestamp TEXT NOT NULL,
            realized_timestamp TEXT NOT NULL,
            reference_price REAL NOT NULL,
            realized_price REAL NOT NULL,
            realized_return_pct REAL NOT NULL,
            realized_action TEXT NOT NULL,
            previous_weights_json TEXT NOT NULL,
            updated_weights_json TEXT NOT NULL,
            wrapper_result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_wrapper_feedback_events_symbol
            ON wrapper_feedback_events(symbol, created_at DESC);
        """
    )


def _parse_timestamp(value: Any) -> Optional[pd.Timestamp]:
    if value is None:
        return None

    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _coerce_weights(weights: Optional[Dict[str, Any]]) -> Dict[str, float]:
    base = default_weight_state()
    if not isinstance(weights, dict):
        return dict(base)

    merged = dict(base)
    for name, value in weights.items():
        try:
            merged[name] = float(value)
        except (TypeError, ValueError):
            continue
    return merged


def _infer_realized_action(
    *,
    reference_price: float,
    realized_price: float,
    config: WrapperConfig,
) -> Tuple[str, float]:
    if reference_price <= 0 or realized_price <= 0:
        return "HOLD", 0.0

    realized_return_pct = (realized_price - reference_price) / reference_price

    if realized_return_pct >= config.realized_buy_return_pct:
        return "BUY", realized_return_pct
    if realized_return_pct <= -config.realized_sell_return_pct:
        return "SELL", realized_return_pct
    return "HOLD", realized_return_pct


def _upsert_weight_state(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    weights: Dict[str, float],
    feedback_count: int,
    update_count: int,
    last_realized_action: Optional[str],
    last_realized_return_pct: Optional[float],
    last_reference_price: Optional[float],
    last_reference_timestamp: Optional[str],
    last_feedback_timestamp: Optional[str],
) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO wrapper_weight_states (
            symbol,
            weights_json,
            feedback_count,
            update_count,
            last_realized_action,
            last_realized_return_pct,
            last_reference_price,
            last_reference_timestamp,
            last_feedback_timestamp,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            weights_json=excluded.weights_json,
            feedback_count=excluded.feedback_count,
            update_count=excluded.update_count,
            last_realized_action=excluded.last_realized_action,
            last_realized_return_pct=excluded.last_realized_return_pct,
            last_reference_price=excluded.last_reference_price,
            last_reference_timestamp=excluded.last_reference_timestamp,
            last_feedback_timestamp=excluded.last_feedback_timestamp,
            updated_at=excluded.updated_at
        """,
        (
            symbol,
            json.dumps(weights, ensure_ascii=False),
            int(feedback_count),
            int(update_count),
            last_realized_action,
            last_realized_return_pct,
            last_reference_price,
            last_reference_timestamp,
            last_feedback_timestamp,
            now,
            now,
        ),
    )


def load_wrapper_weights(
    symbol: str,
    *,
    current_price: Optional[float] = None,
    current_timestamp: Optional[Any] = None,
    config: Optional[WrapperConfig] = None,
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    config = config or WrapperConfig()
    normalized_symbol = normalize_symbol(symbol)
    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    feedback_applied = 0
    latest_feedback: Optional[Dict[str, Any]] = None

    with sqlite3.connect(WRAPPER_STATE_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        ensure_wrapper_state_schema(conn)

        state_row = conn.execute(
            "SELECT * FROM wrapper_weight_states WHERE symbol = ?",
            (normalized_symbol,),
        ).fetchone()
        current_weights = _coerce_weights(
            json.loads(state_row["weights_json"]) if state_row and state_row["weights_json"] else None
        )
        feedback_count = int(state_row["feedback_count"]) if state_row else 0
        update_count = int(state_row["update_count"]) if state_row else 0
        latest_action = state_row["last_realized_action"] if state_row else None
        latest_return_pct = (
            float(state_row["last_realized_return_pct"])
            if state_row and state_row["last_realized_return_pct"] is not None
            else None
        )
        latest_reference_price = (
            float(state_row["last_reference_price"])
            if state_row and state_row["last_reference_price"] is not None
            else None
        )
        latest_reference_timestamp = (
            state_row["last_reference_timestamp"] if state_row else None
        )
        latest_feedback_timestamp = (
            state_row["last_feedback_timestamp"] if state_row else None
        )

        current_ts = _parse_timestamp(current_timestamp)
        if current_ts is not None and current_price is not None:
            pending_rows = conn.execute(
                """
                SELECT * FROM wrapper_pending_predictions
                WHERE symbol = ?
                ORDER BY reference_timestamp ASC, id ASC
                """,
                (normalized_symbol,),
            ).fetchall()

            for row in pending_rows:
                reference_ts = _parse_timestamp(row["reference_timestamp"])
                if reference_ts is None:
                    conn.execute(
                        "DELETE FROM wrapper_pending_predictions WHERE id = ?",
                        (int(row["id"]),),
                    )
                    continue

                elapsed_seconds = (current_ts - reference_ts).total_seconds()
                if elapsed_seconds < config.feedback_min_elapsed_seconds:
                    break

                wrapper_result = json.loads(row["wrapper_result_json"])
                previous_weights = dict(current_weights)
                realized_action, realized_return_pct = _infer_realized_action(
                    reference_price=float(row["reference_price"]),
                    realized_price=float(current_price),
                    config=config,
                )
                current_weights = update_wrapper_weights(
                    decision={},
                    realized_action=realized_action,
                    previous_result=wrapper_result,
                    previous_weights=current_weights,
                    config=config,
                )
                feedback_applied += 1
                feedback_count += 1
                update_count += 1
                latest_action = realized_action
                latest_return_pct = realized_return_pct
                latest_reference_price = float(row["reference_price"])
                latest_reference_timestamp = str(row["reference_timestamp"])
                latest_feedback_timestamp = str(current_ts.isoformat())
                latest_feedback = {
                    "symbol": normalized_symbol,
                    "referenceTimestamp": latest_reference_timestamp,
                    "realizedTimestamp": latest_feedback_timestamp,
                    "referencePrice": latest_reference_price,
                    "realizedPrice": float(current_price),
                    "realizedReturnPct": realized_return_pct,
                    "realizedAction": realized_action,
                    "elapsedSeconds": elapsed_seconds,
                }

                conn.execute(
                    """
                    INSERT INTO wrapper_feedback_events (
                        symbol,
                        reference_timestamp,
                        realized_timestamp,
                        reference_price,
                        realized_price,
                        realized_return_pct,
                        realized_action,
                        previous_weights_json,
                        updated_weights_json,
                        wrapper_result_json,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_symbol,
                        latest_reference_timestamp,
                        latest_feedback_timestamp,
                        latest_reference_price,
                        float(current_price),
                        realized_return_pct,
                        realized_action,
                        json.dumps(previous_weights, ensure_ascii=False),
                        json.dumps(current_weights, ensure_ascii=False),
                        json.dumps(_json_safe(wrapper_result), ensure_ascii=False),
                        utc_now_iso(),
                    ),
                )
                conn.execute(
                    "DELETE FROM wrapper_pending_predictions WHERE id = ?",
                    (int(row["id"]),),
                )

        if state_row or feedback_applied > 0:
            _upsert_weight_state(
                conn,
                symbol=normalized_symbol,
                weights=current_weights,
                feedback_count=feedback_count,
                update_count=update_count,
                last_realized_action=latest_action,
                last_realized_return_pct=latest_return_pct,
                last_reference_price=latest_reference_price,
                last_reference_timestamp=latest_reference_timestamp,
                last_feedback_timestamp=latest_feedback_timestamp,
            )

        conn.commit()

    metadata = {
        "source": "learned" if state_row or feedback_applied > 0 else "default",
        "feedbackApplied": feedback_applied,
        "feedbackCount": feedback_count,
        "updateCount": update_count,
        "latestFeedback": latest_feedback,
    }
    return current_weights, metadata


def store_wrapper_prediction_snapshot(
    symbol: str,
    *,
    reference_price: float,
    reference_timestamp: Any,
    wrapper_result: Dict[str, Any],
    weights_used: Dict[str, float],
) -> None:
    normalized_symbol = normalize_symbol(symbol)
    if not normalized_symbol or reference_price <= 0 or not isinstance(wrapper_result, dict):
        return

    reference_ts = _parse_timestamp(reference_timestamp)
    if reference_ts is None:
        return

    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(WRAPPER_STATE_DB_PATH) as conn:
        ensure_wrapper_state_schema(conn)
        conn.execute(
            """
            INSERT INTO wrapper_pending_predictions (
                symbol,
                reference_price,
                reference_timestamp,
                wrapper_result_json,
                weights_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_symbol,
                float(reference_price),
                str(reference_ts.isoformat()),
                json.dumps(_json_safe(wrapper_result), ensure_ascii=False),
                json.dumps(_coerce_weights(weights_used), ensure_ascii=False),
                utc_now_iso(),
            ),
        )
        conn.execute(
            """
            DELETE FROM wrapper_pending_predictions
            WHERE id IN (
                SELECT id
                FROM wrapper_pending_predictions
                WHERE symbol = ?
                ORDER BY reference_timestamp DESC, id DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (normalized_symbol, MAX_PENDING_SNAPSHOTS_PER_SYMBOL),
        )
        conn.commit()
