#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import sys
import time
import requests
import pandas as pd
import numpy as np
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT_DIR / "services" / "trader" / "model_cache"
CACHE_FILE = CACHE_DIR / "whale_config.json"

# Binance Public API Endpoint
BINANCE_URL = "https://api.binance.com/api/v3/klines"

def fetch_klines(symbol: str, interval: str = "1m", limit: int = 1000, end_time: int = None) -> list:
    """Fetch 1m kline data from Binance."""
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }
    if end_time:
        params["endTime"] = end_time
        
    try:
        res = requests.get(BINANCE_URL, params=params, timeout=10)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        print(f"⚠️ Error fetching {symbol} klines: {e}")
        return []

def get_historical_df(symbol: str, pages: int = 30) -> pd.DataFrame:
    """Fetch multiple pages of 1m data (up to pages * 1000 minutes)."""
    all_klines = []
    last_time = None
    
    print(f"📥 Fetching {pages * 1000} minutes (~{pages * 16.6:.1f} hours) of historical 1m data for {symbol}...")
    for page in range(pages):
        klines = fetch_klines(symbol, "1m", 1000, last_time)
        if not klines:
            break
        all_klines = klines + all_klines  # Keep in chronological order
        last_time = klines[0][0] - 1  # Get endTime for previous page
        time.sleep(0.3)  # Respectful sleep
        if (page + 1) % 5 == 0:
            print(f"  Downloaded page {page + 1}/{pages}...")
        
    if not all_klines:
        return pd.DataFrame()
        
    # Convert to DataFrame
    df = pd.DataFrame(all_klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "count", "taker_buy_volume",
        "taker_buy_quote_volume", "ignore"
    ])
    
    # Cast to numeric
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    return df.sort_values("open_time").reset_index(drop=True)

def backtest_whale_params_numpy(closes, volumes, lows, highs, vol_mas, M: int, X: float, V: float, H: int, SL: float, TP: float) -> dict:
    """Ultra-fast backtesting using Numpy arrays instead of Pandas series lookup."""
    trades = []
    df_len = len(closes)
    
    i = M + 30
    while i < df_len - H:
        vol_ma = vol_mas[i]
        if vol_ma == 0:
            i += 1
            continue
            
        vol_ratio = volumes[i] / vol_ma
        close_t = closes[i]
        close_prev = closes[i - M]
        price_change = ((close_t / close_prev) - 1.0) * 100.0
        
        # Trigger Condition
        if price_change >= X and vol_ratio >= V:
            entry_price = close_t
            exit_price = closes[i + H]
            profit = 0.0
            
            # Simulate trade forward
            for j in range(1, H + 1):
                idx = i + j
                low_j = lows[idx]
                high_j = highs[idx]
                
                # Check Stop-Loss
                sl_price = entry_price * (1.0 - SL / 100.0)
                if low_j <= sl_price:
                    exit_price = sl_price
                    profit = -SL
                    break
                    
                # Check Take-Profit
                tp_price = entry_price * (1.0 + TP / 100.0)
                if high_j >= tp_price:
                    exit_price = tp_price
                    profit = TP
                    break
            else:
                # Timed exit
                profit = ((exit_price / entry_price) - 1.0) * 100.0
                
            trades.append(profit)
            i += H  # Skip hold duration H
        else:
            i += 1
            
    if not trades:
        return {"net_profit": 0.0, "trade_count": 0, "win_rate": 0.0}
        
    wins = sum(1 for t in trades if t > 0)
    win_rate = (wins / len(trades)) * 100.0
    net_profit = sum(trades)
    
    return {
        "net_profit": net_profit,
        "trade_count": len(trades),
        "win_rate": win_rate
    }

def optimize_symbol(symbol: str) -> dict:
    df = get_historical_df(symbol, pages=30) # Fetch 30,000 minutes (21 days)
    if df.empty:
        print(f"⚠️ No data fetched for {symbol}.")
        return {}
        
    print(f"🔍 Optimizing parameters for {symbol} ({len(df)} rows)...")
    
    # Calculate rolling MA of volume
    df["vol_ma"] = df["volume"].rolling(30).mean().fillna(0)
    
    # Pre-extract numpy arrays for speed
    closes = df["close"].values
    volumes = df["volume"].values
    lows = df["low"].values
    highs = df["high"].values
    vol_mas = df["vol_ma"].values
    
    # Search Space
    windows = [2, 3, 5]            # M (minutes)
    price_thresholds = [0.4, 0.8, 1.2, 1.6]  # X (%)
    vol_multipliers = [2.0, 3.0, 5.0]       # V
    hold_times = [5, 10, 15, 30]           # H (minutes)
    stop_losses = [0.5, 1.0, 1.5]          # SL (%)
    take_profits = [1.0, 2.0, 3.0]         # TP (%)
    
    best_profit = -99999.0
    best_params = {}
    
    total_combinations = len(windows) * len(price_thresholds) * len(vol_multipliers) * len(hold_times) * len(stop_losses) * len(take_profits)
    count = 0
    
    # Grid Search
    start_time = time.time()
    for M in windows:
        for X in price_thresholds:
            for V in vol_multipliers:
                for H in hold_times:
                    for SL in stop_losses:
                        for TP in take_profits:
                            res = backtest_whale_params_numpy(closes, volumes, lows, highs, vol_mas, M, X, V, H, SL, TP)
                            
                            # Maximize net profit, requiring at least 3 trades in the 3-week window
                            if res["trade_count"] >= 3 and res["net_profit"] > best_profit:
                                best_profit = res["net_profit"]
                                best_params = {
                                    "M": M,
                                    "X": X,
                                    "V": V,
                                    "H": H,
                                    "SL": SL,
                                    "TP": TP,
                                    "backtest_trades": res["trade_count"],
                                    "backtest_profit": f"{res['net_profit']:.2f}%",
                                    "backtest_win_rate": f"{res['win_rate']:.1f}%"
                                }
                            count += 1
                            
    elapsed = time.time() - start_time
    print(f"⚡ Completed {count} iterations in {elapsed:.2f} seconds.")
    
    if not best_params:
        print(f"⚠️ No profitable setups found for {symbol}. Using defaults.")
        best_params = {
            "M": 3,
            "X": 0.8,
            "V": 3.0,
            "H": 15,
            "SL": 1.0,
            "TP": 2.0,
            "backtest_trades": 0,
            "backtest_profit": "N/A",
            "backtest_win_rate": "N/A"
        }
    else:
        print(f"✨ Best params found for {symbol}: {best_params}")
        
    return best_params

def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    
    config = {}
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r") as f:
                config = json.load(f)
        except Exception:
            pass
            
    for sym in symbols:
        res = optimize_symbol(sym)
        if res:
            config[sym] = res
            
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)
        
    print(f"💾 Whale parameters successfully saved to {CACHE_FILE}")

if __name__ == "__main__":
    main()
