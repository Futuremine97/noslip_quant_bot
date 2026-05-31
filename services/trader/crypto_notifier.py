#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import sys
import subprocess
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# .env 로드
load_dotenv(dotenv_path=ROOT_DIR / ".env")

def extract_json(stdout_text: str) -> dict:
    for line in reversed(stdout_text.split("\n")):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except Exception:
                pass
    return {}

def run_prediction(symbol: str) -> dict:
    python_bin = ROOT_DIR / "services" / "trader" / ".venv" / "bin" / "python"
    script_path = ROOT_DIR / "services" / "trader" / "predict_signal.py"
    
    print(f"⌛ Running prediction for {symbol}...")
    res = subprocess.run(
        [str(python_bin), str(script_path), "--symbol", symbol, "--market-mode", "crypto"],
        capture_output=True,
        text=True,
        cwd=str(ROOT_DIR)
    )
    
    if res.returncode != 0:
        print(f"❌ Failed to run prediction for {symbol}: {res.stderr}")
        return {}
        
    return extract_json(res.stdout)

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

def main():
    symbols = ["BTC", "ETH", "SOL"]
    reports = []
    
    date_str = datetime.today().strftime("%Y-%m-%d")
    
    for symbol in symbols:
        data = run_prediction(symbol)
        if not data:
            continue
            
        cur_price = float(data.get("currentPrice") or 0)
        action = data.get("finalAction", "HOLD")
        
        target_price = float(data.get("targetPrice") or cur_price)
        target_date = (data.get("targetTimestamp") or "N/A")[5:10] # MM-DD
        
        # 기대 상승률 계산
        if cur_price > 0:
            upside = ((target_price / cur_price) - 1.0) * 100.0
        else:
            upside = 0.0
            
        # 매수/매도 기점
        buy_price = float(data.get("optimalBuyPrice") or 0)
        buy_date = (data.get("optimalBuyTimestamp") or "N/A")[5:10]
        sell_price = float(data.get("optimalSellPrice") or 0)
        sell_date = (data.get("optimalSellTimestamp") or "N/A")[5:10]
        
        recommendation = data.get("recommendation", {})
        summary = recommendation.get("summary", "의견 없음")
        
        # 아이콘 결정
        icon = "🟢" if action == "BUY" else "🔴" if action == "SELL" else "🟡"
        
        p_trend = float(data.get("prophetTrend") or 0)
        p_slope = float(data.get("prophetTrendSlope") or 0)
        p_weekly = float(data.get("prophetWeekly") or 0)
        p_monthly = float(data.get("prophetMonthly") or 0)

        report = []
        report.append(f"{icon} <b>{symbol} ({action})</b>")
        report.append(f"  • 현재가: ${cur_price:,.2f}")
        if p_trend > 0 or p_weekly != 0 or p_monthly != 0:
            report.append(
                f"  • <b>[Prophet 예측]</b>: 트렌드 ${p_trend:,.2f} (일변화: {p_slope:+.4f}), "
                f"주간: {p_weekly*100.0:+.2f}%, 월간: {p_monthly*100.0:+.2f}%"
            )
        report.append(f"  • <b>[AI 타겟가]</b>: ${target_price:,.2f} (기대치: +{upside:.1f}% / 시점: {target_date})")
        report.append(f"  • <b>[최적 매수]</b>: ${buy_price:,.2f} (기점: {buy_date})")
        report.append(f"  • <b>[최적 매도]</b>: ${sell_price:,.2f} (기점: {sell_date})")
        report.append(f"  • <b>[AI 종합분석]</b>: {summary}")
        reports.append("\n".join(report))
        
    if not reports:
        print("⚠️ No prediction data generated.")
        return
        
    full_message = []
    full_message.append(f"🪙 <b>[No Slip] 주요 코인 AI 시황 & 매매 타겟 ({date_str})</b>")
    full_message.append("=" * 40)
    full_message.append("\n\n".join(reports))
    full_message.append("\n" + "=" * 40)
    full_message.append("※ 본 코인 보고서는 4시간 기준 Prophet 예측 모델과 AI 카운슬의 합의 의사결정을 통해 생성됩니다.")
    
    msg_str = "\n".join(full_message)
    print("\n--- Generated Message ---")
    print(msg_str)
    print("-------------------------\n")
    
    send_telegram_message(msg_str)

if __name__ == "__main__":
    main()
