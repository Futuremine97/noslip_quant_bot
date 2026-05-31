#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import sys
import time
import sqlite3
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

# Set directories
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv(dotenv_path=ROOT_DIR / ".env")

CACHE_DIR = ROOT_DIR / "services" / "trader" / "model_cache"
CACHE_FILE = CACHE_DIR / "whale_config.json"
RESULTS_FILE = CACHE_DIR / "monthly_optimization_results.json"
KLINE_CACHE_DIR = ROOT_DIR / "data" / "kline_cache"
BINANCE_URL = "https://api.binance.com/api/v3/klines"

# Emojis for Telegram formatting
STRATEGY_EMOJIS = {
    "whale_pump": "🐳",
    "rsi_reversion": "🟢",
    "macd_crossover": "🚀",
    "bb_breakout": "💥",
    "spot_arbitrage": "⚖️",
    "kimchi_arbitrage": "🇰🇷"
}

STRATEGY_NAMES = {
    "whale_pump": "고래 수급 매매",
    "rsi_reversion": "RSI 과매도 반등",
    "macd_crossover": "MACD 골든크로스",
    "bb_breakout": "BB 변동성 돌파",
    "spot_arbitrage": "거래소 차익거래",
    "kimchi_arbitrage": "김치프리미엄 차익거래"
}

# ------------------ Telegram Notification Helper ------------------
def send_telegram_message(text: str) -> bool:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_ids_str = os.getenv("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_ids_str:
        print("⚠️ Telegram settings missing (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")
        return False
        
    chat_ids = [cid.strip() for cid in chat_ids_str.split(",") if cid.strip()]
    success = True
    
    import urllib.request
    for cid in chat_ids:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": cid,
            "text": text,
            "parse_mode": "HTML"
        }
        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                url, 
                data=data, 
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                response.read()
                print(f"✅ [Telegram] {cid} message sent successfully!")
        except Exception as e:
            print(f"❌ [Telegram] {cid} send failed: {e}")
            success = False
            
    return success

# ------------------ Kline Downloader & Caching ------------------
def fetch_binance_klines(symbol: str, interval: str, limit: int, start_time_ms: int = None, end_time_ms: int = None) -> list:
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }
    if start_time_ms:
        params["startTime"] = start_time_ms
    if end_time_ms:
        params["endTime"] = end_time_ms
        
    try:
        res = requests.get(BINANCE_URL, params=params, timeout=10)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        print(f"⚠️ Error fetching {symbol} klines: {e}")
        return []

def get_monthly_data(symbol: str, year: int, month: int) -> pd.DataFrame:
    """Get 1m kline data for a specific calendar month, loading from cache or downloading."""
    KLINE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = KLINE_CACHE_DIR / f"{symbol}_{year}_{month:02d}.csv"
    
    # Check if this is the current active month
    now = datetime.now(timezone.utc)
    is_current_month = (year == now.year and month == now.month)
    
    if cache_path.exists() and not is_current_month:
        # Load from cache
        try:
            df = pd.read_csv(cache_path)
            df["open_time"] = pd.to_datetime(df["open_time"])
            print(f"📁 Loaded cached data for {symbol} ({year}-{month:02d}) - {len(df)} rows.")
            return df
        except Exception as e:
            print(f"⚠️ Error reading cache file {cache_path}: {e}. Redownloading...")
            
    # Download month data
    start_dt = datetime(year, month, 1, tzinfo=timezone.utc)
    # End datetime is the first day of next month (or now if current month)
    if month == 12:
        end_dt = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end_dt = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        
    if end_dt > now:
        end_dt = now
        
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    
    print(f"📥 Downloading historical 1m data for {symbol} ({year}-{month:02d}) from Binance...")
    all_klines = []
    current_start = start_ms
    
    while current_start < end_ms:
        klines = fetch_binance_klines(symbol, "1m", 1000, current_start, end_ms)
        if not klines:
            break
        all_klines.extend(klines)
        # Set next start to open time of last kline + 60,000 ms
        last_kline_time = klines[-1][0]
        if last_kline_time <= current_start:
            break
        current_start = last_kline_time + 60000
        time.sleep(0.1) # Respectful sleep
        
    if not all_klines:
        return pd.DataFrame()
        
    df = pd.DataFrame(all_klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "count", "taker_buy_volume",
        "taker_buy_quote_volume", "ignore"
    ])
    
    # Cast fields
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    
    # Sort and remove duplicates
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    
    # Save to cache (only if it is a completed month)
    if not is_current_month:
        try:
            df.to_csv(cache_path, index=False)
            print(f"💾 Saved {symbol} ({year}-{month:02d}) data to cache: {cache_path}")
        except Exception as e:
            print(f"⚠️ Failed to write cache: {e}")
            
    return df

# ------------------ Ultra-Fast Numpy Backtesting Engines ------------------

def backtest_whale_pump(closes, volumes, lows, highs, vol_mas, M, X, V, H, SL, TP) -> dict:
    trades = []
    df_len = len(closes)
    
    i = max(M, 30) + 1
    while i < df_len - H:
        vol_ma = vol_mas[i-1]
        if vol_ma == 0:
            i += 1
            continue
            
        vol_ratio = volumes[i] / vol_ma
        close_t = closes[i]
        close_prev = closes[i - M]
        price_change = ((close_t / close_prev) - 1.0) * 100.0
        
        if price_change >= X and vol_ratio >= V:
            entry_price = close_t
            profit = 0.0
            for j in range(1, H + 1):
                idx = i + j
                low_j = lows[idx]
                high_j = highs[idx]
                sl_price = entry_price * (1.0 - SL / 100.0)
                if low_j <= sl_price:
                    profit = -SL
                    break
                tp_price = entry_price * (1.0 + TP / 100.0)
                if high_j >= tp_price:
                    profit = TP
                    break
            else:
                exit_price = closes[i + H]
                profit = ((exit_price / entry_price) - 1.0) * 100.0
                
            trades.append(profit)
            i += H
        else:
            i += 1
            
    if not trades:
        return {"net_profit": 0.0, "trade_count": 0, "win_rate": 0.0}
    return {"net_profit": sum(trades), "trade_count": len(trades), "win_rate": (sum(1 for t in trades if t > 0) / len(trades)) * 100.0}


def backtest_rsi_reversion(closes, lows, highs, rsis, rsi_trigger, H, SL, TP) -> dict:
    trades = []
    df_len = len(closes)
    
    i = 15
    while i < df_len - H:
        curr_rsi = rsis[i]
        prev_rsi = rsis[i-1]
        
        if curr_rsi < rsi_trigger and curr_rsi > prev_rsi:
            entry_price = closes[i]
            profit = 0.0
            for j in range(1, H + 1):
                idx = i + j
                low_j = lows[idx]
                high_j = highs[idx]
                sl_price = entry_price * (1.0 - SL / 100.0)
                if low_j <= sl_price:
                    profit = -SL
                    break
                tp_price = entry_price * (1.0 + TP / 100.0)
                if high_j >= tp_price:
                    profit = TP
                    break
            else:
                exit_price = closes[i + H]
                profit = ((exit_price / entry_price) - 1.0) * 100.0
                
            trades.append(profit)
            i += H
        else:
            i += 1
            
    if not trades:
        return {"net_profit": 0.0, "trade_count": 0, "win_rate": 0.0}
    return {"net_profit": sum(trades), "trade_count": len(trades), "win_rate": (sum(1 for t in trades if t > 0) / len(trades)) * 100.0}


def backtest_macd_crossover(closes, lows, highs, macds, macd_signals, volumes, vol_mas, vol_confirm, H, SL, TP) -> dict:
    trades = []
    df_len = len(closes)
    
    i = 35
    while i < df_len - H:
        curr_macd = macds[i]
        curr_sig = macd_signals[i]
        prev_macd = macds[i-1]
        prev_sig = macd_signals[i-1]
        vol_ratio = volumes[i] / (vol_mas[i-1] if vol_mas[i-1] > 0 else 1.0)
        
        if prev_macd <= prev_sig and curr_macd > curr_sig and vol_ratio >= vol_confirm:
            entry_price = closes[i]
            profit = 0.0
            for j in range(1, H + 1):
                idx = i + j
                low_j = lows[idx]
                high_j = highs[idx]
                sl_price = entry_price * (1.0 - SL / 100.0)
                if low_j <= sl_price:
                    profit = -SL
                    break
                tp_price = entry_price * (1.0 + TP / 100.0)
                if high_j >= tp_price:
                    profit = TP
                    break
            else:
                exit_price = closes[i + H]
                profit = ((exit_price / entry_price) - 1.0) * 100.0
                
            trades.append(profit)
            i += H
        else:
            i += 1
            
    if not trades:
        return {"net_profit": 0.0, "trade_count": 0, "win_rate": 0.0}
    return {"net_profit": sum(trades), "trade_count": len(trades), "win_rate": (sum(1 for t in trades if t > 0) / len(trades)) * 100.0}


def backtest_bb_breakout(closes, lows, highs, bandwidths, min30_bandwidths, uppers, squeeze_ratio, H, SL, TP) -> dict:
    trades = []
    df_len = len(closes)
    
    i = 50
    while i < df_len - H:
        curr_close = closes[i]
        prev_close = closes[i-1]
        curr_upper = uppers[i]
        prev_upper = uppers[i-1]
        curr_bw = bandwidths[i]
        min30_bw = min30_bandwidths[i]
        
        is_squeezed = (min30_bw > 0) and (curr_bw <= min30_bw * squeeze_ratio)
        is_breakout = (curr_close > curr_upper) and (prev_close <= prev_upper)
        
        if is_squeezed and is_breakout:
            entry_price = closes[i]
            profit = 0.0
            for j in range(1, H + 1):
                idx = i + j
                low_j = lows[idx]
                high_j = highs[idx]
                sl_price = entry_price * (1.0 - SL / 100.0)
                if low_j <= sl_price:
                    profit = -SL
                    break
                tp_price = entry_price * (1.0 + TP / 100.0)
                if high_j >= tp_price:
                    profit = TP
                    break
            else:
                exit_price = closes[i + H]
                profit = ((exit_price / entry_price) - 1.0) * 100.0
                
            trades.append(profit)
            i += H
        else:
            i += 1
            
    if not trades:
        return {"net_profit": 0.0, "trade_count": 0, "win_rate": 0.0}
    return {"net_profit": sum(trades), "trade_count": len(trades), "win_rate": (sum(1 for t in trades if t > 0) / len(trades)) * 100.0}

# ------------------ Strategy Indicators Calculator ------------------
def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close_series = df["close"]
    volume_series = df["volume"]
    
    # RSI 14
    delta = close_series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    df["rsi"] = 100 - (100 / (1 + rs))
    
    # MACD
    df["ema12"] = close_series.ewm(span=12, adjust=False).mean()
    df["ema26"] = close_series.ewm(span=26, adjust=False).mean()
    df["macd"] = df["ema12"] - df["ema26"]
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    
    # Bollinger Bands
    df["bb_mid"] = close_series.rolling(window=20).mean()
    df["bb_std"] = close_series.rolling(window=20).std()
    df["bb_upper"] = df["bb_mid"] + 2.0 * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - 2.0 * df["bb_std"]
    df["bb_bandwidth"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"].replace(0, 1e-9)
    df["bb_bandwidth_min30"] = df["bb_bandwidth"].shift(1).rolling(window=30).min()
    
    # Volume MA
    df["vol_ma"] = volume_series.rolling(30).mean()
    return df

# ------------------ Monthly Strategy Grid Search ------------------
def optimize_monthly(symbol: str, year: int, month: int) -> dict:
    df = get_monthly_data(symbol, year, month)
    if df.empty or len(df) < 200:
        print(f"⚠️ Insufficient kline data for {symbol} ({year}-{month:02d}).")
        return {}
        
    print(f"📊 Running optimization for {symbol} in {year}-{month:02d} ({len(df)} 1m rows)...")
    df = calculate_indicators(df)
    
    # Prepare Numpy arrays for speed
    closes = df["close"].values
    lows = df["low"].values
    highs = df["high"].values
    volumes = df["volume"].values
    vol_mas = df["vol_ma"].values
    rsis = df["rsi"].values
    macds = df["macd"].values
    macd_signals = df["macd_signal"].values
    bb_bandwidths = df["bb_bandwidth"].values
    min30_bws = df["bb_bandwidth_min30"].values
    bb_uppers = df["bb_upper"].values
    
    min_trades = 3
    results = {}
    
    # --- 1. Optimize whale_pump ---
    print("  🐳 Optimizing Whale Pump...")
    best_whale = {"net_profit": -9999.0, "trade_count": 0, "win_rate": 0.0, "params": {}}
    for M in [2, 3, 5]:
        for X in [0.4, 0.7, 1.1]:
            for V in [1.8, 2.3, 3.2]:
                for H in [15, 30, 45]:
                    for SL in [0.5, 1.0, 1.5]:
                        for TP in [1.5, 3.0]:
                            res = backtest_whale_pump(closes, volumes, lows, highs, vol_mas, M, X, V, H, SL, TP)
                            if res["trade_count"] >= min_trades and res["net_profit"] > best_whale["net_profit"]:
                                best_whale = {**res, "params": {"M": M, "X": X, "V": V, "H": H, "SL": SL, "TP": TP}}
    if best_whale["trade_count"] >= min_trades:
        results["whale_pump"] = best_whale
        
    # --- 2. Optimize rsi_reversion ---
    print("  🟢 Optimizing RSI Reversion...")
    best_rsi = {"net_profit": -9999.0, "trade_count": 0, "win_rate": 0.0, "params": {}}
    for rsi_trigger in [20.0, 25.0, 30.0]:
        for H in [10, 15, 30]:
            for SL in [0.3, 0.5, 1.0]:
                for TP in [0.5, 1.0, 2.0]:
                    res = backtest_rsi_reversion(closes, lows, highs, rsis, rsi_trigger, H, SL, TP)
                    if res["trade_count"] >= min_trades and res["net_profit"] > best_rsi["net_profit"]:
                        best_rsi = {**res, "params": {"rsi_trigger": rsi_trigger, "H": H, "SL": SL, "TP": TP}}
    if best_rsi["trade_count"] >= min_trades:
        results["rsi_reversion"] = best_rsi
        
    # --- 3. Optimize macd_crossover ---
    print("  🚀 Optimizing MACD Crossover...")
    best_macd = {"net_profit": -9999.0, "trade_count": 0, "win_rate": 0.0, "params": {}}
    for vol_confirm in [0.8, 1.0, 1.5]:
        for H in [30, 60, 90]:
            for SL in [0.5, 1.0]:
                for TP in [1.0, 2.0]:
                    res = backtest_macd_crossover(closes, lows, highs, macds, macd_signals, volumes, vol_mas, vol_confirm, H, SL, TP)
                    if res["trade_count"] >= min_trades and res["net_profit"] > best_macd["net_profit"]:
                        best_macd = {**res, "params": {"vol_confirm": vol_confirm, "H": H, "SL": SL, "TP": TP}}
    if best_macd["trade_count"] >= min_trades:
        results["macd_crossover"] = best_macd
        
    # --- 4. Optimize bb_breakout ---
    print("  💥 Optimizing BB Breakout...")
    best_bb = {"net_profit": -9999.0, "trade_count": 0, "win_rate": 0.0, "params": {}}
    for squeeze_ratio in [1.08, 1.15, 1.22]:
        for H in [15, 30, 60]:
            for SL in [0.5, 1.0]:
                for TP in [1.0, 2.5]:
                    res = backtest_bb_breakout(closes, lows, highs, bb_bandwidths, min30_bws, bb_uppers, squeeze_ratio, H, SL, TP)
                    if res["trade_count"] >= min_trades and res["net_profit"] > best_bb["net_profit"]:
                        best_bb = {**res, "params": {"squeeze_ratio": squeeze_ratio, "H": H, "SL": SL, "TP": TP}}
    if best_bb["trade_count"] >= min_trades:
        results["bb_breakout"] = best_bb
        
    return results

# ------------------ Core Pipeline Runner ------------------
def run_optimization_pipeline():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    
    # Determine the past 3 calendar months (current month and 2 preceding months)
    now = datetime.now(timezone.utc)
    months = []
    current_year = now.year
    current_month = now.month
    
    for i in range(3):
        m = current_month - i
        y = current_year
        if m <= 0:
            m += 12
            y -= 1
        months.append((y, m))
        
    # Keep in chronological order
    months = months[::-1]
    
    print(f"🚀 Running optimization for months: {months}")
    
    all_optimizations = {}
    if RESULTS_FILE.exists():
        try:
            with open(RESULTS_FILE, "r") as f:
                all_optimizations = json.load(f)
        except Exception:
            pass
            
    # Gather monthly optimizations
    for sym in symbols:
        if sym not in all_optimizations:
            all_optimizations[sym] = {}
            
        for yr, m in months:
            m_key = f"{yr}-{m:02d}"
            results = optimize_monthly(sym, yr, m)
            if results:
                all_optimizations[sym][m_key] = results
                
    # Save results history
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(all_optimizations, f, indent=4)
    print(f"💾 All monthly optimization history saved to {RESULTS_FILE}")
    
    # Load current active config
    config = {}
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r") as f:
                config = json.load(f)
        except Exception:
            pass
            
    # Apply latest month's optimal parameters to active config
    latest_month_key = f"{months[-1][0]}-{months[-1][1]:02d}"
    print(f"🛠️ Applying optimal parameters from the most recent month ({latest_month_key}) to active configuration...")
    
    report_data = []
    
    for sym in symbols:
        symbol_opts = all_optimizations.get(sym, {}).get(latest_month_key, {})
        if not symbol_opts:
            print(f"⚠️ No optimization data for {sym} in {latest_month_key}. Skipping update.")
            continue
            
        # Determine the winner strategy based on net_profit
        winner_strategy = None
        max_profit = -9999.0
        
        for strat, details in symbol_opts.items():
            if details["net_profit"] > max_profit:
                max_profit = details["net_profit"]
                winner_strategy = strat
                
        if not winner_strategy:
            print(f"⚠️ No profitable strategy found for {sym} in {latest_month_key}.")
            continue
            
        # Update symbol config with new optimal parameters
        if sym not in config:
            config[sym] = {}
            
        # Store individual strategy parameters in config
        for strat, details in symbol_opts.items():
            # Special keys format compatibility with legacy format
            if strat == "whale_pump":
                p = details["params"]
                config[sym]["M"] = p["M"]
                config[sym]["X"] = p["X"]
                config[sym]["V"] = p["V"]
                config[sym]["H"] = p["H"]
                config[sym]["SL"] = p["SL"]
                config[sym]["TP"] = p["TP"]
                config[sym]["backtest_trades"] = details["trade_count"]
                config[sym]["backtest_profit"] = f"{details['net_profit']:.2f}%"
                config[sym]["backtest_win_rate"] = f"{details['win_rate']:.1f}%"
            else:
                config[sym][strat] = details["params"]
                
        # Format metrics to add to report
        report_data.append({
            "symbol": sym,
            "winner_strategy": winner_strategy,
            "winner_details": symbol_opts[winner_strategy],
            "all_strategies": symbol_opts
        })
        
    # Write updated config back to whale_config.json
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)
    print(f"💾 Updated active parameter config: {CACHE_FILE}")
    
    # ------------------ Telegram Report Dispatch ------------------
    msg = []
    msg.append("📊 <b>[No Slip Quant] 자산별 월간 최적 전략 학습 리포트</b>")
    msg.append("=" * 40)
    msg.append(f"📅 <b>학습 대상 월</b>: {latest_month_key}")
    msg.append("AI 퀀트 에이전트가 최근 1개월간의 1분 단위 kline 데이터를 기반으로 각 매매 전략의 파라미터를 시뮬레이션 및 월간 최적화한 결과입니다.")
    msg.append("\n" + "=" * 40)
    
    for item in report_data:
        sym = item["symbol"]
        display_sym = sym.replace("USDT", "")
        winner = item["winner_strategy"]
        win_details = item["winner_details"]
        win_emoji = STRATEGY_EMOJIS.get(winner, "✨")
        win_name = STRATEGY_NAMES.get(winner, winner)
        
        msg.append(f"📈 <b>{display_sym} 최적 포트폴리오 전략</b>")
        msg.append(f"  • <b>월간 종합 우수 전략</b>: {win_emoji} <b>{win_name}</b>")
        msg.append(f"  • <b>수익률 (Net Profit)</b>: <b>{win_details['net_profit']:+.2f}%</b>")
        msg.append(f"  • <b>거래 횟수</b>: {win_details['trade_count']}회 | <b>승률</b>: {win_details['win_rate']:.1f}%")
        
        # Show parameters
        p_str = ", ".join([f"{k}: {v}" for k, v in win_details["params"].items()])
        msg.append(f"  • <b>최적 파라미터</b>: <code>{p_str}</code>")
        
        msg.append("\n  <b>🔍 전략별 백테스트 상세 현황</b>")
        for strat, details in item["all_strategies"].items():
            strat_emoji = STRATEGY_EMOJIS.get(strat, "⚙️")
            strat_name = STRATEGY_NAMES.get(strat, strat)
            msg.append(f"    {strat_emoji} {strat_name}: {details['net_profit']:+.2f}% ({details['trade_count']}회 | 승률 {details['win_rate']:.1f}%)")
        msg.append("\n" + "-" * 40)
        
    msg.append("💡 <b>알림 해석 및 운용 가이드</b>")
    msg.append("• 월간 백테스트에서 가장 높은 누적 수익률을 기록한 전략이 종합 우수 전략으로 선정됩니다.")
    msg.append("• 1분 단위 백테스트 복기를 통해 도출된 파라미터 설정은 실시간 매매 데몬(com.noslip.whale)에 즉각 반영되어 운용됩니다.")
    msg.append("\n" + "=" * 40)
    msg.append("※ 본 최적화 학습은 백테스트 검증에 따른 결과물이며 자동으로 상시 튜닝됩니다.")
    
    send_telegram_message("\n".join(msg))

if __name__ == "__main__":
    run_optimization_pipeline()
