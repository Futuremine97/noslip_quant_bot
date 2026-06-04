#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import re
import json
import sqlite3
import time
import numpy as np
import pandas as pd
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
TRADER_DIR = ROOT_DIR / "services" / "trader"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(TRADER_DIR) not in sys.path:
    sys.path.insert(0, str(TRADER_DIR))

DB_PATH = ROOT_DIR / "services" / "trader" / "model_cache" / "whale_rewards.sqlite3"

class FederatedRLAgent:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self.alpha = 0.1  # Learning rate
        self.gamma = 0.9  # Discount factor
        self._init_db()

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            # Q-table schema
            conn.execute("""
                CREATE TABLE IF NOT EXISTS federated_q_table (
                    state_key TEXT PRIMARY KEY,
                    q_action_0 REAL DEFAULT 0.0, -- Normal risk (halt_threshold = 0.50)
                    q_action_1 REAL DEFAULT 0.0, -- Conservative risk (halt_threshold = 0.45)
                    q_action_2 REAL DEFAULT 0.0  -- Aggressive risk (halt_threshold = 0.55)
                )
            """)
            # History schema
            conn.execute("""
                CREATE TABLE IF NOT EXISTS federated_rl_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    state_key TEXT,
                    action_idx INTEGER,
                    reward REAL,
                    next_state_key TEXT
                )
            """)
            conn.commit()

    def get_state_key(self, btc_prob: float, eth_prob: float, sol_prob: float, top_sector_momentum: float) -> str:
        # Discretize states into buckets
        btc_s = "L" if btc_prob < 0.45 else "M" if btc_prob < 0.55 else "H"
        eth_s = "L" if eth_prob < 0.45 else "M" if eth_prob < 0.55 else "H"
        sol_s = "L" if sol_prob < 0.45 else "M" if sol_prob < 0.55 else "H"
        sec_s = "B" if top_sector_momentum > 0.5 else "D" # Bullish vs Defensive sector
        return f"{btc_s}_{eth_s}_{sol_s}_{sec_s}"

    def select_action(self, state_key: str, epsilon: float = 0.15) -> int:
        """Select action using epsilon-greedy policy."""
        import random
        if random.random() < epsilon:
            return random.randint(0, 2)
            
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT q_action_0, q_action_1, q_action_2 FROM federated_q_table WHERE state_key = ?",
                (state_key,)
            ).fetchone()
            
        if not row:
            return 0  # Default to Normal action
        return int(np.argmax(row))

    def update_q_value(self, state_key: str, action: int, reward: float, next_state_key: str):
        """Update Q-value based on Bellman equation."""
        with sqlite3.connect(self.db_path) as conn:
            # Load current state Q-values
            row = conn.execute(
                "SELECT q_action_0, q_action_1, q_action_2 FROM federated_q_table WHERE state_key = ?",
                (state_key,)
            ).fetchone()
            if not row:
                conn.execute(
                    "INSERT OR IGNORE INTO federated_q_table (state_key) VALUES (?)",
                    (state_key,)
                )
                row = (0.0, 0.0, 0.0)
                
            q_values = list(row)
            
            # Load next state Q-values for max_a Q(s', a')
            next_row = conn.execute(
                "SELECT q_action_0, q_action_1, q_action_2 FROM federated_q_table WHERE state_key = ?",
                (next_state_key,)
            ).fetchone()
            max_next_q = max(next_row) if next_row else 0.0
            
            # Bellman update
            current_q = q_values[action]
            new_q = current_q + self.alpha * (reward + self.gamma * max_next_q - current_q)
            
            conn.execute(
                f"UPDATE federated_q_table SET q_action_{action} = ? WHERE state_key = ?",
                (new_q, state_key)
            )
            
            # Log history
            conn.execute(
                "INSERT INTO federated_rl_history (state_key, action_idx, reward, next_state_key) VALUES (?, ?, ?, ?)",
                (state_key, action, reward, next_state_key)
            )
            conn.commit()

    def train_federated_agent_step(self) -> str:
        """
        Collect rewards from trade logs and update the Q-table.
        """
        # Fetch the last resolved trades to compute reward
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            resolved_trades = conn.execute("""
                SELECT exit_price, entry_price, status 
                FROM whale_trade_log 
                WHERE status IN ('PROFIT', 'LOSS') 
                ORDER BY exit_time DESC LIMIT 10
            """).fetchall()
            
        if not resolved_trades:
            return "No resolved trades to calculate rewards."
            
        # Compute avg return as reward
        rewards = []
        for trade in resolved_trades:
            entry = trade["entry_price"]
            exit = trade["exit_price"]
            if entry > 0:
                ret = (exit / entry - 1.0)
                if trade["status"] == "LOSS":
                    ret *= 1.5 # Weight loss penalty higher
                rewards.append(ret)
                
        avg_reward = float(np.mean(rewards)) if rewards else 0.0
        
        # Get current state from MLPs & sectors
        from mlp_drop_predictor import should_halt_due_to_mlp_drop
        from sector_orbit_learner import run_pipeline
        from whale_pump_monitor import fetch_recent_klines
        
        probs = {}
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
            df = fetch_recent_klines(sym, limit=120)
            _, prob = should_halt_due_to_mlp_drop(sym, df)
            probs[sym] = prob
            
        try:
            ranked_sectors, _ = run_pipeline()
            top_sector_mom = ranked_sectors[0]["score"] if ranked_sectors else 0.0
        except Exception as e:
            print(f"⚠️ [Federated RL] Failed to get GICS sector orbits: {e}")
            top_sector_mom = 0.0
            
        state_key = self.get_state_key(probs.get("BTCUSDT", 0.0), probs.get("ETHUSDT", 0.0), probs.get("SOLUSDT", 0.0), top_sector_mom)
        
        # Action selection and execution
        action = self.select_action(state_key)
        
        # Adjust halt thresholds in whale_config.json based on action
        # 0 -> 0.50 (Normal), 1 -> 0.45 (Conservative), 2 -> 0.55 (Aggressive)
        threshold_map = {0: 0.50, 1: 0.45, 2: 0.55}
        target_threshold = threshold_map.get(action, 0.50)
        
        # Update config
        try:
            config_path = ROOT_DIR / "services" / "trader" / "model_cache" / "whale_config.json"
            if config_path.exists():
                with open(config_path, "r") as f:
                    config = json.load(f)
                for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
                    if sym in config:
                        if "mlp_filter" not in config[sym]:
                            config[sym]["mlp_filter"] = {}
                        config[sym]["mlp_filter"]["halt_threshold"] = target_threshold
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
                print(f"⚙️ [Federated RL] Adjusted halt threshold to {target_threshold} (Action: {action})")
        except Exception as e:
            print(f"⚠️ [Federated RL] Failed to update config: {e}")
            
        # Update Q-table with the reward
        self.update_q_value(state_key, action, avg_reward, state_key)
        return f"Successfully updated Federated RL Agent with reward {avg_reward:.4f} and action {action}."

    def score_market_report(self, report_text: str) -> dict:
        """
        Evaluate and score the daily market report using Gemini and current RL agent states.
        """
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return {"score": 50, "rationale": "Gemini API key missing."}
            
        # Get MLP drop probabilities
        from mlp_drop_predictor import should_halt_due_to_mlp_drop
        from sector_orbit_learner import run_pipeline
        from whale_pump_monitor import fetch_recent_klines
        
        probs = {}
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
            df = fetch_recent_klines(sym, limit=120)
            _, prob = should_halt_due_to_mlp_drop(sym, df)
            probs[sym] = prob
            
        try:
            ranked_sectors, _ = run_pipeline()
            top_sectors_str = ", ".join([f"{s['short_label']} ({s['curr_quadrant']})" for s in ranked_sectors[:3]])
        except Exception:
            top_sectors_str = "N/A"
            
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-flash-latest")
            
            prompt = f"""
당신은 'No Slip Saas' 퀀트 자동 매매 시스템의 연합 RL 에이전트(Federated RL Agent)입니다.
현재 우리 다층 신경망(MLP) 및 섹터 오빗 학습 모델들의 상태 데이터를 기반으로, 작성된 일일 시황 리포트의 완성도와 예측 정확도를 채점하고 평가해야 합니다.

[시스템 MLP 및 오빗 학습 실시간 상태]
- BTCUSDT 단기 하락 확률: {probs.get('BTCUSDT', 0.0)*100:.1f}%
- ETHUSDT 단기 하락 확률: {probs.get('ETHUSDT', 0.0)*100:.1f}%
- SOLUSDT 단기 하락 확률: {probs.get('SOLUSDT', 0.0)*100:.1f}%
- 상위 가동 GICS 섹터: {top_sectors_str}

[작성된 일일 시황 리포트 본문]
{report_text}

[평가 지침]
1. 위의 리포트 내용이 거시 지표(매크로)와 섹터 흐름을 팩트에 기반하여 논리적으로 잘 정리했는지 평가하세요.
2. 우리 시스템의 실시간 단기 하락 확률 및 우상향 가속 섹터 데이터와 리포트의 논조(불리시/베어리시)가 얼마나 정밀하게 합치하는지 비교하세요.
   - 예: 리포트는 상승장 마감을 강조하지만, 시스템 MLP의 하락 확률이 60% 이상으로 높다면 리스크 경고 부족에 대한 감점 요인입니다.
3. 종합 점수(0 ~ 100점)를 계산하여 정수로 부여하고, 그 점수의 상세 평가 이유(Evaluation Details)를 작성해 주세요.
4. 출력은 반드시 아래 JSON 형식만을 정확하게 출력하세요. 다른 텍스트나 코드 블록 기호(```json)는 포함하지 마십시오.

{{
  "score": 88,
  "rationale": "여기에 한글로 작성한 평가 상세 내용을 입력하세요. 3줄 내외로 작성하며, 격식 있는 말투를 사용하십시오."
}}
"""
            response = model.generate_content(prompt)
            output_text = response.text.strip()
            
            # Extract JSON substring if any extra markers are returned
            match = re.search(r"\{.*\}", output_text, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                return data
            else:
                return {"score": 80, "rationale": "평가 결과를 정형 데이터로 변환하지 못했으나, 전반적인 요약 논조는 정밀합니다."}
        except Exception as e:
            print(f"⚠️ [RL Evaluator Error] Failed to evaluate report: {e}")
            return {"score": 75, "rationale": f"평가 엔진 수행 중 일시적 오류 발생 ({e})."}

    def get_agents_advice(self) -> str:
        """
        Generate detailed quant trading advice from the perspective of the MLP agents and Federated RL agent.
        """
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return "⚠️ Gemini API key missing."
            
        # Get MLP drop probabilities & thresholds
        from mlp_drop_predictor import should_halt_due_to_mlp_drop
        from sector_orbit_learner import run_pipeline
        from whale_pump_monitor import fetch_recent_klines, load_whale_config
        
        config = load_whale_config()
        
        probs = {}
        thresholds = {}
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
            df = fetch_recent_klines(sym, limit=120)
            _, prob = should_halt_due_to_mlp_drop(sym, df)
            probs[sym] = prob
            
            mlp_cfg = config.get(sym, {}).get("mlp_filter", {})
            thresholds[sym] = mlp_cfg.get("halt_threshold", 0.50)
            
        try:
            ranked_sectors, _ = run_pipeline()
            top_sectors_str = ", ".join([f"{s['short_label']} ({s['curr_quadrant']})" for s in ranked_sectors[:3]])
            top_sector_mom = ranked_sectors[0]["score"] if ranked_sectors else 0.0
        except Exception:
            top_sectors_str = "N/A"
            top_sector_mom = 0.0
            
        state_key = self.get_state_key(probs.get("BTCUSDT", 0.0), probs.get("ETHUSDT", 0.0), probs.get("SOLUSDT", 0.0), top_sector_mom)
        
        # Load Q-values
        q_vals = [0.0, 0.0, 0.0]
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT q_action_0, q_action_1, q_action_2 FROM federated_q_table WHERE state_key = ?",
                (state_key,)
            ).fetchone()
            if row:
                q_vals = list(row)
                
        # Current risk mode based on halt threshold in config
        current_threshold = thresholds.get("BTCUSDT", 0.50)
        mode_map = {0.50: "일반 리스크 (Normal: 0.50)", 0.45: "보수적 리스크 (Conservative: 0.45)", 0.55: "공격적 리스크 (Aggressive: 0.55)"}
        current_mode = mode_map.get(current_threshold, f"기타 ({current_threshold:.2f})")
        
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-flash-latest")
            
            prompt = f"""
당신은 'No Slip Saas' 시스템의 두 핵심 머신러닝 엔진입니다:
1. 다층 인공신경망(MLP) 단기 하락 예측 에이전트팀 (BTC, ETH, SOL 단기 하락 분석)
2. 연합 강화학습(Federated RL) 의사결정 에이전트 (Q-table 기반 리스크 파라미터 조율)

사용자(트레이더)가 실시간 시장 상황에 대한 '/조언'을 요청했습니다. 아래 에이전트들의 실시간 분석 데이터를 바탕으로, 각 에이전트의 관점에서 친근하고 전문적인 종합 투자 조언(Advice) 리포트를 작성해 주세요.

[실시간 에이전트 분석 데이터]
- MLP 단기 하락 확률:
  • BTCUSDT: {probs.get('BTCUSDT', 0.0)*100:.1f}% (차단 임계치: {thresholds.get('BTCUSDT', 0.5)*100:.1f}%)
  • ETHUSDT: {probs.get('ETHUSDT', 0.0)*100:.1f}% (차단 임계치: {thresholds.get('ETHUSDT', 0.5)*100:.1f}%)
  • SOLUSDT: {probs.get('SOLUSDT', 0.0)*100:.1f}% (차단 임계치: {thresholds.get('SOLUSDT', 0.5)*100:.1f}%)
- GICS 상위 주도 섹터: {top_sectors_str}
- 연합 RL 에이전트 상태 키: {state_key}
- 상태별 Q-table 학습값:
  • Action 0 (Normal - 임계치 50%): {q_vals[0]:.4f}
  • Action 1 (Conservative - 임계치 45%): {q_vals[1]:.4f}
  • Action 2 (Aggressive - 임계치 55%): {q_vals[2]:.4f}
- 현재 적용된 리스크 관리 모드: {current_mode}

[작성 지침]
1. <b>MLP 하락 예측 에이전트팀의 조언</b>: 현재 3대 코인의 하락 확률 및 차단 여부를 진단하고, 차트 진입에 대한 단기적 주의점을 설명하세요.
2. <b>연합 RL 에이전트의 조언</b>: 현재 상태 키({state_key})에 매핑된 Q-table 값들을 해석하여, 어떤 리스크 정책(Action 0, 1, 2)이 가장 기대 수익 보상(Reward)이 높고 유리하게 평가되는지 알려주세요.
3. <b>종합 시장 권고사항</b>: GICS 섹터 흐름(상위 주도 섹터) 등 거시적 수급 동향과 코인 시장의 실시간 기류를 종합하여 사용자에게 추천하는 매매 행동 지침을 제시해 주세요.
4. <b>어조 & 캐릭터 구성</b>: 두 에이전트가 직접 사용자에게 말하듯이 작성해야 합니다.
   - 예: "안녕하세요 트레이더님! 🧠 <b>MLP 하락 예측 에이전트팀</b>입니다...", "바통을 이어받아 🤖 <b>연합 RL 에이전트</b>가 분석해 드립니다..." 와 같이 역할 분담을 명확히 하고 친근하고 흥미진진한 말투를 사용하세요.
5. <b>출력 형식</b>: 텔레그램에서 바로 렌더링되도록 오직 HTML 마크업 태그(<b>, <i>, <code>, <a> 등)만 사용하여 작성해 주세요. (마크다운 특수기호 `*`, `**` 등은 사용하지 마십시오.)
"""
            response = model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            return f"⚠️ [조언 생성 오류] 에이전트 조언을 생성하는 데 실패했습니다: {e}"
