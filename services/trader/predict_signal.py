#!/usr/bin/env python3

import warnings
warnings.filterwarnings("ignore")

import logging
logging.getLogger("prophet").setLevel(logging.CRITICAL)
logging.getLogger("prophet.plot").setLevel(logging.CRITICAL)
logging.getLogger("cmdstanpy").setLevel(logging.CRITICAL)

import argparse
import contextlib
import io
import json
import logging
import os
import re
import sys
from pathlib import Path

import pandas as pd

logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data" / "historical"
MPLCONFIGDIR = Path(os.environ.get("MPLCONFIGDIR", "/tmp/no-slip-matplotlib"))

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from services.llm import WrapperConfig, run_wrapper_pipeline
    from services.llm.weight_store import (
        load_wrapper_weights,
        store_wrapper_prediction_snapshot,
    )
except ImportError:
    WrapperConfig = None
    run_wrapper_pipeline = None
    load_wrapper_weights = None
    store_wrapper_prediction_snapshot = None

from services.trader.pair_fallback import (
    build_pair_raw_df,
    parse_route_symbols,
)
from services.trader.map_store import today_market_date, today_market_timestamp_iso
from services.trader.human_bias import (
    load_symbol_interest_snapshot,
    record_symbol_interest,
)

LOCAL_DATASETS = {}

SYMBOL_ALIASES = {
    "SOL": "SOL",
    "ETH": "ETH",
    "WETH": "ETH",
    "BTC": "BTC",
    "WBTC": "BTC",
}
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._/\- ]{0,79}$")
ROUTE_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._/\- ]{0,31}$")


def parse_args():
    parser = argparse.ArgumentParser(description="Run no-slip signal inference")
    parser.add_argument("--symbol", required=True, help="Token symbol from the UI")
    parser.add_argument("--csv", help="Path to a custom CSV dataset")
    parser.add_argument(
        "--market-mode",
        choices=("crypto", "sp500"),
        help="Optional UI market mode hint so fallback data fetches the right asset class first.",
    )
    parser.add_argument(
        "--track-human-bias",
        action="store_true",
        help="Record this direct symbol request into the aggregate human-bias store.",
    )
    parser.add_argument(
        "--human-bias-source",
        default="predict_signal_cli",
        help="Short source label for aggregate symbol-attention tracking.",
    )
    return parser.parse_args()


def normalize_symbol(raw_symbol: str) -> str:
    normalized = " ".join((raw_symbol or "").strip().upper().split())
    if not normalized:
        return ""

    if SYMBOL_RE.fullmatch(normalized):
        return normalized

    input_symbol, output_symbol = parse_route_symbols(normalized)
    if (
        input_symbol
        and output_symbol
        and ROUTE_SYMBOL_RE.fullmatch(input_symbol)
        and ROUTE_SYMBOL_RE.fullmatch(output_symbol)
    ):
        return f"{input_symbol}→{output_symbol}"

    return ""


def format_rule_label(rule: str) -> str:
    normalized = str(rule or "").strip()
    if normalized.endswith("min"):
        return normalized.replace("min", "m")
    if normalized.endswith("D"):
        return normalized.lower()
    return normalized


def choose_source(symbol: str):
    input_symbol, output_symbol = parse_route_symbols(symbol)
    if input_symbol and output_symbol:
        return {
            "kind": "pair_fallback",
            "resolved_symbol": symbol,
            "dataset": None,
        }

    if symbol in LOCAL_DATASETS and LOCAL_DATASETS[symbol].exists():
        return {
            "kind": "local",
            "resolved_symbol": symbol,
            "dataset": str(LOCAL_DATASETS[symbol]),
        }

    return {
        "kind": "fallback",
        "resolved_symbol": SYMBOL_ALIASES.get(symbol, symbol),
        "dataset": None,
    }


def safe_float(value):
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def json_safe(value):
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(inner) for inner in value]
    return value


def serialize_direction_details(details):
    if not isinstance(details, pd.DataFrame) or details.empty:
        return []

    serialized = []
    for row in details.to_dict(orient="records"):
        serialized.append(
            {
                "agent": row.get("agent"),
                "action": row.get("action"),
                "score": safe_float(row.get("score")),
                "uncertaintyRatio": safe_float(row.get("uncertainty_ratio")),
                "weight": safe_float(row.get("weight")),
                "gold": safe_float(row.get("gold")),
            }
        )
    return serialized


def summarize_per_rule(per_rule):
    summary = {}

    for rule, payload in (per_rule or {}).items():
        direction = payload.get("direction") if isinstance(payload, dict) else None
        low_timing = payload.get("low_timing") if isinstance(payload, dict) else None
        high_timing = payload.get("high_timing") if isinstance(payload, dict) else None

        summary[rule] = {
            "ruleLabel": format_rule_label(rule),
            "direction": {
                "finalAction": direction.get("final_action") if isinstance(direction, dict) else None,
                "weightedScore": safe_float(direction.get("weighted_score")) if isinstance(direction, dict) else None,
                "currentPrice": safe_float(direction.get("current_price")) if isinstance(direction, dict) else None,
                "currentTimestamp": (
                    str(direction.get("current_timestamp"))
                    if isinstance(direction, dict) and direction.get("current_timestamp") is not None
                    else None
                ),
                "firstBelowCurrentTimestamp": (
                    str(direction.get("first_below_current_timestamp"))
                    if isinstance(direction, dict) and direction.get("first_below_current_timestamp") is not None
                    else None
                ),
                "timeToBelowCurrentSeconds": (
                    safe_float(direction.get("time_to_below_current_seconds"))
                    if isinstance(direction, dict)
                    else None
                ),
                "firstMomentPricePerHour": (
                    safe_float(direction.get("first_moment_price_per_hour"))
                    if isinstance(direction, dict)
                    else None
                ),
                "firstMomentPctPerHour": (
                    safe_float(direction.get("first_moment_pct_per_hour"))
                    if isinstance(direction, dict)
                    else None
                ),
                "secondMomentPricePerHour2": (
                    safe_float(direction.get("second_moment_price_per_hour2"))
                    if isinstance(direction, dict)
                    else None
                ),
                "secondMomentPctPerHour2": (
                    safe_float(direction.get("second_moment_pct_per_hour2"))
                    if isinstance(direction, dict)
                    else None
                ),
                "agents": serialize_direction_details(direction.get("details")) if isinstance(direction, dict) else [],
            },
            "lowTiming": {
                "predictedTimestamp": (
                    str(low_timing.get("predicted_timestamp"))
                    if isinstance(low_timing, dict) and low_timing.get("predicted_timestamp") is not None
                    else None
                ),
                "predictedPrice": safe_float(low_timing.get("predicted_price"))
                if isinstance(low_timing, dict)
                else None,
            },
            "highTiming": {
                "predictedTimestamp": (
                    str(high_timing.get("predicted_timestamp"))
                    if isinstance(high_timing, dict) and high_timing.get("predicted_timestamp") is not None
                    else None
                ),
                "predictedPrice": safe_float(high_timing.get("predicted_price"))
                if isinstance(high_timing, dict)
                else None,
            },
        }

    return summary


def generate_wrapper_graph_base64(wrapper_result) -> str:
    """Generate a premium dark-themed visualization of the Wrapper Council decisions and weights."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import io
    import base64
    
    # 1. Extrapolate values
    weighted_vote = safe_float(wrapper_result.get("wrapper_weighted_vote")) or 0.0
    consensus_pct = weighted_vote * 100.0
    
    weights = wrapper_result.get("wrapper_weights") or {}
    agent_outputs = wrapper_result.get("wrapper_agent_outputs") or []
    
    # 2. Styling matching the dark-theme UI mockup
    bg_color = "#121212"
    panel_color = "#1a1a1a"
    grid_color = "#2a2a2a"
    text_color = "#ffffff"
    sub_text_color = "#aaaaaa"
    border_color = "#333333"
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(6, 5), gridspec_kw={'height_ratios': [1.2, 2.5]})
    fig.patch.set_facecolor(bg_color)
    
    # ------------------ Ax1: Stance Gauge ------------------
    ax1.set_facecolor(panel_color)
    ax1.tick_params(left=False, labelleft=False, bottom=True, labelbottom=True, colors=sub_text_color, labelsize=8)
    for spine in ax1.spines.values():
        spine.set_color(border_color)
        
    ax1.set_xlim(-100, 100)
    ax1.set_ylim(-0.5, 0.5)
    ax1.set_title("Wrapper Council Consensus Index", color=text_color, fontsize=10, fontweight="bold", pad=6)
    
    # Background zones
    ax1.axvspan(-100, -15, color="#ef4444", alpha=0.15)
    ax1.axvspan(-15, 15, color="#888888", alpha=0.08)
    ax1.axvspan(15, 100, color="#10b981", alpha=0.15)
    ax1.axhline(0, color="#444444", linewidth=0.6, linestyle=":")
    
    final_action = wrapper_result.get("wrapper_final_action") or "HOLD"
    if final_action == "BUY":
        bar_color = "#10b981"
        stance_lbl = "BUY"
    elif final_action == "SELL":
        bar_color = "#ef4444"
        stance_lbl = "SELL"
    else:
        bar_color = "#f59e0b"
        stance_lbl = "HOLD"
        
    ax1.barh(0, consensus_pct, height=0.25, color=bar_color, edgecolor=border_color, zorder=3)
    ax1.axvline(consensus_pct, color="#ffffff", linewidth=2.0, linestyle="-", zorder=4)
    ax1.text(consensus_pct, 0.26, f"{consensus_pct:+.1f}% ({stance_lbl})", 
             color="#ffffff", fontsize=9, fontweight="bold", ha="center")
             
    ax1.text(-57.5, -0.35, "SELL ZONE", color="#ef4444", fontsize=8, fontweight="bold", ha="center")
    ax1.text(0, -0.35, "NEUTRAL ZONE", color=sub_text_color, fontsize=8, fontweight="bold", ha="center")
    ax1.text(57.5, -0.35, "BUY ZONE", color="#10b981", fontsize=8, fontweight="bold", ha="center")
    
    # ------------------ Ax2: Agent Breakdown ------------------
    ax2.set_facecolor(panel_color)
    ax2.tick_params(colors=sub_text_color, labelsize=8)
    for spine in ax2.spines.values():
        spine.set_color(border_color)
    ax2.grid(True, axis="x", color=grid_color, linestyle=":", linewidth=0.5, zorder=0)
    
    agent_names = []
    agent_weights = []
    agent_actions = []
    
    name_display = {
        "regret_agent": "Regret Agent",
        "em_regime_agent": "EM Regime Agent",
        "minimax_prior_agent": "Minimax Prior",
        "cost_agent": "Execution Cost",
        "drawdown_linger_agent": "Drawdown Linger",
        "spike_sustain_agent": "Spike Sustain"
    }
    
    for agent in agent_outputs:
        if not isinstance(agent, dict):
            continue
        name = agent.get("name")
        if not name:
            continue
        friendly_name = name_display.get(name, name.replace("_", " ").title())
        agent_names.append(friendly_name)
        
        weight = safe_float(weights.get(name)) or 0.0
        agent_weights.append(weight)
        
        agent_actions.append(agent.get("action") or "HOLD")
        
    if not agent_names:
        agent_names = ["No Agents"]
        agent_weights = [1.0]
        agent_actions = ["HOLD"]
        
    vote_colors = {
        "BUY": "#10b981",
        "SELL": "#ef4444",
        "HOLD": "#4b5563"
    }
    bar_colors = [vote_colors.get(v, "#4b5563") for v in agent_actions]
    
    y_pos = range(len(agent_names))
    bars = ax2.barh(y_pos, agent_weights, color=bar_colors, edgecolor=border_color, height=0.5, zorder=3)
    
    ax2.set_yticks(y_pos)
    ax2.set_yticklabels(agent_names, color=text_color, fontsize=8, fontweight="bold")
    ax2.set_xlabel("Agent Weights", color=sub_text_color, fontsize=8, labelpad=5)
    ax2.set_title("Council Agent Breakdown (Votes & Weights)", color=text_color, fontsize=10, fontweight="bold", pad=6)
    
    for bar, vote, weight in zip(bars, agent_actions, agent_weights):
        width = bar.get_width()
        lbl_x = width + 0.005
        ax2.text(lbl_x, bar.get_y() + bar.get_height()/2.0, f"{vote} ({weight*100.0:.1f}%)",
                 color="#ffffff", fontsize=8, fontweight="bold", va="center", ha="left")
                 
    ax2.set_xlim(0, max(agent_weights) + 0.08)
    
    plt.tight_layout()
    
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, facecolor=bg_color, bbox_inches="tight")
    buf.seek(0)
    img_bytes = buf.read()
    plt.close(fig)
    
    return base64.b64encode(img_bytes).decode("utf-8")


def serialize_wrapper_result(wrapper_result):
    if not isinstance(wrapper_result, dict):
        return None

    # Generate graph base64
    base64_graph = None
    try:
        base64_graph = generate_wrapper_graph_base64(wrapper_result)
    except Exception as e:
        # Fallback silently
        pass

    return {
        "finalAction": wrapper_result.get("wrapper_final_action"),
        "weightedVote": safe_float(wrapper_result.get("wrapper_weighted_vote")),
        "executionAllowed": bool(wrapper_result.get("wrapper_execution_allowed")),
        "yesExecutionVotes": int(wrapper_result.get("wrapper_yes_execution_votes") or 0),
        "weights": wrapper_result.get("wrapper_weights") or {},
        "weightSource": wrapper_result.get("wrapper_weight_source") or "default",
        "feedback": wrapper_result.get("wrapper_feedback"),
        "feedbackCount": int(wrapper_result.get("wrapper_feedback_count") or 0),
        "rationale": wrapper_result.get("wrapper_rationale") or [],
        "byzantine": json_safe(wrapper_result.get("wrapper_byzantine") or {}),
        "bagging": json_safe(wrapper_result.get("wrapper_bagging") or {}),
        "consensusGraphBase64": base64_graph,
        "agentOutputs": [
            {
                "name": agent_output.get("name"),
                "action": agent_output.get("action"),
                "confidence": safe_float(agent_output.get("confidence")),
                "voteValue": safe_float(agent_output.get("vote_value")),
                "allowExecution": bool(agent_output.get("allow_execution")),
                "reasons": agent_output.get("reasons") or [],
                "localMetrics": json_safe(agent_output.get("local_metrics") or {}),
            }
            for agent_output in (wrapper_result.get("wrapper_agent_outputs") or [])
            if isinstance(agent_output, dict)
        ],
    }


def main():
    args = parse_args()
    requested_symbol = normalize_symbol(args.symbol)

    if not requested_symbol:
        print(json.dumps({"supported": False, "reason": "Missing token symbol"}))
        return

    source = choose_source(requested_symbol)
    MPLCONFIGDIR.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("MPLCONFIGDIR", str(MPLCONFIGDIR))
    os.environ.setdefault("ALLOW_BOOTSTRAP_EXECUTION", "false")
    os.environ.setdefault("EXECUTE_TRADES", "false")
    os.environ.setdefault("WAIT_FOR_TARGET", "false")
    os.environ.setdefault("TARGET_COIN_SYMBOL", source["resolved_symbol"])
    os.environ.setdefault(
        "PRICE_HISTORY_CSV",
        source["dataset"] if source["dataset"] else f"{source['resolved_symbol']}-USD (yfinance)",
    )

    from services.trader import main as trader_main
    human_bias_snapshot = load_symbol_interest_snapshot(
        requested_symbol,
        market_mode=args.market_mode or "sp500",
    )
    if args.track_human_bias:
        human_bias_snapshot = record_symbol_interest(
            requested_symbol,
            market_mode=args.market_mode or "sp500",
            source=args.human_bias_source,
        )

    try:
        if args.csv and os.path.exists(args.csv):
            raw_df = pd.read_csv(args.csv)
            source["kind"] = "custom_csv"
            source["dataset"] = args.csv
        elif source["kind"] == "pair_fallback":
            raw_df = build_pair_raw_df(requested_symbol)
            source["dataset"] = f"{requested_symbol} synthetic pair fallback"
        elif source["kind"] == "local":
            raw_df = pd.read_csv(source["dataset"])
        else:
            raw_df = trader_main.fetch_fallback_data(
                source["resolved_symbol"],
                market_mode=args.market_mode,
            )

        raw_df = trader_main.ensure_raw_df(raw_df)
        last_close_price = safe_float(raw_df["close"].iloc[-1])
        live_snapshot = trader_main.resolve_reference_market_snapshot(
            raw_df,
            source["resolved_symbol"],
        )
        live_price = safe_float(live_snapshot.get("price")) or last_close_price

        with contextlib.redirect_stdout(io.StringIO()):
            runtime = trader_main.MultiResolutionRuntime(
                raw_df=raw_df,
                symbol=source["resolved_symbol"],
            ).bootstrap()
            decision = runtime.infer()

        resolved_current_price = safe_float(decision.get("current_price")) or last_close_price
        resolved_current_timestamp = decision.get("current_timestamp") or raw_df["ds"].iloc[-1]

        wrapper_result = None
        wrapper_config = WrapperConfig() if WrapperConfig is not None else None
        if (
            run_wrapper_pipeline is not None
            and WrapperConfig is not None
            and load_wrapper_weights is not None
            and store_wrapper_prediction_snapshot is not None
        ):
            current_price = float(resolved_current_price) if resolved_current_price is not None else float(raw_df["close"].iloc[-1])
            current_timestamp = resolved_current_timestamp
            try:
                learned_weights, weight_metadata = load_wrapper_weights(
                    requested_symbol,
                    current_price=current_price,
                    current_timestamp=current_timestamp,
                    config=wrapper_config,
                )
            except Exception as exc:
                learned_weights = None
                weight_metadata = {
                    "source": "default",
                    "feedbackCount": 0,
                    "latestFeedback": None,
                    "weightError": str(exc),
                }

            wrapper_result = run_wrapper_pipeline(
                decision=decision,
                execution_context={
                    "slippageBps": 0.0,
                    "priceImpactPct": 0.0,
                    "totalTime": 0.0,
                },
                weight_state=learned_weights,
                config=wrapper_config,
            )
            wrapper_result["wrapper_weight_source"] = weight_metadata.get("source", "default")
            wrapper_result["wrapper_feedback"] = weight_metadata.get("latestFeedback")
            wrapper_result["wrapper_feedback_count"] = weight_metadata.get("feedbackCount", 0)
            if weight_metadata.get("weightError"):
                wrapper_result.setdefault("wrapper_rationale", []).append(
                    f"weight_store_unavailable={weight_metadata['weightError']}"
                )
            try:
                store_wrapper_prediction_snapshot(
                    requested_symbol,
                    reference_price=current_price,
                    reference_timestamp=current_timestamp,
                    wrapper_result=wrapper_result,
                    weights_used=learned_weights or {},
                )
            except Exception as exc:
                wrapper_result.setdefault("wrapper_rationale", []).append(
                    f"weight_snapshot_skipped={exc}"
                )

        prophet_trend = 0.0
        prophet_trend_slope = 0.0
        prophet_weekly = 0.0
        prophet_monthly = 0.0
        if runtime.cadence_rules:
            primary_rule = runtime.cadence_rules[0]
            if primary_rule in runtime.direction_states:
                coord = runtime.direction_states[primary_rule]
                if coord.agents:
                    first_agent = coord.agents[0]
                    try:
                        curve = first_agent.full_curve()
                        if "trend" in curve.columns and not curve.empty:
                            prophet_trend = float(curve["trend"].iloc[0])
                            prophet_trend_slope = float((curve["trend"].iloc[-1] - curve["trend"].iloc[0]) / len(curve))
                    except Exception:
                        pass

            season_rule = None
            for r in runtime.cadence_rules:
                r_upper = str(r or "").upper()
                is_valid = True
                if r_upper.endswith("D") and r_upper[:-1].isdigit() and int(r_upper[:-1]) > 1:
                    is_valid = False
                if is_valid:
                    season_rule = r
                    break
                    
            if season_rule and season_rule in runtime.direction_states:
                coord = runtime.direction_states[season_rule]
                if coord.agents:
                    season_agent = coord.agents[0]
                    try:
                        is_multi = getattr(season_agent.engine.model, "seasonality_mode", "multiplicative") == "multiplicative"
                        curve = season_agent.full_curve()
                        trend_val = float(curve["trend"].iloc[0]) if "trend" in curve.columns else prophet_trend
                        
                        if "weekly" in curve.columns and not curve.empty:
                            w_val = float(curve["weekly"].iloc[0])
                            w_usd = w_val * trend_val if is_multi else w_val
                            y_val = float(curve["yhat"].iloc[0])
                            prophet_weekly = (w_usd / y_val) if y_val else 0.0
                            
                        if "monthly" in curve.columns and not curve.empty:
                            m_val = float(curve["monthly"].iloc[0])
                            m_usd = m_val * trend_val if is_multi else m_val
                            y_val = float(curve["yhat"].iloc[0])
                            prophet_monthly = (m_usd / y_val) if y_val else 0.0
                    except Exception:
                        pass

        result = {
            "supported": True,
            "requestedSymbol": requested_symbol,
            "resolvedSymbol": source["resolved_symbol"],
            "source": source["kind"],
            "dataset": source["dataset"],
            "analysisDate": today_market_date(),
            "analysisTimestampLocal": today_market_timestamp_iso(),
            "rows": int(len(raw_df)),
            "currentPrice": resolved_current_price,
            "livePrice": live_price,
            "lastClosePrice": last_close_price,
            "finalAction": decision["final_action"],
            "directionVote": float(decision["direction_vote"]),
            "directionStrength": float(decision["direction_strength"]),
            "prophetTrend": prophet_trend,
            "prophetTrendSlope": prophet_trend_slope,
            "prophetWeekly": prophet_weekly,
            "prophetMonthly": prophet_monthly,
            "firstMomentPricePerHour": safe_float(decision.get("first_moment_price_per_hour")),
            "firstMomentPctPerHour": safe_float(decision.get("first_moment_pct_per_hour")),
            "secondMomentPricePerHour2": safe_float(decision.get("second_moment_price_per_hour2")),
            "secondMomentPctPerHour2": safe_float(decision.get("second_moment_pct_per_hour2")),
            "timeToBelowCurrentSeconds": safe_float(
                decision.get("time_to_below_current_seconds")
            ),
            "timeToOptimalBuySeconds": safe_float(decision.get("time_to_optimal_buy_seconds")),
            "timeToOptimalSellSeconds": safe_float(decision.get("time_to_optimal_sell_seconds")),
            "riseWindowSeconds": safe_float(decision.get("rise_window_seconds")),
            "dropWindowSeconds": safe_float(decision.get("drop_window_seconds")),
            "spikeStartTimestamp": (
                str(decision.get("spike_start_timestamp"))
                if decision.get("spike_start_timestamp") is not None
                else None
            ),
            "spikePeakTimestamp": (
                str(decision.get("spike_peak_timestamp"))
                if decision.get("spike_peak_timestamp") is not None
                else None
            ),
            "spikePeakPrice": safe_float(decision.get("spike_peak_price")),
            "spikeSustainSeconds": safe_float(decision.get("spike_sustain_seconds")),
            "spikeFadeTimestamp": (
                str(decision.get("spike_fade_timestamp"))
                if decision.get("spike_fade_timestamp") is not None
                else None
            ),
            "spikeFadeInHorizon": decision.get("spike_fade_in_horizon"),
            "peakToFadeSeconds": safe_float(decision.get("peak_to_fade_seconds")),
            "maxSpikePct": safe_float(decision.get("max_spike_pct")),
            "drawdownStartTimestamp": (
                str(decision.get("drawdown_start_timestamp"))
                if decision.get("drawdown_start_timestamp") is not None
                else None
            ),
            "drawdownRecoveryTimestamp": (
                str(decision.get("drawdown_recovery_timestamp"))
                if decision.get("drawdown_recovery_timestamp") is not None
                else None
            ),
            "drawdownTroughTimestamp": (
                str(decision.get("drawdown_trough_timestamp"))
                if decision.get("drawdown_trough_timestamp") is not None
                else None
            ),
            "drawdownTroughPrice": safe_float(decision.get("drawdown_trough_price")),
            "drawdownLingerSeconds": safe_float(decision.get("drawdown_linger_seconds")),
            "drawdownRecoveryInHorizon": decision.get("drawdown_recovery_in_horizon"),
            "troughToRecoverySeconds": safe_float(decision.get("trough_to_recovery_seconds")),
            "maxDrawdownPct": safe_float(decision.get("max_drawdown_pct")),
            "timesfmDrawdownStartTimestamp": (
                str(decision.get("timesfm_drawdown_start_timestamp"))
                if decision.get("timesfm_drawdown_start_timestamp") is not None
                else None
            ),
            "timesfmDrawdownRecoveryTimestamp": (
                str(decision.get("timesfm_drawdown_recovery_timestamp"))
                if decision.get("timesfm_drawdown_recovery_timestamp") is not None
                else None
            ),
            "timesfmDrawdownTroughTimestamp": (
                str(decision.get("timesfm_drawdown_trough_timestamp"))
                if decision.get("timesfm_drawdown_trough_timestamp") is not None
                else None
            ),
            "timesfmDrawdownTroughPrice": safe_float(decision.get("timesfm_drawdown_trough_price")),
            "timesfmDrawdownLingerSeconds": safe_float(decision.get("timesfm_drawdown_linger_seconds")),
            "timesfmDrawdownRecoveryInHorizon": decision.get("timesfm_drawdown_recovery_in_horizon"),
            "timesfmTroughToRecoverySeconds": safe_float(decision.get("timesfm_trough_to_recovery_seconds")),
            "timesfmMaxDrawdownPct": safe_float(decision.get("timesfm_max_drawdown_pct")),
            "timesfmQuantileBandPct": safe_float(decision.get("timesfm_quantile_band_pct")),
            "timesfmSpikeStartTimestamp": (
                str(decision.get("timesfm_spike_start_timestamp"))
                if decision.get("timesfm_spike_start_timestamp") is not None
                else None
            ),
            "timesfmSpikePeakTimestamp": (
                str(decision.get("timesfm_spike_peak_timestamp"))
                if decision.get("timesfm_spike_peak_timestamp") is not None
                else None
            ),
            "timesfmSpikePeakPrice": safe_float(decision.get("timesfm_spike_peak_price")),
            "timesfmSpikeSustainSeconds": safe_float(decision.get("timesfm_spike_sustain_seconds")),
            "timesfmSpikeFadeTimestamp": (
                str(decision.get("timesfm_spike_fade_timestamp"))
                if decision.get("timesfm_spike_fade_timestamp") is not None
                else None
            ),
            "timesfmSpikeFadeInHorizon": decision.get("timesfm_spike_fade_in_horizon"),
            "timesfmPeakToFadeSeconds": safe_float(decision.get("timesfm_peak_to_fade_seconds")),
            "timesfmMaxSpikePct": safe_float(decision.get("timesfm_max_spike_pct")),
            "timesfmStatus": decision.get("timesfm_status"),
            "timesfmError": decision.get("timesfm_error"),
            "timesfmUsed": bool(decision.get("timesfm_used")),
            "timesfmModelId": decision.get("timesfm_model_id"),
            "timesfmMoeGate": json_safe(decision.get("timesfm_moe_gate") or None),
            "moeRuntime": json_safe(decision.get("moe_runtime") or None),
            "spikeSustainConsensusSeconds": safe_float(decision.get("spike_sustain_consensus_seconds")),
            "peakToFadeConsensusSeconds": safe_float(decision.get("peak_to_fade_consensus_seconds")),
            "spikeFadeConsensusInHorizon": decision.get("spike_fade_consensus_in_horizon"),
            "maxSpikeConsensusPct": safe_float(decision.get("max_spike_consensus_pct")),
            "spikeConsensusSource": decision.get("spike_consensus_source"),
            "prophetSpikeWeight": safe_float(decision.get("prophet_spike_weight")),
            "timesfmSpikeWeight": safe_float(decision.get("timesfm_spike_weight")),
            "drawdownLingerConsensusSeconds": safe_float(decision.get("drawdown_linger_consensus_seconds")),
            "troughToRecoveryConsensusSeconds": safe_float(decision.get("trough_to_recovery_consensus_seconds")),
            "drawdownRecoveryConsensusInHorizon": decision.get("drawdown_recovery_consensus_in_horizon"),
            "maxDrawdownConsensusPct": safe_float(decision.get("max_drawdown_consensus_pct")),
            "drawdownConsensusSource": decision.get("drawdown_consensus_source"),
            "trendCurve": json_safe(decision.get("trend_curve") or []),
            "forecastPlot": json_safe(decision.get("forecast_plot") or None),
            "trendComponent": json_safe(decision.get("trend_component") or None),
            "seasonalityComponents": json_safe(decision.get("seasonality_components") or {}),
            "seasonalitySummary": json_safe(decision.get("seasonality_summary") or {}),
            "avgUncertaintyRatio": safe_float(decision.get("avg_uncertainty_ratio")),
            "geodesicState": json_safe(decision.get("geodesic_state") or None),
            "geodesicAvailable": bool(decision.get("geodesic_available")),
            "geodesicLabel": decision.get("geodesic_label"),
            "geodesicActionBias": decision.get("geodesic_action_bias"),
            "geodesicHistoryCount": int(decision.get("geodesic_history_count") or 0),
            "geodesicPathLength": safe_float(decision.get("geodesic_path_length")),
            "geodesicCurvature": safe_float(decision.get("geodesic_curvature")),
            "geodesicAlignmentScore": safe_float(decision.get("geodesic_alignment_score")),
            "geodesicDeviationScore": safe_float(decision.get("geodesic_deviation_score")),
            "geodesicContinuationScore": safe_float(decision.get("geodesic_continuation_score")),
            "geodesicConfidence": safe_float(decision.get("geodesic_confidence")),
            "geodesicProjectedFirstCoordinateX": safe_float(
                decision.get("geodesic_projected_first_coordinate_x")
            ),
            "geodesicProjectedFirstCoordinateY": safe_float(
                decision.get("geodesic_projected_first_coordinate_y")
            ),
            "geodesicProjectedSecondCoordinateX": safe_float(
                decision.get("geodesic_projected_second_coordinate_x")
            ),
            "geodesicProjectedSecondCoordinateY": safe_float(
                decision.get("geodesic_projected_second_coordinate_y")
            ),
            "geodesicProjectedFirstCoordinateDrift": safe_float(
                decision.get("geodesic_projected_first_coordinate_drift")
            ),
            "geodesicProjectedSecondCoordinateDrift": safe_float(
                decision.get("geodesic_projected_second_coordinate_drift")
            ),
            "targetTimestamp": (
                str(decision["target_timestamp"])
                if decision["target_timestamp"] is not None
                else None
            ),
            "targetPrice": (
                float(decision["target_price"])
                if decision["target_price"] is not None
                else None
            ),
            "timingEnabled": bool(decision["timing_enabled"]),
            "timeToBelowCurrent": (
                float(decision["time_to_below_current_seconds"])
                if decision["time_to_below_current_seconds"] is not None
                else None
            ),
            "optimalBuyTimestamp": (
                str(decision.get("optimal_buy_timestamp"))
                if decision.get("optimal_buy_timestamp") is not None
                else None
            ),
            "optimalBuyPrice": safe_float(decision.get("optimal_buy_price")),
            "optimalSellTimestamp": (
                str(decision.get("optimal_sell_timestamp"))
                if decision.get("optimal_sell_timestamp") is not None
                else None
            ),
            "optimalSellPrice": safe_float(decision.get("optimal_sell_price")),
            "cadenceProfile": decision.get("cadence_profile"),
            "cadenceRules": decision.get("cadence_rules") or [],
            "uncertaintySettings": decision.get("uncertainty_settings") or {},
            "runtimeSymbol": decision.get("runtime_symbol"),
            "humanBias": human_bias_snapshot,
            "championRefresh": decision.get("champion_refresh") or {},
            "perRuleSummary": summarize_per_rule(decision.get("per_rule")),
            "wrapper": serialize_wrapper_result(wrapper_result),
            "recommendation": {
                "shouldBuyWithSol": decision["final_action"] == "BUY",
                "tone": (
                    "positive"
                    if decision["final_action"] == "BUY"
                    else "neutral"
                    if decision["final_action"] == "HOLD"
                    else "negative"
                ),
                "summary": (
                    f"Model suggests buying {requested_symbol} with SOL."
                    if decision["final_action"] == "BUY"
                    else f"Model suggests waiting before buying {requested_symbol}."
                    if decision["final_action"] == "HOLD"
                    else f"Model is bearish on {requested_symbol}; avoid buying with SOL right now."
                ),
            },
        }
        print(json.dumps(result))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "supported": False,
                    "requestedSymbol": requested_symbol,
                    "resolvedSymbol": source["resolved_symbol"],
                    "source": source["kind"],
                    "dataset": source["dataset"],
                    "reason": str(exc),
                }
            )
        )


if __name__ == "__main__":
    main()
