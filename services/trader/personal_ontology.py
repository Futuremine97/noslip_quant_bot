#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sqlite3
import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

ROOT_DIR = Path(__file__).resolve().parents[2]
if not ROOT_DIR.exists() or not (ROOT_DIR / "services" / "trader").exists():
    ROOT_DIR = Path(__file__).resolve().parents[2]

DB_PATH = ROOT_DIR / "services" / "trader" / "model_cache" / "personal_ontology.sqlite3"

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS personal_ontology (
                user_id TEXT,
                concept_name TEXT,
                description TEXT,
                symbols TEXT,
                rules_json TEXT,
                updated_at TEXT,
                PRIMARY KEY (user_id, concept_name)
            )
        """)
        conn.commit()

def save_concept(user_id: str, concept_name: str, description: str, symbols: list, rules: dict) -> dict:
    init_db()
    updated_at = datetime.now().isoformat()
    # Normalize inputs
    symbols = symbols or []
    rules = rules or {}
    symbols_str = json.dumps(symbols)
    rules_str = json.dumps(rules)
    
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO personal_ontology (user_id, concept_name, description, symbols, rules_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, concept_name) DO UPDATE SET
                description = excluded.description,
                symbols = excluded.symbols,
                rules_json = excluded.rules_json,
                updated_at = excluded.updated_at
        """, (user_id, concept_name, description, symbols_str, rules_str, updated_at))
        conn.commit()
        
    return {
        "user_id": user_id,
        "concept_name": concept_name,
        "description": description,
        "symbols": symbols,
        "rules": rules,
        "updated_at": updated_at
    }

def get_concepts(user_id: str) -> list:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT concept_name, description, symbols, rules_json, updated_at
            FROM personal_ontology
            WHERE user_id = ?
            ORDER BY concept_name ASC
        """, (user_id,)).fetchall()
        
    result = []
    for r in rows:
        result.append({
            "concept_name": r["concept_name"],
            "description": r["description"],
            "symbols": json.loads(r["symbols"]),
            "rules": json.loads(r["rules_json"]),
            "updated_at": r["updated_at"]
        })
    return result

def delete_concept(user_id: str, concept_name: str) -> bool:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("""
            DELETE FROM personal_ontology
            WHERE user_id = ? AND concept_name = ?
        """, (user_id, concept_name))
        conn.commit()
        return cursor.rowcount > 0

def evaluate_ontology_concept(user_id: str, concept_name: str) -> str:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("""
            SELECT description, symbols, rules_json, updated_at
            FROM personal_ontology
            WHERE user_id = ? AND concept_name = ?
        """, (user_id, concept_name)).fetchone()
        
    if not row:
        return f"⚠️ Concept '{concept_name}' not found for user '{user_id}'."
        
    description = row["description"]
    symbols = json.loads(row["symbols"])
    rules = json.loads(row["rules_json"])
    
    if not symbols:
        return (
            f"🎯 <b>Personal Ontology Concept: {concept_name}</b>\n"
            f"Description: {description}\n"
            f"⚠️ No symbols/tickers mapped to this concept yet."
        )
        
    # Lazy imports from telegram_interactive_bot to avoid circular reference issues
    from telegram_interactive_bot import fetch_ticker_data, normalize_symbol
    
    report_lines = [
        f"🎯 <b>Personal Ontology Evaluation: {concept_name}</b>",
        f"<i>Description: {description}</i>",
        "="*55,
        f"👤 <b>User ID</b>: <code>{user_id}</code> | 📅 <b>Time</b>: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "⚙️ <b>Configured Constraints</b>:"
    ]
    
    for k, v in rules.items():
        report_lines.append(f"  • <code>{k}</code>: {v}")
    report_lines.append("="*55)
    
    # Load latest info map coordinates if available
    latest_map_path = ROOT_DIR / "services" / "trader" / "model_cache" / "sp500_information_maps" / "latest.json"
    latest_map_data = {}
    if latest_map_path.exists():
        try:
            with open(latest_map_path, "r", encoding="utf-8") as f:
                latest_map_data = json.load(f)
        except Exception:
            pass
            
    points_dict = {p["symbol"]: p for p in latest_map_data.get("points", []) if "symbol" in p}
    
    for symbol in symbols:
        norm_sym = normalize_symbol(symbol)
        report_lines.append(f"🔍 <b>Asset: {norm_sym}</b>")
        
        # Pull 60 days of historical data
        df = fetch_ticker_data(norm_sym)
        if df.empty or len(df) < 20:
            report_lines.append("  ⚠️ Error: Could not fetch sufficient market data from yfinance.")
            report_lines.append("-" * 40)
            continue
            
        cur_price = float(df["Close"].iloc[-1])
        report_lines.append(f"  • Live Price: ${cur_price:,.2f}")
        
        # Calculate RSI (14 days)
        delta = df["Close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs.iloc[-1])) if rs.iloc[-1] is not None and not pd.isna(rs.iloc[-1]) else 50.0
        
        # Calculate SMA 20
        sma_20 = float(df["Close"].rolling(window=20).mean().iloc[-1])
        price_above_sma = cur_price > sma_20
        
        # Fetch information map points
        map_point = points_dict.get(norm_sym)
        info_x, info_y, final_action = None, None, None
        if map_point:
            coords = map_point.get("firstCoordinateSpace")
            if coords:
                info_x = coords.get("x")
                info_y = coords.get("y")
            final_action = map_point.get("finalAction")
            
        # Rules audit check
        passed_rules = []
        failed_rules = []
        
        if "min_price" in rules:
            target = float(rules["min_price"])
            if cur_price >= target:
                passed_rules.append(f"Price (${cur_price:,.2f} >= ${target:,.2f})")
            else:
                failed_rules.append(f"Price (${cur_price:,.2f} < ${target:,.2f})")
                
        if "max_price" in rules:
            target = float(rules["max_price"])
            if cur_price <= target:
                passed_rules.append(f"Price (${cur_price:,.2f} <= ${target:,.2f})")
            else:
                failed_rules.append(f"Price (${cur_price:,.2f} > ${target:,.2f})")
                
        if "min_rsi" in rules:
            target = float(rules["min_rsi"])
            if rsi >= target:
                passed_rules.append(f"RSI ({rsi:.1f} >= {target:.1f})")
            else:
                failed_rules.append(f"RSI ({rsi:.1f} < {target:.1f})")
                
        if "max_rsi" in rules:
            target = float(rules["max_rsi"])
            if rsi <= target:
                passed_rules.append(f"RSI ({rsi:.1f} <= {target:.1f})")
            else:
                failed_rules.append(f"RSI ({rsi:.1f} > {target:.1f})")
                
        if rules.get("require_price_above_sma20") is True:
            if price_above_sma:
                passed_rules.append(f"SMA 20 (Price ${cur_price:,.2f} > SMA20 ${sma_20:,.2f})")
            else:
                failed_rules.append(f"SMA 20 (Price ${cur_price:,.2f} <= SMA20 ${sma_20:,.2f})")
                
        if "min_momentum" in rules:
            target = float(rules["min_momentum"])
            if info_x is not None:
                if info_x >= target:
                    passed_rules.append(f"Momentum X ({info_x:+.3f} >= {target:+.3f})")
                else:
                    failed_rules.append(f"Momentum X ({info_x:+.3f} < {target:+.3f})")
            else:
                failed_rules.append(f"Momentum X (Info Map coordinate missing)")
                
        if "max_volatility" in rules:
            target = float(rules["max_volatility"])
            if info_y is not None:
                if info_y <= target:
                    passed_rules.append(f"Volatility Y ({info_y:.3f} <= {target:.3f})")
                else:
                    failed_rules.append(f"Volatility Y ({info_y:.3f} > {target:.3f})")
            else:
                failed_rules.append(f"Volatility Y (Info Map coordinate missing)")
                
        if "expected_action" in rules:
            target = str(rules["expected_action"]).upper()
            if final_action is not None:
                if final_action.upper() == target:
                    passed_rules.append(f"Expected Action ({final_action} == {target})")
                else:
                    failed_rules.append(f"Expected Action ({final_action} != {target})")
            else:
                failed_rules.append(f"Expected Action (Consensus finalAction missing)")
                
        report_lines.append(f"  • Stats: RSI={rsi:.1f} | SMA20=${sma_20:,.2f} | Info-X={info_x if info_x is not None else 'N/A'} | Info-Y={info_y if info_y is not None else 'N/A'}")
        
        if not passed_rules and not failed_rules:
            report_lines.append("  • ⚖️ <b>Stance: NEUTRAL</b> (No rules matched or data missing)")
        elif not failed_rules:
            report_lines.append("  • 🟢 <b>Stance: PASSED</b> (All user rules satisfied)")
            for pr in passed_rules:
                report_lines.append(f"    └ Passed: {pr}")
        else:
            report_lines.append("  • 🔴 <b>Stance: FAILED</b> (Constraints violated)")
            for pr in passed_rules:
                report_lines.append(f"    └ Passed: {pr}")
            for fr in failed_rules:
                report_lines.append(f"    └ Violated: {fr}")
                
        report_lines.append("-" * 40)
        
    return "\n".join(report_lines)
