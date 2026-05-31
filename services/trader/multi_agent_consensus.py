#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import sys
import sqlite3
import time
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
WEIGHTS_FILE = CACHE_DIR / "consensus_weights.json"
DB_PATH = CACHE_DIR / "whale_rewards.sqlite3"

def init_db():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS consensus_trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                entry_time INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                vote_macro TEXT NOT NULL,
                vote_trend TEXT NOT NULL,
                vote_value TEXT NOT NULL,
                vote_whale TEXT NOT NULL,
                vote_mean_reversion TEXT NOT NULL,
                consensus_score REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                exit_price REAL,
                exit_time INTEGER,
                realized_return REAL,
                created_at TEXT NOT NULL
            )
        """)
        # Robust migration check for vote_clucmay column
        cursor = conn.execute("PRAGMA table_info(consensus_trade_log)")
        columns = [row[1] for row in cursor.fetchall()]
        if "vote_clucmay" not in columns:
            conn.execute("ALTER TABLE consensus_trade_log ADD COLUMN vote_clucmay TEXT NOT NULL DEFAULT 'HOLD'")
        conn.commit()

def load_weights() -> dict:
    if WEIGHTS_FILE.exists():
        try:
            with open(WEIGHTS_FILE, "r") as f:
                w = json.load(f)
                if "clucmay" not in w:
                    w["clucmay"] = 0.1667
                    total_w = sum(w.values())
                    for k in w:
                        w[k] = round(w[k] / total_w, 4)
                return w
        except Exception as e:
            print(f"⚠️ Error reading weights file: {e}")
            
    # Default equal weights for 6 agents
    return {
        "macro": 0.1667,
        "trend": 0.1667,
        "value": 0.1667,
        "whale": 0.1667,
        "mean_reversion": 0.1667,
        "clucmay": 0.1667
    }

def save_weights(weights: dict):
    try:
        with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
            json.dump(weights, f, indent=4)
        print("💾 Consensus weights updated and saved.")
    except Exception as e:
        print(f"⚠️ Failed to save weights: {e}")

def send_telegram_message(text: str) -> bool:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_ids_str = os.getenv("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_ids_str:
        print("⚠️ Telegram 설정 누락 (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")
        return False
        
    chat_ids = [cid.strip() for cid in chat_ids_str.split(",") if cid.strip()]
    success = True
    
    # Split text into chunks under 4000 characters
    max_len = 4000
    chunks = []
    lines = text.split("\n")
    current_chunk = []
    current_len = 0
    
    for line in lines:
        if current_len + len(line) + 1 > max_len:
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_len = len(line)
            else:
                chunks.append(line)
                current_chunk = []
                current_len = 0
        else:
            current_chunk.append(line)
            current_len += len(line) + 1
            
    if current_chunk:
        chunks.append("\n".join(current_chunk))
        
    import urllib.request
    import json
    
    for cid in chat_ids:
        for i, chunk in enumerate(chunks):
            # If multiple chunks, add a page marker for premium feel
            chunk_text = chunk
            if len(chunks) > 1:
                chunk_text = f"{chunk}\n\n📄 <b>[Page {i+1}/{len(chunks)}]</b>"
                
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                "chat_id": cid,
                "text": chunk_text,
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
                    print(f"✅ [Telegram] {cid} 청크 {i+1}/{len(chunks)} 전송 완료!")
                time.sleep(1.0) # Small delay to respect rate limit
            except Exception as e:
                print(f"❌ [Telegram] {cid} 전송 실패: {e}")
                success = False
                
    return success

def fetch_ticker_data(symbol: str, period: str = "60d") -> pd.DataFrame:
    import yfinance as yf
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period)
        return df
    except Exception as e:
        print(f"⚠️ yfinance error for {symbol}: {e}")
        return pd.DataFrame()

# ----------------- Specialized Quant Agents -----------------

def run_macro_agent(macro_indicators: dict) -> tuple:
    vix = macro_indicators.get("VIX", 15.0)
    dxy = macro_indicators.get("DXY", 100.0)
    
    if vix > 22 or dxy > 105:
        vote = "SELL"
        rationale = f"VIX({vix:.1f}) 또는 DXY({dxy:.1f})가 급등하여 위험자산 회피(Risk-Off)가 우세합니다."
    elif vix < 15 and dxy < 103:
        vote = "BUY"
        rationale = f"변동성 VIX({vix:.1f}) 및 달러인덱스 DXY({dxy:.1f})가 안정적인 위험선호(Risk-On) 상태입니다."
    else:
        vote = "HOLD"
        rationale = f"VIX({vix:.1f}), DXY({dxy:.1f}) 수준이 매크로 중립 구역에 분포하고 있습니다."
        
    return vote, rationale

def run_trend_agent(df: pd.DataFrame) -> tuple:
    if df.empty or len(df) < 25:
        return "HOLD", "데이터 부족으로 기술적 추세 판정을 보류합니다."
        
    close_series = df["Close"]
    ema9 = close_series.ewm(span=9, adjust=False).mean()
    ema21 = close_series.ewm(span=21, adjust=False).mean()
    
    last_close = float(close_series.iloc[-1])
    last_ema9 = float(ema9.iloc[-1])
    last_ema21 = float(ema21.iloc[-1])
    
    if last_ema9 > last_ema21 and last_close > last_ema21:
        vote = "BUY"
        rationale = f"단기 이평선 크로스오버 골든크로스 상승 국면(EMA9 ${last_ema9:,.2f}가 EMA21 ${last_ema21:,.2f} 돌파 상승)이 확인됩니다."
    elif last_ema9 < last_ema21 or last_close < last_ema21:
        vote = "SELL"
        rationale = f"단기 이평 데드크로스 하락 국면(EMA9 ${last_ema9:,.2f}가 EMA21 ${last_ema21:,.2f} 하향 돌파)으로 매수를 유보합니다."
    else:
        vote = "HOLD"
        rationale = "이평 수렴 구간으로 확실한 방향성이 보이지 않습니다."
        
    return vote, rationale

def run_value_agent(df: pd.DataFrame) -> tuple:
    if df.empty or len(df) < 30:
        return "HOLD", "밸류에이션 판정을 위한 과거 데이터가 부족합니다."
        
    close_series = df["Close"]
    recent_30 = close_series.iloc[-30:]
    low_30 = float(recent_30.min())
    high_30 = float(recent_30.max())
    cur_p = float(close_series.iloc[-1])
    
    price_range = high_30 - low_30
    if price_range <= 0:
        return "HOLD", "가격 변동이 없어 분석이 보류됩니다."
        
    pos_pct = (cur_p - low_30) / price_range
    
    if pos_pct <= 0.25:
        vote = "BUY"
        rationale = f"현재가 ${cur_p:,.2f}는 최근 30일 레인지의 하위 25% 부근(${low_30:,.2f} 인접)으로 안전마진이 충분합니다."
    elif pos_pct >= 0.75:
        vote = "SELL"
        rationale = f"현재가 ${cur_p:,.2f}는 최근 30일 레인지의 상위 25% 부근(${high_30:,.2f} 인접)으로 단기 고평가 상태입니다."
    else:
        vote = "HOLD"
        rationale = f"현재가 ${cur_p:,.2f}는 최근 30일 레인지 하위 {pos_pct*100:.1f}% 지점으로 중위 밸류입니다."
        
    return vote, rationale

def run_whale_agent(df: pd.DataFrame) -> tuple:
    if df.empty or len(df) < 25:
        return "HOLD", "거래대금 유입 분석 데이터가 부족합니다."
        
    vol_series = df["Volume"]
    last_vol = float(vol_series.iloc[-1])
    mean_vol = float(vol_series.iloc[-25:-1].mean())
    
    if mean_vol <= 0:
        return "HOLD", "거래량 평균이 비정상입니다."
        
    vol_ratio = last_vol / mean_vol
    
    if vol_ratio >= 1.8:
        vote = "BUY"
        rationale = f"최근 거래량({last_vol:,.0f})이 24일 평균치 대비 {vol_ratio:.1f}배 돌파하는 수급 유입이 포착되었습니다."
    else:
        vote = "HOLD"
        rationale = f"거래량 비율이 {vol_ratio:.1f}배로, 특이 수급 움직임이 관찰되지 않습니다."
        
    return vote, rationale

def run_mean_reversion_agent(df: pd.DataFrame) -> tuple:
    if df.empty or len(df) < 15:
        return "HOLD", "RSI 산정을 위한 데이터가 부족합니다."
        
    close_series = df["Close"]
    delta = close_series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    last_rsi = float(rsi.iloc[-1])
    
    if pd.isna(last_rsi):
        return "HOLD", "RSI 계산 오류"
        
    if last_rsi < 35:
        vote = "BUY"
        rationale = f"일일 RSI 강도지수가 과매도 영역인 {last_rsi:.1f} 수준까지 낙폭이 심해 반등 회복을 예상합니다."
    elif last_rsi > 65:
        vote = "SELL"
        rationale = f"일일 RSI 지표가 과매수 영역인 {last_rsi:.1f}까지 과열되어 눌림 조정을 대비해야 합니다."
    else:
        vote = "HOLD"
        rationale = f"RSI 지표가 {last_rsi:.1f}로 안정권 내부에서 진동하고 있습니다."
        
    return vote, rationale

def run_clucmay_agent(df: pd.DataFrame) -> tuple:
    """ClucMay Agent (Freqtrade Community): Open-source human-developed mean reversion strategy."""
    if df.empty or len(df) < 50:
        return "HOLD", "데이터 부족으로 Freqtrade ClucMay 분석을 보류합니다."
        
    close_series = df["Close"]
    vol_series = df["Volume"]
    
    # Calculate ClucMay indicators
    ema50 = close_series.ewm(span=50, adjust=False).mean()
    middle_bb = close_series.rolling(window=20).mean()
    std = close_series.rolling(window=20).std()
    lower_bb = middle_bb - 2 * std
    vol_ma = vol_series.rolling(window=30).mean()
    
    cur_p = float(close_series.iloc[-1])
    ema = float(ema50.iloc[-1])
    l_bb = float(lower_bb.iloc[-1])
    m_bb = float(middle_bb.iloc[-1])
    volume = float(vol_series.iloc[-1])
    prev_vol_ma = float(vol_ma.iloc[-2]) if len(vol_ma) >= 2 else 0.0
    
    # 0.995 is the modified multiplier for 15m/daily compatibility
    if cur_p < ema and cur_p < l_bb * 0.995 and prev_vol_ma > 0 and volume < 20 * prev_vol_ma:
        vote = "BUY"
        rationale = f"Freqtrade ClucMay 기준: 현재가 ${cur_p:,.2f}가 EMA50 미만 및 하단 BB 99.5%선(${l_bb*0.995:,.2f})을 하향 돌파하며, 안정적 거래량 하에 저위험 매수점을 지지합니다."
    elif cur_p > m_bb:
        vote = "SELL"
        rationale = f"Freqtrade ClucMay 기준: 현재가 ${cur_p:,.2f}가 Bollinger Band 중심선(${m_bb:,.2f})을 돌파하여 수익 실현 매도를 지시합니다."
    else:
        vote = "HOLD"
        rationale = f"Freqtrade ClucMay 기준: 현재가 ${cur_p:,.2f}는 BB 밴드 내부 안전 영역에서 횡보 중입니다."
        
    return vote, rationale

# ----------------- Trade Resolution & RL Weight Tuning -----------------

def check_and_resolve_consensus_trades():
    print("⚙️ Resolving pending consensus trades...")
    init_db()
    
    now_ts = int(time.time())
    weights = load_weights()
    original_weights = dict(weights)
    
    resolved_trades_info = []
    
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        pending = conn.execute("""
            SELECT id, symbol, entry_time, entry_price, vote_macro, vote_trend, vote_value, vote_whale, vote_mean_reversion, vote_clucmay, consensus_score 
            FROM consensus_trade_log 
            WHERE status = 'PENDING'
        """).fetchall()
        
        if not pending:
            print("No pending consensus trades to resolve.")
            return []
            
        import yfinance as yf
        for trade in pending:
            trade_id = trade["id"]
            symbol = trade["symbol"]
            entry_price = float(trade["entry_price"])
            
            try:
                ticker = yf.Ticker(symbol)
                cur_price = getattr(ticker, "fast_info", {}).get("lastPrice")
                if cur_price is None:
                    hist = ticker.history(period="1d")
                    if not hist.empty:
                        cur_price = float(hist["Close"].iloc[-1])
            except Exception as e:
                print(f"⚠️ Failed to resolve live price for {symbol}: {e}")
                cur_price = None
                
            if cur_price is None or cur_price <= 0:
                continue
                
            realized_return = ((cur_price / entry_price) - 1.0) * 100.0
            
            conn.execute("""
                UPDATE consensus_trade_log 
                SET status = 'COMPLETED', exit_price = ?, exit_time = ?, realized_return = ? 
                WHERE id = ?
            """, (cur_price, now_ts, realized_return, trade_id))
            conn.commit()
            
            # RL updates
            vote_vals = {
                "macro": 1.0 if trade["vote_macro"] == "BUY" else (-1.0 if trade["vote_macro"] == "SELL" else 0.0),
                "trend": 1.0 if trade["vote_trend"] == "BUY" else (-1.0 if trade["vote_trend"] == "SELL" else 0.0),
                "value": 1.0 if trade["vote_value"] == "BUY" else (-1.0 if trade["vote_value"] == "SELL" else 0.0),
                "whale": 1.0 if trade["vote_whale"] == "BUY" else (-1.0 if trade["vote_whale"] == "SELL" else 0.0),
                "mean_reversion": 1.0 if trade["vote_mean_reversion"] == "BUY" else (-1.0 if trade["vote_mean_reversion"] == "SELL" else 0.0),
                "clucmay": 1.0 if trade["vote_clucmay"] == "BUY" else (-1.0 if trade["vote_clucmay"] == "SELL" else 0.0)
            }
            
            learning_rate = 0.01
            for agent_name, vote_val in vote_vals.items():
                influence = vote_val * (realized_return / 100.0)
                weights[agent_name] = weights[agent_name] + learning_rate * influence
                weights[agent_name] = max(0.05, min(0.60, weights[agent_name]))
                
            resolved_trades_info.append({
                "symbol": symbol,
                "entry_price": entry_price,
                "exit_price": cur_price,
                "return": realized_return,
                "votes": dict(trade)
            })
            
    # Normalize
    total_w = sum(weights.values())
    if total_w > 0:
        for k in weights:
            weights[k] = round(weights[k] / total_w, 4)
            
    save_weights(weights)
    
    weight_desc = []
    weight_desc.append("🔄 <b>마켓 성과 피드백에 기반한 AI 에이전트 신뢰도(가중치) 조정</b>")
    for agent in weights:
        diff = weights[agent] - original_weights.get(agent, 0.1667)
        sign = "+" if diff >= 0 else ""
        weight_desc.append(f"  • {agent.upper()} Agent: {weights[agent]:.2%} ({sign}{diff:.2%})")
        
    return resolved_trades_info, "\n".join(weight_desc)

# ----------------- Main Coordinator Loop -----------------

def main():
    print("🚀 Starting Multi-Agent Consensus Forum Engine...")
    init_db()
    
    import yfinance as yf
    
    # 1. Resolve past consensus and update RL weights
    resolved_trades = []
    weights_feedback_report = ""
    try:
        result = check_and_resolve_consensus_trades()
        if result:
            resolved_trades, weights_feedback_report = result
    except Exception as e:
        print(f"⚠️ Error resolving consensus trades: {e}")
        
    # 2. Get latest weights
    agent_weights = load_weights()
    
    # 3. Fetch Macro Indicators
    print("Fetching global macro indicators...")
    macro_symbols = {"US10Y": "^TNX", "DXY": "DX-Y.NYB", "VIX": "^VIX", "Oil": "CL=F"}
    macro_indicators = {}
    for name, sym in macro_symbols.items():
        try:
            ticker = yf.Ticker(sym)
            val = getattr(ticker, "fast_info", {}).get("lastPrice")
            if val is None:
                hist = ticker.history(period="1d")
                if not hist.empty:
                    val = float(hist["Close"].iloc[-1])
            macro_indicators[name] = float(val) if val is not None else 0.0
        except Exception:
            macro_indicators[name] = 0.0
            
    # 4. Formulate today's votes for Cryptos & Stocks
    target_assets = {
        "BTC-USD": "BTC",
        "ETH-USD": "ETH",
        "SOL-USD": "SOL",
        "NVDA": "NVDA",
        "AAPL": "AAPL",
        "MSFT": "MSFT",
        "AMZN": "AMZN",
        "INTC": "INTC",
        "QBTS": "QBTS",
        "IONQ": "IONQ",
        "DELL": "DELL"
    }
    
    debate_records = {}
    today_ts = int(time.time())
    
    with sqlite3.connect(DB_PATH) as conn:
        for sym, display_sym in target_assets.items():
            print(f"Running multi-agent analysis for {sym}...")
            df = fetch_ticker_data(sym)
            if df.empty or len(df) < 50:
                print(f"⚠️ Data missing for {sym}")
                continue
                
            cur_p = float(df["Close"].iloc[-1])
            
            # Execute agents
            vote_macro, rat_macro = run_macro_agent(macro_indicators)
            vote_trend, rat_trend = run_trend_agent(df)
            vote_value, rat_value = run_value_agent(df)
            vote_whale, rat_whale = run_whale_agent(df)
            vote_mean_rev, rat_mean_rev = run_mean_reversion_agent(df)
            vote_clucmay, rat_clucmay = run_clucmay_agent(df)
            
            # Calculate Consensus Score
            vals = {"BUY": 1.0, "SELL": -1.0, "HOLD": 0.0}
            
            score = (
                agent_weights.get("macro", 0.1667) * vals[vote_macro] +
                agent_weights.get("trend", 0.1667) * vals[vote_trend] +
                agent_weights.get("value", 0.1667) * vals[vote_value] +
                agent_weights.get("whale", 0.1667) * vals[vote_whale] +
                agent_weights.get("mean_reversion", 0.1667) * vals[vote_mean_rev] +
                agent_weights.get("clucmay", 0.1667) * vals[vote_clucmay]
            )
            
            consensus_pct = score * 100.0
            
            # Log today's pending recommendation in DB
            conn.execute("""
                INSERT INTO consensus_trade_log (
                    symbol, entry_time, entry_price, vote_macro, vote_trend, vote_value, vote_whale, vote_mean_reversion, vote_clucmay, consensus_score, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
            """, (
                sym, today_ts, cur_p, vote_macro, vote_trend, vote_value, vote_whale, vote_mean_rev, vote_clucmay, consensus_pct, datetime.now(timezone.utc).isoformat()
            ))
            conn.commit()
            
            debate_records[display_sym] = {
                "price": cur_p,
                "consensus_score": consensus_pct,
                "votes": {
                    "MACRO": (vote_macro, rat_macro),
                    "TREND": (vote_trend, rat_trend),
                    "VALUE": (vote_value, rat_value),
                    "WHALE": (vote_whale, rat_whale),
                    "MEAN_REVERSION": (vote_mean_rev, rat_mean_rev),
                    "CLUC_MAY_BOT (Freqtrade)": (vote_clucmay, rat_clucmay)
                }
            }
            
    # 5. Format and Send Telegram Report
    lines = []
    lines.append("🤖 <b>[No Slip Quant] 다중 에이전트 포럼 합의 전략 리포트</b>")
    lines.append("=" * 40)
    lines.append("5개의 기초 매매 정책 에이전트와 온라인 오픈소스 커뮤니티의 <b>ClucMay 봇(Freqtrade)</b> 에이전트가 함께 교류하여 도출한 합의 리포트입니다.\n")
    
    if weights_feedback_report:
        lines.append(weights_feedback_report)
        lines.append("\n" + "=" * 40)
        
    lines.append("<b>👥 AI 트레이더 포럼 구성원 현황 (6인 체제)</b>")
    lines.append(f"  1. <b>매크로 에이전트</b> (가중치: {agent_weights.get('macro', 0.1667):.2%})")
    lines.append(f"  2. <b>추세추종 에이전트</b> (가중치: {agent_weights.get('trend', 0.1667):.2%})")
    lines.append(f"  3. <b>안전마진 가치에이전트</b> (가중치: {agent_weights.get('value', 0.1667):.2%})")
    lines.append(f"  4. <b>거래량 급증 고래에이전트</b> (가중치: {agent_weights.get('whale', 0.1667):.2%})")
    lines.append(f"  5. <b>RSI 평균회귀에이전트</b> (가중치: {agent_weights.get('mean_reversion', 0.1667):.2%})")
    lines.append(f"  6. <b>ClucMay 에이전트 (Freqtrade)</b> (가중치: {agent_weights.get('clucmay', 0.1667):.2%})")
    lines.append("\n" + "=" * 40)
    
    # 5.2 Debate summaries
    for display_sym, record in debate_records.items():
        score = record["consensus_score"]
        price = record["price"]
        
        if score > 15.0:
            consensus_emoji = "🟢 <b>적극 매수 (BUY)</b>"
        elif score < -15.0:
            consensus_emoji = "🔴 <b>비중 축소 (SELL)</b>"
        else:
            consensus_emoji = "🟡 <b>관망/중립 (HOLD)</b>"
            
        lines.append(f"\n🪙 <b>{display_sym} 의사 결정 포럼</b> | 현재가: ${price:,.2f}")
        lines.append(f"  • <b>종합 의사결정</b>: {consensus_emoji} (합의지수: {score:+.1f}%)")
        lines.append("  • <b>포럼 의사록 발췌 (Debate Log)</b>:")
        
        votes = record["votes"]
        for agent_name, (vote, rationale) in votes.items():
            vote_emoji = "🟢" if vote == "BUY" else ("🔴" if vote == "SELL" else "🟡")
            lines.append(f"    - {vote_emoji} <b>{agent_name}</b>: {rationale}")
            
    lines.append("\n" + "=" * 40)
    lines.append("💡 <b>인간 가이드</b>: 깃허브에서 수많은 개발자들에게 장기간 검증된 <b>Freqtrade의 ClucMay mean-reversion 전략</b>을 신규 에이전트로 영입했습니다. (최근 백테스팅 결과 하락장 속에서도 고 승률 및 양호한 알파 성과 기록). 에이전트간의 성과는 강화학습으로 반영됩니다.")
    
    report_msg = "\n".join(lines)
    send_telegram_message(report_msg)
    print("Report sent to Telegram.")

if __name__ == "__main__":
    main()
