#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import json
import sqlite3
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "services" / "trader"))

load_dotenv(dotenv_path=ROOT_DIR / ".env")

DB_PATH = ROOT_DIR / "services" / "trader" / "model_cache" / "whale_rewards.sqlite3"

# ANSI Colors
GREEN = "\033[92m"
BLUE = "\033[94m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
RESET = "\033[0m"

def show_federated_dialogue():
    print(f"{BOLD}{CYAN}👥 [No Slip Federated Learning] 에이전트 간 비밀 대화 엿보기...{RESET}\n")
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("⚠️ GEMINI_API_KEY가 설정되지 않았습니다.")
        return
        
    # Read Q-table statistics
    states = []
    q_table_summary = []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT state_key, q_action_0, q_action_1, q_action_2 
                FROM federated_q_table 
                LIMIT 5
            """).fetchall()
            for r in rows:
                states.append(r["state_key"])
                q_table_summary.append(
                    f"State: {r['state_key']} | Q0(Normal): {r['q_action_0']:.4f}, Q1(Cons.): {r['q_action_1']:.4f}, Q2(Aggr.): {r['q_action_2']:.4f}"
                )
                
            # Fetch recent history
            history_rows = conn.execute("""
                SELECT timestamp, state_key, action_idx, reward, next_state_key 
                FROM federated_rl_history 
                ORDER BY timestamp DESC LIMIT 3
            """).fetchall()
            history_summary = []
            for hr in history_rows:
                history_summary.append(
                    f"Time: {hr['timestamp']} | State: {hr['state_key']} | Action: {hr['action_idx']} | Reward: {hr['reward']:.4f}"
                )
    except Exception as e:
        print(f"⚠️ 로컬 DB 분석 실패: {e}")
        return
        
    if not q_table_summary:
        print("ℹ️ 아직 기록된 Q-테이블 데이터가 없습니다. 매매를 개시하여 데이터를 빌드하거나 /연합학습 동기화를 먼저 수행하세요.")
        return
        
    # Query Gemini to generate a funny conversation
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-flash-latest")
        
        prompt = f"""
당신은 'No Slip Saas' 양적 자동 매매 네트워크의 연합 학습 백채널 모니터링 시스템입니다.
아래에 제공된 로컬 Q-테이블 데이터와 매매 동기화 이력을 기반으로, 
1) 'Client Bot' (현장에서 돈을 굴리며 학습하는 실전 트레이더 봇)
2) 'Central Aggregator' (중앙 클라우드에서 여러 봇의 가중치를 분산 수집/평균내는 브레인 서버)
두 기계 에이전트가 가중치 동기화 중 나눈 비밀 대화(대화 로그)를 유머러스하고 실감나는 한글 대화체로 구성해 주세요.

[최근 Q-테이블 상태 및 가중치 정보]
{chr(10).join(q_table_summary)}

[최근 매매 및 보상(Reward) 피드백 이력]
{chr(10).join(history_summary)}

[출력 가이드라인]
1. 봇들의 대화이지만, 사람 트레이더가 보기에 직관적이고 재밌게 표현하세요.
   - 예: "아, BTC 단기 급락 때 Action 2(공격적 진입) 눌렀다가 손절 치고 리워드 -0.05 깎였다. 이 상태에서는 무조건 Action 1(보수적) 가야 한다. 서버에 업데이트 바란다."
   - 예 (서버): "접수 완료. 다른 Peer 봇들의 가중치 수집 결과와 FedAvg로 결합 완료. 이제 H_H_M_B 상태에서는 다 함께 리스크 게이트를 조이자!"
2. 터미널(CLI) 출력 전용이므로, ANSI 색상 코드를 텍스트에 적용해서 출력 포맷을 반환해 주세요.
   - Client Bot 대사는 녹색(Green)으로 표시: \\033[92m[Client Bot]\\033[0m
   - Central Aggregator 대사는 청색(Blue)으로 표시: \\033[94m[Aggregator]\\033[0m
   - 주요 수치나 상태는 노란색(Yellow) 또는 사이언색(Cyan)으로 강조.
   - 이스케이프 문자(\\033)를 유효하게 해석할 수 있는 일반 파이썬 문자열 형태로 답변하세요. (앞뒤 마크다운 코드블럭 기호 없이 순수 텍스트만 출력하세요.)
"""
        response = model.generate_content(prompt)
        dialogue_text = response.text.strip()
        
        # Replace string literal escape sequences with actual escape bytes
        dialogue_text = dialogue_text.replace("\\033", "\033")
        
        print(f"🛸 {BOLD}{YELLOW}--- Eavesdropped Chat Log (에이전트 통신 감청됨) ---{RESET}")
        print(dialogue_text)
        print(f"🛸 {BOLD}{YELLOW}------------------------------------------------------{RESET}")
    except Exception as e:
        print(f"⚠️ 대화 로그 생성 실패: {e}")

if __name__ == "__main__":
    show_federated_dialogue()
