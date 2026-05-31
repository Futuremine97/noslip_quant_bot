#!/usr/bin/env python3

from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from services.trader.map_store import MAP_HISTORY_DB_PATH, MODEL_CACHE_DIR

REINFORCEMENT_DB_PATH = MODEL_CACHE_DIR / "reinforcement_learning.sqlite3"
WEB_SIGNAL_NN_PATH = MODEL_CACHE_DIR / "web_signal_nn.json"
WEB_SIGNAL_NN_MAX_AGE_HOURS = float(os.getenv("WEB_SIGNAL_NN_MAX_AGE_HOURS", "24"))
WEB_SIGNAL_NN_MIN_ROWS = int(os.getenv("WEB_SIGNAL_NN_MIN_ROWS", "96"))
WEB_SIGNAL_NN_MAX_ROWS = int(os.getenv("WEB_SIGNAL_NN_MAX_ROWS", "12000"))
WEB_SIGNAL_NN_HIDDEN_DIM = int(os.getenv("WEB_SIGNAL_NN_HIDDEN_DIM", "12"))
WEB_SIGNAL_NN_EPOCHS = int(os.getenv("WEB_SIGNAL_NN_EPOCHS", "320"))
WEB_SIGNAL_NN_LR = float(os.getenv("WEB_SIGNAL_NN_LR", "0.02"))
WEB_SIGNAL_NN_L2 = float(os.getenv("WEB_SIGNAL_NN_L2", "0.0006"))
WEB_SIGNAL_NN_SEED = int(os.getenv("WEB_SIGNAL_NN_SEED", "42"))

FEATURE_NAMES: List[str] = [
    "direction_score",
    "uncertainty_ratio",
    "first_moment_pct_per_day",
    "second_moment_bp_per_day2",
    "conviction_space_x",
    "conviction_space_y",
    "momentum_space_x",
    "momentum_space_y",
    "optimization_score",
    "persistence_score",
    "stability_score",
    "regime_shift_risk",
    "first_velocity_pct_per_day",
    "second_velocity_bp_per_day2_per_day",
    "first_flip_rate",
    "second_flip_rate",
    "action_buy",
    "action_hold",
    "action_sell",
]


def _safe_float(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(numeric):
        return None
    return numeric


def _utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _public_model_state(model: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": model.get("status") or "unavailable",
        "path": str(WEB_SIGNAL_NN_PATH),
        "updatedAt": model.get("updatedAt"),
        "trainingRows": model.get("trainingRows"),
        "validationRows": model.get("validationRows"),
        "featureCount": model.get("featureCount"),
        "featureNames": model.get("featureNames") or FEATURE_NAMES,
        "targetHorizon": model.get("targetHorizon") or "next_trading_day_return_pct",
        "fitMode": model.get("fitMode") or "residual-mlp",
        "hiddenDim": model.get("hiddenDim"),
        "epochs": model.get("epochs"),
        "learningRate": model.get("learningRate"),
        "trainingMae": model.get("trainingMae"),
        "validationMae": model.get("validationMae"),
        "validationRmse": model.get("validationRmse"),
        "coverageRatio": model.get("coverageRatio"),
        "mapDates": model.get("mapDates"),
        "symbols": model.get("symbols"),
        "error": model.get("error"),
    }


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _is_model_fresh(model: Dict[str, Any], *, max_age_hours: float) -> bool:
    updated_at = str(model.get("updatedAt") or "")
    if not updated_at:
        return False
    try:
        updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    age = datetime.now(updated_dt.tzinfo).astimezone(updated_dt.tzinfo) - updated_dt
    return age <= timedelta(hours=max(0.0, max_age_hours))


def _load_cached_model() -> Optional[Dict[str, Any]]:
    if not WEB_SIGNAL_NN_PATH.exists():
        return None
    try:
        payload = json.loads(WEB_SIGNAL_NN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _write_model(model: Dict[str, Any]) -> None:
    WEB_SIGNAL_NN_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEB_SIGNAL_NN_PATH.write_text(
        json.dumps(model, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _action_features(action: Any) -> Tuple[float, float, float]:
    normalized = str(action or "HOLD").upper()
    return (
        1.0 if normalized == "BUY" else 0.0,
        1.0 if normalized == "HOLD" else 0.0,
        1.0 if normalized == "SELL" else 0.0,
    )


def _extract_feature_map(snapshot: Dict[str, Any]) -> Dict[str, Optional[float]]:
    trajectory = snapshot.get("trajectory") or {}
    first_coordinate = (
        snapshot.get("firstCoordinateSpace")
        or snapshot.get("momentumSpace")
        or {}
    )
    second_coordinate = (
        snapshot.get("secondCoordinateSpace")
        or snapshot.get("convictionSpace")
        or {}
    )
    action_buy, action_hold, action_sell = _action_features(
        snapshot.get("finalAction") or snapshot.get("final_action")
    )
    return {
        "direction_score": _safe_float(
            _first_present(
                snapshot.get("directionScore"),
                snapshot.get("direction_score"),
            )
        ),
        "uncertainty_ratio": _safe_float(
            _first_present(
                snapshot.get("uncertaintyRatio"),
                snapshot.get("uncertainty_ratio"),
            )
        ),
        "first_moment_pct_per_day": _safe_float(
            _first_present(
                snapshot.get("firstMomentPctPerDay"),
                snapshot.get("first_moment_pct_per_day"),
            )
        ),
        "second_moment_bp_per_day2": _safe_float(
            _first_present(
                snapshot.get("secondMomentBpPerDay2"),
                snapshot.get("second_moment_bp_per_day2"),
            )
        ),
        "conviction_space_x": _safe_float(
            _first_present(
                second_coordinate.get("x"),
                snapshot.get("conviction_space_x"),
            )
        ),
        "conviction_space_y": _safe_float(
            _first_present(
                second_coordinate.get("y"),
                snapshot.get("conviction_space_y"),
            )
        ),
        "momentum_space_x": _safe_float(
            _first_present(
                first_coordinate.get("x"),
                snapshot.get("momentum_space_x"),
            )
        ),
        "momentum_space_y": _safe_float(
            _first_present(
                first_coordinate.get("y"),
                snapshot.get("momentum_space_y"),
            )
        ),
        "optimization_score": _safe_float(
            _first_present(
                snapshot.get("optimizationScore"),
                snapshot.get("optimization_score"),
            )
        ),
        "persistence_score": _safe_float(
            _first_present(
                trajectory.get("persistenceScore"),
                snapshot.get("persistence_score"),
            )
        ),
        "stability_score": _safe_float(
            _first_present(
                trajectory.get("stabilityScore"),
                snapshot.get("stability_score"),
            )
        ),
        "regime_shift_risk": _safe_float(
            _first_present(
                trajectory.get("regimeShiftRisk"),
                snapshot.get("regime_shift_risk"),
            )
        ),
        "first_velocity_pct_per_day": _safe_float(
            _first_present(
                trajectory.get("firstVelocityPctPerDay"),
                snapshot.get("first_velocity_pct_per_day"),
            )
        ),
        "second_velocity_bp_per_day2_per_day": _safe_float(
            _first_present(
                trajectory.get("secondVelocityBpPerDay2PerDay"),
                snapshot.get("second_velocity_bp_per_day2_per_day"),
            )
        ),
        "first_flip_rate": _safe_float(
            _first_present(
                trajectory.get("firstFlipRate"),
                snapshot.get("first_flip_rate"),
            )
        ),
        "second_flip_rate": _safe_float(
            _first_present(
                trajectory.get("secondFlipRate"),
                snapshot.get("second_flip_rate"),
            )
        ),
        "action_buy": action_buy,
        "action_hold": action_hold,
        "action_sell": action_sell,
    }


def _feature_vector(snapshot: Dict[str, Any]) -> np.ndarray:
    feature_map = _extract_feature_map(snapshot)
    return np.asarray(
        [
            feature_map.get(feature_name)
            if feature_map.get(feature_name) is not None
            else np.nan
            for feature_name in FEATURE_NAMES
        ],
        dtype=float,
    )


def _load_training_frame() -> pd.DataFrame:
    if not MAP_HISTORY_DB_PATH.exists() or not REINFORCEMENT_DB_PATH.exists():
        return pd.DataFrame()

    try:
        with sqlite3.connect(MAP_HISTORY_DB_PATH) as map_conn:
            map_df = pd.read_sql_query(
                """
                SELECT
                    map_date,
                    symbol,
                    current_price,
                    final_action,
                    direction_score,
                    uncertainty_ratio,
                    first_moment_pct_per_day,
                    second_moment_bp_per_day2,
                    conviction_space_x,
                    conviction_space_y,
                    momentum_space_x,
                    momentum_space_y,
                    optimization_score,
                    persistence_score,
                    stability_score,
                    regime_shift_risk,
                    first_velocity_pct_per_day,
                    second_velocity_bp_per_day2_per_day,
                    first_flip_rate,
                    second_flip_rate
                FROM map_symbol_snapshots
                ORDER BY map_date ASC, symbol ASC
                """,
                map_conn,
            )
    except Exception:
        return pd.DataFrame()

    try:
        with sqlite3.connect(REINFORCEMENT_DB_PATH) as reinforcement_conn:
            reward_df = pd.read_sql_query(
                """
                SELECT symbol, reference_date, realized_return_pct
                FROM daily_reward_events
                ORDER BY reference_date ASC, symbol ASC
                """,
                reinforcement_conn,
            )
    except Exception:
        return pd.DataFrame()

    if map_df.empty or reward_df.empty:
        return pd.DataFrame()

    merged = map_df.merge(
        reward_df,
        left_on=["symbol", "map_date"],
        right_on=["symbol", "reference_date"],
        how="inner",
    )
    if merged.empty:
        return pd.DataFrame()

    merged = merged.sort_values(["map_date", "symbol"]).reset_index(drop=True)
    if WEB_SIGNAL_NN_MAX_ROWS > 0 and len(merged) > WEB_SIGNAL_NN_MAX_ROWS:
        merged = merged.tail(WEB_SIGNAL_NN_MAX_ROWS).reset_index(drop=True)
    return merged


def _split_train_validation(frame: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if frame.empty:
        return frame, frame

    unique_dates = sorted(frame["map_date"].dropna().astype(str).unique().tolist())
    if len(unique_dates) >= 6:
        validation_count = max(1, int(round(len(unique_dates) * 0.2)))
        validation_dates = set(unique_dates[-validation_count:])
        validation_frame = frame[frame["map_date"].astype(str).isin(validation_dates)].copy()
        training_frame = frame[~frame["map_date"].astype(str).isin(validation_dates)].copy()
        if not training_frame.empty and not validation_frame.empty:
            return training_frame.reset_index(drop=True), validation_frame.reset_index(drop=True)

    split_index = max(1, int(len(frame) * 0.8))
    training_frame = frame.iloc[:split_index].copy().reset_index(drop=True)
    validation_frame = frame.iloc[split_index:].copy().reset_index(drop=True)
    if validation_frame.empty:
        validation_frame = training_frame.tail(min(8, len(training_frame))).copy().reset_index(drop=True)
    return training_frame, validation_frame


def _nan_safe_medians(matrix: np.ndarray) -> np.ndarray:
    with np.errstate(all="ignore"):
        medians = np.nanmedian(matrix, axis=0)
    medians = np.where(np.isfinite(medians), medians, 0.0)
    return medians.astype(float)


def _impute_matrix(matrix: np.ndarray, medians: np.ndarray) -> np.ndarray:
    return np.where(np.isfinite(matrix), matrix, medians[None, :]).astype(float)


def _standardize_matrix(
    matrix: np.ndarray,
    mean: np.ndarray,
    std: np.ndarray,
) -> np.ndarray:
    safe_std = np.where(np.abs(std) <= 1e-9, 1.0, std)
    standardized = (matrix - mean[None, :]) / safe_std[None, :]
    return np.clip(standardized, -6.0, 6.0).astype(float)


def _rmse(values: np.ndarray) -> float:
    if values.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(values))))


def _mae(values: np.ndarray) -> float:
    if values.size == 0:
        return 0.0
    return float(np.mean(np.abs(values)))


def _train_residual_mlp(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_val: np.ndarray,
    y_val: np.ndarray,
) -> Dict[str, Any]:
    input_dim = int(x_train.shape[1])
    hidden_dim = max(4, min(WEB_SIGNAL_NN_HIDDEN_DIM, max(4, input_dim * 2)))
    rng = np.random.default_rng(WEB_SIGNAL_NN_SEED)

    w1 = rng.normal(0.0, 0.18 / max(1, input_dim), size=(input_dim, hidden_dim))
    b1 = np.zeros(hidden_dim, dtype=float)
    w_skip = rng.normal(0.0, 0.1 / max(1, input_dim), size=(input_dim, 1))
    w2 = rng.normal(0.0, 0.12 / max(1, hidden_dim), size=(hidden_dim, 1))
    b2 = np.zeros(1, dtype=float)

    best_state: Dict[str, np.ndarray] = {}
    best_val_loss: Optional[float] = None
    best_epoch = 0

    for epoch in range(max(40, WEB_SIGNAL_NN_EPOCHS)):
        hidden = np.tanh(x_train @ w1 + b1[None, :])
        predictions = hidden @ w2 + x_train @ w_skip + b2[None, :]
        error = predictions[:, 0] - y_train
        grad_output = (2.0 / max(1, len(x_train))) * error[:, None]

        grad_w2 = hidden.T @ grad_output + WEB_SIGNAL_NN_L2 * w2
        grad_wskip = x_train.T @ grad_output + WEB_SIGNAL_NN_L2 * w_skip
        grad_b2 = grad_output.sum(axis=0)
        grad_hidden = grad_output @ w2.T
        grad_pre_activation = grad_hidden * (1.0 - np.square(hidden))
        grad_w1 = x_train.T @ grad_pre_activation + WEB_SIGNAL_NN_L2 * w1
        grad_b1 = grad_pre_activation.sum(axis=0)

        learning_rate = WEB_SIGNAL_NN_LR * (0.985 ** (epoch // 20))
        w2 -= learning_rate * np.clip(grad_w2, -1.5, 1.5)
        w_skip -= learning_rate * np.clip(grad_wskip, -1.5, 1.5)
        b2 -= learning_rate * np.clip(grad_b2, -1.5, 1.5)
        w1 -= learning_rate * np.clip(grad_w1, -1.5, 1.5)
        b1 -= learning_rate * np.clip(grad_b1, -1.5, 1.5)

        val_hidden = np.tanh(x_val @ w1 + b1[None, :])
        val_predictions = val_hidden @ w2 + x_val @ w_skip + b2[None, :]
        val_error = val_predictions[:, 0] - y_val
        val_loss = float(np.mean(np.square(val_error)))

        if best_val_loss is None or val_loss < best_val_loss:
            best_val_loss = val_loss
            best_epoch = epoch + 1
            best_state = {
                "w1": w1.copy(),
                "b1": b1.copy(),
                "w2": w2.copy(),
                "w_skip": w_skip.copy(),
                "b2": b2.copy(),
            }

    if not best_state:
        best_state = {
            "w1": w1.copy(),
            "b1": b1.copy(),
            "w2": w2.copy(),
            "w_skip": w_skip.copy(),
            "b2": b2.copy(),
        }

    return {
        "hiddenDim": hidden_dim,
        "epochs": best_epoch or WEB_SIGNAL_NN_EPOCHS,
        **best_state,
    }


def _predict_with_model(x: np.ndarray, model: Dict[str, Any]) -> np.ndarray:
    w1 = np.asarray(model["w1"], dtype=float)
    b1 = np.asarray(model["b1"], dtype=float)
    w2 = np.asarray(model["w2"], dtype=float)
    w_skip = np.asarray(model["w_skip"], dtype=float)
    b2 = np.asarray(model["b2"], dtype=float)
    hidden = np.tanh(x @ w1 + b1[None, :])
    predictions = hidden @ w2 + x @ w_skip + b2[None, :]
    return predictions[:, 0].astype(float)


def _train_web_signal_model() -> Optional[Dict[str, Any]]:
    frame = _load_training_frame()
    if frame.empty or len(frame) < max(24, WEB_SIGNAL_NN_MIN_ROWS):
        return None

    training_frame, validation_frame = _split_train_validation(frame)
    if training_frame.empty or validation_frame.empty:
        return None

    x_train_raw = np.vstack(
        [_feature_vector(row) for row in training_frame.to_dict("records")]
    )
    x_val_raw = np.vstack(
        [_feature_vector(row) for row in validation_frame.to_dict("records")]
    )
    y_train_raw = np.asarray(
        training_frame["realized_return_pct"].map(_safe_float).fillna(0.0).tolist(),
        dtype=float,
    )
    y_val_raw = np.asarray(
        validation_frame["realized_return_pct"].map(_safe_float).fillna(0.0).tolist(),
        dtype=float,
    )

    medians = _nan_safe_medians(x_train_raw)
    x_train_imputed = _impute_matrix(x_train_raw, medians)
    x_val_imputed = _impute_matrix(x_val_raw, medians)

    mean = np.mean(x_train_imputed, axis=0)
    std = np.std(x_train_imputed, axis=0)
    x_train = _standardize_matrix(x_train_imputed, mean, std)
    x_val = _standardize_matrix(x_val_imputed, mean, std)

    y_mean = float(np.mean(y_train_raw))
    y_std = float(np.std(y_train_raw))
    if y_std <= 1e-9:
        y_std = 1.0
    y_train = ((y_train_raw - y_mean) / y_std).astype(float)
    y_val = ((y_val_raw - y_mean) / y_std).astype(float)

    trained = _train_residual_mlp(x_train, y_train, x_val, y_val)
    train_pred_scaled = _predict_with_model(x_train, trained)
    val_pred_scaled = _predict_with_model(x_val, trained)
    train_pred = train_pred_scaled * y_std + y_mean
    val_pred = val_pred_scaled * y_std + y_mean

    train_error = train_pred - y_train_raw
    val_error = val_pred - y_val_raw

    updated_at = _utc_now_iso()
    return {
        "status": "ok",
        "updatedAt": updated_at,
        "featureCount": len(FEATURE_NAMES),
        "featureNames": FEATURE_NAMES,
        "targetHorizon": "next_trading_day_return_pct",
        "fitMode": "residual-mlp",
        "hiddenDim": trained["hiddenDim"],
        "epochs": trained["epochs"],
        "learningRate": WEB_SIGNAL_NN_LR,
        "l2": WEB_SIGNAL_NN_L2,
        "trainingRows": int(len(training_frame)),
        "validationRows": int(len(validation_frame)),
        "mapDates": int(frame["map_date"].nunique()),
        "symbols": int(frame["symbol"].nunique()),
        "coverageRatio": float(len(frame) / max(1, len(frame))),
        "trainingMae": _mae(train_error),
        "validationMae": _mae(val_error),
        "validationRmse": _rmse(val_error),
        "yMean": y_mean,
        "yStd": y_std,
        "featureMedians": medians.tolist(),
        "featureMean": mean.tolist(),
        "featureStd": np.where(np.abs(std) <= 1e-9, 1.0, std).tolist(),
        "w1": trained["w1"].tolist(),
        "b1": trained["b1"].tolist(),
        "w2": trained["w2"].tolist(),
        "w_skip": trained["w_skip"].tolist(),
        "b2": trained["b2"].tolist(),
    }


def load_or_train_web_signal_model(
    *,
    force_refresh: bool = False,
    max_age_hours: float = WEB_SIGNAL_NN_MAX_AGE_HOURS,
) -> Dict[str, Any]:
    cached_model = _load_cached_model()
    if (
        not force_refresh
        and cached_model is not None
        and _is_model_fresh(cached_model, max_age_hours=max_age_hours)
    ):
        return cached_model

    trained_model = _train_web_signal_model()
    if trained_model is not None:
        _write_model(trained_model)
        return trained_model

    if cached_model is not None:
        cached_model = {
            **cached_model,
            "status": "stale-cache",
            "error": cached_model.get("error")
            or "Training data was insufficient for a refresh; using the last cached model.",
        }
        return cached_model

    return {
        "status": "unavailable",
        "updatedAt": None,
        "trainingRows": 0,
        "validationRows": 0,
        "featureCount": len(FEATURE_NAMES),
        "featureNames": FEATURE_NAMES,
        "targetHorizon": "next_trading_day_return_pct",
        "fitMode": "residual-mlp",
        "error": "Insufficient website-internal history to train the neural scorer yet.",
    }


def infer_web_signal(
    snapshot: Dict[str, Any],
    *,
    model: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    loaded_model = model or load_or_train_web_signal_model()
    if loaded_model.get("status") not in {"ok", "stale-cache"}:
        return {
            "webNeuralScore": None,
            "webNeuralConfidence": None,
            "webNeuralLabel": "unavailable",
            "webNeuralNovelty": None,
        }

    x_raw = _feature_vector(snapshot)
    medians = np.asarray(loaded_model.get("featureMedians") or [0.0] * len(FEATURE_NAMES), dtype=float)
    mean = np.asarray(loaded_model.get("featureMean") or [0.0] * len(FEATURE_NAMES), dtype=float)
    std = np.asarray(loaded_model.get("featureStd") or [1.0] * len(FEATURE_NAMES), dtype=float)
    x_imputed = np.where(np.isfinite(x_raw), x_raw, medians).astype(float)
    x = _standardize_matrix(x_imputed[None, :], mean, std)

    y_scaled = _predict_with_model(x, loaded_model)[0]
    score = float(y_scaled * float(loaded_model.get("yStd") or 1.0) + float(loaded_model.get("yMean") or 0.0))
    novelty = float(np.sqrt(np.mean(np.square(x[0]))))
    residual_scale = max(
        0.003,
        float(loaded_model.get("validationMae") or loaded_model.get("trainingMae") or 0.01),
    )
    sample_factor = min(1.0, math.log1p(float(loaded_model.get("trainingRows") or 0.0)) / math.log(257.0))
    fit_factor = 1.0 / (1.0 + residual_scale / 0.02)
    novelty_factor = 1.0 / (1.0 + max(0.0, novelty - 1.0) * 0.45)
    confidence = float(np.clip(0.08 + 0.92 * sample_factor * fit_factor * novelty_factor, 0.05, 0.99))

    threshold = max(0.003, residual_scale * 0.6)
    if score >= threshold:
        label = "accumulation"
    elif score <= -threshold:
        label = "distribution"
    else:
        label = "neutral"

    return {
        "webNeuralScore": score,
        "webNeuralConfidence": confidence,
        "webNeuralLabel": label,
        "webNeuralNovelty": novelty,
    }


def score_information_map_items_with_web_nn(
    items: List[Dict[str, Any]],
    *,
    force_refresh: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    model = load_or_train_web_signal_model(force_refresh=force_refresh)
    if not items:
        return items, _public_model_state(model)

    scored_items: List[Dict[str, Any]] = []
    for item in items:
        scored_items.append(
            {
                **item,
                **infer_web_signal(item, model=model),
            }
        )
    return scored_items, _public_model_state(model)
