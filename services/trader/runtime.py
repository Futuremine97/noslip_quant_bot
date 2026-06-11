import json
from dataclasses import dataclass, field
from typing import Any, Dict

import pandas as pd

from .config import SETTINGS
from .jupiter_client import JupiterQuoteBuffer, JupiterQuotePoller, JupiterSwapClient
from .prophet_agents import (
    DirectionCoordinator,
    TimingCoordinator,
    build_direction_agents,
    build_high_agents,
    build_low_agents,
)
from .utils import (
    add_exec_order_features,
    build_training_views_for_rule,
    choose_best_candidate,
    ensure_raw_df,
    wait_until_target,
    weighted_timestamp,
)


@dataclass
class MultiResolutionRuntime:
    raw_df: pd.DataFrame
    direction_states: Dict[str, DirectionCoordinator] = field(default_factory=dict)
    low_states: Dict[str, TimingCoordinator] = field(default_factory=dict)
    high_states: Dict[str, TimingCoordinator] = field(default_factory=dict)

    def bootstrap(self):
        self.raw_df = ensure_raw_df(self.raw_df)
        for rule in SETTINGS.cadence_rules:
            views = build_training_views_for_rule(self.raw_df, rule)
            self.direction_states[rule] = DirectionCoordinator(build_direction_agents(rule)).fit_all(
                views["direction_df"], prev_agents=None, use_warm_start=False
            )
            self.low_states[rule] = TimingCoordinator(build_low_agents(rule), mode="low").fit_all(
                views["low_df"], prev_agents=None, use_warm_start=False
            )
            self.high_states[rule] = TimingCoordinator(build_high_agents(rule), mode="high").fit_all(
                views["high_df"], prev_agents=None, use_warm_start=False
            )
        return self

    def refit_after_update(self):
        self.raw_df = ensure_raw_df(self.raw_df)
        for rule in SETTINGS.cadence_rules:
            prev_dir_agents = self.direction_states[rule].agents if rule in self.direction_states else None
            prev_low_agents = self.low_states[rule].agents if rule in self.low_states else None
            prev_high_agents = self.high_states[rule].agents if rule in self.high_states else None
            views = build_training_views_for_rule(self.raw_df, rule)
            self.direction_states[rule] = DirectionCoordinator(build_direction_agents(rule)).fit_all(
                views["direction_df"], prev_agents=prev_dir_agents, use_warm_start=True
            )
            self.low_states[rule] = TimingCoordinator(build_low_agents(rule), mode="low").fit_all(
                views["low_df"], prev_agents=prev_low_agents, use_warm_start=True
            )
            self.high_states[rule] = TimingCoordinator(build_high_agents(rule), mode="high").fit_all(
                views["high_df"], prev_agents=prev_high_agents, use_warm_start=True
            )
        return self

    def infer(self) -> Dict[str, Any]:
        per_rule: Dict[str, Any] = {}
        direction_vote = 0.0
        direction_strength = 0.0
        action_map = {"BUY": 1.0, "HOLD": 0.0, "SELL": -1.0}

        for rule in SETTINGS.cadence_rules:
            dir_result = self.direction_states[rule].aggregate()
            low_result = self.low_states[rule].aggregate()
            high_result = self.high_states[rule].aggregate()

            w = SETTINGS.cadence_weights.get(rule, 0.0)
            direction_vote += w * action_map[dir_result["final_action"]]
            direction_strength += w * float(dir_result["weighted_score"])

            per_rule[rule] = {
                "direction": dir_result,
                "low_timing": low_result,
                "high_timing": high_result,
            }

        if direction_vote > 0.20 and direction_strength > 0:
            final_action = "BUY"
        elif direction_vote < -0.20 and direction_strength < 0:
            final_action = "SELL"
        else:
            final_action = "HOLD"

        # Always expose the weighted low forecast as the next buy opportunity.
        target_ts = None
        target_price = None
        ts_pairs, price_pairs = [], []
        for rule in SETTINGS.cadence_rules:
            w = SETTINGS.cadence_weights.get(rule, 0.0)
            ts_pairs.append(
                (per_rule[rule]["low_timing"]["predicted_timestamp"], w)
            )
            price_pairs.append(
                (per_rule[rule]["low_timing"]["predicted_price"], w)
            )

        if ts_pairs:
            target_ts = weighted_timestamp(ts_pairs)
        if price_pairs:
            total_w = sum(w for _, w in price_pairs) or 1.0
            target_price = sum(p * w for p, w in price_pairs) / total_w

        return {
            "final_action": final_action,
            "direction_vote": direction_vote,
            "direction_strength": direction_strength,
            "target_timestamp": target_ts,
            "target_price": target_price,
            "per_rule": per_rule,
        }


def signal_to_swap_params(final_action: str):
    if final_action == "BUY":
        return {
            "input_mint": SETTINGS.output_mint_for_price,
            "output_mint": SETTINGS.input_mint_for_price,
            "amount": SETTINGS.buy_amount_usdc,
        }
    if final_action == "SELL":
        return {
            "input_mint": SETTINGS.input_mint_for_price,
            "output_mint": SETTINGS.output_mint_for_price,
            "amount": SETTINGS.sell_amount_sol,
        }
    return None


def fetch_new_rows_from_jupiter_quotes(
    poller: JupiterQuotePoller, n_polls: int, sleep_seconds: float
) -> pd.DataFrame:
    rows = []
    for i in range(n_polls):
        try:
            quote = poller.get_quote_only()
            row = poller.quote_to_snapshot_row(quote)
            if row is not None:
                rows.append(row)
                print(
                    f"[quote {i+1}/{n_polls}] close={row['close']:.8f} router={row['router']} "
                    f"mode={row['mode']} priceImpact={row['priceImpact']} totalTime={row['totalTime']}"
                )
            else:
                print(f"[quote {i+1}/{n_polls}] skipped (non-ultra or invalid)")
        except Exception as e:
            print(f"[quote {i+1}/{n_polls}] error: {e}")
        if i < n_polls - 1:
            import time

            time.sleep(sleep_seconds)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def maybe_execute_with_jupiter(decision: Dict[str, Any], execute_trades: bool = False):
    final_action = decision["final_action"]
    if final_action == "HOLD":
        print("No trade. HOLD.")
        return None

    swap_params = signal_to_swap_params(final_action)
    if swap_params is None:
        return None

    print("\n=== DECISION ===")
    print(
        json.dumps(
            {
                "final_action": final_action,
                "direction_vote": decision["direction_vote"],
                "direction_strength": decision["direction_strength"],
                "target_timestamp": str(decision["target_timestamp"]),
                "target_price": decision["target_price"],
            },
            indent=2,
        )
    )

    from .main import get_wallet  # lazy import to avoid cycles during tests

    wallet = get_wallet()
    taker = str(wallet.pubkey())
    client = JupiterSwapClient(SETTINGS.jupiter_api_key)

    if SETTINGS.wait_for_target and decision["target_timestamp"] is not None:
        wait_until_target(decision["target_timestamp"], lead_seconds=SETTINGS.target_lead_seconds)

    candidates = client.collect_candidate_orders(
        input_mint=swap_params["input_mint"],
        output_mint=swap_params["output_mint"],
        amount=swap_params["amount"],
        taker=taker,
        rounds=SETTINGS.quote_burst_count,
        sleep_seconds=SETTINGS.quote_burst_sleep_seconds,
    )
    if not candidates:
        print("No execution candidates collected.")
        return None

    quotes_df = pd.DataFrame([{k: v for k, v in c.items() if not k.startswith("_")} for c in candidates])
    quotes_df = add_exec_order_features(quotes_df)
    quotes_df.to_csv("data/outputs/candidate_orders.csv", index=False)

    best = choose_best_candidate(candidates)
    best_public = {k: v for k, v in best.items() if not k.startswith("_")}
    pd.DataFrame([best_public]).to_csv("data/outputs/best_order.csv", index=False)

    print("\n=== BEST ORDER ===")
    print(
        json.dumps(
            {
                "requestId": best_public["requestId"],
                "router": best_public["router"],
                "mode": best_public["mode"],
                "outAmount": best_public["outAmount"],
                "priceImpact": best_public["priceImpact"],
                "totalTime": best_public["totalTime"],
            },
            indent=2,
        )
    )

    if not execute_trades:
        print("Dry run only. Set EXECUTE_TRADES=true to actually execute.")
        return {"decision": decision, "best_order": best_public}

    raw_order = best["_raw_order"]
    signed_tx_b64 = client.sign_order_transaction(raw_order, wallet)
    execute_result = client.execute_order(
        signed_transaction_b64=signed_tx_b64,
        request_id=raw_order["requestId"],
        last_valid_block_height=raw_order.get("lastValidBlockHeight"),
    )
    pd.DataFrame([execute_result]).to_csv("data/outputs/execute_result.csv", index=False)
    print("\n=== EXECUTE RESULT ===")
    print(json.dumps(execute_result, indent=2))
    return {"decision": decision, "best_order": best_public, "execute_result": execute_result}


def run_realtime_loop_with_jupiter_quotes(historical_raw_df: pd.DataFrame, execute_trades: bool = False):
    historical_raw_df = ensure_raw_df(historical_raw_df)
    runtime = MultiResolutionRuntime(raw_df=historical_raw_df).bootstrap()

    quote_buffer = JupiterQuoteBuffer()
    quote_buffer.seed_from_historical(historical_raw_df)

    poller = JupiterQuotePoller(
        api_key=SETTINGS.jupiter_api_key,
        input_mint=SETTINGS.input_mint_for_price,
        output_mint=SETTINGS.output_mint_for_price,
        input_decimals=SETTINGS.input_decimals,
        output_decimals=SETTINGS.output_decimals,
        amount_in_smallest_unit=SETTINGS.quote_amount_in_smallest_unit,
        require_ultra=SETTINGS.require_ultra_for_training,
    )

    initial_decision = runtime.infer()
    print("\n=== INITIAL DECISION ===")
    print(
        json.dumps(
            {
                "final_action": initial_decision["final_action"],
                "direction_vote": initial_decision["direction_vote"],
                "direction_strength": initial_decision["direction_strength"],
                "target_timestamp": str(initial_decision["target_timestamp"]),
                "target_price": initial_decision["target_price"],
            },
            indent=2,
        )
    )
    maybe_execute_with_jupiter(initial_decision, execute_trades=execute_trades)

    iteration = 0
    while iteration < SETTINGS.max_iterations:
        new_rows = fetch_new_rows_from_jupiter_quotes(
            poller=poller,
            n_polls=SETTINGS.quote_burst_count,
            sleep_seconds=SETTINGS.quote_burst_sleep_seconds,
        )
        if not new_rows.empty:
            quote_buffer.append_many(new_rows.to_dict(orient="records"))
            latest_raw_df = quote_buffer.to_ohlc("1min")
            if not latest_raw_df.empty and len(latest_raw_df) >= 100:
                runtime.raw_df = latest_raw_df
                runtime.refit_after_update()
                decision = runtime.infer()
                print("\n=== UPDATED DECISION ===")
                print(
                    json.dumps(
                        {
                            "final_action": decision["final_action"],
                            "direction_vote": decision["direction_vote"],
                            "direction_strength": decision["direction_strength"],
                            "target_timestamp": str(decision["target_timestamp"]),
                            "target_price": decision["target_price"],
                        },
                        indent=2,
                    )
                )
                maybe_execute_with_jupiter(decision, execute_trades=execute_trades)
            else:
                print("Not enough realtime bars yet for retraining.")
        else:
            print("No new Jupiter quote rows.")

        iteration += 1
        import time

        time.sleep(SETTINGS.poll_every_seconds)
    return runtime
