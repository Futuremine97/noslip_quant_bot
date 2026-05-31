from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import numpy as np
import pandas as pd

TIMESFM_REPO_PATH = os.getenv("TIMESFM_REPO_PATH", "").strip()
if TIMESFM_REPO_PATH:
    repo_root = Path(TIMESFM_REPO_PATH).expanduser()
    candidate_paths = [repo_root / "src", repo_root]
    for candidate in candidate_paths:
        if candidate.exists():
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)

try:
    import timesfm  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    timesfm = None

try:
    import torch  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    torch = None


TIMESFM_ENABLED = os.getenv("ENABLE_TIMESFM_DRAWDOWN", "true").lower() == "true"
TIMESFM_MODEL_ID = os.getenv("TIMESFM_MODEL_ID", "google/timesfm-2.5-200m-pytorch").strip()
TIMESFM_INTRADAY_CONTEXT = int(os.getenv("TIMESFM_INTRADAY_CONTEXT", "256"))
TIMESFM_DAILY_CONTEXT = int(os.getenv("TIMESFM_DAILY_CONTEXT", "512"))
TIMESFM_INTRADAY_HORIZON = int(os.getenv("TIMESFM_INTRADAY_HORIZON", "96"))
TIMESFM_DAILY_HORIZON = int(os.getenv("TIMESFM_DAILY_HORIZON", "80"))

_TIMESFM_MODEL = None
_TIMESFM_ERROR: Optional[str] = None


def safe_float(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(numeric):
        return None
    return numeric


def safe_duration_seconds(start: Any, end: Any) -> Optional[float]:
    if start is None or end is None:
        return None
    try:
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
    except Exception:
        return None
    if pd.isna(start_ts) or pd.isna(end_ts):
        return None
    return float((end_ts - start_ts).total_seconds())


def _default_result() -> Dict[str, Any]:
    return {
        "timesfm_used": False,
        "timesfm_model_id": TIMESFM_MODEL_ID,
        "timesfm_status": "unavailable",
        "timesfm_error": None,
        "timesfm_drawdown_start_timestamp": None,
        "timesfm_drawdown_recovery_timestamp": None,
        "timesfm_drawdown_trough_timestamp": None,
        "timesfm_drawdown_trough_price": None,
        "timesfm_drawdown_linger_seconds": None,
        "timesfm_drawdown_recovery_in_horizon": None,
        "timesfm_trough_to_recovery_seconds": None,
        "timesfm_max_drawdown_pct": None,
        "timesfm_quantile_band_pct": None,
        "timesfm_spike_start_timestamp": None,
        "timesfm_spike_peak_timestamp": None,
        "timesfm_spike_peak_price": None,
        "timesfm_spike_sustain_seconds": None,
        "timesfm_spike_fade_timestamp": None,
        "timesfm_spike_fade_in_horizon": None,
        "timesfm_peak_to_fade_seconds": None,
        "timesfm_max_spike_pct": None,
        "timesfm_moe_gate": None,
    }


def build_timesfm_skipped_profile(
    reason: str,
    *,
    gate: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    profile = _default_result()
    profile.update(
        {
            "timesfm_status": "skipped",
            "timesfm_error": reason,
            "timesfm_moe_gate": gate or None,
        }
    )
    return profile


def _infer_step_seconds(raw_df: pd.DataFrame, cadence_profile: str) -> int:
    ordered = raw_df[["ds"]].copy()
    ordered["ds"] = pd.to_datetime(ordered["ds"], errors="coerce")
    ordered = ordered.dropna(subset=["ds"]).sort_values("ds").reset_index(drop=True)
    if len(ordered) < 2:
        return 86400 if cadence_profile == "daily" else 3600

    deltas = ordered["ds"].diff().dropna().dt.total_seconds()
    if deltas.empty:
        return 86400 if cadence_profile == "daily" else 3600

    step = int(np.nanmedian(deltas.to_numpy(dtype=float)))
    if step <= 0:
        return 86400 if cadence_profile == "daily" else 3600
    return step


def _load_model():
    global _TIMESFM_MODEL, _TIMESFM_ERROR

    if not TIMESFM_ENABLED:
        _TIMESFM_ERROR = "TimesFM drawdown integration disabled"
        return None, _TIMESFM_ERROR

    if _TIMESFM_MODEL is not None:
        return _TIMESFM_MODEL, None

    if _TIMESFM_ERROR is not None:
        return None, _TIMESFM_ERROR

    if timesfm is None:
        _TIMESFM_ERROR = "timesfm package is not installed"
        return None, _TIMESFM_ERROR

    if torch is None:
        _TIMESFM_ERROR = "torch is not installed"
        return None, _TIMESFM_ERROR

    try:
        torch.set_float32_matmul_precision("high")
        model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(TIMESFM_MODEL_ID)
        model.compile(
            timesfm.ForecastConfig(
                max_context=max(TIMESFM_INTRADAY_CONTEXT, TIMESFM_DAILY_CONTEXT),
                max_horizon=max(TIMESFM_INTRADAY_HORIZON, TIMESFM_DAILY_HORIZON),
                normalize_inputs=True,
                use_continuous_quantile_head=True,
                force_flip_invariance=True,
                infer_is_positive=False,
                fix_quantile_crossing=True,
            )
        )
        _TIMESFM_MODEL = model
        return _TIMESFM_MODEL, None
    except Exception as exc:  # pragma: no cover - depends on runtime env
        _TIMESFM_ERROR = str(exc)
        return None, _TIMESFM_ERROR


def _extract_drawdown_profile(
    timestamps: Sequence[pd.Timestamp],
    values: Sequence[float],
    reference_price: float,
) -> Dict[str, Any]:
    default = {
        "timesfm_drawdown_start_timestamp": None,
        "timesfm_drawdown_recovery_timestamp": None,
        "timesfm_drawdown_trough_timestamp": None,
        "timesfm_drawdown_trough_price": None,
        "timesfm_drawdown_linger_seconds": None,
        "timesfm_drawdown_recovery_in_horizon": None,
        "timesfm_trough_to_recovery_seconds": None,
        "timesfm_max_drawdown_pct": None,
    }
    if not timestamps or not values or reference_price == 0:
        return default

    ordered = pd.DataFrame(
        {
            "ds": pd.to_datetime(list(timestamps), errors="coerce"),
            "value": pd.to_numeric(list(values), errors="coerce"),
        }
    ).dropna(subset=["ds", "value"])
    if len(ordered) < 2:
        return default

    below_df = ordered[ordered["value"] < float(reference_price)].copy()
    if below_df.empty:
        return default

    drawdown_start_ts = pd.Timestamp(below_df.iloc[0]["ds"])
    drawdown_start_idx = int(below_df.index[0])
    post_drawdown = ordered.iloc[drawdown_start_idx:].copy()
    if post_drawdown.empty:
        return default

    trough_idx = int(post_drawdown["value"].idxmin())
    trough_row = ordered.loc[trough_idx]
    drawdown_trough_ts = pd.Timestamp(trough_row["ds"])
    drawdown_trough_price = safe_float(trough_row["value"])

    recovery_candidates = ordered[
        (ordered.index > drawdown_start_idx) & (ordered["value"] >= float(reference_price))
    ].copy()
    recovery_ts = (
        pd.Timestamp(recovery_candidates.iloc[0]["ds"])
        if not recovery_candidates.empty
        else None
    )

    horizon_end_ts = pd.Timestamp(ordered.iloc[-1]["ds"])
    linger_end_ts = recovery_ts or horizon_end_ts
    drawdown_linger_seconds = safe_duration_seconds(drawdown_start_ts, linger_end_ts)
    trough_to_recovery_seconds = safe_duration_seconds(drawdown_trough_ts, linger_end_ts)
    max_drawdown_pct = (
        (drawdown_trough_price / float(reference_price)) - 1.0
        if drawdown_trough_price is not None
        else None
    )

    return {
        "timesfm_drawdown_start_timestamp": drawdown_start_ts,
        "timesfm_drawdown_recovery_timestamp": recovery_ts,
        "timesfm_drawdown_trough_timestamp": drawdown_trough_ts,
        "timesfm_drawdown_trough_price": drawdown_trough_price,
        "timesfm_drawdown_linger_seconds": drawdown_linger_seconds,
        "timesfm_drawdown_recovery_in_horizon": recovery_ts is not None,
        "timesfm_trough_to_recovery_seconds": trough_to_recovery_seconds,
        "timesfm_max_drawdown_pct": max_drawdown_pct,
    }


def _extract_upside_spike_profile(
    timestamps: Sequence[pd.Timestamp],
    values: Sequence[float],
    reference_price: float,
) -> Dict[str, Any]:
    default = {
        "timesfm_spike_start_timestamp": None,
        "timesfm_spike_peak_timestamp": None,
        "timesfm_spike_peak_price": None,
        "timesfm_spike_sustain_seconds": None,
        "timesfm_spike_fade_timestamp": None,
        "timesfm_spike_fade_in_horizon": None,
        "timesfm_peak_to_fade_seconds": None,
        "timesfm_max_spike_pct": None,
    }
    if not timestamps or not values or reference_price == 0:
        return default

    ordered = pd.DataFrame(
        {
            "ds": pd.to_datetime(list(timestamps), errors="coerce"),
            "value": pd.to_numeric(list(values), errors="coerce"),
        }
    ).dropna(subset=["ds", "value"])
    if len(ordered) < 2:
        return default

    above_df = ordered[ordered["value"] > float(reference_price)].copy()
    if above_df.empty:
        return default

    spike_start_ts = pd.Timestamp(above_df.iloc[0]["ds"])
    spike_start_idx = int(above_df.index[0])
    post_spike = ordered.iloc[spike_start_idx:].copy()
    if post_spike.empty:
        return default

    peak_idx = int(post_spike["value"].idxmax())
    peak_row = ordered.loc[peak_idx]
    spike_peak_ts = pd.Timestamp(peak_row["ds"])
    spike_peak_price = safe_float(peak_row["value"])
    if spike_peak_price is None or spike_peak_price <= float(reference_price):
        return default

    amplitude = spike_peak_price - float(reference_price)
    fade_threshold = float(reference_price) + amplitude * 0.4
    fade_candidates = ordered[
        (ordered.index > peak_idx) & (ordered["value"] <= fade_threshold)
    ].copy()
    fade_ts = pd.Timestamp(fade_candidates.iloc[0]["ds"]) if not fade_candidates.empty else None

    horizon_end_ts = pd.Timestamp(ordered.iloc[-1]["ds"])
    sustain_end_ts = fade_ts or horizon_end_ts
    spike_sustain_seconds = safe_duration_seconds(spike_start_ts, sustain_end_ts)
    peak_to_fade_seconds = safe_duration_seconds(spike_peak_ts, sustain_end_ts)
    max_spike_pct = spike_peak_price / float(reference_price) - 1.0

    return {
        "timesfm_spike_start_timestamp": spike_start_ts,
        "timesfm_spike_peak_timestamp": spike_peak_ts,
        "timesfm_spike_peak_price": spike_peak_price,
        "timesfm_spike_sustain_seconds": spike_sustain_seconds,
        "timesfm_spike_fade_timestamp": fade_ts,
        "timesfm_spike_fade_in_horizon": fade_ts is not None,
        "timesfm_peak_to_fade_seconds": peak_to_fade_seconds,
        "timesfm_max_spike_pct": max_spike_pct,
    }


def compute_timesfm_drawdown_profile(
    raw_df: pd.DataFrame,
    *,
    reference_price: Optional[float],
    cadence_profile: str = "intraday",
) -> Dict[str, Any]:
    default = _default_result()
    if reference_price in {None, 0}:
        default["timesfm_error"] = "missing reference price"
        return default

    model, load_error = _load_model()
    if model is None:
        default["timesfm_error"] = load_error
        return default

    ordered = raw_df[["ds", "close"]].copy()
    ordered["ds"] = pd.to_datetime(ordered["ds"], errors="coerce")
    ordered["close"] = pd.to_numeric(ordered["close"], errors="coerce")
    ordered = ordered.dropna(subset=["ds", "close"]).sort_values("ds").reset_index(drop=True)
    if len(ordered) < 48:
        default["timesfm_error"] = "insufficient history for TimesFM"
        return default

    max_context = TIMESFM_DAILY_CONTEXT if cadence_profile == "daily" else TIMESFM_INTRADAY_CONTEXT
    max_horizon = TIMESFM_DAILY_HORIZON if cadence_profile == "daily" else TIMESFM_INTRADAY_HORIZON
    context_values = ordered["close"].tail(max_context).to_numpy(dtype=float)
    if len(context_values) < 48:
        default["timesfm_error"] = "insufficient context after trimming"
        return default

    step_seconds = _infer_step_seconds(ordered, cadence_profile)

    try:
        point_forecast, quantile_forecast = model.forecast(
            horizon=max_horizon,
            inputs=[context_values],
        )
    except Exception as exc:  # pragma: no cover - runtime dependent
        default["timesfm_error"] = str(exc)
        return default

    if point_forecast is None or len(point_forecast) == 0:
        default["timesfm_error"] = "TimesFM returned empty forecast"
        return default

    forecast_values = np.asarray(point_forecast[0], dtype=float)
    last_timestamp = pd.Timestamp(ordered["ds"].iloc[-1])
    timestamps = [
        last_timestamp + pd.Timedelta(seconds=step_seconds * (index + 1))
        for index in range(len(forecast_values))
    ]
    profile = _default_result()
    profile.update(
        {
            "timesfm_used": True,
            "timesfm_model_id": TIMESFM_MODEL_ID,
            "timesfm_status": "ok",
            "timesfm_error": None,
        }
    )
    profile.update(
        _extract_drawdown_profile(
            timestamps,
            forecast_values.tolist(),
            float(reference_price),
        )
    )
    profile.update(
        _extract_upside_spike_profile(
            timestamps,
            forecast_values.tolist(),
            float(reference_price),
        )
    )

    if (
        quantile_forecast is not None
        and len(quantile_forecast) > 0
        and np.asarray(quantile_forecast[0]).ndim == 2
    ):
        quantile_array = np.asarray(quantile_forecast[0], dtype=float)
        if quantile_array.shape[1] >= 3:
            low_band = quantile_array[:, 1]
            high_band = quantile_array[:, -1]
            median_price = max(float(reference_price), 1e-6)
            profile["timesfm_quantile_band_pct"] = safe_float(
                float(np.nanmean(np.maximum(high_band - low_band, 0.0)) / median_price)
            )

    return profile
