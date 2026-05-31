from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from .agents import AgentDecision, build_wrapper_agent
from .config import DEFAULT_AGENT_SPECS, WrapperConfig, default_weight_state
from .debate import aggregate_agent_decisions, update_agent_weights


def extract_decision_features(
    decision: Dict[str, Any],
    execution_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    features = dict(decision)

    avg_uncertainty = None
    per_rule = decision.get("per_rule") or {}
    values: List[float] = []
    for _, payload in per_rule.items():
        direction = payload.get("direction") if isinstance(payload, dict) else None
        details = direction.get("details") if isinstance(direction, dict) else None
        if isinstance(details, pd.DataFrame) and "uncertainty_ratio" in details.columns:
            values.extend([float(x) for x in details["uncertainty_ratio"].dropna().tolist()])
    if values:
        avg_uncertainty = sum(values) / len(values)
    features["avg_uncertainty_ratio"] = avg_uncertainty

    execution_context = execution_context or {}
    features["observed_slippage_bps"] = execution_context.get(
        "slippageBps", decision.get("observed_slippage_bps", 0.0)
    )
    features["observed_price_impact_pct"] = execution_context.get(
        "priceImpactPct", decision.get("observed_price_impact_pct", 0.0)
    )
    features["observed_total_time"] = execution_context.get(
        "totalTime", decision.get("observed_total_time", 0.0)
    )
    return features


def run_wrapper_pipeline(
    decision: Dict[str, Any],
    execution_context: Optional[Dict[str, Any]] = None,
    weight_state: Optional[Dict[str, float]] = None,
    config: Optional[WrapperConfig] = None,
) -> Dict[str, Any]:
    config = config or WrapperConfig()
    weight_state = dict(weight_state or default_weight_state())
    features = extract_decision_features(decision, execution_context=execution_context)

    wrapper_agents = [
        build_wrapper_agent(
            spec.kind,
            spec.name,
            params={
                **spec.params,
                "fast_drop_seconds": config.fast_drop_seconds,
                "slow_drop_seconds": config.slow_drop_seconds,
                "max_uncertainty_for_buy": config.max_uncertainty_for_conservative_buy,
                "max_slippage_bps": config.max_slippage_bps,
                "max_price_impact_pct": config.max_price_impact_pct,
                "em_iterations": config.em_iterations,
                "em_convergence_tol": config.em_convergence_tol,
                "minimax_linear_ascent_step": config.minimax_linear_ascent_step,
                "minimax_logit_adjustment_strength": config.minimax_logit_adjustment_strength,
                "minimax_min_margin": config.minimax_min_margin,
            },
        )
        for spec in DEFAULT_AGENT_SPECS
        if spec.enabled
    ]

    decisions: List[AgentDecision] = [agent.decide(features) for agent in wrapper_agents]
    debate = aggregate_agent_decisions(decisions, weight_state, config)

    return {
        "wrapper_final_action": debate.final_action,
        "wrapper_weighted_vote": debate.weighted_vote,
        "wrapper_execution_allowed": debate.execution_allowed,
        "wrapper_yes_execution_votes": debate.yes_execution_votes,
        "wrapper_agent_outputs": debate.agent_outputs,
        "wrapper_weights": debate.weights,
        "wrapper_rationale": debate.rationale,
        "wrapper_byzantine": debate.byzantine,
        "wrapper_bagging": debate.bagging,
    }


def update_wrapper_weights(
    decision: Dict[str, Any],
    realized_action: Optional[str],
    previous_result: Dict[str, Any],
    previous_weights: Optional[Dict[str, float]] = None,
    config: Optional[WrapperConfig] = None,
) -> Dict[str, float]:
    config = config or WrapperConfig()
    weights = dict(
        previous_weights or previous_result.get("wrapper_weights") or default_weight_state()
    )
    outputs = previous_result.get("wrapper_agent_outputs", [])
    agent_decisions = [AgentDecision(**item) for item in outputs]
    return update_agent_weights(
        agent_decisions,
        weights,
        realized_action=realized_action,
        byzantine=previous_result.get("wrapper_byzantine"),
        config=config,
    )
