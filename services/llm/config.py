from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class AgentSpec:
    name: str
    kind: str
    base_weight: float = 1.0
    enabled: bool = True
    params: Dict[str, float] = field(default_factory=dict)


@dataclass
class WrapperConfig:
    buy_vote_threshold: float = 0.25
    sell_vote_threshold: float = -0.25
    execution_gate_min_yes_votes: int = 2
    max_uncertainty_for_conservative_buy: float = 0.03
    max_slippage_bps: float = 50.0
    max_price_impact_pct: float = 0.30
    fast_drop_seconds: float = 300.0
    slow_drop_seconds: float = 3600.0
    peer_alpha: float = 0.10
    realized_alpha: float = 0.20
    min_weight: float = 0.20
    max_weight: float = 3.00
    byzantine_enabled: bool = True
    byzantine_vote_gap_threshold: float = 0.55
    byzantine_consensus_min_ratio: float = 0.60
    byzantine_weighted_consensus_min_ratio: float = 0.67
    byzantine_min_anomaly_score: float = 1.00
    byzantine_action_min_confidence: float = 0.55
    byzantine_flag_penalty: float = 0.75
    max_byzantine_agents: int = 0
    feedback_min_elapsed_seconds: float = 300.0
    realized_buy_return_pct: float = 0.003
    realized_sell_return_pct: float = 0.003
    em_iterations: int = 7
    em_convergence_tol: float = 0.001
    minimax_linear_ascent_step: float = 0.32
    minimax_logit_adjustment_strength: float = 0.62
    minimax_min_margin: float = 0.08
    agent_bagging_enabled: bool = True
    agent_bagging_iterations: int = 101
    agent_bagging_sample_fraction: float = 0.80
    agent_bagging_blend_alpha: float = 0.35
    agent_bagging_min_sample_size: int = 3
    agent_bagging_min_stability_for_execution: float = 0.55
    agent_bagging_seed_salt: str = "no-slip-agent-bagging-v1"


DEFAULT_AGENT_SPECS: List[AgentSpec] = [
    AgentSpec(name="final_action_agent", kind="final_action", base_weight=1.00),
    AgentSpec(name="time_to_below_agent", kind="time_to_below", base_weight=1.00),
    AgentSpec(name="geodesic_agent", kind="geodesic", base_weight=1.03),
    AgentSpec(name="minimax_prior_agent", kind="minimax_prior", base_weight=1.04),
    AgentSpec(name="em_regime_agent", kind="em_regime", base_weight=1.06),
    AgentSpec(name="spike_sustain_agent", kind="spike_sustain", base_weight=1.02),
    AgentSpec(name="drawdown_linger_agent", kind="drawdown_linger", base_weight=1.05),
    AgentSpec(name="regret_agent", kind="regret", base_weight=1.07),
    AgentSpec(name="conservative_gold_agent", kind="conservative_gold", base_weight=1.10),
    AgentSpec(name="execution_cost_agent", kind="execution_cost", base_weight=1.20),
]


def default_weight_state() -> Dict[str, float]:
    return {spec.name: spec.base_weight for spec in DEFAULT_AGENT_SPECS if spec.enabled}
