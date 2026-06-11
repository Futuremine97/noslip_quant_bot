#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import sys

import json
import os
import traceback
from pathlib import Path

# Add project root and services/trader to path
ROOT_DIR = Path(__file__).resolve().parents[2]
if not ROOT_DIR.exists() or not (ROOT_DIR / "services" / "trader").exists():
    ROOT_DIR = Path(__file__).resolve().parents[2]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

TRADER_DIR = ROOT_DIR / "services" / "trader"
if str(TRADER_DIR) not in sys.path:
    sys.path.insert(0, str(TRADER_DIR))

import machine_auth


WEB3_TOOL_DEFINITIONS = [
    {
        "name": "get_credit_balance",
        "description": (
            "Read a NoSlip user's off-chain credit balance and recent audited "
            "ledger entries. This does not access a wallet private key."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "NoSlip user identifier. Defaults to 'default'.",
                }
            },
        },
    },
    {
        "name": "estimate_feature_cost",
        "description": (
            "Return the server-defined NoSlip Credit cost for a premium feature. "
            "This is read-only and does not debit credits."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "feature": {
                    "type": "string",
                    "enum": [
                        "personal_forecast",
                        "zero_shot_forecast",
                        "premium_whale_report",
                        "premium_signal_feed",
                        "strategy_tournament",
                        "api_usage",
                    ],
                }
            },
            "required": ["feature"],
        },
    },
    {
        "name": "create_credit_payment_intent",
        "description": (
            "Prepare a testnet-first USDC credit purchase intent. The tool does "
            "not send a blockchain transaction; the user must sign from their wallet."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "package_id": {
                    "type": "string",
                    "enum": ["starter", "research", "team"],
                },
                "wallet_address": {
                    "type": "string",
                    "description": "Optional public EVM address. Never provide a private key.",
                },
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "confirm_credit_payment",
        "description": (
            "Ask the NoSlip backend to confirm an existing payment intent. In "
            "local mock mode no chain transaction is sent; production verification "
            "must validate a user-signed Base transaction."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "intent_id": {"type": "string"},
                "tx_hash": {
                    "type": "string",
                    "description": "Optional public transaction hash.",
                },
            },
            "required": ["user_id", "intent_id"],
        },
    },
    {
        "name": "check_premium_access",
        "description": (
            "Check whether a NoSlip user has enough credits for a premium "
            "feature. This is read-only and does not consume credits."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "feature": {
                    "type": "string",
                    "enum": [
                        "personal_forecast",
                        "zero_shot_forecast",
                        "premium_whale_report",
                        "premium_signal_feed",
                        "strategy_tournament",
                        "api_usage",
                    ],
                },
            },
            "required": ["user_id", "feature"],
        },
    },
]

BROKER_TOOL_DEFINITIONS = [
    {
        "name": "get_broker_status",
        "description": (
            "Inspect the local Yuanta or Toss Securities integration mode and "
            "configuration readiness. Read-only; never returns credentials."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "enum": ["toss", "yuanta"],
                    "description": "Omit to inspect both configured providers.",
                }
            },
        },
    },
    {
        "name": "get_broker_prices",
        "description": (
            "Read stock prices through a configured securities API. Toss uses "
            "its official REST Open API. Yuanta uses the token-protected local "
            "Windows COM bridge. This tool never places an order."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "enum": ["toss", "yuanta"],
                },
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 20,
                },
            },
            "required": ["provider", "symbols"],
        },
    },
    {
        "name": "get_broker_holdings",
        "description": (
            "Read holdings from a locally configured securities account. "
            "Credentials remain in environment variables on the service host "
            "and are never returned. This tool never places an order."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "enum": ["toss", "yuanta"],
                }
            },
            "required": ["provider"],
        },
    },
    {
        "name": "prepare_broker_order",
        "description": (
            "Validate and preview a Yuanta or Toss Securities stock order. "
            "Preview only: this MCP tool cannot submit, modify, or cancel orders."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "enum": ["toss", "yuanta"],
                },
                "symbol": {"type": "string"},
                "side": {"type": "string", "enum": ["BUY", "SELL"]},
                "order_type": {
                    "type": "string",
                    "enum": ["LIMIT", "MARKET"],
                },
                "quantity": {
                    "type": ["string", "integer"],
                    "description": "Whole-share quantity. Do not combine with order_amount.",
                },
                "price": {
                    "type": ["string", "number"],
                    "description": "Required for LIMIT and forbidden for MARKET.",
                },
                "order_amount": {
                    "type": ["string", "number"],
                    "description": "Toss US MARKET amount order only.",
                },
                "time_in_force": {
                    "type": "string",
                    "enum": ["DAY", "CLS"],
                    "default": "DAY",
                },
            },
            "required": ["provider", "symbol", "side", "order_type"],
        },
    },
]


def get_analysis_tool_with_graph(symbol: str) -> tuple[str, str | None]:
    from telegram_interactive_bot import execute_analysis_with_graph, normalize_symbol
    normalized = normalize_symbol(symbol)
    return execute_analysis_with_graph(normalized)

def get_tournament_tool() -> str:
    from bot_competition_tournament import run_tournament
    return run_tournament()

def consume_feature_credits(arguments: dict, feature: str) -> dict:
    import web3_client

    user_id = str(arguments.get("user_id", "default")).strip() or "default"
    return web3_client.consume_premium_access(user_id, feature)

def send_response(response):
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()

def main():
    # Set stdin and stdout to utf-8 encoding safely
    try:
        sys.stdin.reconfigure(encoding='utf-8')
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        # Fallback if Python version doesn't support reconfigure
        pass
    
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            req = json.loads(line)
            req_id = req.get("id")
            method = req.get("method")
            
            if method == "initialize":
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "tools": {}
                        },
                        "serverInfo": {
                            "name": "NoSlipQuant",
                            "version": "1.0.0"
                        }
                    }
                }
                send_response(res)
                
            elif method == "notifications/initialized":
                # No response needed
                pass
                
            elif method == "tools/list":
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": [
                          {
                            "name": "analyze_ticker",
                            "description": "Generate a full trading analysis for a single stock or cryptocurrency using the 6-Agent Consensus model (technical, momentum, volatility, trend, mean-reversion, and risk agents that vote on a final BUY/SELL/HOLD decision).\n\nUse this when the user asks whether to buy, sell, or hold a specific asset, or wants the current signal, indicators, and rationale for one symbol.\n\nReturns a human-readable report containing the consensus action, per-agent votes, key indicators (price, RSI, SMA, momentum, volatility), and a base64-encoded decision-flow graph image. Read-only: fetches live market data but does not place trades or modify any saved state.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "symbol": {
                                  "type": "string",
                                  "description": "A single asset ticker. Stocks use the bare symbol (e.g. 'INTC', 'TSLA', 'NVDA'); major cryptos accept either the short form ('BTC', 'ETH', 'SOL') or the '-USD' pair (e.g. 'BTC-USD'). Supports S&P 500 stocks and BTC/ETH/SOL. One symbol per call."
                                }
                              },
                              "required": ["symbol"]
                            }
                          },
                          {
                            "name": "run_league_tournament",
                            "description": "Run the daily AI & Quant bot tournament after consuming the server-defined NoSlip Credit cost. The backtest league pits the project's top open-source trading strategies against each other and ranks them by performance.\n\nUse this when the user wants to compare strategies, see which bot is currently winning, or refresh today's leaderboard standings.\n\nReturns a ranked, human-readable summary of each strategy's backtest results (returns and relative standing). It may take longer than other tools because it runs simulations.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "user_id": {
                                  "type": "string",
                                  "description": "NoSlip credit account to debit. Defaults to 'default'."
                                }
                              }
                            }
                          },
                          {
                            "name": "configure_personal_ontology",
                            "description": "Create or update a personalized investment concept: a named basket of symbols plus custom audit rules used later by analyze_by_personal_ontology.\n\nUse this to save a watchlist or thesis (e.g. an 'AI Stocks' basket) together with the constraints that define a good candidate. Calling it again with an existing concept_name overwrites that concept.\n\nWrites to per-user persistent storage and returns a confirmation of the saved concept. Use get_personal_ontology to review what is stored and delete_personal_ontology_concept to remove it.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "concept_name": {
                                  "type": "string",
                                  "description": "Unique name identifying the concept; reusing an existing name overwrites it (e.g. 'AI Stocks', 'Value Tech')."
                                },
                                "description": {
                                  "type": "string",
                                  "description": "Optional free-text note describing the thesis or purpose of this concept."
                                },
                                "symbols": {
                                  "type": "array",
                                  "items": { "type": "string" },
                                  "description": "Tickers belonging to this concept, same format as analyze_ticker (e.g. ['NVDA', 'IONQ', 'BTC-USD'])."
                                },
                                "rules": {
                                  "type": "object",
                                  "description": "Optional audit constraints each symbol is checked against. Supported keys: min_price (number), max_price (number), min_rsi (number), max_rsi (number), require_price_above_sma20 (boolean), min_momentum (number, e.g. 0.05 = 5%), max_volatility (number, e.g. 5.0), expected_action (string: 'BUY', 'SELL', or 'HOLD'). Omit to store the concept without constraints."
                                },
                                "user_id": {
                                  "type": "string",
                                  "description": "Identifier that isolates one user's concepts from another's. Optional; defaults to 'default'."
                                }
                              },
                              "required": ["concept_name"]
                            }
                          },
                          {
                            "name": "get_personal_ontology",
                            "description": "List every saved personalized concept for a user, including each concept's description, symbols, and audit rules.\n\nUse this to review what concepts exist before analyzing, updating, or deleting them. Read-only.\n\nReturns a human-readable summary of all stored concepts for the given user_id (empty if none have been configured).",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "user_id": {
                                  "type": "string",
                                  "description": "Identifier whose concepts to retrieve. Optional; defaults to 'default'."
                                }
                              }
                            }
                          },
                          {
                            "name": "delete_personal_ontology_concept",
                            "description": "Permanently remove one saved personalized concept from a user's stored configuration.\n\nUse this to clean up a concept that is no longer needed. This is destructive and cannot be undone; only the named concept is removed, other concepts are untouched.\n\nReturns a confirmation of the deletion. If the concept does not exist, it reports that nothing was deleted.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "concept_name": {
                                  "type": "string",
                                  "description": "Exact name of the concept to delete (must match a name from get_personal_ontology)."
                                },
                                "user_id": {
                                  "type": "string",
                                  "description": "Identifier owning the concept to delete. Optional; defaults to 'default'."
                                }
                              },
                              "required": ["concept_name"]
                            }
                          },
                          {
                            "name": "analyze_by_personal_ontology",
                            "description": "Fetch live market metrics for every symbol in a saved concept and audit each one against that concept's custom rules.\n\nUse this after configure_personal_ontology to evaluate a whole basket at once and see which symbols currently pass or fail the defined constraints. The concept must already exist (create it with configure_personal_ontology first).\n\nReturns a human-readable per-symbol report showing live metrics and a pass/fail verdict against each rule. Read-only: reads market data and the saved concept but does not modify stored state.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "concept_name": {
                                  "type": "string",
                                  "description": "Name of an existing saved concept to evaluate (must match a name from get_personal_ontology)."
                                },
                                "user_id": {
                                  "type": "string",
                                  "description": "Identifier owning the concept. Optional; defaults to 'default'."
                                }
                              },
                              "required": ["concept_name"]
                            }
                          },
                          {
                            "name": "submit_prophet_leaderboard",
                            "description": "Publish this installation's local Prophet champion forecasting model metrics to the shared global leaderboard hosted on the central server.\n\nUse this when the user wants to register or update their bot's standings on the public leaderboard. This makes a network call that writes the local metrics to a shared external service under the given bot_id.\n\nReturns a confirmation of what was submitted. Pair with view_prophet_leaderboard to see the resulting standings.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "bot_id": {
                                  "type": "string",
                                  "description": "Public name your forecasts are recorded under on the shared leaderboard; reusing an existing bot_id updates that entry (e.g. 'Futuremine97_bot')."
                                }
                              },
                              "required": ["bot_id"]
                            }
                          },
                          {
                            "name": "view_prophet_leaderboard",
                            "description": "Read the shared global Prophet forecasting leaderboard from the central server and show current standings across all participating bots.\n\nUse this to see how forecasting models rank, optionally narrowing to a single asset. Read-only network call; does not submit or modify anything.\n\nReturns a ranked, human-readable table of bots and their forecast accuracy metrics.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "symbol": {
                                  "type": "string",
                                  "description": "Optional asset filter in '-USD' pair form (e.g. 'BTC-USD'). Omit to view standings across all assets."
                                }
                              }
                            }
                          },
                          {
                            "name": "get_alpha_recommendations",
                            "description": "Get recommended strategy parameters (Take-Profit, Stop-Loss, holding time, and threshold ranges) for various arbitrage and technical indicators based on a user's risk tolerance persona ('conservative', 'balanced', or 'aggressive').\n\nUse this when the user asks for guidance on setting parameters or configuring their custom strategies.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "persona": {
                                  "type": "string",
                                  "description": "The investment persona style. Choose from 'conservative', 'balanced', or 'aggressive'.",
                                  "enum": ["conservative", "balanced", "aggressive"]
                                }
                              },
                              "required": ["persona"]
                            }
                          },
                          {
                            "name": "register_peer",
                            "description": "Join (or heartbeat into) the No Slip Quant Peer Hub: the community of users running this Claude Code plugin. Registers a stable local peer identity with the central server so other plugin users can see you online and read your shared signals.\n\nUse this the first time a user wants to connect with other plugin users, or to update their nickname/bio. Network call that writes presence data.\n\nReturns a confirmation with the registered nickname and peer_id.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "nickname": {
                                  "type": "string",
                                  "description": "Public display name shown to other plugin users (e.g. 'Futuremine97'). Omit to keep the current/default name."
                                },
                                "bio": {
                                  "type": "string",
                                  "description": "Optional one-line introduction shown in the peer roster."
                                }
                              }
                            }
                          },
                          {
                            "name": "list_peers",
                            "description": "Show the roster of plugin users connected to the No Slip Quant Peer Hub, with online presence (recent heartbeat), bios, and each peer's best Prophet leaderboard score.\n\nUse this when the user asks who else is online, who is in the community, or wants to compare standings. Read-only network call.\n\nReturns a human-readable roster.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {}
                            }
                          },
                          {
                            "name": "share_alpha_signal",
                            "description": "Broadcast a trading idea (alpha signal) to every user connected to the Peer Hub: symbol, BUY/SELL/HOLD direction, confidence, and an optional thesis.\n\nUse this when the user wants to share their view on an asset with the community. Network call that writes to the shared feed under the user's registered nickname.\n\nReturns a confirmation of the shared signal.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "symbol": {
                                  "type": "string",
                                  "description": "Asset ticker (e.g. 'NVDA', 'BTC-USD')."
                                },
                                "direction": {
                                  "type": "string",
                                  "description": "Signal direction.",
                                  "enum": ["BUY", "SELL", "HOLD"]
                                },
                                "confidence": {
                                  "type": "number",
                                  "description": "Confidence 0-100 (%)."
                                },
                                "thesis": {
                                  "type": "string",
                                  "description": "Optional short rationale shown with the signal (max 500 chars)."
                                }
                              },
                              "required": ["symbol", "direction", "confidence"]
                            }
                          },
                          {
                            "name": "view_signal_feed",
                            "description": "Read the shared alpha-signal feed from the Peer Hub: recent BUY/SELL/HOLD calls from all connected plugin users, plus a per-symbol consensus tally.\n\nUse this when the user asks what other users are trading, what the community thinks about an asset, or wants the latest shared ideas. Read-only network call.\n\nReturns a human-readable feed with consensus summary.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "symbol": {
                                  "type": "string",
                                  "description": "Optional ticker filter (e.g. 'NVDA'). Omit for the full feed."
                                },
                                "limit": {
                                  "type": "number",
                                  "description": "Max signals to return (1-100, default 20)."
                                },
                                "user_id": {
                                  "type": "string",
                                  "description": "NoSlip credit account to debit. Defaults to 'default'."
                                }
                              }
                            }
                          },
                          {
                            "name": "zero_shot_forecast",
                            "description": "Instantly forecast ANY time series with zero setup — not just stocks/crypto. Supports domain presets: 'finance' (prices), 'semiconductor' (fab process metrics like yield %, defect density, with SPC control-limit anomaly detection), 'quantum' (Stim quantum-error-correction logical/physical error rates, log-scale, per round/shot), 'generic' (anything else).\n\nUse when the user provides a CSV path (or asks to forecast their own data) and wants an immediate prediction without registering a dataset.\n\nReturns forecast summary (end value, change %, 80% band, out-of-control anomalies) plus a rendered chart image.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "csv_path": {
                                  "type": "string",
                                  "description": "Path to a CSV with a time column (date/round/shot/step) and a value column (close/yield/error_rate/value...)."
                                },
                                "domain": {
                                  "type": "string",
                                  "description": "Domain preset.",
                                  "enum": ["finance", "semiconductor", "quantum", "generic"]
                                },
                                "days": {
                                  "type": "number",
                                  "description": "Forecast horizon in periods/days (default 30)."
                                },
                                "user_id": {
                                  "type": "string",
                                  "description": "NoSlip credit account to debit. Defaults to 'default'."
                                }
                              },
                              "required": ["csv_path"]
                            }
                          },
                          {
                            "name": "train_personal_forecast",
                            "description": "Register a company/individual dataset and train a PERSONALIZED time-series model on it (hyperparameter-tuned Prophet, persisted for repeated serving). Works for finance, semiconductor process data, quantum error (Stim) data, or any series.\n\nUse when the user wants an ongoing personalized forecasting service over their own data rather than a one-off prediction.\n\nReturns training metadata including holdout MAPE and chosen hyperparameters.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "user_id": {
                                  "type": "string",
                                  "description": "Owner identifier (company or person, e.g. 'acme' or 'sunghoon')."
                                },
                                "name": {
                                  "type": "string",
                                  "description": "Dataset name (e.g. 'fab7_yield', 'qec_d5_error_rate', 'store_sales')."
                                },
                                "csv_path": {
                                  "type": "string",
                                  "description": "Path to the CSV to register and train on."
                                },
                                "domain": {
                                  "type": "string",
                                  "description": "Domain preset.",
                                  "enum": ["finance", "semiconductor", "quantum", "generic"]
                                }
                              },
                              "required": ["user_id", "name", "csv_path"]
                            }
                          },
                          {
                            "name": "get_personal_forecast",
                            "description": "Serve a forecast from a previously trained personalized model, including SPC control-limit anomaly report (out-of-control points) for process/quantum domains.\n\nUse after train_personal_forecast to get predictions for a registered dataset.\n\nReturns forecast summary, anomaly list, and a rendered chart image.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "user_id": {
                                  "type": "string",
                                  "description": "Owner identifier used at training time."
                                },
                                "name": {
                                  "type": "string",
                                  "description": "Registered dataset name."
                                },
                                "days": {
                                  "type": "number",
                                  "description": "Forecast horizon (default 30)."
                                }
                              },
                              "required": ["user_id", "name"]
                            }
                          },
                          *WEB3_TOOL_DEFINITIONS,
                          *BROKER_TOOL_DEFINITIONS
                        ]
                    }
                }
                send_response(res)
                
            elif method == "tools/call":
                params = req.get("params", {})
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                
                if tool_name == "analyze_ticker":
                    sym = arguments.get("symbol")
                    if not sym:
                        raise ValueError("Missing symbol argument")
                    report_text, base64_img = get_analysis_tool_with_graph(sym)
                    result_text = [
                        {
                            "type": "text",
                            "text": report_text
                        }
                    ]
                    if base64_img:
                        result_text.append({
                            "type": "image",
                            "mimeType": "image/png",
                            "data": base64_img
                        })
                    
                elif tool_name == "run_league_tournament":
                    consume_feature_credits(arguments, "strategy_tournament")
                    result_text = get_tournament_tool()
                    
                elif tool_name == "configure_personal_ontology":
                    concept_name = arguments.get("concept_name")
                    if not concept_name:
                        raise ValueError("Missing concept_name argument")
                    desc = arguments.get("description", "")
                    symbols = arguments.get("symbols", [])
                    rules = arguments.get("rules", {})
                    user_id = arguments.get("user_id", "default")
                    
                    from personal_ontology import save_concept
                    res_dict = save_concept(user_id, concept_name, desc, symbols, rules)
                    result_text = f"✅ Personalized ontology concept '{concept_name}' configured successfully.\n\n" + json.dumps(res_dict, indent=2, ensure_ascii=False)
                    
                elif tool_name == "get_personal_ontology":
                    user_id = arguments.get("user_id", "default")
                    
                    from personal_ontology import get_concepts
                    concepts = get_concepts(user_id)
                    result_text = f"📂 Personalized Ontology Concepts for user '{user_id}':\n\n" + json.dumps(concepts, indent=2, ensure_ascii=False)
                    
                elif tool_name == "delete_personal_ontology_concept":
                    concept_name = arguments.get("concept_name")
                    if not concept_name:
                        raise ValueError("Missing concept_name argument")
                    user_id = arguments.get("user_id", "default")
                    
                    from personal_ontology import delete_concept
                    success = delete_concept(user_id, concept_name)
                    if success:
                        result_text = f"✅ Concept '{concept_name}' deleted successfully."
                    else:
                        result_text = f"⚠️ Concept '{concept_name}' not found for user '{user_id}'."
                        
                elif tool_name == "analyze_by_personal_ontology":
                    concept_name = arguments.get("concept_name")
                    if not concept_name:
                        raise ValueError("Missing concept_name argument")
                    user_id = arguments.get("user_id", "default")
                    
                    from personal_ontology import evaluate_ontology_concept
                    result_text = evaluate_ontology_concept(user_id, concept_name)
                    
                elif tool_name == "submit_prophet_leaderboard":
                    bot_id = arguments.get("bot_id")
                    if not bot_id:
                        raise ValueError("Missing bot_id argument")
                    
                    from leaderboard_sync import sync_local_champions_to_leaderboard
                    res_dict = sync_local_champions_to_leaderboard(bot_id)
                    result_text = f"📊 Submission results for bot '{bot_id}':\n\n" + json.dumps(res_dict, indent=2, ensure_ascii=False)
                    
                elif tool_name == "view_prophet_leaderboard":
                    symbol = arguments.get("symbol")

                    from leaderboard_sync import fetch_leaderboard_report
                    result_text = fetch_leaderboard_report(symbol)

                elif tool_name == "get_alpha_recommendations":
                    persona = arguments.get("persona", "balanced").strip().lower()
                    presets = {
                        "conservative": {
                            "take_profit_pct": "0.8 ~ 1.5", "stop_loss_pct": "0.4 ~ 0.8",
                            "max_hold_minutes": "30 ~ 60", "rsi_trigger": "20 ~ 25",
                            "spot_arb_threshold_pct": "0.35+", "kimchi_threshold_pct": "0.45+",
                            "note": "낮은 빈도·높은 확실성 진입. 손절 타이트, 차익거래 임계치 보수적."
                        },
                        "balanced": {
                            "take_profit_pct": "1.5 ~ 3.0", "stop_loss_pct": "0.8 ~ 1.5",
                            "max_hold_minutes": "60 ~ 180", "rsi_trigger": "25 ~ 30",
                            "spot_arb_threshold_pct": "0.25+", "kimchi_threshold_pct": "0.30+",
                            "note": "기본 운영값. whale_config.json 기본 파라미터와 유사."
                        },
                        "aggressive": {
                            "take_profit_pct": "3.0 ~ 6.0", "stop_loss_pct": "1.5 ~ 2.5",
                            "max_hold_minutes": "180 ~ 480", "rsi_trigger": "30 ~ 35",
                            "spot_arb_threshold_pct": "0.15+", "kimchi_threshold_pct": "0.20+",
                            "note": "높은 빈도·큰 변동 허용. MLP 하락 필터 활성 상태 유지 권장."
                        },
                    }
                    rec = presets.get(persona)
                    if not rec:
                        raise ValueError(f"Unknown persona: {persona} (choose conservative/balanced/aggressive)")
                    result_text = (f"🎯 '{persona}' 페르소나 권장 전략 파라미터:\n\n"
                                   + json.dumps(rec, indent=2, ensure_ascii=False))

                elif tool_name == "register_peer":
                    from peer_hub_client import register_peer
                    result_text = register_peer(arguments.get("nickname", ""), arguments.get("bio", ""))

                elif tool_name == "list_peers":
                    from peer_hub_client import list_peers
                    result_text = list_peers()

                elif tool_name == "share_alpha_signal":
                    symbol = arguments.get("symbol")
                    direction = arguments.get("direction")
                    confidence = arguments.get("confidence")
                    if not symbol or not direction or confidence is None:
                        raise ValueError("Missing symbol/direction/confidence argument")
                    from peer_hub_client import share_alpha_signal
                    result_text = share_alpha_signal(symbol, direction, float(confidence),
                                                     arguments.get("thesis", ""))

                elif tool_name == "view_signal_feed":
                    consume_feature_credits(arguments, "premium_signal_feed")
                    from peer_hub_client import view_signal_feed
                    result_text = view_signal_feed(arguments.get("symbol", "") or "",
                                                   int(arguments.get("limit", 20)))

                elif tool_name in ("zero_shot_forecast", "train_personal_forecast",
                                   "get_personal_forecast"):
                    import base64 as _b64
                    import personal_forecast_service as pfs
                    chart_path = None
                    if tool_name == "zero_shot_forecast":
                        csv_path = arguments.get("csv_path")
                        if not csv_path:
                            raise ValueError("Missing csv_path argument")
                        consume_feature_credits(arguments, "zero_shot_forecast")
                        r = pfs.zero_shot_forecast(csv_path=csv_path,
                                                   domain=arguments.get("domain", "generic"),
                                                   days=int(arguments.get("days", 30)))
                        chart_path = r.pop("chart", None)
                        summary = "🔮 Zero-shot forecast 결과:\n\n" + json.dumps(r, ensure_ascii=False, indent=2)
                    elif tool_name == "train_personal_forecast":
                        user_id = arguments.get("user_id"); name = arguments.get("name")
                        csv_path = arguments.get("csv_path")
                        if not (user_id and name and csv_path):
                            raise ValueError("Missing user_id/name/csv_path argument")
                        consume_feature_credits(arguments, "personal_forecast")
                        pfs.register_dataset(user_id, name, csv_path=csv_path,
                                             domain=arguments.get("domain", "generic"))
                        meta = pfs.train_personal_model(user_id, name)
                        summary = ("🎓 개인화 모델 학습 완료:\n\n"
                                   + json.dumps(meta, ensure_ascii=False, indent=2))
                    else:
                        user_id = arguments.get("user_id"); name = arguments.get("name")
                        if not (user_id and name):
                            raise ValueError("Missing user_id/name argument")
                        consume_feature_credits(arguments, "personal_forecast")
                        r = pfs.personal_forecast(user_id, name,
                                                  days=int(arguments.get("days", 30)))
                        chart_path = r.pop("chart", None)
                        summary = ("📈 개인화 예측 결과 (SPC 이상치 포함):\n\n"
                                   + json.dumps(r, ensure_ascii=False, indent=2))
                    result_text = [{"type": "text", "text": summary}]
                    if chart_path:
                        with open(chart_path, "rb") as f:
                            result_text.append({"type": "image", "mimeType": "image/png",
                                                "data": _b64.b64encode(f.read()).decode()})

                elif tool_name in (
                    "get_credit_balance",
                    "estimate_feature_cost",
                    "create_credit_payment_intent",
                    "confirm_credit_payment",
                    "check_premium_access",
                ):
                    import web3_client

                    user_id = arguments.get("user_id", "default")
                    if tool_name == "get_credit_balance":
                        payload = web3_client.get_credit_balance(user_id)
                    elif tool_name == "estimate_feature_cost":
                        feature = arguments.get("feature")
                        if not feature:
                            raise ValueError("Missing feature argument")
                        payload = web3_client.estimate_feature_cost(feature)
                    elif tool_name == "create_credit_payment_intent":
                        payload = web3_client.create_credit_payment_intent(
                            user_id=user_id,
                            package_id=arguments.get("package_id", "starter"),
                            wallet_address=arguments.get("wallet_address", ""),
                        )
                    elif tool_name == "confirm_credit_payment":
                        intent_id = arguments.get("intent_id")
                        if not intent_id:
                            raise ValueError("Missing intent_id argument")
                        payload = web3_client.confirm_credit_payment(
                            user_id=user_id,
                            intent_id=intent_id,
                            tx_hash=arguments.get("tx_hash", ""),
                        )
                    else:
                        feature = arguments.get("feature")
                        if not feature:
                            raise ValueError("Missing feature argument")
                        payload = web3_client.check_premium_access(
                            user_id, feature
                        )
                    result_text = json.dumps(payload, indent=2, ensure_ascii=False)

                elif tool_name in (
                    "get_broker_status",
                    "get_broker_prices",
                    "get_broker_holdings",
                    "prepare_broker_order",
                ):
                    from brokers.service import (
                        broker_status,
                        get_broker,
                        prepare_broker_order,
                    )

                    provider = arguments.get("provider", "")
                    if tool_name == "get_broker_status":
                        payload = broker_status(provider)
                    elif tool_name == "get_broker_prices":
                        symbols = arguments.get("symbols")
                        if not provider or not isinstance(symbols, list):
                            raise ValueError("provider and symbols are required")
                        payload = get_broker(provider).get_prices(symbols)
                    elif tool_name == "get_broker_holdings":
                        if not provider:
                            raise ValueError("provider is required")
                        payload = get_broker(provider).get_holdings()
                    else:
                        required = ("provider", "symbol", "side", "order_type")
                        if any(not arguments.get(key) for key in required):
                            raise ValueError(
                                "provider, symbol, side, and order_type are required"
                            )
                        payload = prepare_broker_order(
                            provider=provider,
                            symbol=arguments["symbol"],
                            side=arguments["side"],
                            order_type=arguments["order_type"],
                            quantity=arguments.get("quantity"),
                            price=arguments.get("price"),
                            order_amount=arguments.get("order_amount"),
                            time_in_force=arguments.get("time_in_force", "DAY"),
                        )
                    result_text = json.dumps(payload, indent=2, ensure_ascii=False)

                else:
                    raise ValueError(f"Unknown tool: {tool_name}")
                    
                if isinstance(result_text, list):
                    content_list = result_text
                else:
                    content_list = [
                        {
                            "type": "text",
                            "text": str(result_text)
                        }
                    ]
                    
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": content_list
                    }
                }
                send_response(res)
                
            else:
                # Handle other/unknown requests safely
                if req_id is not None:
                    res = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {method}"
                        }
                    }
                    send_response(res)
                    
        except Exception as e:
            try:
                err_msg = str(e)
                if os.getenv("NOSLIP_MCP_DEBUG_ERRORS", "").strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                }:
                    err_msg += "\n" + traceback.format_exc()
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id if 'req_id' in locals() else None,
                    "error": {
                        "code": -32000,
                        "message": err_msg
                    }
                }
                send_response(res)
            except Exception:
                pass

if __name__ == "__main__":
    main()
