#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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

# Lazy imports to prevent startup delays
def get_analysis_tool(symbol: str) -> str:
    from telegram_interactive_bot import execute_analysis, normalize_symbol
    normalized = normalize_symbol(symbol)
    return execute_analysis(normalized)

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
                            "description": "Analyze a stock (e.g. INTC, TSLA, NVDA) or crypto (e.g. BTC, ETH, SOL) symbol using the 6-Agent Consensus model.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "symbol": {
                                  "type": "string",
                                  "description": "The stock or crypto symbol to analyze."
                                }
                              },
                              "required": ["symbol"]
                            }
                          },
                          {
                            "name": "run_league_tournament",
                            "description": "Run the daily AI & Quant bot tournament backtest league to compare top open-source strategies.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {}
                            }
                          },
                          {
                            "name": "configure_personal_ontology",
                            "description": "Configure or update a personalized investment concept/category with a list of symbols and target evaluation constraints.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "concept_name": {
                                  "type": "string",
                                  "description": "Unique name for the concept (e.g., 'Quantum Computing' or 'Value Tech')."
                                },
                                "description": {
                                  "type": "string",
                                  "description": "A description of this personalized category."
                                },
                                "symbols": {
                                  "type": "array",
                                  "items": { "type": "string" },
                                  "description": "List of tickers/symbols to map to this concept."
                                },
                                "rules": {
                                  "type": "object",
                                  "description": "Custom audit rules. Supported: min_price (number), max_price (number), min_rsi (number), max_rsi (number), require_price_above_sma20 (boolean), min_momentum (number, e.g. 0.05), max_volatility (number, e.g. 5.0), expected_action (string, 'BUY'/'SELL'/'HOLD')."
                                },
                                "user_id": {
                                  "type": "string",
                                  "description": "User identifier to isolate configs. Defaults to 'default'."
                                }
                              },
                              "required": ["concept_name"]
                            }
                          },
                          {
                            "name": "get_personal_ontology",
                            "description": "Retrieve all saved personalized ontology concepts and rules for a user.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "user_id": {
                                  "type": "string",
                                  "description": "User identifier. Defaults to 'default'."
                                }
                              }
                            }
                          },
                          {
                            "name": "delete_personal_ontology_concept",
                            "description": "Delete a personalized ontology concept from the user config.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "concept_name": {
                                  "type": "string",
                                  "description": "The name of the concept to delete."
                                },
                                "user_id": {
                                  "type": "string",
                                  "description": "User identifier. Defaults to 'default'."
                                }
                              },
                              "required": ["concept_name"]
                            }
                          },
                          {
                            "name": "analyze_by_personal_ontology",
                            "description": "Fetch live market metrics for symbols in a personalized concept and audit them against custom constraints.",
                            "inputSchema": {
                              "type": "object",
                              "properties": {
                                "concept_name": {
                                  "type": "string",
                                  "description": "The name of the personalized concept to analyze."
                                },
                                "user_id": {
                                  "type": "string",
                                  "description": "User identifier. Defaults to 'default'."
                                }
                              },
                              "required": ["concept_name"]
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
                    result_text = get_analysis_tool(sym)
                    
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
                    
                else:
                    raise ValueError(f"Unknown tool: {tool_name}")
                    
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": result_text
                            }
                        ]
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
