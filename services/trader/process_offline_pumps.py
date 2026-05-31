#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import sys
import time
import sqlite3
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
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

def init_db():
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

def load_whale_config() -> dict:
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Error reading config file: {e}")
            
    return {
        "BTCUSDT": {"M": 3, "X": 0.4, "V": 2.0, "H": 30, "SL": 1.0, "TP": 2.0},
        "ETHUSDT": {"M": 5, "X": 0.4, "V": 2.0, "H": 30, "SL": 1.0, "TP": 3.0},
        "SOLUSDT": {"M": 5, "X": 0.4, "V": 2.0, "H": 30, "SL": 1.5, "TP": 3.0}
    }

def save_whale_config(config: dict):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
        print(f"💾 Whale config dynamically updated by offline processor.")
    except Exception as e:
        print(f"⚠️ Failed to save whale config: {e}")

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

def fetch_klines_range(symbol: str, start_time_ms: int, end_time_ms: int) -> pd.DataFrame:
    all_klines = []
    current_start = start_time_ms
    
    print(f"Fetching klines for {symbol} from {datetime.fromtimestamp(start_time_ms/1000, tz=timezone.utc)} to {datetime.fromtimestamp(end_time_ms/1000, tz=timezone.utc)}...")
    
    while current_start < end_time_ms:
        params = {
            "symbol": symbol,
            "interval": "1m",
            "startTime": current_start,
            "endTime": end_time_ms,
            "limit": 1000
        }
        try:
            res = requests.get("https://api.binance.com/api/v3/klines", params=params, timeout=10)
            res.raise_for_status()
            klines = res.json()
            if not klines:
                break
            all_klines.extend(klines)
            last_close_time = klines[-1][6]
            current_start = last_close_time + 1
            if len(klines) < 1000:
                break
        except Exception as e:
            print(f"⚠️ Failed to fetch klines: {e}")
            break
            
    if not all_klines:
        return pd.DataFrame()
        
    df = pd.DataFrame(all_klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "count", "taker_buy_volume",
        "taker_buy_quote_volume", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    return df

def get_existing_trade_times(symbol: str, start_time_s: int) -> set:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("""
            SELECT entry_time FROM whale_trade_log 
            WHERE symbol = ? AND entry_time >= ?
        """, (symbol, start_time_s))
        return {row[0] for row in cursor.fetchall()}

def main():
    print("🚀 Starting Retroactive Offline Pump Signal Processor...")
    init_db()
    
    config = load_whale_config()
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    
    # Calculate time windows
    utc_now = datetime.now(timezone.utc)
    kst_now = utc_now + timedelta(hours=9)
    # Start of today (KST midnight)
    kst_midnight = kst_now.replace(hour=0, minute=0, second=0, microsecond=0)
    utc_midnight = kst_midnight - timedelta(hours=9)
    
    # Fetch data starting 30 minutes before KST midnight to populate vol_ma
    fetch_start_utc = utc_midnight - timedelta(minutes=30)
    
    start_ms = int(fetch_start_utc.timestamp() * 1000)
    end_ms = int(utc_now.timestamp() * 1000)
    
    midnight_s = int(utc_midnight.timestamp())
    
    all_simulated_trades = []
    param_changes = {}
    
    for symbol in symbols:
        df = fetch_klines_range(symbol, start_ms, end_ms)
        if df.empty or len(df) < 35:
            print(f"⚠️ No kline data found for {symbol}")
            continue
            
        print(f"Analyzing {len(df)} candles for {symbol}...")
        
        # Calculate volume moving average excluding current candle
        df["vol_ma"] = df["volume"].rolling(30).mean()
        
        # Load starting parameters
        sym_params = config.get(symbol, {"M": 5, "X": 0.4, "V": 2.0, "H": 30, "SL": 1.0, "TP": 2.0})
        M = int(sym_params["M"])
        X = float(sym_params["X"])
        V = float(sym_params["V"])
        H = int(sym_params["H"])
        SL = float(sym_params["SL"])
        TP = float(sym_params["TP"])
        
        orig_X, orig_V = X, V
        
        last_trigger_time = 0
        existing_times = get_existing_trade_times(symbol, midnight_s)
        print(f"Found {len(existing_times)} existing trades in DB for {symbol} today.")
        
        symbol_trades = []
        
        # Chronological scanning
        for i in range(30, len(df)):
            kline_time = df["open_time"].iloc[i]
            kline_time_utc = kline_time.replace(tzinfo=timezone.utc)
            
            # Check if this candle is after KST midnight
            if kline_time_utc < utc_midnight:
                continue
                
            open_ts_s = int(kline_time_utc.timestamp())
            
            # Calculate signals using current parameters (which update dynamically!)
            current_price = float(df["close"].iloc[i])
            prev_price = float(df["close"].iloc[i - M])
            current_volume = float(df["volume"].iloc[i])
            vol_ma = float(df["vol_ma"].iloc[i - 1])
            
            if vol_ma == 0:
                continue
                
            price_change = ((current_price / prev_price) - 1.0) * 100.0
            vol_ratio = current_volume / vol_ma
            
            # Trigger check
            if price_change >= X and vol_ratio >= V:
                # Check cooldown (15 mins)
                if open_ts_s - last_trigger_time < 15 * 60:
                    continue
                    
                # Check duplication
                if open_ts_s in existing_times:
                    print(f"Skipping already recorded trade for {symbol} at {kline_time_utc}")
                    last_trigger_time = open_ts_s
                    continue
                    
                last_trigger_time = open_ts_s
                entry_price = current_price
                entry_time_s = open_ts_s
                
                # Simulate trade lifecycle
                resolved = False
                exit_price = None
                exit_time_s = None
                realized_return = 0.0
                resolution_reason = "Unresolved"
                
                for j in range(i + 1, len(df)):
                    row_j = df.iloc[j]
                    low_p = float(row_j["low"])
                    high_p = float(row_j["high"])
                    close_p = float(row_j["close"])
                    row_time_s = int(row_j["open_time"].replace(tzinfo=timezone.utc).timestamp())
                    
                    elapsed_minutes = j - i
                    
                    # Stop-Loss
                    sl_price = entry_price * (1.0 - SL / 100.0)
                    if low_p <= sl_price:
                        exit_price = sl_price
                        exit_time_s = row_time_s
                        realized_return = -SL
                        resolved = True
                        resolution_reason = "Stop-Loss"
                        break
                        
                    # Take-Profit
                    tp_price = entry_price * (1.0 + TP / 100.0)
                    if high_p >= tp_price:
                        exit_price = tp_price
                        exit_time_s = row_time_s
                        realized_return = TP
                        resolved = True
                        resolution_reason = "Take-Profit"
                        break
                        
                    # Time-out
                    if elapsed_minutes >= H:
                        exit_price = close_p
                        exit_time_s = row_time_s
                        realized_return = ((close_p / entry_price) - 1.0) * 100.0
                        resolved = True
                        resolution_reason = "Time-out"
                        break
                
                # Save to database
                status = 'COMPLETED' if resolved else 'PENDING'
                created_iso = kline_time_utc.isoformat()
                
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute("""
                        INSERT INTO whale_trade_log (
                            symbol, entry_time, entry_price, param_M, param_X, param_V, param_H, param_SL, param_TP, 
                            status, exit_price, exit_time, realized_return, reward, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        symbol, entry_time_s, entry_price, M, X, V, H, SL, TP, 
                        status, exit_price, exit_time_s, realized_return if resolved else None, realized_return if resolved else None, created_iso
                    ))
                    conn.commit()
                
                trade_info = {
                    "symbol": symbol,
                    "entry_time": kline_time_utc + timedelta(hours=9), # Local KST display
                    "entry_price": entry_price,
                    "status": status,
                    "exit_price": exit_price,
                    "return": realized_return if resolved else 0.0,
                    "reason": resolution_reason,
                    "param_X": X,
                    "param_V": V
                }
                symbol_trades.append(trade_info)
                all_simulated_trades.append(trade_info)
                
                # Apply Reinforcement Learning updates chronologically
                if resolved:
                    old_X_rl = X
                    old_V_rl = V
                    if realized_return < 0:
                        # Penalty
                        X = min(2.5, X + 0.05)
                        V = min(8.0, V + 0.1)
                    else:
                        # Reinforce
                        X = max(0.2, X - 0.01)
                        
                    # Round parameters
                    X = round(X, 3)
                    V = round(V, 3)
                    
        # Update config dict with final values
        config[symbol]["X"] = X
        config[symbol]["V"] = V
        save_whale_config(config)
        
        param_changes[symbol] = {
            "before_X": orig_X,
            "after_X": X,
            "before_V": orig_V,
            "after_V": V,
            "trade_count": len(symbol_trades)
        }
        
    # Generate Telegram Message
    if not all_simulated_trades:
        msg = []
        msg.append("🔄 <b>[No Slip] 오프라인 시간대 급등 복기 리포트</b>")
        msg.append("=" * 40)
        msg.append("오늘 하루 동안 오프라인 상태에서 발생한 급등 신호를 소급 스캔한 결과, <b>새로운 돌파 고래 신호가 검출되지 않았습니다.</b>")
        msg.append("\n현재 AI 모델 파라미터는 이전 학습 상태를 유지합니다.")
        msg.append("=" * 40)
        send_telegram_message("\n".join(msg))
        return
        
    lines = []
    lines.append("🔄 <b>[No Slip] 오프라인 시간대 급등 복기 & RL 추가 학습 보고서</b>")
    lines.append("=" * 40)
    lines.append("오늘 하루 노트북 오프라인 기기 다운타임 동안 발생한 바이낸스 1분봉 시세를 전수 조사하여 매매 시뮬레이션 및 온라인 강화 학습을 실행했습니다.\n")
    
    lines.append("🧠 <b>AI 최적 매매 파라미터 변동 현황</b>")
    for sym, change in param_changes.items():
        disp_sym = sym.replace("USDT", "")
        lines.append(f"• <b>{disp_sym}</b> (검출 신호: {change['trade_count']}건)")
        lines.append(f"  - 가격 임계치(X): {change['before_X']}% ➡️ {change['after_X']}%")
        lines.append(f"  - 거래량 배수(V): {change['before_V']}x ➡️ {change['after_V']}x")
        
    lines.append("\n📈 <b>소급 매매 시뮬레이션 상세 결과</b>")
    for t in all_simulated_trades:
        disp_sym = t["symbol"].replace("USDT", "")
        entry_time_str = t["entry_time"].strftime("%m/%d %H:%M")
        
        if t["status"] == "COMPLETED":
            emoji = "🟢" if t["return"] > 0 else "🔴"
            lines.append(f"{emoji} <b>{disp_sym}</b> ({entry_time_str})")
            lines.append(f"  • 진입가: ${t['entry_price']:,.2f} ➡️ 청산가: ${t['exit_price']:,.2f}")
            lines.append(f"  • 결과: <b>{t['return']:+.2f}%</b> ({t['reason']})")
        else:
            lines.append(f"🟡 <b>{disp_sym}</b> ({entry_time_str})")
            lines.append(f"  • 진입가: ${t['entry_price']:,.2f} ➡️ <b>진행 중 (PENDING)</b>")
            lines.append(f"  • 상태: 실시간 감지 데몬에 의해 자동 추적 청산 예정")
        lines.append("")
        
    lines.append("=" * 40)
    lines.append("💡 <b>인간 가이드</b>: 오프라인 기기 공백 동안의 거래 결과를 백테스팅 엔진으로 복기하여 DB에 기록하고, RL 정책을 업데이트하여 다음 실시간 신호 감지에 즉시 반영하였습니다.")
    
    send_telegram_message("\n".join(lines))
    print("Report sent to Telegram rooms.")

if __name__ == "__main__":
    main()
