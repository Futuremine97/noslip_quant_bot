#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sqlite3
import json
import os
import requests
from pathlib import Path
from datetime import datetime

ROOT_DIR = Path(__file__).resolve().parents[2]
if not ROOT_DIR.exists() or not (ROOT_DIR / "services" / "trader").exists():
    ROOT_DIR = Path(__file__).resolve().parents[2]

REGISTRY_PATH = ROOT_DIR / "services" / "trader" / "model_cache" / "model_registry.sqlite3"

def get_prediction_api_url() -> str:
    url = os.getenv("PREDICTION_API_URL", "").strip()
    if not url:
        return "http://localhost:8000"
    return url.rstrip("/")

def get_headers() -> dict:
    token = os.getenv("PREDICTION_API_TOKEN", "").strip()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

def sanitize_float(val, fallback=0.0) -> float:
    import math
    try:
        fval = float(val)
        if math.isinf(fval) or math.isnan(fval):
            return fallback
        return fval
    except (TypeError, ValueError):
        return fallback

def sync_local_champions_to_leaderboard(bot_id: str) -> dict:
    if not REGISTRY_PATH.exists():
        return {"error": "Local model registry database not found."}
        
    try:
        with sqlite3.connect(REGISTRY_PATH) as conn:
            rows = conn.execute("""
                SELECT symbol, metrics_json, updated_at
                FROM model_documents
            """).fetchall()
    except Exception as e:
        return {"error": f"Failed to query local registry database: {e}"}
        
    submitted = []
    errors = []
    
    api_url = get_prediction_api_url()
    submit_url = f"{api_url}/leaderboard/submit"
    headers = get_headers()
    
    for symbol, metrics_json, updated_at in rows:
        try:
            metrics = json.loads(metrics_json)
            mae = metrics.get("mae")
            rmse = metrics.get("rmse")
            dir_acc = metrics.get("directional_accuracy")
            comp_score = metrics.get("composite_score")
            folds = metrics.get("folds", 0)
            
            # Sanitize metrics
            sanitized_mae = sanitize_float(mae, fallback=999999.0)
            sanitized_rmse = sanitize_float(rmse, fallback=999999.0)
            sanitized_dir_acc = sanitize_float(dir_acc, fallback=0.0)
            sanitized_comp_score = sanitize_float(comp_score, fallback=999999.0)
            
            payload = {
                "bot_id": bot_id,
                "symbol": symbol,
                "mae": sanitized_mae,
                "rmse": sanitized_rmse,
                "directional_accuracy": sanitized_dir_acc,
                "composite_score": sanitized_comp_score,
                "cv_folds": int(folds) if folds is not None else 0,
                "updated_at": updated_at
            }
            
            res = requests.post(submit_url, json=payload, headers=headers, timeout=10)
            res.raise_for_status()
            submitted.append(symbol)
        except Exception as e:
            errors.append(f"{symbol}: {e}")
            
    return {
        "bot_id": bot_id,
        "submitted_symbols": submitted,
        "errors": errors
    }

def fetch_leaderboard_report(symbol: str = None) -> str:
    api_url = get_prediction_api_url()
    url = f"{api_url}/leaderboard"
    if symbol:
        url += f"?symbol={symbol.upper().strip()}"
        
    headers = get_headers()
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        return f"⚠️ Failed to fetch leaderboard from central server ({api_url}): {e}"
        
    leaderboard = data.get("leaderboard", [])
    if not leaderboard:
        return f"ℹ️ Prophet Leaderboard: No entries found at this time."
        
    lines = [
        f"🏆 <b>Prophet Forecast Leaderboard</b>",
        f"<i>Central Server: {api_url}</i>",
        "="*60,
        f"Rank | Bot ID | Ticker | Composite Score | MAE | Dir. Acc | CV Folds",
        "-"*60
    ]
    
    # Group by symbol
    from collections import defaultdict
    grouped = defaultdict(list)
    for entry in leaderboard:
        grouped[entry["symbol"]].append(entry)
        
    for sym, entries in sorted(grouped.items()):
        lines.append(f"📂 <b>Asset: {sym}</b>")
        # Sort by composite score ascending (lower is better)
        sorted_entries = sorted(entries, key=lambda x: x["composite_score"])
        for rank, entry in enumerate(sorted_entries, 1):
            comp_score = entry["composite_score"]
            mae = entry["mae"]
            dir_acc = entry["directional_accuracy"]
            
            comp_str = f"{comp_score:.4f}" if comp_score != float('inf') else "Inf"
            mae_str = f"{mae:.4f}" if mae != float('inf') else "Inf"
            dir_acc_str = f"{dir_acc*100.0:.1f}%"
            
            lines.append(
                f"  #{rank} | 🤖 {entry['bot_id']} | {comp_str} | MAE {mae_str} | {dir_acc_str} | ({entry['cv_folds']}f)"
            )
        lines.append("-" * 40)
        
    return "\n".join(lines)
