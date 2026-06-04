#!/usr/bin/env python3

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import logging
logging.getLogger("prophet").setLevel(logging.CRITICAL)
logging.getLogger("prophet.plot").setLevel(logging.CRITICAL)
logging.getLogger("cmdstanpy").setLevel(logging.CRITICAL)

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from services.trader import main as trader_main
from services.trader.correlation_forecast import build_portfolio_correlation_forecast
from services.trader.fmkorea_stock import build_fmkorea_stock_snapshot
from services.trader.reddit_smallcap import build_reddit_smallcap_snapshot
from services.trader.reinforcement import load_champion_reinforcement_state
from services.trader.sp500_information_map import (
    DEFAULT_CACHE_MAX_AGE_HOURS,
    DEFAULT_LIMIT,
    belief_label,
    build_sp500_information_map,
    build_symbol_metadata,
    ensure_sp500_matrix,
    safe_float,
)
from services.trader.portfolio_manifold import (
    build_champion_agent_summary,
    build_portfolio_manifold_report,
    load_recent_portfolio_history,
    persist_portfolio_history,
)
from services.trader.natural_gradient_bound import build_natural_gradient_bound_report
from services.trader.tail_diagnostics import clamp01

DEFAULT_PORTFOLIO_HOLDINGS = int(os.getenv("SP500_PORTFOLIO_HOLDINGS", "10"))
DEFAULT_CANDIDATE_LIMIT = int(os.getenv("SP500_PORTFOLIO_CANDIDATES", "24"))
DEFAULT_TRAILING_DAYS = int(os.getenv("SP500_PORTFOLIO_TRAILING_DAYS", "63"))
MAX_PER_SECTOR = int(os.getenv("SP500_PORTFOLIO_MAX_PER_SECTOR", "2"))
MIN_WEIGHT = float(os.getenv("SP500_PORTFOLIO_MIN_WEIGHT", "0.04"))
MAX_WEIGHT = float(os.getenv("SP500_PORTFOLIO_MAX_WEIGHT", "0.18"))
GEOMETRY_TARGET_POOL = int(os.getenv("SP500_PORTFOLIO_GEOMETRY_POOL", "18"))
CHAMPION_TASK_WEIGHTS = {"direction": 0.5, "low": 0.25, "high": 0.25}
CHAMPION_RULE_WEIGHTS = {"20D": 0.5, "5D": 0.3, "1D": 0.2}


class PandasAndNumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        from datetime import date, timedelta
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


@dataclass(frozen=True)
class PortfolioProfile:
    name: str
    label: str
    holdings: int = DEFAULT_PORTFOLIO_HOLDINGS
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT
    min_weight: float = MIN_WEIGHT
    max_weight: float = MAX_WEIGHT
    upside_multiplier: float = 1.0
    uncertainty_penalty: float = 1.0
    persistence_multiplier: float = 1.0
    regime_penalty: float = 1.0
    drawdown_penalty: float = 1.0
    geometry_multiplier: float = 1.0
    turnover_multiplier: float = 1.0
    volatility_penalty: float = 1.0
    description: str = ""


PORTFOLIO_PROFILES: List[PortfolioProfile] = [
    PortfolioProfile(
        name="balanced_submanifold",
        label="Balanced submanifold agent",
        description="Balanced baseline that stays close to the learned portfolio submanifold while preserving upside.",
    ),
    PortfolioProfile(
        name="drawdown_guard",
        label="Drawdown guard agent",
        upside_multiplier=0.88,
        uncertainty_penalty=1.2,
        regime_penalty=1.25,
        drawdown_penalty=1.35,
        geometry_multiplier=1.05,
        turnover_multiplier=0.9,
        volatility_penalty=1.15,
        description="Biases toward resilience when drawdown linger and regime risk are elevated.",
    ),
    PortfolioProfile(
        name="trend_capture",
        label="Trend capture agent",
        upside_multiplier=1.18,
        persistence_multiplier=1.14,
        geometry_multiplier=1.1,
        turnover_multiplier=1.1,
        uncertainty_penalty=0.94,
        drawdown_penalty=0.88,
        description="Leans into persistent upside and faster turnover when the manifold is trending cleanly.",
    ),
    PortfolioProfile(
        name="rotation_relief",
        label="Rotation relief agent",
        candidate_limit=max(DEFAULT_CANDIDATE_LIMIT, 30),
        holdings=max(DEFAULT_PORTFOLIO_HOLDINGS, 12),
        min_weight=0.035,
        max_weight=0.14,
        persistence_multiplier=1.06,
        regime_penalty=0.92,
        geometry_multiplier=1.18,
        turnover_multiplier=1.02,
        description="Prefers sector rotation breadth and geometry alignment when the market is rebalancing.",
    ),
]


def seconds_to_days(value: Optional[float]) -> Optional[float]:
    if value is None or not np.isfinite(value):
        return None
    return float(value) / 86_400.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an explainable S&P500 portfolio from information-map outputs."
    )
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--holdings", type=int, default=DEFAULT_PORTFOLIO_HOLDINGS)
    parser.add_argument("--candidate-limit", type=int, default=DEFAULT_CANDIDATE_LIMIT)
    parser.add_argument("--trailing-days", type=int, default=DEFAULT_TRAILING_DAYS)
    parser.add_argument("--map-limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument(
        "--cache-max-age-hours",
        type=float,
        default=DEFAULT_CACHE_MAX_AGE_HOURS,
    )
    return parser.parse_args()


def annualized_volatility(close_matrix: pd.DataFrame, symbol: str, trailing_days: int) -> Optional[float]:
    if symbol not in close_matrix.columns:
        return None

    prices = pd.to_numeric(close_matrix[symbol], errors="coerce").dropna().tail(trailing_days + 1)
    if len(prices) < 21:
        return None

    returns = prices.pct_change().dropna()
    if returns.empty:
        return None

    return safe_float(float(returns.std(ddof=0) * np.sqrt(252.0)))


def classify_turnover_potential(item: Dict[str, Any]) -> str:
    candidates = [
        safe_float(item.get("timeToOptimalBuySeconds")),
        safe_float(item.get("timeToOptimalSellSeconds")),
    ]
    valid = [value for value in candidates if value is not None and value > 0]
    if not valid:
        return "unknown"

    best_seconds = min(valid)
    if best_seconds <= 5 * 24 * 3600:
        return "high"
    if best_seconds <= 20 * 24 * 3600:
        return "medium"
    return "low"


def turnover_bonus(turnover: str) -> float:
    if turnover == "high":
        return 0.18
    if turnover == "medium":
        return 0.08
    if turnover == "low":
        return 0.03
    return 0.0


def classify_market_cap_bucket(market_cap: Optional[float]) -> str:
    numeric = safe_float(market_cap)
    if numeric is None or numeric <= 0:
        return "unknown"
    if numeric >= 200_000_000_000:
        return "mega-cap"
    if numeric >= 20_000_000_000:
        return "large-cap"
    if numeric >= 5_000_000_000:
        return "mid-cap"
    if numeric >= 500_000_000:
        return "small-cap"
    return "micro-cap"


def compute_small_cap_heavy_tail_metrics(
    item: Dict[str, Any],
    volatility: Optional[float],
    market_cap: Optional[float] = None,
) -> Dict[str, Any]:
    numeric_market_cap = safe_float(market_cap) or safe_float(item.get("marketCap"))
    market_cap_bucket = classify_market_cap_bucket(numeric_market_cap)
    proxy_small_cap = max(
        0.0,
        min(1.0, (safe_float(item.get("smallCapTailProxyScore")) or 50.0) / 100.0),
    )
    proxy_heavy_tail = max(
        0.0,
        min(1.0, (safe_float(item.get("heavyTailProxyScore")) or 50.0) / 100.0),
    )
    tail_diagnostics = item.get("tailDiagnostics") or {}
    actual_heavy_tail = clamp01((tail_diagnostics.get("heavyTailScore")))
    actual_long_tail = clamp01((tail_diagnostics.get("longTailScore")))
    left_tail_risk = clamp01((tail_diagnostics.get("leftTailRiskScore")))
    tail_skewness = safe_float(tail_diagnostics.get("skewness"))
    tail_excess_kurtosis = safe_float(tail_diagnostics.get("excessKurtosis"))
    tail_regime_label = str(tail_diagnostics.get("regimeLabel") or "tail-neutral")
    if numeric_market_cap is not None and numeric_market_cap > 0:
        small_cap_signal = max(
            0.0,
            min(1.0, (10.2 - float(np.log10(max(numeric_market_cap, 1_000_000.0)))) / 2.8),
        )
    else:
        small_cap_signal = proxy_small_cap

    volatility_signal = max(0.0, min(1.0, ((volatility or 0.22) - 0.16) / 0.46))
    spike_signal = max(
        0.0,
        min(1.0, (safe_float(item.get("maxSpikePct")) or 0.0) / 0.32),
    )
    sustain_signal = max(
        0.0,
        min(
            1.0,
            (seconds_to_days(safe_float(item.get("spikeSustainSeconds"))) or 0.0) / 24.0,
        ),
    )
    downside_penalty = max(
        0.0,
        min(1.0, abs(min(0.0, safe_float(item.get("maxDrawdownPct")) or 0.0)) / 0.22),
    )
    persistence_signal = max(
        0.0,
        min(
            1.0,
            safe_float((item.get("trajectory") or {}).get("persistenceScore")) or 0.0,
        ),
    )
    heavy_tail_score = max(
        0.0,
        min(
            1.0,
            small_cap_signal * 0.38
            + proxy_heavy_tail * 0.18
            + actual_heavy_tail * 0.24
            + actual_long_tail * 0.10
            + volatility_signal * 0.12
            + spike_signal * 0.14
            + sustain_signal * 0.08
            + persistence_signal * 0.08
            - downside_penalty * 0.08
            - left_tail_risk * 0.12,
        ),
    )
    upside = max(0.0, safe_float(item.get("maxUpsidePct")) or 0.0)
    uncertainty = max(0.0, safe_float(item.get("uncertaintyRatio")) or 0.0)
    heavy_tail_premium = max(
        0.0,
        heavy_tail_score
        * max(
            0.0,
            upside * 2.6
            + proxy_heavy_tail * 0.14
            + actual_long_tail * 0.26
            + spike_signal * 0.1,
        )
        * max(0.28, 1.0 - uncertainty * 2.4)
        * max(0.42, 1.0 - left_tail_risk * 0.58),
    )
    label = (
        "small-cap tail engine"
        if heavy_tail_score >= 0.74
        else "tail-optional"
        if heavy_tail_score >= 0.56
        else "tail-muted"
    )
    rationale = (
        f"{label} driven by {market_cap_bucket}, {tail_regime_label}, spike sustain, volatility, symmetry, "
        f"and downside balance"
    )
    return {
        "marketCap": numeric_market_cap,
        "marketCapBucket": market_cap_bucket,
        "smallCapTailScore": small_cap_signal,
        "heavyTailScore": heavy_tail_score,
        "heavyTailPremium": heavy_tail_premium,
        "heavyTailLabel": label,
        "heavyTailRationale": rationale,
        "tailRegimeLabel": tail_regime_label,
        "tailSkewness": tail_skewness,
        "tailExcessKurtosis": tail_excess_kurtosis,
        "tailLongScore": actual_long_tail,
        "tailLeftRiskScore": left_tail_risk,
        "tailConcentration": safe_float(tail_diagnostics.get("tailConcentration")),
        "tailExtremeMoveRate": safe_float(tail_diagnostics.get("extremeMoveRate")),
    }


def aggregate_champion_prophet_state(symbol: str) -> Dict[str, float]:
    weighted_reward = 0.0
    weighted_reward_count = 0.0
    weighted_preferred_cps = 0.0
    used_weight = 0.0

    for task, task_weight in CHAMPION_TASK_WEIGHTS.items():
        for rule, rule_weight in CHAMPION_RULE_WEIGHTS.items():
            state = load_champion_reinforcement_state(symbol, task, rule)
            if not state:
                continue
            unit_weight = float(task_weight * rule_weight)
            weighted_reward += unit_weight * (safe_float(state.get("avgReward")) or 0.0)
            weighted_reward_count += unit_weight * float(
                safe_float(state.get("rewardCount")) or 0.0
            )
            weighted_preferred_cps += unit_weight * (
                safe_float(state.get("preferredChangepointScale")) or 0.03
            )
            used_weight += unit_weight

    if used_weight <= 0:
        return {
            "avgReward": 0.0,
            "rewardCount": 0.0,
            "preferredCps": 0.03,
            "alignmentScore": 0.0,
            "coverage": 0.0,
        }

    avg_reward = weighted_reward / used_weight
    reward_count = weighted_reward_count / used_weight
    preferred_cps = weighted_preferred_cps / used_weight
    reward_count_norm = min(1.0, reward_count / 24.0)
    cps_gap = abs(preferred_cps - 0.03)
    alignment_score = max(
        0.0,
        min(
            1.0,
            0.48
            + avg_reward * 0.34
            + reward_count_norm * 0.18
            - min(0.28, cps_gap * 3.8),
        ),
    )
    return {
        "avgReward": avg_reward,
        "rewardCount": reward_count,
        "preferredCps": preferred_cps,
        "alignmentScore": alignment_score,
        "coverage": used_weight,
    }


def extract_space(item: Dict[str, Any], key: str) -> Dict[str, Any]:
    return (item.get(key) or {}) if isinstance(item.get(key), dict) else {}


def safe_space_coordinate(space: Dict[str, Any], axis: str) -> Optional[float]:
    return safe_float(space.get(axis))


def coordinate_distribution(x: Optional[float], y: Optional[float]) -> Optional[np.ndarray]:
    if x is None or y is None:
        return None
    vector = np.asarray([float(x), float(y)], dtype=float)
    if not np.all(np.isfinite(vector)):
        return None
    shifted = vector - np.max(vector)
    probs = np.exp(np.clip(shifted, -30.0, 30.0))
    probs = np.maximum(probs, 1e-9)
    probs_sum = probs.sum()
    if probs_sum <= 0:
        return None
    return probs / probs_sum


def kl_divergence(p: Optional[np.ndarray], q: Optional[np.ndarray]) -> Optional[float]:
    if p is None or q is None:
        return None
    if p.shape != q.shape:
        return None
    p_safe = np.clip(p, 1e-9, None)
    q_safe = np.clip(q, 1e-9, None)
    return safe_float(float(np.sum(p_safe * np.log(p_safe / q_safe))))


def build_geometry_target(points: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not points:
        return None

    ranked = sorted(
        points,
        key=lambda item: (
            (safe_float(item.get("optimizationScore")) or -999.0)
            + (safe_float(item.get("maxUpsidePct")) or 0.0) * 4.0
            - (safe_float(item.get("uncertaintyRatio")) or 0.0) * 2.5
            + (safe_float((item.get("trajectory") or {}).get("persistenceScore")) or 0.0)
        ),
        reverse=True,
    )[: max(6, min(GEOMETRY_TARGET_POOL, len(points)))]

    x_values = []
    y_values = []
    for item in ranked:
        second_space = extract_space(item, "secondCoordinateSpace") or extract_space(
            item, "convictionSpace"
        )
        x_value = safe_space_coordinate(second_space, "x")
        y_value = safe_space_coordinate(second_space, "y")
        if x_value is None or y_value is None:
            continue
        x_values.append(x_value)
        y_values.append(y_value)

    if not x_values or not y_values:
        return None

    target_x = safe_float(float(np.nanpercentile(np.asarray(x_values), 62)))
    target_y = safe_float(float(np.nanpercentile(np.asarray(y_values), 68)))
    target_distribution = coordinate_distribution(target_x, target_y)
    if target_x is None or target_y is None or target_distribution is None:
        return None

    return {
        "space": "uncertainty-adjusted geometry",
        "label": "Geometry target",
        "riskProfile": "balanced defensive growth",
        "method": "KL-minimizing e-projection toward an uncertainty-adjusted target point",
        "x": target_x,
        "y": target_y,
        "distribution": target_distribution.tolist(),
    }


def geometry_metrics(
    item: Dict[str, Any],
    geometry_target: Optional[Dict[str, Any]],
) -> Dict[str, Optional[float]]:
    if not geometry_target:
        return {
            "distance": None,
            "kl_divergence": None,
            "alignment_score": None,
            "x": None,
            "y": None,
        }

    second_space = extract_space(item, "secondCoordinateSpace") or extract_space(
        item, "convictionSpace"
    )
    x_value = safe_space_coordinate(second_space, "x")
    y_value = safe_space_coordinate(second_space, "y")
    if x_value is None or y_value is None:
        return {
            "distance": None,
            "kl_divergence": None,
            "alignment_score": None,
            "x": None,
            "y": None,
        }

    distance = safe_float(
        float(
            np.hypot(
                x_value - float(geometry_target["x"]),
                y_value - float(geometry_target["y"]),
            )
        )
    )
    kl_value = kl_divergence(
        coordinate_distribution(x_value, y_value),
        np.asarray(geometry_target["distribution"], dtype=float),
    )
    alignment_score = (
        safe_float(1.0 / (1.0 + (distance or 0.0) + (kl_value or 0.0)))
        if distance is not None or kl_value is not None
        else None
    )

    return {
        "distance": distance,
        "kl_divergence": kl_value,
        "alignment_score": alignment_score,
        "x": x_value,
        "y": y_value,
    }


def candidate_score(
    item: Dict[str, Any],
    volatility: Optional[float],
    geometry_target: Optional[Dict[str, Any]] = None,
    profile: Optional[PortfolioProfile] = None,
) -> float:
    optimization_score = safe_float(item.get("optimizationScore")) or 0.0
    upside = max(0.0, safe_float(item.get("maxUpsidePct")) or 0.0)
    expected_return = max(0.0, safe_float(item.get("expectedReturnPct")) or 0.0)
    direction_score = max(0.0, safe_float(item.get("directionScore")) or 0.0)
    first_moment = max(0.0, safe_float(item.get("firstMomentPctPerDay")) or 0.0)
    uncertainty = max(0.0, safe_float(item.get("uncertaintyRatio")) or 0.0)
    trajectory = item.get("trajectory") or {}
    persistence = max(0.0, safe_float(trajectory.get("persistenceScore")) or 0.0)
    stability = max(0.0, safe_float(trajectory.get("stabilityScore")) or 0.0)
    regime_risk = max(0.0, safe_float(trajectory.get("regimeShiftRisk")) or 0.0)
    continuation = safe_float(trajectory.get("continuationBias")) or 0.0
    drawdown_linger_days = max(
        0.0,
        seconds_to_days(safe_float(item.get("drawdownLingerSeconds"))) or 0.0,
    )
    spike_sustain_days = max(
        0.0,
        seconds_to_days(safe_float(item.get("spikeSustainSeconds"))) or 0.0,
    )
    peak_to_fade_days = max(
        0.0,
        seconds_to_days(safe_float(item.get("peakToFadeSeconds"))) or 0.0,
    )
    trough_to_recovery_days = max(
        0.0,
        seconds_to_days(safe_float(item.get("troughToRecoverySeconds"))) or 0.0,
    )
    max_drawdown_pct = abs(min(0.0, safe_float(item.get("maxDrawdownPct")) or 0.0))
    max_spike_pct = max(0.0, safe_float(item.get("maxSpikePct")) or 0.0)
    dark_horse_score = max(0.0, safe_float(item.get("darkHorseScore")) or 0.0)
    belief_network = item.get("beliefNetwork") or {}
    belief_score = max(0.0, min(100.0, safe_float(item.get("beliefScore")) or 0.0)) / 100.0
    belief_agreement = max(0.0, min(1.0, safe_float(belief_network.get("agreementRatio")) or 0.0))
    belief_polarization = max(0.0, safe_float(belief_network.get("polarizationScore")) or 0.0)
    human_bias_score = max(0.0, min(100.0, safe_float(item.get("humanBiasScore")) or 0.0)) / 100.0
    recovery_in_horizon = bool(item.get("drawdownRecoveryInHorizon"))
    spike_fade_in_horizon = item.get("spikeFadeInHorizon")
    turnover = classify_turnover_potential(item)
    vol = max(0.05, volatility or 0.25)
    geometry = geometry_metrics(item, geometry_target)
    geometry_alignment = max(0.0, safe_float(geometry.get("alignment_score")) or 0.0)
    geometry_distance = max(0.0, safe_float(geometry.get("distance")) or 0.0)
    geometry_kl = max(0.0, safe_float(geometry.get("kl_divergence")) or 0.0)
    web_neural_score = safe_float(item.get("webNeuralScore")) or 0.0
    web_neural_confidence = max(
        0.0,
        min(1.0, safe_float(item.get("webNeuralConfidence")) or 0.0),
    )
    champion_prophet = item.get("_championProphet") or item.get("championProphet") or {}
    champion_avg_reward = safe_float(champion_prophet.get("avgReward")) or 0.0
    champion_reward_count = max(
        0.0,
        safe_float(champion_prophet.get("rewardCount")) or 0.0,
    )
    champion_alignment = max(
        0.0,
        safe_float(champion_prophet.get("alignmentScore")) or 0.0,
    )
    tail_metrics = compute_small_cap_heavy_tail_metrics(
        item,
        volatility,
        safe_float(item.get("marketCap")),
    )
    small_cap_tail_score = max(0.0, safe_float(tail_metrics.get("smallCapTailScore")) or 0.0)
    heavy_tail_score = max(0.0, safe_float(tail_metrics.get("heavyTailScore")) or 0.0)
    heavy_tail_premium = max(0.0, safe_float(tail_metrics.get("heavyTailPremium")) or 0.0)
    reddit_smallcap = item.get("_redditSmallCap") or {}
    reddit_heat = max(0.0, min(1.0, safe_float(reddit_smallcap.get("heatScore")) or 0.0))
    fmkorea_stock = item.get("_fmkoreaStock") or {}
    fmkorea_heat = max(0.0, min(1.0, safe_float(fmkorea_stock.get("heatScore")) or 0.0))
    fmkorea_direct_surge = max(0.0, min(1.0, safe_float(item.get("fmkoreaSurgeScore")) or 0.0))
    profile = profile or PORTFOLIO_PROFILES[0]

    raw_score = (
        (optimization_score + 2.0) * 0.55
        + upside * 5.5 * profile.upside_multiplier
        + expected_return * 3.5
        + direction_score * 0.7
        + first_moment * 1.8
        + persistence * 0.85 * profile.persistence_multiplier
        + stability * 0.55
        + continuation * 0.25
        + turnover_bonus(turnover) * profile.turnover_multiplier
        + spike_sustain_days * 0.05 * profile.upside_multiplier
        + peak_to_fade_days * 0.02
        + max_spike_pct * 2.0 * profile.upside_multiplier
        + (dark_horse_score / 100.0) * 0.28 * profile.upside_multiplier
        + belief_score * 0.52
        + belief_agreement * 0.28
        + human_bias_score * 0.12
        + web_neural_score * 18.0 * max(0.25, web_neural_confidence)
        + web_neural_confidence * 0.16
        - uncertainty * 2.5 * profile.uncertainty_penalty
        - regime_risk * 1.2 * profile.regime_penalty
        - belief_polarization * 0.32
        - drawdown_linger_days * 0.06 * profile.drawdown_penalty
        - trough_to_recovery_days * 0.03
        - max_drawdown_pct * 2.4 * profile.drawdown_penalty
        + geometry_alignment * 1.35 * profile.geometry_multiplier
        - geometry_distance * 0.10 * profile.geometry_multiplier
        - geometry_kl * 0.95 * profile.geometry_multiplier
        + champion_avg_reward * 0.65
        + champion_alignment * 0.55
        + min(0.14, champion_reward_count * 0.004)
        + small_cap_tail_score * 0.28 * profile.upside_multiplier
        + heavy_tail_score * 0.38 * profile.upside_multiplier
        + heavy_tail_premium * (2.1 + reddit_heat * 1.2) * profile.upside_multiplier
        + fmkorea_heat * 0.10 * profile.upside_multiplier
        + fmkorea_direct_surge * 0.36 * profile.upside_multiplier
        + (0.12 if recovery_in_horizon else -0.18)
        + (0.10 if spike_fade_in_horizon is False else (0.04 if spike_fade_in_horizon else 0.0))
    )
    return max(0.01, raw_score / max(0.05, vol * profile.volatility_penalty))


def normalize_weights_with_bounds(
    raw_scores: List[float],
    *,
    min_weight: float = MIN_WEIGHT,
    max_weight: float = MAX_WEIGHT,
) -> List[float]:
    count = len(raw_scores)
    if count == 0:
        return []

    safe_scores = [max(0.01, float(score)) for score in raw_scores]
    total_min = min_weight * count
    if total_min > 1.0:
        return [1.0 / count] * count

    remaining = set(range(count))
    weights = [0.0] * count
    remaining_weight = 1.0

    while remaining:
        score_sum = sum(safe_scores[index] for index in remaining)
        if score_sum <= 0:
            equal_weight = remaining_weight / len(remaining)
            for index in remaining:
                weights[index] = equal_weight
            break

        changed = False
        for index in list(remaining):
            proposed = remaining_weight * (safe_scores[index] / score_sum)
            if proposed < min_weight:
                weights[index] = min_weight
                remaining_weight -= min_weight
                remaining.remove(index)
                changed = True
            elif proposed > max_weight:
                weights[index] = max_weight
                remaining_weight -= max_weight
                remaining.remove(index)
                changed = True

        if not changed:
            for index in remaining:
                weights[index] = remaining_weight * (safe_scores[index] / score_sum)
            break

    total_weight = sum(weights)
    if total_weight <= 0:
        return [1.0 / count] * count
    return [weight / total_weight for weight in weights]


def build_rationale(
    item: Dict[str, Any],
    weight: float,
    volatility: Optional[float],
    geometry_target: Optional[Dict[str, Any]] = None,
    profile: Optional[PortfolioProfile] = None,
) -> str:
    profile = profile or PORTFOLIO_PROFILES[0]
    upside = safe_float(item.get("maxUpsidePct"))
    uncertainty = safe_float(item.get("uncertaintyRatio"))
    turnover = classify_turnover_potential(item)
    volatility_pct = volatility * 100 if volatility is not None else None
    trajectory = item.get("trajectory") or {}
    persistence = safe_float(trajectory.get("persistenceScore"))
    regime_risk = safe_float(trajectory.get("regimeShiftRisk"))
    regime_label = trajectory.get("regimeLabel")
    drawdown_linger_days = seconds_to_days(safe_float(item.get("drawdownLingerSeconds")))
    spike_sustain_days = seconds_to_days(safe_float(item.get("spikeSustainSeconds")))
    max_drawdown_pct = safe_float(item.get("maxDrawdownPct"))
    max_spike_pct = safe_float(item.get("maxSpikePct"))
    dark_horse_score = safe_float(item.get("darkHorseScore"))
    belief_score = safe_float(item.get("beliefScore"))
    belief_label_text = str(item.get("beliefLabel") or belief_label(belief_score))
    belief_rationale = str(item.get("beliefRationale") or "")
    belief_network = item.get("beliefNetwork") or {}
    belief_agreement = safe_float(belief_network.get("agreementRatio"))
    belief_private_signal = safe_float(belief_network.get("privateSignalPct"))
    belief_crowd_signal = safe_float(belief_network.get("crowdBeliefPct"))
    human_bias = item.get("humanBias") or {}
    human_bias_score = safe_float(item.get("humanBiasScore"))
    human_bias_label_text = str(item.get("humanBiasLabel") or human_bias.get("label") or "attention diffuse")
    human_bias_short_count = safe_float(human_bias.get("shortCount"))
    symmetry = item.get("symmetry") or {}
    symmetry_counterpart = symmetry.get("counterpartSymbol")
    geometry = geometry_metrics(item, geometry_target)
    geometry_alignment = safe_float(geometry.get("alignment_score"))
    geometry_distance = safe_float(geometry.get("distance"))
    web_neural_score = safe_float(item.get("webNeuralScore"))
    web_neural_confidence = safe_float(item.get("webNeuralConfidence"))
    web_neural_label = str(item.get("webNeuralLabel") or "neutral")
    champion_prophet = item.get("_championProphet") or item.get("championProphet") or {}
    champion_avg_reward = safe_float(champion_prophet.get("avgReward"))
    champion_reward_count = safe_float(champion_prophet.get("rewardCount"))
    champion_alignment = safe_float(champion_prophet.get("alignmentScore"))
    natural_gradient_target_weight = safe_float(item.get("naturalGradientTargetWeightPct"))
    natural_gradient_bound_weight = safe_float(item.get("naturalGradientBoundWeightPct"))
    tail_metrics = compute_small_cap_heavy_tail_metrics(
        item,
        volatility,
        safe_float(item.get("marketCap")),
    )
    market_cap = safe_float(tail_metrics.get("marketCap"))
    market_cap_bucket = str(tail_metrics.get("marketCapBucket") or "unknown")
    small_cap_tail_score = safe_float(tail_metrics.get("smallCapTailScore"))
    heavy_tail_score = safe_float(tail_metrics.get("heavyTailScore"))
    heavy_tail_premium = safe_float(tail_metrics.get("heavyTailPremium"))
    heavy_tail_label = str(tail_metrics.get("heavyTailLabel") or "tail-muted")
    reddit_smallcap = item.get("_redditSmallCap") or {}
    reddit_heat = safe_float(reddit_smallcap.get("heatScore"))
    reddit_regime = str(reddit_smallcap.get("regime") or "small-cap muted")
    fmkorea_stock = item.get("_fmkoreaStock") or {}
    fmkorea_heat = safe_float(fmkorea_stock.get("heatScore"))
    fmkorea_regime = str(fmkorea_stock.get("regime") or "Korean retail muted")
    fmkorea_direct_surge = safe_float(item.get("fmkoreaSurgeScore"))
    fmkorea_mentions = safe_float(item.get("fmkoreaMentionCount"))
    linger_text = (
        f"drop linger {drawdown_linger_days:.1f}d"
        if drawdown_linger_days is not None
        else "drop linger unknown"
    )
    spike_text = (
        f"spike sustain {spike_sustain_days:.1f}d"
        if spike_sustain_days is not None
        else "spike sustain unknown"
    )
    drawdown_text = (
        f"max drawdown {abs(max_drawdown_pct) * 100:.1f}%"
        if max_drawdown_pct is not None
        else "max drawdown unknown"
    )
    max_spike_text = (
        f"max spike {max_spike_pct * 100:.1f}%"
        if max_spike_pct is not None
        else "max spike unknown"
    )

    upside_text = f"upside {upside * 100:.1f}%" if upside is not None else "upside unknown"
    uncertainty_text = (
        f"uncertainty {uncertainty * 100:.1f}%"
        if uncertainty is not None
        else "uncertainty unknown"
    )
    volatility_text = (
        f"volatility {volatility_pct:.1f}%"
        if volatility_pct is not None
        else "volatility unknown"
    )
    persistence_text = (
        f"persistence {persistence * 100:.0f}%"
        if persistence is not None
        else "persistence unknown"
    )
    regime_text = (
        f"regime risk {regime_risk * 100:.0f}%"
        if regime_risk is not None
        else "regime risk unknown"
    )
    geometry_text = (
        f"geometry alignment {geometry_alignment * 100:.0f}% at distance {geometry_distance:.2f}"
        if geometry_alignment is not None and geometry_distance is not None
        else "geometry alignment unknown"
    )
    dark_horse_text = (
        f"dark-horse symmetry {dark_horse_score:.0f} vs mirror {symmetry_counterpart or 'unknown'}"
        if dark_horse_score is not None and dark_horse_score >= 58.0
        else "dark-horse symmetry muted"
    )
    belief_text = (
        f"belief {belief_score:.0f} ({belief_label_text})"
        if belief_score is not None
        else "belief still forming"
    )
    belief_network_text = (
        f"private {belief_private_signal:.0f}, crowd {belief_crowd_signal:.0f}, agreement {belief_agreement * 100:.0f}%"
        if belief_private_signal is not None
        and belief_crowd_signal is not None
        and belief_agreement is not None
        else "belief network still calibrating"
    )
    human_bias_text = (
        f"user attention {human_bias_score:.0f} ({human_bias_label_text}, {human_bias_short_count:.0f} recent taps)"
        if human_bias_score is not None and human_bias_short_count is not None
        else f"user attention {human_bias_score:.0f} ({human_bias_label_text})"
        if human_bias_score is not None
        else "aggregate user attention still diffuse"
    )
    web_neural_text = (
        f"website neural score {web_neural_score * 100:.2f}% with confidence {web_neural_confidence * 100:.0f}% ({web_neural_label})"
        if web_neural_score is not None and web_neural_confidence is not None
        else "website neural model still warming up"
    )
    champion_text = (
        f"champion Prophet avg reward {champion_avg_reward:.3f}, alignment {champion_alignment * 100:.0f}%, reward count {champion_reward_count:.1f}"
        if champion_avg_reward is not None
        and champion_alignment is not None
        and champion_reward_count is not None
        else "champion Prophet reinforcement still shallow"
    )
    natural_gradient_text = (
        f"natural-gradient target {natural_gradient_target_weight:.1f}% with bound {natural_gradient_bound_weight:.1f}%"
        if natural_gradient_target_weight is not None and natural_gradient_bound_weight is not None
        else "natural-gradient envelope warming up"
    )
    market_cap_text = (
        f"market cap {market_cap / 1_000_000_000:.1f}B ({market_cap_bucket})"
        if market_cap is not None
        else f"market cap bucket {market_cap_bucket}"
    )
    tail_text = (
        f"small-cap tail {small_cap_tail_score * 100:.0f}%, heavy-tail {heavy_tail_score * 100:.0f}%, premium {heavy_tail_premium * 100:.1f}% ({heavy_tail_label})"
        if small_cap_tail_score is not None
        and heavy_tail_score is not None
        and heavy_tail_premium is not None
        else "small-cap tail effect muted"
    )
    reddit_text = (
        f"Reddit Daytrading small-cap heat {reddit_heat * 100:.0f}% ({reddit_regime})"
        if reddit_heat is not None
        else "Reddit small-cap tape muted"
    )
    fmkorea_text = (
        f"Korean retail surge pulse {fmkorea_heat * 100:.0f}% ({fmkorea_regime}), direct score {fmkorea_direct_surge * 100:.0f}% with {fmkorea_mentions:.0f} mentions"
        if fmkorea_heat is not None
        and fmkorea_direct_surge is not None
        and fmkorea_mentions is not None
        else "Korean retail surge pulse muted"
    )

    return (
        f"{item.get('symbol')} is sized at {weight * 100:.1f}% inside the {profile.label} because {upside_text}, "
        f"{uncertainty_text}, {persistence_text}, {belief_text}, {belief_network_text}, {human_bias_text}, {geometry_text}, {dark_horse_text}, {web_neural_text}, {champion_text}, {natural_gradient_text}, {market_cap_text}, {tail_text}, {reddit_text}, {fmkorea_text}, and {turnover} turnover potential balance well with "
        f"{volatility_text}, {linger_text}, {spike_text}, and {max_spike_text} while {drawdown_text} and {regime_text} keep the "
        f"{regime_label or 'current regime'} in check."
        f"{(' ' + belief_rationale + '.') if belief_rationale else ''}"
    )


def select_candidates(
    points: List[Dict[str, Any]],
    close_matrix: pd.DataFrame,
    holdings: int,
    candidate_limit: int,
    trailing_days: int,
    geometry_target: Optional[Dict[str, Any]],
    profile: Optional[PortfolioProfile] = None,
    reddit_smallcap: Optional[Dict[str, Any]] = None,
    fmkorea_stock: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    profile = profile or PORTFOLIO_PROFILES[0]
    ranked = sorted(
        points,
        key=lambda item: safe_float(item.get("optimizationScore")) or -999.0,
        reverse=True,
    )[: max(holdings * 2, candidate_limit)]

    sector_counts: Dict[str, int] = {}
    selected: List[Dict[str, Any]] = []

    for item in ranked:
        linger = safe_float(item.get("drawdownLingerSeconds"))
        if linger is not None and linger > 15.0 * 86400.0:
            continue
            
        sector = str(item.get("sector") or "Other")
        if sector_counts.get(sector, 0) >= MAX_PER_SECTOR:
            continue
        volatility = annualized_volatility(close_matrix, item["symbol"], trailing_days)
        live_snapshot = trader_main.fetch_live_equity_snapshot(item["symbol"]) or {}
        market_cap = safe_float(live_snapshot.get("marketCap"))
        scored_item = {
            **item,
            "marketCap": market_cap,
            "_redditSmallCap": reddit_smallcap or {},
            "_fmkoreaStock": fmkorea_stock or {},
        }
        selected.append(
            {
                **scored_item,
                "turnoverPotential": classify_turnover_potential(item),
                "annualizedVolatilityPct": volatility * 100 if volatility is not None else None,
                "marketCap": market_cap,
                "trajectory": item.get("trajectory") or {},
                "beliefScore": safe_float(item.get("beliefScore")),
                "beliefLabel": item.get("beliefLabel"),
                "beliefRationale": item.get("beliefRationale"),
                "beliefNetwork": item.get("beliefNetwork") or {},
                "humanBiasScore": safe_float(item.get("humanBiasScore")),
                "humanBiasLabel": item.get("humanBiasLabel"),
                "humanBiasRationale": item.get("humanBiasRationale"),
                "humanBias": item.get("humanBias") or {},
                "_championProphet": aggregate_champion_prophet_state(item["symbol"]),
                "_geometry": geometry_metrics(scored_item, geometry_target),
                "_portfolioScore": candidate_score(scored_item, volatility, geometry_target, profile),
                "_volatility": volatility,
                "_liveSnapshot": live_snapshot,
                "_redditSmallCap": reddit_smallcap or {},
                "_fmkoreaStock": fmkorea_stock or {},
            }
        )
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if len(selected) >= holdings:
            break

    return selected


def summarize_portfolio(holdings: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not holdings:
        return {
            "holdingsCount": 0,
            "weightedUpsidePct": None,
            "weightedUncertaintyPct": None,
            "weightedVolatilityPct": None,
            "turnoverMix": {},
            "sectorCount": 0,
        }

    weighted_upside = 0.0
    weighted_uncertainty = 0.0
    weighted_volatility = 0.0
    weighted_drawdown_linger_days = 0.0
    weighted_max_drawdown_pct = 0.0
    weighted_spike_sustain_days = 0.0
    weighted_max_spike_pct = 0.0
    weighted_dark_horse_score = 0.0
    weighted_belief_score = 0.0
    weighted_belief_agreement = 0.0
    weighted_belief_polarization = 0.0
    weighted_human_bias_score = 0.0
    weighted_persistence_pct = 0.0
    weighted_regime_risk_pct = 0.0
    weighted_web_neural_score = 0.0
    weighted_web_neural_confidence = 0.0
    weighted_champion_prophet_avg_reward = 0.0
    weighted_champion_prophet_alignment_score = 0.0
    weighted_champion_prophet_reward_count = 0.0
    weighted_champion_prophet_preferred_cps = 0.0
    weighted_small_cap_tail_score = 0.0
    weighted_heavy_tail_score = 0.0
    weighted_heavy_tail_premium = 0.0
    weighted_long_tail_score = 0.0
    weighted_left_tail_risk_score = 0.0
    reddit_smallcap_heat_score = None
    reddit_smallcap_regime = None
    fmkorea_stock_heat_score = None
    fmkorea_stock_regime = None
    weighted_fmkorea_surge_score = 0.0
    turnover_mix: Dict[str, int] = {}
    sectors = set()

    for holding in holdings:
        weight = float(holding["weight"])
        sectors.add(str(holding.get("sector") or "Other"))
        turnover = str(holding.get("turnoverPotential") or "unknown")
        turnover_mix[turnover] = turnover_mix.get(turnover, 0) + 1

        upside = safe_float(holding.get("maxUpsidePct")) or 0.0
        uncertainty = safe_float(holding.get("uncertaintyRatio")) or 0.0
        volatility_pct = safe_float(holding.get("annualizedVolatilityPct")) or 0.0
        drawdown_linger_days = (
            seconds_to_days(safe_float(holding.get("drawdownLingerSeconds"))) or 0.0
        )
        max_drawdown_pct = abs(min(0.0, safe_float(holding.get("maxDrawdownPct")) or 0.0))
        spike_sustain_days = (
            seconds_to_days(safe_float(holding.get("spikeSustainSeconds"))) or 0.0
        )
        max_spike_pct = max(0.0, safe_float(holding.get("maxSpikePct")) or 0.0)
        dark_horse_score = max(0.0, safe_float(holding.get("darkHorseScore")) or 0.0)
        belief_score = max(0.0, safe_float(holding.get("beliefScore")) or 0.0)
        belief_network = holding.get("beliefNetwork") or {}
        belief_agreement = max(
            0.0,
            min(1.0, safe_float(belief_network.get("agreementRatio")) or 0.0),
        )
        belief_polarization = max(0.0, safe_float(belief_network.get("polarizationScore")) or 0.0)
        human_bias_score = max(0.0, safe_float(holding.get("humanBiasScore")) or 0.0)
        web_neural_score = safe_float(holding.get("webNeuralScore")) or 0.0
        web_neural_confidence = max(
            0.0,
            min(1.0, safe_float(holding.get("webNeuralConfidence")) or 0.0),
        )
        persistence_score = max(
            0.0,
            safe_float((holding.get("trajectory") or {}).get("persistenceScore")) or 0.0,
        )
        regime_risk_score = max(
            0.0,
            safe_float((holding.get("trajectory") or {}).get("regimeShiftRisk")) or 0.0,
        )
        champion_avg_reward = safe_float(holding.get("championProphetAvgReward")) or 0.0
        champion_alignment_score = safe_float(holding.get("championProphetAlignmentScore")) or 0.0
        champion_reward_count = safe_float(holding.get("championProphetRewardCount")) or 0.0
        champion_preferred_cps = safe_float(holding.get("championProphetPreferredCps")) or 0.03
        small_cap_tail_score = max(0.0, safe_float(holding.get("smallCapTailScore")) or 0.0)
        heavy_tail_score = max(0.0, safe_float(holding.get("heavyTailScore")) or 0.0)
        heavy_tail_premium = max(0.0, safe_float(holding.get("heavyTailPremium")) or 0.0)
        long_tail_score = max(0.0, safe_float(holding.get("longTailScore")) or 0.0)
        left_tail_risk_score = max(0.0, safe_float(holding.get("leftTailRiskScore")) or 0.0)
        fmkorea_surge_score = max(0.0, safe_float(holding.get("fmkoreaSurgeScore")) or 0.0)

        weighted_upside += weight * upside
        weighted_uncertainty += weight * uncertainty
        weighted_volatility += weight * volatility_pct
        weighted_drawdown_linger_days += weight * drawdown_linger_days
        weighted_max_drawdown_pct += weight * max_drawdown_pct
        weighted_spike_sustain_days += weight * spike_sustain_days
        weighted_max_spike_pct += weight * max_spike_pct
        weighted_dark_horse_score += weight * dark_horse_score
        weighted_belief_score += weight * belief_score
        weighted_belief_agreement += weight * belief_agreement
        weighted_belief_polarization += weight * belief_polarization
        weighted_human_bias_score += weight * human_bias_score
        weighted_persistence_pct += weight * persistence_score
        weighted_regime_risk_pct += weight * regime_risk_score
        weighted_web_neural_score += weight * web_neural_score
        weighted_web_neural_confidence += weight * web_neural_confidence
        weighted_champion_prophet_avg_reward += weight * champion_avg_reward
        weighted_champion_prophet_alignment_score += weight * champion_alignment_score
        weighted_champion_prophet_reward_count += weight * champion_reward_count
        weighted_champion_prophet_preferred_cps += weight * champion_preferred_cps
        weighted_small_cap_tail_score += weight * small_cap_tail_score
        weighted_heavy_tail_score += weight * heavy_tail_score
        weighted_heavy_tail_premium += weight * heavy_tail_premium
        weighted_long_tail_score += weight * long_tail_score
        weighted_left_tail_risk_score += weight * left_tail_risk_score
        weighted_fmkorea_surge_score += weight * fmkorea_surge_score
        reddit_snapshot = holding.get("redditSmallCap") or {}
        if reddit_smallcap_heat_score is None:
            reddit_smallcap_heat_score = safe_float(reddit_snapshot.get("heatScore"))
        if reddit_smallcap_regime is None:
            reddit_smallcap_regime = reddit_snapshot.get("regime")
        fmkorea_snapshot = holding.get("fmkoreaStock") or {}
        if fmkorea_stock_heat_score is None:
            fmkorea_stock_heat_score = safe_float(fmkorea_snapshot.get("heatScore"))
        if fmkorea_stock_regime is None:
            fmkorea_stock_regime = fmkorea_snapshot.get("regime")

    return {
        "holdingsCount": len(holdings),
        "weightedUpsidePct": weighted_upside,
        "weightedUncertaintyPct": weighted_uncertainty,
        "weightedVolatilityPct": weighted_volatility,
        "weightedDrawdownLingerDays": weighted_drawdown_linger_days,
        "weightedMaxDrawdownPct": weighted_max_drawdown_pct,
        "weightedSpikeSustainDays": weighted_spike_sustain_days,
        "weightedMaxSpikePct": weighted_max_spike_pct,
        "weightedDarkHorseScore": weighted_dark_horse_score,
        "weightedBeliefScore": weighted_belief_score,
        "weightedBeliefAgreement": weighted_belief_agreement,
        "weightedBeliefPolarization": weighted_belief_polarization,
        "weightedHumanBiasScore": weighted_human_bias_score,
        "weightedPersistencePct": weighted_persistence_pct,
        "weightedRegimeRiskPct": weighted_regime_risk_pct,
        "weightedWebNeuralScore": weighted_web_neural_score,
        "weightedWebNeuralConfidence": weighted_web_neural_confidence,
        "weightedChampionProphetAvgReward": weighted_champion_prophet_avg_reward,
        "weightedChampionProphetAlignmentScore": weighted_champion_prophet_alignment_score,
        "weightedChampionProphetRewardCount": weighted_champion_prophet_reward_count,
        "weightedChampionProphetPreferredCps": weighted_champion_prophet_preferred_cps,
        "weightedSmallCapTailScore": weighted_small_cap_tail_score,
        "weightedHeavyTailScore": weighted_heavy_tail_score,
        "weightedHeavyTailPremium": weighted_heavy_tail_premium,
        "weightedLongTailScore": weighted_long_tail_score,
        "weightedLeftTailRiskScore": weighted_left_tail_risk_score,
        "redditSmallCapHeatScore": reddit_smallcap_heat_score,
        "redditSmallCapRegime": reddit_smallcap_regime,
        "fmkoreaStockHeatScore": fmkorea_stock_heat_score,
        "fmkoreaStockRegime": fmkorea_stock_regime,
        "weightedFmkoreaSurgeScore": weighted_fmkorea_surge_score,
        "turnoverMix": turnover_mix,
        "sectorCount": len(sectors),
    }


def build_geometry_summary(
    holdings: List[Dict[str, Any]],
    geometry_target: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not holdings or not geometry_target:
        return None

    portfolio_x = 0.0
    portfolio_y = 0.0
    any_points = False
    frontier_candidates = []
    for holding in holdings:
        weight = float(holding.get("weight") or 0.0)
        geometry = holding.get("_geometry") or {}
        x_value = safe_float(geometry.get("x"))
        y_value = safe_float(geometry.get("y"))
        if x_value is None or y_value is None:
            continue
        portfolio_x += weight * x_value
        portfolio_y += weight * y_value
        any_points = True
        frontier_candidates.append(
            {
                "symbol": holding.get("symbol"),
                "x": x_value,
                "y": y_value,
                "weightPct": safe_float(holding.get("weightPct")),
            }
        )

    if not any_points:
        return None

    portfolio_distribution = coordinate_distribution(portfolio_x, portfolio_y)
    target_distribution = np.asarray(geometry_target["distribution"], dtype=float)
    portfolio_kl = kl_divergence(portfolio_distribution, target_distribution)
    portfolio_distance = safe_float(
        float(
            np.hypot(
                portfolio_x - float(geometry_target["x"]),
                portfolio_y - float(geometry_target["y"]),
            )
        )
    )
    alignment_score = (
        safe_float(1.0 / (1.0 + (portfolio_distance or 0.0) + (portfolio_kl or 0.0)))
        if portfolio_distance is not None or portfolio_kl is not None
        else None
    )

    frontier_points = sorted(frontier_candidates, key=lambda item: (item["x"], item["y"]))

    return {
        "space": geometry_target["space"],
        "method": geometry_target["method"],
        "riskProfile": geometry_target["riskProfile"],
        "targetPoint": {
            "label": geometry_target["label"],
            "x": safe_float(geometry_target["x"]),
            "y": safe_float(geometry_target["y"]),
        },
        "portfolioPoint": {
            "label": "Optimized portfolio",
            "x": safe_float(portfolio_x),
            "y": safe_float(portfolio_y),
        },
        "projectionLine": [
            {
                "label": "Optimized portfolio",
                "x": safe_float(portfolio_x),
                "y": safe_float(portfolio_y),
            },
            {
                "label": geometry_target["label"],
                "x": safe_float(geometry_target["x"]),
                "y": safe_float(geometry_target["y"]),
            },
        ],
        "frontierLine": frontier_points,
        "portfolioKlDivergence": portfolio_kl,
        "portfolioDistance": portfolio_distance,
        "alignmentScore": alignment_score,
    }


def build_portfolio_candidate_payload(
    *,
    profile: PortfolioProfile,
    candidate_points: List[Dict[str, Any]],
    close_matrix: pd.DataFrame,
    symbol_metadata: Optional[Dict[str, Dict[str, str]]],
    geometry_target: Optional[Dict[str, Any]],
    generated_at: str,
    map_date: Optional[str],
    cache_payload: Dict[str, Any],
    trailing_days: int,
    methodology_objective: str,
    reddit_smallcap: Optional[Dict[str, Any]] = None,
    fmkorea_stock: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    selected = select_candidates(
        candidate_points,
        close_matrix,
        holdings=max(1, profile.holdings),
        candidate_limit=max(profile.holdings, profile.candidate_limit),
        trailing_days=max(20, trailing_days),
        geometry_target=geometry_target,
        profile=profile,
        reddit_smallcap=reddit_smallcap,
        fmkorea_stock=fmkorea_stock,
    )
    selected.sort(key=lambda item: item["_portfolioScore"], reverse=True)

    initial_weights = normalize_weights_with_bounds(
        [item["_portfolioScore"] for item in selected],
        min_weight=profile.min_weight,
        max_weight=profile.max_weight,
    )
    natural_gradient = build_natural_gradient_bound_report(
        selected,
        initial_weights,
        min_weight=profile.min_weight,
        max_weight=profile.max_weight,
    )
    natural_gradient_lookup = {
        str(entry.get("symbol")): entry for entry in (natural_gradient.get("perHolding") or [])
    }
    weights = [
        max(
            0.0,
            (
                safe_float(
                    (natural_gradient_lookup.get(str(item.get("symbol"))) or {}).get("liveWeightPct")
                )
                or (initial_weight * 100.0)
            )
            / 100.0,
        )
        for item, initial_weight in zip(selected, initial_weights)
    ]
    enriched_holdings: List[Dict[str, Any]] = []
    for item, weight in zip(selected, weights):
        live_snapshot = item.get("_liveSnapshot") or trader_main.fetch_live_equity_snapshot(item["symbol"])
        live_price = safe_float((live_snapshot or {}).get("price"))
        market_cap = safe_float((live_snapshot or {}).get("marketCap")) or safe_float(
            item.get("marketCap")
        )
        natural_gradient_item = natural_gradient_lookup.get(str(item.get("symbol"))) or {}
        tail_metrics = compute_small_cap_heavy_tail_metrics(
            item,
            item.get("_volatility"),
            market_cap,
        )
        enriched_holdings.append(
            {
                **item,
                "weight": weight,
                "weightPct": weight * 100.0,
                "livePrice": live_price if live_price is not None else item.get("lastClosePrice"),
                "marketCap": market_cap,
                "marketCapBucket": tail_metrics.get("marketCapBucket"),
                "smallCapTailScore": safe_float(tail_metrics.get("smallCapTailScore")),
                "heavyTailScore": safe_float(tail_metrics.get("heavyTailScore")),
                "heavyTailPremium": safe_float(tail_metrics.get("heavyTailPremium")),
                "heavyTailLabel": tail_metrics.get("heavyTailLabel"),
                "heavyTailRationale": tail_metrics.get("heavyTailRationale"),
                "tailDiagnostics": item.get("tailDiagnostics") or {},
                "tailRegimeLabel": tail_metrics.get("tailRegimeLabel"),
                "tailSkewness": safe_float(tail_metrics.get("tailSkewness")),
                "tailExcessKurtosis": safe_float(tail_metrics.get("tailExcessKurtosis")),
                "longTailScore": safe_float(tail_metrics.get("tailLongScore")),
                "leftTailRiskScore": safe_float(tail_metrics.get("tailLeftRiskScore")),
                "tailConcentration": safe_float(tail_metrics.get("tailConcentration")),
                "tailExtremeMoveRate": safe_float(tail_metrics.get("tailExtremeMoveRate")),
                "beliefScore": safe_float(item.get("beliefScore")),
                "beliefLabel": item.get("beliefLabel"),
                "beliefRationale": item.get("beliefRationale"),
                "beliefNetwork": item.get("beliefNetwork") or {},
                "redditSmallCap": reddit_smallcap or {},
                "fmkoreaStock": fmkorea_stock or {},
                "fmkoreaSurgeScore": safe_float(item.get("fmkoreaSurgeScore")),
                "fmkoreaMentionCount": safe_float(item.get("fmkoreaMentionCount")),
                "fmkoreaSurgeLabel": item.get("fmkoreaSurgeLabel"),
                "championProphet": item.get("_championProphet") or {},
                "championProphetAvgReward": safe_float(
                    ((item.get("_championProphet") or {}).get("avgReward"))
                ),
                "championProphetAlignmentScore": safe_float(
                    ((item.get("_championProphet") or {}).get("alignmentScore"))
                ),
                "championProphetRewardCount": safe_float(
                    ((item.get("_championProphet") or {}).get("rewardCount"))
                ),
                "championProphetPreferredCps": safe_float(
                    ((item.get("_championProphet") or {}).get("preferredCps"))
                ),
                "naturalGradientTargetWeightPct": safe_float(
                    natural_gradient_item.get("targetWeightPct")
                ),
                "naturalGradientBoundWeightPct": safe_float(
                    natural_gradient_item.get("boundWeightPct")
                ),
                "naturalGradientUtilityScore": safe_float(
                    natural_gradient_item.get("utilityScore")
                ),
                "naturalGradientLiftPct": safe_float(natural_gradient_item.get("liftPct")),
                "geometryDistance": safe_float((item.get("_geometry") or {}).get("distance")),
                "geometryKlDivergence": safe_float((item.get("_geometry") or {}).get("kl_divergence")),
                "geometryAlignmentScore": safe_float((item.get("_geometry") or {}).get("alignment_score")),
                "rationale": build_rationale(
                    {
                        **item,
                        "naturalGradientTargetWeightPct": safe_float(
                            natural_gradient_item.get("targetWeightPct")
                        ),
                        "naturalGradientBoundWeightPct": safe_float(
                            natural_gradient_item.get("boundWeightPct")
                        ),
                    },
                    weight,
                    item.get("_volatility"),
                    geometry_target,
                    profile,
                ),
            }
        )

    portfolio_correlation = build_portfolio_correlation_forecast(
        enriched_holdings,
        close_matrix,
        metadata=symbol_metadata,
    )
    per_holding_correlation = {
        str(entry.get("symbol")): entry
        for entry in (portfolio_correlation.get("perHolding") or [])
        if entry.get("symbol")
    }
    for holding in enriched_holdings:
        correlation_entry = per_holding_correlation.get(str(holding.get("symbol")))
        if not correlation_entry:
            continue
        holding["averagePredictedCorrelation"] = safe_float(
            correlation_entry.get("averagePredictedCorrelation")
        )
        holding["diversificationSupportScore"] = safe_float(
            correlation_entry.get("diversificationSupportScore")
        )
        holding["strongestCorrelationPeer"] = correlation_entry.get(
            "strongestCorrelationPeer"
        )
        holding["strongestCorrelationValue"] = safe_float(
            correlation_entry.get("strongestCorrelationValue")
        )
        holding["strongestDiversifierPeer"] = correlation_entry.get(
            "strongestDiversifierPeer"
        )
        holding["strongestDiversifierValue"] = safe_float(
            correlation_entry.get("strongestDiversifierValue")
        )
        holding["correlationConfidence"] = safe_float(correlation_entry.get("confidence"))

    summary = summarize_portfolio(enriched_holdings)
    if portfolio_correlation.get("status") == "ok":
        summary.update(
            {
                "averagePredictedCorrelation": safe_float(
                    portfolio_correlation.get("averagePredictedCorrelation")
                ),
                "averageAbsoluteCorrelation": safe_float(
                    portfolio_correlation.get("averageAbsoluteCorrelation")
                ),
                "averagePositiveCorrelation": safe_float(
                    portfolio_correlation.get("averagePositiveCorrelation")
                ),
                "diversificationScore": safe_float(
                    portfolio_correlation.get("diversificationScore")
                ),
                "crowdedPairRiskScore": safe_float(
                    portfolio_correlation.get("crowdedPairRiskScore")
                ),
                "concentrationRiskLabel": portfolio_correlation.get(
                    "concentrationRiskLabel"
                ),
            }
        )
    geometry_summary = build_geometry_summary(enriched_holdings, geometry_target)
    cleaned_holdings = []
    for holding in enriched_holdings:
        next_holding = dict(holding)
        next_holding.pop("_portfolioScore", None)
        next_holding.pop("_volatility", None)
        next_holding.pop("_geometry", None)
        cleaned_holdings.append(next_holding)

    return {
        "ok": True,
        "generatedAt": generated_at,
        "mapDate": map_date,
        "cache": cache_payload,
        "summary": summary,
        "methodology": {
            "objective": methodology_objective,
            "candidateLimit": profile.candidate_limit,
            "holdings": profile.holdings,
            "trailingDays": trailing_days,
            "maxPerSector": MAX_PER_SECTOR,
            "weightBounds": {
                "min": profile.min_weight,
                "max": profile.max_weight,
            },
            "heavyTailMethod": (
                "Blend live market-cap bucket when available with map-derived small-cap proxy, "
                "spike sustain, volatility, symmetry, and downside balance to price heavy-tail optionality."
            ),
            "naturalGradientMethod": natural_gradient.get("method"),
        },
        "geometry": geometry_summary,
        "naturalGradient": natural_gradient,
        "correlationForecast": portfolio_correlation,
        "holdings": cleaned_holdings,
        "redditSmallCap": reddit_smallcap or {},
        "fmkoreaStock": fmkorea_stock or {},
        "_profile": profile.name,
        "_profileLabel": profile.label,
        "_profileDescription": profile.description,
    }


def evaluate_portfolio_candidate(
    payload: Dict[str, Any],
    manifold_report: Dict[str, Any],
) -> float:
    summary = payload.get("summary") or {}
    geometry = payload.get("geometry") or {}
    natural_gradient = payload.get("naturalGradient") or {}
    upside = max(0.0, safe_float(summary.get("weightedUpsidePct")) or 0.0)
    uncertainty = max(0.0, safe_float(summary.get("weightedUncertaintyPct")) or 0.0)
    volatility = max(0.0, safe_float(summary.get("weightedVolatilityPct")) or 0.0)
    belief_score = max(0.0, safe_float(summary.get("weightedBeliefScore")) or 0.0)
    belief_agreement = max(0.0, safe_float(summary.get("weightedBeliefAgreement")) or 0.0)
    belief_polarization = max(0.0, safe_float(summary.get("weightedBeliefPolarization")) or 0.0)
    average_predicted_correlation = max(
        0.0,
        safe_float(summary.get("averagePredictedCorrelation")) or 0.0,
    )
    average_absolute_correlation = max(
        0.0,
        safe_float(summary.get("averageAbsoluteCorrelation")) or 0.0,
    )
    diversification_score = max(
        0.0,
        safe_float(summary.get("diversificationScore")) or 0.0,
    )
    crowded_pair_risk = max(
        0.0,
        safe_float(summary.get("crowdedPairRiskScore")) or 0.0,
    )
    linger = max(0.0, safe_float(summary.get("weightedDrawdownLingerDays")) or 0.0)
    spike_sustain = max(0.0, safe_float(summary.get("weightedSpikeSustainDays")) or 0.0)
    max_spike = max(0.0, safe_float(summary.get("weightedMaxSpikePct")) or 0.0)
    dark_horse = max(0.0, safe_float(summary.get("weightedDarkHorseScore")) or 0.0)
    persistence = max(0.0, safe_float(summary.get("weightedPersistencePct")) or 0.0)
    regime_risk = max(0.0, safe_float(summary.get("weightedRegimeRiskPct")) or 0.0)
    web_neural_score = safe_float(summary.get("weightedWebNeuralScore")) or 0.0
    web_neural_confidence = max(
        0.0,
        min(1.0, safe_float(summary.get("weightedWebNeuralConfidence")) or 0.0),
    )
    small_cap_tail = max(0.0, safe_float(summary.get("weightedSmallCapTailScore")) or 0.0)
    heavy_tail = max(0.0, safe_float(summary.get("weightedHeavyTailScore")) or 0.0)
    heavy_tail_premium = max(0.0, safe_float(summary.get("weightedHeavyTailPremium")) or 0.0)
    reddit_heat = max(0.0, min(1.0, safe_float(summary.get("redditSmallCapHeatScore")) or 0.0))
    fmkorea_heat = max(0.0, min(1.0, safe_float(summary.get("fmkoreaStockHeatScore")) or 0.0))
    fmkorea_surge = max(0.0, min(1.0, safe_float(summary.get("weightedFmkoreaSurgeScore")) or 0.0))
    geometry_alignment = max(0.0, safe_float(geometry.get("alignmentScore")) or 0.0)
    geometry_kl = max(0.0, safe_float(geometry.get("portfolioKlDivergence")) or 0.0)
    continuity = max(0.0, safe_float(manifold_report.get("continuityScore")) or 0.0)
    target_distance = max(0.0, safe_float(manifold_report.get("targetDistance")) or 0.0)
    champion_avg_reward = max(
        0.0,
        safe_float(summary.get("weightedChampionProphetAvgReward")) or 0.0,
    )
    champion_alignment = max(
        0.0,
        safe_float(summary.get("weightedChampionProphetAlignmentScore")) or 0.0,
    )
    champion_reward_count = max(
        0.0,
        safe_float(summary.get("weightedChampionProphetRewardCount")) or 0.0,
    )
    natural_gradient_upper_bound_score = max(
        0.0,
        safe_float(natural_gradient.get("upperBoundScore")) or 0.0,
    )
    natural_gradient_live_distance = max(
        0.0,
        safe_float(natural_gradient.get("liveDistanceToTarget")) or 0.0,
    )
    natural_gradient_live_bound_distance = max(
        0.0,
        safe_float(natural_gradient.get("liveDistanceToBound")) or 0.0,
    )
    natural_gradient_fisher_trace = max(
        0.0,
        safe_float(natural_gradient.get("fisherTrace")) or 0.0,
    )
    natural_gradient_fisher_curvature = max(
        0.0,
        safe_float(natural_gradient.get("fisherCurvature")) or 0.0,
    )
    score = (
        upside * 5.0
        + persistence * 1.8
        + geometry_alignment * 1.5
        + continuity * 1.6
        + spike_sustain * 0.08
        + max_spike * 2.1
        + champion_avg_reward * 0.75
        + champion_alignment * 0.55
        + min(0.16, champion_reward_count * 0.0035)
        + (dark_horse / 100.0) * 0.55
        + small_cap_tail * 0.42
        + heavy_tail * 0.58
        + heavy_tail_premium * (1.25 + reddit_heat * 0.55)
        + fmkorea_heat * 0.16
        + fmkorea_surge * 0.34
        + web_neural_score * 22.0 * max(0.25, web_neural_confidence)
        + web_neural_confidence * 0.22
        + belief_score * 0.012
        + belief_agreement * 0.32
        + diversification_score * 0.9
        + natural_gradient_upper_bound_score * 1.6
        + natural_gradient_fisher_trace * 0.55
        - uncertainty * 3.0
        - volatility * 0.035
        - average_predicted_correlation * 0.85
        - average_absolute_correlation * 0.35
        - crowded_pair_risk * 0.18
        - linger * 0.08
        - regime_risk * 1.75
        - belief_polarization * 0.28
        - geometry_kl * 1.1
        - target_distance * 0.8
        - natural_gradient_live_distance * 0.65
        - natural_gradient_live_bound_distance * 0.85
        - natural_gradient_fisher_curvature * 0.55
    )
    return float(score)


def build_sp500_portfolio(
    *,
    force_refresh: bool = False,
    holdings: int = DEFAULT_PORTFOLIO_HOLDINGS,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    trailing_days: int = DEFAULT_TRAILING_DAYS,
    map_limit: int = DEFAULT_LIMIT,
    cache_max_age_hours: float = DEFAULT_CACHE_MAX_AGE_HOURS,
) -> Dict[str, Any]:
    map_payload = build_sp500_information_map(
        force_refresh=force_refresh,
        limit=map_limit,
        cache_max_age_hours=cache_max_age_hours,
    )
    if not map_payload.get("ok"):
        return {
            "ok": False,
            "holdings": [],
            "error": map_payload.get("error") or "Failed to build the S&P500 portfolio.",
        }

    constituents, close_matrix = ensure_sp500_matrix()
    symbol_metadata = build_symbol_metadata(constituents)
    base_points = map_payload.get("points") or map_payload.get("topPicks") or []
    geometry_target = build_geometry_target(base_points)
    candidate_points = [
        point
        for point in base_points
        if str(point.get("finalAction") or "HOLD") != "SELL"
    ]
    generated_at = datetime.now().astimezone().isoformat()
    history_rows = load_recent_portfolio_history()
    reddit_smallcap = build_reddit_smallcap_snapshot(force_refresh=force_refresh)
    fmkorea_stock = build_fmkorea_stock_snapshot(force_refresh=force_refresh)
    methodology_objective = (
        "Blend Prophet moment maps, trajectory persistence, regime-shift risk, drawdown linger timing, "
        "website-internal neural next-day return estimates, upside spike sustain timing, cross-symbol correlation forecasting, symmetry-based dark-horse discovery, small-cap heavy-tail optionality, Reddit Daytrading small-cap tape heat, Korean retail surge pulse, aggregate human symbol-attention bias, belief-weighted conviction, upside, uncertainty, turnover, recent volatility, a Fisher-metric natural-gradient bound flow, and a KL-minimizing uncertainty-adjusted geometry target "
        "into a diversified weight set. The champion portfolio agent now compares multiple profile candidates against "
        "a temporal portfolio submanifold learned from prior snapshots."
    )

    candidate_reports: List[Dict[str, Any]] = []
    for profile in PORTFOLIO_PROFILES:
        effective_profile = PortfolioProfile(
            **{
                **profile.__dict__,
                "holdings": max(1, holdings if profile.name == "balanced_submanifold" else profile.holdings),
                "candidate_limit": max(
                    holdings if profile.name == "balanced_submanifold" else profile.holdings,
                    candidate_limit if profile.name == "balanced_submanifold" else profile.candidate_limit,
                ),
            }
        )
        candidate_payload = build_portfolio_candidate_payload(
            profile=effective_profile,
            candidate_points=candidate_points,
            close_matrix=close_matrix,
            symbol_metadata=symbol_metadata,
            geometry_target=geometry_target,
            generated_at=generated_at,
            map_date=map_payload.get("mapDate"),
            cache_payload=map_payload.get("cache") or {"used": False},
            trailing_days=max(20, trailing_days),
            methodology_objective=methodology_objective,
            reddit_smallcap=reddit_smallcap,
            fmkorea_stock=fmkorea_stock,
        )
        manifold_report = build_portfolio_manifold_report(history_rows, candidate_payload)
        candidate_reports.append(
            {
                **candidate_payload,
                "manifold": manifold_report,
                "score": evaluate_portfolio_candidate(candidate_payload, manifold_report),
                "profile": effective_profile.name,
                "label": effective_profile.label,
            }
        )

    selected_report = max(
        candidate_reports,
        key=lambda item: safe_float(item.get("score")) or -999.0,
    )
    champion_agent = build_champion_agent_summary(candidate_reports, selected_report)

    payload = {
        "ok": True,
        "generatedAt": generated_at,
        "mapDate": map_payload.get("mapDate"),
        "cache": map_payload.get("cache") or {"used": False},
        "summary": selected_report.get("summary"),
        "methodology": {
            **(selected_report.get("methodology") or {}),
            "objective": methodology_objective,
        },
        "geometry": selected_report.get("geometry"),
        "naturalGradient": selected_report.get("naturalGradient"),
        "correlationForecast": selected_report.get("correlationForecast"),
        "holdings": selected_report.get("holdings") or [],
        "manifold": selected_report.get("manifold"),
        "championAgent": champion_agent,
        "redditSmallCap": reddit_smallcap,
        "fmkoreaStock": fmkorea_stock,
    }
    persist_portfolio_history(payload)
    return payload


def main() -> None:
    args = parse_args()
    payload = build_sp500_portfolio(
        force_refresh=bool(args.force_refresh),
        holdings=max(1, int(args.holdings)),
        candidate_limit=max(1, int(args.candidate_limit)),
        trailing_days=max(20, int(args.trailing_days)),
        map_limit=max(1, int(args.map_limit)),
        cache_max_age_hours=max(0.0, float(args.cache_max_age_hours)),
    )
    print(json.dumps(payload, cls=PandasAndNumpyEncoder, ensure_ascii=False))


if __name__ == "__main__":
    main()
