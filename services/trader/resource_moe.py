from __future__ import annotations

import math
import os
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

import numpy as np
import pandas as pd


PROFILE_THRESHOLDS = {
    "cheap": {"timesfm": 0.72, "correlation": 0.68},
    "balanced": {"timesfm": 0.55, "correlation": 0.45},
    "quality": {"timesfm": 0.36, "correlation": 0.25},
}


def _safe_float(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _clip(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(upper, max(lower, value))


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_expert_set(name: str) -> set[str]:
    return {
        item.strip().lower()
        for item in os.getenv(name, "").split(",")
        if item.strip()
    }


def resource_moe_enabled() -> bool:
    return _env_flag("ENABLE_RESOURCE_MOE", True)


def resource_moe_profile() -> str:
    profile = os.getenv("MOE_PROFILE", "balanced").strip().lower()
    return profile if profile in PROFILE_THRESHOLDS else "balanced"


def _threshold(expert: str) -> float:
    return PROFILE_THRESHOLDS[resource_moe_profile()].get(expert, 0.5)


def _recent_return_stats(raw_df: pd.DataFrame) -> Dict[str, Optional[float]]:
    try:
        close = pd.to_numeric(raw_df["close"], errors="coerce").dropna()
    except Exception:
        return {"recentVolatility": None, "recentAbsoluteMove": None}
    if len(close) < 8:
        return {"recentVolatility": None, "recentAbsoluteMove": None}

    returns = close.pct_change().dropna().tail(80)
    if returns.empty:
        return {"recentVolatility": None, "recentAbsoluteMove": None}

    return {
        "recentVolatility": _safe_float(float(returns.std(ddof=0))),
        "recentAbsoluteMove": _safe_float(float(returns.abs().mean())),
    }


def _base_gate(expert: str) -> Dict[str, Any]:
    return {
        "expert": expert,
        "enabled": resource_moe_enabled(),
        "profile": resource_moe_profile(),
        "run": True,
        "reason": "moe disabled; expert runs by default",
        "score": None,
        "threshold": _threshold(expert),
        "signals": {},
    }


def _finalize_gate(expert: str, score: float, signals: Dict[str, Optional[float]]) -> Dict[str, Any]:
    gate = _base_gate(expert)
    forced = expert in _env_expert_set("MOE_FORCE_EXPERTS")
    disabled = expert in _env_expert_set("MOE_DISABLE_EXPERTS")
    threshold = _threshold(expert)

    gate.update(
        {
            "score": _safe_float(score),
            "threshold": threshold,
            "signals": signals,
        }
    )

    if not resource_moe_enabled():
        return gate
    if disabled:
        gate["run"] = False
        gate["reason"] = "expert disabled by MOE_DISABLE_EXPERTS"
        return gate
    if forced:
        gate["run"] = True
        gate["reason"] = "expert forced by MOE_FORCE_EXPERTS"
        return gate

    gate["run"] = score >= threshold
    gate["reason"] = (
        "signal score cleared resource gate"
        if gate["run"]
        else "signal score below resource gate; use cheaper experts"
    )
    return gate


def evaluate_timesfm_gate(
    raw_df: pd.DataFrame,
    *,
    cadence_profile: str,
    current_price: Optional[float],
    curve_moments: Optional[Dict[str, Any]] = None,
    spike_profile: Optional[Dict[str, Any]] = None,
    drawdown_profile: Optional[Dict[str, Any]] = None,
    symbol: Optional[str] = None,
) -> Dict[str, Any]:
    stats = _recent_return_stats(raw_df)
    curve_moments = curve_moments or {}
    spike_profile = spike_profile or {}
    drawdown_profile = drawdown_profile or {}

    recent_vol = _safe_float(stats.get("recentVolatility")) or 0.0
    recent_move = _safe_float(stats.get("recentAbsoluteMove")) or 0.0
    max_drawdown_pct = _safe_float(drawdown_profile.get("max_drawdown_pct")) or 0.0
    drawdown_linger_seconds = _safe_float(drawdown_profile.get("drawdown_linger_seconds")) or 0.0
    max_spike_pct = _safe_float(spike_profile.get("max_spike_pct")) or 0.0
    spike_sustain_seconds = _safe_float(spike_profile.get("spike_sustain_seconds")) or 0.0
    first_moment = abs(_safe_float(curve_moments.get("first_moment_pct_per_hour")) or 0.0)
    second_moment = abs(_safe_float(curve_moments.get("second_moment_pct_per_hour2")) or 0.0)

    linger_days = drawdown_linger_seconds / 86_400.0
    spike_days = spike_sustain_seconds / 86_400.0
    signals = {
        "recentVolatility": _safe_float(recent_vol),
        "recentAbsoluteMove": _safe_float(recent_move),
        "drawdownSeverity": _safe_float(abs(min(0.0, max_drawdown_pct))),
        "drawdownLingerDays": _safe_float(linger_days),
        "spikeMagnitude": _safe_float(max(0.0, max_spike_pct)),
        "spikeSustainDays": _safe_float(spike_days),
        "firstMomentMagnitude": _safe_float(first_moment),
        "secondMomentMagnitude": _safe_float(second_moment),
        "currentPrice": _safe_float(current_price),
    }

    score = (
        _clip(recent_vol / 0.045) * 0.20
        + _clip(abs(min(0.0, max_drawdown_pct)) / 0.06) * 0.24
        + _clip(max(0.0, max_spike_pct) / 0.05) * 0.22
        + _clip(max(linger_days, spike_days) / 6.0) * 0.14
        + _clip(recent_move / 0.032) * 0.08
        + _clip(first_moment * 1_200.0) * 0.05
        + _clip(second_moment * 75_000.0) * 0.07
    )

    gate = _finalize_gate("timesfm", score, signals)
    gate["cadenceProfile"] = cadence_profile
    gate["symbol"] = symbol
    return gate


def evaluate_correlation_gate(
    symbol: str,
    raw_df: pd.DataFrame,
    decision: Dict[str, Any],
    tail_diagnostics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    stats = _recent_return_stats(raw_df)
    tail_diagnostics = tail_diagnostics or {}

    recent_vol = _safe_float(stats.get("recentVolatility")) or 0.0
    avg_uncertainty = _safe_float(decision.get("avg_uncertainty_ratio")) or 0.0
    direction_strength = abs(_safe_float(decision.get("direction_strength")) or 0.0)
    heavy_tail = _safe_float(tail_diagnostics.get("heavyTailScore")) or 0.0
    left_tail = _safe_float(tail_diagnostics.get("leftTailRiskScore")) or 0.0
    long_tail = _safe_float(tail_diagnostics.get("longTailScore")) or 0.0
    geodesic_risk = _safe_float(decision.get("geodesic_regime_shift_risk")) or 0.0

    signals = {
        "recentVolatility": _safe_float(recent_vol),
        "avgUncertaintyRatio": _safe_float(avg_uncertainty),
        "directionStrength": _safe_float(direction_strength),
        "heavyTailScore": _safe_float(heavy_tail),
        "leftTailRiskScore": _safe_float(left_tail),
        "longTailScore": _safe_float(long_tail),
        "geodesicRegimeShiftRisk": _safe_float(geodesic_risk),
    }
    score = (
        _clip(recent_vol / 0.04) * 0.18
        + _clip(avg_uncertainty / 0.22) * 0.16
        + _clip(direction_strength / 0.18) * 0.12
        + _clip(heavy_tail) * 0.20
        + _clip(left_tail) * 0.18
        + _clip(long_tail) * 0.08
        + _clip(geodesic_risk) * 0.08
    )

    gate = _finalize_gate("correlation", score, signals)
    gate["symbol"] = symbol
    return gate


def build_moe_runtime(*gates: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    expert_gates = {
        str(gate.get("expert")): gate
        for gate in gates
        if isinstance(gate, dict) and gate.get("expert")
    }
    active = [name for name, gate in expert_gates.items() if gate.get("run")]
    skipped = [name for name, gate in expert_gates.items() if gate.get("run") is False]
    return {
        "enabled": resource_moe_enabled(),
        "profile": resource_moe_profile(),
        "activeExperts": active,
        "skippedExperts": skipped,
        "experts": expert_gates,
        "budget": {
            "maxHeavyInFlight": _max_heavy_in_flight(),
        },
    }


def build_skipped_correlation_forecast(symbol: str, gate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "skipped",
        "symbol": symbol,
        "reason": gate.get("reason") or "skipped by resource MoE gate",
        "moeGate": gate,
    }


def _max_heavy_in_flight() -> int:
    try:
        return max(1, int(os.getenv("MOE_MAX_HEAVY_IN_FLIGHT", "1")))
    except ValueError:
        return 1


_HEAVY_EXPERT_SEMAPHORE = threading.BoundedSemaphore(_max_heavy_in_flight())


@contextmanager
def heavy_expert_slot(expert: str) -> Iterator[bool]:
    if not resource_moe_enabled():
        yield True
        return

    acquired = _HEAVY_EXPERT_SEMAPHORE.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            _HEAVY_EXPERT_SEMAPHORE.release()
