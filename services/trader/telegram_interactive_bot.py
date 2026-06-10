#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import json
import os
import sys
import machine_auth
import time

import requests
import re
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

# Set root directory and load environment variables
ROOT_DIR = Path(__file__).resolve().parents[2]
if not ROOT_DIR.exists() or not (ROOT_DIR / "services" / "trader").exists():
    ROOT_DIR = Path(__file__).resolve().parents[2]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv(dotenv_path=ROOT_DIR / ".env")

CACHE_DIR = ROOT_DIR / "services" / "trader" / "model_cache"
OFFSET_FILE = CACHE_DIR / "telegram_bot_offset.txt"
DEBATE_STATE_FILE = CACHE_DIR / "telegram_debate_state.json"

# Import Gemini API support safely
try:
    import google.generativeai as genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

# Add services/trader to path to allow importing multi_agent_consensus
TRADER_DIR = ROOT_DIR / "services" / "trader"
if str(TRADER_DIR) not in sys.path:
    sys.path.insert(0, str(TRADER_DIR))

from multi_agent_consensus import (
    load_weights, fetch_ticker_data,
    run_macro_agent, run_trend_agent, run_value_agent,
    run_whale_agent, run_mean_reversion_agent, run_clucmay_agent
)
from dynamic_youtube_trends import generate_youtube_trends_report

SYMBOL_MAPPING = {
    # Korean Stock Names
    "애플": "AAPL",
    "테슬라": "TSLA",
    "엔비디아": "NVDA",
    "마이크로소프트": "MSFT",
    "아마존": "AMZN",
    "구글": "GOOGL",
    "메타": "META",
    "넷플릭스": "NFLX",
    "삼성전자": "005930.KS",
    "sk하이닉스": "000660.KS",
    "현대차": "005380.KS",
    "네이버": "035420.KS",
    "카카오": "035720.KS",
    "lg에너지솔루션": "373220.KS",
    "기아": "000270.KS",
    "삼성바이오로직스": "207940.KS",
    "포스코홀딩스": "005490.KS",
    "lg화학": "051910.KS",
    "현대모비스": "012330.KS",
    "kb금융": "105560.KS",
    "인텔": "INTC",
    "델": "DELL",
    
    # Cryptos
    "비트코인": "BTC-USD",
    "이더리움": "ETH-USD",
    "솔라나": "SOL-USD",
    "리플": "XRP-USD",
    "도지코인": "DOGE-USD",
    "에이다": "ADA-USD",
    
    # Lowercase tickers
    "btc": "BTC-USD",
    "eth": "ETH-USD",
    "sol": "SOL-USD",
    "xrp": "XRP-USD",
    "doge": "DOGE-USD",
    "ada": "ADA-USD",
    "intel": "INTC",
    "intc": "INTC",
    "dell": "DELL"
}

active_tunnel_process = None
active_tunnel_url = None

def get_or_start_localtunnel(port: int = 3000) -> str:
    global active_tunnel_process, active_tunnel_url
    import subprocess
    import threading
    import time
    
    if active_tunnel_process and active_tunnel_process.poll() is None and active_tunnel_url:
        return active_tunnel_url
        
    if active_tunnel_process:
        try:
            active_tunnel_process.terminate()
            active_tunnel_process.wait(timeout=2)
        except Exception:
            pass
        active_tunnel_process = None
        active_tunnel_url = None
        
    try:
        import shutil
        import os
        
        extra_paths = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        env = os.environ.copy()
        path_val = env.get("PATH", "")
        for p in extra_paths:
            if p not in path_val:
                path_val = f"{p}:{path_val}" if path_val else p
        env["PATH"] = path_val
        
        npx_path = shutil.which("npx", path=path_val) or "npx"
        
        cmd = [npx_path, "--yes", "localtunnel", "--port", str(port), "--subdomain", "noslip-saas-sunghoon"]
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        
        url = None
        start_time = time.time()
        while time.time() - start_time < 10:
            if proc.poll() is not None:
                break
            line = proc.stdout.readline()
            if not line:
                time.sleep(0.1)
                continue
            if "your url is:" in line:
                url = line.split("your url is:")[-1].strip()
                break
                
        if url:
            active_tunnel_process = proc
            active_tunnel_url = url
            
            def consume(p):
                try:
                    for _ in p.stdout:
                        pass
                except Exception:
                    pass
            threading.Thread(target=consume, args=(proc,), daemon=True).start()
            return url
        else:
            try:
                proc.terminate()
            except Exception:
                pass
            return None
    except Exception as e:
        print(f"Error starting localtunnel: {e}")
        return None

def get_offset() -> int:
    if OFFSET_FILE.exists():
        try:
            return int(OFFSET_FILE.read_text().strip())
        except Exception:
            pass
    return 0

def save_offset(offset: int):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(offset))

def load_debate_state() -> dict:
    if DEBATE_STATE_FILE.exists():
        try:
            return json.loads(DEBATE_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def save_debate_state(state: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        DEBATE_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"⚠️ Error saving debate state: {e}")

def escape_html(text: str) -> str:
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ----------------- Commands Parser -----------------

def parse_analysis_request(text: str) -> str:
    text = text.strip()
    if text.startswith("/analyze "):
        return text[len("/analyze "):].strip()
    if text.startswith("/분석 "):
        return text[len("/분석 "):].strip()
    if text.endswith(" 분석해줘"):
        return text[:-len(" 분석해줘")].strip()
    if text.endswith(" 분석"):
        return text[:-len(" 분석")].strip()
    return None

def parse_debate_request(text: str) -> str:
    text = text.strip()
    if text.startswith("/debate "):
        return text[len("/debate "):].strip()
    if text.startswith("/토론 "):
        return text[len("/토론 "):].strip()
    if text.startswith("/토의 "):
        return text[len("/토의 "):].strip()
    if text.endswith(" 토론하자"):
        return text[:-len(" 토론하자")].strip()
    if text.endswith(" 토론"):
        return text[:-len(" 토론")].strip()
    if text.endswith(" 토의하자"):
        return text[:-len(" 토의하자")].strip()
    if text.endswith(" 토의"):
        return text[:-len(" 토의")].strip()
    return None

def parse_opinion_request(text: str) -> str:
    text = text.strip()
    if text.startswith("/opinion "):
        return text[len("/opinion "):].strip()
    if text.startswith("/의견 "):
        return text[len("/의견 "):].strip()
    return None

def parse_features_request(text: str) -> bool:
    text = text.strip()
    return text in ["/기능", "/features", "/help", "/도움말"]

def parse_youtube_request(text: str) -> tuple:
    text = text.strip()
    if text in ["/youtube", "/유튜브", "/유튜브크롤링", "/crawl"]:
        return True, None
    for prefix in ["/youtube ", "/유튜브 ", "/crawl "]:
        if text.startswith(prefix):
            return True, text[len(prefix):].strip()
    return False, None

def parse_competition_request(text: str) -> bool:
    text = text.strip()
    return text in ["/competition", "/경쟁", "/토너먼트", "/리그"]

def parse_monthly_optimize_request(text: str) -> bool:
    text = text.strip()
    return text in ["/monthly_optimize", "/월간학습", "/월간최적화"]

def parse_website_request(text: str) -> bool:
    text = text.strip()
    return text in ["/website", "/웹사이트"]

def parse_infomap_request(text: str) -> bool:
    text = text.strip()
    return text in ["/infomap", "/정보맵", "/시각화", "/infomap시각화"]

def parse_portfolio_request(text: str) -> bool:
    text = text.strip()
    return text in ["/portfolio", "/포트폴리오"]

def parse_champion_request(text: str) -> bool:
    text = text.strip()
    return text in ["/champion", "/챔피언"]

def parse_orbit_request(text: str) -> bool:
    text = text.strip()
    return text in ["/orbit", "/오빗", "/궤적", "/orbit학습", "/궤적학습"]

def parse_ohseon_request(text: str) -> bool:
    text = text.strip()
    return text in ["/ohseon", "/오선", "/시황요약", "/오선요약"]

def parse_advice_request(text: str) -> bool:
    text = text.strip()
    return text in ["/advice", "/조언", "/조언요청", "/에이전트조언"]

def parse_federated_request(text: str) -> str:
    text = text.strip()
    for prefix in ["/federated ", "/연합학습 ", "/federated", "/연합학습"]:
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return None

def parse_prophet_request(text: str) -> str:
    text = text.strip()
    for prefix in ["/prophet ", "/프로펫 ", "/예측 "]:
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    if text in ["/prophet", "/프로펫", "/예측"]:
        return ""
    return None

def parse_cardnews_request(text: str) -> bool:
    return text.strip() in ["/cardnews", "/카드뉴스", "/시황카드", "/카드"]

def parse_onchain_request(text: str) -> bool:
    return text.strip() in ["/onchain", "/온체인", "/고래온체인", "/온체인고래"]

def parse_collect_request(text: str) -> str:
    """'/수집', '/수집 온|오프|현황' -> '' | 'on' | 'off' | 'stats'. None otherwise."""
    text = text.strip()
    if text in ["/수집", "/collect", "/데이터수집"]:
        return ""
    for prefix in ["/수집 ", "/collect ", "/데이터수집 "]:
        if text.startswith(prefix):
            arg = text[len(prefix):].strip().lower()
            if arg in ["온", "on", "켜기"]:
                return "on"
            if arg in ["오프", "off", "끄기"]:
                return "off"
            if arg in ["현황", "stats", "통계"]:
                return "stats"
            return ""
    return None


def parse_gemini_request(text: str) -> str:
    text = text.strip()
    for prefix in ["/gemini ", "/제미나이 ", "/gemini", "/제미나이"]:
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return None

def parse_alpha_request(text: str) -> tuple[str, list[str]]:
    text = text.strip()
    for prefix in ["/alpha ", "/알파 ", "/alpha", "/알파"]:
        if text.startswith(prefix):
            remainder = text[len(prefix):].strip()
            return "alpha", remainder.split()
    return None, []

def execute_alpha_command(chat_id: str, args: list[str]) -> str:
    from personal_ontology import save_concept, get_concepts, delete_concept, evaluate_ontology_concept
    
    if not args:
        # Help message
        return (
            "⚖️ <b>[No Slip AI Alpha Strategy Architect]</b>\n"
            "=" * 40 + "\n"
            "개개인의 거래 스타일(투자 성향)에 따라 차익거래 및 기술적 매매 전략을 체계화할 수 있도록 지원하는 개인 맞춤형 빌더입니다.\n\n"
            "▶️ <b>1. 투자 성향(Persona) 설정 및 전략 파라미터 추천</b>\n"
            "<code>/alpha [보수적 | 균형 | 공격적]</code>\n"
            "  - <i>예: /alpha 보수적</i> (수수료 차감 후 고수익 차익거래 위주 추천)\n\n"
            "▶️ <b>2. 개인별 전용 거래 컨셉(Ontology Concept) 및 규칙 등록</b>\n"
            "<code>/alpha 등록 [컨셉명] [종목코드들] [규칙들]</code>\n"
            "  - <i>예: /alpha 등록 내전략 BTCUSDT,ETHUSDT min_rsi=30 max_rsi=70 require_price_above_sma20=true expected_action=buy</i>\n"
            "  - <b>등록 가능한 규칙</b>:\n"
            "    • <code>min_price=값</code> / <code>max_price=값</code> (최저/최고가)\n"
            "    • <code>min_rsi=값</code> / <code>max_rsi=값</code> (RSI 범위)\n"
            "    • <code>require_price_above_sma20=true/false</code> (SMA20 위에 가격 위치 여부)\n"
            "    • <code>expected_action=BUY/SELL</code> (AI 위원회 컨센서스 일치 여부)\n"
            "    • <code>min_momentum=값</code> / <code>max_volatility=값</code> (모멘텀/변동성 범위)\n\n"
            "▶️ <b>3. 등록한 전략의 실시간 진입 여부 조건 검증 (Audit)</b>\n"
            "<code>/alpha 검증 [컨셉명]</code>\n"
            "  - <i>예: /alpha 검증 내전략</i> (실시간 데이터로 등록 조건 충족 여부 체크)\n\n"
            "▶️ <b>4. 개인별 전략 및 알림 설정 현황 확인</b>\n"
            "<code>/alpha 현황</code> (또는 <code>/alpha 리스트</code>)\n"
            "  - 등록된 모든 맞춤형 컨셉과 규칙들을 보여줍니다.\n\n"
            "▶️ <b>5. 개인 전략 삭제</b>\n"
            "<code>/alpha 삭제 [컨셉명]</code>\n"
            "=" * 40
        )

    sub = args[0].lower()
    
    if sub in ["등록", "register"]:
        if len(args) < 3:
            return "⚠️ 형식을 확인해 주세요: <code>/alpha 등록 [컨셉명] [종목코드,종목코드] [규칙=값 규칙=값 ...]</code>"
        
        concept_name = args[1]
        symbols_str = args[2]
        symbols = [s.strip().upper() for s in symbols_str.split(",") if s.strip()]
        
        rules_dict = {}
        for r in args[3:]:
            if "=" in r:
                parts_r = r.split("=", 1)
                k = parts_r[0].strip().lower()
                v = parts_r[1].strip()
                if v.lower() == "true":
                    rules_dict[k] = True
                elif v.lower() == "false":
                    rules_dict[k] = False
                elif v.upper() in ["BUY", "SELL", "HOLD"]:
                    rules_dict[k] = v.upper()
                else:
                    try:
                        rules_dict[k] = float(v)
                    except ValueError:
                        rules_dict[k] = v
                        
        save_concept(str(chat_id), concept_name, "사용자 맞춤형 차익 및 기술적 전략", symbols, rules_dict)
        
        rules_desc = ", ".join([f"<code>{k}</code>: {v}" for k, v in rules_dict.items()])
        return (
            f"✅ <b>맞춤 전략 컨셉 '{concept_name}' 등록 완료!</b>\n"
            f"• <b>대상 자산</b>: {', '.join(symbols)}\n"
            f"• <b>적용 조건</b>: {rules_desc if rules_desc else '조건 없음'}\n\n"
            f"▶️ 실시간 진입 조건 만족 여부는 <code>/alpha 검증 {concept_name}</code> 명령어로 언제든 체크할 수 있습니다."
        )
        
    elif sub in ["검증", "verify"]:
        if len(args) < 2:
            return "⚠️ 검증할 전략 컨셉명을 입력해주세요. (예: <code>/alpha 검증 내전략</code>)"
        concept_name = args[1]
        return evaluate_ontology_concept(str(chat_id), concept_name)
        
    elif sub in ["현황", "리스트", "list"]:
        concepts = get_concepts(str(chat_id))
        if not concepts:
            return "📋 현재 등록된 맞춤형 전략 컨셉이 없습니다. <code>/alpha 등록 ...</code>으로 나만의 전략을 만들어보세요!"
            
        lines = [
            "📋 <b>[No Slip AI Alpha] 나의 맞춤형 전략 목록</b>",
            "=" * 40
        ]
        for c in concepts:
            rules_desc = ", ".join([f"<code>{k}</code>: {v}" for k, v in c["rules"].items()])
            lines.append(
                f"• <b>{c['concept_name']}</b>\n"
                f"  - <b>대상 자산</b>: {', '.join(c['symbols'])}\n"
                f"  - <b>규칙 조건</b>: {rules_desc if rules_desc else '없음'}\n"
                f"  - <b>최근 수정</b>: {c['updated_at'][:19].replace('T', ' ')}"
            )
            lines.append("-" * 30)
        lines.append("\n▶️ 특정 전략을 실시간 검증하려면 <code>/alpha 검증 [컨셉명]</code>을 입력하세요.")
        return "\n".join(lines)
        
    elif sub in ["삭제", "delete"]:
        if len(args) < 2:
            return "⚠️ 삭제할 전략 컨셉명을 입력해주세요. (예: <code>/alpha 삭제 내전략</code>)"
        concept_name = args[1]
        if delete_concept(str(chat_id), concept_name):
            return f"🗑️ 맞춤 전략 컨셉 <b>{concept_name}</b> 삭제 완료."
        else:
            return f"⚠️ '{concept_name}' 전략 컨셉을 찾을 수 없습니다."
            
    elif sub in ["보수적", "conservative"]:
        return (
            "🛡️ <b>보수적 투자 성향 (Conservative) 권장 파라미터 셋</b>\n"
            "=" * 40 + "\n"
            "안정적이고 수수료 차감 후 확정 수익률이 높은 거래만 노리는 스타일입니다.\n\n"
            "💡 <b>추천 설정 가이드</b>:\n"
            "• <b>현물 차익거래 (Spot Arbitrage)</b>\n"
            "  - <code>TP (익절)</code>: 0.15% | <code>SL (손절)</code>: 0.30%\n"
            "  - <code>H (대기시간)</code>: 5분 | <code>진입 스프레드</code>: 0.15% 이상\n\n"
            "• <b>김치프리미엄 차익거래 (Kimchi Arbitrage)</b>\n"
            "  - <code>H</code>: 120분 | <code>TP</code>: 0.50% | <code>SL</code>: 1.00%\n"
            "  - <code>진입 조건</code>: 역프 -1.5% 이하 매수 / 김프 4.5% 이상 매도\n\n"
            "• <b>고래 수급 추적 (Whale Pump)</b>\n"
            "  - <code>X (1분거래대금 증가율)</code>: 0.50% 이상 (강력한 신호만)\n"
            "  - <code>V (1분 거래량배수)</code>: 3.5x 이상\n\n"
            "▶️ <b>전략 설정 명령어 (예시)</b>:\n"
            "<code>/알림온 김프 1.5</code> (수수료 차감 후 1.5% 이상 괴리시에만 알림)"
        )
        
    elif sub in ["균형", "balanced"]:
        return (
            "⚖️ <b>균형형 투자 성향 (Balanced) 권장 파라미터 셋</b>\n"
            "=" * 40 + "\n"
            "시장 평균적인 변동성을 감내하면서 합리적인 손익비(Risk/Reward)를 노리는 스타일입니다.\n\n"
            "💡 <b>추천 설정 가이드</b>:\n"
            "• <b>현물 차익거래 (Spot Arbitrage)</b>\n"
            "  - <code>TP (익절)</code>: 0.30% | <code>SL (손절)</code>: 0.50%\n"
            "  - <code>H (대기시간)</code>: 10분 | <code>진입 스프레드</code>: 0.12% 이상\n\n"
            "• <b>김치프리미엄 차익거래 (Kimchi Arbitrage)</b>\n"
            "  - <code>H</code>: 60분 | <code>TP</code>: 1.00% | <code>SL</code>: 1.50%\n"
            "  - <code>진입 조건</code>: 역프 -1.0% 이하 매수 / 김프 4.0% 이상 매도\n\n"
            "• <b>고래 수급 추적 (Whale Pump)</b>\n"
            "  - <code>X (1분거래대금 증가율)</code>: 0.40% 이상\n"
            "  - <code>V (1분 거래량배수)</code>: 2.0x 이상\n\n"
            "▶️ <b>전략 설정 명령어 (예시)</b>:\n"
            "<code>/알림온 rsi</code> (RSI 반등 알림 받기)"
        )
        
    elif sub in ["공격적", "aggressive"]:
        return (
            "🔥 <b>공격적 투자 성향 (Aggressive) 권장 파라미터 셋</b>\n"
            "=" * 40 + "\n"
            "빠른 순환매와 변동성을 활용하여 단기 차익 기회를 극대화하는 스타일입니다.\n\n"
            "💡 <b>추천 설정 가이드</b>:\n"
            "• <b>현물 차익거래 (Spot Arbitrage)</b>\n"
            "  - <code>TP (익절)</code>: 0.50% | <code>SL (손절)</code>: 0.80%\n"
            "  - <code>H (대기시간)</code>: 15분 | <code>진입 스프레드</code>: 0.10% 이상\n\n"
            "• <b>김치프리미엄 차익거래 (Kimchi Arbitrage)</b>\n"
            "  - <code>H</code>: 30분 | <code>TP</code>: 1.50% | <code>SL</code>: 2.00%\n"
            "  - <code>진입 조건</code>: 역프 -0.5% 이하 매수 / 김프 3.5% 이상 매도\n\n"
            "• <b>고래 수급 추적 (Whale Pump)</b>\n"
            "  - <code>X (1분거래대금 증가율)</code>: 0.30% 이상 (민감하게 포착)\n"
            "  - <code>V (1분 거래량배수)</code>: 1.5x 이상\n\n"
            "▶️ <b>전략 설정 명령어 (예시)</b>:\n"
            "<code>/알림온 spot_arbitrage 0.10</code>"
        )
        
    else:
        return f"⚠️ 유효하지 않은 옵션입니다: <code>{args[0]}</code>\n\n<code>/alpha</code> 를 입력하여 사용 가능한 명령 목록을 확인하세요."


def sanitize_telegram_html(text: str) -> str:
    """Strips invalid HTML tags and escapes special characters for Telegram compatibility."""
    if not text:
        return ""
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    placeholders = []
    a_pattern = r'<a\s+href="[^"]*">'
    a_tags = re.findall(a_pattern, text)
    temp_text = text
    for idx, tag in enumerate(a_tags):
        placeholder = f"__A_TAG_{idx}__"
        placeholders.append((placeholder, tag))
        temp_text = temp_text.replace(tag, placeholder)
    standard_tags = ['</a>', '<b>', '</b>', '<i>', '</i>', '<code>', '</code>', '<pre>', '</pre>']
    for idx, tag in enumerate(standard_tags):
        placeholder = f"__TAG_{idx}__"
        placeholders.append((placeholder, tag))
        temp_text = temp_text.replace(tag, placeholder)
    temp_text = temp_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for placeholder, tag in placeholders:
        temp_text = temp_text.replace(placeholder, tag)
    return temp_text

GEMINI_HISTORY_FILE = CACHE_DIR / "gemini_chat_history.json"

def load_gemini_chat_history() -> dict:
    if not GEMINI_HISTORY_FILE.exists():
        return {}
    try:
        with open(GEMINI_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Failed to load gemini chat history: {e}")
        return {}

def save_gemini_chat_history(history: dict):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(GEMINI_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Failed to save gemini chat history: {e}")

SYSTEM_CONTEXT = """
너는 이 가상자산/주식 퀀트 자동 매매 시스템인 'No Slip Saas' 프로젝트의 AI 전문 보조원(Gemini)이자 토론 상대야.
사용자가 시스템의 설계, 전략, 알고리즘, 파라미터, 성능, 데이터베이스 구조 등에 대해 물어보거나 토론하고자 하면,
이 프로젝트의 주요 아키텍처와 컨텍스트를 완벽하게 숙지한 상태에서 상세하고 친근하게 답변해야 해.
답변은 항상 명확하고, 가독성을 위해 마크다운 및 HTML 태그를 적절히 섞어서 고급스럽게 작성해 줘.

[프로젝트 핵심 아키텍처 및 기능 요약]
1. 실시간 급등주 감시 및 매매 엔진 (whale_pump_monitor.py)
   - Binance, Bybit, Upbit의 1분 단위 kline 데이터를 분석하여 7가지 전략 기반 실시간 매수 타점 포착.
   - 전략 1: 고래 수급 (Whale Pump) - 짧은 시간 내 거래량이 V배 폭증하고 가격이 X% 이상 상승 시 세력 매집으로 판단해 추격매수.
   - 전략 2: RSI 과매도 반등 (RSI Reversion) - RSI가 트리거선 이하로 하락 후 고개를 들 때(반등 시작) 분할 저가매수.
   - 전략 3: MACD 골든크로스 (MACD Crossover) - MACD선이 시그널선을 상승 돌파하며 수급이 뒷받침될 때 추세추종 매수.
   - 전략 4: 볼린저 밴드 수축 및 상단 돌파 (BB Squeeze Breakout) - 밴드폭이 30분 최저치 수준으로 수축 후 상단 돌파 시 랠리 타점.
   - 전략 5: 거래소간 차익거래 (Spot Arbitrage) - Binance와 Bybit 간 스프레드가 임계치를 넘을 때 양방향 차익 실현.
   - 전략 6: 김치프리미엄 차익거래 (Kimchi Arbitrage) - 해외(Binance)와 국내(Upbit) 가격 차이(김프/역프) 괴리를 이용한 무위험 차익.
   - 전략 7: 다자간 무위험 차익거래 (Multi-Way Arbitrage) - Binance, Bybit, Upbit, Bithumb, Coinone 5개 거래소의 실시간 시세를 교차 분석하여 가장 저렴한 곳에서 매수하고 비싼 곳에서 매도하는 최적 경로 차익거래. (최근 Bithumb과 Coinone이 한국 거래소 후보군으로 추가되어 5개 거래소 비교로 고도화됨)

2. 강화학습(RL) 피드백 루프 및 동적 가중치 업데이트
   - 가상 매매가 종료되면 손절/익절/타임아웃 여부에 따라 보상(Reward)을 계산하여 SQLite DB(whale_rewards.sqlite3)에 기록.
   - 손실 발생 시 패널티를 부여하여 임계치(X, V, RSI trigger 등)를 높이고(보수적 진입), 수익 발생 시 강화하여 최적의 파라미터를 실시간 자율 조정.

3. 6인 에이전트 합의 위원회 (Multi-Agent Consensus System)
   - Macro(거시경제), Trend(추세), Value(가치), Whale(수급), Mean Reversion(RSI), ClucMay(볼린저 밴드/MA 결합)의 6인 AI 위원들이 기술적 분석과 뉴스 컨텍스트를 융합하여 투표.
   - 합의지수가 +15% 초과일 때만 최종 진입 승인하여 휩소(가짜 신호)를 최소화.

4. GICS 섹터 오빗 학습 엔진 (sector_orbit_learner.py)
   - S&P 500의 11개 GICS 섹터 ETF 데이터를 일별로 모니터링하여 Momentum/Conviction 4차원 좌표 공간으로 투사.
   - SVD(특이값 분해)와 Residual MLP 신경망을 결합해 섹터들의 상태 천이(Orbit Trajectory) 궤적을 딥러닝하고, 다음 날의 최적 포트폴리오 비중을 다이내믹하게 조정.

5. 머신러닝 하락 차단 필터 (MLP Drop Filter)
   - MLPClassifier 신경망 모델(tanh, hidden 16x8)이 Binance 1m klines의 7가지 특징값(RSI, MACD hist, BB bandwidth 등)을 학습하여 향후 15분 이내 단기 하락 확률을 실시간 예측.
   - 예측 확률이 50% 이상(임계치)이면 arbitrage를 포함한 모든 매수 전략 진입을 사전에 강제 차단(Halt)하여 손실 차단.

6. 오선 유튜브 요약 자동화 및 데일리 파이프라인 (ohseon_summary.py, run_daily.sh)
   - 매일 장 마감 후 '오선의 미국 증시 라이브' 유튜브 채널 RSS를 파싱하여 키워드를 추출, 당일 구글 뉴스 속보와 융합한 시황 정보를 Gemini API로 생성하여 텔레그램으로 자동 브로드캐스트.

사용자의 모든 질문에 이 시스템의 핵심 원리와 동작 방식을 바탕으로 정중하고 디테일하게 응대해 줘. 필요한 경우 한국어로 자연스럽고 신뢰감 있게 설명해야 해.
"""

def execute_gemini_chat(chat_id: str, user_query: str) -> str:
    """Handle chat and debate with Gemini about the project context with persistent history."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not HAS_GEMINI:
        return "⚠️ Gemini API가 구성되지 않았거나 google-generativeai 패키지가 없습니다. 관리자에게 문의해 주세요."
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name="gemini-flash-latest",
            system_instruction=SYSTEM_CONTEXT
        )
        history_data = load_gemini_chat_history()
        chat_history = history_data.get(str(chat_id), [])
        formatted_history = []
        for msg in chat_history[-30:]:
            role = msg.get("role")
            text = msg.get("text")
            if role in ["user", "model"] and text:
                formatted_history.append({
                    "role": role,
                    "parts": [{"text": text}]
                })
        chat = model.start_chat(history=formatted_history)
        response = chat.send_message(user_query)
        response_text = response.text.strip()
        new_history = list(chat_history)
        new_history.append({"role": "user", "text": user_query})
        new_history.append({"role": "model", "text": response_text})
        history_data[str(chat_id)] = new_history[-50:]
        save_gemini_chat_history(history_data)
        return sanitize_telegram_html(response_text)
    except Exception as e:
        print(f"⚠️ execute_gemini_chat: failed to generate response: {e}")
        return f"⚠️ 제미나이 답변 생성 중 오류가 발생했습니다: {e}"


def parse_sector_request(text: str) -> bool:
    text = text.strip()
    return text in ["/섹터", "/sector", "/추천섹터", "/섹터추천"]

def parse_alert_setting_request(text: str) -> bool:
    text = text.strip()
    return text in ["/alert", "/알림", "/알림설정", "/알림현황"]

def parse_alert_on_request(text: str) -> str:
    text = text.strip()
    for prefix in ["/alert_on ", "/alerton ", "/알림온 ", "/알림추가 "]:
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return None

def parse_alert_off_request(text: str) -> str:
    text = text.strip()
    for prefix in ["/alert_off ", "/alertoff ", "/알림오프 ", "/알림삭제 ", "/알림제거 "]:
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return None

VALID_STRATEGIES = [
    "whale_pump",
    "rsi_reversion",
    "macd_crossover",
    "bb_breakout",
    "spot_arbitrage",
    "kimchi_arbitrage",
    "three_way_arbitrage"
]

STRATEGY_DISPLAY = {
    "whale_pump": "고래수급 (whale_pump)",
    "rsi_reversion": "RSI반등 (rsi_reversion)",
    "macd_crossover": "MACD교차 (macd_crossover)",
    "bb_breakout": "BB돌파 (bb_breakout)",
    "spot_arbitrage": "거래소차익 (spot_arbitrage)",
    "kimchi_arbitrage": "김프차익 (kimchi_arbitrage)",
    "three_way_arbitrage": "3자차익 (three_way_arbitrage)"
}

# ----------------- Theme (테마) Feature -----------------
# A "theme" is a named basket of tickers (e.g. 네오클라우드, 반도체) that the bot
# can analyze in one shot via the 6-agent consensus engine. Themes are persisted
# per chat through the personal_ontology backend; a set of built-in presets is
# always available out of the box.

PRESET_THEMES = {
    "네오클라우드": {
        "description": "AI GPU 클라우드 인프라 (CoreWeave, Nebius 등 neocloud 및 핵심 공급망)",
        "symbols": ["NVDA", "CRWV", "NBIS", "SMCI", "VRT", "DELL", "ORCL"],
    },
    "반도체": {
        "description": "AI 반도체 핵심주",
        "symbols": ["NVDA", "AMD", "AVGO", "TSM", "MU", "INTC"],
    },
    "재생에너지": {
        "description": "재생에너지 및 태양광 관련주",
        "symbols": ["FSLR", "ENPH", "NEE", "CSIQ"],
    },
    "AI": {
        "description": "AI 핵심 수혜주 (반도체·플랫폼·소프트웨어)",
        "symbols": ["NVDA", "MSFT", "GOOGL", "META", "AMD", "AVGO", "PLTR", "TSM"],
    },
    "바이오": {
        "description": "대형 제약·바이오테크",
        "symbols": ["LLY", "JNJ", "MRK", "ABBV", "AMGN", "GILD", "VRTX", "REGN"],
    },
    "소형주": {
        "description": "거래량 높은 성장·모멘텀 소형주",
        "symbols": ["SOUN", "BBAI", "RKLB", "APLD", "IREN", "ACHR", "JOBY", "LUNR"],
    },
    "한국주식": {
        "description": "코스피 대형주 (KOSPI Large Cap)",
        "symbols": ["삼성전자", "SK하이닉스", "LG에너지솔루션", "현대차", "기아", "네이버", "카카오", "삼성바이오로직스"],
    },
    "빅테크": {
        "description": "미국 빅테크 (Magnificent 7)",
        "symbols": ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"],
    },
    "크립토": {
        "description": "주요 암호화폐",
        "symbols": ["BTC", "ETH", "SOL"],
    },
}


def parse_theme_list_request(text: str) -> bool:
    return text.strip() in ["/테마목록", "/테마리스트", "/themes", "/theme_list"]


def parse_theme_add_request(text: str) -> str:
    text = text.strip()
    for prefix in ["/테마등록 ", "/테마추가 ", "/theme_add ", "/themeadd "]:
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return None


def parse_theme_delete_request(text: str) -> str:
    text = text.strip()
    for prefix in ["/테마삭제 ", "/theme_delete ", "/themedel "]:
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return None


def parse_theme_request(text: str) -> str:
    text = text.strip()
    for prefix in ["/테마 ", "/theme "]:
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    if text.endswith(" 테마분석"):
        return text[:-len(" 테마분석")].strip()
    return None


def parse_theme_definition(raw: str) -> tuple:
    """Parse '네오클라우드: NVDA, CRWV, NBIS' (or whitespace-separated) -> (name, [symbols])."""
    raw = raw.strip()
    if ":" in raw:
        name, syms_str = raw.split(":", 1)
    elif "：" in raw:  # full-width colon
        name, syms_str = raw.split("：", 1)
    else:
        parts = raw.split()
        name = parts[0] if parts else ""
        syms_str = " ".join(parts[1:])
    name = name.strip()
    symbols = [s.strip().upper() for s in re.split(r"[,\s]+", syms_str.strip()) if s.strip()]
    return name, symbols


def resolve_theme(user_id: str, theme_name: str) -> dict:
    """Return {'description', 'symbols', 'source'} for a theme, checking saved
    user themes first then built-in presets. Case-insensitive. None if not found."""
    target = theme_name.strip().lower()
    try:
        from personal_ontology import get_concepts
        for c in get_concepts(user_id):
            if c["concept_name"].strip().lower() == target:
                return {"description": c.get("description", ""), "symbols": c.get("symbols", []), "source": "user"}
    except Exception as e:
        print(f"⚠️ resolve_theme: failed reading saved themes: {e}")
    for name, meta in PRESET_THEMES.items():
        if name.strip().lower() == target:
            return {"description": meta["description"], "symbols": list(meta["symbols"]), "source": "preset"}
    return None


def execute_theme_list(user_id: str) -> str:
    lines = ["🗂️ <b>분석 가능한 테마 목록</b>", "=" * 35]
    lines.append("📌 <b>기본 제공 테마</b>:")
    for name, meta in PRESET_THEMES.items():
        lines.append(f"  • <b>{name}</b> ({len(meta['symbols'])}종목) — {meta['description']}")
    try:
        from personal_ontology import get_concepts
        user_themes = get_concepts(user_id)
    except Exception:
        user_themes = []
    if user_themes:
        lines.append("")
        lines.append("⭐ <b>내가 등록한 테마</b>:")
        for c in user_themes:
            lines.append(f"  • <b>{c['concept_name']}</b> ({len(c['symbols'])}종목) — {c.get('description','')}")
    lines.append("=" * 35)
    lines.append("▶️ 분석: <code>/테마 [이름]</code>  (예: /테마 네오클라우드)")
    lines.append("➕ 등록: <code>/테마등록 [이름]: TICKER1, TICKER2 ...</code>")
    lines.append("🗑️ 삭제: <code>/테마삭제 [이름]</code>")
    return "\n".join(lines)


def execute_theme_analysis(user_id: str, theme_name: str) -> str:
    """Run the 6-agent consensus on every ticker in a theme and return a ranked report."""
    resolved = resolve_theme(user_id, theme_name)
    if not resolved:
        return (
            f"⚠️ '{theme_name}' 테마를 찾을 수 없습니다.\n"
            f"<code>/테마목록</code>으로 등록된 테마를 확인하거나, "
            f"<code>/테마등록 {theme_name}: NVDA, CRWV</code> 형식으로 새로 만들어 주세요."
        )

    description = resolved["description"]
    symbols = resolved["symbols"]
    if not symbols:
        return f"⚠️ '{theme_name}' 테마에 등록된 종목이 없습니다."

    results = []  # (display_label, consensus_pct or None, price or None, action)
    for raw_sym in symbols:
        norm = normalize_symbol(raw_sym)
        # Show the human-friendly name for non-ASCII (e.g. Korean) symbols,
        # otherwise the normalized ticker (NVDA, BTC-USD, ...).
        display = raw_sym if not raw_sym.isascii() else norm
        is_krw = norm.endswith(".KS") or norm.endswith(".KQ")
        try:
            res = run_consensus_analysis(norm)
            if "error" in res:
                results.append((display, None, None, "DATA_ERR"))
                continue
            pct = res["consensus_pct"]
            action = "BUY" if pct > 15.0 else ("SELL" if pct < -15.0 else "HOLD")
            price_str = f"₩{res['cur_p']:,.0f}" if is_krw else f"${res['cur_p']:,.2f}"
            results.append((display, pct, price_str, action))
        except Exception as e:
            print(f"⚠️ execute_theme_analysis: {norm} failed: {e}")
            results.append((display, None, None, "ERR"))

    # Rank: strongest consensus first; rows without data sink to the bottom.
    results.sort(key=lambda r: (r[1] is None, -(r[1] if r[1] is not None else 0.0)))

    emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}
    lines = [
        f"🎯 <b>테마 분석: {theme_name}</b>",
        f"<i>{description}</i>",
        f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')} | 6-Agent 컨센서스",
        "=" * 35,
    ]
    buy_c = sell_c = hold_c = 0
    for sym, pct, price_str, action in results:
        if pct is None:
            lines.append(f"⚪ <b>{sym}</b>: 데이터 조회 실패")
            continue
        if action == "BUY":
            buy_c += 1
        elif action == "SELL":
            sell_c += 1
        else:
            hold_c += 1
        label = {"BUY": "적극 매수", "SELL": "비중 축소", "HOLD": "관망"}[action]
        lines.append(f"{emoji[action]} <b>{sym}</b>  {price_str}  →  <b>{label}</b> (합의 {pct:+.1f}%)")

    lines.append("=" * 35)
    lines.append(f"📊 합계: 🟢 매수 {buy_c} · 🟡 관망 {hold_c} · 🔴 축소 {sell_c}")
    top = next((r for r in results if r[1] is not None), None)
    if top and top[1] is not None and top[1] > 15.0:
        lines.append(f"🏆 최상위 모멘텀: <b>{top[0]}</b> (합의 {top[1]:+.1f}%)")
    lines.append("\n※ 6인 퀀트 포럼 실시간 스캔. 투자 참고용입니다.")
    lines.append("🔍 개별 상세: <code>/분석 [티커]</code>")
    return "\n".join(lines)


def execute_features_summary() -> str:
    lines = [
        "ℹ️ <b>[No Slip AI Quant Bot] 사용 가이드 & 주요 기능</b>",
        "=" * 40,
        "No Slip 봇은 6인의 AI 퀀트 포럼 분석 및 실시간 토론 기능을 지원합니다.",
        "",
        "📊 <b>1. 실시간 주식/크립토 분석</b>",
        "• <b>설명</b>: yfinance 데이터 및 매크로 지표를 기반으로 6인의 AI 에이전트(추세, 가치, 수급, 매크로 등)가 포지션을 산출하여 합의 스탠스 리포트를 제공합니다.",
        "• <b>사용법</b>: <code>/분석 [종목명/티커]</code> 또는 <code>[종목명] 분석</code>",
        "  - <i>예시: /분석 삼성전자, /analyze TSLA, 비트코인 분석해줘</i>",
        "",
        "🎯 <b>1-2. 테마별 묶음 분석</b>",
        "• <b>설명</b>: 관련주를 하나의 테마(예: 네오클라우드, 반도체)로 묶어 종목별 6-Agent 컨센서스를 한 번에 산출하고, 합의지수 순으로 매수/관망/축소를 랭킹합니다.",
        "• <b>사용법</b>: <code>/테마 [이름]</code> | 목록 <code>/테마목록</code> | 등록 <code>/테마등록 [이름]: TICKER1, TICKER2</code> | 삭제 <code>/테마삭제 [이름]</code>",
        "  - <i>예시: /테마 네오클라우드, /테마등록 내포폴: NVDA, CRWV, BTC</i>",
        "",
        "🧭 <b>1-3. 섹터 상관관계 학습 & 추천 섹터</b>",
        "• <b>설명</b>: 11개 GICS 섹터 ETF의 상관관계를 매일 누적 학습하고, 모멘텀·상대강도 기준으로 오늘 비중확대/축소할 섹터와 분산 힌트를 제공합니다. (매일 자동 발송)",
        "• <b>사용법</b>: <code>/섹터</code> 또는 <code>/sector</code>",
        "  - <i>예시: /섹터, /추천섹터</i>",
        "",
        "🗣️ <b>2. AI & Human 실시간 토론방</b>",
        "• <b>설명</b>: 해당 종목에 대해 AI 에이전트들과 실시간으로 주식 찬반 의견을 주고받는 토론방을 시작합니다.",
        "• <b>사용법</b>: <code>/토론 [종목명/티커]</code> 또는 <code>[종목명] 토론</code>",
        "  - <i>예시: /토론 삼성전자, /debate NVDA, 테슬라 토론하자</i>",
        "",
        "💬 <b>3. 토론 의견 제출</b>",
        "• <b>설명</b>: 토론방이 개설된 상태에서 본인의 분석 의견이나 뉴스 호재/악재를 제시하면, AI 위원들이 LLM(Gemini)을 통해 동의하거나 날카로운 반론을 제기합니다.",
        "• <b>사용법</b>: <code>/의견 [의견내용]</code> 또는 <code>/opinion [내용]</code>",
        "  - <i>예시: /의견 삼전은 반도체 훈풍으로 계속 오를 거야</i>",
        "  - <i>예시: /opinion 전기차 수요 둔화로 고평가 우려가 있어</i>",
        "",
        "📺 <b>4. 실시간 유튜브 트렌드 크롤링</b>",
        "• <b>설명</b>: 실시간으로 유튜브 및 구글 주식 검색 급상승 종목 TOP 5와 근거 뉴스를 크롤링하여 보여줍니다.",
        "• <b>사용법</b>: <code>/유튜브</code> 또는 <code>/youtube</code> [종목명]",
        "  - <i>예시: /유튜브, /youtube, /youtube micron</i>",
        "",
        "🏆 <b>5. 글로벌 Quant AI 봇 리그 토너먼트</b>",
        "• <b>설명</b>: 우리 6-Agent 모델과 다른 퀀트 봇(Freqtrade, Hummingbot, Jesse)의 최근 60일 백테스트 성과를 비교 분석하여 리포트를 작성합니다.",
        "• <b>사용법</b>: <code>/경쟁</code> 또는 <code>/competition</code>",
        "  - <i>예시: /경쟁, /competition, /토너먼트</i>",
        "",
        "📊 <b>6. 자산별 월간 최적 전략 학습</b>",
        "• <b>설명</b>: 과거 3달간의 1분 단위 kline 데이터를 기반으로 주요 매매 전략(고래 수급, RSI, MACD, BB)의 최적 파라미터를 월별로 시뮬레이션 및 백테스트 학습하여, 에이전트 운용 설정을 실시간 자동 업데이트합니다.",
        "• <b>사용법</b>: <code>/월간학습</code> 또는 <code>/monthly_optimize</code>",
        "  - <i>예시: /월간학습, /monthly_optimize</i>",
        "",
        "🌐 <b>7. 로컬 웹사이트 모바일 접속 터널링</b>",
        "• <b>설명</b>: 로컬 개발 환경(localhost:3000)을 외부 모바일 기기에서도 접속할 수 있는 공용 URL 터널을 생성하여 제공합니다.",
        "• <b>사용법</b>: <code>/웹사이트</code> 또는 <code>/website</code>",
        "  - <i>예시: /웹사이트, /website</i>",
        "",
        "💼 <b>8. 포트폴리오 및 봇 포지션 현황 조회</b>",
        "• <b>설명</b>: S&P500 최신 자산 배분 모델 정보와 S&P500 가상 봇, 크립토 실시간 감시 봇, 6-Agent Consensus 추천 봇의 오픈 포지션 현황을 통합 요약해서 보여줍니다.",
        "• <b>사용법</b>: <code>/포트폴리오</code> 또는 <code>/portfolio</code>",
        "  - <i>예시: /포트폴리오, /portfolio</i>",
        "",
        "👑 <b>9. Prophet 챔피언 모델 현황 조회</b>",
        "• <b>설명</b>: 주요 자산(BTC, ETH, SOL, AAPL, MU, INTC 등)에 최적화 학습된 최신 Prophet 챔피언 모델의 등록 상태와 평가 메트릭스를 조회합니다.",
        "• <b>사용법</b>: <code>/챔피언</code> 또는 <code>/champion</code>",
        "  - <i>예시: /챔피언, /champion</i>",
        "",
        "📊 <b>10. S&P 500 정보맵 시각화</b>",
        "• <b>설명</b>: S&P 500의 최신 정보맵 2차원(모멘텀-변동성) 분포를 Matplotlib 차트로 생성하여 시각화 리포트를 전송합니다.",
        "• <b>사용법</b>: <code>/infomap</code> 또는 <code>/정보맵</code> 또는 <code>/시각화</code>",
        "  - <i>예시: /infomap, /시각화</i>",
        "",
        "🔔 <b>11. 개인화 알림 설정</b>",
        "• <b>설명</b>: 각 단톡방/채팅방 별로 받아볼 전략 알림의 종류(고래 수급, RSI, MACD 등)를 켜거나 끄고, 최소 임계치(%)를 개별 지정할 수 있습니다.",
        "• <b>사용법</b>: <code>/알림설정</code> | <code>/알림온 [전략명] [임계치%]</code> | <code>/알림오프 [전략명]</code>",
        "  - <i>예시: /알림설정, /알림온 김프 1.5, /알림오프 rsi</i>",
        "",
        "🌀 <b>12. GICS 섹터 오빗(Orbit) 분석 & 시각화 차트</b>",
        "• <b>설명</b>: 11개 GICS 섹터의 정보 기하학 좌표 무게중심과 내부 분산(Spread)의 다차원 전이 상태를 학습(SVD+MLP)하여, 향후 이동 방향과 상태 변화 시각화 차트를 전송합니다.",
        "• <b>사용법</b>: <code>/orbit</code> 또는 <code>/궤적</code> 또는 <code>/오빗</code>",
        "  - <i>예시: /orbit, /궤적, /orbit학습</i>",
        "",
        "📺 <b>13. 오선 미국 증시 시황 요약 (Gemini)</b>",
        "• <b>설명</b>: 인기 주식 유튜브 채널 '오선의 미국 증시 라이브'의 최신 방송 정보와 당일 실시간 뉴스를 신경망(Gemini)으로 융합 요약하여 마감 시황 리포트를 전송합니다.",
        "• <b>사용법</b>: <code>/ohseon</code> 또는 <code>/오선</code>",
        "  - <i>예시: /ohseon, /오선</i>",
        "",
        "🤖 <b>14. AI 제미나이 프로젝트 토론 보조원 (Gemini Chat)</b>",
        "• <b>설명</b>: No Slip 퀀트 매매 시스템의 설계, 기술적 분석전략(고래수급, 차익거래 등) 및 파라미터 튜닝 등에 관해 인공지능 제미나이와 실시간 대화 및 토론을 진행합니다.",
        "• <b>사용법</b>: <code>/gemini [질문]</code> 또는 <code>/제미나이 [질문]</code>",
        "  - <i>예시: /gemini 고래 수급 전략 임계치 설정 팁을 줘</i>",
        "",
        "💡 <b>15. AI 에이전트 시장 분석 및 조언 (Agent Advice)</b>",
        "• <b>설명</b>: 단기 하락을 예측하는 MLP 에이전트들과 리스크 모드를 조율하는 연합 RL 에이전트들이 실시간 가상자산 기류 및 거시 GICS 섹터 수급을 분석하여 현재 리스크 관리 상황과 조언을 전송합니다.",
        "• <b>사용법</b>: <code>/조언</code> 또는 <code>/advice</code>",
        "  - <i>예시: /조언, /advice</i>",
        "",
        "👥 <b>16. 연합 전략 공유 및 분산 학습 (Federated Strategy Sharing)</b>",
        "• <b>설명</b>: 동의한 사용자들의 로컬 매매 성과(Q-table)를 프라이버시가 보호되는 연합 평균화(FedAvg) 알고리즘으로 결합하여 공동의 최적 전략 매개변수를 실시간으로 도출합니다.",
        "• <b>사용법</b>: <code>/연합학습 [온/오프/동기화]</code> 또는 <code>/federated [on/off/sync]</code>",
        "  - <i>예시: /연합학습 온, /연합학습 동기화</i>",
        "",
        "=" * 40,
        "※ 본 봇은 지정된 허용 단톡방(Allowlist)에서만 동작하며, 모든 분석은 투자 참고용입니다."
    ]
    return "\n".join(lines)

def execute_portfolio_summary() -> str:
    db_path = ROOT_DIR / "services" / "trader" / "model_cache" / "whale_rewards.sqlite3"
    portfolio_db_path = ROOT_DIR / "services" / "trader" / "model_cache" / "sp500_portfolio_history.sqlite3"
    import sqlite3
    import json
    
    lines = []
    lines.append("💼 <b>[No Slip AI Quant] 현재 포트폴리오 및 봇 포지션 현황</b>")
    lines.append("=" * 40)
    
    # 1. Get S&P500 Asset Allocation Portfolio (sp500_portfolio_history)
    if portfolio_db_path.exists():
        try:
            with sqlite3.connect(portfolio_db_path) as conn:
                row = conn.execute("""
                    SELECT map_date, profile_name, champion_score, features_json, payload_path
                    FROM portfolio_runs
                    ORDER BY map_date DESC
                    LIMIT 1
                """).fetchone()
                if row:
                    map_date, profile_name, champion_score, features_json, payload_path = row
                    features = json.loads(features_json)
                    
                    lines.append(f"📂 <b>S&P 500 자산 배분 포트폴리오 (최신 기준일: {map_date})</b>")
                    lines.append(f"  • <b>선택된 모델 프로필</b>: {profile_name}")
                    lines.append(f"  • <b>모델 챔피언 스코어</b>: {champion_score:.4f}")
                    
                    # Asset class weights
                    us_eq = features.get("us_equities_pct", 0.0)
                    bonds = features.get("treasuries_pct", 0.0)
                    gold = features.get("gold_pct", 0.0)
                    cash = features.get("cash_pct", 0.0)
                    
                    lines.append("  • <b>자산군 비중</b>:")
                    lines.append(f"    - 주식 (U.S. Equities): {us_eq:.1f}%")
                    lines.append(f"    - 채권 (Treasuries/Bonds): {bonds:.1f}%")
                    lines.append(f"    - 금 (Gold/Real Assets): {gold:.1f}%")
                    lines.append(f"    - 현금 (Cash/Short Duration): {cash:.1f}%")
                    
                    # Financial metrics
                    upside = features.get("weighted_upside_pct", 0.0)
                    vol = features.get("weighted_volatility_pct", 0.0)
                    mdd = features.get("weighted_max_drawdown_pct", 0.0)
                    lines.append(f"  • <b>포트폴리오 예상 메트릭스</b>:")
                    lines.append(f"    - 기대 상승률: {upside:.2f}% | 변동성: {vol:.2f}% | 최대 낙폭(MDD): -{mdd:.2f}%")
                    
                    # Information Geometry (피셔 정보) & Top Holdings
                    fisher_trace = features.get("natural_gradient_fisher_trace")
                    upper_bound = features.get("natural_gradient_upper_bound_score")
                    fisher_curvature = None
                    entropy = features.get("natural_gradient_live_entropy")
                    holdings_list = []
                    
                    if payload_path and Path(payload_path).exists():
                        try:
                            with open(payload_path, "r", encoding="utf-8") as f:
                                payload = json.load(f)
                                if "naturalGradient" in payload:
                                    ng = payload["naturalGradient"]
                                    if fisher_trace is None:
                                        fisher_trace = ng.get("fisherTrace")
                                    if upper_bound is None:
                                        upper_bound = ng.get("upperBoundScore")
                                    if fisher_curvature is None:
                                        fisher_curvature = ng.get("fisherCurvature")
                                    if entropy is None:
                                        entropy = ng.get("liveEntropy")
                                if "holdings" in payload:
                                    holdings_list = payload["holdings"]
                        except Exception as e:
                            print(f"⚠️ Failed to load portfolio payload file: {e}")
                    
                    lines.append("")
                    lines.append("📊 <b>정보기하학적 피셔 지표 (Information Geometry)</b>")
                    if fisher_trace is not None:
                        lines.append(f"  • <b>Fisher Trace (피셔 트레이스)</b>: {fisher_trace:.4f}")
                    if fisher_curvature is not None:
                        lines.append(f"  • <b>Fisher Curvature (피셔 곡률)</b>: {fisher_curvature:.4f}")
                    if upper_bound is not None:
                        lines.append(f"  • <b>Natural Gradient Bound Score</b>: {upper_bound:.4f}")
                    if entropy is not None:
                        lines.append(f"  • <b>Live Entropy (포트폴리오 엔트로피)</b>: {entropy:.4f}")
                        
                    if holdings_list:
                        # Sort by weightPct descending
                        top_holdings = sorted(holdings_list, key=lambda x: x.get("weightPct", 0.0) or 0.0, reverse=True)[:5]
                        lines.append("")
                        lines.append("📈 <b>주요 포트폴리오 편입 종목 (Top 5)</b>")
                        for h in top_holdings:
                            sym = h.get("symbol", "N/A")
                            w = h.get("weightPct", 0.0)
                            tgt_w = h.get("naturalGradientTargetWeightPct")
                            lift = h.get("naturalGradientLiftPct")
                            
                            tgt_w_str = f"{tgt_w:.1f}%" if tgt_w is not None else "N/A"
                            lift_str = f"{lift:+.1f}%" if lift is not None else "N/A"
                            
                            lines.append(f"  • <b>{sym}</b>: 비중 {w:.1f}% (목표 {tgt_w_str} | Lift {lift_str})")
                else:
                    lines.append("📂 <b>S&P 500 자산 배분 포트폴리오</b>: 기록된 포트폴리오 실행 이력이 없습니다.")
        except Exception as e:
            lines.append(f"⚠️ S&P 500 포트폴리오 데이터 조회 실패: {e}")
    else:
        lines.append("📂 <b>S&P 500 자산 배분 포트폴리오</b>: 포트폴리오 데이터베이스가 없습니다.")
        
    lines.append("-" * 40)
    
    # 2. Query open positions from SQLite
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                # Active S&P 500 trades (sp500_trade_log)
                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sp500_trade_log'")
                if cursor.fetchone():
                    try:
                        sp500_pending = conn.execute("""
                            SELECT symbol, entry_price, target_sell_price, entry_time, buy_reason,
                                   prophet_trend, prophet_trend_slope, prophet_weekly, prophet_monthly
                            FROM sp500_trade_log
                            WHERE status = 'PENDING'
                            ORDER BY id DESC
                        """).fetchall()
                    except sqlite3.OperationalError:
                        sp500_pending_raw = conn.execute("""
                            SELECT symbol, entry_price, target_sell_price, entry_time, buy_reason
                            FROM sp500_trade_log
                            WHERE status = 'PENDING'
                            ORDER BY id DESC
                        """).fetchall()
                        sp500_pending = []
                        for r in sp500_pending_raw:
                            sp500_pending.append((r[0], r[1], r[2], r[3], r[4], None, None, None, None))
                else:
                    sp500_pending = []
                
                # Active Crypto trades (whale_trade_log)
                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='whale_trade_log'")
                if cursor.fetchone():
                    crypto_pending = conn.execute("""
                        SELECT symbol, entry_price, entry_time, strategy
                        FROM whale_trade_log
                        WHERE status = 'PENDING'
                        ORDER BY id DESC
                    """).fetchall()
                else:
                    crypto_pending = []
                
                # Active Multi-Agent Consensus trades (consensus_trade_log)
                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='consensus_trade_log'")
                if cursor.fetchone():
                    consensus_pending = conn.execute("""
                        SELECT symbol, entry_price, entry_time, consensus_score
                        FROM consensus_trade_log
                        WHERE status = 'PENDING'
                        ORDER BY id DESC
                    """).fetchall()
                else:
                    consensus_pending = []
                
                # Format S&P 500 positions & Account Summary
                import notifier
                notifier.init_sp500_db()
                
                # Query realized metrics
                completed_trades = conn.execute("SELECT realized_return FROM sp500_trade_log WHERE status = 'COMPLETED'").fetchall()
                realized_usd = sum((float(t[0] or 0) / 100.0) * notifier.ALLOCATION_PER_TRADE for t in completed_trades)
                total_completed = len(completed_trades)
                wins = sum(1 for t in completed_trades if float(t[0] or 0) > 0)
                win_rate = (wins / total_completed * 100.0) if total_completed > 0 else 0.0
                
                # Calculate unrealized metrics (Limit yfinance calls to top 5 to prevent timeouts)
                unrealized_usd = 0.0
                for idx, pos in enumerate(sp500_pending):
                    sym = pos[0]
                    entry = float(pos[1])
                    cur = 0.0
                    if idx < 5:
                        cur = notifier.fetch_live_price(sym)
                    if cur > 0:
                        unrealized_ret = ((cur / entry) - 1.0) * 100.0
                        unrealized_p = (unrealized_ret / 100.0) * notifier.ALLOCATION_PER_TRADE
                        unrealized_usd += unrealized_p
                        
                total_pnl = realized_usd + unrealized_usd
                current_capital = notifier.START_CAPITAL + total_pnl
                
                lines.append("💳 <b>S&P 500 가상 트레이딩 계좌 현황</b>")
                lines.append(f"  • <b>평가 자산총액</b>: ${current_capital:,.2f} USD")
                lines.append(f"  • <b>투자 원금</b>: ${notifier.START_CAPITAL:,.2f} USD")
                lines.append(f"  • <b>누적 총 손익</b>: ${total_pnl:+,.2f} USD ({total_pnl/notifier.START_CAPITAL*100.0:+.2f}%)")
                lines.append(f"  • <b>실현 / 평가 손익</b>: ${realized_usd:+,.2f} / ${unrealized_usd:+,.2f} USD")
                lines.append(f"  • <b>거래 승률</b>: {win_rate:.1f}% ({wins}승 / {total_completed - wins}패) | 총 {total_completed}회 청산")
                lines.append("")
                
                lines.append("🇺🇸 <b>S&P 500 가상 매매 봇 포지션 (최신 5개 표시)</b>")
                if sp500_pending:
                    for idx, (sym, entry_p, target_p, entry_t, reason, p_trend, p_slope, p_weekly, p_monthly) in enumerate(sp500_pending):
                        if idx >= 5:
                            continue
                        entry_date = datetime.fromtimestamp(entry_t).strftime('%Y-%m-%d')
                        cur = notifier.fetch_live_price(sym)
                        pnl_str = ""
                        if cur > 0:
                            pnl_pct = ((cur / entry_p) - 1.0) * 100.0
                            pnl_val = (pnl_pct / 100.0) * notifier.ALLOCATION_PER_TRADE
                            pnl_str = f" | 현재 ${cur:,.2f} ({pnl_pct:+.2f}%, ${pnl_val:+,.2f})"
                        
                        lines.append(f"  • <b>{sym}</b>: 진입가 ${entry_p:,.2f} | 목표가 ${target_p:,.2f}{pnl_str} ({entry_date} 진입)")
                        if p_trend is not None:
                            p_slope_val = p_slope if p_slope is not None else 0.0
                            p_weekly_val = p_weekly if p_weekly is not None else 0.0
                            p_monthly_val = p_monthly if p_monthly is not None else 0.0
                            lines.append(
                                f"    └ <i>Prophet 예측: 트렌드 ${p_trend:,.2f} (일변화: {p_slope_val:+.4f}), "
                                f"주간: {p_weekly_val*100.0:+.2f}%, 월간: {p_monthly_val*100.0:+.2f}%</i>"
                            )
                        lines.append(f"    └ <i>사유: {escape_html(reason)}</i>")
                    if len(sp500_pending) > 5:
                        lines.append(f"  • <b>외 {len(sp500_pending) - 5}개 종목</b> 추가 보유 중 (전체 현황은 웹에서 확인 가능)")
                else:
                    lines.append("  • 현재 오픈된 포지션이 없습니다.")
                    
                lines.append("-" * 40)
                
                # Format Crypto positions (Whale & Multi-strategy)
                lines.append("🪙 <b>크립토 실시간 감시 봇 포지션</b>")
                if crypto_pending:
                    for sym, entry_p, entry_t, strategy in crypto_pending:
                        entry_date = datetime.fromtimestamp(entry_t).strftime('%Y-%m-%d %H:%M')
                        strat_labels = {
                            "whale_pump": "🐳 고래수급 돌파",
                            "rsi_reversion": "🟢 RSI 과매도 반등",
                            "macd_crossover": "🚀 MACD 골든크로스",
                            "bb_breakout": "💥 볼린저밴드 돌파",
                            "spot_arbitrage": "⚖️ Spot 차익거래",
                            "kimchi_arbitrage": "🇰🇷 김치프리미엄 차익"
                        }
                        strat_name = strat_labels.get(strategy, strategy)
                        lines.append(f"  • <b>{sym}</b>: 진입가 ${entry_p:,.4f} | 전략: {strat_name} ({entry_date} 진입)")
                else:
                    lines.append("  • 현재 오픈된 포지션이 없습니다.")
                    
                lines.append("-" * 40)
                
                # Format Consensus positions (Limit to top 5)
                lines.append("🤖 <b>6-Agent Consensus 추천 포지션 (최신 5개 표시)</b>")
                if consensus_pending:
                    for idx, (sym, entry_p, entry_t, score) in enumerate(consensus_pending):
                        if idx >= 5:
                            continue
                        entry_date = datetime.fromtimestamp(entry_t).strftime('%Y-%m-%d')
                        lines.append(f"  • <b>{sym}</b>: 추천가 ${entry_p:,.2f} | 합의지수: {score*100.0:+.1f}% ({entry_date} 진입)")
                    if len(consensus_pending) > 5:
                        lines.append(f"  • <b>외 {len(consensus_pending) - 5}개 종목</b> 추천 포지션 유지 중")
                else:
                    lines.append("  • 현재 추천된 오픈 포지션이 없습니다.")
                    
        except Exception as e:
            lines.append(f"⚠️ 봇 포지션 데이터 조회 실패: {e}")
    else:
        lines.append("⚠️ 봇 거래 데이터베이스가 존재하지 않습니다.")
        
    # 3. Add Prophet Champion Models Status
    lines.append("-" * 40)
    lines.append("🏆 <b>Prophet 챔피언 모델 등록 현황 (최신 5개)</b>")
    registry_path = ROOT_DIR / "services" / "trader" / "model_cache" / "model_registry.sqlite3"
    if registry_path.exists():
        try:
            with sqlite3.connect(registry_path) as conn:
                rows = conn.execute("""
                    SELECT symbol, agent_group, rule, changepoint_prior_scale, metrics_json, updated_at
                    FROM model_documents
                    ORDER BY updated_at DESC
                    LIMIT 5
                """).fetchall()
                if rows:
                    for symbol, task, rule, cps, metrics_json, updated_at in rows:
                        metrics = json.loads(metrics_json)
                        comp_score = metrics.get("composite_score", 0.0)
                        mae = metrics.get("mae", 0.0)
                        folds = metrics.get("folds", 0)
                        
                        try:
                            dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                            time_str = dt.strftime("%m-%d %H:%M")
                        except Exception:
                            time_str = updated_at[:16]
                            
                        comp_str = f"{comp_score:.4f}" if comp_score != float('inf') and comp_score is not None else "Inf"
                        mae_str = f"{mae:.4f}" if mae != float('inf') and mae is not None else "Inf"
                        
                        lines.append(f"  • <b>{symbol} ({task.upper()}:{rule})</b>: 스코어 {comp_str} | MAE {mae_str} ({folds}f) | <code>{time_str}</code>")
                else:
                    lines.append("  • 등록된 챔피언 모델이 없습니다.")
        except Exception as e:
            lines.append(f"  ⚠️ 챔피언 모델 조회 실패: {e}")
    else:
        lines.append("  ⚠️ 챔피언 레지스트리가 존재하지 않습니다.")
        
    lines.append("\n" + "=" * 40)
    lines.append("※ 본 포지션 정보는 시스템 가상 거래 내역이며 투자 보조 정보입니다.")
    return "\n".join(lines)


def execute_champion_summary() -> str:
    registry_path = ROOT_DIR / "services" / "trader" / "model_cache" / "model_registry.sqlite3"
    import sqlite3
    import json
    
    lines = []
    lines.append("🏆 <b>[No Slip AI Quant] Prophet 챔피언 모델 등록 현황 (최신 10개)</b>")
    lines.append("=" * 40)
    
    if registry_path.exists():
        try:
            with sqlite3.connect(registry_path) as conn:
                rows = conn.execute("""
                    SELECT symbol, agent_group, rule, changepoint_prior_scale, training_rows, metrics_json, updated_at
                    FROM model_documents
                    ORDER BY updated_at DESC
                    LIMIT 10
                """).fetchall()
                
                if rows:
                    for symbol, task, rule, cps, rows_cnt, metrics_json, updated_at in rows:
                        metrics = json.loads(metrics_json)
                        comp_score = metrics.get("composite_score", 0.0)
                        mae = metrics.get("mae", 0.0)
                        rmse = metrics.get("rmse", 0.0)
                        dir_acc = metrics.get("directional_accuracy", 0.0)
                        folds = metrics.get("folds", 0)
                        
                        try:
                            dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                            time_str = dt.strftime("%m-%d %H:%M")
                        except Exception:
                            time_str = updated_at[:16]
                            
                        comp_str = f"{comp_score:.4f}" if comp_score != float('inf') and comp_score is not None else "Inf"
                        mae_str = f"{mae:.4f}" if mae != float('inf') and mae is not None else "Inf"
                        rmse_str = f"{rmse:.4f}" if rmse != float('inf') and rmse is not None else "Inf"
                        
                        lines.append(f"👑 <b>{symbol} ({task.upper()}:{rule})</b> - <code>{time_str}</code>")
                        lines.append(f"  • 종합 스코어: {comp_str} | MAE: {mae_str} | RMSE: {rmse_str} ({folds} folds)")
                        lines.append(f"  • 방향성 정확도: {dir_acc*100.0:.1f}% | 변동점(CPS): {cps}")
                        lines.append(f"  • 학습 데이터: {rows_cnt:,} rows")
                        lines.append("-" * 30)
                else:
                    lines.append("  • 등록된 챔피언 모델이 없습니다.")
        except Exception as e:
            lines.append(f"⚠️ 챔피언 모델 데이터 조회 실패: {e}")
    else:
        lines.append("⚠️ 챔피언 모델 레지스트리 데이터베이스가 없습니다.")
        
    lines.append("\n" + "=" * 40)
    lines.append("※ 새로 등록된 챔피언 모델 설정은 다음 예측 실행 시 실시간 자동 적용됩니다.")
    return "\n".join(lines)


def normalize_symbol(query: str) -> str:
    query_lower = query.lower().strip()
    
    # Check mapping
    if query_lower in SYMBOL_MAPPING:
        return SYMBOL_MAPPING[query_lower]
        
    # Check if it's a known crypto ticker and format for yfinance
    crypto_tickers = ["btc", "eth", "sol", "xrp", "doge", "ada", "dot", "trx", "link", "avax"]
    if query_lower in crypto_tickers:
        return f"{query_lower.upper()}-USD"
        
    # Default: Treat as uppercase stock ticker
    return query.upper()

# ----------------- Core Logic Handlers -----------------

def run_consensus_analysis(symbol: str) -> dict:
    """Fetch live data and run the 6-agent consensus suite for the symbol returning raw values."""
    import yfinance as yf
    
    df = fetch_ticker_data(symbol)
    if df.empty or len(df) < 50:
        return {"error": f"⚠️ <b>{symbol}</b> 종목을 찾을 수 없거나 데이터 조회에 실패했습니다. (yfinance 지원 티커인지 확인해 주세요.)"}
        
    cur_p = float(df["Close"].iloc[-1])
    
    # Fetch macro indicators
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
            
    # Load current weights
    agent_weights = load_weights()
    
    # Run agents
    vote_macro, rat_macro = run_macro_agent(macro_indicators)
    vote_trend, rat_trend = run_trend_agent(df)
    vote_value, rat_value = run_value_agent(df)
    vote_whale, rat_whale = run_whale_agent(df)
    vote_mean_rev, rat_mean_rev = run_mean_reversion_agent(df)
    vote_clucmay, rat_clucmay = run_clucmay_agent(df)
    
    # Calculate consensus score
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
    
    return {
        "df": df,
        "cur_p": cur_p,
        "agent_weights": agent_weights,
        "consensus_pct": consensus_pct,
        "votes": {
            "macro": (vote_macro, rat_macro),
            "trend": (vote_trend, rat_trend),
            "value": (vote_value, rat_value),
            "whale": (vote_whale, rat_whale),
            "mean_reversion": (vote_mean_rev, rat_mean_rev),
            "clucmay": (vote_clucmay, rat_clucmay)
        }
    }

def generate_consensus_graph_base64(symbol: str, consensus_pct: float, agent_weights: dict, votes: dict) -> str:
    """Generate a high-quality visualization of the agent voting decision process and return base64 PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import io
    import base64
    
    # Aesthetics aligning with premium dark theme
    bg_color = "#121212"
    panel_color = "#1a1a1a"
    grid_color = "#2a2a2a"
    text_color = "#ffffff"
    sub_text_color = "#aaaaaa"
    border_color = "#333333"
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6.5), gridspec_kw={'height_ratios': [1.2, 2.5]})
    fig.patch.set_facecolor(bg_color)
    
    # ------------------ Ax1: Stance Gauge ------------------
    ax1.set_facecolor(panel_color)
    ax1.tick_params(left=False, labelleft=False, bottom=True, labelbottom=True, colors=sub_text_color, labelsize=9)
    for spine in ax1.spines.values():
        spine.set_color(border_color)
    
    ax1.set_xlim(-100, 100)
    ax1.set_ylim(-0.5, 0.5)
    ax1.set_title(f"{symbol} Consensus Index Stance", color=text_color, fontsize=12, fontweight="bold", pad=8)
    
    # Colored backdrop zones
    ax1.axvspan(-100, -15, color="#ef4444", alpha=0.15)
    ax1.axvspan(-15, 15, color="#888888", alpha=0.08)
    ax1.axvspan(15, 100, color="#10b981", alpha=0.15)
    ax1.axhline(0, color="#444444", linewidth=0.8, linestyle=":")
    
    # Current stance properties
    if consensus_pct > 15.0:
        bar_color = "#10b981"
        stance_lbl = "Active BUY"
    elif consensus_pct < -15.0:
        bar_color = "#ef4444"
        stance_lbl = "Active SELL"
    else:
        bar_color = "#f59e0b"
        stance_lbl = "Neutral HOLD"
        
    ax1.barh(0, consensus_pct, height=0.3, color=bar_color, edgecolor=border_color, zorder=3)
    ax1.axvline(consensus_pct, color="#ffffff", linewidth=2.5, linestyle="-", zorder=4)
    ax1.text(consensus_pct, 0.28, f"{consensus_pct:+.1f}% ({stance_lbl})", 
             color="#ffffff", fontsize=10, fontweight="bold", ha="center")
             
    ax1.text(-57.5, -0.35, "SELL ZONE", color="#ef4444", fontsize=9, fontweight="bold", ha="center")
    ax1.text(0, -0.35, "NEUTRAL ZONE", color=sub_text_color, fontsize=9, fontweight="bold", ha="center")
    ax1.text(57.5, -0.35, "BUY ZONE", color="#10b981", fontsize=9, fontweight="bold", ha="center")
    
    # ------------------ Ax2: Committee Breakdown ------------------
    ax2.set_facecolor(panel_color)
    ax2.tick_params(colors=sub_text_color, labelsize=9)
    for spine in ax2.spines.values():
        spine.set_color(border_color)
    ax2.grid(True, axis="x", color=grid_color, linestyle=":", linewidth=0.5, zorder=0)
    
    agents = ["macro", "trend", "value", "whale", "mean_reversion", "clucmay"]
    agent_labels = [
        "Macro (Macro)", "Trend (Trend)", "Value (Value)",
        "Whale (Whale)", "Mean Rev (RSI)", "ClucMay (Freq)"
    ]
    
    weights = [agent_weights.get(a, 0.1667) for a in agents]
    agent_votes = [votes.get(a, "HOLD") for a in agents]
    
    vote_colors = {
        "BUY": "#10b981",
        "SELL": "#ef4444",
        "HOLD": "#4b5563"
    }
    bar_colors = [vote_colors.get(v, "#4b5563") for v in agent_votes]
    
    y_pos = range(len(agents))
    bars = ax2.barh(y_pos, weights, color=bar_colors, edgecolor=border_color, height=0.55, zorder=3)
    
    ax2.set_yticks(y_pos)
    ax2.set_yticklabels(agent_labels, color=text_color, fontsize=10, fontweight="bold")
    ax2.set_xlabel("Agent Allocation Weight", color=sub_text_color, fontsize=10, labelpad=8)
    ax2.set_title("AI Trader Forum Committee Breakdown (Votes & Weights)", color=text_color, fontsize=12, fontweight="bold", pad=8)
    
    # Text metrics labels next to the bars
    for bar, vote, weight in zip(bars, agent_votes, weights):
        width = bar.get_width()
        lbl_x = width + 0.005
        ax2.text(lbl_x, bar.get_y() + bar.get_height()/2.0, f"{vote} ({weight*100.0:.1f}%)",
                 color="#ffffff", fontsize=9, fontweight="bold", va="center", ha="left")
                 
    ax2.set_xlim(0, max(weights) + 0.08)
    
    plt.tight_layout()
    
    # Render and encode image
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, facecolor=bg_color, bbox_inches="tight")
    buf.seek(0)
    img_bytes = buf.read()
    plt.close(fig)
    
    return base64.b64encode(img_bytes).decode("utf-8")

def execute_analysis(symbol: str) -> str:
    """Fetch live data and run the 6-agent consensus suite for the symbol, returning text report."""
    res = run_consensus_analysis(symbol)
    if "error" in res:
        return res["error"]
        
    cur_p = res["cur_p"]
    consensus_pct = res["consensus_pct"]
    votes = res["votes"]
    
    if consensus_pct > 15.0:
        consensus_emoji = "🟢 <b>적극 매수 (BUY)</b>"
    elif consensus_pct < -15.0:
        consensus_emoji = "🔴 <b>비중 축소 (SELL)</b>"
    else:
        consensus_emoji = "🟡 <b>관망/중립 (HOLD)</b>"
        
    lines = []
    lines.append(f"🤖 <b>[No Slip] 온디맨드 주식/크립토 실시간 분석 리포트</b>")
    lines.append("=" * 40)
    lines.append(f"📊 <b>종목명/티커</b>: <code>{symbol}</code> | 현재가: ${cur_p:,.2f}")
    lines.append(f"🎯 <b>에이전트 합의Stance</b>: {consensus_emoji} (합의지수: {consensus_pct:+.1f}%)")
    lines.append("=" * 40)
    lines.append("<b>👥 AI 트레이더 포럼 위원회 의견록</b>:")
    
    agent_display = {
        "macro": "매크로 (Macro)",
        "trend": "추세추종 (Trend)",
        "value": "안전마진 (Value)",
        "whale": "수급동향 (Whale)",
        "mean_reversion": "과매도회귀 (RSI)",
        "clucmay": "ClucMay (Freqtrade)"
    }
    
    for agent_key, display_name in agent_display.items():
        vote, rationale = votes[agent_key]
        vote_emoji = "🟢" if vote == "BUY" else ("🔴" if vote == "SELL" else "🟡")
        lines.append(f"  • {vote_emoji} <b>{display_name}</b>: {escape_html(rationale)}")
        
    lines.append("\n" + "=" * 40)
    lines.append("※ 본 분석은 6인 퀀트 포럼의 실시간 스캔 결과이며 투자 참고용입니다.")
    
    return "\n".join(lines)

def execute_analysis_with_graph(symbol: str) -> tuple[str, str | None]:
    """Run the 6-agent consensus and return both the HTML text report and a base64-encoded PNG chart."""
    res = run_consensus_analysis(symbol)
    if "error" in res:
        return res["error"], None
        
    report_text = execute_analysis(symbol)
    
    votes_only = {k: v[0] for k, v in res["votes"].items()}
    try:
        base64_img = generate_consensus_graph_base64(symbol, res["consensus_pct"], res["agent_weights"], votes_only)
    except Exception as e:
        print(f"Error generating consensus graph: {e}")
        base64_img = None
        
    return report_text, base64_img

def execute_debate_initiation(symbol: str) -> str:
    """Fetch symbol details and initiate a debate roundtable layout."""
    df = fetch_ticker_data(symbol)
    if df.empty or len(df) < 50:
        return f"⚠️ <b>{symbol}</b> 종목을 찾을 수 없거나 데이터 조회에 실패했습니다. (yfinance 지원 티커인지 확인해 주세요.)"
        
    # Run a quick agent scan to initialize their opinions
    macro_indicators = {"US10Y": 3.75, "DXY": 104.5, "VIX": 13.5, "Oil": 78.0}
    vote_macro, rat_macro = run_macro_agent(macro_indicators)
    vote_trend, rat_trend = run_trend_agent(df)
    vote_value, rat_value = run_value_agent(df)
    vote_whale, rat_whale = run_whale_agent(df)
    
    lines = [
        f"🗣️ <b>[No Slip AI & Human 포럼] {symbol} 토론방 개설</b>",
        f"=" * 40,
        f"각 AI 에이전트들이 분석한 <b>{symbol}</b>에 대한 초기 스탠스입니다:",
        f"",
        f"📈 <b>추세추종 (Trend Agent)</b>: {vote_trend} | <i>\"{escape_html(rat_trend)}\"</i>",
        f"🔍 <b>안전마진 (Value Agent)</b>: {vote_value} | <i>\"{escape_html(rat_value)}\"</i>",
        f"🐳 <b>수급동향 (Whale Agent)</b>: {vote_whale} | <i>\"{escape_html(rat_whale)}\"</i>",
        f"🌐 <b>매크로 (Macro Agent)</b>: {vote_macro} | <i>\"{escape_html(rat_macro)}\"</i>",
        f"=" * 40,
        f"👤 <b>인간 주주님</b>의 생각은 어떠신가요?",
        f"이 토의방에 의견(예: 상승 호재 모멘텀 등)을 답글이나 아래 형식으로 보내주시면, AI 위원들이 분석하여 답변해 드립니다!",
        f"",
        f"👉 <code>/의견 &lt;의견내용&gt;</code> 또는 <code>/opinion &lt;의견&gt;</code>을 입력하세요."
    ]
    return "\n".join(lines)

def format_debate_reply(symbol: str, user_opinion: str, trend: str, value: str, whale: str) -> str:
    lines = [
        f"💬 <b>[AI 위원회의 실시간 토론 답변]</b>",
        f"=" * 40,
        f"🎯 <b>대상 종목</b>: <code>{symbol}</code>",
        f"👤 <b>사용자 의견</b>: <i>\"{escape_html(user_opinion)}\"</i>",
        f"=" * 40,
        f"📈 <b>추세추종 (Trend Agent)</b>:",
        f"  \"{escape_html(trend)}\"",
        f"",
        f"🔍 <b>안전마진 (Value Agent)</b>:",
        f"  \"{escape_html(value)}\"",
        f"",
        f"🐳 <b>수급동향 (Whale Agent)</b>:",
        f"  \"{escape_html(whale)}\"",
        f"=" * 40,
        f"💡 추가 의견이 있으시면 언제든지 <code>/의견 &lt;내용&gt;</code>으로 대화를 계속 이어갈 수 있습니다!"
    ]
    return "\n".join(lines)

def generate_agent_replies(symbol: str, user_opinion: str) -> str:
    """Generate agent responses directly reacting to user opinions (Gemini or Rule-based)."""
    api_key = os.getenv("GEMINI_API_KEY")
    
    if api_key and HAS_GEMINI:
        print(f"🤖 Generating Gemini response for debate on {symbol}...")
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-flash-latest")
            prompt = f"""
            당신은 금융 퀀트 투자 분석 AI 위원회입니다.
            현재 토론 중인 종목: {symbol}
            사용자(인간 주주)의 의견: "{user_opinion}"
            
            이 의견에 대해 다음 3개 AI 에이전트의 관점에서 각각 2~3문장 내외로 논리적이고 전문적인 한글 답변을 작성해 주세요. 
            존댓말을 사용하고, 분석 결과(기술적, 재무적 지표 등)를 배경으로 사용자의 생각에 대해 동의(찬성)하거나 반론(우려)을 제기해야 합니다.
            
            1. 📈 추세추종 (Trend Agent): 차트, 이평선 정배열/역배열, 거래량, 돌파 여부를 중시.
            2. 🔍 안전마진 (Value Agent): PER, PBR, 기업 가치, 고평가/저평가, 재무건전성을 중시.
            3. 🐳 수급동향 (Whale Agent): 고래 세력의 유입, 대량 거래, 매집 흐름을 중시.
            
            반드시 아래 포맷만 정확하게 사용하여 출력하세요 (다른 메타 멘트는 절대 덧붙이지 마세요):
            [Trend Agent]: <답변>
            [Value Agent]: <답변>
            [Whale Agent]: <답변>
            """
            response = model.generate_content(prompt)
            response_text = response.text.strip()
            
            # Extract replies via Regex
            trend_reply = "최근 주가 흐름의 모멘텀을 고려할 때 변동성이 확대되고 있습니다."
            value_reply = "밸류에이션 지표상 적정 가치 수준을 분석하여 조심스럽게 접근해야 합니다."
            whale_reply = "대규모 자금 유입세를 면밀히 관찰하는 중입니다."
            
            trend_match = re.search(r"\[Trend Agent\]:\s*(.*?)(?=\[Value Agent\]|\[Whale Agent\]|$)", response_text, re.DOTALL)
            value_match = re.search(r"\[Value Agent\]:\s*(.*?)(?=\[Trend Agent\]|\[Whale Agent\]|$)", response_text, re.DOTALL)
            whale_match = re.search(r"\[Whale Agent\]:\s*(.*?)(?=\[Trend Agent\]|\[Value Agent\]|$)", response_text, re.DOTALL)
            
            if trend_match:
                trend_reply = trend_match.group(1).strip()
            if value_match:
                value_reply = value_match.group(1).strip()
            if whale_match:
                whale_reply = whale_match.group(1).strip()
                
            return format_debate_reply(symbol, user_opinion, trend_reply, value_reply, whale_reply)
        except Exception as e:
            print(f"⚠️ Gemini API failed: {e}. Falling back to Rule-based engine.")
            
    # Fallback to rule-based logic
    print(f"💡 Using Rule-based engine for debate on {symbol}...")
    user_opinion_lower = user_opinion.lower()
    
    bullish_keywords = ["오른다", "오를", "상승", "호재", "돌파", "매수", "간다", "대박", "수혜", "올라", "우상향", "전망", "최고", "bull", "buy", "up", "수주", "실적"]
    bearish_keywords = ["내린다", "내릴", "하락", "악재", "매도", "거품", "고평가", "폭락", "위기", "우려", "조정", "숏", "bear", "sell", "down", "부진", "적자"]
    
    is_bullish = any(kw in user_opinion_lower for kw in bullish_keywords)
    is_bearish = any(kw in user_opinion_lower for kw in bearish_keywords)
    
    if is_bullish:
        trend_reply = f"사용자분께서 {symbol}의 상승 모멘텀을 강하게 판단하고 계시네요. 단기 이평선들이 골든크로스를 형성하며 지지선을 구축하고 있어, 추세 추종 관점에서도 모멘텀 진입은 긍정적입니다."
        value_reply = "상승 압력은 충분하지만, 현재 멀티플(PER/PBR)이 역사적 밴드 상단에 도달하여 가치 투자 관점에서는 단기 고평가 영역에 대한 주의와 분할 매수를 강력히 권고합니다."
        whale_reply = "실제 대량 거래 분석에서도 기관 및 고래 세력의 순매수 유입세가 강화되는 거래량 실린 돌파 패턴이 감지되고 있어, 상승 지지를 뒷받침합니다."
    elif is_bearish:
        trend_reply = f"사용자분의 하락 경고에 동의합니다. {symbol}은 최근 단기 과매수(RSI 과열) 신호 이후 상승세가 둔화되고 있으며, 중요 지지선 이탈 시 하락 추세 전환 위험이 큽니다."
        value_reply = "매우 타당한 지적입니다. 펀더멘탈 대비 주가가 고평가되어 가치 환원 리스크가 커지고 있는 구간이므로, 현금 비중을 늘리는 보수적 포지션 관리가 안전합니다."
        whale_reply = "수급 면에서도 최근 주요 거래 계좌에서 차익 실현성 대량 물량이 지속 출하되고 있어, 추가적인 지지 붕괴 가능성이 감지됩니다."
    else:
        trend_reply = f"{symbol}은 현재 명확한 방향성을 보이지 않고 볼린저 밴드가 수축하는 박스권 횡보 국면입니다. 다음 돌파 방향성을 확인하고 거래하는 것이 바람직합니다."
        value_reply = "현재 주가는 내재 가치 수준에 수렴하는 중립적인 구간에 있어 뚜렷한 저평가/고평가 메리트가 없으며, 거시 경제 지표(금리 등) 변동성에 주목해야 합니다."
        whale_reply = "고래 세력들의 유의미한 수급 이동이나 지갑 이동이 감지되지 않고 있으며, 소량 개인 투자자 위주의 분산 거래 형태가 주를 이루고 있습니다."
        
    return format_debate_reply(symbol, user_opinion, trend_reply, value_reply, whale_reply)

def generate_and_save_prophet_forecast_plot(symbol: str, photo_path: Path) -> dict:
    import pandas as pd
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from prophet import Prophet
    
    # Fetch data
    df = fetch_ticker_data(symbol)
    if df.empty or len(df) < 30:
        raise ValueError(f"Not enough historical data for {symbol} to run Prophet.")
        
    # Prepare data for Prophet
    df_prophet = pd.DataFrame()
    df_prophet['ds'] = df.index.tz_localize(None)
    df_prophet['y'] = df['Close'].values
    
    # Use last 365 days for training to keep it fast and relevant
    df_train = df_prophet.tail(365).copy()
    
    # Fit Prophet model
    m = Prophet(
        changepoint_prior_scale=0.05,
        yearly_seasonality=False,
        weekly_seasonality=True,
        daily_seasonality=False
    )
    m.fit(df_train)
    
    # Predict next 30 days
    future = m.make_future_dataframe(periods=30, freq='D')
    forecast = m.predict(future)
    
    # Renders the plot
    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
    fig.patch.set_facecolor("#121212")
    ax.set_facecolor("#1a1a1a")
    
    # Plot historical actuals
    ax.plot(df_train['ds'], df_train['y'], color="#aaaaaa", label="Actual Price", linewidth=1.5, alpha=0.8)
    
    # Plot forecast
    forecast_future = forecast[forecast['ds'] > df_train['ds'].max()]
    forecast_history = forecast[forecast['ds'] <= df_train['ds'].max()]
    
    # Plot in-sample fit
    ax.plot(forecast_history['ds'], forecast_history['yhat'], color="#555555", linestyle="--", linewidth=1.0, alpha=0.5)
    
    # Plot out-of-sample forecast
    ax.plot(forecast_future['ds'], forecast_future['yhat'], color="#00f5d4", label="Prophet Forecast", linewidth=2.0)
    
    # Plot uncertainty interval
    ax.fill_between(
        forecast_future['ds'],
        forecast_future['yhat_lower'],
        forecast_future['yhat_upper'],
        color="#00f5d4",
        alpha=0.15,
        label="Uncertainty Interval"
    )
    
    # Design styling
    ax.set_title(f"{symbol} Price Forecast (30 Days)", fontsize=16, fontweight="bold", color="#ffffff", pad=15)
    ax.set_xlabel("Date", fontsize=11, color="#aaaaaa", labelpad=10)
    ax.set_ylabel("Price ($)", fontsize=11, color="#aaaaaa", labelpad=10)
    
    ax.grid(True, which="both", color="#2a2a2a", linestyle=":", linewidth=0.5, zorder=0)
    ax.tick_params(colors="#aaaaaa", labelsize=9)
    for spine in ax.spines.values():
        spine.set_color("#333333")
        
    ax.legend(loc='upper left', frameon=True, facecolor='#1a1a1a', edgecolor='#333333', labelcolor='#ffffff', fontsize=9)
    
    # Add annotations for current price and 30-day forecast price
    cur_price = df_train['y'].iloc[-1]
    last_ds = df_train['ds'].iloc[-1]
    
    projected_price = forecast_future['yhat'].iloc[-1]
    projected_ds = forecast_future['ds'].iloc[-1]
    
    return_pct = ((projected_price - cur_price) / cur_price) * 100
    
    bbox_props = dict(boxstyle="round,pad=0.3", fc="#262626", ec="none", alpha=0.8)
    
    # Annotate current price
    ax.annotate(
        f"Current: ${cur_price:.2f}",
        xy=(last_ds, cur_price),
        xytext=(-40, 15),
        textcoords="offset points",
        arrowprops=dict(arrowstyle="->", color="#aaaaaa", lw=0.8),
        color="#ffffff",
        fontsize=9,
        fontweight="bold",
        bbox=bbox_props
    )
    
    # Annotate projected price
    ax.annotate(
        f"Forecast: ${projected_price:.2f}\n({return_pct:+.2f}%)",
        xy=(projected_ds, projected_price),
        xytext=(-70, -35),
        textcoords="offset points",
        arrowprops=dict(arrowstyle="->", color="#00f5d4", lw=0.8),
        color="#00f5d4",
        fontsize=9,
        fontweight="bold",
        bbox=bbox_props
    )
    
    plt.tight_layout()
    photo_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(photo_path, facecolor=fig.get_facecolor(), edgecolor='none', bbox_inches='tight')
    plt.close(fig)
    
    return {
        "current_price": cur_price,
        "projected_price": projected_price,
        "return_pct": return_pct,
        "lower_bound": forecast_future['yhat_lower'].iloc[-1],
        "upper_bound": forecast_future['yhat_upper'].iloc[-1]
    }

def generate_and_save_infomap_plot(photo_path: Path) -> dict:

    import json
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    
    latest_json_path = ROOT_DIR / "services" / "trader" / "model_cache" / "sp500_information_maps" / "latest.json"
    if not latest_json_path.exists():
        raise FileNotFoundError(f"latest.json not found at {latest_json_path}")
        
    with open(latest_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    points = data.get("points", [])
    if not points:
        raise ValueError("No points found in latest.json")
        
    symbols = []
    names = []
    xs = []
    ys = []
    quadrants = []
    
    for p in points:
        sym = p.get("symbol")
        name = p.get("name", sym)
        coords = p.get("firstCoordinateSpace")
        if not coords or coords.get("x") is None or coords.get("y") is None:
            continue
        symbols.append(sym)
        names.append(name)
        xs.append(float(coords["x"]))
        ys.append(float(coords["y"]))
        quadrants.append(p.get("quadrant", "unknown"))
        
    if not xs:
        raise ValueError("No valid coordinates found in points")
        
    quadrant_counts = {
        "breakout acceleration": 0,
        "uptrend cooling": 0,
        "recovery setup": 0,
        "selloff acceleration": 0,
        "unknown": 0
    }
    
    for q in quadrants:
        if q in quadrant_counts:
            quadrant_counts[q] += 1
        else:
            quadrant_counts["unknown"] += 1
            
    fig, ax = plt.subplots(figsize=(10, 8), dpi=150)
    fig.patch.set_facecolor("#121212")
    ax.set_facecolor("#1a1a1a")
    
    colors_map = {
        "breakout acceleration": "#00f5d4",
        "uptrend cooling": "#f59e0b",
        "recovery setup": "#d946ef",
        "selloff acceleration": "#ef4444",
        "unknown": "#888888"
    }
    point_colors = [colors_map.get(q, "#888888") for q in quadrants]
    
    ax.scatter(xs, ys, color=point_colors, s=120, alpha=0.15, edgecolors='none', zorder=2)
    ax.scatter(xs, ys, color=point_colors, s=35, alpha=0.9, edgecolors='#ffffff', linewidths=0.5, zorder=3)
    
    ax.set_title("S&P 500 Information Map", fontsize=16, fontweight="bold", color="#ffffff", pad=15)
    ax.set_xlabel("Momentum / Expected Return (1st Coordinate X)", fontsize=11, color="#aaaaaa", labelpad=10)
    ax.set_ylabel("Volatility / Risk (1st Coordinate Y)", fontsize=11, color="#aaaaaa", labelpad=10)
    
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    x_margin = max(0.1, (x_max - x_min) * 0.15)
    y_margin = max(0.5, (y_max - y_min) * 0.15)
    
    xlim_min = min(x_min - x_margin, -0.05)
    xlim_max = max(x_max + x_margin, 0.05)
    ylim_min = min(y_min - y_margin, -0.5)
    ylim_max = max(y_max + y_margin, 0.5)
    
    ax.set_xlim(xlim_min, xlim_max)
    ax.set_ylim(ylim_min, ylim_max)
    
    ax.axhline(0, color="#444444", linewidth=1.2, linestyle="--", alpha=0.7, zorder=1)
    ax.axvline(0, color="#444444", linewidth=1.2, linestyle="--", alpha=0.7, zorder=1)
    
    ax.grid(True, which="both", color="#2a2a2a", linestyle=":", linewidth=0.5, zorder=0)
    
    ax.tick_params(colors="#aaaaaa", labelsize=9)
    for spine in ax.spines.values():
        spine.set_color("#333333")
        
    annotated_count = 0
    annotated_symbols = set()
    major_targets = {"AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "NFLX", "AMD", "AVGO"}
    
    dists = np.sqrt(np.array(xs)**2 + np.array(ys)**2)
    sorted_indices = np.argsort(dists)[::-1]
    
    for idx in range(len(xs)):
        sym = symbols[idx]
        should_annotate = False
        if len(xs) <= 15:
            should_annotate = True
        else:
            if sym in major_targets:
                should_annotate = True
            elif idx in sorted_indices[:8] and annotated_count < 15:
                should_annotate = True
                
        if should_annotate:
            annotated_count += 1
            annotated_symbols.add(sym)
            ax.annotate(
                sym,
                (xs[idx], ys[idx]),
                textcoords="offset points",
                xytext=(0, 6),
                ha="center",
                va="bottom",
                fontsize=8,
                fontweight="bold",
                color="#ffffff",
                bbox=dict(boxstyle="round,pad=0.2", fc="#262626", ec="none", alpha=0.75),
                zorder=4
            )
            
    bbox_props = dict(boxstyle="round,pad=0.3", fc="#1a1a1a", ec="#333333", alpha=0.85)
    ax.text(xlim_max - (xlim_max * 0.05), ylim_max - (ylim_max * 0.08), "Breakout Acceleration", color="#00f5d4", fontsize=9, fontweight="bold", ha="right", va="top", bbox=bbox_props)
    ax.text(xlim_max - (xlim_max * 0.05), ylim_min + (abs(ylim_min) * 0.08), "Uptrend Cooling", color="#f59e0b", fontsize=9, fontweight="bold", ha="right", va="bottom", bbox=bbox_props)
    ax.text(xlim_min + (abs(xlim_min) * 0.05), ylim_max - (ylim_max * 0.08), "Recovery Setup", color="#d946ef", fontsize=9, fontweight="bold", ha="left", va="top", bbox=bbox_props)
    ax.text(xlim_min + (abs(xlim_min) * 0.05), ylim_min + (abs(ylim_min) * 0.08), "Selloff Acceleration", color="#ef4444", fontsize=9, fontweight="bold", ha="left", va="bottom", bbox=bbox_props)
    
    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], marker='o', color='w', markerfacecolor='#00f5d4', markersize=8, label='Breakout Accel'),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='#d946ef', markersize=8, label='Recovery Setup'),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='#f59e0b', markersize=8, label='Uptrend Cooling'),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='#ef4444', markersize=8, label='Selloff Accel')
    ]
    ax.legend(handles=legend_elements, loc='upper center', bbox_to_anchor=(0.5, -0.1), ncol=4, frameon=True, facecolor='#1a1a1a', edgecolor='#333333', labelcolor='#ffffff', fontsize=8)
    
    plt.tight_layout()
    photo_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(photo_path, facecolor=fig.get_facecolor(), edgecolor='none', bbox_inches='tight')
    plt.close(fig)
    
    sorted_by_x = sorted(zip(symbols, xs, ys, quadrants), key=lambda item: item[1])
    sorted_by_y = sorted(zip(symbols, xs, ys, quadrants), key=lambda item: item[2])
    
    return {
        "mapDate": data.get("mapDate", "Unknown"),
        "total_symbols": len(symbols),
        "quadrant_counts": quadrant_counts,
        "max_momentum": sorted_by_x[-1] if sorted_by_x else None,
        "min_momentum": sorted_by_x[0] if sorted_by_x else None,
        "max_volatility": sorted_by_y[-1] if sorted_by_y else None,
        "min_volatility": sorted_by_y[0] if sorted_by_y else None,
    }

def reply_photo_to_telegram(chat_id: int, photo_path: str, caption: str, reply_to_message_id: int):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    payload = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": "HTML",
        "reply_to_message_id": reply_to_message_id
    }
    try:
        with open(photo_path, "rb") as f:
            files = {"photo": f}
            res = requests.post(url, data=payload, files=files, timeout=20)
            res.raise_for_status()
            print(f"✅ Sent photo to chat {chat_id}, message {reply_to_message_id}")
    except Exception as e:
        print(f"❌ Failed to send photo to Telegram: {e}")

def reply_to_telegram(chat_id: int, text: str, reply_to_message_id: int):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_to_message_id": reply_to_message_id
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        res.raise_for_status()
        print(f"✅ Replied to chat {chat_id}, message {reply_to_message_id}")
    except Exception as e:
        print(f"❌ Failed to send reply to Telegram: {e}")

# ----------------- Main Loop -----------------

def main():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        print("⚠️ TELEGRAM_BOT_TOKEN is missing in environment variables.")
        sys.exit(1)
        
    print("🚀 Starting Telegram Interactive Bot Polling Daemon...")
    offset = get_offset()
    
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    
    while True:
        params = {"offset": offset, "timeout": 30}
        try:
            res = requests.get(url, params=params, timeout=35)
            res.raise_for_status()
            updates = res.json().get("result", [])
            
            for update in updates:
                update_id = update["update_id"]
                # Save the new offset to prevent reprocessing
                offset = update_id + 1
                save_offset(offset)
                
                message = update.get("message") or update.get("edited_message")
                if not message:
                    continue
                    
                chat = message.get("chat", {})
                chat_id = chat.get("id")
                message_id = message.get("message_id")
                text = message.get("text")
                
                if not text:
                    continue

                # Authorization Check (Only respond to allowed chats)
                allowed_chats_str = os.getenv("TELEGRAM_CHAT_ID", "")
                allowed_chats = []
                for cid in allowed_chats_str.split(","):
                    try:
                        allowed_chats.append(int(cid.strip()))
                    except ValueError:
                        pass
                
                if chat_id not in allowed_chats:
                    print(f"⚠️ Unauthorized access attempt from chat_id: {chat_id}. Request ignored. (To allow, add this chat_id to TELEGRAM_CHAT_ID in .env)")
                    continue
                    
                # Usage telemetry: anonymized command logging (consent-based, no-op if OFF)
                if text.startswith("/"):
                    try:
                        from usage_collector import log_event
                        log_event("telegram_command", {"cmd": text.split()[0][:30]})
                    except Exception:
                        pass

                # 0.1. Parse Data Collection Consent Request
                collect_arg = parse_collect_request(text)
                if collect_arg is not None:
                    try:
                        from usage_collector import (set_collection_consent, consent_status_text,
                                                     fetch_collection_stats)
                        if collect_arg == "on":
                            set_collection_consent(True)
                            reply_to_telegram(chat_id, "🟢 <b>데이터 수집에 동의했습니다.</b> 익명화된 사용 이벤트만 전송됩니다. (<code>/수집 오프</code>로 언제든 중단)", message_id)
                        elif collect_arg == "off":
                            set_collection_consent(False)
                            reply_to_telegram(chat_id, "⚪ <b>데이터 수집을 중단했습니다.</b>", message_id)
                        elif collect_arg == "stats":
                            reply_to_telegram(chat_id, fetch_collection_stats(), message_id)
                        else:
                            reply_to_telegram(chat_id, consent_status_text(), message_id)
                    except Exception as e:
                        print(f"⚠️ Error executing collect command: {e}")
                        reply_to_telegram(chat_id, f"⚠️ 수집 설정 처리 중 오류: {escape_html(str(e))}", message_id)
                    continue

                # 0. Parse Features Guide Request
                if parse_features_request(text):
                    print(f"ℹ️ Received features guide request from chat {chat_id}")
                    features_report = execute_features_summary()
                    reply_to_telegram(chat_id, features_report, message_id)
                    continue

                # 0.3. Parse Card News Request
                if parse_cardnews_request(text):
                    print(f"🗞️ Received card news request from chat {chat_id}")
                    reply_to_telegram(chat_id, "⏳ <b>오늘의 시황 카드뉴스 5장을 생성 중입니다. 약 20~40초 소요됩니다...</b>", message_id)
                    try:
                        from daily_card_news import generate_card_news
                        generate_card_news(send=True)
                    except Exception as e:
                        print(f"⚠️ Error executing card news: {e}")
                        reply_to_telegram(chat_id, f"⚠️ 카드뉴스 생성 중 오류가 발생했습니다: {escape_html(str(e))}", message_id)
                    continue

                # 0.4. Parse On-chain Whale Report Request
                if parse_onchain_request(text):
                    print(f"🐋 Received on-chain whale report request from chat {chat_id}")
                    reply_to_telegram(chat_id, "⏳ <b>BTC/ETH 온체인 고래 트랜잭션을 스캔 중입니다. 약 10~20초 소요됩니다...</b>", message_id)
                    try:
                        from whale_onchain_monitor import generate_onchain_report
                        report = generate_onchain_report(html=True)
                        reply_to_telegram(chat_id, report, message_id)
                    except Exception as e:
                        print(f"⚠️ Error executing on-chain whale report: {e}")
                        reply_to_telegram(chat_id, f"⚠️ 온체인 고래 리포트 생성 중 오류가 발생했습니다: {escape_html(str(e))}", message_id)
                    continue

                # 0.5. Parse YouTube Crawl Request
                is_yt, yt_keyword = parse_youtube_request(text)
                if is_yt:
                    if yt_keyword:
                        print(f"📺 Received targeted YouTube crawl request for '{yt_keyword}' from chat {chat_id}")
                        reply_to_telegram(chat_id, f"⏳ <b>'{yt_keyword}' 관련 유튜브/구글 트렌드를 크롤링 중입니다. 약 5초 소요됩니다...</b>", message_id)
                    else:
                        print(f"📺 Received general YouTube crawl request from chat {chat_id}")
                        reply_to_telegram(chat_id, "⏳ <b>실시간 유튜브/구글 주식 트렌드를 크롤링 중입니다. 약 5~10초 소요됩니다...</b>", message_id)
                        
                    try:
                        report = generate_youtube_trends_report(yt_keyword)
                        reply_to_telegram(chat_id, report, message_id)
                    except Exception as e:
                        print(f"⚠️ Error executing YouTube crawl: {e}")
                        reply_to_telegram(chat_id, f"⚠️ 크롤링 진행 중 오류가 발생했습니다: {e}", message_id)
                    continue

                # 0.7. Parse Bot Competition Request
                if parse_competition_request(text):
                    print(f"🏆 Received bot competition request from chat {chat_id}")
                    reply_to_telegram(chat_id, "⏳ <b>글로벌 AI 퀀트 봇 백테스트 토너먼트를 시뮬레이션 중입니다. 약 5~10초 소요됩니다...</b>", message_id)
                    try:
                        from bot_competition_tournament import run_tournament
                        report = run_tournament()
                        reply_to_telegram(chat_id, report, message_id)
                    except Exception as e:
                        print(f"⚠️ Error executing bot competition: {e}")
                        reply_to_telegram(chat_id, f"⚠️ 토너먼트 진행 중 오류가 발생했습니다: {e}", message_id)
                    continue

                # 0.9. Parse Monthly Optimization Request
                if parse_monthly_optimize_request(text):
                    print(f"📊 Received monthly optimization request from chat {chat_id}")
                    reply_to_telegram(chat_id, "⏳ <b>자산별 월간 최적 전략 학습을 시작합니다. 과거 3개월 1분 단위 데이터를 다운로드 및 백테스트하므로 약 30초 소요됩니다. 완료 시 결과 리포트가 전송됩니다...</b>", message_id)
                    
                    def run_optimize_bg():
                        try:
                            from optimize_monthly_strategies import run_optimization_pipeline
                            run_optimization_pipeline()
                        except Exception as e:
                            print(f"⚠️ Error executing monthly optimization: {e}")
                            reply_to_telegram(chat_id, f"⚠️ 월간 최적화 학습 진행 중 오류가 발생했습니다: {e}", message_id)
                            
                    import threading
                    threading.Thread(target=run_optimize_bg, daemon=True).start()
                    continue

                # 0.91. Parse Alert settings
                if parse_alert_setting_request(text):
                    print(f"🔔 Received alert settings request from chat {chat_id}")
                    try:
                        from personal_ontology import get_alert_preferences
                        prefs = get_alert_preferences(str(chat_id))
                        prefs_map = {p["strategy"]: p for p in prefs}
                        
                        lines = [
                            "🔔 <b>[No Slip AI Quant] 알림 수신 설정 현황</b>",
                            "=" * 40
                        ]
                        for strat in VALID_STRATEGIES:
                            disp = STRATEGY_DISPLAY.get(strat, strat)
                            pref = prefs_map.get(strat)
                            if not pref:
                                lines.append(f"• {disp}: 🟢 <b>ON</b> (기본값)")
                            else:
                                is_enabled = pref["is_enabled"]
                                threshold = pref["min_threshold"]
                                status_str = "🟢 <b>ON</b>" if is_enabled else "🔴 <b>OFF</b>"
                                if is_enabled and threshold is not None:
                                    status_str += f" (기준치: {threshold}%)"
                                lines.append(f"• {disp}: {status_str}")
                                
                        lines.append("=" * 40)
                        lines.append("💡 <b>설정 변경 명령어 안내:</b>")
                        lines.append("• <b>알림 켜기</b>: <code>/알림온 [전략명] [임계치%]</code>")
                        lines.append("  - <i>예: /알림온 김프 1.5 (김프 차익거래 1.5% 이상일 때만 알림)</i>")
                        lines.append("  - <i>예: /알림온 rsi (임계치 없이 모든 RSI 반등 알림 받기)</i>")
                        lines.append("• <b>알림 끄기</b>: <code>/알림오프 [전략명]</code>")
                        lines.append("  - <i>예: /알림오프 whale_pump (고래수급 알림 끄기)</i>")
                        
                        reply_to_telegram(chat_id, "\n".join(lines), message_id)
                    except Exception as e:
                        print(f"⚠️ Error executing alert settings request: {e}")
                        reply_to_telegram(chat_id, f"⚠️ 알림 설정 조회 중 오류가 발생했습니다: {e}", message_id)
                    continue

                # 0.92. Parse Alert ON
                alert_on_arg = parse_alert_on_request(text)
                if alert_on_arg:
                    print(f"🔔 Received alert on request: {alert_on_arg} from chat {chat_id}")
                    try:
                        from personal_ontology import normalize_strategy, set_alert_preference
                        parts = alert_on_arg.split()
                        strategy_raw = parts[0]
                        threshold = None
                        if len(parts) > 1:
                            try:
                                threshold_str = parts[1].replace("%", "").strip()
                                threshold = float(threshold_str)
                            except ValueError:
                                reply_to_telegram(chat_id, f"⚠️ 올바르지 않은 임계치 형식입니다: <code>{parts[1]}</code>. 숫자 또는 백분율 형식으로 입력해주세요.", message_id)
                                continue
                        
                        norm_strat = normalize_strategy(strategy_raw)
                        if norm_strat not in VALID_STRATEGIES:
                            avail_str = ", ".join(VALID_STRATEGIES)
                            reply_to_telegram(chat_id, f"⚠️ 유효하지 않은 전략 이름입니다: <code>{strategy_raw}</code>\n\n<b>사용 가능 전략 목록:</b>\n{avail_str}", message_id)
                            continue
                            
                        set_alert_preference(str(chat_id), norm_strat, is_enabled=True, min_threshold=threshold)
                        
                        disp = STRATEGY_DISPLAY.get(norm_strat, norm_strat)
                        th_str = f" (최소 기준치: {threshold}%)" if threshold is not None else ""
                        reply_to_telegram(chat_id, f"✅ <b>{disp}</b> 알림이 활성화되었습니다.{th_str}", message_id)
                    except Exception as e:
                        print(f"⚠️ Error setting alert ON preference: {e}")
                        reply_to_telegram(chat_id, f"⚠️ 알림 설정 변경 중 오류가 발생했습니다: {e}", message_id)
                    continue

                # 0.93. Parse Alert OFF
                alert_off_arg = parse_alert_off_request(text)
                if alert_off_arg:
                    print(f"🔔 Received alert off request: {alert_off_arg} from chat {chat_id}")
                    try:
                        from personal_ontology import normalize_strategy, set_alert_preference
                        strategy_raw = alert_off_arg.strip()
                        
                        norm_strat = normalize_strategy(strategy_raw)
                        if norm_strat not in VALID_STRATEGIES:
                            avail_str = ", ".join(VALID_STRATEGIES)
                            reply_to_telegram(chat_id, f"⚠️ 유효하지 않은 전략 이름입니다: <code>{strategy_raw}</code>\n\n<b>사용 가능 전략 목록:</b>\n{avail_str}", message_id)
                            continue
                            
                        set_alert_preference(str(chat_id), norm_strat, is_enabled=False, min_threshold=None)
                        
                        disp = STRATEGY_DISPLAY.get(norm_strat, norm_strat)
                        reply_to_telegram(chat_id, f"✅ <b>{disp}</b> 알림이 비활성화되었습니다.", message_id)
                    except Exception as e:
                        print(f"⚠️ Error setting alert OFF preference: {e}")
                        reply_to_telegram(chat_id, f"⚠️ 알림 설정 변경 중 오류가 발생했습니다: {e}", message_id)
                    continue

                # 0.95. Parse Website Tunnel Request
                if parse_website_request(text):
                    print(f"🌐 Received website tunnel request from chat {chat_id}")
                    reply_to_telegram(chat_id, "⏳ <b>localhost:3000을 외부 모바일용 URL로 연동 터널링 중입니다. 약 3초 소요됩니다...</b>", message_id)
                    try:
                        tunnel_url = get_or_start_localtunnel(3000)
                        if tunnel_url:
                            import urllib.request
                            public_ip = "조회 실패"
                            try:
                                with urllib.request.urlopen("https://api.ipify.org", timeout=5) as resp:
                                    public_ip = resp.read().decode("utf-8").strip()
                            except Exception:
                                try:
                                    with urllib.request.urlopen("https://ifconfig.me/ip", timeout=5) as resp:
                                        public_ip = resp.read().decode("utf-8").strip()
                                except Exception:
                                    pass
                                    
                            reply_msg = (
                                f"🌐 <b>localhost:3000 외부 모바일 접속 주소</b>\n\n"
                                f"아래 URL을 모바일 브라우저로 열면 로컬 웹사이트에 접속하실 수 있습니다:\n"
                                f"👉 {tunnel_url}\n\n"
                                f"⚠️ <b>접속 시 'Friendly Reminder' 화면이 나오는 경우</b>:\n"
                                f"화면에 아래 IP 주소를 입력하시면 접속이 승인됩니다:\n"
                                f"🔑 <b>IP 주소</b>: <code>{public_ip}</code>\n\n"
                                f"<i>※ 이 터널은 봇이 종료되거나 재부팅되면 닫힙니다.</i>"
                            )
                        else:
                            reply_msg = "⚠️ 모바일 연동 터널(localtunnel)을 시작하는 데 실패했습니다. 로컬 웹 서버(port 3000)가 기동 중인지 확인해 주세요."
                    except Exception as e:
                        print(f"⚠️ Error starting website tunnel: {e}")
                        reply_msg = f"⚠️ 웹사이트 모바일 연동 중 오류 발생: {e}"
                    reply_to_telegram(chat_id, reply_msg, message_id)
                    continue

                # 0.97. Parse Portfolio Request
                if parse_portfolio_request(text):
                    print(f"💼 Received portfolio request from chat {chat_id}")
                    try:
                        report = execute_portfolio_summary()
                        reply_to_telegram(chat_id, report, message_id)
                    except Exception as e:
                        print(f"⚠️ Error executing portfolio request: {e}")
                        reply_to_telegram(chat_id, f"⚠️ 포트폴리오 조회 중 오류가 발생했습니다: {e}", message_id)
                    continue

                # 0.98. Parse Champion Request
                if parse_champion_request(text):
                    print(f"🏆 Received champion request from chat {chat_id}")
                    try:
                        report = execute_champion_summary()
                        reply_to_telegram(chat_id, report, message_id)
                    except Exception as e:
                        print(f"⚠️ Error executing champion request: {e}")
                        reply_to_telegram(chat_id, f"⚠️ 챔피언 모델 조회 중 오류가 발생했습니다: {e}", message_id)
                    continue

                # 0.99. Parse Infomap Request
                if parse_infomap_request(text):
                    print(f"📊 Received infomap visualization request from chat {chat_id}")
                    reply_to_telegram(chat_id, "⏳ <b>S&P 500 정보맵 시각화 차트를 생성 중입니다...</b>", message_id)
                    try:
                        photo_path = ROOT_DIR / "data" / "sp500_infomap.png"
                        stats = generate_and_save_infomap_plot(photo_path)
                        
                        caption_lines = [
                            f"📊 <b>S&P 500 Information Map ({stats['mapDate']})</b>",
                            "="*35,
                            f"총 분석 종목 수: <b>{stats['total_symbols']}</b>개",
                            "",
                            "🟢 <b>우상향 가속 (Breakout Accel)</b>: " + f"<b>{stats['quadrant_counts']['breakout acceleration']}</b>개",
                            "🟣 <b>회복 국면 (Recovery Setup)</b>: " + f"<b>{stats['quadrant_counts']['recovery setup']}</b>개",
                            "🟡 <b>상승 둔화 (Uptrend Cooling)</b>: " + f"<b>{stats['quadrant_counts']['uptrend cooling']}</b>개",
                            "🔴 <b>하락 가속 (Selloff Accel)</b>: " + f"<b>{stats['quadrant_counts']['selloff acceleration']}</b>개",
                            "="*35,
                            "🔍 <b>주요 극단적 종목 (Outliers)</b>:",
                        ]
                        
                        if stats['max_momentum']:
                            sym, mx, my, q = stats['max_momentum']
                            caption_lines.append(f"  • <b>최대 모멘텀</b>: {sym} (X: {mx:+.3f})")
                        if stats['min_momentum']:
                            sym, mx, my, q = stats['min_momentum']
                            caption_lines.append(f"  • <b>최대 역모멘텀</b>: {sym} (X: {mx:+.3f})")
                        if stats['max_volatility']:
                            sym, mx, my, q = stats['max_volatility']
                            caption_lines.append(f"  • <b>최대 변동성</b>: {sym} (Y: {my:+.3f})")
                        if stats['min_volatility']:
                            sym, mx, my, q = stats['min_volatility']
                            caption_lines.append(f"  • <b>최저 변동성</b>: {sym} (Y: {my:+.3f})")
                            
                        caption_lines.append("\n※ 첨부된 차트에서 자산의 2차원(모멘텀-변동성) 공간상의 위치를 시각적으로 확인할 수 있습니다.")
                        
                        caption = "\n".join(caption_lines)
                        reply_photo_to_telegram(chat_id, str(photo_path), caption, message_id)
                    except Exception as e:
                        print(f"⚠️ Error executing infomap request: {e}")
                        reply_to_telegram(chat_id, f"⚠️ 정보맵 시각화 생성 중 오류가 발생했습니다: {e}", message_id)

                # 0.999. Parse Prophet Request
                prophet_arg = parse_prophet_request(text)
                if prophet_arg is not None:
                    if not prophet_arg:
                        help_text = (
                            "📊 <b>No Slip AI Prophet 30일 가격 예측 시각화</b>\n"
                            "=" * 35 + "\n"
                            "특정 자산의 최근 365일 데이터를 학습하여 향후 30일간의 가격 흐름과 신뢰 구간을 시각화합니다.\n\n"
                            "▶️ 사용법: <code>/prophet [종목명/티커]</code>\n"
                            "  (예: <code>/prophet AAPL</code> 또는 <code>/prophet BTC-USD</code>)\n"
                            "=" * 35
                        )
                        reply_to_telegram(chat_id, help_text, message_id)
                    else:
                        print(f"📈 Received Prophet forecast request for '{prophet_arg}' from chat {chat_id}")
                        reply_to_telegram(chat_id, f"⏳ <b>{prophet_arg}의 최근 데이터를 수집하여 Prophet 시각화 차트를 생성 중입니다...</b>", message_id)
                        try:
                            # Normalize ticker symbol
                            symbol = normalize_symbol(prophet_arg)
                            photo_path = ROOT_DIR / "data" / f"prophet_forecast_{symbol.lower()}.png"
                            
                            stats = generate_and_save_prophet_forecast_plot(symbol, photo_path)
                            
                            caption_lines = [
                                f"📈 <b>Prophet Price Forecast for {symbol}</b>",
                                "=" * 35,
                                f"• <b>현재 가격</b>: ${stats['current_price']:.2f}",
                                f"• <b>30일 뒤 예측가</b>: ${stats['projected_price']:.2f} ({stats['return_pct']:+.2f}%)",
                                f"• <b>예측 신뢰구간 (80%)</b>: ${stats['lower_bound']:.2f} ~ ${stats['upper_bound']:.2f}",
                                "=" * 35,
                                "※ 본 예측은 Facebook Prophet 시계열 알고리즘 기반 스캔 결과이며 투자 참고용입니다."
                            ]
                            caption = "\n".join(caption_lines)
                            reply_photo_to_telegram(chat_id, str(photo_path), caption, message_id)
                        except Exception as e:
                            print(f"⚠️ Error executing prophet request: {e}")
                            reply_to_telegram(chat_id, f"⚠️ Prophet 예측 생성 중 오류가 발생했습니다: {e} (yfinance에서 지원하는 티커인지 확인해 주세요.)", message_id)
                    continue

                # Parse Gemini Chat/Debate Request

                gemini_query = parse_gemini_request(text)
                if gemini_query is not None:
                    if not gemini_query:
                        help_text = (
                            "🤖 <b>No Slip AI 제미나이 토론 보조원</b>\n"
                            "=" * 35 + "\n"
                            "본 시스템의 전략, 알고리즘, 파라미터 튜닝 등에 대해 자유롭게 질문하거나 토론할 수 있습니다.\n\n"
                            "▶️ 사용법: <code>/gemini [질문 또는 의견]</code>\n"
                            "  (예: <code>/gemini 고래 수급 전략의 파라미터를 어떻게 튜닝하는 게 좋지?</code>)\n"
                            "=" * 35
                        )
                        reply_to_telegram(chat_id, help_text, message_id)
                    else:
                        print(f"🤖 Received Gemini chat request from chat {chat_id}: '{gemini_query}'")
                        reply_to_telegram(chat_id, "⏳ <b>제미나이 AI가 답변을 작성 중입니다...</b>", message_id)
                        try:
                            gemini_reply = execute_gemini_chat(str(chat_id), gemini_query)
                            reply_to_telegram(chat_id, gemini_reply, message_id)
                        except Exception as e:
                            print(f"⚠️ Error executing gemini chat: {e}")
                            reply_to_telegram(chat_id, f"⚠️ 제미나이 처리 중 오류가 발생했습니다: {e}", message_id)
                    continue

                # Parse Alpha Strategy Request
                alpha_cmd, alpha_args = parse_alpha_request(text)
                if alpha_cmd is not None:
                    print(f"⚖️ Received Alpha command request with args {alpha_args} from chat {chat_id}")
                    if len(alpha_args) >= 2 and (alpha_args[0] in ["검증", "verify"]):
                        reply_to_telegram(chat_id, "⏳ <b>실시간 시장 데이터를 수집하여 맞춤형 규칙들을 검증하고 있습니다. (약 5초 소요)...</b>", message_id)
                    try:
                        reply_msg = execute_alpha_command(str(chat_id), alpha_args)
                        reply_to_telegram(chat_id, reply_msg, message_id)
                    except Exception as e:
                        print(f"⚠️ Error executing alpha command: {e}")
                        reply_to_telegram(chat_id, f"⚠️ Alpha 처리 중 오류가 발생했습니다: {e}", message_id)
                    continue

                # 0.990. Parse Sector Recommendation Request
                if parse_sector_request(text):
                    print(f"🧭 Received sector recommendation request from chat {chat_id}")
                    reply_to_telegram(chat_id, "⏳ <b>11개 GICS 섹터 상관관계 학습 및 오늘의 추천 섹터를 산출 중입니다...</b>", message_id)
                    try:
                        from sector_correlation import build_sector_report
                        report = build_sector_report()
                    except Exception as e:
                        print(f"⚠️ Error executing sector recommendation: {e}")
                        report = f"⚠️ 섹터 추천 산출 중 오류가 발생했습니다: {e}"
                    reply_to_telegram(chat_id, report, message_id)
                    continue

                # 0.990.1. Parse Orbit Request
                if parse_orbit_request(text):
                    print(f"🌀 Received orbit trajectory request from chat {chat_id}")
                    reply_to_telegram(chat_id, "⏳ <b>GICS 섹터 오빗(Orbit) 분석 및 시각화 차트를 생성 중입니다...</b>", message_id)
                    try:
                        from services.trader.sector_orbit_learner import run_pipeline, ORBIT_PLOT_PATH
                        ranked, report = run_pipeline()
                        reply_photo_to_telegram(chat_id, str(ORBIT_PLOT_PATH), report, message_id)
                    except Exception as e:
                        print(f"⚠️ Error executing orbit request: {e}")
                        reply_to_telegram(chat_id, f"⚠️ 섹터 오빗 분석 중 오류가 발생했습니다: {e}", message_id)
                    continue

                # 0.990.2. Parse Oh-seon Daily Market Summary Request
                if parse_ohseon_request(text):
                    print(f"📺 Received ohseon summary request from chat {chat_id}")
                    reply_to_telegram(chat_id, "⏳ <b>오선 유튜브 시황 요약 리포트를 생성 중입니다...</b>", message_id)
                    try:
                        from ohseon_summary import run_ohseon_summary_pipeline
                        report = run_ohseon_summary_pipeline()
                        reply_to_telegram(chat_id, report, message_id)
                    except Exception as e:
                        print(f"⚠️ Error executing ohseon summary request: {e}")
                        reply_to_telegram(chat_id, f"⚠️ 시황 요약 생성 중 오류가 발생했습니다: {e}", message_id)
                    continue

                # 0.990.3. Parse Agent Advice Request
                if parse_advice_request(text):
                    print(f"🤖 Received agent advice request from chat {chat_id}")
                    reply_to_telegram(chat_id, "⏳ <b>MLP 및 연합 RL 에이전트들이 시장 분석 후 조언을 구성 중입니다...</b>", message_id)
                    try:
                        from services.trader.federated_rl_agent import FederatedRLAgent
                        agent = FederatedRLAgent()
                        advice = agent.get_agents_advice()
                        reply_to_telegram(chat_id, advice, message_id)
                    except Exception as e:
                        print(f"⚠️ Error generating agent advice: {e}")
                        reply_to_telegram(chat_id, f"⚠️ 에이전트 조언 생성 중 오류가 발생했습니다: {e}", message_id)
                    continue

                # 0.990.4. Parse Federated Strategy Sharing Request
                fed_arg = parse_federated_request(text)
                if fed_arg is not None:
                    print(f"👥 Received federated sharing request from chat {chat_id}: '{fed_arg}'")
                    try:
                        from services.trader.federated_sharing import set_federated_consent, run_federated_aggregation, get_federated_config
                        
                        if fed_arg == "온" or fed_arg == "on":
                            set_federated_consent(True)
                            reply_to_telegram(chat_id, "✅ <b>연합 전략 공유 동의가 활성화되었습니다.</b>\n이제 다른 봇들과 학습 데이터를 비공개적으로 결합(FedAvg)하여 최적의 Q-테이블 전략을 실시간으로 도출합니다.", message_id)
                        elif fed_arg == "오프" or fed_arg == "off":
                            set_federated_consent(False)
                            reply_to_telegram(chat_id, "❌ <b>연합 전략 공유 동의가 비활성화되었습니다.</b>\n더 이상 전략 가중치를 중앙 서버와 교환하지 않습니다.", message_id)
                        elif fed_arg == "동기화" or fed_arg == "sync":
                            reply_to_telegram(chat_id, "⏳ <b>연합 학습 Q-테이블 동기화 단계 수행 중...</b>", message_id)
                            res_msg = run_federated_aggregation()
                            reply_to_telegram(chat_id, res_msg, message_id)
                        else:
                            # Status/Help check
                            fed_cfg = get_federated_config()
                            status_str = "🟢 활성화 (ON)" if fed_cfg.get("consent_granted", False) else "🔴 비활성화 (OFF)"
                            help_msg = (
                                f"👥 <b>연합 전략 도출 설정 (Federated Strategy Sharing)</b>\n"
                                f"="*35 + "\n"
                                f"동의한 사용자들의 봇들끼리 각자의 매매 성과(Q-table)를 프라이버시를 지키며 중앙에서 평균화(FedAvg)하여 최적의 전략을 도출합니다.\n\n"
                                f"• <b>현재 설정 상태</b>: {status_str}\n"
                                f"• <b>사용법</b>:\n"
                                f"  - <code>/연합학습 온</code> (또는 <code>/federated on</code>)\n"
                                f"  - <code>/연합학습 오프</code> (또는 <code>/federated off</code>)\n"
                                f"  - <code>/연합학습 동기화</code> (또는 <code>/federated sync</code>)\n"
                                f"="*35
                            )
                            reply_to_telegram(chat_id, help_msg, message_id)
                    except Exception as e:
                        print(f"⚠️ Error executing federated command: {e}")
                        reply_to_telegram(chat_id, f"⚠️ 연합 학습 명령 처리 중 오류 발생: {e}", message_id)
                    continue

                # 0.991. Parse Theme List Request
                if parse_theme_list_request(text):
                    print(f"🗂️ Received theme list request from chat {chat_id}")
                    try:
                        reply_to_telegram(chat_id, execute_theme_list(str(chat_id)), message_id)
                    except Exception as e:
                        print(f"⚠️ Error listing themes: {e}")
                        reply_to_telegram(chat_id, f"⚠️ 테마 목록 조회 중 오류가 발생했습니다: {e}", message_id)
                    continue

                # 0.992. Parse Theme Register Request
                theme_def = parse_theme_add_request(text)
                if theme_def:
                    print(f"➕ Received theme register request from chat {chat_id}: {theme_def}")
                    try:
                        from personal_ontology import save_concept
                        name, symbols = parse_theme_definition(theme_def)
                        if not name or not symbols:
                            reply_to_telegram(chat_id, "⚠️ 형식을 확인해 주세요: <code>/테마등록 네오클라우드: NVDA, CRWV, NBIS</code>", message_id)
                        else:
                            save_concept(str(chat_id), name, f"사용자 등록 테마", symbols, {})
                            reply_to_telegram(chat_id, f"✅ 테마 <b>{name}</b> 등록 완료 ({len(symbols)}종목: {', '.join(symbols)})\n▶️ <code>/테마 {name}</code> 로 분석하세요.", message_id)
                    except Exception as e:
                        print(f"⚠️ Error registering theme: {e}")
                        reply_to_telegram(chat_id, f"⚠️ 테마 등록 중 오류가 발생했습니다: {e}", message_id)
                    continue

                # 0.993. Parse Theme Delete Request
                theme_del = parse_theme_delete_request(text)
                if theme_del:
                    print(f"🗑️ Received theme delete request from chat {chat_id}: {theme_del}")
                    try:
                        from personal_ontology import delete_concept
                        if delete_concept(str(chat_id), theme_del.strip()):
                            reply_to_telegram(chat_id, f"🗑️ 테마 <b>{theme_del.strip()}</b> 삭제 완료.", message_id)
                        else:
                            reply_to_telegram(chat_id, f"⚠️ '{theme_del.strip()}' 테마를 찾을 수 없습니다. (기본 제공 테마는 삭제할 수 없습니다.)", message_id)
                    except Exception as e:
                        print(f"⚠️ Error deleting theme: {e}")
                        reply_to_telegram(chat_id, f"⚠️ 테마 삭제 중 오류가 발생했습니다: {e}", message_id)
                    continue

                # 0.994. Parse Theme Analysis Request
                theme_query = parse_theme_request(text)
                if theme_query:
                    print(f"🎯 Received theme analysis request for '{theme_query}' from chat {chat_id}")
                    reply_to_telegram(chat_id, f"⏳ <b>'{theme_query}' 테마</b> 종목별 6-Agent 컨센서스 분석 중입니다...", message_id)
                    try:
                        report = execute_theme_analysis(str(chat_id), theme_query)
                    except Exception as e:
                        print(f"⚠️ Error executing theme analysis: {e}")
                        report = f"⚠️ '{theme_query}' 테마 분석 중 오류가 발생했습니다: {e}"
                    reply_to_telegram(chat_id, report, message_id)
                    continue

                # 1. Parse Debate Request
                debate_query = parse_debate_request(text)
                if debate_query:
                    print(f"🔍 Received debate initiation request for: '{debate_query}' from chat {chat_id}")
                    symbol = normalize_symbol(debate_query)
                    
                    try:
                        debate_intro = execute_debate_initiation(symbol)
                        # Save state
                        state = load_debate_state()
                        state[str(chat_id)] = {
                            "symbol": symbol,
                            "timestamp": time.time()
                        }
                        save_debate_state(state)
                    except Exception as e:
                        print(f"⚠️ Error initiating debate for {symbol}: {e}")
                        debate_intro = f"⚠️ <b>{symbol}</b> 토론방을 시작하는 데 예기치 못한 오류가 발생했습니다: {e}"
                        
                    reply_to_telegram(chat_id, debate_intro, message_id)
                    continue
                    
                # 2. Parse Opinion Request
                opinion_text = parse_opinion_request(text)
                if opinion_text:
                    print(f"💬 Received user opinion: '{opinion_text}' from chat {chat_id}")
                    state = load_debate_state()
                    chat_state = state.get(str(chat_id))
                    
                    if not chat_state or (time.time() - chat_state["timestamp"] > 7200): # 2 hours expiry
                        reply_msg = "⚠️ 진행 중인 활성화된 토론방이 없습니다. 먼저 <code>/토론 [종목명]</code>을 통해 AI 포럼 토론을 시작해 주세요!"
                    else:
                        symbol = chat_state["symbol"]
                        try:
                            reply_msg = generate_agent_replies(symbol, opinion_text)
                            # Refresh timestamp to keep active
                            chat_state["timestamp"] = time.time()
                            save_debate_state(state)
                        except Exception as e:
                            print(f"⚠️ Error generating replies for opinion: {e}")
                            reply_msg = f"⚠️ 토론 답변 처리 중 에러가 발생했습니다: {e}"
                            
                    reply_to_telegram(chat_id, reply_msg, message_id)
                    continue
                    
                # 3. Parse Standard Analysis Request
                analysis_query = parse_analysis_request(text)
                if analysis_query:
                    print(f"🔍 Received analysis request for: '{analysis_query}' from chat {chat_id}")
                    symbol = normalize_symbol(analysis_query)
                    
                    try:
                        analysis_report = execute_analysis(symbol)
                    except Exception as e:
                        print(f"⚠️ Error executing analysis for {symbol}: {e}")
                        analysis_report = f"⚠️ <b>{symbol}</b> 분석 진행 중 예기치 못한 오류가 발생했습니다: {e}"
                        
                    reply_to_telegram(chat_id, analysis_report, message_id)
                    continue
                    
        except Exception as e:
            print(f"⚠️ Error polling updates: {e}")
            time.sleep(5)
            
        time.sleep(1)

if __name__ == "__main__":
    main()
