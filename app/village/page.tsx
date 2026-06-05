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
  bubbleText?: string;
  rewardFloating?: string;
}

interface Building {
  name: string;
  x: number;
  y: number;
  color: string;
  glow: string;
  icon: string;
}

export default function QuantVillagePage() {
  const [bots, setBots] = useState<BotCharacter[]>([
    {
      id: "btc",
      name: "BTC Agent (7호기)",
      color: "#F7931A",
      symbol: "₿",
      x: 120,
      y: 120,
      targetX: 120,
      targetY: 120,
      state: "idle",
    },
    {
      id: "eth",
      name: "ETH Agent (3호기)",
      color: "#627EEA",
      symbol: "Ξ",
      x: 680,
      y: 320,
      targetX: 680,
      targetY: 320,
      state: "idle",
    },
    {
      id: "sol",
      name: "SOL Agent (9호기)",
      color: "#14F195",
      symbol: "◎",
      x: 650,
      y: 120,
      targetX: 650,
      targetY: 120,
      state: "idle",
    },
  ]);

  const [marketStatus, setMarketStatus] = useState<"NORMAL" | "ALERT">("NORMAL");
  const [syncProgress, setSyncProgress] = useState<number>(0); // 0 to 100
  const [syncingState, setSyncingState] = useState<"IDLE" | "WALKING" | "SYNCING" | "COMPLETE">("IDLE");
  const [riskMode, setRiskMode] = useState<string>("Normal (halt_threshold = 0.50)");
  const [logs, setLogs] = useState<string[]>([
    "[System] No Slip 에이전트 연합 타운십 통신망 가동되었습니다.",
    "[Aggregator] 중앙 클러스터 대기 중. 학습 세션이 활성화되었습니다.",
  ]);

  const buildings: Record<string, Building> = {
    binance: { name: "Binance Outpost", x: 150, y: 150, color: "#F0B90B", glow: "rgba(240, 185, 11, 0.4)", icon: "📈" },
    upbit: { name: "Upbit Exchange Hub", x: 650, y: 300, color: "#0062DF", glow: "rgba(0, 98, 223, 0.4)", icon: "🇰🇷" },
    central: { name: "Town Hall (Aggregator)", x: 400, y: 220, color: "#A855F7", glow: "rgba(168, 85, 247, 0.4)", icon: "🧠" },
    oracle: { name: "Gemini Oracle Temple", x: 650, y: 120, color: "#EC4899", glow: "rgba(236, 72, 153, 0.4)", icon: "🔮" },
  };

  const logsEndRef = useRef<HTMLDivElement>(null);

  // Scroll logs to bottom
  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  // Main animation / state tick loop (runs every 60ms)
  useEffect(() => {
    const interval = setInterval(() => {
      setBots((prevBots) =>
        prevBots.map((bot) => {
          // Determine speed based on state
          const speed = bot.state === "warning" ? 12 : bot.state === "syncing" ? 8 : 4;
          const dx = bot.targetX - bot.x;
          const dy = bot.targetY - bot.y;
          const dist = Math.sqrt(dx * dx + dy * dy);

          let nextX = bot.x;
          let nextY = bot.y;

          if (dist > 5) {
            nextX += (dx / dist) * speed;
            nextY += (dy / dist) * speed;
          } else {
            // Arrived at target
            nextX = bot.targetX;
            nextY = bot.targetY;
          }

          // Random idle wandering if not actively walking to a building
          let nextTargetX = bot.targetX;
          let nextTargetY = bot.targetY;
          let newBubble = bot.bubbleText;

          if (bot.state === "idle" && dist <= 5 && Math.random() < 0.04) {
            // Wander near local area
            const range = 60;
            const originX = bot.id === "btc" ? 150 : bot.id === "eth" ? 650 : 650;
            const originY = bot.id === "btc" ? 150 : bot.id === "eth" ? 300 : 120;
            nextTargetX = originX + (Math.random() - 0.5) * range;
            nextTargetY = originY + (Math.random() - 0.5) * range;
          }

          return {
            ...bot,
            x: nextX,
            y: nextY,
            targetX: nextTargetX,
            targetY: nextTargetY,
            bubbleText: newBubble,
          };
        })
      );
    }, 60);

    return () => clearInterval(interval);
  }, []);

  // Sync animation handler
  useEffect(() => {
    if (syncingState === "WALKING") {
      // Guide all bots to the central Town Hall
      setBots((prev) =>
        prev.map((bot) => ({
          ...bot,
          state: "syncing",
          targetX: buildings.central.x + (bot.id === "btc" ? -35 : bot.id === "eth" ? 35 : 0),
          targetY: buildings.central.y + (bot.id === "sol" ? 35 : 15),
          bubbleText: "💼 Q-테이블 지참!",
        }))
      );
      
      // Check if they arrived
      const checkArrival = setInterval(() => {
        setBots((currentBots) => {
          const allArrived = currentBots.every((bot) => {
            const dx = bot.x - bot.targetX;
            const dy = bot.y - bot.targetY;
            return Math.sqrt(dx*dx + dy*dy) <= 10;
          });
          
          if (allArrived) {
            clearInterval(checkArrival);
            setSyncingState("SYNCING");
            setLogs((l) => [...l, "[System] 모든 에이전트들이 타운홀에 도착했습니다. FedAvg 연산 개시."]);
          }
          return currentBots;
        });
      }, 500);
      
      return () => clearInterval(checkArrival);
    } else if (syncingState === "SYNCING") {
      // Simulating Q-value upload / aggregation progress
      const progressTimer = setInterval(() => {
        setSyncProgress((p) => {
          if (p >= 100) {
            clearInterval(progressTimer);
            setSyncingState("COMPLETE");
            return 100;
          }
          return p + 10;
        });
      }, 250);
      return () => clearInterval(progressTimer);
    } else if (syncingState === "COMPLETE") {
      // Trigger floating reward values, print dialogue logs, and return to idle
      setLogs((l) => [
        ...l,
        "[Client Bot] 야, 대가리 서버! Q-테이블 가중치 병합 끝났냐?",
        "[Aggregator] 완료했다. 다른 피어 데이터 취합해서 FedAvg 갱신 완료! 로컬 수치 주입한다.",
        "[Client Bot] 뇌 대리 업데이트 완료! L_L_L_B 상태 Action 2(Aggr) 가중치 0.3024 꿀통 주입 확인!",
        "[System] 연합 학습 가중치 동기화가 성공적으로 종결되었습니다."
      ]);

      setBots((prev) =>
        prev.map((bot) => ({
          ...bot,
          state: "idle",
          bubbleText: "⚡ 학습완료!",
          rewardFloating: bot.id === "btc" ? "+0.3024 Q" : bot.id === "eth" ? "-0.0003 Q" : "+0.0154 Q",
          // Return to original locations
          targetX: bot.id === "btc" ? buildings.binance.x : buildings.upbit.x,
          targetY: bot.id === "btc" ? buildings.binance.y : bot.id === "eth" ? buildings.upbit.y : buildings.oracle.y,
        }))
      );

      // Clear floating rewards and bubbles after 3 seconds
      setTimeout(() => {
        setBots((prev) =>
          prev.map((bot) => ({
            ...bot,
            bubbleText: undefined,
            rewardFloating: undefined,
          }))
        );
        setSyncProgress(0);
        setSyncingState("IDLE");
      }, 3000);
    }
  }, [syncingState]);

  // Market Downtrend handler
  const triggerDowntrend = () => {
    if (marketStatus === "NORMAL") {
      setMarketStatus("ALERT");
      setRiskMode("Conservative (halt_threshold = 0.45)");
      setLogs((l) => [
        ...l,
        "🚨 [ALERT] 거시 지표 XLU/XLRE 매도 급증! 코인 단기 하락 확률 급상승!",
        "[Client Bot] 악! ETH 하락 확률 74.1%! BTC 56% 돌파! 리스크 모드 비상 격상한다!",
      ]);

      // All bots alert and run to safety
      setBots((prev) =>
        prev.map((bot) => ({
          ...bot,
          state: "warning",
          bubbleText: bot.id === "eth" ? "🚨 ETH 74.1% 대피!" : "⚠️ MLP 하락 차단!",
          targetX: buildings.central.x + (bot.id === "btc" ? -40 : bot.id === "eth" ? 40 : 0),
          targetY: buildings.central.y + (bot.id === "sol" ? 40 : -10),
        }))
      );
    } else {
      setMarketStatus("NORMAL");
      setRiskMode("Normal (halt_threshold = 0.50)");
      setLogs((l) => [
        ...l,
        "🟢 [NORMAL] 거시 지표 반등 성공. 가상자산 MLP 하락 압력 해제.",
        "[Client Bot] 후우, 살았다. 하락 확률 30% 선 복귀. 다시 필드로 복귀합니다.",
      ]);

      setBots((prev) =>
        prev.map((bot) => ({
          ...bot,
          state: "idle",
          bubbleText: "🟢 복귀 완료",
          targetX: bot.id === "btc" ? buildings.binance.x : buildings.upbit.x,
          targetY: bot.id === "btc" ? buildings.binance.y : bot.id === "eth" ? buildings.upbit.y : buildings.oracle.y,
        }))
      );

      setTimeout(() => {
        setBots((prev) => prev.map((b) => ({ ...b, bubbleText: undefined })));
      }, 2000);
    }
  };

  // Run manual sync
  const startFederatedSync = () => {
    if (syncingState !== "IDLE") return;
    setSyncingState("WALKING");
    setLogs((l) => [...l, "[System] 로컬 Q-table 동기화 세션 시작. 타운홀로 에이전트 소집 중..."]);
  };

  return (
    <div className="min-h-screen bg-[#090D1A] text-slate-100 font-sans p-6 selection:bg-purple-500 selection:text-white">
      {/* Header */}
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center border-b border-slate-800 pb-4 mb-6">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight bg-gradient-to-r from-purple-400 via-pink-400 to-indigo-400 bg-clip-text text-transparent flex items-center gap-3">
            👥 No Slip Federated Quant Village
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            동의한 유저들의 봇이 가상의 마을에서 협력하여 최적의 전략 파라미터를 도출하는 자율형 샌드박스
          </p>
        </div>
        <div className="mt-4 md:mt-0 flex gap-3">
          <div className="bg-slate-900 border border-slate-800 rounded-lg px-4 py-2 text-xs flex flex-col justify-center">
            <span className="text-slate-400">네트워크 상태</span>
            <span className="font-semibold text-emerald-400 flex items-center gap-1.5 mt-0.5">
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping"></span>
              Aggregator Online
            </span>
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-lg px-4 py-2 text-xs flex flex-col justify-center">
            <span className="text-slate-400">동기화 합산 모드</span>
            <span className="font-semibold text-purple-400 mt-0.5">FedAvg v1.1 (Privacy Shield)</span>
          </div>
        </div>
      </div>

      {/* Main Grid Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        {/* Game Township Arena */}
        <div className="lg:col-span-2 bg-slate-950 border border-slate-800 rounded-2xl overflow-hidden shadow-2xl relative flex flex-col">
          
          {/* Dashboard Header Bar */}
          <div className="bg-slate-900 border-b border-slate-800 px-4 py-3 flex justify-between items-center z-10">
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono text-purple-400 font-bold">&gt;_ SIMULATION_SCREEN</span>
              {marketStatus === "ALERT" && (
                <span className="bg-red-500/20 text-red-400 border border-red-500/40 text-[10px] px-2 py-0.5 rounded font-bold uppercase animate-pulse">
                  🚨 Red Alert: Circuit Breaker
                </span>
              )}
            </div>
            <div className="text-xs font-mono text-slate-400">
              Risk parameters: <code className="text-yellow-400">{riskMode}</code>
            </div>
          </div>

          {/* Canvas Map Area */}
          <div 
            className="h-[450px] relative w-full overflow-hidden"
            style={{
              backgroundImage: "radial-gradient(#1e293b 1px, transparent 1px)",
              backgroundSize: "24px 24px"
            }}
          >
            {/* Grid overlay */}
            <div className="absolute inset-0 bg-gradient-to-t from-slate-950 via-transparent to-transparent opacity-80 pointer-events-none"></div>

            {/* Render Buildings */}
            {Object.entries(buildings).map(([key, b]) => {
              const isActive = 
                (key === "central" && syncingState === "SYNCING") ||
                (key === "binance" && marketStatus === "NORMAL") ||
                (key === "upbit" && marketStatus === "NORMAL");

              return (
                <div
                  key={key}
                  className="absolute transform -translate-x-1/2 -translate-y-1/2 flex flex-col items-center group transition-all duration-300"
                  style={{ left: b.x, top: b.y }}
                >
                  {/* Glowing aura under building */}
                  <div
                    className="absolute w-28 h-28 rounded-full blur-2xl opacity-70 -z-10 transition-all duration-500"
                    style={{
                      backgroundColor: b.color,
                      transform: isActive ? "scale(1.2)" : "scale(0.8)"
                    }}
                  ></div>

                  {/* Building Visual Wrapper */}
                  <div 
                    className="w-16 h-16 rounded-2xl flex items-center justify-center text-3xl border shadow-lg cursor-pointer transition-transform duration-200 hover:scale-110"
                    style={{
                      backgroundColor: "#0d1329",
                      borderColor: b.color,
                      boxShadow: isActive ? `0 0 20px ${b.color}` : "none"
                    }}
                  >
                    <span>{b.icon}</span>
                  </div>
                  <span className="text-[10px] font-mono text-slate-400 mt-1.5 font-bold tracking-tight bg-slate-900/95 border border-slate-800 px-2 py-0.5 rounded">
                    {b.name}
                  </span>
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
                    x2={buildings.central.x}
                    y2={buildings.central.y}
                    stroke="#A855F7"
                    strokeWidth="2"
                    strokeDasharray="4 4"
                    className="animate-[dash_2s_linear_infinite]"
                  />
                ))}
              </svg>
            )}

            {/* Render Bots / Agents */}
            {bots.map((bot) => (
              <div
                key={bot.id}
                className="absolute transform -translate-x-1/2 -translate-y-1/2 transition-all duration-100 z-10 flex flex-col items-center"
                style={{ left: bot.x, top: bot.y }}
              >
                {/* Speech Bubble */}
                {bot.bubbleText && (
                  <div className="absolute bottom-11 bg-slate-900 border border-slate-700 text-[9px] px-2 py-1 rounded-md shadow-2xl font-bold whitespace-nowrap animate-bounce z-20">
                    {bot.bubbleText}
                    <div className="absolute top-full left-1/2 transform -translate-x-1/2 border-4 border-transparent border-t-slate-900"></div>
                  </div>
                )}

                {/* Floating Q-value feedback */}
                {bot.rewardFloating && (
                  <div className="absolute -top-10 text-xs font-mono font-bold text-yellow-400 animate-[fadeOutUp_2s_ease-out_forwards] z-20">
                    {bot.rewardFloating}
                  </div>
                )}

                {/* Robot Circle Token */}
                <div
                  className="w-10 h-10 rounded-full flex items-center justify-center border-2 shadow-2xl cursor-pointer hover:scale-115 transition-transform"
                  style={{
                    backgroundColor: "#0d1329",
                    borderColor: bot.color,
                    boxShadow: `0 0 10px ${bot.color}80`
                  }}
                >
                  <span className="text-lg font-bold" style={{ color: bot.color }}>
                    {bot.symbol}
                  </span>
                </div>

                {/* Agent Name Tag */}
                <span className="text-[9px] font-mono font-bold text-slate-300 mt-1 bg-slate-900/90 px-1 py-0.5 rounded shadow">
                  {bot.id.toUpperCase()}
                </span>
              </div>
            ))}
          </div>

          {/* Sync Progress Bar */}
          {syncingState === "SYNCING" && (
            <div className="bg-slate-900 border-t border-slate-800 p-4">
              <div className="flex justify-between items-center text-xs mb-1 font-mono text-purple-400">
                <span>Aggregating model parameters (FedAvg calculation)...</span>
                <span>{syncProgress}%</span>
              </div>
              <div className="w-full bg-slate-950 h-2.5 rounded-full overflow-hidden border border-slate-800">
                <div 
                  className="bg-gradient-to-r from-purple-500 via-pink-500 to-indigo-500 h-full rounded-full transition-all duration-300"
                  style={{ width: `${syncProgress}%` }}
                ></div>
              </div>
            </div>
          )}

          {/* Control Panel Footer */}
          <div className="bg-slate-900 border-t border-slate-800 p-5 flex flex-wrap gap-4 justify-between items-center">
            <div className="flex gap-3">
              <button
                onClick={startFederatedSync}
                disabled={syncingState !== "IDLE"}
                className={`px-4 py-2 rounded-lg text-xs font-extrabold flex items-center gap-2 border shadow-lg transition-all duration-200 ${
                  syncingState !== "IDLE"
                    ? "bg-slate-800 border-slate-700 text-slate-500 cursor-not-allowed"
                    : "bg-purple-600 hover:bg-purple-500 border-purple-500 hover:shadow-purple-500/20 text-white cursor-pointer"
                }`}
              >
                👥 연합 매개변수 동기화 (FedAvg)
              </button>
              <button
                onClick={triggerDowntrend}
                className={`px-4 py-2 rounded-lg text-xs font-extrabold flex items-center gap-2 border shadow-lg transition-all duration-200 cursor-pointer ${
                  marketStatus === "ALERT"
                    ? "bg-emerald-600 hover:bg-emerald-500 border-emerald-500 text-white"
                    : "bg-red-600 hover:bg-red-500 border-red-500 text-white animate-pulse"
                }`}
              >
                {marketStatus === "ALERT" ? "🟢 시장 안정화 해제" : "🚨 시장 강제 하락 트리거"}
              </button>
            </div>
            <div className="text-[10px] text-slate-400 font-mono">
              * 동기화 시 로컬 학습 이력(`Q-value`)이 암호화 전송됩니다.
            </div>
          </div>

        </div>

        {/* Eavesdropping CLI Console Terminal */}
        <div className="bg-[#050814] border border-slate-800 rounded-2xl p-5 shadow-2xl flex flex-col h-[580px]">
          {/* Terminal Title Bar */}
          <div className="flex items-center justify-between border-b border-slate-850 pb-3 mb-4">
            <div className="flex items-center gap-2">
              <span className="w-3 h-3 rounded-full bg-red-500"></span>
              <span className="w-3 h-3 rounded-full bg-yellow-500"></span>
              <span className="w-3 h-3 rounded-full bg-green-500"></span>
              <span className="text-xs font-mono text-slate-400 font-bold ml-2">AGENT_COMMUNICATION_TAP.LOG</span>
            </div>
            <span className="text-[10px] font-mono text-yellow-400 bg-yellow-500/10 px-2 py-0.5 rounded border border-yellow-500/20">
              📡 감청 중 (TAP ACTIVE)
            </span>
          </div>

          {/* CRT Terminal Screen */}
          <div className="flex-1 overflow-y-auto font-mono text-xs p-4 rounded-xl bg-black/40 border border-slate-900/60 leading-relaxed space-y-3 shadow-inner custom-scrollbar relative">
            <div className="absolute inset-0 bg-gradient-to-b from-transparent via-transparent to-slate-950 pointer-events-none opacity-40"></div>
            
            {logs.map((log, idx) => {
              let colorClass = "text-slate-300";
              if (log.includes("[System]")) colorClass = "text-indigo-400 font-bold";
              else if (log.includes("[Aggregator]")) colorClass = "text-blue-400";
              else if (log.includes("[Client Bot]")) colorClass = "text-emerald-400";
              else if (log.includes("[ALERT]")) colorClass = "text-red-400 font-extrabold animate-pulse";
              else if (log.includes("[NORMAL]")) colorClass = "text-emerald-400 font-bold";

              return (
                <div key={idx} className={`${colorClass} whitespace-pre-wrap`}>
                  {log}
                </div>
              );
            })}
            <div ref={logsEndRef} />
          </div>

          {/* Mini-stats panel */}
          <div className="mt-4 bg-slate-900/60 border border-slate-850 p-4 rounded-xl">
            <h3 className="text-xs font-bold font-mono text-slate-400 mb-2.5 flex items-center gap-1.5">
              <span>📊</span> Local Federated Learning Cache
            </h3>
            <div className="space-y-2 text-[10px] font-mono text-slate-400">
              <div className="flex justify-between">
                <span>학습 데이터 테이블:</span>
                <span className="text-slate-200">federated_q_table (Active)</span>
              </div>
              <div className="flex justify-between">
                <span>최근 기록된 보상 평균 (Avg Reward):</span>
                <span className="text-yellow-400 font-bold">+0.0100 (PROFIT)</span>
              </div>
              <div className="flex justify-between">
                <span>결합 계수 (Aggregation eta):</span>
                <span className="text-purple-400">0.30 (Standard FedAvg)</span>
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
          width: 6px;
        }
        .custom-scrollbar::-webkit-scrollbar-track {
          background: rgba(0, 0, 0, 0.2);
        }
        .custom-scrollbar::-webkit-scrollbar-thumb {
          background: rgba(148, 163, 184, 0.3);
          border-radius: 4px;
        }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover {
          background: rgba(148, 163, 184, 0.5);
        }
      `}</style>
    </div>
  );
}
