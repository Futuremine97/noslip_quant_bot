from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from services.trader.sp500_information_map import safe_float

EPSILON = 1e-9
DEFAULT_ITERATIONS = 18
DEFAULT_STEP_SIZE = 0.24
DEFAULT_TEMPERATURE = 0.78


def seconds_to_days(value: Optional[float]) -> float:
    numeric = safe_float(value)
    if numeric is None or not np.isfinite(numeric):
        return 0.0
    return max(0.0, float(numeric) / 86_400.0)


def _safe_array(values: List[float]) -> np.ndarray:
    array = np.asarray([max(EPSILON, float(value)) for value in values], dtype=float)
    total = float(array.sum())
    if total <= 0:
        return np.full((len(values),), 1.0 / max(1, len(values)), dtype=float)
    return array / total


def _softmax(logits: np.ndarray, temperature: float) -> np.ndarray:
    safe_temperature = max(0.2, float(temperature))
    shifted = logits / safe_temperature
    shifted = shifted - float(np.max(shifted))
    exp_values = np.exp(np.clip(shifted, -40.0, 40.0))
    exp_values = np.maximum(exp_values, EPSILON)
    return exp_values / float(exp_values.sum())


def _project_box_simplex(
    weights: np.ndarray,
    *,
    min_weight: float,
    max_weight: float,
) -> np.ndarray:
    count = int(weights.size)
    if count == 0:
        return weights

    safe_min = max(0.0, float(min_weight))
    safe_max = max(safe_min, float(max_weight))
    if safe_min * count >= 1.0:
        return np.full((count,), 1.0 / count, dtype=float)

    weights = _safe_array(weights.tolist())
    projected = np.zeros((count,), dtype=float)
    remaining = set(range(count))
    remaining_weight = 1.0

    while remaining:
        remainder = weights[list(remaining)]
        remainder_sum = float(remainder.sum())
        if remainder_sum <= 0:
            equal_weight = remaining_weight / max(1, len(remaining))
            for index in remaining:
                projected[index] = equal_weight
            break

        changed = False
        for index in list(remaining):
            proposed = remaining_weight * (weights[index] / remainder_sum)
            if proposed < safe_min:
                projected[index] = safe_min
                remaining_weight -= safe_min
                remaining.remove(index)
                changed = True
            elif proposed > safe_max:
                projected[index] = safe_max
                remaining_weight -= safe_max
                remaining.remove(index)
                changed = True

        if not changed:
            for index in remaining:
                projected[index] = remaining_weight * (weights[index] / remainder_sum)
            break

    projected = np.maximum(projected, EPSILON)
    return projected / float(projected.sum())


def _kl_divergence(p: np.ndarray, q: np.ndarray) -> float:
    p_safe = np.clip(p, EPSILON, None)
    q_safe = np.clip(q, EPSILON, None)
    return float(np.sum(p_safe * np.log(p_safe / q_safe)))


def _entropy(weights: np.ndarray) -> float:
    safe_weights = np.clip(weights, EPSILON, None)
    return float(-np.sum(safe_weights * np.log(safe_weights)))


def _fisher_trace(weights: np.ndarray) -> float:
    return float(max(0.0, 1.0 - np.sum(np.square(weights))))


def _holding_utilities(item: Dict[str, Any]) -> Dict[str, float]:
    upside = max(0.0, safe_float(item.get("maxUpsidePct")) or 0.0)
    expected_return = max(0.0, safe_float(item.get("expectedReturnPct")) or 0.0)
    uncertainty = max(0.0, safe_float(item.get("uncertaintyRatio")) or 0.0)
    volatility = max(
        0.0,
        (safe_float(item.get("annualizedVolatilityPct")) or 0.0) / 100.0,
    )
    trajectory = item.get("trajectory") or {}
    persistence = max(0.0, safe_float(trajectory.get("persistenceScore")) or 0.0)
    continuation = safe_float(trajectory.get("continuationBias")) or 0.0
    regime_risk = max(0.0, safe_float(trajectory.get("regimeShiftRisk")) or 0.0)
    drawdown_linger_days = seconds_to_days(item.get("drawdownLingerSeconds"))
    spike_sustain_days = seconds_to_days(item.get("spikeSustainSeconds"))
    max_drawdown_pct = abs(min(0.0, safe_float(item.get("maxDrawdownPct")) or 0.0))
    max_spike_pct = max(0.0, safe_float(item.get("maxSpikePct")) or 0.0)
    geometry_alignment = max(
        0.0,
        safe_float(item.get("geometryAlignmentScore"))
        or safe_float(((item.get("_geometry") or {}).get("alignment_score")))
        or 0.0,
    )
    dark_horse = max(0.0, (safe_float(item.get("darkHorseScore")) or 0.0) / 100.0)
    belief = max(0.0, min(1.0, (safe_float(item.get("beliefScore")) or 0.0) / 100.0))
    web_neural_score = safe_float(item.get("webNeuralScore")) or 0.0
    web_neural_confidence = max(
        0.0,
        min(1.0, safe_float(item.get("webNeuralConfidence")) or 0.0),
    )
    champion_prophet = item.get("_championProphet") or item.get("championProphet") or {}
    champion_avg_reward = safe_float(champion_prophet.get("avgReward")) or 0.0
    champion_alignment = max(
        0.0,
        safe_float(champion_prophet.get("alignmentScore")) or 0.0,
    )
    small_cap_tail = max(0.0, safe_float(item.get("smallCapTailScore")) or 0.0)
    heavy_tail = max(0.0, safe_float(item.get("heavyTailScore")) or 0.0)
    heavy_tail_premium = max(0.0, safe_float(item.get("heavyTailPremium")) or 0.0)
    reddit_snapshot = item.get("_redditSmallCap") or item.get("redditSmallCap") or {}
    reddit_heat = max(0.0, min(1.0, safe_float(reddit_snapshot.get("heatScore")) or 0.0))
    fmkorea_snapshot = item.get("_fmkoreaStock") or item.get("fmkoreaStock") or {}
    fmkorea_heat = max(0.0, min(1.0, safe_float(fmkorea_snapshot.get("heatScore")) or 0.0))
    fmkorea_direct_surge = max(0.0, min(1.0, safe_float(item.get("fmkoreaSurgeScore")) or 0.0))

    reward_signal = (
        upside * 4.4
        + expected_return * 2.8
        + persistence * 1.55
        + continuation * 0.35
        + geometry_alignment * 1.6
        + spike_sustain_days * 0.06
        + max_spike_pct * 1.55
        + dark_horse * 0.34
        + belief * 0.72
        + web_neural_score * 14.0 * max(0.25, web_neural_confidence)
        + champion_avg_reward * 0.82
        + champion_alignment * 0.58
        + small_cap_tail * 0.24
        + heavy_tail * 0.34
        + heavy_tail_premium * (1.05 + reddit_heat * 0.35)
        + fmkorea_heat * 0.16
        + fmkorea_direct_surge * 0.32
    )
    risk_signal = (
        uncertainty * 3.3
        + regime_risk * 1.85
        + drawdown_linger_days * 0.11
        + max_drawdown_pct * 2.45
        + volatility * 0.38
    )
    conservative_penalty = (
        uncertainty * 1.3
        + regime_risk * 0.8
        + drawdown_linger_days * 0.06
        + max_drawdown_pct * 1.0
        + volatility * 0.24
        + max(0.0, 0.55 - belief) * 0.32
        + small_cap_tail * 0.06
        + heavy_tail_premium * 0.28
    )
    live_utility = reward_signal - risk_signal
    bound_utility = reward_signal * 0.78 - risk_signal * 0.92 - conservative_penalty

    return {
        "live": float(live_utility),
        "bound": float(bound_utility),
        "reward": float(reward_signal),
        "risk": float(risk_signal + conservative_penalty),
    }


def _natural_flow(
    initial_weights: np.ndarray,
    utilities: np.ndarray,
    *,
    anchor_distribution: np.ndarray,
    iterations: int,
    step_size: float,
    anchor_strength: float,
    concentration_strength: float,
    min_weight: float,
    max_weight: float,
) -> Dict[str, Any]:
    weights = _project_box_simplex(
        initial_weights,
        min_weight=min_weight,
        max_weight=max_weight,
    )
    mean_weight = 1.0 / max(1, weights.size)
    path = [weights.copy()]
    velocities: List[np.ndarray] = []

    for _ in range(max(1, iterations)):
        anchor_term = anchor_strength * (
            np.log(np.clip(anchor_distribution, EPSILON, None))
            - np.log(np.clip(weights, EPSILON, None))
        )
        concentration_term = concentration_strength * (mean_weight - weights)
        fisher_gradient = utilities + anchor_term + concentration_term
        fisher_gradient = fisher_gradient - float(np.dot(weights, fisher_gradient))
        velocity = weights * fisher_gradient
        weights = _project_box_simplex(
            weights + step_size * velocity,
            min_weight=min_weight,
            max_weight=max_weight,
        )
        velocities.append(velocity)
        path.append(weights.copy())

    curvature = 0.0
    if len(velocities) >= 2:
        curvature = float(
            np.mean(
                [
                    np.linalg.norm(next_velocity - current_velocity)
                    for current_velocity, next_velocity in zip(velocities[:-1], velocities[1:])
                ]
            )
        )

    return {
        "weights": weights,
        "path": path,
        "curvature": curvature,
    }


def build_natural_gradient_bound_report(
    holdings: List[Dict[str, Any]],
    initial_weights: List[float],
    *,
    min_weight: float,
    max_weight: float,
    iterations: int = DEFAULT_ITERATIONS,
    step_size: float = DEFAULT_STEP_SIZE,
    temperature: float = DEFAULT_TEMPERATURE,
) -> Dict[str, Any]:
    if not holdings or not initial_weights or len(holdings) != len(initial_weights):
        return {
            "method": "Fisher-simplex natural-gradient bound flow",
            "metric": "Categorical Fisher information metric",
            "iterations": 0,
            "stepSize": step_size,
            "temperature": temperature,
            "upperBoundScore": None,
            "liveDistanceToTarget": None,
            "boundDistanceToTarget": None,
            "liveDistanceToBound": None,
            "liveEntropy": None,
            "boundEntropy": None,
            "fisherTrace": None,
            "fisherCurvature": None,
            "riskEnvelopeStrength": None,
            "targetConcentration": None,
            "boundConcentration": None,
            "perHolding": [],
        }

    utilities = [_holding_utilities(item) for item in holdings]
    initial_distribution = _project_box_simplex(
        _safe_array(initial_weights),
        min_weight=min_weight,
        max_weight=max_weight,
    )
    live_logits = np.asarray([entry["live"] for entry in utilities], dtype=float)
    bound_logits = np.asarray([entry["bound"] for entry in utilities], dtype=float)
    target_distribution = _project_box_simplex(
        _softmax(live_logits, temperature),
        min_weight=min_weight,
        max_weight=max_weight,
    )
    bound_target_distribution = _project_box_simplex(
        _softmax(bound_logits, temperature * 0.92),
        min_weight=min_weight,
        max_weight=max_weight,
    )
    risk_envelope_strength = 0.62
    live_anchor_distribution = _safe_array(
        (
            bound_target_distribution * risk_envelope_strength
            + target_distribution * 0.26
            + initial_distribution * (1.0 - risk_envelope_strength - 0.26)
        ).tolist()
    )

    live_flow = _natural_flow(
        initial_distribution,
        live_logits,
        anchor_distribution=live_anchor_distribution,
        iterations=iterations,
        step_size=step_size,
        anchor_strength=0.56,
        concentration_strength=0.48,
        min_weight=min_weight,
        max_weight=max_weight,
    )
    bound_flow = _natural_flow(
        initial_distribution,
        bound_logits,
        anchor_distribution=bound_target_distribution,
        iterations=iterations,
        step_size=step_size * 0.9,
        anchor_strength=0.88,
        concentration_strength=0.72,
        min_weight=min_weight,
        max_weight=max_weight,
    )

    live_weights = live_flow["weights"]
    bound_weights = bound_flow["weights"]
    live_distance_to_target = _kl_divergence(live_weights, target_distribution)
    bound_distance_to_target = _kl_divergence(bound_weights, bound_target_distribution)
    live_distance_to_bound = _kl_divergence(live_weights, bound_weights)
    fisher_trace = _fisher_trace(live_weights)
    fisher_curvature = max(float(live_flow["curvature"]), float(bound_flow["curvature"]))
    live_entropy = _entropy(live_weights)
    bound_entropy = _entropy(bound_weights)
    target_concentration = float(np.sum(np.square(target_distribution)))
    bound_concentration = float(np.sum(np.square(bound_weights)))
    upper_bound_score = float(
        1.0
        / (
            1.0
            + live_distance_to_target
            + bound_distance_to_target * 0.55
            + live_distance_to_bound * 0.8
            + fisher_curvature * 0.65
            + max(0.0, bound_concentration - target_concentration) * 0.4
        )
    )

    per_holding: List[Dict[str, Any]] = []
    for index, item in enumerate(holdings):
        utility = utilities[index]
        per_holding.append(
            {
                "symbol": item.get("symbol"),
                "liveWeightPct": safe_float(live_weights[index] * 100.0),
                "targetWeightPct": safe_float(target_distribution[index] * 100.0),
                "boundWeightPct": safe_float(bound_weights[index] * 100.0),
                "initialWeightPct": safe_float(initial_distribution[index] * 100.0),
                "utilityScore": safe_float(utility["live"]),
                "boundUtilityScore": safe_float(utility["bound"]),
                "rewardSignal": safe_float(utility["reward"]),
                "riskSignal": safe_float(utility["risk"]),
                "liftPct": safe_float((live_weights[index] - initial_distribution[index]) * 100.0),
            }
        )

    return {
        "method": (
            "Fisher-simplex natural-gradient bound flow inspired by "
            "information-geometric upper-envelope dynamics"
        ),
        "metric": "Categorical Fisher information metric",
        "iterations": int(iterations),
        "stepSize": safe_float(step_size),
        "temperature": safe_float(temperature),
        "upperBoundScore": safe_float(upper_bound_score),
        "liveDistanceToTarget": safe_float(live_distance_to_target),
        "boundDistanceToTarget": safe_float(bound_distance_to_target),
        "liveDistanceToBound": safe_float(live_distance_to_bound),
        "liveEntropy": safe_float(live_entropy),
        "boundEntropy": safe_float(bound_entropy),
        "fisherTrace": safe_float(fisher_trace),
        "fisherCurvature": safe_float(fisher_curvature),
        "riskEnvelopeStrength": safe_float(risk_envelope_strength),
        "targetConcentration": safe_float(target_concentration),
        "boundConcentration": safe_float(bound_concentration),
        "perHolding": per_holding,
    }
