from __future__ import annotations

import json
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

class PandasAndNumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (pd.Timestamp, datetime, date)):
            return obj.isoformat()
        if isinstance(obj, timedelta):
            return str(obj)
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)

from services.trader.map_store import MODEL_CACHE_DIR, MARKET_TIMEZONE, today_market_date

PORTFOLIO_HISTORY_DB_PATH = MODEL_CACHE_DIR / "sp500_portfolio_history.sqlite3"
PORTFOLIO_SNAPSHOT_DIR = MODEL_CACHE_DIR / "sp500_portfolios"
DEFAULT_PORTFOLIO_LOOKBACK_DAYS = 90
DEFAULT_MANIFOLD_RANK = 4
DEFAULT_HISTORY_LIMIT = 120

BASE_FEATURE_KEYS = [
    "weighted_upside_pct",
    "weighted_uncertainty_pct",
    "weighted_volatility_pct",
    "weighted_drawdown_linger_days",
    "weighted_max_drawdown_pct",
    "weighted_persistence_pct",
    "weighted_regime_risk_pct",
    "geometry_alignment_score",
    "geometry_distance",
    "geometry_kl_divergence",
    "geometry_target_x",
    "geometry_target_y",
    "geometry_portfolio_x",
    "geometry_portfolio_y",
    "us_equities_pct",
    "treasuries_pct",
    "gold_pct",
    "cash_pct",
    "sector_count",
    "turnover_high_ratio",
    "turnover_medium_ratio",
    "concentration_hhi",
    "top1_weight_pct",
    "top3_weight_pct",
    "natural_gradient_upper_bound_score",
    "natural_gradient_live_distance_to_target",
    "natural_gradient_live_distance_to_bound",
    "natural_gradient_live_entropy",
    "natural_gradient_fisher_trace",
]

PROJECTED_FEATURE_KEYS = [
    "weighted_upside_pct",
    "weighted_uncertainty_pct",
    "weighted_volatility_pct",
    "weighted_drawdown_linger_days",
    "weighted_persistence_pct",
    "weighted_regime_risk_pct",
    "geometry_alignment_score",
    "geometry_distance",
    "us_equities_pct",
    "concentration_hhi",
    "natural_gradient_upper_bound_score",
    "natural_gradient_live_distance_to_target",
    "natural_gradient_live_distance_to_bound",
]

FEATURE_LABELS = {
    "weighted_upside_pct": "weightedUpsidePct",
    "weighted_uncertainty_pct": "weightedUncertaintyPct",
    "weighted_volatility_pct": "weightedVolatilityPct",
    "weighted_drawdown_linger_days": "weightedDrawdownLingerDays",
    "weighted_max_drawdown_pct": "weightedMaxDrawdownPct",
    "weighted_persistence_pct": "weightedPersistencePct",
    "weighted_regime_risk_pct": "weightedRegimeRiskPct",
    "geometry_alignment_score": "geometryAlignmentScore",
    "geometry_distance": "geometryDistance",
    "geometry_kl_divergence": "geometryKlDivergence",
    "geometry_target_x": "geometryTargetX",
    "geometry_target_y": "geometryTargetY",
    "geometry_portfolio_x": "geometryPortfolioX",
    "geometry_portfolio_y": "geometryPortfolioY",
    "us_equities_pct": "usEquitiesPct",
    "treasuries_pct": "treasuriesPct",
    "gold_pct": "goldPct",
    "cash_pct": "cashPct",
    "sector_count": "sectorCount",
    "turnover_high_ratio": "turnoverHighRatio",
    "turnover_medium_ratio": "turnoverMediumRatio",
    "concentration_hhi": "concentrationHhi",
    "top1_weight_pct": "top1WeightPct",
    "top3_weight_pct": "top3WeightPct",
    "natural_gradient_upper_bound_score": "naturalGradientUpperBoundScore",
    "natural_gradient_live_distance_to_target": "naturalGradientLiveDistanceToTarget",
    "natural_gradient_live_distance_to_bound": "naturalGradientLiveDistanceToBound",
    "natural_gradient_live_entropy": "naturalGradientLiveEntropy",
    "natural_gradient_fisher_trace": "naturalGradientFisherTrace",
}


def safe_float(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(numeric):
        return None
    return float(numeric)


def portfolio_snapshot_path(map_date: str) -> Path:
    return PORTFOLIO_SNAPSHOT_DIR / f"{map_date}.json"


def ensure_portfolio_history_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS portfolio_runs (
            map_date TEXT PRIMARY KEY,
            generated_at TEXT NOT NULL,
            payload_path TEXT NOT NULL,
            profile_name TEXT,
            champion_score REAL,
            features_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_portfolio_runs_generated_at
            ON portfolio_runs(generated_at DESC);
        """
    )


def _sleeve_weight(payload: Dict[str, Any], label: str) -> float:
    allocation = payload.get("allocation") or {}
    sleeves = allocation.get("sleeves") or []
    for sleeve in sleeves:
        if str(sleeve.get("label") or "").strip().lower() == label.strip().lower():
            return safe_float(sleeve.get("weightPct")) or 0.0
    return 0.0


def _turnover_ratio(summary: Dict[str, Any], key: str, holdings_count: int) -> float:
    mix = summary.get("turnoverMix") or {}
    if holdings_count <= 0:
        return 0.0
    return float((safe_float(mix.get(key)) or 0.0) / holdings_count)


def _holding_concentration(holdings: List[Dict[str, Any]]) -> Dict[str, float]:
    weights = np.asarray(
        [
            safe_float(holding.get("portfolioWeightPct"))
            or safe_float(holding.get("weightPct"))
            or ((safe_float(holding.get("weight")) or 0.0) * 100.0)
            for holding in holdings
        ],
        dtype=float,
    )
    if weights.size == 0:
        return {"concentration_hhi": 0.0, "top1_weight_pct": 0.0, "top3_weight_pct": 0.0}

    weights = np.clip(weights, 0.0, None)
    total = weights.sum()
    if total <= 0:
        return {"concentration_hhi": 0.0, "top1_weight_pct": 0.0, "top3_weight_pct": 0.0}
    normalized = weights / total
    ordered = np.sort(normalized)[::-1]
    return {
        "concentration_hhi": float(np.sum(np.square(normalized))),
        "top1_weight_pct": float(ordered[:1].sum() * 100.0),
        "top3_weight_pct": float(ordered[:3].sum() * 100.0),
    }


def extract_portfolio_features(payload: Dict[str, Any]) -> Dict[str, float]:
    summary = payload.get("summary") or {}
    geometry = payload.get("geometry") or {}
    natural_gradient = payload.get("naturalGradient") or {}
    holdings = payload.get("holdings") or []
    holdings_count = int(safe_float(summary.get("holdingsCount")) or len(holdings) or 0)
    concentration = _holding_concentration(holdings)

    return {
        "weighted_upside_pct": safe_float(summary.get("weightedUpsidePct")) or 0.0,
        "weighted_uncertainty_pct": safe_float(summary.get("weightedUncertaintyPct")) or 0.0,
        "weighted_volatility_pct": safe_float(summary.get("weightedVolatilityPct")) or 0.0,
        "weighted_drawdown_linger_days": safe_float(summary.get("weightedDrawdownLingerDays")) or 0.0,
        "weighted_max_drawdown_pct": safe_float(summary.get("weightedMaxDrawdownPct")) or 0.0,
        "weighted_persistence_pct": safe_float(summary.get("weightedPersistencePct")) or 0.0,
        "weighted_regime_risk_pct": safe_float(summary.get("weightedRegimeRiskPct")) or 0.0,
        "geometry_alignment_score": safe_float(geometry.get("alignmentScore")) or 0.0,
        "geometry_distance": safe_float(geometry.get("portfolioDistance")) or 0.0,
        "geometry_kl_divergence": safe_float(geometry.get("portfolioKlDivergence")) or 0.0,
        "geometry_target_x": safe_float((geometry.get("targetPoint") or {}).get("x")) or 0.0,
        "geometry_target_y": safe_float((geometry.get("targetPoint") or {}).get("y")) or 0.0,
        "geometry_portfolio_x": safe_float((geometry.get("portfolioPoint") or {}).get("x")) or 0.0,
        "geometry_portfolio_y": safe_float((geometry.get("portfolioPoint") or {}).get("y")) or 0.0,
        "us_equities_pct": _sleeve_weight(payload, "U.S. equities"),
        "treasuries_pct": _sleeve_weight(payload, "Treasuries / IG bonds"),
        "gold_pct": _sleeve_weight(payload, "Gold / real assets"),
        "cash_pct": _sleeve_weight(payload, "Cash / short duration"),
        "sector_count": safe_float(summary.get("sectorCount")) or 0.0,
        "turnover_high_ratio": _turnover_ratio(summary, "high", holdings_count),
        "turnover_medium_ratio": _turnover_ratio(summary, "medium", holdings_count),
        "concentration_hhi": concentration["concentration_hhi"],
        "top1_weight_pct": concentration["top1_weight_pct"],
        "top3_weight_pct": concentration["top3_weight_pct"],
        "natural_gradient_upper_bound_score": safe_float(
            natural_gradient.get("upperBoundScore")
        )
        or 0.0,
        "natural_gradient_live_distance_to_target": safe_float(
            natural_gradient.get("liveDistanceToTarget")
        )
        or 0.0,
        "natural_gradient_live_distance_to_bound": safe_float(
            natural_gradient.get("liveDistanceToBound")
        )
        or 0.0,
        "natural_gradient_live_entropy": safe_float(natural_gradient.get("liveEntropy")) or 0.0,
        "natural_gradient_fisher_trace": safe_float(
            natural_gradient.get("fisherTrace")
        )
        or 0.0,
    }


def persist_portfolio_history(payload: Dict[str, Any]) -> Dict[str, str]:
    if not payload.get("ok"):
        return {}

    map_date = str(payload.get("mapDate") or today_market_date())
    generated_at = str(payload.get("generatedAt") or datetime.now(MARKET_TIMEZONE).isoformat())
    snapshot_path = portfolio_snapshot_path(map_date)
    features = extract_portfolio_features(payload)
    champion_agent = payload.get("championAgent") or {}

    PORTFOLIO_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(payload, cls=PandasAndNumpyEncoder, ensure_ascii=False, indent=2), encoding="utf-8")

    PORTFOLIO_HISTORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(PORTFOLIO_HISTORY_DB_PATH) as conn:
        ensure_portfolio_history_schema(conn)
        conn.execute(
            """
            INSERT INTO portfolio_runs (
                map_date, generated_at, payload_path, profile_name, champion_score, features_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(map_date) DO UPDATE SET
                generated_at = excluded.generated_at,
                payload_path = excluded.payload_path,
                profile_name = excluded.profile_name,
                champion_score = excluded.champion_score,
                features_json = excluded.features_json
            """,
            (
                map_date,
                generated_at,
                str(snapshot_path),
                champion_agent.get("selectedProfile"),
                safe_float(champion_agent.get("score")),
                json.dumps(features, cls=PandasAndNumpyEncoder, ensure_ascii=False),
            ),
        )

    return {"datedPath": str(snapshot_path)}


def load_recent_portfolio_history(
    *,
    lookback_days: int = DEFAULT_PORTFOLIO_LOOKBACK_DAYS,
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> List[Dict[str, Any]]:
    if not PORTFOLIO_HISTORY_DB_PATH.exists():
        return []

    cutoff_date = (
        datetime.now(MARKET_TIMEZONE).date() - timedelta(days=max(1, lookback_days))
    ).isoformat()
    with sqlite3.connect(PORTFOLIO_HISTORY_DB_PATH) as conn:
        ensure_portfolio_history_schema(conn)
        rows = conn.execute(
            """
            SELECT map_date, generated_at, payload_path, profile_name, champion_score, features_json
            FROM portfolio_runs
            WHERE map_date >= ?
            ORDER BY map_date ASC
            LIMIT ?
            """,
            (cutoff_date, max(1, limit)),
        ).fetchall()

    history: List[Dict[str, Any]] = []
    for row in rows:
        try:
            features = json.loads(row[5]) if row[5] else {}
        except json.JSONDecodeError:
            features = {}
        history.append(
            {
                "mapDate": row[0],
                "generatedAt": row[1],
                "payloadPath": row[2],
                "profileName": row[3],
                "championScore": safe_float(row[4]),
                "features": features,
            }
        )
    return history


def _feature_vector(features: Dict[str, float]) -> np.ndarray:
    return np.asarray([safe_float(features.get(key)) or 0.0 for key in BASE_FEATURE_KEYS], dtype=float)


def _submanifold_vector(
    current_features: Dict[str, float],
    previous_features: Optional[Dict[str, float]],
    earlier_features: Optional[Dict[str, float]],
) -> np.ndarray:
    current = _feature_vector(current_features)
    previous = _feature_vector(previous_features or {})
    earlier = _feature_vector(earlier_features or {})
    first_delta = current - previous if previous_features is not None else np.zeros_like(current)
    second_delta = first_delta - (previous - earlier) if previous_features is not None and earlier_features is not None else np.zeros_like(current)
    return np.concatenate([current, first_delta, second_delta], axis=0)


def _prepare_state_sequence(
    history_rows: List[Dict[str, Any]],
    current_features: Dict[str, float],
) -> tuple[List[str], np.ndarray]:
    labels: List[str] = []
    vectors: List[np.ndarray] = []
    feature_rows = [row.get("features") or {} for row in history_rows]

    for index, row in enumerate(history_rows):
        previous = feature_rows[index - 1] if index - 1 >= 0 else None
        earlier = feature_rows[index - 2] if index - 2 >= 0 else None
        labels.append(str(row.get("mapDate") or index))
        vectors.append(_submanifold_vector(feature_rows[index], previous, earlier))

    previous_current = feature_rows[-1] if feature_rows else None
    earlier_current = feature_rows[-2] if len(feature_rows) >= 2 else None
    labels.append("current")
    vectors.append(_submanifold_vector(current_features, previous_current, earlier_current))
    return labels, np.vstack(vectors) if vectors else np.zeros((0, 0), dtype=float)


def _fit_svd_embedding(state_matrix: np.ndarray) -> Dict[str, Any]:
    if state_matrix.size == 0:
        return {
            "mean": np.zeros((0,), dtype=float),
            "scale": np.ones((0,), dtype=float),
            "basis": np.zeros((0, 0), dtype=float),
            "latent": np.zeros((0, 0), dtype=float),
            "singular_values": [],
            "explained_variance": [],
            "rank": 0,
        }

    mean = state_matrix.mean(axis=0)
    scale = state_matrix.std(axis=0)
    scale = np.where(scale < 1e-6, 1.0, scale)
    normalized = (state_matrix - mean) / scale
    _, singular_values, vt = np.linalg.svd(normalized, full_matrices=False)
    rank = max(1, min(DEFAULT_MANIFOLD_RANK, vt.shape[0], normalized.shape[0]))
    basis = vt[:rank]
    latent = normalized @ basis.T
    variance_denom = float(np.sum(np.square(singular_values))) or 1.0
    explained_variance = [float((value * value) / variance_denom) for value in singular_values[:rank]]

    return {
        "mean": mean,
        "scale": scale,
        "basis": basis,
        "latent": latent,
        "singular_values": [float(value) for value in singular_values[:rank]],
        "explained_variance": explained_variance,
        "rank": rank,
    }


def _train_residual_network(latent: np.ndarray) -> Dict[str, Any]:
    if latent.shape[0] < 3 or latent.shape[1] == 0:
        return {
            "mode": "identity",
            "hiddenDim": 0,
            "epochs": 0,
            "loss": 0.0,
        }

    x_train = latent[:-1]
    y_train = latent[1:] - latent[:-1]
    input_dim = x_train.shape[1]
    hidden_dim = max(4, min(10, input_dim * 2))
    rng = np.random.default_rng(42)
    w1 = rng.normal(0.0, 0.18, size=(input_dim, hidden_dim))
    b1 = np.zeros((hidden_dim,), dtype=float)
    w2 = rng.normal(0.0, 0.18, size=(hidden_dim, input_dim))
    b2 = np.zeros((input_dim,), dtype=float)
    learning_rate = 0.03
    epochs = 240
    l2 = 1e-3
    final_loss = 0.0

    for _ in range(epochs):
        hidden_pre = x_train @ w1 + b1
        hidden = np.tanh(hidden_pre)
        pred = hidden @ w2 + b2
        error = pred - y_train
        final_loss = float(np.mean(np.square(error)))

        grad_pred = (2.0 / x_train.shape[0]) * error
        grad_w2 = hidden.T @ grad_pred + l2 * w2
        grad_b2 = grad_pred.sum(axis=0)
        grad_hidden = grad_pred @ w2.T
        grad_hidden_pre = grad_hidden * (1.0 - np.square(hidden))
        grad_w1 = x_train.T @ grad_hidden_pre + l2 * w1
        grad_b1 = grad_hidden_pre.sum(axis=0)

        w2 -= learning_rate * grad_w2
        b2 -= learning_rate * grad_b2
        w1 -= learning_rate * grad_w1
        b1 -= learning_rate * grad_b1

    return {
        "mode": "residual_mlp",
        "hiddenDim": hidden_dim,
        "epochs": epochs,
        "loss": final_loss,
        "w1": w1,
        "b1": b1,
        "w2": w2,
        "b2": b2,
    }


def _predict_next_latent(last_latent: np.ndarray, model: Dict[str, Any]) -> np.ndarray:
    if model.get("mode") != "residual_mlp":
        return last_latent
    hidden = np.tanh(last_latent @ model["w1"] + model["b1"])
    delta = hidden @ model["w2"] + model["b2"]
    return last_latent + delta


def _decode_latent(latent_vector: np.ndarray, svd_fit: Dict[str, Any]) -> np.ndarray:
    if svd_fit["rank"] == 0:
        return np.zeros((0,), dtype=float)
    normalized = latent_vector @ svd_fit["basis"]
    return normalized * svd_fit["scale"] + svd_fit["mean"]


def _base_feature_projection(decoded_state: np.ndarray) -> Dict[str, Optional[float]]:
    if decoded_state.size == 0:
        return {}
    values = decoded_state[: len(BASE_FEATURE_KEYS)]
    return {
        FEATURE_LABELS[key]: safe_float(values[index])
        for index, key in enumerate(BASE_FEATURE_KEYS)
    }


def _target_distance(
    current_features: Dict[str, float],
    projected_features: Dict[str, Optional[float]],
) -> Optional[float]:
    deltas: List[float] = []
    for key in PROJECTED_FEATURE_KEYS:
        target = projected_features.get(FEATURE_LABELS[key])
        current = safe_float(current_features.get(key))
        if target is None or current is None:
            continue
        scale = max(1e-6, abs(target) * 0.35, abs(current) * 0.35, 0.05)
        deltas.append(((current - target) / scale) ** 2)
    if not deltas:
        return None
    return safe_float(float(np.sqrt(np.mean(deltas))))


def build_portfolio_manifold_report(
    history_rows: List[Dict[str, Any]],
    current_payload: Dict[str, Any],
) -> Dict[str, Any]:
    current_features = extract_portfolio_features(current_payload)
    labels, state_matrix = _prepare_state_sequence(history_rows, current_features)
    svd_fit = _fit_svd_embedding(state_matrix)
    latent = svd_fit["latent"]
    if latent.size == 0:
        return {
            "method": "Temporal portfolio submanifold learning with residual neural bridge and SVD decoder",
            "historyCount": len(history_rows),
            "rank": 0,
            "stateDimension": 0,
            "singularValues": [],
            "explainedVariance": [],
            "currentLatent": [],
            "forecastLatent": [],
            "continuityScore": 0.0,
            "targetDistance": None,
            "projectedTarget": {},
            "currentState": {},
            "submanifoldLabels": labels,
            "neuralBridge": {"mode": "identity", "hiddenDim": 0, "epochs": 0, "loss": 0.0},
        }

    model = _train_residual_network(latent)
    current_latent = latent[-1]
    forecast_latent = _predict_next_latent(current_latent, model)
    current_decoded = _decode_latent(current_latent, svd_fit)
    forecast_decoded = _decode_latent(forecast_latent, svd_fit)
    current_state = _base_feature_projection(current_decoded)
    projected_target = _base_feature_projection(forecast_decoded)
    continuity_distance = float(np.linalg.norm(forecast_latent - current_latent))
    continuity_score = float(1.0 / (1.0 + continuity_distance))
    target_distance = _target_distance(current_features, projected_target)

    return {
        "method": "Temporal portfolio submanifold learning with residual neural bridge and SVD decoder",
        "historyCount": len(history_rows),
        "rank": svd_fit["rank"],
        "stateDimension": int(state_matrix.shape[1]),
        "singularValues": svd_fit["singular_values"],
        "explainedVariance": svd_fit["explained_variance"],
        "currentLatent": [float(value) for value in current_latent.tolist()],
        "forecastLatent": [float(value) for value in forecast_latent.tolist()],
        "continuityScore": continuity_score,
        "targetDistance": target_distance,
        "projectedTarget": projected_target,
        "currentState": current_state,
        "submanifoldLabels": labels,
        "neuralBridge": {
            "mode": model.get("mode"),
            "hiddenDim": model.get("hiddenDim"),
            "epochs": model.get("epochs"),
            "loss": safe_float(model.get("loss")),
        },
    }


def build_champion_agent_summary(
    candidate_reports: List[Dict[str, Any]],
    selected_report: Dict[str, Any],
) -> Dict[str, Any]:
    contender_scores = [
        {
            "profile": report.get("profile"),
            "label": report.get("label"),
            "score": safe_float(report.get("score")),
            "continuityScore": safe_float((report.get("manifold") or {}).get("continuityScore")),
            "targetDistance": safe_float((report.get("manifold") or {}).get("targetDistance")),
        }
        for report in sorted(
            candidate_reports,
            key=lambda item: safe_float(item.get("score")) or -999.0,
            reverse=True,
        )
    ]
    selected_manifold = selected_report.get("manifold") or {}
    projected = selected_manifold.get("projectedTarget") or {}
    rationale = (
        f"{selected_report.get('label')} was chosen because it balanced current upside "
        f"{(selected_report.get('summary') or {}).get('weightedUpsidePct') or 0:.3f}, "
        f"uncertainty {(selected_report.get('summary') or {}).get('weightedUncertaintyPct') or 0:.3f}, "
        f"geometry fit {(selected_report.get('geometry') or {}).get('alignmentScore') or 0:.3f}, "
        f"natural-gradient bound {(selected_report.get('naturalGradient') or {}).get('upperBoundScore') or 0:.3f}, "
        f"and a manifold continuity score of {selected_manifold.get('continuityScore') or 0:.3f} "
        f"while staying {selected_manifold.get('targetDistance') or 0:.3f} from the projected submanifold target."
    )
    return {
        "name": "Champion portfolio agent",
        "method": selected_manifold.get("method"),
        "selectedProfile": selected_report.get("profile"),
        "selectedLabel": selected_report.get("label"),
        "score": safe_float(selected_report.get("score")),
        "continuityScore": safe_float(selected_manifold.get("continuityScore")),
        "targetDistance": safe_float(selected_manifold.get("targetDistance")),
        "projectedTarget": projected,
        "historyCount": selected_manifold.get("historyCount"),
        "rank": selected_manifold.get("rank"),
        "rationale": rationale,
        "candidateScores": contender_scores,
    }
