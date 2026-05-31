#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import warnings
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from services.trader.web_signal_nn import (
    FEATURE_NAMES,
    MODEL_CACHE_DIR,
    WEB_SIGNAL_NN_MIN_ROWS,
    _feature_vector,
    _impute_matrix,
    _load_training_frame,
    _mae,
    _nan_safe_medians,
    _rmse,
    _safe_float,
    _split_train_validation,
    _standardize_matrix,
    _utc_now_iso,
)

FEATURE_SELECTION_BENCHMARK_PATH = MODEL_CACHE_DIR / "feature_selection_benchmark.json"
FEATURE_SELECTION_BENCHMARK_MAX_AGE_HOURS = float(
    os.getenv("FEATURE_SELECTION_BENCHMARK_MAX_AGE_HOURS", "24")
)
FEATURE_SELECTION_BENCHMARK_SEED = int(
    os.getenv("FEATURE_SELECTION_BENCHMARK_SEED", "42")
)
FEATURE_SELECTION_RIDGE_ALPHA = float(
    os.getenv("FEATURE_SELECTION_RIDGE_ALPHA", "1.0")
)
FEATURE_SELECTION_AUTOENCODER_ALPHA = float(
    os.getenv("FEATURE_SELECTION_AUTOENCODER_ALPHA", "0.0008")
)
FEATURE_SELECTION_AUTOENCODER_EPOCHS = int(
    os.getenv("FEATURE_SELECTION_AUTOENCODER_EPOCHS", "600")
)
FEATURE_SELECTION_COMPONENT_GRID = tuple(
    int(part.strip())
    for part in os.getenv("FEATURE_SELECTION_COMPONENT_GRID", "2,3,4,6,8,12")
    .split(",")
    if part.strip()
)

try:
    from sklearn.cross_decomposition import PLSRegression
    from sklearn.decomposition import KernelPCA, PCA
    from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
    from sklearn.feature_selection import SelectKBest, f_regression
    from sklearn.linear_model import Ridge
    from sklearn.neural_network import MLPRegressor
except Exception as exc:  # pragma: no cover - defensive import guard
    PLSRegression = None  # type: ignore[assignment]
    KernelPCA = None  # type: ignore[assignment]
    PCA = None  # type: ignore[assignment]
    LinearDiscriminantAnalysis = None  # type: ignore[assignment]
    SelectKBest = None  # type: ignore[assignment]
    f_regression = None  # type: ignore[assignment]
    Ridge = None  # type: ignore[assignment]
    MLPRegressor = None  # type: ignore[assignment]
    SKLEARN_IMPORT_ERROR = str(exc)
else:
    SKLEARN_IMPORT_ERROR = None


def _load_cached_report() -> Optional[Dict[str, Any]]:
    if not FEATURE_SELECTION_BENCHMARK_PATH.exists():
        return None
    try:
        payload = json.loads(FEATURE_SELECTION_BENCHMARK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_report(report: Dict[str, Any]) -> None:
    FEATURE_SELECTION_BENCHMARK_PATH.parent.mkdir(parents=True, exist_ok=True)
    FEATURE_SELECTION_BENCHMARK_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def public_feature_selection_benchmark_state(report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload = dict(report or {})
    recommended = payload.get("recommendedMethod")
    best_by_method = payload.get("bestByMethod")
    return {
        "status": payload.get("status") or "unavailable",
        "path": str(FEATURE_SELECTION_BENCHMARK_PATH),
        "updatedAt": payload.get("updatedAt"),
        "rows": payload.get("rows"),
        "trainingRows": payload.get("trainingRows"),
        "validationRows": payload.get("validationRows"),
        "symbols": payload.get("symbols"),
        "mapDates": payload.get("mapDates"),
        "featureCount": payload.get("featureCount"),
        "featureNames": payload.get("featureNames") or FEATURE_NAMES,
        "targetHorizon": payload.get("targetHorizon") or "next_trading_day_return_pct",
        "downstreamModel": payload.get("downstreamModel") or "ridge",
        "methodsCompared": payload.get("methodsCompared") or [],
        "recommendedMethod": recommended if isinstance(recommended, dict) else None,
        "bestByMethod": best_by_method[:5] if isinstance(best_by_method, list) else [],
        "summary": payload.get("summary"),
        "error": payload.get("error"),
    }


def _is_report_fresh(report: Dict[str, Any], *, max_age_hours: float) -> bool:
    updated_at = str(report.get("updatedAt") or "")
    if not updated_at:
        return False
    try:
        updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    age = datetime.now(updated_dt.tzinfo).astimezone(updated_dt.tzinfo) - updated_dt
    return age <= timedelta(hours=max(0.0, max_age_hours))


def _component_grid(feature_count: int) -> List[int]:
    candidates = [value for value in FEATURE_SELECTION_COMPONENT_GRID if value > 0]
    if not candidates:
        candidates = [2, 3, 4, 6, 8, 12]
    bounded = sorted({min(feature_count, value) for value in candidates if feature_count > 0})
    if 1 <= feature_count and 1 not in bounded:
        bounded.insert(0, 1)
    return bounded[: max(1, min(len(bounded), feature_count))]


def _ensure_2d(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=float)
    if matrix.ndim == 1:
        return matrix[:, None]
    return matrix


def _prepare_dataset() -> Dict[str, Any]:
    frame = _load_training_frame()
    if frame.empty:
        return {
            "status": "unavailable",
            "error": "The map history and reinforcement reward tables do not overlap yet.",
            "rows": 0,
            "trainingRows": 0,
            "validationRows": 0,
            "symbols": 0,
            "mapDates": 0,
        }

    training_frame, validation_frame = _split_train_validation(frame)
    if training_frame.empty or validation_frame.empty:
        return {
            "status": "unavailable",
            "error": "The current history cannot be split into train and validation windows yet.",
            "rows": int(len(frame)),
            "trainingRows": int(len(training_frame)),
            "validationRows": int(len(validation_frame)),
            "symbols": int(frame["symbol"].nunique()),
            "mapDates": int(frame["map_date"].nunique()),
        }

    if len(frame) < max(24, WEB_SIGNAL_NN_MIN_ROWS):
        return {
            "status": "unavailable",
            "error": "There are not enough labeled rows yet for a stable benchmark.",
            "rows": int(len(frame)),
            "trainingRows": int(len(training_frame)),
            "validationRows": int(len(validation_frame)),
            "symbols": int(frame["symbol"].nunique()),
            "mapDates": int(frame["map_date"].nunique()),
        }

    x_train_raw = np.vstack(
        [_feature_vector(row) for row in training_frame.to_dict("records")]
    )
    x_val_raw = np.vstack(
        [_feature_vector(row) for row in validation_frame.to_dict("records")]
    )
    y_train = np.asarray(
        training_frame["realized_return_pct"].map(_safe_float).fillna(0.0).tolist(),
        dtype=float,
    )
    y_val = np.asarray(
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

    return {
        "status": "ok",
        "frame": frame,
        "trainingFrame": training_frame,
        "validationFrame": validation_frame,
        "xTrain": x_train,
        "xVal": x_val,
        "yTrain": y_train,
        "yVal": y_val,
    }


def _fit_ridge(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_val: np.ndarray,
) -> Tuple[np.ndarray, Any]:
    if Ridge is None:
        raise RuntimeError(SKLEARN_IMPORT_ERROR or "scikit-learn is unavailable")
    regressor = Ridge(alpha=FEATURE_SELECTION_RIDGE_ALPHA)
    regressor.fit(_ensure_2d(x_train), y_train)
    predictions = regressor.predict(_ensure_2d(x_val))
    return np.asarray(predictions, dtype=float), regressor


def _evaluate_predictions(
    name: str,
    dimension: int,
    predictions: np.ndarray,
    target: np.ndarray,
    *,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    error = np.asarray(predictions, dtype=float) - np.asarray(target, dtype=float)
    result = {
        "method": name,
        "latentDim": int(dimension),
        "validationMae": _mae(error),
        "validationRmse": _rmse(error),
    }
    if extra:
        result.update(extra)
    return result


def _top_feature_weights(weights: np.ndarray, *, limit: int = 6) -> List[Dict[str, Any]]:
    ranked_indices = np.argsort(-np.abs(weights))[:limit]
    return [
        {
            "feature": FEATURE_NAMES[int(index)],
            "weight": float(weights[int(index)]),
        }
        for index in ranked_indices
    ]


def _target_classes(y_train: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
    q1, q2 = np.quantile(y_train, [1.0 / 3.0, 2.0 / 3.0])
    if not np.isfinite(q1) or not np.isfinite(q2) or abs(q2 - q1) <= 1e-9:
        median = float(np.median(y_train))
        labels = (y_train >= median).astype(int)
        return labels, {
            "classScheme": "median-split",
            "classCounts": [int(np.sum(labels == 0)), int(np.sum(labels == 1))],
        }

    labels = np.zeros(len(y_train), dtype=int)
    labels[y_train >= q1] = 1
    labels[y_train >= q2] = 2
    counts = [int(np.sum(labels == value)) for value in np.unique(labels)]
    if len(np.unique(labels)) < 3 or min(counts) < 4:
        median = float(np.median(y_train))
        labels = (y_train >= median).astype(int)
        return labels, {
            "classScheme": "median-split",
            "classCounts": [int(np.sum(labels == 0)), int(np.sum(labels == 1))],
        }

    return labels, {
        "classScheme": "terciles",
        "classCounts": [int(np.sum(labels == value)) for value in [0, 1, 2]],
        "thresholds": [float(q1), float(q2)],
    }


def _benchmark_identity(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_val: np.ndarray,
    y_val: np.ndarray,
) -> Dict[str, Any]:
    predictions, _ = _fit_ridge(x_train, y_train, x_val)
    return _evaluate_predictions(
        "identity",
        x_train.shape[1],
        predictions,
        y_val,
        extra={"note": "No feature reduction; ridge on the full standardized feature set."},
    )


def _benchmark_direct_feature_selection(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_val: np.ndarray,
    y_val: np.ndarray,
    dimensions: Sequence[int],
) -> List[Dict[str, Any]]:
    if SelectKBest is None or f_regression is None:
        raise RuntimeError(SKLEARN_IMPORT_ERROR or "scikit-learn is unavailable")

    results: List[Dict[str, Any]] = []
    for dimension in dimensions:
        selector = SelectKBest(score_func=f_regression, k=min(dimension, x_train.shape[1]))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            transformed_train = selector.fit_transform(x_train, y_train)
            transformed_val = selector.transform(x_val)
        predictions, _ = _fit_ridge(transformed_train, y_train, transformed_val)
        selected_indices = selector.get_support(indices=True).tolist()
        results.append(
            _evaluate_predictions(
                "direct_feature_selection",
                len(selected_indices),
                predictions,
                y_val,
                extra={
                    "selectedFeatures": [FEATURE_NAMES[int(index)] for index in selected_indices],
                },
            )
        )
    return results


def _benchmark_supervised_pca(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_val: np.ndarray,
    y_val: np.ndarray,
    dimensions: Sequence[int],
) -> List[Dict[str, Any]]:
    if PCA is None:
        raise RuntimeError(SKLEARN_IMPORT_ERROR or "scikit-learn is unavailable")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        scores, _ = f_regression(x_train, y_train) if f_regression is not None else (None, None)
    weights = np.asarray(scores if scores is not None else np.ones(x_train.shape[1]), dtype=float)
    weights = np.where(np.isfinite(weights) & (weights > 0.0), np.sqrt(weights), 0.0)
    if float(np.sum(weights)) <= 1e-9:
        weights = np.ones(x_train.shape[1], dtype=float)

    weighted_train = x_train * weights[None, :]
    weighted_val = x_val * weights[None, :]

    results: List[Dict[str, Any]] = []
    for dimension in dimensions:
        projector = PCA(n_components=min(dimension, x_train.shape[1]), random_state=FEATURE_SELECTION_BENCHMARK_SEED)
        transformed_train = projector.fit_transform(weighted_train)
        transformed_val = projector.transform(weighted_val)
        predictions, _ = _fit_ridge(transformed_train, y_train, transformed_val)
        results.append(
            _evaluate_predictions(
                "supervised_pca",
                transformed_train.shape[1],
                predictions,
                y_val,
                extra={
                    "explainedVarianceRatio": float(np.sum(projector.explained_variance_ratio_)),
                    "topFeatureWeights": _top_feature_weights(weights),
                },
            )
        )
    return results


def _benchmark_pls(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_val: np.ndarray,
    y_val: np.ndarray,
    dimensions: Sequence[int],
) -> List[Dict[str, Any]]:
    if PLSRegression is None:
        raise RuntimeError(SKLEARN_IMPORT_ERROR or "scikit-learn is unavailable")

    results: List[Dict[str, Any]] = []
    for dimension in dimensions:
        components = min(dimension, x_train.shape[1], max(1, len(x_train) - 1))
        if components <= 0:
            continue
        estimator = PLSRegression(n_components=components, scale=False)
        estimator.fit(x_train, y_train)
        predictions = np.asarray(estimator.predict(x_val), dtype=float).reshape(-1)
        x_scores = np.asarray(estimator.transform(x_train), dtype=float)
        results.append(
            _evaluate_predictions(
                "pls",
                components,
                predictions,
                y_val,
                extra={
                    "xScoreVariance": float(np.mean(np.var(x_scores, axis=0))) if x_scores.size else 0.0,
                },
            )
        )
    return results


def _benchmark_lda(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_val: np.ndarray,
    y_val: np.ndarray,
    dimensions: Sequence[int],
) -> List[Dict[str, Any]]:
    if LinearDiscriminantAnalysis is None:
        raise RuntimeError(SKLEARN_IMPORT_ERROR or "scikit-learn is unavailable")

    labels, meta = _target_classes(y_train)
    unique_labels = np.unique(labels)
    max_components = max(0, len(unique_labels) - 1)
    if max_components <= 0:
        return []

    results: List[Dict[str, Any]] = []
    for dimension in dimensions:
        components = min(dimension, max_components)
        if components <= 0:
            continue
        reducer = LinearDiscriminantAnalysis(solver="svd", n_components=components)
        transformed_train = reducer.fit_transform(x_train, labels)
        transformed_val = reducer.transform(x_val)
        predictions, _ = _fit_ridge(transformed_train, y_train, transformed_val)
        results.append(
            _evaluate_predictions(
                "lda",
                components,
                predictions,
                y_val,
                extra={
                    **meta,
                    "note": "LDA needs discrete labels, so next-day returns are binned before the transform.",
                },
            )
        )
    return results


def _benchmark_kernel_pca(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_val: np.ndarray,
    y_val: np.ndarray,
    dimensions: Sequence[int],
) -> List[Dict[str, Any]]:
    if KernelPCA is None:
        raise RuntimeError(SKLEARN_IMPORT_ERROR or "scikit-learn is unavailable")

    results: List[Dict[str, Any]] = []
    gamma_grid = [
        0.15 / max(1, x_train.shape[1]),
        0.35 / max(1, x_train.shape[1]),
        0.75 / max(1, x_train.shape[1]),
    ]
    for dimension in dimensions:
        best_for_dimension: Optional[Dict[str, Any]] = None
        for gamma in gamma_grid:
            reducer = KernelPCA(
                n_components=min(dimension, x_train.shape[1]),
                kernel="rbf",
                gamma=gamma,
                fit_inverse_transform=False,
                eigen_solver="auto",
            )
            try:
                transformed_train = reducer.fit_transform(x_train)
                transformed_val = reducer.transform(x_val)
            except Exception:
                continue
            predictions, _ = _fit_ridge(transformed_train, y_train, transformed_val)
            current = _evaluate_predictions(
                "kernel_pca",
                transformed_train.shape[1],
                predictions,
                y_val,
                extra={"kernel": "rbf", "gamma": float(gamma)},
            )
            if best_for_dimension is None or current["validationMae"] < best_for_dimension["validationMae"]:
                best_for_dimension = current
        if best_for_dimension is not None:
            results.append(best_for_dimension)
    return results


def _autoencoder_latent(matrix: np.ndarray, encoder: Any) -> np.ndarray:
    hidden_linear = np.asarray(matrix, dtype=float) @ np.asarray(encoder.coefs_[0], dtype=float)
    hidden_linear += np.asarray(encoder.intercepts_[0], dtype=float)[None, :]
    return np.tanh(hidden_linear).astype(float)


def _benchmark_autoencoder(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_val: np.ndarray,
    y_val: np.ndarray,
    dimensions: Sequence[int],
) -> List[Dict[str, Any]]:
    if MLPRegressor is None:
        raise RuntimeError(SKLEARN_IMPORT_ERROR or "scikit-learn is unavailable")

    results: List[Dict[str, Any]] = []
    for dimension in dimensions:
        encoder = MLPRegressor(
            hidden_layer_sizes=(min(dimension, x_train.shape[1]),),
            activation="tanh",
            alpha=FEATURE_SELECTION_AUTOENCODER_ALPHA,
            max_iter=max(200, FEATURE_SELECTION_AUTOENCODER_EPOCHS),
            random_state=FEATURE_SELECTION_BENCHMARK_SEED,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                encoder.fit(x_train, x_train)
            except Exception:
                continue
        transformed_train = _autoencoder_latent(x_train, encoder)
        transformed_val = _autoencoder_latent(x_val, encoder)
        predictions, _ = _fit_ridge(transformed_train, y_train, transformed_val)
        reconstructions = np.asarray(encoder.predict(x_val), dtype=float)
        reconstruction_error = reconstructions - x_val
        results.append(
            _evaluate_predictions(
                "autoencoder",
                transformed_train.shape[1],
                predictions,
                y_val,
                extra={
                    "reconstructionMae": _mae(reconstruction_error),
                    "reconstructionRmse": _rmse(reconstruction_error),
                },
            )
        )
    return results


def benchmark_feature_reduction_methods() -> Dict[str, Any]:
    dataset = _prepare_dataset()
    if dataset.get("status") != "ok":
        return {
            "status": "unavailable",
            "updatedAt": _utc_now_iso(),
            "featureCount": len(FEATURE_NAMES),
            "featureNames": FEATURE_NAMES,
            "rows": dataset.get("rows", 0),
            "trainingRows": dataset.get("trainingRows", 0),
            "validationRows": dataset.get("validationRows", 0),
            "symbols": dataset.get("symbols", 0),
            "mapDates": dataset.get("mapDates", 0),
            "error": dataset.get("error") or "Insufficient data for benchmarking.",
        }

    if SKLEARN_IMPORT_ERROR:
        return {
            "status": "unavailable",
            "updatedAt": _utc_now_iso(),
            "featureCount": len(FEATURE_NAMES),
            "featureNames": FEATURE_NAMES,
            "rows": int(len(dataset["frame"])),
            "trainingRows": int(len(dataset["trainingFrame"])),
            "validationRows": int(len(dataset["validationFrame"])),
            "symbols": int(dataset["frame"]["symbol"].nunique()),
            "mapDates": int(dataset["frame"]["map_date"].nunique()),
            "error": f"scikit-learn import failed: {SKLEARN_IMPORT_ERROR}",
        }

    x_train = np.asarray(dataset["xTrain"], dtype=float)
    x_val = np.asarray(dataset["xVal"], dtype=float)
    y_train = np.asarray(dataset["yTrain"], dtype=float)
    y_val = np.asarray(dataset["yVal"], dtype=float)
    dimensions = _component_grid(x_train.shape[1])

    all_runs: List[Dict[str, Any]] = []
    all_runs.append(_benchmark_identity(x_train, y_train, x_val, y_val))
    for runner in (
        _benchmark_direct_feature_selection,
        _benchmark_supervised_pca,
        _benchmark_pls,
        _benchmark_lda,
        _benchmark_kernel_pca,
        _benchmark_autoencoder,
    ):
        try:
            all_runs.extend(runner(x_train, y_train, x_val, y_val, dimensions))
        except Exception as exc:
            all_runs.append(
                {
                    "method": runner.__name__.replace("_benchmark_", ""),
                    "status": "error",
                    "error": str(exc),
                }
            )

    successful_runs = [
        run for run in all_runs if run.get("status") != "error" and run.get("validationMae") is not None
    ]

    best_by_method: Dict[str, Dict[str, Any]] = {}
    for run in successful_runs:
        method_name = str(run.get("method") or "unknown")
        current_best = best_by_method.get(method_name)
        if current_best is None or float(run["validationMae"]) < float(current_best["validationMae"]):
            best_by_method[method_name] = run

    ranking = sorted(
        best_by_method.values(),
        key=lambda item: (float(item["validationMae"]), float(item.get("validationRmse") or 0.0)),
    )
    recommended = ranking[0] if ranking else None

    report = {
        "status": "ok" if ranking else "unavailable",
        "updatedAt": _utc_now_iso(),
        "featureCount": len(FEATURE_NAMES),
        "featureNames": FEATURE_NAMES,
        "rows": int(len(dataset["frame"])),
        "trainingRows": int(len(dataset["trainingFrame"])),
        "validationRows": int(len(dataset["validationFrame"])),
        "symbols": int(dataset["frame"]["symbol"].nunique()),
        "mapDates": int(dataset["frame"]["map_date"].nunique()),
        "targetHorizon": "next_trading_day_return_pct",
        "downstreamModel": "ridge",
        "componentGrid": dimensions,
        "methodsCompared": [
            "identity",
            "direct_feature_selection",
            "supervised_pca",
            "pls",
            "lda",
            "kernel_pca",
            "autoencoder",
        ],
        "allRuns": successful_runs,
        "errors": [run for run in all_runs if run.get("status") == "error"],
        "bestByMethod": ranking,
        "recommendedMethod": recommended,
    }
    if recommended is not None:
        report["summary"] = (
            f"{recommended['method']} was the strongest validator on the current next-day-return split "
            f"with MAE {float(recommended['validationMae']):.4f} and latent dimension {int(recommended['latentDim'])}."
        )
    else:
        report["summary"] = "No benchmark method completed successfully."
    return report


def load_or_run_feature_selection_benchmark(
    *,
    force_refresh: bool = False,
    max_age_hours: float = FEATURE_SELECTION_BENCHMARK_MAX_AGE_HOURS,
) -> Dict[str, Any]:
    cached = _load_cached_report()
    if (
        not force_refresh
        and cached is not None
        and _is_report_fresh(cached, max_age_hours=max_age_hours)
    ):
        return cached

    report = benchmark_feature_reduction_methods()
    if report.get("status") == "ok":
        _write_report(report)
        return report

    if cached is not None:
        return {
            **cached,
            "status": "stale-cache",
            "error": report.get("error")
            or cached.get("error")
            or "The benchmark could not refresh, so the last cached report is being used.",
        }
    return report


def main() -> int:
    report = load_or_run_feature_selection_benchmark(force_refresh=True)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
