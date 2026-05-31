from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Dict, List, Optional


ACTION_TO_VOTE = {"BUY": 1.0, "SELL": -1.0, "HOLD": 0.0, "WAIT": 0.0}


@dataclass
class AgentDecision:
    name: str
    action: str
    confidence: float
    vote_value: float
    allow_execution: bool
    reasons: List[str]
    local_metrics: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BaseWrapperAgent:
    def __init__(self, name: str, params: Optional[Dict[str, float]] = None):
        self.name = name
        self.params = params or {}

    def decide(self, features: Dict[str, Any]) -> AgentDecision:
        raise NotImplementedError

    @staticmethod
    def _clamp01(x: float) -> float:
        return max(0.0, min(1.0, float(x)))


class FinalActionAgent(BaseWrapperAgent):
    def decide(self, features: Dict[str, Any]) -> AgentDecision:
        action = str(features.get("final_action", "HOLD"))
        strength = abs(float(features.get("direction_strength", 0.0) or 0.0))
        first_moment = features.get("first_moment_pct_per_hour")
        second_moment = features.get("second_moment_pct_per_hour2")
        rise_window_seconds = features.get("rise_window_seconds")
        drop_window_seconds = features.get("drop_window_seconds")
        confidence = self._clamp01(min(1.0, strength * 20.0))
        reasons = [
            f"uses runtime final_action={action}",
            f"direction_strength={features.get('direction_strength')}",
            f"first_moment_pct_per_hour={first_moment}",
            f"second_moment_pct_per_hour2={second_moment}",
            f"rise_window_seconds={rise_window_seconds}",
            f"drop_window_seconds={drop_window_seconds}",
        ]
        return AgentDecision(
            name=self.name,
            action=action,
            confidence=confidence,
            vote_value=ACTION_TO_VOTE.get(action, 0.0),
            allow_execution=(action != "HOLD"),
            reasons=reasons,
            local_metrics={
                "direction_strength": features.get("direction_strength"),
                "first_moment_pct_per_hour": first_moment,
                "second_moment_pct_per_hour2": second_moment,
                "time_to_optimal_buy_seconds": features.get("time_to_optimal_buy_seconds"),
                "time_to_optimal_sell_seconds": features.get("time_to_optimal_sell_seconds"),
                "rise_window_seconds": rise_window_seconds,
                "drop_window_seconds": drop_window_seconds,
                "optimal_buy_timestamp": features.get("optimal_buy_timestamp"),
                "optimal_sell_timestamp": features.get("optimal_sell_timestamp"),
            },
        )


class TimeToBelowAgent(BaseWrapperAgent):
    def decide(self, features: Dict[str, Any]) -> AgentDecision:
        base_action = str(features.get("final_action", "HOLD"))
        ttb = features.get("time_to_below_current_seconds")
        optimal_buy_timestamp = features.get("optimal_buy_timestamp")
        optimal_sell_timestamp = features.get("optimal_sell_timestamp")
        rise_window_seconds = features.get("rise_window_seconds")
        drop_window_seconds = features.get("drop_window_seconds")
        fast_drop_seconds = float(self.params.get("fast_drop_seconds", 300.0))
        slow_drop_seconds = float(self.params.get("slow_drop_seconds", 3600.0))

        reasons: List[str] = [
            f"time_to_below_current_seconds={ttb}",
            f"optimal_buy_timestamp={optimal_buy_timestamp}",
            f"optimal_sell_timestamp={optimal_sell_timestamp}",
            f"rise_window_seconds={rise_window_seconds}",
            f"drop_window_seconds={drop_window_seconds}",
        ]
        action = base_action
        confidence = 0.50

        if ttb is None:
            reasons.append("no below-current crossing found; keeps baseline action")
            if base_action == "BUY":
                confidence = 0.65
            elif base_action == "SELL":
                confidence = 0.55
            else:
                confidence = 0.55
        else:
            ttb = float(ttb)
            if base_action == "BUY":
                if ttb <= fast_drop_seconds:
                    action = "HOLD"
                    confidence = 0.85
                    reasons.append("predicted drop comes too soon after now; blocks BUY")
                elif ttb <= slow_drop_seconds:
                    action = "HOLD"
                    confidence = 0.65
                    reasons.append("drop arrives within moderate horizon; cautious HOLD")
                else:
                    action = "BUY"
                    confidence = 0.70
                    reasons.append("drop is far enough away; BUY remains acceptable")
            elif base_action == "SELL":
                if ttb <= fast_drop_seconds:
                    action = "SELL"
                    confidence = 0.85
                    reasons.append("price forecast drops soon; strengthens SELL")
                else:
                    action = "HOLD"
                    confidence = 0.55
                    reasons.append("drop is not immediate; softens SELL")
            else:
                action = "HOLD"
                confidence = 0.60
                reasons.append("baseline already HOLD")

        return AgentDecision(
            name=self.name,
            action=action,
            confidence=confidence,
            vote_value=ACTION_TO_VOTE.get(action, 0.0),
            allow_execution=(action != "HOLD"),
            reasons=reasons,
            local_metrics={
                "time_to_below_current_seconds": features.get("time_to_below_current_seconds"),
                "first_moment_pct_per_hour": features.get("first_moment_pct_per_hour"),
                "second_moment_pct_per_hour2": features.get("second_moment_pct_per_hour2"),
                "time_to_optimal_buy_seconds": features.get("time_to_optimal_buy_seconds"),
                "time_to_optimal_sell_seconds": features.get("time_to_optimal_sell_seconds"),
                "rise_window_seconds": rise_window_seconds,
                "drop_window_seconds": drop_window_seconds,
                "optimal_buy_timestamp": optimal_buy_timestamp,
                "optimal_sell_timestamp": optimal_sell_timestamp,
            },
        )


class GeodesicAgent(BaseWrapperAgent):
    def decide(self, features: Dict[str, Any]) -> AgentDecision:
        base_action = str(features.get("final_action", "HOLD"))
        geodesic_available = bool(features.get("geodesic_available"))
        geodesic_label = features.get("geodesic_label")
        geodesic_action_bias = str(features.get("geodesic_action_bias") or "hold")
        geodesic_history_count = features.get("geodesic_history_count")
        geodesic_path_length = features.get("geodesic_path_length")
        geodesic_curvature = features.get("geodesic_curvature")
        geodesic_alignment_score = features.get("geodesic_alignment_score")
        geodesic_deviation_score = features.get("geodesic_deviation_score")
        geodesic_continuation_score = features.get("geodesic_continuation_score")
        geodesic_confidence = features.get("geodesic_confidence")
        geodesic_projected_first_coordinate_drift = features.get(
            "geodesic_projected_first_coordinate_drift"
        )
        geodesic_projected_second_coordinate_drift = features.get(
            "geodesic_projected_second_coordinate_drift"
        )
        geodesic_stability_score = features.get("geodesic_stability_score")
        geodesic_persistence_score = features.get("geodesic_persistence_score")
        geodesic_regime_shift_risk = features.get("geodesic_regime_shift_risk")

        reasons: List[str] = [
            f"geodesic_available={geodesic_available}",
            f"geodesic_label={geodesic_label}",
            f"geodesic_action_bias={geodesic_action_bias}",
            f"geodesic_history_count={geodesic_history_count}",
            f"geodesic_path_length={geodesic_path_length}",
            f"geodesic_curvature={geodesic_curvature}",
            f"geodesic_alignment_score={geodesic_alignment_score}",
            f"geodesic_deviation_score={geodesic_deviation_score}",
            f"geodesic_continuation_score={geodesic_continuation_score}",
            f"geodesic_confidence={geodesic_confidence}",
            f"geodesic_projected_first_coordinate_drift={geodesic_projected_first_coordinate_drift}",
            f"geodesic_projected_second_coordinate_drift={geodesic_projected_second_coordinate_drift}",
            f"geodesic_stability_score={geodesic_stability_score}",
            f"geodesic_persistence_score={geodesic_persistence_score}",
            f"geodesic_regime_shift_risk={geodesic_regime_shift_risk}",
        ]

        if not geodesic_available:
            reasons.append("geodesic history not ready; keeps baseline action")
            return AgentDecision(
                name=self.name,
                action=base_action,
                confidence=0.54 if base_action != "HOLD" else 0.58,
                vote_value=ACTION_TO_VOTE.get(base_action, 0.0),
                allow_execution=(base_action != "HOLD"),
                reasons=reasons,
                local_metrics={
                    "geodesic_available": geodesic_available,
                    "geodesic_label": geodesic_label,
                    "geodesic_action_bias": geodesic_action_bias,
                    "geodesic_history_count": geodesic_history_count,
                },
            )

        continuation = float(geodesic_continuation_score or 0.0)
        curvature = float(geodesic_curvature or 0.0)
        deviation = float(geodesic_deviation_score or 0.0)
        confidence = self._clamp01(
            0.46
            + float(geodesic_confidence or 0.0) * 0.34
            + float(geodesic_alignment_score or 0.0) * 0.10
            + continuation * 0.10
        )
        action = base_action

        if base_action == "BUY":
            if geodesic_action_bias == "sell" and (curvature >= 0.95 or deviation >= 0.95):
                action = "HOLD"
                confidence = self._clamp01(confidence + 0.16)
                reasons.append("geodesic bends against BUY; softens to HOLD")
            elif geodesic_action_bias == "buy" and continuation >= 0.64:
                action = "BUY"
                confidence = self._clamp01(confidence + 0.08)
                reasons.append("geodesic continuation supports BUY")
            else:
                reasons.append("geodesic signal is mixed; keeps BUY with caution")
        elif base_action == "SELL":
            if geodesic_action_bias == "buy" and continuation >= 0.62:
                action = "HOLD"
                confidence = self._clamp01(confidence + 0.14)
                reasons.append("geodesic continuation still positive; softens SELL")
            elif geodesic_action_bias == "sell":
                action = "SELL"
                confidence = self._clamp01(confidence + 0.08)
                reasons.append("geodesic drift supports SELL")
            else:
                reasons.append("geodesic signal is mixed; keeps SELL with caution")
        else:
            if geodesic_action_bias == "buy" and continuation >= 0.68:
                action = "BUY"
                confidence = self._clamp01(confidence + 0.10)
                reasons.append("baseline HOLD tilts BUY because geodesic continues upward")
            elif geodesic_action_bias == "sell" and (curvature >= 0.82 or deviation >= 0.82):
                action = "SELL"
                confidence = self._clamp01(confidence + 0.08)
                reasons.append("baseline HOLD tilts SELL because geodesic kinks downward")
            else:
                action = "HOLD"
                reasons.append("baseline HOLD remains appropriate on the geodesic")

        return AgentDecision(
            name=self.name,
            action=action,
            confidence=confidence,
            vote_value=ACTION_TO_VOTE.get(action, 0.0),
            allow_execution=(action != "HOLD"),
            reasons=reasons,
            local_metrics={
                "geodesic_available": geodesic_available,
                "geodesic_label": geodesic_label,
                "geodesic_action_bias": geodesic_action_bias,
                "geodesic_history_count": geodesic_history_count,
                "geodesic_path_length": geodesic_path_length,
                "geodesic_curvature": geodesic_curvature,
                "geodesic_alignment_score": geodesic_alignment_score,
                "geodesic_deviation_score": geodesic_deviation_score,
                "geodesic_continuation_score": geodesic_continuation_score,
                "geodesic_confidence": geodesic_confidence,
                "geodesic_projected_first_coordinate_drift": geodesic_projected_first_coordinate_drift,
                "geodesic_projected_second_coordinate_drift": geodesic_projected_second_coordinate_drift,
                "geodesic_stability_score": geodesic_stability_score,
                "geodesic_persistence_score": geodesic_persistence_score,
                "geodesic_regime_shift_risk": geodesic_regime_shift_risk,
                "time_to_optimal_buy_seconds": features.get("time_to_optimal_buy_seconds"),
                "time_to_optimal_sell_seconds": features.get("time_to_optimal_sell_seconds"),
            },
        )


class MinimaxPriorAgent(BaseWrapperAgent):
    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if math.isfinite(numeric) else None

    @staticmethod
    def _clip(value: float, floor: float, ceiling: float) -> float:
        return max(floor, min(ceiling, float(value)))

    def _signal(self, value: Optional[float], scale: float, cap: float = 3.0) -> float:
        if value is None:
            return 0.0
        return self._clip(value * scale, -cap, cap)

    def _softmax(self, logits: Dict[str, float]) -> Dict[str, float]:
        items = list(logits.items())
        maximum = max(value for _, value in items)
        exps = {label: math.exp(value - maximum) for label, value in items}
        total = sum(exps.values()) or 1e-12
        return {label: value / total for label, value in exps.items()}

    def _implied_upside(self, features: Dict[str, Any]) -> Optional[float]:
        current_price = self._safe_float(features.get("current_price"))
        target_price = self._safe_float(features.get("target_price"))
        if current_price is None or target_price is None or current_price == 0:
            return None
        return (target_price / current_price) - 1.0

    def decide(self, features: Dict[str, Any]) -> AgentDecision:
        base_action = str(features.get("final_action", "HOLD") or "HOLD").upper()
        cadence_profile = str(features.get("cadence_profile", "intraday") or "intraday")
        unit_seconds = 3600.0 if cadence_profile == "intraday" else 86400.0
        medium_window_seconds = 8 * unit_seconds if cadence_profile == "intraday" else 5 * 86400.0
        long_window_seconds = 24 * unit_seconds if cadence_profile == "intraday" else 20 * 86400.0
        ascent_step = max(
            0.05,
            float(self.params.get("minimax_linear_ascent_step", 0.32)),
        )
        logit_adjustment_strength = max(
            0.10,
            float(self.params.get("minimax_logit_adjustment_strength", 0.62)),
        )
        min_margin = max(0.03, float(self.params.get("minimax_min_margin", 0.08)))

        direction_strength = self._safe_float(features.get("direction_strength")) or 0.0
        first_moment = self._safe_float(features.get("first_moment_pct_per_hour")) or 0.0
        second_moment = self._safe_float(features.get("second_moment_pct_per_hour2")) or 0.0
        avg_uncertainty = (
            self._safe_float(features.get("avg_uncertainty_ratio"))
            or self._safe_float(features.get("uncertainty_ratio"))
            or 0.0
        )
        implied_upside = self._implied_upside(features) or 0.0
        time_to_below = self._safe_float(features.get("time_to_below_current_seconds"))

        drawdown_linger_seconds = self._safe_float(
            features.get("drawdown_linger_consensus_seconds")
        )
        if drawdown_linger_seconds is None:
            drawdown_linger_seconds = self._safe_float(features.get("drawdown_linger_seconds"))
        max_drawdown_pct = self._safe_float(features.get("max_drawdown_consensus_pct"))
        if max_drawdown_pct is None:
            max_drawdown_pct = self._safe_float(features.get("max_drawdown_pct"))

        spike_sustain_seconds = self._safe_float(features.get("spike_sustain_consensus_seconds"))
        if spike_sustain_seconds is None:
            spike_sustain_seconds = self._safe_float(features.get("spike_sustain_seconds"))
        max_spike_pct = self._safe_float(features.get("max_spike_consensus_pct"))
        if max_spike_pct is None:
            max_spike_pct = self._safe_float(features.get("max_spike_pct"))

        tail_long_score = self._safe_float(features.get("tail_long_score")) or 0.0
        tail_heavy_score = self._safe_float(features.get("tail_heavy_score")) or 0.0
        tail_left_risk_score = self._safe_float(features.get("tail_left_risk_score")) or 0.0
        tail_regime_label = str(features.get("tail_regime_label") or "tail-neutral")

        positive_direction = self._clip(direction_strength * 14.0, 0.0, 3.0)
        negative_direction = self._clip((-direction_strength) * 14.0, 0.0, 3.0)
        positive_drift = self._clip(first_moment * 9000.0, 0.0, 3.0)
        negative_drift = self._clip((-first_moment) * 9000.0, 0.0, 3.0)
        convex_up = self._clip(second_moment * 42000.0, 0.0, 3.0)
        convex_down = self._clip((-second_moment) * 42000.0, 0.0, 3.0)
        uncertainty_penalty = self._clip(avg_uncertainty * 24.0, 0.0, 3.0)
        upside_signal = self._clip(implied_upside * 14.0, 0.0, 3.0)
        drawdown_signal = self._clip(abs(min(0.0, max_drawdown_pct or 0.0)) * 10.0, 0.0, 3.0)
        linger_signal = self._clip(
            (drawdown_linger_seconds or 0.0) / max(long_window_seconds, 1.0) * 1.8,
            0.0,
            3.0,
        )
        spike_signal = self._clip(max(0.0, max_spike_pct or 0.0) * 8.5, 0.0, 3.0)
        sustain_signal = self._clip(
            (spike_sustain_seconds or 0.0) / max(long_window_seconds, 1.0) * 1.5,
            0.0,
            3.0,
        )
        long_tail_signal = self._clip(tail_long_score * 2.8, 0.0, 3.0)
        heavy_tail_signal = self._clip(tail_heavy_score * 2.5, 0.0, 3.0)
        left_tail_signal = self._clip(tail_left_risk_score * 2.8, 0.0, 3.0)

        fast_drop_signal = 0.0
        if time_to_below is not None:
            fast_drop_signal = self._clip(
                1.0 - (time_to_below / max(medium_window_seconds, 1.0)),
                0.0,
                1.0,
            )

        geodesic_bias = str(features.get("geodesic_action_bias") or "hold").lower()
        geodesic_continuation = self._safe_float(features.get("geodesic_continuation_score")) or 0.0
        geodesic_alignment = self._safe_float(features.get("geodesic_alignment_score")) or 0.0
        geodesic_sell_penalty = 0.0
        geodesic_buy_bonus = 0.0
        if geodesic_bias == "buy":
            geodesic_buy_bonus = 0.55 + geodesic_continuation * 0.65 + geodesic_alignment * 0.25
        elif geodesic_bias == "sell":
            geodesic_sell_penalty = 0.55 + geodesic_continuation * 0.65 + geodesic_alignment * 0.25

        buy_logit = (
            0.24
            + positive_direction * 0.82
            + positive_drift * 0.78
            + convex_up * 0.22
            + upside_signal * 0.68
            + spike_signal * 0.46
            + sustain_signal * 0.32
            + long_tail_signal * 0.42
            + geodesic_buy_bonus
            - uncertainty_penalty * 0.46
            - drawdown_signal * 0.48
            - linger_signal * 0.38
            - left_tail_signal * 0.56
            - geodesic_sell_penalty * 0.34
        )
        sell_logit = (
            0.24
            + negative_direction * 0.82
            + negative_drift * 0.76
            + convex_down * 0.24
            + drawdown_signal * 0.58
            + linger_signal * 0.42
            + left_tail_signal * 0.50
            + fast_drop_signal * 0.62
            + geodesic_sell_penalty
            - upside_signal * 0.52
            - spike_signal * 0.34
            - sustain_signal * 0.22
            - long_tail_signal * 0.46
            - geodesic_buy_bonus * 0.34
        )
        neutrality_signal = self._clip(
            1.0 - min(1.0, abs(direction_strength) * 7.0 + abs(first_moment) * 4200.0),
            0.0,
            1.0,
        )
        hold_logit = (
            0.36
            + uncertainty_penalty * 0.58
            + neutrality_signal * 0.92
            + heavy_tail_signal * 0.18
            - max(upside_signal, drawdown_signal + linger_signal * 0.6) * 0.18
        )

        if base_action == "BUY":
            buy_logit += 0.20
        elif base_action == "SELL":
            sell_logit += 0.20
        else:
            hold_logit += 0.18

        raw_logits = {"BUY": buy_logit, "HOLD": hold_logit, "SELL": sell_logit}
        base_prior = self._softmax(raw_logits)

        buy_loss = self._clamp01(
            0.22
            + (uncertainty_penalty / 3.0) * 0.22
            + (drawdown_signal / 3.0) * 0.20
            + (left_tail_signal / 3.0) * 0.26
            + fast_drop_signal * 0.16
            - min(1.0, (upside_signal + spike_signal + long_tail_signal) / 6.0) * 0.20
        )
        sell_loss = self._clamp01(
            0.20
            + min(1.0, (upside_signal + spike_signal + sustain_signal + long_tail_signal) / 7.0)
            * 0.46
            + (positive_direction / 3.0) * 0.12
            - min(1.0, (drawdown_signal + left_tail_signal + linger_signal) / 6.0) * 0.18
        )
        hold_loss = self._clamp01(
            0.20
            + max(base_prior["BUY"], base_prior["SELL"]) * 0.42
            + self._clip(abs(buy_logit - sell_logit) / 3.5, 0.0, 1.0) * 0.18
            - (uncertainty_penalty / 3.0) * 0.16
        )

        losses = {"BUY": buy_loss, "HOLD": hold_loss, "SELL": sell_loss}
        mean_loss = sum(losses.values()) / 3.0
        adversarial_prior_raw = {
            label: max(0.05, base_prior[label] + ascent_step * (losses[label] - mean_loss))
            for label in raw_logits
        }
        total_prior = sum(adversarial_prior_raw.values()) or 1e-12
        adversarial_prior = {
            label: value / total_prior for label, value in adversarial_prior_raw.items()
        }

        robust_logits = {
            label: raw_logits[label]
            + math.log(max(base_prior[label], 1e-9))
            - logit_adjustment_strength * math.log(max(adversarial_prior[label], 1e-9))
            for label in raw_logits
        }
        robust_posterior = self._softmax(robust_logits)
        ordered_actions = sorted(
            robust_logits.items(),
            key=lambda item: item[1],
            reverse=True,
        )
        selected_action = ordered_actions[0][0]
        runner_up_logit = ordered_actions[1][1]
        robust_margin = ordered_actions[0][1] - runner_up_logit
        worst_class = max(losses.items(), key=lambda item: item[1])[0]
        worst_loss = losses[worst_class]
        adversarial_focus = max(adversarial_prior.items(), key=lambda item: item[1])[0]

        reasons: List[str] = [
            f"paper_logic=targeted_logit_adjustment_with_linear_ascent_prior",
            f"base_action={base_action}",
            f"tail_regime_label={tail_regime_label}",
            f"base_prior_buy={base_prior['BUY']:.4f}",
            f"base_prior_hold={base_prior['HOLD']:.4f}",
            f"base_prior_sell={base_prior['SELL']:.4f}",
            f"loss_buy={buy_loss:.4f}",
            f"loss_hold={hold_loss:.4f}",
            f"loss_sell={sell_loss:.4f}",
            f"adversarial_prior_buy={adversarial_prior['BUY']:.4f}",
            f"adversarial_prior_hold={adversarial_prior['HOLD']:.4f}",
            f"adversarial_prior_sell={adversarial_prior['SELL']:.4f}",
            f"worst_class={worst_class}",
            f"worst_loss={worst_loss:.4f}",
            f"adversarial_focus={adversarial_focus}",
            f"robust_buy_logit={robust_logits['BUY']:.4f}",
            f"robust_hold_logit={robust_logits['HOLD']:.4f}",
            f"robust_sell_logit={robust_logits['SELL']:.4f}",
            f"robust_margin={robust_margin:.4f}",
            f"linear_ascent_step={ascent_step:.3f}",
            f"logit_adjustment_strength={logit_adjustment_strength:.3f}",
        ]

        action = selected_action
        if action in {"BUY", "SELL"} and robust_margin < min_margin:
            action = "HOLD"
            reasons.append("robust class margin is too small; defaults to HOLD")
        elif action == "BUY" and worst_class == "BUY" and worst_loss >= 0.60:
            action = "HOLD"
            reasons.append("BUY is the current worst-case class under the adversarial prior")
        elif action == "SELL" and worst_class == "SELL" and worst_loss >= 0.60:
            action = "HOLD"
            reasons.append("SELL is the current worst-case class under the adversarial prior")
        else:
            reasons.append(f"robust minimax class selects {action}")

        confidence = self._clamp01(
            0.44
            + robust_posterior[selected_action] * 0.22
            + min(0.20, robust_margin * 0.24)
            + min(0.14, abs(worst_loss - mean_loss) * 0.22)
        )
        allow_execution = (
            action in {"BUY", "SELL"}
            and confidence >= 0.57
            and robust_margin >= min_margin * 0.75
            and worst_loss < 0.78
        )

        return AgentDecision(
            name=self.name,
            action=action,
            confidence=confidence,
            vote_value=ACTION_TO_VOTE.get(action, 0.0),
            allow_execution=allow_execution,
            reasons=reasons,
            local_metrics={
                "minimax_base_action": base_action,
                "minimax_selected_class": selected_action,
                "minimax_worst_class": worst_class,
                "minimax_adversarial_focus": adversarial_focus,
                "minimax_robust_margin": robust_margin,
                "minimax_worst_loss": worst_loss,
                "minimax_buy_loss": buy_loss,
                "minimax_hold_loss": hold_loss,
                "minimax_sell_loss": sell_loss,
                "minimax_base_prior_buy": base_prior["BUY"],
                "minimax_base_prior_hold": base_prior["HOLD"],
                "minimax_base_prior_sell": base_prior["SELL"],
                "minimax_adversarial_prior_buy": adversarial_prior["BUY"],
                "minimax_adversarial_prior_hold": adversarial_prior["HOLD"],
                "minimax_adversarial_prior_sell": adversarial_prior["SELL"],
                "minimax_robust_buy_logit": robust_logits["BUY"],
                "minimax_robust_hold_logit": robust_logits["HOLD"],
                "minimax_robust_sell_logit": robust_logits["SELL"],
                "minimax_buy_probability": robust_posterior["BUY"],
                "minimax_hold_probability": robust_posterior["HOLD"],
                "minimax_sell_probability": robust_posterior["SELL"],
                "minimax_linear_ascent_step": ascent_step,
                "minimax_logit_adjustment_strength": logit_adjustment_strength,
                "tail_long_score": tail_long_score,
                "tail_heavy_score": tail_heavy_score,
                "tail_left_risk_score": tail_left_risk_score,
                "tail_regime_label": tail_regime_label,
                "tail_skewness": features.get("tail_skewness"),
                "tail_excess_kurtosis": features.get("tail_excess_kurtosis"),
                "first_moment_pct_per_hour": features.get("first_moment_pct_per_hour"),
                "second_moment_pct_per_hour2": features.get("second_moment_pct_per_hour2"),
                "time_to_below_current_seconds": features.get("time_to_below_current_seconds"),
                "drawdown_linger_seconds": drawdown_linger_seconds,
                "spike_sustain_seconds": spike_sustain_seconds,
                "implied_upside": implied_upside,
            },
        )


class EMRegimeAgent(BaseWrapperAgent):
    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if math.isfinite(numeric) else None

    @staticmethod
    def _clip(value: float, floor: float, ceiling: float) -> float:
        return max(floor, min(ceiling, float(value)))

    def _signal(self, value: Optional[float], scale: float, cap: float = 3.0) -> float:
        if value is None:
            return 0.0
        return self._clip(value * scale, -cap, cap)

    def _implied_upside(self, features: Dict[str, Any]) -> Optional[float]:
        current_price = self._safe_float(features.get("current_price"))
        target_price = self._safe_float(features.get("target_price"))
        if current_price is None or target_price is None or current_price == 0:
            return None
        return (target_price / current_price) - 1.0

    def _build_observations(
        self, features: Dict[str, Any]
    ) -> tuple[List[tuple[str, float, float]], Optional[float]]:
        observations: List[tuple[str, float, float]] = []
        cadence_profile = str(features.get("cadence_profile", "intraday") or "intraday")
        unit_seconds = 3600.0 if cadence_profile == "intraday" else 86400.0
        medium_window_seconds = 6 * unit_seconds if cadence_profile == "intraday" else 5 * 86400.0
        long_window_seconds = 24 * unit_seconds if cadence_profile == "intraday" else 20 * 86400.0

        direction_strength = self._safe_float(features.get("direction_strength"))
        first_moment = self._safe_float(features.get("first_moment_pct_per_hour"))
        second_moment = self._safe_float(features.get("second_moment_pct_per_hour2"))
        avg_uncertainty = self._safe_float(features.get("avg_uncertainty_ratio"))
        implied_upside = self._implied_upside(features)

        if direction_strength is not None:
            observations.append(
                ("direction_strength", self._signal(direction_strength, 14.0), 1.25)
            )
        if first_moment is not None:
            observations.append(("first_moment", self._signal(first_moment, 9000.0), 1.10))
        if second_moment is not None:
            observations.append(("second_moment", self._signal(second_moment, 42000.0), 0.90))
        if implied_upside is not None:
            observations.append(("implied_upside", self._signal(implied_upside, 14.0), 1.00))
        if avg_uncertainty is not None:
            observations.append(
                (
                    "uncertainty_penalty",
                    -self._clip(avg_uncertainty * 25.0, 0.0, 3.0),
                    0.95,
                )
            )

        time_to_below = self._safe_float(features.get("time_to_below_current_seconds"))
        if time_to_below is not None:
            if time_to_below <= medium_window_seconds:
                downside_urgency = 1.0 - min(
                    1.0, time_to_below / max(medium_window_seconds, 1.0)
                )
                observations.append(
                    ("time_to_below", -(0.60 + downside_urgency * 2.20), 1.05)
                )
            elif time_to_below >= long_window_seconds:
                resilience = min(1.0, time_to_below / max(long_window_seconds * 2.0, 1.0))
                observations.append(
                    ("time_to_below_buffer", 0.30 + resilience * 0.80, 0.55)
                )

        drawdown_linger_seconds = self._safe_float(
            features.get("drawdown_linger_consensus_seconds")
        )
        if drawdown_linger_seconds is None:
            drawdown_linger_seconds = self._safe_float(features.get("drawdown_linger_seconds"))
        drawdown_recovery_in_horizon = features.get("drawdown_recovery_consensus_in_horizon")
        if drawdown_recovery_in_horizon is None:
            drawdown_recovery_in_horizon = features.get("drawdown_recovery_in_horizon")
        max_drawdown_pct = self._safe_float(features.get("max_drawdown_consensus_pct"))
        if max_drawdown_pct is None:
            max_drawdown_pct = self._safe_float(features.get("max_drawdown_pct"))

        drawdown_signal = 0.0
        if drawdown_linger_seconds is not None:
            drawdown_signal -= min(1.6, drawdown_linger_seconds / max(long_window_seconds, 1.0)) * 1.05
        if drawdown_recovery_in_horizon is False:
            drawdown_signal -= 0.95
        elif drawdown_recovery_in_horizon is True:
            drawdown_signal += 0.18
        if max_drawdown_pct is not None:
            drawdown_signal -= min(1.1, abs(max_drawdown_pct) * 5.5)
        if abs(drawdown_signal) >= 0.12:
            observations.append(("drawdown_profile", self._clip(drawdown_signal, -3.0, 3.0), 1.05))

        spike_sustain_seconds = self._safe_float(features.get("spike_sustain_consensus_seconds"))
        if spike_sustain_seconds is None:
            spike_sustain_seconds = self._safe_float(features.get("spike_sustain_seconds"))
        spike_fade_in_horizon = features.get("spike_fade_consensus_in_horizon")
        if spike_fade_in_horizon is None:
            spike_fade_in_horizon = features.get("spike_fade_in_horizon")
        max_spike_pct = self._safe_float(features.get("max_spike_consensus_pct"))
        if max_spike_pct is None:
            max_spike_pct = self._safe_float(features.get("max_spike_pct"))

        spike_signal = 0.0
        if spike_sustain_seconds is not None:
            spike_signal += min(1.6, spike_sustain_seconds / max(long_window_seconds, 1.0)) * 0.95
        if spike_fade_in_horizon is False:
            spike_signal += 0.82
        elif spike_fade_in_horizon is True:
            spike_signal -= 0.12
        if max_spike_pct is not None:
            spike_signal += min(1.05, max(0.0, max_spike_pct) * 5.0)
        if abs(spike_signal) >= 0.12:
            observations.append(("spike_profile", self._clip(spike_signal, -3.0, 3.0), 0.95))

        if bool(features.get("geodesic_available")):
            geodesic_bias = str(features.get("geodesic_action_bias") or "hold").lower()
            geodesic_continuation = self._safe_float(features.get("geodesic_continuation_score")) or 0.0
            geodesic_deviation = self._safe_float(features.get("geodesic_deviation_score")) or 0.0
            geodesic_regime_shift_risk = (
                self._safe_float(features.get("geodesic_regime_shift_risk")) or 0.0
            )
            geodesic_signal = 0.0
            if geodesic_bias == "buy":
                geodesic_signal += 0.95
            elif geodesic_bias == "sell":
                geodesic_signal -= 0.95
            geodesic_signal += geodesic_continuation * 1.55
            geodesic_signal -= geodesic_deviation * 1.10
            geodesic_signal -= geodesic_regime_shift_risk * 0.95
            observations.append(
                ("geodesic_regime", self._clip(geodesic_signal, -3.0, 3.0), 1.00)
            )

        base_action = str(features.get("final_action", "HOLD") or "HOLD").upper()
        if base_action == "BUY":
            observations.append(("baseline_action", 0.65, 0.45))
        elif base_action == "SELL":
            observations.append(("baseline_action", -0.65, 0.45))

        return observations, implied_upside

    @staticmethod
    def _gaussian_pdf(value: float, mean: float, variance: float) -> float:
        variance = max(0.10, float(variance))
        numerator = math.exp(-((value - mean) ** 2) / (2.0 * variance))
        denominator = math.sqrt(2.0 * math.pi * variance)
        return max(1e-12, numerator / max(denominator, 1e-12))

    def _fit_em(
        self, observations: List[tuple[str, float, float]]
    ) -> Dict[str, Any]:
        signals = [value for _, value, _ in observations]
        sample_weights = [max(0.25, weight) for _, _, weight in observations]
        total_weight = sum(sample_weights) or 1.0
        means = [-1.35, 0.0, 1.35]
        variances = [0.85, 0.42, 0.85]
        mixture_weights = [0.31, 0.38, 0.31]
        max_iterations = max(3, int(self.params.get("em_iterations", 7)))
        tolerance = max(1e-6, float(self.params.get("em_convergence_tol", 0.001)))
        responsibilities = [[1 / 3, 1 / 3, 1 / 3] for _ in signals]
        previous_log_likelihood: Optional[float] = None
        log_likelihood = 0.0
        completed_iterations = 0

        for iteration in range(max_iterations):
            completed_iterations = iteration + 1
            log_likelihood = 0.0

            for index, signal in enumerate(signals):
                components = [
                    mixture_weights[k] * self._gaussian_pdf(signal, means[k], variances[k])
                    for k in range(3)
                ]
                denominator = sum(components) or 1e-12
                responsibilities[index] = [component / denominator for component in components]
                log_likelihood += sample_weights[index] * math.log(denominator)

            if (
                previous_log_likelihood is not None
                and abs(log_likelihood - previous_log_likelihood) <= tolerance
            ):
                break
            previous_log_likelihood = log_likelihood

            for component_index in range(3):
                effective_count = sum(
                    sample_weights[index] * responsibilities[index][component_index]
                    for index in range(len(signals))
                )
                if effective_count <= 1e-9:
                    continue

                updated_mean = sum(
                    sample_weights[index]
                    * responsibilities[index][component_index]
                    * signals[index]
                    for index in range(len(signals))
                ) / effective_count
                updated_variance = sum(
                    sample_weights[index]
                    * responsibilities[index][component_index]
                    * ((signals[index] - updated_mean) ** 2)
                    for index in range(len(signals))
                ) / effective_count

                means[component_index] = self._clip(updated_mean, -3.5, 3.5)
                variances[component_index] = self._clip(updated_variance, 0.10, 4.0)
                mixture_weights[component_index] = max(0.05, effective_count / total_weight)

            weight_total = sum(mixture_weights) or 1.0
            mixture_weights = [weight / weight_total for weight in mixture_weights]

        component_order = sorted(range(3), key=lambda idx: means[idx])
        labels = ["bear", "neutral", "bull"]
        label_to_index = {
            labels[position]: component_order[position] for position in range(len(component_order))
        }

        regime_probabilities = {"bear": 0.0, "neutral": 0.0, "bull": 0.0}
        for sample_weight, sample_responsibilities in zip(sample_weights, responsibilities):
            for label, component_index in label_to_index.items():
                regime_probabilities[label] += (
                    sample_weight * sample_responsibilities[component_index]
                )

        for label in regime_probabilities:
            regime_probabilities[label] = regime_probabilities[label] / total_weight

        dominant_regime = max(regime_probabilities.items(), key=lambda item: item[1])[0]
        sorted_probs = sorted(regime_probabilities.values(), reverse=True)
        regime_gap = (
            sorted_probs[0] - sorted_probs[1] if len(sorted_probs) >= 2 else sorted_probs[0]
        )
        weighted_signal_mean = sum(
            sample_weight * signal for sample_weight, signal in zip(sample_weights, signals)
        ) / total_weight

        return {
            "iterations": completed_iterations,
            "log_likelihood": log_likelihood,
            "weighted_signal_mean": weighted_signal_mean,
            "dominant_regime": dominant_regime,
            "regime_gap": regime_gap,
            "probabilities": regime_probabilities,
            "means": {
                label: means[label_to_index[label]] for label in ("bull", "neutral", "bear")
            },
            "variances": {
                label: variances[label_to_index[label]]
                for label in ("bull", "neutral", "bear")
            },
            "weights": {
                label: mixture_weights[label_to_index[label]]
                for label in ("bull", "neutral", "bear")
            },
        }

    def decide(self, features: Dict[str, Any]) -> AgentDecision:
        base_action = str(features.get("final_action", "HOLD") or "HOLD").upper()
        observations, implied_upside = self._build_observations(features)
        reasons: List[str] = [
            f"base_action={base_action}",
            f"em_observation_count={len(observations)}",
        ]

        if len(observations) < 3:
            reasons.append("not enough regime observations; keeps baseline action")
            fallback_confidence = 0.56 if base_action != "HOLD" else 0.60
            return AgentDecision(
                name=self.name,
                action=base_action,
                confidence=fallback_confidence,
                vote_value=ACTION_TO_VOTE.get(base_action, 0.0),
                allow_execution=(base_action != "HOLD"),
                reasons=reasons,
                local_metrics={
                    "em_observation_count": len(observations),
                    "em_dominant_regime": "unresolved",
                    "implied_upside": implied_upside,
                },
            )

        evidence_summary = " | ".join(
            f"{label}={value:+.2f}" for label, value, _ in observations[:8]
        )
        fit = self._fit_em(observations)
        bull_probability = float(fit["probabilities"]["bull"])
        neutral_probability = float(fit["probabilities"]["neutral"])
        bear_probability = float(fit["probabilities"]["bear"])
        dominant_regime = str(fit["dominant_regime"])
        regime_gap = float(fit["regime_gap"])
        dominant_probability = max(bull_probability, neutral_probability, bear_probability)
        confidence = self._clamp01(0.48 + dominant_probability * 0.28 + regime_gap * 0.34)
        action = base_action

        reasons.extend(
            [
                f"em_iterations={fit['iterations']}",
                f"em_log_likelihood={fit['log_likelihood']:.4f}",
                f"em_weighted_signal_mean={fit['weighted_signal_mean']:.4f}",
                f"em_bull_probability={bull_probability:.4f}",
                f"em_neutral_probability={neutral_probability:.4f}",
                f"em_bear_probability={bear_probability:.4f}",
                f"em_dominant_regime={dominant_regime}",
                f"em_regime_gap={regime_gap:.4f}",
                f"em_evidence={evidence_summary}",
            ]
        )

        if base_action == "BUY":
            if bear_probability >= 0.44 and bear_probability >= bull_probability + 0.10:
                action = "HOLD"
                confidence = self._clamp01(confidence + 0.08)
                reasons.append("EM latent regime tilts bearish against BUY")
            elif dominant_regime == "neutral":
                action = "HOLD"
                confidence = self._clamp01(0.56 + neutral_probability * 0.20 + regime_gap * 0.16)
                reasons.append("EM mixture sees neutral dominance; BUY stays gated")
            else:
                action = "BUY"
                reasons.append("EM latent regime supports bullish continuation")
        elif base_action == "SELL":
            if bull_probability >= 0.44 and bull_probability >= bear_probability + 0.10:
                action = "HOLD"
                confidence = self._clamp01(confidence + 0.08)
                reasons.append("EM latent regime still leans bullish against SELL")
            elif dominant_regime == "neutral":
                action = "HOLD"
                confidence = self._clamp01(0.56 + neutral_probability * 0.20 + regime_gap * 0.16)
                reasons.append("EM mixture sees neutral dominance; SELL stays gated")
            else:
                action = "SELL"
                reasons.append("EM latent regime supports bearish continuation")
        else:
            if bull_probability >= 0.54 and regime_gap >= 0.08:
                action = "BUY"
                confidence = self._clamp01(confidence + 0.05)
                reasons.append("baseline HOLD tilts BUY because the EM bull regime dominates")
            elif bear_probability >= 0.54 and regime_gap >= 0.08:
                action = "SELL"
                confidence = self._clamp01(confidence + 0.05)
                reasons.append("baseline HOLD tilts SELL because the EM bear regime dominates")
            else:
                action = "HOLD"
                reasons.append("baseline HOLD remains appropriate under the EM regime mix")

        allow_execution = (
            action in {"BUY", "SELL"} and dominant_regime != "neutral" and regime_gap >= 0.05
        )

        return AgentDecision(
            name=self.name,
            action=action,
            confidence=self._clamp01(confidence),
            vote_value=ACTION_TO_VOTE.get(action, 0.0),
            allow_execution=allow_execution,
            reasons=reasons,
            local_metrics={
                "em_observation_count": len(observations),
                "em_iterations": fit["iterations"],
                "em_log_likelihood": fit["log_likelihood"],
                "em_weighted_signal_mean": fit["weighted_signal_mean"],
                "em_dominant_regime": dominant_regime,
                "em_dominant_probability": dominant_probability,
                "em_regime_gap": regime_gap,
                "em_bull_probability": bull_probability,
                "em_neutral_probability": neutral_probability,
                "em_bear_probability": bear_probability,
                "em_bull_component_mean": fit["means"]["bull"],
                "em_neutral_component_mean": fit["means"]["neutral"],
                "em_bear_component_mean": fit["means"]["bear"],
                "em_bull_component_variance": fit["variances"]["bull"],
                "em_neutral_component_variance": fit["variances"]["neutral"],
                "em_bear_component_variance": fit["variances"]["bear"],
                "em_bull_component_weight": fit["weights"]["bull"],
                "em_neutral_component_weight": fit["weights"]["neutral"],
                "em_bear_component_weight": fit["weights"]["bear"],
                "implied_upside": implied_upside,
                "first_moment_pct_per_hour": features.get("first_moment_pct_per_hour"),
                "second_moment_pct_per_hour2": features.get("second_moment_pct_per_hour2"),
                "time_to_below_current_seconds": features.get("time_to_below_current_seconds"),
                "drawdown_linger_seconds": features.get("drawdown_linger_consensus_seconds")
                or features.get("drawdown_linger_seconds"),
                "spike_sustain_seconds": features.get("spike_sustain_consensus_seconds")
                or features.get("spike_sustain_seconds"),
                "geodesic_action_bias": features.get("geodesic_action_bias"),
            },
        )


class DrawdownLingerAgent(BaseWrapperAgent):
    def decide(self, features: Dict[str, Any]) -> AgentDecision:
        base_action = str(features.get("final_action", "HOLD"))
        prophet_drawdown_linger_seconds = features.get("drawdown_linger_seconds")
        drawdown_linger_seconds = features.get("drawdown_linger_consensus_seconds")
        if drawdown_linger_seconds is None:
            drawdown_linger_seconds = prophet_drawdown_linger_seconds
        drawdown_recovery_in_horizon = features.get("drawdown_recovery_consensus_in_horizon")
        if drawdown_recovery_in_horizon is None:
            drawdown_recovery_in_horizon = features.get("drawdown_recovery_in_horizon")
        drawdown_start_timestamp = features.get("drawdown_start_timestamp")
        drawdown_recovery_timestamp = features.get("drawdown_recovery_timestamp")
        drawdown_trough_timestamp = features.get("drawdown_trough_timestamp")
        trough_to_recovery_seconds = features.get("trough_to_recovery_consensus_seconds")
        if trough_to_recovery_seconds is None:
            trough_to_recovery_seconds = features.get("trough_to_recovery_seconds")
        max_drawdown_pct = features.get("max_drawdown_consensus_pct")
        if max_drawdown_pct is None:
            max_drawdown_pct = features.get("max_drawdown_pct")
        cadence_profile = str(features.get("cadence_profile", "intraday") or "intraday")
        timesfm_drawdown_linger_seconds = features.get("timesfm_drawdown_linger_seconds")
        timesfm_recovery_in_horizon = features.get("timesfm_drawdown_recovery_in_horizon")
        timesfm_max_drawdown_pct = features.get("timesfm_max_drawdown_pct")
        drawdown_consensus_source = features.get("drawdown_consensus_source")
        timesfm_status = features.get("timesfm_status")

        medium_linger_seconds = 12 * 3600.0 if cadence_profile == "intraday" else 5 * 86400.0
        long_linger_seconds = 48 * 3600.0 if cadence_profile == "intraday" else 20 * 86400.0

        reasons: List[str] = [
            f"prophet_drawdown_linger_seconds={prophet_drawdown_linger_seconds}",
            f"drawdown_linger_consensus_seconds={drawdown_linger_seconds}",
            f"drawdown_recovery_in_horizon={drawdown_recovery_in_horizon}",
            f"drawdown_start_timestamp={drawdown_start_timestamp}",
            f"drawdown_recovery_timestamp={drawdown_recovery_timestamp}",
            f"drawdown_trough_timestamp={drawdown_trough_timestamp}",
            f"trough_to_recovery_seconds={trough_to_recovery_seconds}",
            f"max_drawdown_pct={max_drawdown_pct}",
            f"cadence_profile={cadence_profile}",
            f"timesfm_status={timesfm_status}",
            f"timesfm_drawdown_linger_seconds={timesfm_drawdown_linger_seconds}",
            f"timesfm_drawdown_recovery_in_horizon={timesfm_recovery_in_horizon}",
            f"timesfm_max_drawdown_pct={timesfm_max_drawdown_pct}",
            f"drawdown_consensus_source={drawdown_consensus_source}",
        ]

        action = base_action
        confidence = 0.55

        if drawdown_linger_seconds is None:
            reasons.append("no persistent drawdown period found; keeps baseline action")
            if base_action == "BUY":
                confidence = 0.62
            elif base_action == "SELL":
                confidence = 0.58
            else:
                confidence = 0.55
        else:
            linger_seconds = float(drawdown_linger_seconds)
            recovery_missing = drawdown_recovery_in_horizon is False

            if base_action == "BUY":
                if recovery_missing and linger_seconds >= medium_linger_seconds:
                    action = "HOLD"
                    confidence = 0.90
                    reasons.append("drawdown does not recover inside horizon; blocks BUY")
                elif linger_seconds >= long_linger_seconds:
                    action = "HOLD"
                    confidence = 0.82
                    reasons.append("drawdown lingers for a long regime window; softens BUY")
                elif linger_seconds >= medium_linger_seconds:
                    action = "HOLD"
                    confidence = 0.68
                    reasons.append("drawdown linger is moderate; prefers waiting")
                else:
                    action = "BUY"
                    confidence = 0.66
                    reasons.append("drawdown linger is short enough to keep BUY")
            elif base_action == "SELL":
                if recovery_missing or linger_seconds >= medium_linger_seconds:
                    action = "SELL"
                    confidence = 0.78
                    reasons.append("persistent drawdown supports SELL")
                else:
                    action = "HOLD"
                    confidence = 0.58
                    reasons.append("drawdown does not persist long enough to reinforce SELL")
            else:
                action = "HOLD"
                confidence = 0.62
                reasons.append("baseline HOLD remains appropriate")

        return AgentDecision(
            name=self.name,
            action=action,
            confidence=confidence,
            vote_value=ACTION_TO_VOTE.get(action, 0.0),
            allow_execution=(action != "HOLD"),
            reasons=reasons,
            local_metrics={
                "drawdown_linger_seconds": drawdown_linger_seconds,
                "prophet_drawdown_linger_seconds": prophet_drawdown_linger_seconds,
                "drawdown_recovery_in_horizon": drawdown_recovery_in_horizon,
                "drawdown_start_timestamp": drawdown_start_timestamp,
                "drawdown_recovery_timestamp": drawdown_recovery_timestamp,
                "drawdown_trough_timestamp": drawdown_trough_timestamp,
                "trough_to_recovery_seconds": trough_to_recovery_seconds,
                "max_drawdown_pct": max_drawdown_pct,
                "timesfm_status": timesfm_status,
                "timesfm_drawdown_linger_seconds": timesfm_drawdown_linger_seconds,
                "timesfm_drawdown_recovery_in_horizon": timesfm_recovery_in_horizon,
                "timesfm_max_drawdown_pct": timesfm_max_drawdown_pct,
                "drawdown_consensus_source": drawdown_consensus_source,
                "time_to_below_current_seconds": features.get("time_to_below_current_seconds"),
                "first_moment_pct_per_hour": features.get("first_moment_pct_per_hour"),
                "second_moment_pct_per_hour2": features.get("second_moment_pct_per_hour2"),
                "time_to_optimal_buy_seconds": features.get("time_to_optimal_buy_seconds"),
                "time_to_optimal_sell_seconds": features.get("time_to_optimal_sell_seconds"),
            },
        )


class SpikeSustainAgent(BaseWrapperAgent):
    def decide(self, features: Dict[str, Any]) -> AgentDecision:
        base_action = str(features.get("final_action", "HOLD"))
        prophet_spike_sustain_seconds = features.get("spike_sustain_seconds")
        spike_sustain_seconds = features.get("spike_sustain_consensus_seconds")
        if spike_sustain_seconds is None:
            spike_sustain_seconds = prophet_spike_sustain_seconds
        spike_fade_in_horizon = features.get("spike_fade_consensus_in_horizon")
        if spike_fade_in_horizon is None:
            spike_fade_in_horizon = features.get("spike_fade_in_horizon")
        spike_start_timestamp = features.get("spike_start_timestamp")
        spike_peak_timestamp = features.get("spike_peak_timestamp")
        spike_fade_timestamp = features.get("spike_fade_timestamp")
        peak_to_fade_seconds = features.get("peak_to_fade_consensus_seconds")
        if peak_to_fade_seconds is None:
            peak_to_fade_seconds = features.get("peak_to_fade_seconds")
        max_spike_pct = features.get("max_spike_consensus_pct")
        if max_spike_pct is None:
            max_spike_pct = features.get("max_spike_pct")
        cadence_profile = str(features.get("cadence_profile", "intraday") or "intraday")
        timesfm_spike_sustain_seconds = features.get("timesfm_spike_sustain_seconds")
        timesfm_spike_fade_in_horizon = features.get("timesfm_spike_fade_in_horizon")
        timesfm_max_spike_pct = features.get("timesfm_max_spike_pct")
        spike_consensus_source = features.get("spike_consensus_source")
        prophet_spike_weight = features.get("prophet_spike_weight")
        timesfm_spike_weight = features.get("timesfm_spike_weight")

        medium_sustain_seconds = 6 * 3600.0 if cadence_profile == "intraday" else 5 * 86400.0
        long_sustain_seconds = 24 * 3600.0 if cadence_profile == "intraday" else 20 * 86400.0

        reasons: List[str] = [
            f"prophet_spike_sustain_seconds={prophet_spike_sustain_seconds}",
            f"spike_sustain_consensus_seconds={spike_sustain_seconds}",
            f"spike_fade_in_horizon={spike_fade_in_horizon}",
            f"spike_start_timestamp={spike_start_timestamp}",
            f"spike_peak_timestamp={spike_peak_timestamp}",
            f"spike_fade_timestamp={spike_fade_timestamp}",
            f"peak_to_fade_seconds={peak_to_fade_seconds}",
            f"max_spike_pct={max_spike_pct}",
            f"cadence_profile={cadence_profile}",
            f"timesfm_spike_sustain_seconds={timesfm_spike_sustain_seconds}",
            f"timesfm_spike_fade_in_horizon={timesfm_spike_fade_in_horizon}",
            f"timesfm_max_spike_pct={timesfm_max_spike_pct}",
            f"spike_consensus_source={spike_consensus_source}",
            f"prophet_spike_weight={prophet_spike_weight}",
            f"timesfm_spike_weight={timesfm_spike_weight}",
        ]

        action = base_action
        confidence = 0.56

        if spike_sustain_seconds is None:
            reasons.append("no persistent upside spike found; keeps baseline action")
            if base_action == "BUY":
                confidence = 0.60
            elif base_action == "SELL":
                confidence = 0.58
        else:
            sustain_seconds = float(spike_sustain_seconds)
            fade_missing = spike_fade_in_horizon is False

            if base_action == "BUY":
                if sustain_seconds >= long_sustain_seconds or fade_missing:
                    action = "BUY"
                    confidence = 0.84
                    reasons.append("upside spike persists across a long window; strengthens BUY")
                elif sustain_seconds >= medium_sustain_seconds:
                    action = "BUY"
                    confidence = 0.70
                    reasons.append("upside spike persists long enough to keep BUY")
                else:
                    action = "HOLD"
                    confidence = 0.66
                    reasons.append("upside spike fades too quickly; softens BUY")
            elif base_action == "SELL":
                if sustain_seconds >= medium_sustain_seconds and not spike_fade_in_horizon:
                    action = "HOLD"
                    confidence = 0.72
                    reasons.append("upside spike still persists; softens SELL")
                else:
                    action = "SELL"
                    confidence = 0.68
                    reasons.append("upside spike likely fades inside horizon; keeps SELL")
            else:
                if sustain_seconds >= medium_sustain_seconds:
                    action = "BUY"
                    confidence = 0.62
                    reasons.append("baseline HOLD tilts positive because upside spike persists")
                else:
                    action = "HOLD"
                    confidence = 0.58
                    reasons.append("baseline HOLD remains appropriate")

        return AgentDecision(
            name=self.name,
            action=action,
            confidence=confidence,
            vote_value=ACTION_TO_VOTE.get(action, 0.0),
            allow_execution=(action != "HOLD"),
            reasons=reasons,
            local_metrics={
                "spike_sustain_seconds": spike_sustain_seconds,
                "prophet_spike_sustain_seconds": prophet_spike_sustain_seconds,
                "spike_fade_in_horizon": spike_fade_in_horizon,
                "spike_start_timestamp": spike_start_timestamp,
                "spike_peak_timestamp": spike_peak_timestamp,
                "spike_fade_timestamp": spike_fade_timestamp,
                "peak_to_fade_seconds": peak_to_fade_seconds,
                "max_spike_pct": max_spike_pct,
                "timesfm_spike_sustain_seconds": timesfm_spike_sustain_seconds,
                "timesfm_spike_fade_in_horizon": timesfm_spike_fade_in_horizon,
                "timesfm_max_spike_pct": timesfm_max_spike_pct,
                "spike_consensus_source": spike_consensus_source,
                "prophet_spike_weight": prophet_spike_weight,
                "timesfm_spike_weight": timesfm_spike_weight,
                "time_to_optimal_buy_seconds": features.get("time_to_optimal_buy_seconds"),
                "time_to_optimal_sell_seconds": features.get("time_to_optimal_sell_seconds"),
                "rise_window_seconds": features.get("rise_window_seconds"),
                "drop_window_seconds": features.get("drop_window_seconds"),
            },
        )


class RegretAgent(BaseWrapperAgent):
    def decide(self, features: Dict[str, Any]) -> AgentDecision:
        base_action = str(features.get("final_action", "HOLD"))
        avg_uncertainty = float(features.get("avg_uncertainty_ratio", 0.0) or 0.0)
        time_to_below = features.get("time_to_below_current_seconds")
        drawdown_linger_seconds = features.get("drawdown_linger_consensus_seconds")
        if drawdown_linger_seconds is None:
            drawdown_linger_seconds = features.get("drawdown_linger_seconds")
        drawdown_recovery_in_horizon = features.get("drawdown_recovery_consensus_in_horizon")
        if drawdown_recovery_in_horizon is None:
            drawdown_recovery_in_horizon = features.get("drawdown_recovery_in_horizon")
        max_drawdown_pct = features.get("max_drawdown_consensus_pct")
        if max_drawdown_pct is None:
            max_drawdown_pct = features.get("max_drawdown_pct")
        spike_sustain_seconds = features.get("spike_sustain_consensus_seconds")
        if spike_sustain_seconds is None:
            spike_sustain_seconds = features.get("spike_sustain_seconds")
        spike_fade_in_horizon = features.get("spike_fade_consensus_in_horizon")
        if spike_fade_in_horizon is None:
            spike_fade_in_horizon = features.get("spike_fade_in_horizon")
        max_spike_pct = features.get("max_spike_consensus_pct")
        if max_spike_pct is None:
            max_spike_pct = features.get("max_spike_pct")
        time_to_optimal_buy_seconds = features.get("time_to_optimal_buy_seconds")
        time_to_optimal_sell_seconds = features.get("time_to_optimal_sell_seconds")
        cadence_profile = str(features.get("cadence_profile", "intraday") or "intraday")
        current_price = features.get("current_price")
        target_price = features.get("target_price")

        unit_seconds = 3600.0 if cadence_profile == "intraday" else 86400.0
        quick_regret_seconds = 6 * unit_seconds
        heavy_regret_seconds = 24 * unit_seconds

        implied_upside = None
        if current_price is not None and target_price is not None and float(current_price) != 0:
            implied_upside = (float(target_price) / float(current_price)) - 1.0

        buy_regret_score = 0.0
        sell_regret_score = 0.0

        if time_to_below is not None:
            ttb = float(time_to_below)
            if ttb <= quick_regret_seconds:
                buy_regret_score += 0.32
            elif ttb <= heavy_regret_seconds:
                buy_regret_score += 0.18

        if drawdown_linger_seconds is not None:
            linger = float(drawdown_linger_seconds)
            if linger >= heavy_regret_seconds:
                buy_regret_score += 0.26
            elif linger >= quick_regret_seconds:
                buy_regret_score += 0.14

        if drawdown_recovery_in_horizon is False:
            buy_regret_score += 0.18

        if max_drawdown_pct is not None:
            buy_regret_score += min(0.18, abs(float(max_drawdown_pct)) * 1.6)

        buy_regret_score += min(0.24, avg_uncertainty * 4.5)

        if implied_upside is not None and implied_upside <= 0:
            buy_regret_score += 0.14

        if spike_sustain_seconds is not None:
            sustain = float(spike_sustain_seconds)
            if sustain >= heavy_regret_seconds:
                sell_regret_score += 0.28
            elif sustain >= quick_regret_seconds:
                sell_regret_score += 0.16

        if spike_fade_in_horizon is False:
            sell_regret_score += 0.18

        if max_spike_pct is not None:
            sell_regret_score += min(0.20, max(0.0, float(max_spike_pct)) * 1.8)

        if implied_upside is not None and implied_upside > 0:
            sell_regret_score += min(0.18, implied_upside * 1.5)

        if time_to_optimal_sell_seconds is not None:
            sell_eta = float(time_to_optimal_sell_seconds)
            if sell_eta <= quick_regret_seconds:
                sell_regret_score += 0.10

        if time_to_optimal_buy_seconds is not None:
            buy_eta = float(time_to_optimal_buy_seconds)
            if buy_eta <= quick_regret_seconds:
                buy_regret_score += 0.08

        buy_regret_score = self._clamp01(buy_regret_score)
        sell_regret_score = self._clamp01(sell_regret_score)
        regret_risk_score = self._clamp01(max(buy_regret_score, sell_regret_score))
        regret_bias = (
            "buy_regret"
            if buy_regret_score > sell_regret_score + 0.08
            else "sell_regret"
            if sell_regret_score > buy_regret_score + 0.08
            else "balanced"
        )

        reasons: List[str] = [
            f"avg_uncertainty_ratio={avg_uncertainty}",
            f"time_to_below_current_seconds={time_to_below}",
            f"drawdown_linger_seconds={drawdown_linger_seconds}",
            f"drawdown_recovery_in_horizon={drawdown_recovery_in_horizon}",
            f"max_drawdown_pct={max_drawdown_pct}",
            f"spike_sustain_seconds={spike_sustain_seconds}",
            f"spike_fade_in_horizon={spike_fade_in_horizon}",
            f"max_spike_pct={max_spike_pct}",
            f"time_to_optimal_buy_seconds={time_to_optimal_buy_seconds}",
            f"time_to_optimal_sell_seconds={time_to_optimal_sell_seconds}",
            f"implied_upside={implied_upside}",
            f"buy_regret_score={buy_regret_score:.4f}",
            f"sell_regret_score={sell_regret_score:.4f}",
            f"regret_bias={regret_bias}",
        ]

        action = base_action
        confidence = 0.58

        if base_action == "BUY":
            if buy_regret_score >= 0.62:
                action = "HOLD"
                confidence = min(0.92, 0.58 + buy_regret_score * 0.42)
                reasons.append("buy regret risk is elevated; softens BUY to HOLD")
            else:
                action = "BUY"
                confidence = 0.55 + max(0.0, 0.35 - buy_regret_score * 0.25)
                reasons.append("buy regret risk stays acceptable; keeps BUY")
        elif base_action == "SELL":
            if sell_regret_score >= 0.58:
                action = "HOLD"
                confidence = min(0.90, 0.56 + sell_regret_score * 0.40)
                reasons.append("sell regret risk is elevated; softens SELL to HOLD")
            else:
                action = "SELL"
                confidence = 0.56 + max(0.0, 0.34 - sell_regret_score * 0.22)
                reasons.append("sell regret risk stays acceptable; keeps SELL")
        else:
            action = "HOLD"
            confidence = 0.55 + abs(buy_regret_score - sell_regret_score) * 0.20
            reasons.append("baseline HOLD remains appropriate while regret balance is unresolved")

        return AgentDecision(
            name=self.name,
            action=action,
            confidence=self._clamp01(confidence),
            vote_value=ACTION_TO_VOTE.get(action, 0.0),
            allow_execution=(action != "HOLD"),
            reasons=reasons,
            local_metrics={
                "regret_risk_score": regret_risk_score,
                "regret_bias": regret_bias,
                "buy_regret_score": buy_regret_score,
                "sell_regret_score": sell_regret_score,
                "avg_uncertainty_ratio": avg_uncertainty,
                "time_to_below_current_seconds": time_to_below,
                "drawdown_linger_seconds": drawdown_linger_seconds,
                "drawdown_recovery_in_horizon": drawdown_recovery_in_horizon,
                "max_drawdown_pct": max_drawdown_pct,
                "spike_sustain_seconds": spike_sustain_seconds,
                "spike_fade_in_horizon": spike_fade_in_horizon,
                "max_spike_pct": max_spike_pct,
                "time_to_optimal_buy_seconds": time_to_optimal_buy_seconds,
                "time_to_optimal_sell_seconds": time_to_optimal_sell_seconds,
                "implied_upside": implied_upside,
            },
        )


class ConservativeGoldAgent(BaseWrapperAgent):
    def decide(self, features: Dict[str, Any]) -> AgentDecision:
        base_action = str(features.get("final_action", "HOLD"))
        current_price = features.get("current_price")
        target_price = features.get("target_price")
        avg_uncertainty = float(features.get("avg_uncertainty_ratio", 1.0) or 1.0)
        direction_strength = float(features.get("direction_strength", 0.0) or 0.0)
        first_moment = features.get("first_moment_pct_per_hour")
        second_moment = features.get("second_moment_pct_per_hour2")
        rise_window_seconds = features.get("rise_window_seconds")
        drop_window_seconds = features.get("drop_window_seconds")
        max_uncertainty = float(self.params.get("max_uncertainty_for_buy", 0.03))

        reasons: List[str] = [
            f"current_price={current_price}",
            f"target_price={target_price}",
            f"avg_uncertainty_ratio={avg_uncertainty}",
            f"direction_strength={direction_strength}",
            f"first_moment_pct_per_hour={first_moment}",
            f"second_moment_pct_per_hour2={second_moment}",
            f"rise_window_seconds={rise_window_seconds}",
            f"drop_window_seconds={drop_window_seconds}",
        ]

        action = base_action
        confidence = 0.50
        upside = None
        if current_price is not None and target_price is not None and float(current_price) != 0:
            upside = (float(target_price) / float(current_price)) - 1.0
            reasons.append(f"implied_upside={upside:.6f}")

        if base_action == "BUY":
            if avg_uncertainty > max_uncertainty:
                action = "HOLD"
                confidence = 0.90
                reasons.append("uncertainty too high for conservative BUY")
            elif upside is not None and upside <= 0:
                action = "HOLD"
                confidence = 0.80
                reasons.append("target_price not above current_price")
            else:
                action = "BUY"
                confidence = 0.70
                reasons.append("conservative checks pass")
        elif base_action == "SELL":
            if direction_strength < 0 and avg_uncertainty <= max_uncertainty * 1.5:
                action = "SELL"
                confidence = 0.75
                reasons.append("negative strength with acceptable uncertainty")
            else:
                action = "HOLD"
                confidence = 0.60
                reasons.append("conservative filter softens SELL")
        else:
            action = "HOLD"
            confidence = 0.60
            reasons.append("baseline HOLD")

        return AgentDecision(
            name=self.name,
            action=action,
            confidence=confidence,
            vote_value=ACTION_TO_VOTE.get(action, 0.0),
            allow_execution=(action != "HOLD"),
            reasons=reasons,
            local_metrics={
                "avg_uncertainty_ratio": avg_uncertainty,
                "current_price": current_price,
                "target_price": target_price,
                "implied_upside": upside,
                "first_moment_pct_per_hour": first_moment,
                "second_moment_pct_per_hour2": second_moment,
                "time_to_optimal_buy_seconds": features.get("time_to_optimal_buy_seconds"),
                "time_to_optimal_sell_seconds": features.get("time_to_optimal_sell_seconds"),
                "rise_window_seconds": rise_window_seconds,
                "drop_window_seconds": drop_window_seconds,
                "optimal_buy_timestamp": features.get("optimal_buy_timestamp"),
                "optimal_buy_price": features.get("optimal_buy_price"),
                "optimal_sell_timestamp": features.get("optimal_sell_timestamp"),
                "optimal_sell_price": features.get("optimal_sell_price"),
            },
        )


class ExecutionCostAwareAgent(BaseWrapperAgent):
    def decide(self, features: Dict[str, Any]) -> AgentDecision:
        base_action = str(features.get("final_action", "HOLD"))
        slippage_bps = float(features.get("observed_slippage_bps", 0.0) or 0.0)
        price_impact_pct = float(features.get("observed_price_impact_pct", 0.0) or 0.0)
        max_slippage_bps = float(self.params.get("max_slippage_bps", 50.0))
        max_price_impact_pct = float(self.params.get("max_price_impact_pct", 0.30))
        rise_window_seconds = features.get("rise_window_seconds")
        drop_window_seconds = features.get("drop_window_seconds")

        reasons = [
            f"observed_slippage_bps={slippage_bps}",
            f"observed_price_impact_pct={price_impact_pct}",
            f"first_moment_pct_per_hour={features.get('first_moment_pct_per_hour')}",
            f"second_moment_pct_per_hour2={features.get('second_moment_pct_per_hour2')}",
            f"rise_window_seconds={rise_window_seconds}",
            f"drop_window_seconds={drop_window_seconds}",
        ]

        if base_action == "HOLD":
            return AgentDecision(
                name=self.name,
                action="HOLD",
                confidence=0.60,
                vote_value=0.0,
                allow_execution=False,
                reasons=reasons + ["baseline HOLD"],
                local_metrics={
                    "slippage_bps": slippage_bps,
                    "price_impact_pct": price_impact_pct,
                    "first_moment_pct_per_hour": features.get("first_moment_pct_per_hour"),
                    "second_moment_pct_per_hour2": features.get("second_moment_pct_per_hour2"),
                    "rise_window_seconds": rise_window_seconds,
                    "drop_window_seconds": drop_window_seconds,
                    "optimal_sell_timestamp": features.get("optimal_sell_timestamp"),
                },
            )

        if slippage_bps > max_slippage_bps or price_impact_pct > max_price_impact_pct:
            reasons.append("execution cost too high; blocks trade")
            return AgentDecision(
                name=self.name,
                action="HOLD",
                confidence=0.90,
                vote_value=0.0,
                allow_execution=False,
                reasons=reasons,
                local_metrics={
                    "slippage_bps": slippage_bps,
                    "price_impact_pct": price_impact_pct,
                    "first_moment_pct_per_hour": features.get("first_moment_pct_per_hour"),
                    "second_moment_pct_per_hour2": features.get("second_moment_pct_per_hour2"),
                    "rise_window_seconds": rise_window_seconds,
                    "drop_window_seconds": drop_window_seconds,
                    "optimal_sell_timestamp": features.get("optimal_sell_timestamp"),
                },
            )

        reasons.append("execution cost within limits")
        return AgentDecision(
            name=self.name,
            action=base_action,
            confidence=0.80,
            vote_value=ACTION_TO_VOTE.get(base_action, 0.0),
            allow_execution=True,
            reasons=reasons,
            local_metrics={
                "slippage_bps": slippage_bps,
                "price_impact_pct": price_impact_pct,
                "first_moment_pct_per_hour": features.get("first_moment_pct_per_hour"),
                "second_moment_pct_per_hour2": features.get("second_moment_pct_per_hour2"),
                "rise_window_seconds": rise_window_seconds,
                "drop_window_seconds": drop_window_seconds,
                "optimal_sell_timestamp": features.get("optimal_sell_timestamp"),
            },
        )


def build_wrapper_agent(kind: str, name: str, params: Optional[Dict[str, float]] = None) -> BaseWrapperAgent:
    if kind == "final_action":
        return FinalActionAgent(name, params)
    if kind == "time_to_below":
        return TimeToBelowAgent(name, params)
    if kind == "geodesic":
        return GeodesicAgent(name, params)
    if kind == "minimax_prior":
        return MinimaxPriorAgent(name, params)
    if kind == "em_regime":
        return EMRegimeAgent(name, params)
    if kind == "spike_sustain":
        return SpikeSustainAgent(name, params)
    if kind == "drawdown_linger":
        return DrawdownLingerAgent(name, params)
    if kind == "regret":
        return RegretAgent(name, params)
    if kind == "conservative_gold":
        return ConservativeGoldAgent(name, params)
    if kind == "execution_cost":
        return ExecutionCostAwareAgent(name, params)
    raise ValueError(f"Unknown wrapper agent kind: {kind}")
