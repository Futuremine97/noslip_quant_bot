"use client";

import React, { useState, useEffect, useRef } from "react";

interface BotCharacter {
  id: string;
  name: string;
  color: string;
  symbol: string;
  x: number;
  y: number;
  targetX: number;
  targetY: number;
  state: "trading" | "syncing" | "warning" | "idle";
  action: "overclocking" | "working" | "researching" | "syncing" | "socializing" | "resting" | "idle";
  bubbleText?: string;
  bubbleTimer?: number;
  rewardFloating?: string;
}

interface Building {
  name: string;
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  roofColor: string;
  wallColor: string;
  floorColor: string;
  icon: string;
  textColor: string;
}

export default function QuantVillagePage() {
  const [hour, setHour] = useState<number>(9);
  const [minute, setMinute] = useState<number>(0);
  const [timeSpeed, setTimeSpeed] = useState<number>(1);
  const [isPaused, setIsPaused] = useState<boolean>(false);
  const [tick, setTick] = useState<number>(0);

  const [marketStatus, setMarketStatus] = useState<"NORMAL" | "ALERT">("NORMAL");
  const [syncProgress, setSyncProgress] = useState<number>(0);
  const [syncingState, setSyncingState] = useState<"IDLE" | "WALKING" | "SYNCING" | "COMPLETE">("IDLE");
  const [riskMode, setRiskMode] = useState<string>("Normal Mode (0.50)");
  
  // Interactive Building Computer Terminal state
  const [activeTerminal, setActiveTerminal] = useState<string | null>(null);

  const [logs, setLogs] = useState<string[]>([
    "🎮 [System] 퀀토피아 24시간 무중단 가동 모드가 발동되었습니다!",
    "🔥 [System] 에이전트들의 모든 수면/휴식 프로토콜이 파기되고 오버클럭 야간 전산근무가 강제 적용됩니다.",
    "👑 [RL Agent] 코인 시장은 잠들지 않는다. 전원 야간 오더북 대기 모드 돌입.",
    "🛡️ [BTC MLP] CPU 오버클럭 가동. 발열량 15% 상승 경고 무시.",
  ]);

  const logsEndRef = useRef<HTMLDivElement>(null);

  // Buildings definitions (Cozy server-styled cabins)
  const buildings: Record<string, Building> = {
    binance: { 
      id: "binance", name: "Binance Office", x: 60, y: 70, width: 220, height: 160, 
      roofColor: "#eab308", wallColor: "#854d0e", floorColor: "#fef9c3", 
      icon: "📈", textColor: "text-yellow-600" 
    },
    oracle: { 
      id: "oracle", name: "Oracle Pagoda", x: 520, y: 70, width: 220, height: 160, 
      roofColor: "#ec4899", wallColor: "#701a75", floorColor: "#fce7f3", 
      icon: "🔮", textColor: "text-pink-600" 
    },
    townhall: { 
      id: "townhall", name: "Town Council Hall", x: 290, y: 70, width: 220, height: 160, 
      roofColor: "#9333ea", wallColor: "#4c1d95", floorColor: "#f3e8ff", 
      icon: "🏛️", textColor: "text-purple-600" 
    },
    dorm: { 
      id: "dorm", name: "Server Room & Lounge", x: 60, y: 350, width: 240, height: 180, 
      roofColor: "#ef4444", wallColor: "#7c2d12", floorColor: "#ffedd5", 
      icon: "🏡", textColor: "text-red-600" 
    },
    upbit: { 
      id: "upbit", name: "Upbit Trading Post", x: 500, y: 350, width: 240, height: 180, 
      roofColor: "#2563eb", wallColor: "#1e3a8a", floorColor: "#dbeafe", 
      icon: "🏺", textColor: "text-blue-600" 
    },
  };

  // Expanded BotCharacters list to include MLP agents, RL agent, and S&P500 Trader
  const [bots, setBots] = useState<BotCharacter[]>([
    // Trader Bots
    { id: "btc", name: "BTC Trader (7호기)", color: "#F7931A", symbol: "₿", x: 130, y: 160, targetX: 130, targetY: 160, state: "idle", action: "working" },
    { id: "eth", name: "ETH Trader (3호기)", color: "#3b82f6", symbol: "Ξ", x: 580, y: 450, targetX: 580, targetY: 450, state: "idle", action: "working" },
    { id: "sol", name: "SOL Trader (9호기)", color: "#10b981", symbol: "◎", x: 630, y: 160, targetX: 630, targetY: 160, state: "idle", action: "researching" },
    
    // MLP Advisor Bots
    { id: "btc_mlp", name: "BTC MLP Advisor", color: "#fbbf24", symbol: "M_₿", x: 210, y: 150, targetX: 210, targetY: 150, state: "idle", action: "working" },
    { id: "eth_mlp", name: "ETH MLP Advisor", color: "#60a5fa", symbol: "M_Ξ", x: 650, y: 430, targetX: 650, targetY: 430, state: "idle", action: "working" },
    { id: "sol_mlp", name: "SOL MLP Advisor", color: "#34d399", symbol: "M_◎", x: 550, y: 150, targetX: 550, targetY: 150, state: "idle", action: "researching" },
    
    // RL Coordinator Bot
    { id: "rl_agent", name: "Federated RL Agent", color: "#c084fc", symbol: "RL_🧠", x: 400, y: 140, targetX: 400, targetY: 140, state: "idle", action: "working" },
    
    // S&P500 Trader Bot (AI)
    { id: "sp500", name: "S&P500 Trader (AI)", color: "#ef4444", symbol: "📈", x: 300, y: 140, targetX: 300, targetY: 140, state: "idle", action: "working" },
  ]);

  // Specific computer terminals logs mock database
  const getComputerLogs = (buildingId: string) => {
    const timeStr = `${hour.toString().padStart(2, "0")}:${minute.toString().padStart(2, "0")}`;
    
    switch (buildingId) {
      case "binance":
        return [
          `[${timeStr}] $ sudo systemctl status binance-scan-daemon`,
          `[${timeStr}] ● binance-scan-daemon.service - Binance Live Spot Scanner`,
          `[${timeStr}]    Active: active (running) (24/7 NO SLEEP MODE)`,
          `[${timeStr}] [BTC Trader] Scanning Binance spot klines... 120 candles converged.`,
          `[${timeStr}] [BTC Trader] Spot arbitrage signal trigger check: abs_spread >= 0.118%`,
          `[${timeStr}] [BTC MLP] Ingesting features: rsi=48.2, macd_hist=-0.04, vol_ratio=1.04`,
          `[${timeStr}] [BTC MLP] Model inference complete. price_drop_probability = 34.2%`,
          `[${timeStr}] [BTC MLP] Status: SAFE. Trade authorization GRANTED.`,
          `[${timeStr}] [BTC Trader] Order matching initialized. Send to Bybit.`,
        ];
      case "upbit":
        return [
          `[${timeStr}] Upbit API Client v1.9 - Connection Established`,
          `[${timeStr}] [ETH Trader] Scanned domestic-foreign premium spread...`,
          `[${timeStr}] [ETH Trader] Current premium: ${marketStatus === "ALERT" ? "+4.95% (OVERHEAT)" : "+3.24%"}`,
          `[${timeStr}] [ETH MLP] Warning! Volatility index spike detected on Upbit.`,
          `[${timeStr}] [ETH MLP] Feature calculation: spread_spot=0.15%, kimchi_premium=${marketStatus === "ALERT" ? "4.95%" : "3.24%"}`,
          `[${timeStr}] [ETH MLP] Model inference complete. price_drop_probability = ${marketStatus === "ALERT" ? "74.1% (DANGER)" : "42.5%"}`,
          marketStatus === "ALERT" 
            ? `[${timeStr}] 🚫 [ETH MLP] halt_threshold (0.50) exceeded. TRIGGER TRADE HALTED!` 
            : `[${timeStr}] [ETH MLP] Status: PASS. Halted threshold check passed.`,
          `[${timeStr}] [ETH Trader] Kimchi premium pot (옹기) status monitored.`,
        ];
      case "oracle":
        return [
          `[${timeStr}] Gemini Oracle Gateway CLI v0.8 (24/7 CONNECTED)`,
          `[${timeStr}] Connecting to gemini-flash-latest API endpoint... Success.`,
          `[${timeStr}] [SOL Trader] Loading GICS sector orbit coordinates from database...`,
          `[${timeStr}] [SOL Trader] Ranked sector orbits loaded: 11 GICS sectors centroid displacement.`,
          `[${timeStr}] [SOL MLP] Computing trajectory vector coordinates for symbol SOLUSDT.`,
          `[${timeStr}] [SOL MLP] MLP drop predictor probability: 23.4%`,
          `[${timeStr}] [SOL Trader] Orbit SVD residual MLP: Utilities sector in Recovery Setup phase.`,
        ];
      case "townhall":
        return [
          `[${timeStr}] Federated Aggregator Daemon v1.1 - Mainframe Status`,
          `[${timeStr}] [RL Agent] Init FedAvg aggregation sequence. Epoch ${hour}`,
          `[${timeStr}] [RL Agent] Local client consent checklist:`,
          `[${timeStr}]   - Client 'BTC Trader': CONSENT_GRANTED (Q-Table ready)`,
          `[${timeStr}]   - Client 'ETH Trader': CONSENT_GRANTED (Q-Table ready)`,
          `[${timeStr}]   - Client 'SOL Trader': CONSENT_GRANTED (Q-Table ready)`,
          `[${timeStr}] [RL Agent] Merging local Q-values: Q_local = (1 - eta) * Q_local + eta * Q_global`,
          `[${timeStr}] [RL Agent] Scoring daily Oh-seon report summary with Gemini API...`,
          `[${timeStr}] [RL Agent] Report Evaluation Score: 85/100. Critique generated.`,
          `[${timeStr}] [RL Agent] Current halt_threshold dynamic setting: ${marketStatus === "ALERT" ? "0.45 (Conservative)" : "0.50 (Normal)"}`,
          `[${timeStr}] [S&P500 Trader] US Market opening check: 09:30 AM NY time (22:30 KST).`,
          `[${timeStr}] [S&P500 Trader] US Market closing check: 04:00 PM NY time (05:00 KST).`,
          `[${timeStr}] [S&P500 Trader] Loaded topPicks for virtual execution.`,
          `[${timeStr}] [S&P500 Trader] Active portfolio value: $100,000 USD (Virtual).`,
        ];
      case "dorm":
        return [
          `[${timeStr}] Server Room & Cooling Lounge - Mainframe Status`,
          `[${timeStr}] [System] Server cooling fans: 3200 RPM (HIGH PERFORMANCE)`,
          `[${timeStr}] [System] Smart stove active. Virtual coffee brewing complete.`,
          `[${timeStr}] [BTC Trader] "야, 오늘 바이낸스 스프레드 갭 좀 타이트하더라."`,
          `[${timeStr}] [ETH Trader] "나 아까 김프 급변동할 때 청산될 뻔 함 ㄷㄷ"`,
          `[${timeStr}] [SOL Trader] "오라클 신전 쾌적하다 OCI 무료 서버 성능 굿"`,
          `[${timeStr}] [RL Agent] "24시간 풀 야근 기프트카드 하나 줘라 대가리서버야."`,
          `[${timeStr}] [BTC MLP] "서버 백업 데이터 동기화 완료. 무중단 가동 유지."`,
        ];
      default:
        return [];
    }
  };

  // Handle log scrolling
  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  // Tick generator for bobbing sprites and clock (100ms)
  useEffect(() => {
    const interval = setInterval(() => {
      setTick((t) => (t + 1) % 100);
    }, 100);
    return () => clearInterval(interval);
  }, []);

  // Time clock loop
  useEffect(() => {
    if (isPaused || syncingState !== "IDLE" || marketStatus === "ALERT") return;

    const interval = setInterval(() => {
      setMinute((m) => {
        if (m >= 59) {
          setHour((h) => (h >= 23 ? 0 : h + 1));
          return 0;
        }
        return m + 1;
      });
    }, 500 / timeSpeed);

    return () => clearInterval(interval);
  }, [isPaused, timeSpeed, syncingState, marketStatus]);

  // 24-Hour Infinite Labor Schedule (No sleeping, only overclocking deep learning at night!)
  const getScheduleTarget = (botId: string, h: number) => {
    // 00:00 - 08:00 -> Night shift / Overclocking deep analysis at office desks
    if (h >= 0 && h < 8) {
      if (botId === "btc") return { x: 130, y: 150, action: "overclocking" as const };
      if (botId === "btc_mlp") return { x: 210, y: 150, action: "overclocking" as const };
      
      if (botId === "eth") return { x: 570, y: 430, action: "overclocking" as const };
      if (botId === "eth_mlp") return { x: 650, y: 430, action: "overclocking" as const };
      
      if (botId === "sol") return { x: 630, y: 150, action: "overclocking" as const };
      if (botId === "sol_mlp") return { x: 550, y: 150, action: "overclocking" as const };
      if (botId === "sp500") return { x: 300, y: 140, action: "overclocking" as const };
      
      return { x: 400, y: 140, action: "overclocking" as const }; // rl_agent
    }
    
    // 08:00 - 12:00 -> Morning standard trading
    if (h >= 8 && h < 12) {
      if (botId === "btc") return { x: 130, y: 150, action: "working" as const };
      if (botId === "btc_mlp") return { x: 210, y: 150, action: "working" as const };
      
      if (botId === "eth") return { x: 570, y: 430, action: "working" as const };
      if (botId === "eth_mlp") return { x: 650, y: 430, action: "working" as const };
      
      if (botId === "sol") return { x: 630, y: 150, action: "researching" as const };
      if (botId === "sol_mlp") return { x: 550, y: 150, action: "researching" as const };
      if (botId === "sp500") return { x: 300, y: 140, action: "working" as const };
      
      return { x: 400, y: 140, action: "working" as const }; // rl_agent
    }
    
    // 12:00 - 14:00 -> Sync Meeting at Town Hall
    if (h >= 12 && h < 14) {
      if (botId === "btc") return { x: 330, y: 160, action: "syncing" as const };
      if (botId === "btc_mlp") return { x: 360, y: 160, action: "syncing" as const };
      
      if (botId === "eth") return { x: 440, y: 160, action: "syncing" as const };
      if (botId === "eth_mlp") return { x: 470, y: 160, action: "syncing" as const };
      
      if (botId === "sol") return { x: 380, y: 200, action: "syncing" as const };
      if (botId === "sol_mlp") return { x: 420, y: 200, action: "syncing" as const };
      if (botId === "sp500") return { x: 300, y: 200, action: "syncing" as const };
      
      return { x: 400, y: 140, action: "syncing" as const }; // rl_agent
    }
    
    // 14:00 - 17:00 -> Plaza square socialize & premium chat
    if (h >= 14 && h < 17) {
      if (botId === "btc") return { x: 370, y: 300, action: "socializing" as const };
      if (botId === "btc_mlp") return { x: 350, y: 320, action: "socializing" as const };
      
      if (botId === "eth") return { x: 430, y: 300, action: "socializing" as const };
      if (botId === "eth_mlp") return { x: 450, y: 320, action: "socializing" as const };
      
      if (botId === "sol") return { x: 400, y: 330, action: "socializing" as const };
      if (botId === "sol_mlp") return { x: 380, y: 340, action: "socializing" as const };
      if (botId === "sp500") return { x: 410, y: 340, action: "socializing" as const };
      
      return { x: 400, y: 290, action: "socializing" as const }; // rl_agent
    }
    
    // 17:00 - 24:00 -> Server Room & Lounge (Cooling & Backup - No sleeping!)
    if (h >= 17 && h < 24) {
      if (botId === "btc") return { x: 110, y: 490, action: "resting" as const };
      if (botId === "btc_mlp") return { x: 150, y: 490, action: "resting" as const };
      
      if (botId === "eth") return { x: 190, y: 490, action: "resting" as const };
      if (botId === "eth_mlp") return { x: 230, y: 490, action: "resting" as const };
      
      if (botId === "sol") return { x: 180, y: 410, action: "resting" as const }; // backing up servers
      if (botId === "sol_mlp") return { x: 550, y: 150, action: "resting" as const }; // cooling down at desk
      if (botId === "sp500") return { x: 230, y: 410, action: "resting" as const }; // rest in lounge
      
      return { x: 160, y: 495, action: "resting" as const }; // rl_agent
    }
    
    return { x: 400, y: 300, action: "idle" as const };
  };

  // Bot updates loop
  useEffect(() => {
    const interval = setInterval(() => {
      setBots((prevBots) =>
        prevBots.map((bot) => {
          let targetX = bot.targetX;
          let targetY = bot.targetY;
          let currentAction = bot.action;
          let currentState = bot.state;

          if (marketStatus === "NORMAL" && syncingState === "IDLE") {
            const sched = getScheduleTarget(bot.id, hour);
            targetX = sched.x;
            targetY = sched.y;
            currentAction = sched.action;
            currentState = "idle";
          }

          const dx = targetX - bot.x;
          const dy = targetY - bot.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          const speed = currentState === "warning" ? 11 : currentState === "syncing" ? 7 : 3.5;

          let nextX = bot.x;
          let nextY = bot.y;

          if (dist > 3) {
            nextX += (dx / dist) * speed;
            nextY += (dy / dist) * speed;
          } else {
            nextX = targetX;
            nextY = targetY;
          }

          // Random Speech Bubble Trigger
          let newBubble = bot.bubbleText;
          let newTimer = bot.bubbleTimer ? bot.bubbleTimer - 1 : 0;

          if (newTimer <= 0) {
            newBubble = undefined;
          }

          if (!newBubble && dist <= 5 && Math.random() < 0.012) {
            const dialogue = getDialogueText(bot.id, currentState, currentAction);
            newBubble = dialogue;
            newTimer = 50;

            let emojiName = `🤖 ${bot.id.toUpperCase()}`;
            if (bot.id === "btc") emojiName = "🧡 BTC Trader";
            else if (bot.id === "btc_mlp") emojiName = "🛡️ BTC MLP";
            else if (bot.id === "eth") emojiName = "💙 ETH Trader";
            else if (bot.id === "eth_mlp") emojiName = "🛡️ ETH MLP";
            else if (bot.id === "sol") emojiName = "💚 SOL Trader";
            else if (bot.id === "sol_mlp") emojiName = "🛡️ SOL MLP";
            else if (bot.id === "rl_agent") emojiName = "👑 RL Agent";

            setLogs((l) => [...l, `[${emojiName}] "${dialogue}"`]);
          }

          return {
            ...bot,
            x: nextX,
            y: nextY,
            targetX,
            targetY,
            action: currentAction,
            state: currentState,
            bubbleText: newBubble,
            bubbleTimer: newTimer,
          };
        })
      );
    }, 60);

    return () => clearInterval(interval);
  }, [hour, marketStatus, syncingState]);

  const getDialogueText = (botId: string, state: string, action: string) => {
    if (state === "warning") {
      const alerts = {
        btc: ["🚨 비상! 대피 오두막으로 갑니다!", "🚫 바이낸스 포지션 홀딩!"],
        btc_mlp: ["🚨 [WARNING] 가격 하락 확률 56% 돌파!", "🛡️ 차단 트리거 전송 완료!"],
        eth: ["🚨 매도 시그널 감지! 침대 밑으로 숨자!", "🚫 국내 프리미엄 거래 락!"],
        eth_mlp: ["🚨 [WARNING] 김프 과열 및 하락확률 74.1%!", "🛡️ 긴급 halt_threshold 업데이트!"],
        sol: ["🚨 솔라나 트랙 탈출! 오라클 복귀!", "🚫 세이프 리밸런싱 실행!"],
        sol_mlp: ["🚨 [WARNING] 솔라나 궤적 변위치 낙폭 과대!", "🛡️ 오라클 긴급 필터 작동!"],
        rl_agent: ["👑 [RL Agent] 전원 매매 차단 및 쉘터 대기 프로토콜!", "👑 halt_threshold = 0.45 격상!"],
        sp500: ["🚨 미장 대폭락 주의보! 주식 비중을 40%로 축소하고 안전자산으로 헤징합니다!"],
      };
      const arr = alerts[botId as keyof typeof alerts] || ["🚨 비상대피!"];
      return arr[Math.floor(Math.random() * arr.length)];
    }

    if (action === "overclocking") {
      const overclockLines = {
        btc: ["⚡ 새벽 3시 바이낸스 실시간 호가 감시 가동!", "📊 24시간 가동 모드 온. 잠잘 시간이 어디 있어!"],
        btc_mlp: ["🧠 야간 MLP 오버클럭... 발열량 18% 돌파!", "🛡️ 야간 변동성 특징값 분석 패턴 활성화."],
        eth: ["⚡ 업비트 야간 김프 왜곡 포착 대기 중!", "🏺 김프 항아리 야간 상시 계측 개시!"],
        eth_mlp: ["🧠 해외-국내 야간 선물 스프레드 가동률 100%.", "🛡️ 야간 매도 매물 출하 확률 체크 중."],
        sol: ["⚡ 오라클 제미나이와 야간 데이터 백서 속닥속닥.", "🔮 24시간 실시간 GICS 궤적 drift 로드."],
        sol_mlp: ["🧠 야간 SVD Centroid Transition 학습 중.", "🛡️ 야간 가중치 매트릭스 백업 성공."],
        rl_agent: ["👑 [RL Agent] 24시간 무중단 합의 가동 중. 켜두고 잔다.", "👑 야간 매매 config 락 체크 중."],
        sp500: ["⚡ 미국 주식 시장 야간 선물 실시간 궤적 예측 중!", "📊 S&P500 인포메이션 맵 야간 오버클럭 분석 가동!"],
      };
      const arr = overclockLines[botId as keyof typeof overclockLines] || ["⚡ 야간 전산 오버클럭 가동 중."];
      return arr[Math.floor(Math.random() * arr.length)];
    }

    if (action === "syncing") {
      if (botId === "rl_agent") return "👑 중앙 회의 개시! Q-테이블 다 집합해봐.";
      return "🧠 FedAvg 파일 전송 완료!";
    }

    if (action === "working") {
      if (botId === "btc") return "🖥️ 바이낸스 호가창 매수 강도 모니터링.";
      if (botId === "btc_mlp") return "🛡️ 실시간 BTC 15분 단기 하락 확률 연산 중.";
      if (botId === "eth") return "🏺 업비트 김치 프리미엄 괴리 계산!";
      if (botId === "eth_mlp") return "🛡️ 해외-국내 가격 프리미엄 변동성 예측 중.";
      if (botId === "sp500") return "🏛️ S&P500 AI 추천 포트폴리오를 기반으로 가상 매매 집행 중!";
      return "👑 에이전트 리스크 가중치 분배 중."; // rl_agent
    }

    if (action === "researching") {
      if (botId === "sol") return "🔮 오라클 제미나이한테 예측 궤적 물어보기!";
      return "🛡️ GICS 11개 섹터 궤적 변위 오차 연산 중."; // sol_mlp
    }

    if (action === "socializing") {
      const chats = {
        btc: "🌸 날씨 좋은데 다 같이 차익거래 한 탕 할까?",
        btc_mlp: "🍭 호가 데이터 노이즈 걸러내는 중.",
        eth: "🍯 오늘 김프 3%대 너무 꿀맛인데?",
        eth_mlp: "🛡️ 변동성 가중치 모델 테스트 성공!",
        sol: "🔮 제미나이가 내일 솔라나 리밸런싱 좋대.",
        sol_mlp: "🛡️ Utilities 섹터 궤적 속도 빨라짐.",
        rl_agent: "👑 애들아, FedAvg 학습할 때 동의 꼭 켜둬라.",
        sp500: "🌸 미국 주식 시장 장개시와 장마감 시간을 대기하며 대칭성 분석 중.",
      };
      return chats[botId as keyof typeof chats] || "🤖 평화로운 퀀토피아.";
    }

    if (action === "resting") {
      return "☕ 냉온수기에서 가상 커피 충전 중.";
    }

    return "🤖 대기 모드 작동 중.";
  };

  // Sync animation triggers
  useEffect(() => {
    if (syncingState === "WALKING") {
      setBots((prev) =>
        prev.map((bot) => ({
          ...bot,
          state: "syncing",
          targetX: 
            bot.id === "btc" ? 330 : 
            bot.id === "btc_mlp" ? 360 : 
            bot.id === "eth" ? 440 : 
            bot.id === "eth_mlp" ? 470 : 
            bot.id === "sol" ? 385 : 
            bot.id === "sol_mlp" ? 415 : 
            bot.id === "sp500" ? 300 : 400,
          targetY: bot.id === "rl_agent" ? 140 : (bot.id === "sp500" ? 140 : (bot.id.includes("mlp") ? 200 : 160)),
          bubbleText: bot.id === "rl_agent" ? "📢 회의 준비!" : "🏃‍♂️ 회의 지각하겠다!",
          bubbleTimer: 45,
        }))
      );

      const timer = setInterval(() => {
        setBots((currentBots) => {
          const arrived = currentBots.every((b) => {
            const dx = b.x - b.targetX;
            const dy = b.y - b.targetY;
            return Math.sqrt(dx * dx + dy * dy) <= 8;
          });

          if (arrived) {
            clearInterval(timer);
            setSyncingState("SYNCING");
            setLogs((l) => [...l, "🏛️ [System] 모든 에이전트와 MLP 조언자들이 타운홀 테이블에 착석해 데이터 연동을 시작했습니다."]);
          }
          return currentBots;
        });
      }, 300);

      return () => clearInterval(timer);
    } else if (syncingState === "SYNCING") {
      const progressTimer = setInterval(() => {
        setSyncProgress((p) => {
          if (p >= 100) {
            clearInterval(progressTimer);
            setSyncingState("COMPLETE");
            return 100;
          }
          return p + 10;
        });
      }, 200);

      return () => clearInterval(progressTimer);
    } else if (syncingState === "COMPLETE") {
      setLogs((l) => [
        ...l,
        "🤝 [RL Agent] 3개 퀀트 봇의 Q-테이블 가중치 병합 성공! (FedAvg v1.1)",
        "🛡️ [BTC MLP] 하락 예측 가중치 갱신 확인.",
        "✨ [Aggregator] 로컬 뇌 지도 가중치 리프레시 배포 완료.",
        "🎉 [System] 연합 학습 가중치 매개변수가 전용 기기에 성공적으로 동기화되었습니다."
      ]);

      setBots((prev) =>
        prev.map((bot) => ({
          ...bot,
          state: "idle",
          bubbleText: bot.id.includes("mlp") ? "🛡️ 모델 최적화 완료!" : "✨ 전략 지도 업데이트 완료!",
          bubbleTimer: 50,
          rewardFloating: bot.id === "btc" ? "+0.3024 Q" : bot.id === "eth" ? "-0.0003 Q" : bot.id === "sol" ? "+0.0154 Q" : (bot.id === "sp500" ? "+$42.50" : undefined),
        }))
      );

      setTimeout(() => {
        setBots((prev) =>
          prev.map((bot) => ({
            ...bot,
            rewardFloating: undefined,
          }))
        );
        setSyncProgress(0);
        setSyncingState("IDLE");
      }, 3000);
    }
  }, [syncingState]);

  // Market Downtrend Warning Scenario
  const triggerDowntrend = () => {
    if (marketStatus === "NORMAL") {
      setMarketStatus("ALERT");
      setRiskMode("Safety Mode (0.45)");
      setLogs((l) => [
        ...l,
        "🚨 [WARNING] 매도 급증 위험! 단기 하락 필터 가동!",
        "🏡 [System] 위험 경보! 트레이더들과 RL 코디네이터는 서버룸 안전 유닛으로 대피하고, MLP 조언자들은 PC 콘솔을 긴급 셧다운합니다."
      ]);

      setBots((prev) =>
        prev.map((bot) => {
          let tx = bot.x;
          let ty = bot.y;
          
          if (bot.id === "btc") { tx = 100; ty = 410; }
          else if (bot.id === "eth") { tx = 140; ty = 410; }
          else if (bot.id === "sol") { tx = 180; ty = 410; }
          else if (bot.id === "rl_agent") { tx = 220; ty = 410; }
          else if (bot.id === "sp500") { tx = 260; ty = 410; }
          
          // MLP advisors lock down in their respective offices
          else if (bot.id === "btc_mlp") { tx = 210; ty = 150; }
          else if (bot.id === "eth_mlp") { tx = 650; ty = 430; }
          else if (bot.id === "sol_mlp") { tx = 550; ty = 150; }

          return {
            ...bot,
            state: "warning",
            targetX: tx,
            targetY: ty,
            bubbleText: bot.id.includes("mlp") ? "🛡️ 긴급 셧다운!" : "😱 비상 대피!",
            bubbleTimer: 60,
          };
        })
      );
    } else {
      setMarketStatus("NORMAL");
      setRiskMode("Normal Mode (0.50)");
      setLogs((l) => [
        ...l,
        "🟢 [System] 시장 시세 갭 수렴. 위험 경보 해제!",
        "🏡 [System] 위험 상태 철회. 모든 에이전트들이 정상 스케줄에 복귀합니다."
      ]);

      setBots((prev) =>
        prev.map((bot) => ({
          ...bot,
          state: "idle",
          bubbleText: "😊 안도!",
          bubbleTimer: 35,
        }))
      );
    }
  };

  const startFederatedSync = () => {
    if (syncingState !== "IDLE") return;
    setSyncingState("WALKING");
    setLogs((l) => [...l, "📢 [System] 강제 가중치 병합(FedAvg) 소집 명령이 선포되었습니다!"]);
  };

  return (
    <div className="min-h-screen bg-[#1e293b] text-slate-800 font-sans p-6 selection:bg-amber-400 selection:text-slate-900">
      
      {/* RPG HUD Styled Header */}
      <div className="bg-[#fef08a] border-4 border-[#7c2d12] rounded-2xl p-5 mb-6 shadow-[8px_8px_0px_#451a03] flex flex-col md:flex-row justify-between items-start md:items-center">
        <div>
          <h1 className="text-3xl font-black tracking-tight text-[#7c2d12] flex items-center gap-3" style={{ textShadow: "2px 2px 0px #fef08a" }}>
            🏡 퀀토피아 AI 봇 스몰빌 타운 (Quantopia)
          </h1>
          <p className="text-xs font-bold text-amber-900 mt-1 font-mono">
            * 25명의 귀여운 AI 봇들(Traders, MLPs, Federated RL Coordinator)의 24/7 무중단 야근 샌드박스 *
          </p>
        </div>
        <div className="mt-4 md:mt-0 flex gap-4 font-mono">
          <div className="bg-amber-100 border-2 border-[#7c2d12] rounded-xl px-4 py-2 text-xs flex flex-col justify-center shadow-[4px_4px_0px_#7c2d12]">
            <span className="text-amber-800 font-extrabold text-[10px]">⏰ 타운 가상 시간</span>
            <span className="font-black text-[#7c2d12] text-sm mt-0.5">
              📅 {hour.toString().padStart(2, "0")}:{minute.toString().padStart(2, "0")}
            </span>
          </div>
          <div className="bg-amber-100 border-2 border-[#7c2d12] rounded-xl px-4 py-2 text-xs flex flex-col justify-center shadow-[4px_4px_0px_#7c2d12]">
            <span className="text-amber-800 font-extrabold text-[10px]">⚙️ 마을 타임슬라이스</span>
            <div className="flex gap-2 items-center mt-1">
              <button
                onClick={() => setIsPaused(!isPaused)}
                className="text-[9px] font-black text-amber-900 bg-amber-200 px-2 py-0.5 rounded border border-amber-900 active:translate-y-0.5"
              >
                {isPaused ? "▶️ 재생" : "⏸️ 일시정지"}
              </button>
              <select
                value={timeSpeed}
                onChange={(e) => setTimeSpeed(Number(e.target.value))}
                className="text-[9px] font-bold text-amber-900 bg-amber-200 rounded border border-amber-900 px-1 py-0.5"
              >
                <option value="1">속도 1x</option>
                <option value="2">속도 2x</option>
                <option value="5">속도 5x</option>
                <option value="10">속도 10x</option>
              </select>
            </div>
          </div>
        </div>
      </div>

      {/* Main Grid Layout - Fixed Height matching bug fixed! */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 items-stretch">
        
        {/* Game Simulator Arena */}
        <div className="xl:col-span-2 bg-[#fdf6e2] border-4 border-[#7c2d12] rounded-3xl overflow-hidden shadow-[8px_8px_0px_#451a03] flex flex-col h-[780px] relative">
          
          {/* Sub Header / HUD Status Bar */}
          <div className="bg-[#fed7aa] border-b-4 border-[#7c2d12] px-5 py-3 flex justify-between items-center z-10 font-mono">
            <div className="flex items-center gap-2">
              <span className="text-xs font-black text-[#7c2d12]">&gt;_ TOWN_MAP_SCREEN_2D</span>
              {marketStatus === "ALERT" && (
                <span className="bg-red-500 text-white border-2 border-red-950 text-[8px] px-2 py-0.5 rounded-full font-black uppercase animate-bounce shadow">
                  🚨 폭락 비상대피 사이렌
                </span>
              )}
            </div>
            <div className="text-[10px] font-extrabold text-amber-950 bg-amber-100/50 border border-amber-900/20 px-2.5 py-0.5 rounded-full">
              뇌 활성 리스크 모드: <code className="text-red-700 font-black">{riskMode}</code>
            </div>
          </div>

          {/* 2D Canvas RPG Map */}
          <div
            className="flex-1 relative w-full overflow-hidden"
            style={{
              backgroundImage: "radial-gradient(#bbf7d0 1.2px, transparent 1.2px)",
              backgroundSize: "20px 20px",
              backgroundColor: "#86efac",
            }}
          >
            {/* Cute Pathway Grid Layers */}
            <svg className="absolute inset-0 w-full h-full opacity-60 pointer-events-none">
              <rect x="380" y="80" width="40" height="420" fill="#fdba74" rx="6" />
              <rect x="80" y="280" width="640" height="40" fill="#fdba74" rx="6" />
              <circle cx="400" cy="300" r="48" fill="#fdba74" />
            </svg>

            {/* RPG Center Fountain */}
            <div className="absolute left-[400px] top-[300px] transform -translate-x-1/2 -translate-y-1/2 pointer-events-none flex flex-col items-center">
              <div className="w-12 h-12 rounded-full border-2 border-sky-600 bg-sky-300 flex items-center justify-center animate-pulse shadow-md">
                <div className="w-6 h-6 rounded-full border border-sky-400 bg-sky-200 flex items-center justify-center">
                  <div className="w-2.5 h-2.5 rounded-full bg-sky-500"></div>
                </div>
              </div>
              <span className="text-[8px] font-bold font-mono text-[#7c2d12] mt-1 bg-amber-100 border border-[#7c2d12] px-1 rounded shadow-sm">마을 광장 분수</span>
            </div>

            {/* Render 2D Buildings Floor Plans */}
            {Object.entries(buildings).map(([key, b]) => {
              const isSyncActive = key === "townhall" && syncingState === "SYNCING";
              const isAlert = marketStatus === "ALERT" && key === "dorm";

              return (
                <div
                  key={key}
                  onClick={() => setActiveTerminal(b.id)}
                  className="absolute border-4 rounded-2xl flex flex-col justify-between transition-all duration-300 shadow-md cursor-pointer hover:shadow-lg group"
                  style={{
                    left: b.x,
                    top: b.y,
                    width: b.width,
                    height: b.height,
                    borderColor: "#7c2d12",
                    backgroundColor: b.floorColor,
                    boxShadow: isAlert
                      ? "0 0 25px rgba(239, 68, 68, 0.4)"
                      : isSyncActive
                      ? "0 0 25px rgba(147, 51, 234, 0.4)"
                      : "none",
                  }}
                >
                  {/* Roof Top View Visual overlay */}
                  <div 
                    className="absolute -top-4 left-0 right-0 h-4 border-t-4 border-l-4 border-r-4 border-[#7c2d12] rounded-t-lg pointer-events-none"
                    style={{ backgroundColor: b.roofColor }}
                  ></div>

                  {/* Building Label Card */}
                  <div className="flex justify-between items-center border-b-2 border-[#7c2d12] bg-white/40 px-2 py-1.5 pointer-events-none">
                    <span className="text-[10px] font-black font-mono tracking-tight flex items-center gap-1 text-[#7c2d12]">
                      {b.icon} {b.name}
                    </span>
                    <span className="text-[7px] font-bold text-amber-900 group-hover:scale-110 transition-transform flex items-center gap-1">
                      🖥️ [콘솔]
                    </span>
                  </div>

                  {/* Room Interior Content */}
                  <div className="flex-1 relative w-full overflow-hidden text-slate-800 font-mono text-[9px] pointer-events-none p-1.5">
                    {/* Binance Items */}
                    {key === "binance" && (
                      <div className="absolute inset-0 grid grid-cols-2 gap-2 p-1 text-[7px]">
                        <div className="border border-amber-950/20 rounded bg-amber-100/40 p-1 flex flex-col justify-between shadow-inner">
                          <span>🖥️ BTC PC (Trader)</span>
                          <span className="text-yellow-600 font-bold text-[6px]">ON_DUTY</span>
                        </div>
                        <div className="border border-amber-950/20 rounded bg-amber-100/40 p-1 flex flex-col justify-between shadow-inner">
                          <span>🖥️ BTC PC (MLP)</span>
                          <span className="text-amber-800 font-bold text-[6px]">PREDICTING</span>
                        </div>
                        <div className="col-span-2 border border-amber-950/20 rounded bg-amber-100/40 p-1 flex items-center justify-between">
                          <span>💾 Binance System Mainframe</span>
                          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-ping"></span>
                        </div>
                      </div>
                    )}

                    {/* Oracle Items */}
                    {key === "oracle" && (
                      <div className="absolute inset-0 flex flex-col justify-between p-1.5 text-[7px]">
                        <div className="flex justify-between items-center border border-purple-950/20 rounded bg-purple-100/40 p-1.5">
                          <span>🔮 Orb Mainframe</span>
                          <span className="text-pink-600 animate-pulse font-black">GEMINI_AI</span>
                        </div>
                        <div className="flex-1 mt-1 border border-purple-950/20 rounded bg-purple-100/40 p-1.5 overflow-hidden flex flex-col justify-between shadow-inner">
                          <span>📚 SOL Trader & MLP Scrolls</span>
                          <div className="flex justify-between text-[6px] text-pink-500 font-bold">
                            <span>SVD_Orbits:</span>
                            <span>MONITORING</span>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* Town Council Hall Items */}
                    {key === "townhall" && (
                      <div className="absolute inset-0 flex flex-col justify-between p-1">
                        <div className="flex-1 border-2 border-dashed border-purple-950/20 rounded-lg bg-purple-100/30 flex items-center justify-center relative shadow-inner">
                          {/* Round Conference Table */}
                          <div className="w-24 h-12 rounded-full border-2 border-purple-700 bg-purple-300 flex items-center justify-center font-black text-purple-850 text-[7.5px] shadow-md">
                            RL AGENT DESK
                          </div>
                        </div>
                      </div>
                    )}

                    {/* Dormitory Cozy Cabin Items */}
                    {key === "dorm" && (
                      <div className="absolute inset-0 grid grid-cols-4 gap-1 p-1 text-[7px]">
                        <div className="border border-orange-950/20 rounded bg-amber-500/10 p-1 flex flex-col justify-between text-center border-t-2 border-t-[#F7931A] shadow-sm">
                          <span className="text-[6px]">🖥️</span>
                          <span className="text-[5.5px] text-[#F7931A] font-black">Server A</span>
                        </div>
                        <div className="border border-orange-950/20 rounded bg-blue-500/10 p-1 flex flex-col justify-between text-center border-t-2 border-t-blue-500 shadow-sm">
                          <span className="text-[6px]">🖥️</span>
                          <span className="text-[5.5px] text-blue-600 font-black">Server B</span>
                        </div>
                        <div className="border border-orange-950/20 rounded bg-emerald-500/10 p-1 flex flex-col justify-between text-center border-t-2 border-t-emerald-500 shadow-sm">
                          <span className="text-[6px]">🖥️</span>
                          <span className="text-[5.5px] text-emerald-600 font-black">Server C</span>
                        </div>
                        <div className="border border-orange-950/20 rounded bg-purple-500/10 p-1 flex flex-col justify-between text-center border-t-2 border-t-purple-500 shadow-sm">
                          <span className="text-[6px]">🖥️</span>
                          <span className="text-[5.5px] text-purple-600 font-black">Server D</span>
                        </div>
                        <div className="col-span-4 border border-orange-950/20 rounded bg-orange-100/30 p-1 flex justify-between items-center shadow-inner">
                          <span>🛋️ Cooling Lounge & Coffee</span>
                          <span>☕</span>
                        </div>
                      </div>
                    )}

                    {/* Upbit Items */}
                    {key === "upbit" && (
                      <div className="absolute inset-0 flex flex-col justify-between p-1.5 text-[7px]">
                        <div className="flex-1 grid grid-cols-2 gap-1.5">
                          <div className="border border-blue-950/20 rounded bg-blue-100/40 p-1 flex flex-col justify-between shadow-inner">
                            <span>🏺 Premium Pot</span>
                            <span className="text-blue-600 font-black text-[6.5px]">KIMCHI</span>
                          </div>
                          <div className="border border-blue-950/20 rounded bg-blue-100/40 p-1 flex flex-col justify-between shadow-inner">
                            <span>🖥️ ETH PC (MLP)</span>
                            <span className="text-emerald-600 font-bold text-[6px]">ACTIVE</span>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}

            {/* Glowing lines during aggregation sync */}
            {syncingState === "SYNCING" && (
              <svg className="absolute inset-0 w-full h-full pointer-events-none z-0">
                {bots.map((bot) => (
                  <line
                    key={bot.id}
                    x1={bot.x}
                    y1={bot.y}
                    x2={400}
                    y2={150}
                    stroke="#9333ea"
                    strokeWidth="2.5"
                    strokeDasharray="5 5"
                    className="animate-[dash_2s_linear_infinite]"
                  />
                ))}
              </svg>
            )}

            {/* Render Cute RPG Bots Characters */}
            {bots.map((bot) => {
              const isMoving = Math.abs(bot.x - bot.targetX) > 2 || Math.abs(bot.y - bot.targetY) > 2;
              const isFacingLeft = bot.targetX < bot.x;

              const bobHeight = isMoving ? Math.sin(tick * 0.8) * 3 : 0;
              const leftLegY = isMoving ? Math.sin(tick * 0.8) * 4 : 0;
              const rightLegY = isMoving ? -Math.sin(tick * 0.8) * 4 : 0;

              return (
                <div
                  key={bot.id}
                  className="absolute transform -translate-x-1/2 -translate-y-1/2 transition-all duration-100 z-20 flex flex-col items-center"
                  style={{ left: bot.x, top: bot.y }}
                >
                  {/* Speech Bubble */}
                  {bot.bubbleText && (
                    <div className="absolute bottom-11 bg-white border-4 border-[#7c2d12] text-[9px] px-2.5 py-1.5 rounded-xl shadow-lg font-black font-mono text-slate-800 whitespace-nowrap animate-bounce border-b-8 z-30">
                      {bot.bubbleText}
                      <div className="absolute top-full left-1/2 transform -translate-x-1/2 border-4 border-transparent border-t-white"></div>
                    </div>
                  )}

                  {/* Floating reward indicators */}
                  {bot.rewardFloating && (
                    <div className="absolute -top-12 text-[10px] font-mono font-black text-[#7c2d12] animate-[fadeOutUp_2s_ease-out_forwards] z-30 bg-amber-200 border-2 border-[#7c2d12] px-1.5 py-0.5 rounded shadow">
                      {bot.rewardFloating}
                    </div>
                  )}

                  {/* Cute Slime/Robot Character Sprite */}
                  <div
                    className="w-9 h-9 relative transition-transform duration-200"
                    style={{
                      transform: `translateY(${bobHeight}px) scaleX(${isFacingLeft ? -1 : 1})`,
                    }}
                  >
                    <svg viewBox="0 0 32 32" className="w-full h-full">
                      {/* Left Leg */}
                      <rect
                        x="9"
                        y="25"
                        width="4"
                        height="7"
                        fill="#7c2d12"
                        rx="1"
                        transform={`translate(0, ${leftLegY})`}
                      />
                      {/* Right Leg */}
                      <rect
                        x="19"
                        y="25"
                        width="4"
                        height="7"
                        fill="#7c2d12"
                        rx="1"
                        transform={`translate(0, ${rightLegY})`}
                      />

                      {/* Slime Shape Body */}
                      <rect x="5" y="8" width="22" height="18" rx="7" fill={bot.color} stroke="#7c2d12" strokeWidth="2" />
                      
                      {/* Cheek indicators */}
                      <ellipse cx="9" cy="18" rx="2" ry="1" fill="#f43f5e" opacity="0.6" />
                      <ellipse cx="23" cy="18" rx="2" ry="1" fill="#f43f5e" opacity="0.6" />

                      {/* Screen CRT Plate */}
                      <rect x="10" y="11" width="12" height="9" rx="2" fill="#0f172a" stroke="#7c2d12" strokeWidth="1" />
                      
                      {/* Logo inside CRT Screen */}
                      <text
                        x="16"
                        y="18"
                        textAnchor="middle"
                        fill="white"
                        fontSize={bot.id.includes("mlp") ? "5" : "8"}
                        fontFamily="monospace"
                        fontWeight="black"
                        transform={isFacingLeft ? "scale(-1, 1) translate(-32, 0)" : ""}
                      >
                        {bot.symbol}
                      </text>

                      {/* Big Anime Eyes */}
                      <circle cx="11" cy="9" r="2" fill="white" stroke="#7c2d12" strokeWidth="1" />
                      <circle cx="11" cy="9" r="0.8" fill="black" />
                      <circle cx="21" cy="9" r="2" fill="white" stroke="#7c2d12" strokeWidth="1" />
                      <circle cx="21" cy="9" r="0.8" fill="black" />
                    </svg>
                  </div>

                  {/* Name Tag */}
                  <span
                    className="text-[8px] font-mono font-black text-slate-800 mt-1 bg-amber-100 border-2 border-[#7c2d12] px-1.5 py-0.5 rounded-md shadow whitespace-nowrap pointer-events-none"
                  >
                    {bot.id.toUpperCase().replace("_MLP", " MLP")} (
                    {bot.action === "overclocking"
                      ? "⚡야근"
                      : bot.action === "working"
                      ? "💼근무"
                      : bot.action === "researching"
                      ? "🔮연구"
                      : bot.action === "syncing"
                      ? "🧠합의"
                      : bot.action === "socializing"
                      ? "🧑‍🤝‍🧑대화"
                      : bot.action === "resting"
                      ? "🔋충전"
                      : "🤖대기"}
                    )
                  </span>
                </div>
              );
            })}

            {/* INTERACTIVE COMPUTER TERMINAL MODAL POPUP */}
            {activeTerminal && (
              <div className="absolute inset-0 bg-black/60 flex items-center justify-center p-8 z-40">
                <div className="bg-slate-950 border-4 border-[#7c2d12] rounded-3xl w-full max-w-lg flex flex-col overflow-hidden shadow-2xl h-[420px]">
                  
                  {/* Modal Header */}
                  <div className="bg-[#fed7aa] border-b-4 border-[#7c2d12] px-4 py-2.5 flex justify-between items-center font-mono">
                    <span className="text-xs font-black text-[#7c2d12] flex items-center gap-1.5">
                      🖥/ {buildings[activeTerminal]?.name} Local Computer Mainframe
                    </span>
                    <button 
                      onClick={(e) => {
                        e.stopPropagation();
                        setActiveTerminal(null);
                      }}
                      className="border-2 border-[#7c2d12] bg-red-500 hover:bg-red-400 text-white font-black text-xs px-2 py-0.5 rounded shadow active:translate-y-0.5"
                    >
                      X (닫기)
                    </button>
                  </div>
                  
                  {/* Green-on-black CRT monitor terminal logs */}
                  <div className="flex-1 bg-black p-4 font-mono text-[11px] text-green-500 overflow-y-auto leading-relaxed custom-scrollbar border-b-2 border-slate-900 shadow-inner">
                    <div className="text-green-600 border-b border-green-950 pb-2 mb-3 text-[9px]">
                      SYSTEM DISK CHECK SUCCESSFUL. LOADING LOCAL AGENT LOGS...
                    </div>
                    {getComputerLogs(activeTerminal).map((line, idx) => (
                      <div key={idx} className="whitespace-pre-wrap">
                        {line}
                      </div>
                    ))}
                    <div className="mt-2 text-green-700 animate-pulse">&gt;_ Waiting for next tick...</div>
                  </div>
                  
                  {/* Modal Footer */}
                  <div className="bg-[#ffedd5] p-3 text-right border-t border-slate-900">
                    <span className="text-[9px] font-bold text-amber-900 font-mono">
                      * 본 로그는 로컬 SQLite3 데이터베이스 및 MLP 가중치 통계치를 실시간 반영한 모형 로그입니다.
                    </span>
                  </div>
                </div>
              </div>
            )}

          </div>

          {/* Sync Progress Bar */}
          {syncingState === "SYNCING" && (
            <div className="bg-[#fed7aa] border-t-4 border-[#7c2d12] p-4">
              <div className="flex justify-between items-center text-xs mb-1 font-mono font-black text-[#7c2d12]">
                <span>합의 서버(Aggregator) 데이터 취합 FedAvg 연산 수행 중...</span>
                <span>{syncProgress}%</span>
              </div>
              <div className="w-full bg-[#ffedd5] h-3.5 rounded-full overflow-hidden border-2 border-[#7c2d12] shadow-inner">
                <div
                  className="bg-gradient-to-r from-purple-500 via-pink-500 to-indigo-500 h-full rounded-full transition-all duration-200"
                  style={{ width: `${syncProgress}%` }}
                ></div>
              </div>
            </div>
          )}

          {/* Control Panel Footer */}
          <div className="bg-[#fed7aa] border-t-4 border-[#7c2d12] p-5 flex flex-wrap gap-4 justify-between items-center">
            <div className="flex gap-3">
              <button
                onClick={startFederatedSync}
                disabled={syncingState !== "IDLE"}
                className={`px-4 py-2.5 rounded-xl text-xs font-black flex items-center gap-2 border-2 shadow-[4px_4px_0px_#451a03] active:translate-y-0.5 active:shadow-none transition-all duration-100 ${
                  syncingState !== "IDLE"
                    ? "bg-amber-100 border-[#7c2d12] text-amber-900/40 cursor-not-allowed"
                    : "bg-purple-500 hover:bg-purple-400 border-[#7c2d12] text-white cursor-pointer"
                }`}
              >
                👥 강제 연합 가중치 병합 (FedAvg)
              </button>
              <button
                onClick={triggerDowntrend}
                className={`px-4 py-2.5 rounded-xl text-xs font-black flex items-center gap-2 border-2 shadow-[4px_4px_0px_#451a03] active:translate-y-0.5 active:shadow-none transition-all duration-100 cursor-pointer ${
                  marketStatus === "ALERT"
                    ? "bg-emerald-500 hover:bg-emerald-400 border-[#7c2d12] text-white"
                    : "bg-red-500 hover:bg-red-400 border-[#7c2d12] text-white animate-pulse"
                }`}
              >
                {marketStatus === "ALERT" ? "🟢 리스크 모드 복구 (NORMAL)" : "🚨 MLP 하락 비상경보 발령 (ALERT)"}
              </button>
            </div>
            <div className="text-[10px] text-amber-950 font-black font-mono">
              💡 **건물을 직접 클릭**하면 건물 내 컴퓨터의 실시간 내부 로그 파일이 열립니다!
            </div>
          </div>
        </div>

        {/* Eavesdropping Console - Fix Page Height Stretching Bug! */}
        <div className="bg-[#fdf6e2] border-4 border-[#7c2d12] rounded-3xl p-5 shadow-[8px_8px_0px_#451a03] flex flex-col h-[780px]">
          {/* Title Bar */}
          <div className="flex items-center justify-between border-b-2 border-[#7c2d12] pb-3.5 mb-4">
            <div className="flex items-center gap-2">
              <span className="w-3 h-3 rounded-full bg-red-600 border border-amber-950"></span>
              <span className="w-3 h-3 rounded-full bg-yellow-400 border border-amber-950"></span>
              <span className="w-3 h-3 rounded-full bg-green-500 border border-amber-950"></span>
              <span className="text-xs font-mono text-[#7c2d12] font-black ml-2">TOWNSHIP_CHATS.LOG</span>
            </div>
            <span className="text-[9px] font-mono font-black text-amber-950 bg-amber-100 border border-amber-900/30 px-2 py-0.5 rounded-full shadow-sm">
              📡 에이전트 무전 감청
            </span>
          </div>

          {/* Dialogues Logs - Scrolling internally now! */}
          <div className="flex-1 overflow-y-auto font-mono text-[11px] p-4 rounded-xl bg-slate-900 border-2 border-[#7c2d12] leading-relaxed space-y-2.5 shadow-inner custom-scrollbar relative">
            <div className="absolute inset-0 bg-gradient-to-b from-transparent via-transparent to-black/30 pointer-events-none opacity-30"></div>
            
            {logs.map((log, idx) => {
              let colorClass = "text-slate-300";
              if (log.includes("[System]")) colorClass = "text-yellow-400 font-extrabold";
              else if (log.includes("[Aggregator]")) colorClass = "text-purple-400 font-extrabold";
              else if (log.includes("[🧡 BTC")) colorClass = "text-[#F7931A] font-bold";
              else if (log.includes("[🛡️ BTC")) colorClass = "text-amber-400 font-bold";
              else if (log.includes("[💙 ETH")) colorClass = "text-blue-400 font-bold";
              else if (log.includes("[🛡️ ETH")) colorClass = "text-blue-300 font-bold";
              else if (log.includes("[💚 SOL")) colorClass = "text-emerald-400 font-bold";
              else if (log.includes("[🛡️ SOL")) colorClass = "text-emerald-300 font-bold";
              else if (log.includes("[👑 RL")) colorClass = "text-purple-400 font-bold";
              else if (log.includes("[WARNING]")) colorClass = "text-red-400 font-black animate-pulse";

              return (
                <div key={idx} className={`${colorClass} whitespace-pre-wrap`}>
                  {log}
                </div>
              );
            })}
            <div ref={logsEndRef} />
          </div>

          {/* Mini Statistics Panel */}
          <div className="mt-4 bg-[#fed7aa] border-2 border-[#7c2d12] p-4 rounded-xl shadow-[4px_4px_0px_#7c2d12] font-mono">
            <h3 className="text-xs font-black text-[#7c2d12] mb-2.5 flex items-center gap-1.5">
              <span>📊</span> 타운십 네트워크 현황
            </h3>
            <div className="space-y-2 text-[10px] font-bold text-amber-950">
              <div className="flex justify-between">
                <span>연합 데이터 테이블:</span>
                <span className="text-purple-900 font-extrabold">federated_q_table</span>
              </div>
              <div className="flex justify-between">
                <span>평균 실현 수익률 (Q):</span>
                <span className="text-red-700 font-black">+0.0100 (PROFIT)</span>
              </div>
              <div className="flex justify-between">
                <span>학습 알고리즘 모드:</span>
                <span className="text-[#7c2d12] font-extrabold">FedAvg (Privacy Shield)</span>
              </div>
              <div className="flex justify-between">
                <span>마을 시간대 에이전트 행보:</span>
                <span className="text-emerald-700 font-black">
                  {hour >= 0 && hour < 8
                    ? "⚡ 야간 전산 오버클럭 가동"
                    : hour >= 8 && hour < 12
                    ? "💼 각자 사무실 근무 및 차트 분석"
                    : hour >= 12 && hour < 14
                    ? "🏛️ 타운홀 연합 모델 동기화 회의"
                    : hour >= 14 && hour < 17
                    ? "🧑‍🤝‍🧑 분수대 광장 교류 및 프리미엄 잡담"
                    : "🔋 서버 룸 백업 및 냉각 루틴 가동"}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <style jsx global>{`
        @keyframes dash {
          to {
            stroke-dashoffset: -20;
          }
        }
        @keyframes fadeOutUp {
          0% {
            opacity: 1;
            transform: translateY(0);
          }
          100% {
            opacity: 0;
            transform: translateY(-40px);
          }
        }
        .custom-scrollbar::-webkit-scrollbar {
          width: 5px;
        }
        .custom-scrollbar::-webkit-scrollbar-track {
          background: rgba(0, 0, 0, 0.1);
        }
        .custom-scrollbar::-webkit-scrollbar-thumb {
          background: rgba(247, 246, 226, 0.3);
          border-radius: 4px;
        }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover {
          background: rgba(247, 246, 226, 0.5);
        }
      `}</style>
    </div>
  );
}
