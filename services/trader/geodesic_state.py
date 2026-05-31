from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from services.trader.map_store import (
    compute_symbol_trajectory_metrics,
    load_recent_symbol_information_map_rows,
    today_market_date,
)

DEFAULT_GEODESIC_LOOKBACK_DAYS = 24


def _safe_float(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(numeric):
        return None
    return numeric


def _clip01(value: float) -> float:
    return float(max(0.0, min(1.0, value)))


def _build_first_coordinate_space(
    first_moment_pct_per_day: Optional[float],
    second_moment_bp_per_day2: Optional[float],
) -> Dict[str, Optional[float]]:
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
        "x": _safe_float(transformed_first),
        "y": _safe_float(transformed_second),
    }


def _build_second_coordinate_space(
    first_moment_pct_per_day: Optional[float],
    second_moment_bp_per_day2: Optional[float],
    uncertainty_ratio: Optional[float],
) -> Dict[str, Optional[float]]:
    uncertainty_scale = max(0.01, _safe_float(uncertainty_ratio) or 0.01)
    return {
        "x": _safe_float(first_moment_pct_per_day / uncertainty_scale)
        if first_moment_pct_per_day is not None
        else None,
        "y": _safe_float(second_moment_bp_per_day2 / uncertainty_scale)
        if second_moment_bp_per_day2 is not None
        else None,
    }


def _vector_from_row(row: Dict[str, Any]) -> Optional[np.ndarray]:
    values = [
        _safe_float(row.get("momentum_space_x")),
        _safe_float(row.get("momentum_space_y")),
        _safe_float(row.get("conviction_space_x")),
        _safe_float(row.get("conviction_space_y")),
    ]
    if any(value is None for value in values):
        return None
    return np.asarray(values, dtype=float)


def estimate_symbol_geodesic_state(
    *,
    symbol: Optional[str],
    first_moment_pct_per_hour: Optional[float],
    second_moment_pct_per_hour2: Optional[float],
    uncertainty_ratio: Optional[float],
    lookback_days: int = DEFAULT_GEODESIC_LOOKBACK_DAYS,
    map_date: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_symbol = str(symbol or "").strip().upper().replace(".", "-")
    if not normalized_symbol:
        return {
            "available": False,
            "label": "geodesic unavailable",
            "actionBias": "hold",
        }

    first_moment_pct_per_day = (
        _safe_float(first_moment_pct_per_hour) * 24.0
        if _safe_float(first_moment_pct_per_hour) is not None
        else None
    )
    second_moment_bp_per_day2 = (
        _safe_float(second_moment_pct_per_hour2) * 10_000.0 * 24.0 * 24.0
        if _safe_float(second_moment_pct_per_hour2) is not None
        else None
    )

    first_space = _build_first_coordinate_space(
        first_moment_pct_per_day,
        second_moment_bp_per_day2,
    )
    second_space = _build_second_coordinate_space(
        first_moment_pct_per_day,
        second_moment_bp_per_day2,
        uncertainty_ratio,
    )
    current_row = {
        "map_date": map_date or today_market_date(),
        "symbol": normalized_symbol,
        "first_moment_pct_per_day": first_moment_pct_per_day,
        "second_moment_bp_per_day2": second_moment_bp_per_day2,
        "momentum_space_x": first_space.get("x"),
        "momentum_space_y": first_space.get("y"),
        "conviction_space_x": second_space.get("x"),
        "conviction_space_y": second_space.get("y"),
        "uncertainty_ratio": _safe_float(uncertainty_ratio),
        "optimization_score": None,
        "final_action": None,
    }
    current_vector = _vector_from_row(current_row)
    if current_vector is None:
        return {
            "available": False,
            "label": "geodesic unavailable",
            "actionBias": "hold",
            "currentFirstCoordinateX": first_space.get("x"),
            "currentFirstCoordinateY": first_space.get("y"),
            "currentSecondCoordinateX": second_space.get("x"),
            "currentSecondCoordinateY": second_space.get("y"),
        }

    history_rows = load_recent_symbol_information_map_rows(
        [normalized_symbol],
        lookback_days=max(6, lookback_days),
    ).get(normalized_symbol, [])
    if history_rows and str(history_rows[-1].get("map_date")) == current_row["map_date"]:
        history_rows = history_rows[:-1]

    sequence_rows: List[Dict[str, Any]] = [dict(row) for row in history_rows]
    sequence_rows.append(current_row)
    vectors = [vector for row in sequence_rows if (vector := _vector_from_row(row)) is not None]
    if len(vectors) < 4:
        return {
            "available": False,
            "label": "geodesic warming up",
            "actionBias": "hold",
            "historyCount": max(0, len(vectors) - 1),
            "currentFirstCoordinateX": first_space.get("x"),
            "currentFirstCoordinateY": first_space.get("y"),
            "currentSecondCoordinateX": second_space.get("x"),
            "currentSecondCoordinateY": second_space.get("y"),
        }

    points = np.vstack(vectors)
    diffs = np.diff(points, axis=0)
    if diffs.shape[0] < 2:
        return {
            "available": False,
            "label": "geodesic warming up",
            "actionBias": "hold",
            "historyCount": max(0, len(vectors) - 1),
            "currentFirstCoordinateX": first_space.get("x"),
            "currentFirstCoordinateY": first_space.get("y"),
            "currentSecondCoordinateX": second_space.get("x"),
            "currentSecondCoordinateY": second_space.get("y"),
        }

    tail = diffs[-min(3, len(diffs)) :]
    previous_tail = diffs[-min(5, len(diffs)) : -1] if len(diffs) > 1 else diffs[-1:]
    tangent = np.mean(tail, axis=0)
    previous_tangent = np.mean(previous_tail, axis=0) if len(previous_tail) else tangent
    tangent_norm = float(np.linalg.norm(tangent))
    previous_tangent_norm = float(np.linalg.norm(previous_tangent))
    path_length = float(np.sum(np.linalg.norm(diffs, axis=1)))

    centered_diffs = diffs - np.mean(diffs, axis=0, keepdims=True)
    _, singular_values, vh = np.linalg.svd(centered_diffs, full_matrices=False)
    principal_direction = vh[0] if len(vh) else tangent
    principal_norm = float(np.linalg.norm(principal_direction))

    tangent_unit = tangent / max(tangent_norm, 1e-9)
    principal_unit = principal_direction / max(principal_norm, 1e-9)
    alignment_signed = float(np.dot(tangent_unit, principal_unit))
    alignment_score = abs(alignment_signed)

    curvature = float(np.linalg.norm(tangent - previous_tangent) / max(tangent_norm, 1e-6))
    acceleration = tangent - previous_tangent
    projected_next = points[-1] + tangent + 0.5 * acceleration
    predicted_previous = points[-2] + previous_tangent
    deviation = float(np.linalg.norm(points[-1] - predicted_previous))
    scale = max(0.25, float(np.mean(np.linalg.norm(diffs, axis=1))))
    normalized_deviation = float(deviation / scale)

    trajectory = compute_symbol_trajectory_metrics(
        history_rows,
        current_snapshot={
            "mapDate": current_row["map_date"],
            "firstMomentPctPerDay": first_moment_pct_per_day,
            "secondMomentBpPerDay2": second_moment_bp_per_day2,
            "firstCoordinateSpace": {
                "x": first_space.get("x"),
                "y": first_space.get("y"),
            },
            "secondCoordinateSpace": {
                "x": second_space.get("x"),
                "y": second_space.get("y"),
            },
        },
    )
    persistence = float(trajectory.get("persistenceScore") or 0.5)
    stability = float(trajectory.get("stabilityScore") or 0.5)
    regime_risk = float(trajectory.get("regimeShiftRisk") or 0.5)

    continuation_score = _clip01(
        0.34 * alignment_score
        + 0.24 * persistence
        + 0.18 * stability
        + 0.10 * max(0.0, alignment_signed)
        + 0.14 * (1.0 / (1.0 + curvature))
        - 0.16 * min(1.0, normalized_deviation / 2.5)
        - 0.10 * regime_risk
    )
    geodesic_confidence = _clip01(
        0.30
        + 0.28 * alignment_score
        + 0.20 * stability
        + 0.12 * persistence
        + 0.10 * (1.0 / (1.0 + normalized_deviation))
    )

    x_drift = float(projected_next[0] - points[-1][0])
    y_drift = float(projected_next[1] - points[-1][1])
    conviction_x_drift = float(projected_next[2] - points[-1][2])
    conviction_y_drift = float(projected_next[3] - points[-1][3])

    buy_pressure = continuation_score + max(0.0, x_drift) * 0.55 + max(0.0, conviction_x_drift) * 0.18
    sell_pressure = (
        (1.0 - continuation_score) * 0.35
        + max(0.0, -x_drift) * 0.55
        + max(0.0, -conviction_x_drift) * 0.18
        + min(1.0, curvature * 0.22)
        + min(1.0, normalized_deviation * 0.16)
    )

    if buy_pressure > sell_pressure + 0.08:
        action_bias = "buy"
    elif sell_pressure > buy_pressure + 0.08:
        action_bias = "sell"
    else:
        action_bias = "hold"

    if continuation_score >= 0.72 and curvature <= 0.7:
        label = "smooth continuation geodesic"
    elif action_bias == "sell" and (curvature >= 0.9 or normalized_deviation >= 1.0):
        label = "bending geodesic warning"
    elif action_bias == "buy":
        label = "constructive geodesic drift"
    else:
        label = "mixed geodesic regime"

    return {
        "available": True,
        "label": label,
        "actionBias": action_bias,
        "historyCount": len(vectors) - 1,
        "pathLength": path_length,
        "curvature": curvature,
        "alignmentScore": alignment_score,
        "alignmentSigned": alignment_signed,
        "deviationScore": normalized_deviation,
        "continuationScore": continuation_score,
        "confidence": geodesic_confidence,
        "currentFirstCoordinateX": float(points[-1][0]),
        "currentFirstCoordinateY": float(points[-1][1]),
        "currentSecondCoordinateX": float(points[-1][2]),
        "currentSecondCoordinateY": float(points[-1][3]),
        "projectedFirstCoordinateX": float(projected_next[0]),
        "projectedFirstCoordinateY": float(projected_next[1]),
        "projectedSecondCoordinateX": float(projected_next[2]),
        "projectedSecondCoordinateY": float(projected_next[3]),
        "projectedFirstCoordinateDrift": x_drift,
        "projectedSecondCoordinateDrift": conviction_x_drift,
        "projectedFirstCoordinateYDrift": y_drift,
        "projectedSecondCoordinateYDrift": conviction_y_drift,
        "stabilityScore": stability,
        "persistenceScore": persistence,
        "regimeShiftRisk": regime_risk,
        "trajectoryRegimeLabel": trajectory.get("regimeLabel"),
        "singularValues": [float(value) for value in singular_values[:4]],
    }
