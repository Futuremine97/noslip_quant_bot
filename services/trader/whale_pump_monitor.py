#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import sys
import time
import sqlite3
import requests
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# .env 로드
load_dotenv(dotenv_path=ROOT_DIR / ".env")

CACHE_DIR = ROOT_DIR / "services" / "trader" / "model_cache"
CACHE_FILE = CACHE_DIR / "whale_config.json"
DB_PATH = CACHE_DIR / "whale_rewards.sqlite3"
BINANCE_URL = "https://api.binance.com/api/v3/klines"

# Cooldown to avoid spam alerts (15 minutes)
ALERT_COOLDOWN_SECONDS = 15 * 60
last_alert_times = {}

# Quant Account configuration
START_CAPITAL = 10000.0   # $10,000 USD virtual starting balance
ALLOCATION_PER_TRADE = 1000.0  # $1,000 USD allocated per trade
HOURLY_REPORT_INTERVAL = 3600  # 1 hour
last_hourly_report_time = 0

def init_db():
    """Initialize SQLite database for tracking whale trades."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS whale_trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                entry_time INTEGER NOT NULL, -- Unix timestamp in seconds
                entry_price REAL NOT NULL,
                param_M INTEGER NOT NULL,
                param_X REAL NOT NULL,
                param_V REAL NOT NULL,
                param_H INTEGER NOT NULL,
                param_SL REAL NOT NULL,
                param_TP REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                exit_price REAL,
                exit_time INTEGER,
                realized_return REAL,
                reward REAL,
                strategy TEXT NOT NULL DEFAULT 'whale_pump',
                created_at TEXT NOT NULL
            )
        """)
        # Robust migration check for strategy column
        cursor = conn.execute("PRAGMA table_info(whale_trade_log)")
        columns = [row[1] for row in cursor.fetchall()]
        if "strategy" not in columns:
            conn.execute("ALTER TABLE whale_trade_log ADD COLUMN strategy TEXT NOT NULL DEFAULT 'whale_pump'")
        conn.commit()

def send_telegram_message(text: str) -> bool:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_ids_str = os.getenv("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_ids_str:
        print("⚠️ Telegram 설정 누락 (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")
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
                print(f"✅ [Telegram] {cid} 전송 완료!")
        except Exception as e:
            print(f"❌ [Telegram] {cid} 전송 실패: {e}")
            success = False
            
    return success

def load_whale_config() -> dict:
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Error reading config file: {e}")
            
    return {
        "BTCUSDT": {"M": 3, "X": 0.4, "V": 2.0, "H": 5, "SL": 0.5, "TP": 1.0},
        "ETHUSDT": {"M": 5, "X": 0.4, "V": 2.0, "H": 5, "SL": 0.5, "TP": 1.0},
        "SOLUSDT": {"M": 2, "X": 0.4, "V": 2.0, "H": 30, "SL": 0.5, "TP": 1.0}
    }

def save_whale_config(config: dict):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
        print(f"💾 Whale config dynamically updated by RL.")
    except Exception as e:
        print(f"⚠️ Failed to save whale config: {e}")

def fetch_recent_klines(symbol: str, limit: int = 60, start_time_ms: int = None) -> pd.DataFrame:
    params = {
        "symbol": symbol,
        "interval": "1m",
        "limit": limit
    }
    if start_time_ms:
        params["startTime"] = start_time_ms
        
    try:
        res = requests.get(BINANCE_URL, params=params, timeout=10)
        res.raise_for_status()
        klines = res.json()
        
        df = pd.DataFrame(klines, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "count", "taker_buy_volume",
            "taker_buy_quote_volume", "ignore"
        ])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col])
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        return df
    except Exception as e:
        print(f"⚠️ Failed to fetch klines for {symbol}: {e}")
        return pd.DataFrame()

def fetch_current_price(symbol: str) -> float:
    """Fetch the latest price for a symbol from Binance."""
    try:
        res = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}", timeout=5)
        res.raise_for_status()
        return float(res.json()["price"])
    except Exception:
        # Fallback to klines if direct price endpoint fails
        df = fetch_recent_klines(symbol, limit=1)
        if not df.empty:
            return float(df["close"].iloc[-1])
        return 0.0

# Caching USD/KRW exchange rate
usd_krw_rate_cache = 0.0
last_rate_fetch_time = 0.0
RATE_CACHE_TTL = 300 # 5 minutes

def get_usd_krw_rate() -> float:
    global usd_krw_rate_cache, last_rate_fetch_time
    now = time.time()
    if now - last_rate_fetch_time < RATE_CACHE_TTL and usd_krw_rate_cache > 0:
        return usd_krw_rate_cache
        
    try:
        import yfinance as yf
        ticker = yf.Ticker("KRW=X")
        hist = ticker.history(period="1d")
        if not hist.empty:
            rate = float(hist["Close"].iloc[-1])
            usd_krw_rate_cache = rate
            last_rate_fetch_time = now
            print(f"💵 USD/KRW exchange rate cached: {rate:.2f} KRW per USD")
            return rate
    except Exception as e:
        print(f"⚠️ Failed to fetch USD/KRW exchange rate from yfinance: {e}")
        
    if usd_krw_rate_cache > 0:
        return usd_krw_rate_cache
    return 1380.0

def fetch_upbit_price(symbol: str) -> float:
    """Fetch the latest price for a symbol from Upbit Spot in USD."""
    symbol_map = {
        "BTCUSDT": "KRW-BTC",
        "ETHUSDT": "KRW-ETH",
        "SOLUSDT": "KRW-SOL"
    }
    market = symbol_map.get(symbol)
    if not market:
        return 0.0
        
    try:
        url = f"https://api.upbit.com/v1/ticker?markets={market}"
        res = requests.get(url, timeout=5)
        res.raise_for_status()
        ticker_data = res.json()
        if ticker_data:
            trade_price_krw = float(ticker_data[0]["trade_price"])
            rate = get_usd_krw_rate()
            return trade_price_krw / rate if rate > 0 else 0.0
    except Exception as e:
        print(f"⚠️ Failed to fetch Upbit price for {symbol}: {e}")
    return 0.0

def fetch_bybit_price(symbol: str) -> float:
    """Fetch the latest price for a symbol from Bybit Spot."""
    try:
        url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}"
        res = requests.get(url, timeout=5)
        res.raise_for_status()
        data = res.json()
        if data.get("retCode") == 0 and data.get("result", {}).get("list"):
            return float(data["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print(f"⚠️ Failed to fetch Bybit price for {symbol}: {e}")
    return 0.0

def send_hourly_quant_report():
    """Calculate and broadcast the paper trading account status report."""
    print("📊 Generating hourly Quant Trader Agent report...")
    init_db()
    
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        
        # 1. Fetch completed trades metrics
        completed = conn.execute("""
            SELECT symbol, entry_price, exit_price, realized_return, status, strategy 
            FROM whale_trade_log 
            WHERE status = 'COMPLETED'
        """).fetchall()
        
        # 2. Fetch pending trades
        pending = conn.execute("""
            SELECT symbol, entry_price, entry_time, strategy 
            FROM whale_trade_log 
            WHERE status = 'PENDING'
        """).fetchall()
        
        # 3. Fetch recent history (last 5 resolved trades)
        recent_closed = conn.execute("""
            SELECT symbol, entry_price, exit_price, realized_return, created_at, entry_time, exit_time, strategy
            FROM whale_trade_log 
            WHERE status = 'COMPLETED'
            ORDER BY id DESC
            LIMIT 5
        """).fetchall()

    # Calculate statistics
    total_trades = len(completed)
    winning_trades = sum(1 for t in completed if float(t["realized_return"] or 0) > 0)
    win_rate = (winning_trades / total_trades * 100.0) if total_trades > 0 else 0.0
    
    # Cumulative realized profit (in USD)
    realized_usd_profit = sum((float(t["realized_return"] or 0) / 100.0) * ALLOCATION_PER_TRADE for t in completed)
    
    strat_names = {
        "whale_pump": "고래수급",
        "rsi_reversion": "RSI반등",
        "macd_crossover": "MACD",
        "bb_breakout": "BB돌파",
        "spot_arbitrage": "거래소차익",
        "kimchi_arbitrage": "김프차익"
    }

    # Active positions calculations
    active_positions_list = []
    unrealized_usd_profit = 0.0
    
    for pos in pending:
        symbol = pos["symbol"]
        entry_price = float(pos["entry_price"])
        display_sym = symbol.replace("USDT", "")
        strategy = pos["strategy"] if ("strategy" in pos.keys() and pos["strategy"]) else "whale_pump"
        strat_disp = strat_names.get(strategy, strategy)
        
        # Fetch live current price
        cur_price = fetch_current_price(symbol)
        if cur_price > 0:
            unrealized_return = ((cur_price / entry_price) - 1.0) * 100.0
            unrealized_usd = (unrealized_return / 100.0) * ALLOCATION_PER_TRADE
            unrealized_usd_profit += unrealized_usd
            active_positions_list.append(
                f"• <b>{display_sym} ({strat_disp})</b>: 진입 ${entry_price:,.2f} ➡️ 현재 ${cur_price:,.2f} ({unrealized_return:+.2f}%)"
            )
        else:
            active_positions_list.append(
                f"• <b>{display_sym} ({strat_disp})</b>: 진입 ${entry_price:,.2f} ➡️ 현재 N/A"
            )
            
    # Total Valuation
    total_usd_profit = realized_usd_profit + unrealized_usd_profit
    current_capital = START_CAPITAL + total_usd_profit
    
    # Format message
    lines = []
    lines.append("🤖 <b>[No Slip Quant] 실시간 가상 매매 리포트 (1시간 현황)</b>")
    lines.append("=" * 40)
    lines.append("AI 퀀트 에이전트가 최적 파라미터 전략으로 가상 매매를 집행한 결과입니다.\n")
    
    lines.append("💳 <b>계좌 현황 (Account Status)</b>")
    lines.append(f"  • <b>가상 자산 평가총액</b>: ${current_capital:,.2f} USD")
    lines.append(f"  • <b>시작 원금</b>: ${START_CAPITAL:,.2f} USD")
    lines.append(f"  • <b>누적 총 손익</b>: ${total_usd_profit:+,.2f} USD ({total_usd_profit/START_CAPITAL*100.0:+.2f}%)")
    lines.append(f"  • <b>실현 손익 (Realized)</b>: ${realized_usd_profit:+,.2f} USD")
    lines.append(f"  • <b>평가 손익 (Unrealized)</b>: ${unrealized_usd_profit:+,.2f} USD")
    lines.append(f"  • <b>총 거래 횟수</b>: {total_trades}회")
    lines.append(f"  • <b>승률 (Win Rate)</b>: {win_rate:.1f}% ({winning_trades}승 / {total_trades - winning_trades}패)")
    
    lines.append("\n📈 <b>보유 중인 가상 포지션 ({0}개)</b>".format(len(pending)))
    if active_positions_list:
        lines.extend(active_positions_list)
    else:
        lines.append("  • 현재 보유 중인 가상 포지션 없음")
        
    lines.append("\n🕒 <b>최근 청산 내역 (최근 5건)</b>")
    if recent_closed:
        from datetime import timedelta
        kst = timezone(timedelta(hours=9))
        for t in recent_closed:
            display_sym = t["symbol"].replace("USDT", "")
            ret = float(t["realized_return"] or 0)
            emoji = "🟢" if ret > 0 else "🔴"
            strategy = t["strategy"] if ("strategy" in t.keys() and t["strategy"]) else "whale_pump"
            strat_disp = strat_names.get(strategy, strategy)
            
            # Format timestamps
            if t["entry_time"]:
                in_time_str = datetime.fromtimestamp(t["entry_time"], tz=timezone.utc).astimezone(kst).strftime("%m/%d %H:%M")
            else:
                in_time_str = "N/A"
                
            if t["exit_time"]:
                out_time_str = datetime.fromtimestamp(t["exit_time"], tz=timezone.utc).astimezone(kst).strftime("%m/%d %H:%M")
            else:
                out_time_str = "N/A"
                
            lines.append(f"  • {emoji} <b>{display_sym} ({strat_disp})</b>: ${float(t['entry_price']):,.2f} ➡️ ${float(t['exit_price']):,.2f} ({ret:+.2f}%)\n    [매수 {in_time_str} ➡️ 매도 {out_time_str}]")
    else:
        lines.append("  • 최근 청산 완료된 가상 포지션 없음")
        
    lines.append("\n" + "=" * 40)
    lines.append("💡 <b>인간 가이드</b>: 퀀트 에이전트의 실시간 가상 손익과 승률을 참고하여 실제 추격 매수 시 리스크 관리에 활용하세요.")
    
    report_msg = "\n".join(lines)
    send_telegram_message(report_msg)

def check_and_resolve_pending_trades():
    """Scan SQLite for pending trades and resolve them based on subsequent prices."""
    config = load_whale_config()
    
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        pending = conn.execute("""
            SELECT id, symbol, entry_time, entry_price, param_M, param_X, param_V, param_H, param_SL, param_TP, strategy
            FROM whale_trade_log 
            WHERE status = 'PENDING'
        """).fetchall()
        
        for trade in pending:
            trade_id = trade["id"]
            symbol = trade["symbol"]
            entry_time = trade["entry_time"]
            entry_price = trade["entry_price"]
            H = int(trade["param_H"])
            SL = float(trade["param_SL"])
            TP = float(trade["param_TP"])
            
            # Fetch klines starting from entry time (ms)
            start_ms = entry_time * 1000
            df = fetch_recent_klines(symbol, limit=H + 10, start_time_ms=start_ms)
            
            if df.empty or len(df) < 2:
                continue
                
            resolved = False
            exit_price = None
            exit_time_s = None
            realized_return = 0.0
            resolution_reason = ""
            
            # Loop minute-by-minute through holding duration
            for idx, row in df.iterrows():
                low_p = row["low"]
                high_p = row["high"]
                close_p = row["close"]
                row_time_s = int(row["open_time"].timestamp())
                
                # Check how many minutes have elapsed
                elapsed_minutes = (row_time_s - entry_time) // 60
                
                # Check Stop-Loss
                sl_price = entry_price * (1.0 - SL / 100.0)
                if low_p <= sl_price:
                    exit_price = sl_price
                    exit_time_s = row_time_s
                    realized_return = -SL
                    resolved = True
                    resolution_reason = "손절선 터치 (Stop-Loss)"
                    break
                    
                # Check Take-Profit
                tp_price = entry_price * (1.0 + TP / 100.0)
                if high_p >= tp_price:
                    exit_price = tp_price
                    exit_time_s = row_time_s
                    realized_return = TP
                    resolved = True
                    resolution_reason = "목표가 달성 (Take-Profit)"
                    break
                    
                # Check time-out H minutes
                if elapsed_minutes >= H:
                    exit_price = close_p
                    exit_time_s = row_time_s
                    realized_return = ((close_p / entry_price) - 1.0) * 100.0
                    resolved = True
                    resolution_reason = "최대 보유 시간 만료 (Time-out)"
                    break
            
            if resolved:
                # Update database
                reward = realized_return
                conn.execute("""
                    UPDATE whale_trade_log 
                    SET status = 'COMPLETED', exit_price = ?, exit_time = ?, realized_return = ?, reward = ?
                    WHERE id = ?
                """, (exit_price, exit_time_s, realized_return, reward, trade_id))
                conn.commit()
                
                # Apply Dynamic RL Parameter Adjustments (Policy Gradient style)
                symbol_config = config.get(symbol, {})
                strategy = trade["strategy"] if ("strategy" in trade.keys() and trade["strategy"]) else "whale_pump"
                
                rl_note = ""
                if strategy == "whale_pump":
                    old_X = float(symbol_config.get("X", 0.4))
                    old_V = float(symbol_config.get("V", 2.0))
                    if reward < 0:
                        new_X = min(2.5, old_X + 0.05)
                        new_V = min(8.0, old_V + 0.1)
                        symbol_config["X"] = round(new_X, 3)
                        symbol_config["V"] = round(new_V, 3)
                        rl_note = f"❌ <b>손실 패널티 적용 (고래 수급)</b>: X: {old_X}% ➡️ {symbol_config['X']}%, V: {old_V}x ➡️ {symbol_config['V']}x"
                    else:
                        new_X = max(0.2, old_X - 0.01)
                        symbol_config["X"] = round(new_X, 3)
                        rl_note = f"✨ <b>수익 강화 적용 (고래 수급)</b>: X: {old_X}% ➡️ {symbol_config['X']}%"
                    config[symbol] = symbol_config
                    save_whale_config(config)
                elif strategy == "rsi_reversion":
                    strategy_config = symbol_config.get("rsi_reversion", {"rsi_trigger": 25.0, "H": 15, "SL": 0.5, "TP": 1.0})
                    old_rsi_trigger = float(strategy_config.get("rsi_trigger", 25.0))
                    if reward < 0:
                        new_rsi_trigger = max(15.0, old_rsi_trigger - 1.0)
                        strategy_config["rsi_trigger"] = round(new_rsi_trigger, 2)
                        rl_note = f"❌ <b>손실 패널티 적용 (RSI 반등)</b>: RSI Trigger: {old_rsi_trigger} ➡️ {new_rsi_trigger}"
                    else:
                        new_rsi_trigger = min(30.0, old_rsi_trigger + 0.2)
                        strategy_config["rsi_trigger"] = round(new_rsi_trigger, 2)
                        rl_note = f"✨ <b>수익 강화 적용 (RSI 반등)</b>: RSI Trigger: {old_rsi_trigger} ➡️ {new_rsi_trigger}"
                    symbol_config["rsi_reversion"] = strategy_config
                    config[symbol] = symbol_config
                    save_whale_config(config)
                elif strategy == "macd_crossover":
                    strategy_config = symbol_config.get("macd_crossover", {"vol_confirm": 1.0, "H": 60, "SL": 0.7, "TP": 1.5})
                    old_vol_confirm = float(strategy_config.get("vol_confirm", 1.0))
                    if reward < 0:
                        new_vol_confirm = min(2.5, old_vol_confirm + 0.1)
                        strategy_config["vol_confirm"] = round(new_vol_confirm, 2)
                        rl_note = f"❌ <b>손실 패널티 적용 (MACD 골든크로스)</b>: Vol Confirm: {old_vol_confirm}x ➡️ {new_vol_confirm}x"
                    else:
                        new_vol_confirm = max(0.5, old_vol_confirm - 0.02)
                        strategy_config["vol_confirm"] = round(new_vol_confirm, 2)
                        rl_note = f"✨ <b>수익 강화 적용 (MACD 골든크로스)</b>: Vol Confirm: {old_vol_confirm}x ➡️ {new_vol_confirm}x"
                    symbol_config["macd_crossover"] = strategy_config
                    config[symbol] = symbol_config
                    save_whale_config(config)
                elif strategy == "bb_breakout":
                    strategy_config = symbol_config.get("bb_breakout", {"squeeze_ratio": 1.15, "H": 30, "SL": 1.0, "TP": 2.0})
                    old_squeeze_ratio = float(strategy_config.get("squeeze_ratio", 1.15))
                    if reward < 0:
                        new_squeeze_ratio = max(1.02, old_squeeze_ratio - 0.02)
                        strategy_config["squeeze_ratio"] = round(new_squeeze_ratio, 3)
                        rl_note = f"❌ <b>손실 패널티 적용 (BB 수축돌파)</b>: Squeeze Ratio: {old_squeeze_ratio} ➡️ {new_squeeze_ratio}"
                    else:
                        new_squeeze_ratio = min(1.3, old_squeeze_ratio + 0.005)
                        strategy_config["squeeze_ratio"] = round(new_squeeze_ratio, 3)
                        rl_note = f"✨ <b>수익 강화 적용 (BB 수축돌파)</b>: Squeeze Ratio: {old_squeeze_ratio} ➡️ {new_squeeze_ratio}"
                    symbol_config["bb_breakout"] = strategy_config
                    config[symbol] = symbol_config
                    save_whale_config(config)
                elif strategy == "spot_arbitrage":
                    strategy_config = symbol_config.get("spot_arbitrage", {"spread_trigger": 0.12, "H": 10, "SL": 0.5, "TP": 0.3})
                    old_spread_trigger = float(strategy_config.get("spread_trigger", 0.12))
                    if reward < 0:
                        new_spread_trigger = min(0.5, old_spread_trigger + 0.01)
                        strategy_config["spread_trigger"] = round(new_spread_trigger, 3)
                        rl_note = f"❌ <b>손실 패널티 적용 (거래소 차익)</b>: Spread Trigger: {old_spread_trigger}% ➡️ {new_spread_trigger}%"
                    else:
                        new_spread_trigger = max(0.06, old_spread_trigger - 0.002)
                        strategy_config["spread_trigger"] = round(new_spread_trigger, 3)
                        rl_note = f"✨ <b>수익 강화 적용 (거래소 차익)</b>: Spread Trigger: {old_spread_trigger}% ➡️ {new_spread_trigger}%"
                    symbol_config["spot_arbitrage"] = strategy_config
                    config[symbol] = symbol_config
                    save_whale_config(config)
                elif strategy == "kimchi_arbitrage":
                    strategy_config = symbol_config.get("kimchi_arbitrage", {"min_premium": -1.0, "max_premium": 4.0, "H": 60, "SL": 1.5, "TP": 1.0})
                    old_min = float(strategy_config.get("min_premium", -1.0))
                    old_max = float(strategy_config.get("max_premium", 4.0))
                    if reward < 0:
                        new_min = old_min - 0.1
                        new_max = old_max + 0.2
                        strategy_config["min_premium"] = round(new_min, 2)
                        strategy_config["max_premium"] = round(new_max, 2)
                        rl_note = f"❌ <b>손실 패널티 적용 (김프 차익)</b>: Min Premium: {old_min}% ➡️ {new_min}%, Max Premium: {old_max}% ➡️ {new_max}%"
                    else:
                        new_min = min(-0.2, old_min + 0.02)
                        new_max = max(1.5, old_max - 0.05)
                        strategy_config["min_premium"] = round(new_min, 2)
                        strategy_config["max_premium"] = round(new_max, 2)
                        rl_note = f"✨ <b>수익 강화 적용 (김프 차익)</b>: Min Premium: {old_min}% ➡️ {new_min}%, Max Premium: {old_max}% ➡️ {new_max}%"
                    symbol_config["kimchi_arbitrage"] = strategy_config
                    config[symbol] = symbol_config
                    save_whale_config(config)
                
                # Broadcast results to Telegram
                display_sym = symbol.replace("USDT", "")
                outcome_emoji = "🟢" if reward > 0 else "🔴"
                strat_display_names = {
                    "whale_pump": "고래 수급 매매",
                    "rsi_reversion": "RSI 과매도 반등",
                    "macd_crossover": "MACD 골든크로스",
                    "bb_breakout": "BB 변동성 돌파",
                    "spot_arbitrage": "거래소 차익거래",
                    "kimchi_arbitrage": "김치프리미엄 차익거래"
                }
                strat_disp = strat_display_names.get(strategy, strategy)
                
                msg = []
                msg.append(f"🔄 <b>[No Slip RL] {strat_disp} 종료 결과 피드백 ({display_sym})</b>")
                msg.append("=" * 40)
                msg.append(f"• <b>상태</b>: {resolution_reason}")
                msg.append(f"• <b>진입 가격</b>: ${entry_price:,.2f}")
                msg.append(f"• <b>청산 가격</b>: ${exit_price:,.2f}")
                msg.append(f"• <b>실현 수익률</b>: {outcome_emoji} <b>{realized_return:+.2f}%</b>")
                msg.append(f"• <b>RL 피드백 보상</b>: {reward:+.2f}")
                msg.append(f"\n🧠 <b>AI 모델 가중치 업데이트</b>")
                msg.append(f"  {rl_note}")
                msg.append("\n" + "=" * 40)
                msg.append("※ 본 피드백은 1분 단위 백테스트 복기 엔진에 의해 실시간 계산되어 자동 반영됩니다.")
                
                send_telegram_message("\n".join(msg))

def trigger_trade(symbol, current_price, M, X, V, H, SL, TP, strategy):
    now = time.time()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO whale_trade_log (symbol, entry_time, entry_price, param_M, param_X, param_V, param_H, param_SL, param_TP, status, strategy, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
        """, (symbol, int(now), current_price, M, X, V, H, SL, TP, strategy, datetime.now(timezone.utc).isoformat()))
        conn.commit()

def check_signals_for_symbol(symbol: str, config: dict):
    symbol_config = config.get(symbol, {})
    if not symbol_config:
        return
        
    # Fetch 120 klines to ensure indicator convergence
    df = fetch_recent_klines(symbol, limit=120)
    if df.empty or len(df) < 50:
        return
        
    now = time.time()
    
    # 1. Technical Indicators calculation
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
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    
    # Bollinger Bands (20, 2.0)
    df["bb_mid"] = close_series.rolling(window=20).mean()
    df["bb_std"] = close_series.rolling(window=20).std()
    df["bb_upper"] = df["bb_mid"] + 2.0 * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - 2.0 * df["bb_std"]
    df["bb_bandwidth"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"].replace(0, 1e-9)
    df["bb_bandwidth_min30"] = df["bb_bandwidth"].shift(1).rolling(window=30).min()
    
    # Volume MA for confirmations
    df["vol_ma"] = volume_series.rolling(30).mean()
    
    # Latest prices & indicators
    current_price = float(close_series.iloc[-1])
    current_volume = float(volume_series.iloc[-1])
    vol_ma = float(df["vol_ma"].iloc[-2]) if not pd.isna(df["vol_ma"].iloc[-2]) else 1.0
    
    display_sym = symbol.replace("USDT", "")
    
    # ------------------ Strategy 1: Whale Pump ------------------
    strategy_whale = "whale_pump"
    M = int(symbol_config.get("M", 3))
    X = float(symbol_config.get("X", 0.4))
    V = float(symbol_config.get("V", 2.0))
    H_whale = int(symbol_config.get("H", 30))
    SL_whale = float(symbol_config.get("SL", 1.0))
    TP_whale = float(symbol_config.get("TP", 2.0))
    
    prev_price_whale = float(close_series.iloc[-1 - M])
    price_change = ((current_price / prev_price_whale) - 1.0) * 100.0
    vol_ratio = current_volume / vol_ma if vol_ma > 0 else 0.0
    
    if price_change >= X and vol_ratio >= V:
        last_alert = last_alert_times.get((symbol, strategy_whale), 0)
        if now - last_alert >= ALERT_COOLDOWN_SECONDS:
            last_alert_times[(symbol, strategy_whale)] = now
            trigger_trade(symbol, current_price, M, X, vol_ratio, H_whale, SL_whale, TP_whale, strategy_whale)
            
            tp_price = current_price * (1.0 + TP_whale / 100.0)
            sl_price = current_price * (1.0 - SL_whale / 100.0)
            emoji = "🐳" if display_sym in ["BTC", "ETH"] else "⚡"
            
            lines = [
                f"{emoji} <b>[No Slip Whale] 고래 수급 유입 포착 ({display_sym})</b>",
                "=" * 40,
                f"🔥 <b>{display_sym} 실시간 급등 감지</b>",
                f"  • 현재가: ${current_price:,.2f}",
                f"  • <b>진입 사유</b>: {M}분 전 가격 대비 +{price_change:.2f}% 급등 (기준 {X}%) 및 거래량이 30분 평균 대비 {vol_ratio:.1f}배 급증 (기준 {V}x)",
                f"  • <b>매매 전략</b>: 고래 수급 추격 매수 전략 (Whale Momentum Breakout)",
                f"  • <b>핵심 근거</b>: 거래량이 동반된 단기 주가 폭증은 세력(고래)의 대규모 시장가 매집 신호이며, 단기 추세 상승 지속 가능성이 극대화되는 타점입니다.",
                "\n<b>🎯 추격매수 시뮬레이션 매매 타겟</b>",
                f"  • <b>추천 진입가</b>: ${current_price:,.2f} (즉시 매수)",
                f"  • <b>익절 목표가 (+{TP_whale}%)</b>: ${tp_price:,.2f}",
                f"  • <b>손절 가격 (-{SL_whale}%)</b>: ${sl_price:,.2f}",
                f"  • <b>추천 보유 시간</b>: 최대 {H_whale}분",
                "\n💡 <b>알림 해석 가이드</b>",
                "• 갑작스러운 가격 상승과 거래량 동반은 고래가 대규모 시장가 주문을 집행한 신호입니다. 수치와 배수가 높을수록 돌파 성공률이 높습니다.",
                "\n" + "=" * 40,
                f"※ 본 알림은 1분 단위 고래 거래량 급증 포착 알고리즘에 의해 발송됩니다."
            ]
            send_telegram_message("\n".join(lines))
            
    # ------------------ Strategy 2: RSI Reversion ------------------
    strategy_rsi = "rsi_reversion"
    rsi_config = symbol_config.get("rsi_reversion", {"rsi_trigger": 25.0, "H": 15, "SL": 0.5, "TP": 1.0})
    rsi_trigger = float(rsi_config.get("rsi_trigger", 25.0))
    H_rsi = int(rsi_config.get("H", 15))
    SL_rsi = float(rsi_config.get("SL", 0.5))
    TP_rsi = float(rsi_config.get("TP", 1.0))
    
    curr_rsi = float(df["rsi"].iloc[-1])
    prev_rsi = float(df["rsi"].iloc[-2])
    
    if (curr_rsi < rsi_trigger) and (curr_rsi > prev_rsi):
        last_alert = last_alert_times.get((symbol, strategy_rsi), 0)
        if now - last_alert >= ALERT_COOLDOWN_SECONDS:
            last_alert_times[(symbol, strategy_rsi)] = now
            trigger_trade(symbol, current_price, 0, rsi_trigger, curr_rsi, H_rsi, SL_rsi, TP_rsi, strategy_rsi)
            
            tp_price = current_price * (1.0 + TP_rsi / 100.0)
            sl_price = current_price * (1.0 - SL_rsi / 100.0)
            
            lines = [
                f"🟢 <b>[No Slip RSI] 과매도 반등 시그널 포착 ({display_sym})</b>",
                "=" * 40,
                f"📈 <b>{display_sym} 과매도 권역 반등 감지</b>",
                f"  • 현재가: ${current_price:,.2f}",
                f"  • <b>진입 사유</b>: RSI(14)가 {curr_rsi:.1f}로 과매도 기준치인 {rsi_trigger} 이하로 급락 후 상승 전환 반등 (직전 RSI: {prev_rsi:.1f})",
                f"  • <b>매매 전략</b>: RSI 과매도 회귀 전략 (RSI Mean Reversion)",
                f"  • <b>핵심 근거</b>: 주가가 과매도 국면에 접어든 상태에서 저점 매수세가 유입되어 RSI가 상승 반전하는 것은 하락세가 진정되고 기술적 반등이 개시되는 대표적인 전환 타점입니다.",
                "\n<b>🎯 추격매수 시뮬레이션 매매 타겟</b>",
                f"  • <b>추천 진입가</b>: ${current_price:,.2f} (즉시 매수)",
                f"  • <b>익절 목표가 (+{TP_rsi}%)</b>: ${tp_price:,.2f}",
                f"  • <b>손절 가격 (-{SL_rsi}%)</b>: ${sl_price:,.2f}",
                f"  • <b>추천 보유 시간</b>: 최대 {H_rsi}분",
                "\n💡 <b>알림 해석 가이드</b>",
                "• RSI 과매도 구간(일반적으로 30 이하)에서의 주가 반등은 단기 투매 진정 및 저가 매수세 유입을 뜻하며, 높은 확률로 단기 기술적 반등을 이끌어냅니다.",
                "\n" + "=" * 40,
                f"※ 본 알림은 1분 단위 RSI 과매도 회귀 포착 알고리즘에 의해 발송됩니다."
            ]
            send_telegram_message("\n".join(lines))

    # ------------------ Strategy 3: MACD Crossover ------------------
    strategy_macd = "macd_crossover"
    macd_config = symbol_config.get("macd_crossover", {"vol_confirm": 1.0, "H": 60, "SL": 0.7, "TP": 1.5})
    vol_confirm = float(macd_config.get("vol_confirm", 1.0))
    H_macd = int(macd_config.get("H", 60))
    SL_macd = float(macd_config.get("SL", 0.7))
    TP_macd = float(macd_config.get("TP", 1.5))
    
    curr_macd = float(df["macd"].iloc[-1])
    curr_signal = float(df["macd_signal"].iloc[-1])
    prev_macd = float(df["macd"].iloc[-2])
    prev_signal = float(df["macd_signal"].iloc[-2])
    
    vol_ratio_macd = current_volume / vol_ma if vol_ma > 0 else 0.0
    
    if (prev_macd <= prev_signal) and (curr_macd > curr_signal) and (vol_ratio_macd >= vol_confirm):
        last_alert = last_alert_times.get((symbol, strategy_macd), 0)
        if now - last_alert >= ALERT_COOLDOWN_SECONDS:
            last_alert_times[(symbol, strategy_macd)] = now
            trigger_trade(symbol, current_price, 0, vol_confirm, vol_ratio_macd, H_macd, SL_macd, TP_macd, strategy_macd)
            
            tp_price = current_price * (1.0 + TP_macd / 100.0)
            sl_price = current_price * (1.0 - SL_macd / 100.0)
            
            lines = [
                f"🚀 <b>[No Slip MACD] 골든크로스 상승 전환 포착 ({display_sym})</b>",
                "=" * 40,
                f"📈 <b>{display_sym} 추세 상승 반전 감지</b>",
                f"  • 현재가: ${current_price:,.2f}",
                f"  • <b>진입 사유</b>: MACD 지표선({curr_macd:.4f})이 Signal선({curr_signal:.4f})을 골든크로스 돌파 및 거래량 {vol_ratio_macd:.1f}배 동반(기준 {vol_confirm}x)",
                f"  • <b>매매 전략</b>: MACD 추세 추종 돌파 전략 (MACD Momentum Follower)",
                f"  • <b>핵심 근거</b>: MACD 골든크로스는 중단기 하락 추세가 상승으로 복귀하는 강력한 전환점이며, 거래량 증가가 동반되어 휩소(가짜 돌파) 가능성이 최소화된 타점입니다.",
                "\n<b>🎯 추격매수 시뮬레이션 매매 타겟</b>",
                f"  • <b>추천 진입가</b>: ${current_price:,.2f} (즉시 매수)",
                f"  • <b>익절 목표가 (+{TP_macd}%)</b>: ${tp_price:,.2f}",
                f"  • <b>손절 가격 (-{SL_macd}%)</b>: ${sl_price:,.2f}",
                f"  • <b>추천 보유 시간</b>: 최대 {H_macd}분",
                "\n💡 <b>알림 해석 가이드</b>",
                "• MACD 골든크로스는 중단기 추세가 하락에서 상승으로 공식 전환됨을 나타내며, 수급(거래량)의 증가와 수평 결합될 때 신뢰도가 극대화됩니다.",
                "\n" + "=" * 40,
                f"※ 본 알림은 1분 단위 MACD Golden Cross 포착 알고리즘에 의해 발송됩니다."
            ]
            send_telegram_message("\n".join(lines))

    # ------------------ Strategy 4: Bollinger Band Breakout ------------------
    strategy_bb = "bb_breakout"
    bb_config = symbol_config.get("bb_breakout", {"squeeze_ratio": 1.15, "H": 30, "SL": 1.0, "TP": 2.0})
    squeeze_ratio = float(bb_config.get("squeeze_ratio", 1.15))
    H_bb = int(bb_config.get("H", 30))
    SL_bb = float(bb_config.get("SL", 1.0))
    TP_bb = float(bb_config.get("TP", 2.0))
    
    curr_bandwidth = float(df["bb_bandwidth"].iloc[-1])
    min30_bandwidth = float(df["bb_bandwidth_min30"].iloc[-1]) if not pd.isna(df["bb_bandwidth_min30"].iloc[-1]) else 0.02
    curr_close = float(close_series.iloc[-1])
    curr_upper = float(df["bb_upper"].iloc[-1])
    prev_close = float(close_series.iloc[-2])
    prev_upper = float(df["bb_upper"].iloc[-2])
    
    is_squeezed = (min30_bandwidth > 0) and (curr_bandwidth <= min30_bandwidth * squeeze_ratio)
    is_breakout = (curr_close > curr_upper) and (prev_close <= prev_upper)
    
    if is_squeezed and is_breakout:
        last_alert = last_alert_times.get((symbol, strategy_bb), 0)
        if now - last_alert >= ALERT_COOLDOWN_SECONDS:
            last_alert_times[(symbol, strategy_bb)] = now
            trigger_trade(symbol, current_price, 0, squeeze_ratio, curr_bandwidth, H_bb, SL_bb, TP_bb, strategy_bb)
            
            tp_price = current_price * (1.0 + TP_bb / 100.0)
            sl_price = current_price * (1.0 - SL_bb / 100.0)
            
            lines = [
                f"💥 <b>[No Slip BB] 변동성 수축 돌파 포착 ({display_sym})</b>",
                "=" * 40,
                f"📈 <b>{display_sym} 볼린저 밴드 상단 돌파</b>",
                f"  • 현재가: ${current_price:,.2f}",
                f"  • <b>진입 사유</b>: 볼린저 밴드 대역폭(Bandwidth)이 {curr_bandwidth*100:.2f}%로 30분 최저치 대비 수축 조건({squeeze_ratio:.2f}x) 충족 후 종가가 상단 밴드(${curr_upper:,.2f}) 돌파",
                f"  • <b>매매 전략</b>: 볼린저 밴드 수축 및 밴드상단 돌파 전략 (BB Squeeze Breakout)",
                f"  • <b>핵심 근거</b>: 횡보로 인해 밴드가 극도로 수축한 것은 시세 분출 에너지가 응축되었음을 의미하며, 이 상태에서 밴드 상단 돌파는 강력한 방향성 랠리의 신호탄입니다.",
                "\n<b>🎯 추격매수 시뮬레이션 매매 타겟</b>",
                f"  • <b>추천 진입가</b>: ${current_price:,.2f} (즉시 매수)",
                f"  • <b>익절 목표가 (+{TP_bb}%)</b>: ${tp_price:,.2f}",
                f"  • <b>손절 가격 (-{SL_bb}%)</b>: ${sl_price:,.2f}",
                f"  • <b>추천 보유 시간</b>: 최대 {H_bb}분",
                "\n💡 <b>알림 해석 가이드</b>",
                "• 주가가 오랜 횡보를 거치며 볼린저 밴드가 극도로 수축한 후(Squeeze), 밴드 상단 돌파는 강력한 새 추세 랠리의 시작점(Breakout) 역할을 합니다.",
                "\n" + "=" * 40,
                f"※ 본 알림은 1분 단위 Bollinger Band Squeeze 돌파 포착 알고리즘에 의해 발송됩니다."
            ]
            send_telegram_message("\n".join(lines))

    # ------------------ Strategy 5: Spot Exchange Arbitrage ------------------
    strategy_spot_arb = "spot_arbitrage"
    spot_arb_config = symbol_config.get("spot_arbitrage", {"spread_trigger": 0.12, "H": 10, "SL": 0.5, "TP": 0.3})
    spread_trigger = float(spot_arb_config.get("spread_trigger", 0.12))
    H_spot_arb = int(spot_arb_config.get("H", 10))
    SL_spot_arb = float(spot_arb_config.get("SL", 0.5))
    TP_spot_arb = float(spot_arb_config.get("TP", 0.3))
    
    bybit_price = fetch_bybit_price(symbol)
    
    if bybit_price > 0:
        spread = ((current_price / bybit_price) - 1.0) * 100.0
        abs_spread = abs(spread)
        
        if abs_spread >= spread_trigger:
            last_alert = last_alert_times.get((symbol, strategy_spot_arb), 0)
            if now - last_alert >= ALERT_COOLDOWN_SECONDS:
                last_alert_times[(symbol, strategy_spot_arb)] = now
                trigger_trade(symbol, current_price, 0, spread_trigger, spread, H_spot_arb, SL_spot_arb, TP_spot_arb, strategy_spot_arb)
                
                # Exits targets (simulation tracks directional TP/SL)
                tp_price = current_price * (1.0 + TP_spot_arb / 100.0) if spread > 0 else current_price * (1.0 - TP_spot_arb / 100.0)
                sl_price = current_price * (1.0 - SL_spot_arb / 100.0) if spread > 0 else current_price * (1.0 + SL_spot_arb / 100.0)
                
                direction = "Binance 매도 (Short) ➡️ Bybit 매수 (Long)" if spread > 0 else "Binance 매수 (Long) ➡️ Bybit 매도 (Short)"
                cheap_ex = "Bybit" if spread > 0 else "Binance"
                exp_ex = "Binance" if spread > 0 else "Bybit"
                cheap_p = bybit_price if spread > 0 else current_price
                exp_p = current_price if spread > 0 else bybit_price
                
                lines = [
                    f"⚖️ <b>[No Slip Arbitrage] 거래소간 차익 거래 포착 ({display_sym})</b>",
                    "=" * 40,
                    f"🔥 <b>{display_sym} 글로벌 거래소간 가격 괴리 발생</b>",
                    f"  • Binance 가격: ${current_price:,.2f}",
                    f"  • Bybit 가격: ${bybit_price:,.2f}",
                    f"  • <b>현재 스프레드</b>: <b>{spread:+.3f}%</b> (기준치: {spread_trigger}%)",
                    f"  • <b>진입 사유</b>: 두 거래소간 시세 괴리가 {abs_spread:.3f}%까지 확대되어 차익 발생",
                    f"  • <b>매매 전략</b>: 글로벌 현물간 무위험 차익거래 (Spot-Spot Arbitrage)",
                    f"  • <b>핵심 근거</b>: 동일 기초자산에 대해 다른 거래소간 시세가 단기 왜곡될 경우, 저평가 거래소에서 매수하고 고평가 거래소에서 매도하여 괴리 수렴 시 무위험 수익을 획득합니다.",
                    "\n<b>🎯 차익거래 실행 방향 가이드</b>",
                    f"  • <b>실행 방향</b>: {direction}",
                    f"  • <b>매수 처 ({cheap_ex})</b>: ${cheap_p:,.2f}",
                    f"  • <b>매도 처 ({exp_ex})</b>: ${exp_p:,.2f}",
                    f"  • <b>목표 익절값 ({TP_spot_arb}%)</b>: 스프레드 {TP_spot_arb}% 수렴 또는 역전 시 전량 청산",
                    f"  • <b>최대 홀딩시간</b>: {H_spot_arb}분",
                    "\n💡 <b>알림 해석 가이드</b>",
                    "• 차익 거래 알림 수신 시 즉시 저평가 거래소에서 매수하고 고평가 거래소에서 매도(혹은 선물 매도 헷징)를 진입합니다. 수렴이 빠르게 완료되므로 신속한 집행이 생명입니다.",
                    "\n" + "=" * 40,
                    f"※ 본 알림은 1분 단위 Binance-Bybit 실시간 시세 괴리 스캔 엔진에 의해 발송됩니다."
                ]
                send_telegram_message("\n".join(lines))

    # ------------------ Strategy 6: Kimchi Premium Arbitrage ------------------
    strategy_kimchi_arb = "kimchi_arbitrage"
    kimchi_config = symbol_config.get("kimchi_arbitrage", {"min_premium": -1.0, "max_premium": 4.0, "H": 60, "SL": 1.5, "TP": 1.0})
    min_premium = float(kimchi_config.get("min_premium", -1.0))
    max_premium = float(kimchi_config.get("max_premium", 4.0))
    H_kimchi = int(kimchi_config.get("H", 60))
    SL_kimchi = float(kimchi_config.get("SL", 1.5))
    TP_kimchi = float(kimchi_config.get("TP", 1.0))
    
    upbit_price = fetch_upbit_price(symbol)
    
    if upbit_price > 0:
        premium = ((upbit_price / current_price) - 1.0) * 100.0
        
        trigger_kimchi = False
        direction = ""
        action_desc = ""
        reason = ""
        
        if premium <= min_premium:
            trigger_kimchi = True
            direction = "역프 발생 (Reverse Premium)"
            action_desc = "Upbit 매수 (KRW) ➡️ Binance 매도 (USD) 또는 송금 후 해외 매도"
            reason = f"김치 프리미엄이 {premium:+.2f}%로 임계치인 {min_premium}%를 하회하여 해외 대비 역프리가 심화되었습니다."
        elif premium >= max_premium:
            trigger_kimchi = True
            direction = "김프 과열 (High Kimchi Premium)"
            action_desc = "Binance 매수 (USD) ➡️ Upbit 매도 (KRW) 또는 국내 보유 자산 해외로 송금/헷징"
            reason = f"김치 프리미엄이 {premium:+.2f}%로 임계치인 {max_premium}%를 상회하여 국내 매수세가 과열되었습니다."
            
        if trigger_kimchi:
            last_alert = last_alert_times.get((symbol, strategy_kimchi_arb), 0)
            if now - last_alert >= ALERT_COOLDOWN_SECONDS:
                last_alert_times[(symbol, strategy_kimchi_arb)] = now
                trigger_trade(symbol, current_price, 0, min_premium, premium, H_kimchi, SL_kimchi, TP_kimchi, strategy_kimchi_arb)
                
                # Fetch KRW price for display
                usd_rate = get_usd_krw_rate()
                upbit_krw = upbit_price * usd_rate
                
                lines = [
                    f"🇰🇷 <b>[No Slip Kimchi] 김치 프리미엄 괴리 포착 ({display_sym})</b>",
                    "=" * 40,
                    f"🔥 <b>{display_sym} 국내-해외 거래소 가격 괴리 포착</b>",
                    f"  • Binance 가격 (해외): ${current_price:,.2f}",
                    f"  • Upbit 가격 (국내): ₩{upbit_krw:,.0f} (${upbit_price:,.2f})",
                    f"  • <b>현재 김치 프리미엄</b>: <b>{premium:+.2f}%</b> (범위: {min_premium}% ~ {max_premium}%)",
                    f"  • <b>진입 사유</b>: {reason}",
                    f"  • <b>매매 전략</b>: 한국 프리미엄 차익거래 (Kimchi Premium Arbitrage)",
                    f"  • <b>핵심 근거</b>: 한국 거래소의 외환 규제 및 수급 왜곡으로 유발되는 김프/역프 가격 편차를 이용해, 상대적으로 저평가된 시장에서 사고 고평가된 시장에서 매도하여 변동성을 차익화합니다.",
                    "\n<b>🎯 차익거래 실행 방향 가이드</b>",
                    f"  • <b>시장 상태</b>: {direction}",
                    f"  • <b>추천 대응</b>: {action_desc}",
                    f"  • <b>목표 익절값 ({TP_kimchi}%)</b>: 프리미엄 수렴시 청산",
                    f"  • <b>최대 홀딩시간</b>: {H_kimchi}분",
                    "\n💡 <b>알림 해석 가이드</b>",
                    "• 역프(국내가 더 저렴) 일 때 구매하여 해외로 송금해 매도하는 테이커 전략이나, 김프가 급등할 때 헷징 숏 포지션을 해외에 잡고 국내 현물을 고가에 정리하는 전략 등으로 무위험 고수익 확보가 가능합니다.",
                    "\n" + "=" * 40,
                    f"※ 본 알림은 1분 단위 Upbit-Binance 프리미엄 실시간 스캔 엔진에 의해 발송됩니다."
                ]
                send_telegram_message("\n".join(lines))

def main():
    global last_hourly_report_time
    print("🚀 Starting Whale & Multi-Strategy Monitor Daemon with Reinforcement Learning & Quant Trader Agent...")
    init_db()
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    
    config = load_whale_config()
    print(f"Loaded config: {json.dumps(config, indent=2)}")
    
    # Trigger an initial report on startup
    try:
        send_hourly_quant_report()
        last_hourly_report_time = time.time()
    except Exception as e:
        print(f"⚠️ Error sending initial quant report: {e}")
        
    while True:
        now = time.time()
        
        # 1. Check and resolve any active trades (RL Update)
        try:
            check_and_resolve_pending_trades()
        except Exception as e:
            print(f"⚠️ Error in trade resolution loop: {e}")
            
        # 2. Check for new strategy triggers
        config = load_whale_config()
        for sym in symbols:
            try:
                check_signals_for_symbol(sym, config)
            except Exception as e:
                print(f"⚠️ Error checking {sym}: {e}")
                
        # 3. Check if it's time to send the hourly quant report
        if now - last_hourly_report_time >= HOURLY_REPORT_INTERVAL:
            try:
                send_hourly_quant_report()
                last_hourly_report_time = now
            except Exception as e:
                print(f"⚠️ Error sending hourly quant report: {e}")
                
        # Wait 60 seconds
        time.sleep(60)

if __name__ == "__main__":
    main()

