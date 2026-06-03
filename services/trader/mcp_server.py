#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import sys

import json
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

def get_analysis_tool_with_graph(symbol: str) -> tuple[str, str | None]:
    from telegram_interactive_bot import execute_analysis_with_graph, normalize_symbol
    normalized = normalize_symbol(symbol)
    return execute_analysis_with_graph(normalized)

def get_tournament_tool() -> str:
    from bot_competition_tournament import run_tournament
    return run_tournament()

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
                            "description": "Run the daily AI & Quant bot tournament: a backtest league that pits the project's top open-source trading strategies against each other and ranks them by performance.\n\nUse this when the user wants to compare strategies, see which bot is currently winning, or refresh today's leaderboard standings. No input is required.\n\nReturns a ranked, human-readable summary of each strategy's backtest results (returns and relative standing). Read-only and may take longer than other tools because it runs simulations.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {}
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
                          }
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
                err_msg = str(e) + "\n" + traceback.format_exc()
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
