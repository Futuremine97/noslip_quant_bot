from __future__ import annotations

import hashlib
import random
from collections import Counter
from dataclasses import asdict, dataclass
from statistics import median
from typing import Any, Dict, List, Tuple

from .agents import ACTION_TO_VOTE, AgentDecision
from .config import WrapperConfig


@dataclass
class DebateResult:
    final_action: str
    weighted_vote: float
    execution_allowed: bool
    yes_execution_votes: int
    agent_outputs: List[Dict[str, Any]]
    weights: Dict[str, float]
    rationale: List[str]
    byzantine: Dict[str, Any]
    bagging: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _allowed_fault_count(decision_count: int, config: WrapperConfig) -> int:
    protocol_limit = max(0, (decision_count - 1) // 3)
    if config.max_byzantine_agents > 0:
        return min(protocol_limit, int(config.max_byzantine_agents))
    return protocol_limit


def _decision_weight(decision: AgentDecision, weights: Dict[str, float]) -> float:
    return max(0.05, float(weights.get(decision.name, 1.0)))


def _signed_vote(decision: AgentDecision) -> float:
    return float(decision.vote_value) * float(decision.confidence)


def _action_from_vote(weighted_vote: float, config: WrapperConfig) -> str:
    if weighted_vote >= config.buy_vote_threshold:
        return "BUY"
    if weighted_vote <= config.sell_vote_threshold:
        return "SELL"
    return "HOLD"


def _weighted_vote_summary(
    decisions: List[AgentDecision],
    weights: Dict[str, float],
) -> Tuple[float, int, float]:
    total_weight = 0.0
    vote_sum = 0.0
    yes_execution_votes = 0

    for decision in decisions:
        weight = float(weights.get(decision.name, 1.0))
        total_weight += weight
        vote_sum += decision.vote_value * decision.confidence * weight
        if decision.allow_execution and decision.action in {"BUY", "SELL"}:
            yes_execution_votes += 1

    return vote_sum / (total_weight or 1.0), yes_execution_votes, total_weight


def _vote_percentile(values: List[float], percentile: float) -> float:
    if not values:
        return 0.0

    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]

    rank = max(0.0, min(1.0, percentile)) * (len(ordered) - 1)
    lower_index = int(rank)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = rank - lower_index
    return ordered[lower_index] * (1.0 - fraction) + ordered[upper_index] * fraction


def bag_agent_decisions(
    decisions: List[AgentDecision],
    weights: Dict[str, float],
    config: WrapperConfig,
) -> Dict[str, Any]:
    default = {
        "enabled": bool(config.agent_bagging_enabled),
        "iterations": 0,
        "sampleSize": 0,
        "sampleFraction": float(config.agent_bagging_sample_fraction),
        "blendAlpha": float(config.agent_bagging_blend_alpha),
        "action": None,
        "meanAction": None,
        "meanVote": 0.0,
        "voteStd": 0.0,
        "voteInterval": {"lower": 0.0, "upper": 0.0},
        "stability": 0.0,
        "executionAllowedProbability": 0.0,
        "actionProbabilities": {"BUY": 0.0, "HOLD": 0.0, "SELL": 0.0},
    }
    if not config.agent_bagging_enabled or not decisions:
        return default

    iterations = max(1, int(config.agent_bagging_iterations))
    decision_count = len(decisions)
    sample_fraction = max(0.10, min(1.0, float(config.agent_bagging_sample_fraction)))
    min_sample_size = max(1, int(config.agent_bagging_min_sample_size))
    sample_size = min(decision_count, max(min_sample_size, round(decision_count * sample_fraction)))

    seed_material = "|".join(
        sorted(
            f"{decision.name}:{decision.action}:{decision.confidence:.6f}:"
            f"{decision.vote_value:.6f}:{float(weights.get(decision.name, 1.0)):.6f}"
            for decision in decisions
        )
    )
    seed_digest = hashlib.sha256(
        f"{config.agent_bagging_seed_salt}|{seed_material}".encode("utf-8")
    ).hexdigest()
    rng = random.Random(int(seed_digest[:16], 16))

    votes: List[float] = []
    action_counts = Counter()
    execution_allowed_count = 0

    for _ in range(iterations):
        sample = [rng.choice(decisions) for _ in range(sample_size)]
        sample_vote, sample_yes_votes, _ = _weighted_vote_summary(sample, weights)
        sample_action = _action_from_vote(sample_vote, config)
        votes.append(float(sample_vote))
        action_counts[sample_action] += 1
        if (
            sample_action != "HOLD"
            and sample_yes_votes >= config.execution_gate_min_yes_votes
        ):
            execution_allowed_count += 1

    mean_vote = sum(votes) / len(votes)
    vote_variance = sum((vote - mean_vote) ** 2 for vote in votes) / len(votes)
    probabilities = {
        action: float(action_counts.get(action, 0) / iterations)
        for action in ("BUY", "HOLD", "SELL")
    }
    action, stability = max(probabilities.items(), key=lambda item: item[1])

    return {
        **default,
        "iterations": iterations,
        "sampleSize": sample_size,
        "sampleFraction": sample_fraction,
        "action": action,
        "meanAction": _action_from_vote(mean_vote, config),
        "meanVote": float(mean_vote),
        "voteStd": float(vote_variance ** 0.5),
        "voteInterval": {
            "lower": float(_vote_percentile(votes, 0.05)),
            "upper": float(_vote_percentile(votes, 0.95)),
        },
        "stability": float(stability),
        "executionAllowedProbability": float(execution_allowed_count / iterations),
        "actionProbabilities": probabilities,
    }


def _weighted_median(values: List[Tuple[float, float]]) -> float:
    if not values:
        return 0.0

    normalized = [
        (float(value), max(0.0, float(weight)))
        for value, weight in values
    ]
    total_weight = sum(weight for _, weight in normalized)
    if total_weight <= 0:
        return float(median(value for value, _ in normalized))

    running_weight = 0.0
    for value, weight in sorted(normalized, key=lambda item: item[0]):
        running_weight += weight
        if running_weight >= total_weight / 2.0:
            return value
    return float(sorted(normalized, key=lambda item: item[0])[-1][0])


def _weighted_action_consensus(
    decisions: List[AgentDecision], weights: Dict[str, float]
) -> Tuple[str | None, float]:
    action_weights: Dict[str, float] = {}
    total_weight = 0.0

    for decision in decisions:
        effective_weight = _decision_weight(decision, weights) * max(
            0.15, float(decision.confidence)
        )
        action_weights[decision.action] = (
            action_weights.get(decision.action, 0.0) + effective_weight
        )
        total_weight += effective_weight

    if not action_weights or total_weight <= 0:
        return None, 0.0

    action, action_weight = max(action_weights.items(), key=lambda item: item[1])
    return action, float(action_weight / total_weight)


def detect_byzantine_agents(
    decisions: List[AgentDecision],
    weights: Dict[str, float],
    config: WrapperConfig,
) -> Dict[str, Any]:
    default = {
        "enabled": bool(config.byzantine_enabled),
        "toleratedFaults": 0,
        "consensusAction": None,
        "consensusRatio": 0.0,
        "weightedConsensusAction": None,
        "weightedConsensusRatio": 0.0,
        "medianSignedVote": 0.0,
        "robustCenterVote": 0.0,
        "trustedAgents": [],
        "flaggedAgents": [],
    }
    if not config.byzantine_enabled or len(decisions) < 3:
        default["trustedAgents"] = [decision.name for decision in decisions]
        return default

    signed_votes = {decision.name: _signed_vote(decision) for decision in decisions}
    median_signed_vote = float(median(signed_votes.values())) if signed_votes else 0.0
    robust_center_vote = _weighted_median(
        [
            (signed_votes[decision.name], _decision_weight(decision, weights))
            for decision in decisions
        ]
    )
    action_counts = Counter(decision.action for decision in decisions)
    consensus_action = None
    consensus_ratio = 0.0
    if action_counts:
        consensus_action, consensus_count = action_counts.most_common(1)[0]
        consensus_ratio = consensus_count / max(len(decisions), 1)
    weighted_consensus_action, weighted_consensus_ratio = _weighted_action_consensus(
        decisions, weights
    )
    strong_quorum_action = (
        consensus_action
        if consensus_action is not None
        and consensus_action == weighted_consensus_action
        and consensus_ratio >= config.byzantine_consensus_min_ratio
        and weighted_consensus_ratio >= config.byzantine_weighted_consensus_min_ratio
        else None
    )

    tolerated_faults = _allowed_fault_count(len(decisions), config)
    candidates: List[Dict[str, Any]] = []
    trusted_names = [decision.name for decision in decisions]

    for decision in decisions:
        signed_vote = signed_votes[decision.name]
        vote_gap = abs(signed_vote - robust_center_vote)
        hard_vote_outlier = vote_gap >= config.byzantine_vote_gap_threshold
        action_mismatch = (
            strong_quorum_action is not None
            and decision.action != strong_quorum_action
            and float(decision.confidence) >= config.byzantine_action_min_confidence
        )
        allow_mismatch = (
            strong_quorum_action is not None
            and decision.allow_execution
            != (strong_quorum_action in {"BUY", "SELL"})
            and float(decision.confidence) >= config.byzantine_action_min_confidence
        )
        anomaly_score = 0.0
        reasons: List[str] = []

        if hard_vote_outlier:
            normalized_gap = vote_gap / max(config.byzantine_vote_gap_threshold, 1e-9)
            reasons.append(
                f"vote_gap={vote_gap:.3f} exceeds threshold {config.byzantine_vote_gap_threshold:.3f}"
            )
            anomaly_score += normalized_gap
        if action_mismatch:
            reasons.append(
                f"action deviates from robust quorum action {strong_quorum_action}"
            )
            anomaly_score += 0.35 + max(
                0.0,
                weighted_consensus_ratio - config.byzantine_weighted_consensus_min_ratio,
            )
        if allow_mismatch:
            reasons.append("execution permission deviates from quorum direction")
            anomaly_score += 0.20

        if hard_vote_outlier or anomaly_score >= config.byzantine_min_anomaly_score:
            candidates.append(
                {
                    "name": decision.name,
                    "action": decision.action,
                    "confidence": float(decision.confidence),
                    "signedVote": signed_vote,
                    "voteGap": vote_gap,
                    "anomalyScore": anomaly_score,
                    "reasons": reasons,
                }
            )

    candidates.sort(key=lambda item: item["anomalyScore"], reverse=True)
    flagged = candidates[:tolerated_faults] if tolerated_faults > 0 else []
    flagged_names = {item["name"] for item in flagged}
    if flagged_names and len(flagged_names) < len(decisions):
        trusted_names = [decision.name for decision in decisions if decision.name not in flagged_names]

    return {
        "enabled": True,
        "toleratedFaults": tolerated_faults,
        "consensusAction": consensus_action,
        "consensusRatio": float(consensus_ratio),
        "weightedConsensusAction": weighted_consensus_action,
        "weightedConsensusRatio": float(weighted_consensus_ratio),
        "medianSignedVote": median_signed_vote,
        "robustCenterVote": robust_center_vote,
        "trustedAgents": trusted_names,
        "flaggedAgents": flagged,
    }


def aggregate_agent_decisions(
    decisions: List[AgentDecision],
    weights: Dict[str, float],
    config: WrapperConfig,
) -> DebateResult:
    byzantine = detect_byzantine_agents(decisions, weights, config)
    flagged_names = {item["name"] for item in byzantine.get("flaggedAgents", [])}
    trusted_decisions = [
        decision for decision in decisions if decision.name in byzantine.get("trustedAgents", [])
    ] or list(decisions)

    rationale: List[str] = []

    for decision in decisions:
        weight = float(weights.get(decision.name, 1.0))
        rationale.append(
            f"{decision.name}: action={decision.action}, confidence={decision.confidence:.3f}, weight={weight:.3f}"
            + (" [flagged-byzantine]" if decision.name in flagged_names else "")
        )

    base_weighted_vote, yes_execution_votes, _ = _weighted_vote_summary(
        trusted_decisions, weights
    )
    bagging = bag_agent_decisions(trusted_decisions, weights, config)
    bagging["baseWeightedVote"] = float(base_weighted_vote)
    blend_alpha = (
        max(0.0, min(1.0, float(config.agent_bagging_blend_alpha)))
        if bagging.get("enabled")
        else 0.0
    )
    weighted_vote = (
        base_weighted_vote * (1.0 - blend_alpha)
        + float(bagging.get("meanVote") or 0.0) * blend_alpha
    )
    bagging["blendedVote"] = float(weighted_vote)
    final_action = _action_from_vote(weighted_vote, config)

    bagging_execution_ok = True
    if bagging.get("enabled") and final_action != "HOLD":
        bagging_execution_ok = (
            bagging.get("action") == final_action
            and float(bagging.get("stability") or 0.0)
            >= config.agent_bagging_min_stability_for_execution
        )

    execution_allowed = (
        final_action != "HOLD"
        and yes_execution_votes >= config.execution_gate_min_yes_votes
        and bagging_execution_ok
    )
    rationale.append(
        f"weighted_vote={weighted_vote:.4f}, base_weighted_vote={base_weighted_vote:.4f}, final_action={final_action}, yes_execution_votes={yes_execution_votes}"
    )
    if bagging.get("enabled"):
        action_probabilities = bagging.get("actionProbabilities") or {}
        rationale.append(
            "agent_bagging="
            + f"{bagging.get('action') or 'unknown'} "
            + f"mean_vote={float(bagging.get('meanVote') or 0.0):.4f}, "
            + f"vote_std={float(bagging.get('voteStd') or 0.0):.4f}, "
            + f"stability={float(bagging.get('stability') or 0.0):.3f}, "
            + "p_buy="
            + f"{float(action_probabilities.get('BUY') or 0.0):.3f}, "
            + "p_hold="
            + f"{float(action_probabilities.get('HOLD') or 0.0):.3f}, "
            + "p_sell="
            + f"{float(action_probabilities.get('SELL') or 0.0):.3f}"
        )
        if not bagging_execution_ok:
            rationale.append(
                "agent_bagging blocked execution because resampled council stability or direction was insufficient"
            )
        elif final_action != "HOLD":
            rationale.append("agent_bagging confirmed execution direction stability")
    if flagged_names:
        rationale.append(
            "byzantine_filter flagged "
            + ", ".join(sorted(flagged_names))
            + f" (trusted={len(trusted_decisions)}/{len(decisions)})"
        )
    if not execution_allowed:
        rationale.append("execution gate blocked by insufficient execution-allowing agent votes")

    return DebateResult(
        final_action=final_action,
        weighted_vote=weighted_vote,
        execution_allowed=execution_allowed,
        yes_execution_votes=yes_execution_votes,
        agent_outputs=[decision.to_dict() for decision in decisions],
        weights=dict(weights),
        rationale=rationale,
        byzantine=byzantine,
        bagging=bagging,
    )


def update_agent_weights(
    decisions: List[AgentDecision],
    weights: Dict[str, float],
    realized_action: str | None,
    config: WrapperConfig,
    byzantine: Dict[str, Any] | None = None,
) -> Dict[str, float]:
    new_weights = dict(weights)
    if not decisions:
        return new_weights

    flagged_names = {
        item.get("name")
        for item in ((byzantine or {}).get("flaggedAgents") or [])
        if isinstance(item, dict)
    }
    flagged_meta = {
        item.get("name"): item
        for item in ((byzantine or {}).get("flaggedAgents") or [])
        if isinstance(item, dict) and item.get("name")
    }
    trusted_decisions = [
        decision for decision in decisions if decision.name not in flagged_names
    ] or list(decisions)
    trusted_weight_total = sum(
        float(weights.get(decision.name, 1.0)) for decision in trusted_decisions
    )
    peer_vote = (
        sum(
            _signed_vote(decision) * float(weights.get(decision.name, 1.0))
            for decision in trusted_decisions
        )
        / max(trusted_weight_total, 1e-9)
    )
    for decision in decisions:
        weight = float(new_weights.get(decision.name, 1.0))

        peer_gap = abs(_signed_vote(decision) - peer_vote)
        peer_multiplier = max(0.50, 1.0 - config.peer_alpha * peer_gap)

        if realized_action is None:
            realized_multiplier = 1.0
        else:
            if decision.action == realized_action:
                realized_multiplier = 1.0 + config.realized_alpha
            elif decision.action == "HOLD" and realized_action == "HOLD":
                realized_multiplier = 1.0 + config.realized_alpha * 0.5
            else:
                realized_multiplier = max(0.50, 1.0 - config.realized_alpha)

        updated = weight * peer_multiplier * realized_multiplier
        if decision.name in flagged_names:
            anomaly_score = float(
                (flagged_meta.get(decision.name) or {}).get(
                    "anomalyScore", config.byzantine_min_anomaly_score
                )
            )
            severity = min(
                1.75,
                max(1.0, anomaly_score / max(config.byzantine_min_anomaly_score, 1e-9)),
            )
            penalty = max(
                0.35,
                1.0 - (1.0 - config.byzantine_flag_penalty) * severity,
            )
            updated *= penalty
        updated = max(config.min_weight, min(config.max_weight, updated))
        new_weights[decision.name] = updated

    return new_weights
