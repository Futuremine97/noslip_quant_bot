from __future__ import annotations

import math
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


def safe_float(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def clamp01(value: Any) -> float:
    numeric = safe_float(value) or 0.0
    return float(np.clip(numeric, 0.0, 1.0))


def _sigmoid_score(value: float, *, scale: float = 1.0) -> float:
    scaled = float(np.clip(value * scale, -8.0, 8.0))
    return float(1.0 / (1.0 + math.exp(-scaled)))


def _extract_returns(raw_df: pd.DataFrame, lookback_days: int) -> np.ndarray:
    if raw_df is None or raw_df.empty or "close" not in raw_df.columns:
        return np.array([], dtype=float)
    ordered = raw_df[["close"]].copy()
    ordered["close"] = pd.to_numeric(ordered["close"], errors="coerce")
    ordered = ordered.dropna(subset=["close"]).tail(max(lookback_days + 1, 40))
    if len(ordered) < 20:
        return np.array([], dtype=float)
    returns = ordered["close"].pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    returns = returns.tail(lookback_days)
    return returns.to_numpy(dtype=float)


def _hill_tail_index(abs_returns: np.ndarray) -> Optional[float]:
    positive = np.sort(abs_returns[np.isfinite(abs_returns) & (abs_returns > 0)])
    if positive.size < 8:
        return None
    k = int(min(max(positive.size // 8, 6), positive.size - 1))
    top = positive[-k:]
    xk = positive[-k]
    if xk <= 0:
        return None
    logs = np.log(top / xk)
    denom = float(np.sum(logs))
    if denom <= 1e-12:
        return None
    return float(k / denom)


def build_tail_diagnostics(
    raw_df: pd.DataFrame,
    *,
    lookback_days: int = 252,
) -> Dict[str, Any]:
    returns = _extract_returns(raw_df, lookback_days)
    if returns.size < 20:
        return {
            "status": "insufficient_data",
            "lookbackDays": int(lookback_days),
            "sampleSize": int(returns.size),
            "skewness": None,
            "excessKurtosis": None,
            "hillTailIndex": None,
            "tailConcentration": None,
            "extremeMoveRate": None,
            "upsideTailShare": None,
            "downsideTailShare": None,
            "longTailScore": None,
            "heavyTailScore": None,
            "leftTailRiskScore": None,
            "regimeLabel": "tail-unavailable",
            "rationale": "Tail diagnostics need at least 20 return observations.",
        }

    mean = float(np.mean(returns))
    centered = returns - mean
    variance = float(np.mean(centered ** 2))
    std = math.sqrt(max(variance, 0.0))

    if std <= 1e-12:
        return {
            "status": "flat_series",
            "lookbackDays": int(lookback_days),
            "sampleSize": int(returns.size),
            "skewness": 0.0,
            "excessKurtosis": 0.0,
            "hillTailIndex": None,
            "tailConcentration": 0.0,
            "extremeMoveRate": 0.0,
            "upsideTailShare": 0.0,
            "downsideTailShare": 0.0,
            "longTailScore": 0.0,
            "heavyTailScore": 0.0,
            "leftTailRiskScore": 0.0,
            "regimeLabel": "tail-flat",
            "rationale": "Recent returns were too flat to estimate a meaningful tail shape.",
        }

    skewness = float(np.mean((centered / std) ** 3))
    excess_kurtosis = float(np.mean((centered / std) ** 4) - 3.0)

    abs_returns = np.abs(returns)
    tail_cutoff = float(np.nanquantile(abs_returns, 0.9))
    tail_mask = abs_returns >= tail_cutoff
    tail_concentration = (
        float(abs_returns[tail_mask].sum() / max(abs_returns.sum(), 1e-12))
        if abs_returns.size
        else 0.0
    )

    extreme_move_rate = float(np.mean(abs_returns >= (std * 2.0)))

    positive_moves = np.clip(returns, 0.0, None)
    negative_moves = np.clip(-returns, 0.0, None)
    positive_cutoff = float(np.nanquantile(returns, 0.9))
    negative_cutoff = float(np.nanquantile(returns, 0.1))

    upside_tail_mass = float(positive_moves[returns >= positive_cutoff].sum())
    downside_tail_mass = float(negative_moves[returns <= negative_cutoff].sum())
    upside_tail_share = upside_tail_mass / max(float(positive_moves.sum()), 1e-12)
    downside_tail_share = downside_tail_mass / max(float(negative_moves.sum()), 1e-12)

    hill_tail_index = _hill_tail_index(abs_returns)
    hill_heaviness = (
        clamp01((3.5 - hill_tail_index) / 2.5) if hill_tail_index is not None else 0.45
    )

    kurtosis_signal = clamp01((excess_kurtosis - 0.5) / 7.5)
    concentration_signal = clamp01((tail_concentration - 0.26) / 0.34)
    extreme_signal = clamp01((extreme_move_rate - 0.03) / 0.12)
    upside_signal = clamp01(upside_tail_share / 0.65)
    downside_signal = clamp01(downside_tail_share / 0.65)
    positive_skew_signal = _sigmoid_score(skewness - 0.1, scale=1.35)
    negative_skew_signal = _sigmoid_score((-skewness) - 0.1, scale=1.35)

    heavy_tail_score = clamp01(
        kurtosis_signal * 0.34
        + concentration_signal * 0.26
        + extreme_signal * 0.18
        + hill_heaviness * 0.22
    )
    long_tail_score = clamp01(
        positive_skew_signal * 0.34
        + upside_signal * 0.28
        + heavy_tail_score * 0.22
        + clamp01((skewness + 0.2) / 1.8) * 0.16
        - downside_signal * 0.12
    )
    left_tail_risk_score = clamp01(
        negative_skew_signal * 0.36
        + downside_signal * 0.30
        + heavy_tail_score * 0.22
        + clamp01((-skewness + 0.15) / 1.8) * 0.12
    )

    if heavy_tail_score >= 0.72 and long_tail_score >= left_tail_risk_score + 0.08:
        regime_label = "right-tail heavy"
    elif heavy_tail_score >= 0.72 and left_tail_risk_score >= long_tail_score + 0.08:
        regime_label = "left-tail heavy"
    elif heavy_tail_score >= 0.62:
        regime_label = "balanced heavy-tail"
    elif long_tail_score >= 0.58:
        regime_label = "right-tail extension"
    elif left_tail_risk_score >= 0.58:
        regime_label = "left-tail stress"
    else:
        regime_label = "tail-neutral"

    rationale = (
        f"Tail regime {regime_label} from skew {skewness:.2f}, excess kurtosis {excess_kurtosis:.2f}, "
        f"tail concentration {tail_concentration * 100:.0f}%, upside tail share {upside_tail_share * 100:.0f}%, "
        f"and downside tail share {downside_tail_share * 100:.0f}%."
    )

    return {
        "status": "ok",
        "lookbackDays": int(lookback_days),
        "sampleSize": int(returns.size),
        "skewness": skewness,
        "excessKurtosis": excess_kurtosis,
        "hillTailIndex": hill_tail_index,
        "tailConcentration": tail_concentration,
        "extremeMoveRate": extreme_move_rate,
        "upsideTailShare": upside_tail_share,
        "downsideTailShare": downside_tail_share,
        "longTailScore": long_tail_score,
        "heavyTailScore": heavy_tail_score,
        "leftTailRiskScore": left_tail_risk_score,
        "regimeLabel": regime_label,
        "rationale": rationale,
    }
