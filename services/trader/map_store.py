from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[2]
MODEL_CACHE_DIR = ROOT_DIR / "services" / "trader" / "model_cache"
MAP_HISTORY_DB_PATH = MODEL_CACHE_DIR / "sp500_information_map_history.sqlite3"
MAP_SNAPSHOT_DIR = MODEL_CACHE_DIR / "sp500_information_maps"
MARKET_TIMEZONE = ZoneInfo(os.getenv("NO_SLIP_MARKET_TIMEZONE", "America/Chicago"))
DEFAULT_TRAJECTORY_LOOKBACK_DAYS = int(
    os.getenv("SP500_MAP_TRAJECTORY_LOOKBACK_DAYS", "20")
)
SQLITE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def local_now() -> datetime:
    return datetime.now(MARKET_TIMEZONE)


def today_market_date() -> str:
    return local_now().date().isoformat()


def today_market_timestamp_iso() -> str:
    return local_now().isoformat()


def dated_map_snapshot_path(map_date: str) -> Path:
    return MAP_SNAPSHOT_DIR / f"{map_date}.json"


def latest_map_snapshot_path() -> Path:
    return MAP_SNAPSHOT_DIR / "latest.json"


def _safe_sqlite_identifier(identifier: str) -> str:
    normalized = str(identifier or "").strip()
    if not SQLITE_IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"Unsafe SQLite identifier: {identifier!r}")
    return f'"{normalized}"'


def ensure_map_history_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS map_runs (
            map_date TEXT PRIMARY KEY,
            generated_at TEXT NOT NULL,
            payload_path TEXT NOT NULL,
            evaluated_symbols INTEGER NOT NULL,
            failed_symbols INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS map_symbol_snapshots (
            map_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT,
            sector TEXT,
            current_price REAL,
            final_action TEXT,
            direction_score REAL,
            uncertainty_ratio REAL,
            first_moment_pct_per_day REAL,
            second_moment_bp_per_day2 REAL,
            conviction_space_x REAL,
            conviction_space_y REAL,
            momentum_space_x REAL,
            momentum_space_y REAL,
            optimization_score REAL,
            persistence_score REAL,
            stability_score REAL,
            regime_shift_risk REAL,
            first_velocity_pct_per_day REAL,
            second_velocity_bp_per_day2_per_day REAL,
            first_flip_rate REAL,
            second_flip_rate REAL,
            quadrant TEXT,
            generated_at TEXT NOT NULL,
            PRIMARY KEY (map_date, symbol)
        );

        CREATE INDEX IF NOT EXISTS idx_map_symbol_snapshots_symbol_date
            ON map_symbol_snapshots(symbol, map_date DESC);
        """
    )
    existing_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(map_symbol_snapshots)").fetchall()
    }
    for column_name in (
        "conviction_space_x",
        "conviction_space_y",
        "momentum_space_x",
        "momentum_space_y",
        "persistence_score",
        "stability_score",
        "regime_shift_risk",
        "first_velocity_pct_per_day",
        "second_velocity_bp_per_day2_per_day",
        "first_flip_rate",
        "second_flip_rate",
    ):
        if column_name not in existing_columns:
            safe_column_name = _safe_sqlite_identifier(column_name)
            conn.execute(
                f"ALTER TABLE map_symbol_snapshots ADD COLUMN {safe_column_name} REAL"
            )

    legacy_pairs = (
        ("conviction_space_x", "e_coordinate_x"),
        ("conviction_space_y", "e_coordinate_y"),
        ("momentum_space_x", "m_coordinate_x"),
        ("momentum_space_y", "m_coordinate_y"),
    )
    for new_column, legacy_column in legacy_pairs:
        if legacy_column in existing_columns:
            safe_new_column = _safe_sqlite_identifier(new_column)
            safe_legacy_column = _safe_sqlite_identifier(legacy_column)
            conn.execute(
                f"""
                UPDATE map_symbol_snapshots
                SET {safe_new_column} = COALESCE({safe_new_column}, {safe_legacy_column})
                WHERE {safe_new_column} IS NULL AND {safe_legacy_column} IS NOT NULL
                """
            )


def _to_float(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(numeric):
        return None
    return numeric


def _clip01(value: float) -> float:
    return float(max(0.0, min(1.0, value)))


def _finite_series(values: Iterable[Optional[float]]) -> np.ndarray:
    return np.array(
        [value for value in values if value is not None and np.isfinite(value)],
        dtype=float,
    )


def _series_diffs(values: Iterable[Optional[float]]) -> np.ndarray:
    arr = _finite_series(values)
    if arr.size < 2:
        return np.array([], dtype=float)
    return np.diff(arr)


def _series_sign_flip_rate(values: Iterable[Optional[float]]) -> float:
    arr = _finite_series(values)
    if arr.size < 2:
        return 0.0
    signs = np.sign(arr)
    signs = signs[signs != 0]
    if signs.size < 2:
        return 0.0
    flips = np.sum(signs[1:] != signs[:-1])
    return float(flips / max(1, signs.size - 1))


def _latest_velocity(values: Iterable[Optional[float]]) -> Optional[float]:
    diffs = _series_diffs(values)
    if diffs.size == 0:
        return None
    tail = diffs[-min(3, diffs.size) :]
    return float(np.mean(tail))


def _normalized_stability(
    values: Iterable[Optional[float]],
    *,
    base_scale: float,
) -> float:
    arr = _finite_series(values)
    if arr.size <= 1:
        return 0.5
    diffs = np.diff(arr)
    mean_scale = max(
        base_scale,
        float(np.nanmean(np.abs(arr))) * 0.35,
        float(np.nanmean(np.abs(diffs))) * 0.65 if diffs.size else base_scale,
    )
    std_component = float(np.nanstd(arr)) / max(mean_scale, 1e-9)
    drift_component = (
        float(np.nanmean(np.abs(diffs))) / max(mean_scale, 1e-9)
        if diffs.size
        else 0.0
    )
    return _clip01(1.0 / (1.0 + std_component + drift_component))


def _latest_shock_ratio(values: Iterable[Optional[float]], *, floor: float) -> float:
    diffs = _series_diffs(values)
    if diffs.size < 2:
        return 0.0
    baseline = diffs[:-1]
    baseline_center = float(np.mean(baseline))
    baseline_scale = max(
        floor,
        float(np.std(baseline)) if baseline.size > 1 else 0.0,
        float(np.mean(np.abs(baseline))) if baseline.size else 0.0,
    )
    latest = float(diffs[-1])
    return _clip01(abs(latest - baseline_center) / (3.0 * baseline_scale))


def _sign_consistency(values: Iterable[Optional[float]]) -> float:
    arr = _finite_series(values)
    if arr.size == 0:
        return 0.5
    latest_sign = np.sign(arr[-1])
    if latest_sign == 0:
        return 0.5
    comparable = np.sign(arr[arr != 0])
    if comparable.size == 0:
        return 0.5
    return float(np.mean(comparable == latest_sign))


def compute_symbol_trajectory_metrics(
    history_rows: List[Dict[str, Any]],
    *,
    current_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    series: List[Dict[str, Any]] = [dict(row) for row in history_rows]

    if current_snapshot is not None:
        current_date = str(
            current_snapshot.get("mapDate")
            or current_snapshot.get("analysisDate")
            or today_market_date()
        )
        current_row = {
            "map_date": current_date,
            "first_moment_pct_per_day": _to_float(
                current_snapshot.get("firstMomentPctPerDay")
            ),
            "second_moment_bp_per_day2": _to_float(
                current_snapshot.get("secondMomentBpPerDay2")
            ),
            "momentum_space_x": _to_float(
                ((current_snapshot.get("firstCoordinateSpace") or current_snapshot.get("momentumSpace") or {}).get("x"))
            ),
            "momentum_space_y": _to_float(
                ((current_snapshot.get("firstCoordinateSpace") or current_snapshot.get("momentumSpace") or {}).get("y"))
            ),
            "conviction_space_x": _to_float(
                ((current_snapshot.get("secondCoordinateSpace") or current_snapshot.get("convictionSpace") or {}).get("x"))
            ),
            "conviction_space_y": _to_float(
                ((current_snapshot.get("secondCoordinateSpace") or current_snapshot.get("convictionSpace") or {}).get("y"))
            ),
            "uncertainty_ratio": _to_float(current_snapshot.get("uncertaintyRatio")),
            "optimization_score": _to_float(current_snapshot.get("optimizationScore")),
            "final_action": current_snapshot.get("finalAction"),
        }
        if not series or str(series[-1].get("map_date")) != current_date:
            series.append(current_row)
        else:
            series[-1] = {**series[-1], **current_row}

    first_values = [row.get("first_moment_pct_per_day") for row in series]
    second_values = [row.get("second_moment_bp_per_day2") for row in series]
    momentum_x_values = [row.get("momentum_space_x") for row in series]
    momentum_y_values = [row.get("momentum_space_y") for row in series]
    conviction_x_values = [row.get("conviction_space_x") for row in series]
    conviction_y_values = [row.get("conviction_space_y") for row in series]
    uncertainty_values = [row.get("uncertainty_ratio") for row in series]

    first_flip_rate = _series_sign_flip_rate(first_values)
    second_flip_rate = _series_sign_flip_rate(second_values)
    first_velocity = _latest_velocity(first_values)
    second_velocity = _latest_velocity(second_values)
    momentum_x_drift = _latest_velocity(momentum_x_values)
    momentum_y_drift = _latest_velocity(momentum_y_values)
    conviction_x_drift = _latest_velocity(conviction_x_values)
    conviction_y_drift = _latest_velocity(conviction_y_values)

    first_stability = _normalized_stability(first_values, base_scale=0.08)
    second_stability = _normalized_stability(second_values, base_scale=12.0)
    stability_score = _clip01(first_stability * 0.55 + second_stability * 0.45)

    first_shock = _latest_shock_ratio(first_values, floor=0.02)
    second_shock = _latest_shock_ratio(second_values, floor=4.0)
    sign_consistency = _clip01(
        (_sign_consistency(first_values) + _sign_consistency(second_values)) / 2.0
    )

    continuation_bias = 0.0
    latest_first = _to_float(first_values[-1]) if first_values else None
    latest_second = _to_float(second_values[-1]) if second_values else None
    if latest_first is not None and first_velocity is not None:
        continuation_bias += 0.6 if latest_first * first_velocity >= 0 else -0.6
    if latest_second is not None and second_velocity is not None:
        continuation_bias += 0.4 if latest_second * second_velocity >= 0 else -0.4
    continuation_bias = float(max(-1.0, min(1.0, continuation_bias)))

    uncertainty_mean = float(np.mean(_finite_series(uncertainty_values))) if _finite_series(uncertainty_values).size else 0.0
    uncertainty_penalty = _clip01(uncertainty_mean / 0.08)
    avg_flip_rate = (first_flip_rate + second_flip_rate) / 2.0
    avg_shock = (first_shock + second_shock) / 2.0

    persistence_score = _clip01(
        stability_score * 0.4
        + sign_consistency * 0.3
        + ((continuation_bias + 1.0) / 2.0) * 0.15
        + (1.0 - avg_shock) * 0.15
    )
    regime_shift_risk = _clip01(
        avg_flip_rate * 0.35
        + avg_shock * 0.3
        + (1.0 - stability_score) * 0.2
        + uncertainty_penalty * 0.15
    )

    if regime_shift_risk >= 0.72:
        regime_label = "regime transition"
    elif persistence_score >= 0.7 and continuation_bias >= 0.0:
        regime_label = "stable continuation"
    elif persistence_score >= 0.6:
        regime_label = "stable but slowing"
    else:
        regime_label = "fragile regime"

    return {
        "daysObserved": len(series),
        "stabilityScore": stability_score,
        "persistenceScore": persistence_score,
        "regimeShiftRisk": regime_shift_risk,
        "continuationBias": continuation_bias,
        "signConsistency": sign_consistency,
        "firstVelocityPctPerDay": first_velocity,
        "secondVelocityBpPerDay2PerDay": second_velocity,
        "firstFlipRate": first_flip_rate,
        "secondFlipRate": second_flip_rate,
        "momentumSpaceDrift": {
            "x": momentum_x_drift,
            "y": momentum_y_drift,
        },
        "convictionSpaceDrift": {
            "x": conviction_x_drift,
            "y": conviction_y_drift,
        },
        "regimeLabel": regime_label,
    }


def load_recent_symbol_information_map_rows(
    symbols: Iterable[str],
    *,
    lookback_days: int = DEFAULT_TRAJECTORY_LOOKBACK_DAYS,
) -> Dict[str, List[Dict[str, Any]]]:
    normalized_symbols = sorted(
        {
            (symbol or "").strip().upper().replace(".", "-")
            for symbol in symbols
            if (symbol or "").strip()
        }
    )
    if not normalized_symbols or not MAP_HISTORY_DB_PATH.exists():
        return {symbol: [] for symbol in normalized_symbols}

    cutoff_date = (
        local_now().date() - timedelta(days=max(1, lookback_days) - 1)
    ).isoformat()
    placeholders = ",".join("?" for _ in normalized_symbols)

    with sqlite3.connect(MAP_HISTORY_DB_PATH) as conn:
        ensure_map_history_schema(conn)
        rows = conn.execute(
            f"""
            SELECT
                map_date,
                symbol,
                first_moment_pct_per_day,
                second_moment_bp_per_day2,
                momentum_space_x,
                momentum_space_y,
                conviction_space_x,
                conviction_space_y,
                uncertainty_ratio,
                optimization_score,
                final_action
            FROM map_symbol_snapshots
            WHERE symbol IN ({placeholders}) AND map_date >= ?
            ORDER BY symbol ASC, map_date ASC
            """,
            (*normalized_symbols, cutoff_date),
        ).fetchall()

    grouped: Dict[str, List[Dict[str, Any]]] = {
        symbol: [] for symbol in normalized_symbols
    }
    for row in rows:
        grouped[row[1]].append(
            {
                "map_date": row[0],
                "symbol": row[1],
                "first_moment_pct_per_day": _to_float(row[2]),
                "second_moment_bp_per_day2": _to_float(row[3]),
                "momentum_space_x": _to_float(row[4]),
                "momentum_space_y": _to_float(row[5]),
                "conviction_space_x": _to_float(row[6]),
                "conviction_space_y": _to_float(row[7]),
                "uncertainty_ratio": _to_float(row[8]),
                "optimization_score": _to_float(row[9]),
                "final_action": row[10],
            }
        )
    return grouped


def persist_information_map_history(payload: Dict[str, Any]) -> Dict[str, Any]:
    map_date = str(payload.get("mapDate") or today_market_date())
    generated_at = str(payload.get("generatedAt") or datetime.now(timezone.utc).isoformat())

    MAP_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    dated_path = dated_map_snapshot_path(map_date)
    latest_path = latest_map_snapshot_path()
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    dated_path.write_text(serialized, encoding="utf-8")
    latest_path.write_text(serialized, encoding="utf-8")

    points = payload.get("points") or []
    universe = payload.get("universe") or {}

    with sqlite3.connect(MAP_HISTORY_DB_PATH) as conn:
        ensure_map_history_schema(conn)
        conn.execute(
            """
            INSERT INTO map_runs (map_date, generated_at, payload_path, evaluated_symbols, failed_symbols)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(map_date) DO UPDATE SET
                generated_at=excluded.generated_at,
                payload_path=excluded.payload_path,
                evaluated_symbols=excluded.evaluated_symbols,
                failed_symbols=excluded.failed_symbols
            """,
            (
                map_date,
                generated_at,
                str(dated_path),
                int(universe.get("evaluatedSymbols") or len(points)),
                int(universe.get("failedSymbols") or 0),
            ),
        )

        conn.execute("DELETE FROM map_symbol_snapshots WHERE map_date = ?", (map_date,))
        for point in points:
            momentum_space = point.get("firstCoordinateSpace") or point.get("momentumSpace") or {}
            conviction_space = point.get("secondCoordinateSpace") or point.get("convictionSpace") or {}
            trajectory = point.get("trajectory") or {}
            conn.execute(
                """
                INSERT INTO map_symbol_snapshots (
                    map_date, symbol, name, sector, current_price, final_action, direction_score,
                    uncertainty_ratio, first_moment_pct_per_day, second_moment_bp_per_day2,
                    conviction_space_x, conviction_space_y, momentum_space_x, momentum_space_y,
                    optimization_score, persistence_score, stability_score, regime_shift_risk,
                    first_velocity_pct_per_day, second_velocity_bp_per_day2_per_day,
                    first_flip_rate, second_flip_rate, quadrant, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    map_date,
                    point.get("symbol"),
                    point.get("name"),
                    point.get("sector"),
                    _to_float(point.get("currentPrice")),
                    point.get("finalAction"),
                    _to_float(point.get("directionScore")),
                    _to_float(point.get("uncertaintyRatio")),
                    _to_float(point.get("firstMomentPctPerDay")),
                    _to_float(point.get("secondMomentBpPerDay2")),
                    _to_float(conviction_space.get("x")),
                    _to_float(conviction_space.get("y")),
                    _to_float(momentum_space.get("x")),
                    _to_float(momentum_space.get("y")),
                    _to_float(point.get("optimizationScore")),
                    _to_float(trajectory.get("persistenceScore")),
                    _to_float(trajectory.get("stabilityScore")),
                    _to_float(trajectory.get("regimeShiftRisk")),
                    _to_float(trajectory.get("firstVelocityPctPerDay")),
                    _to_float(trajectory.get("secondVelocityBpPerDay2PerDay")),
                    _to_float(trajectory.get("firstFlipRate")),
                    _to_float(trajectory.get("secondFlipRate")),
                    point.get("quadrant"),
                    generated_at,
                ),
            )

        conn.commit()

    return {
        "mapDate": map_date,
        "datedPath": str(dated_path),
        "latestPath": str(latest_path),
    }


def _summarize_series(values: Iterable[Optional[float]]) -> Dict[str, Optional[float]]:
    arr = np.array(
        [value for value in values if value is not None and np.isfinite(value)],
        dtype=float,
    )
    if arr.size == 0:
        return {"mean": None, "std": None, "min": None, "max": None}
    return {
        "mean": float(arr.mean()),
        "std": float(arr.std()),
        "min": float(arr.min()),
        "max": float(arr.max()),
    }


def load_symbol_information_map_summary(
    symbol: str,
    *,
    lookback_days: int = 30,
) -> Optional[Dict[str, Any]]:
    normalized_symbol = (symbol or "").strip().upper().replace(".", "-")
    if not normalized_symbol or not MAP_HISTORY_DB_PATH.exists():
        return None

    cutoff_date = (local_now().date() - timedelta(days=max(1, lookback_days) - 1)).isoformat()

    rows = load_recent_symbol_information_map_rows(
        [normalized_symbol],
        lookback_days=lookback_days,
    ).get(normalized_symbol, [])

    if not rows:
        return None

    first_summary = _summarize_series(row.get("first_moment_pct_per_day") for row in rows)
    second_summary = _summarize_series(row.get("second_moment_bp_per_day2") for row in rows)
    conviction_x_summary = _summarize_series(row.get("conviction_space_x") for row in rows)
    conviction_y_summary = _summarize_series(row.get("conviction_space_y") for row in rows)
    direction_summary = _summarize_series(row.get("direction_score") for row in rows)
    uncertainty_summary = _summarize_series(row.get("uncertainty_ratio") for row in rows)
    optimization_summary = _summarize_series(row.get("optimization_score") for row in rows)
    trajectory_summary = compute_symbol_trajectory_metrics(rows)

    positive_first_share = float(
        np.mean([1.0 if (row.get("first_moment_pct_per_day") or 0.0) > 0 else 0.0 for row in rows])
    )
    positive_second_share = float(
        np.mean([1.0 if (row.get("second_moment_bp_per_day2") or 0.0) > 0 else 0.0 for row in rows])
    )
    buy_share = float(np.mean([1.0 if row.get("final_action") == "BUY" else 0.0 for row in rows]))

    first_std = first_summary["std"] or 0.0
    second_std = second_summary["std"] or 0.0
    uncertainty_mean = uncertainty_summary["mean"] or 0.0
    persistence_score = float(trajectory_summary.get("persistenceScore") or 0.5)
    stability_score = float(trajectory_summary.get("stabilityScore") or 0.5)
    regime_shift_risk = float(trajectory_summary.get("regimeShiftRisk") or 0.5)
    volatility_index = min(
        1.5,
        abs(first_std) * 8.0
        + abs(second_std) / 24.0
        + uncertainty_mean * 3.0
        + regime_shift_risk * 0.35,
    )
    preferred_changepoint_scale = float(
        min(
            0.06,
            max(
                0.008,
                0.011
                + volatility_index * 0.022
                + regime_shift_risk * 0.012
                - persistence_score * 0.006
                - stability_score * 0.004,
            ),
        )
    )

    return {
        "symbol": normalized_symbol,
        "daysObserved": len(rows),
        "dateRange": {
            "start": rows[0]["map_date"],
            "end": rows[-1]["map_date"],
        },
        "momentumSpace": {
            "x": first_summary,
            "y": second_summary,
        },
        "convictionSpace": {
            "x": conviction_x_summary,
            "y": conviction_y_summary,
        },
        "directionScore": direction_summary,
        "uncertaintyRatio": uncertainty_summary,
        "optimizationScore": optimization_summary,
        "positiveFirstShare": positive_first_share,
        "positiveSecondShare": positive_second_share,
        "buyShare": buy_share,
        "volatilityIndex": volatility_index,
        "preferredChangepointScale": preferred_changepoint_scale,
        "trajectory": trajectory_summary,
    }
