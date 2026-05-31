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
