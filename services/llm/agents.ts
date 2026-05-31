import { createHash } from "node:crypto";

const sleep = (milliseconds: number) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

const GEMINI_CACHE_TTL_MS = Math.max(
  30_000,
  Number(process.env.GEMINI_CACHE_TTL_MS || 5 * 60 * 1000)
);
const GEMINI_MAX_CACHE_ENTRIES = Math.max(
  32,
  Number(process.env.GEMINI_CACHE_MAX_ENTRIES || 256)
);
const GEMINI_MAX_CONCURRENCY = Math.max(
  1,
  Number(process.env.GEMINI_MAX_CONCURRENCY || 3)
);

const geminiResponseCache = new Map<
  string,
  {
    expiresAt: number;
    value: string;
  }
>();
const geminiInFlightRequests = new Map<string, Promise<string>>();
const geminiQueue: Array<() => void> = [];
let geminiActiveRequests = 0;

const getGeminiCacheKey = (modelName: string, prompt: string) =>
  createHash("sha1")
    .update(modelName)
    .update("\u0000")
    .update(prompt)
    .digest("hex");

const pruneGeminiCache = () => {
  const now = Date.now();

  for (const [key, entry] of geminiResponseCache.entries()) {
    if (entry.expiresAt <= now) {
      geminiResponseCache.delete(key);
    }
  }

  while (geminiResponseCache.size > GEMINI_MAX_CACHE_ENTRIES) {
    const oldestKey = geminiResponseCache.keys().next().value;
    if (!oldestKey) {
      break;
    }
    geminiResponseCache.delete(oldestKey);
  }
};

const acquireGeminiSlot = async () => {
  if (geminiActiveRequests < GEMINI_MAX_CONCURRENCY) {
    geminiActiveRequests += 1;
    return;
  }

  await new Promise<void>((resolve) => {
    geminiQueue.push(() => {
      geminiActiveRequests += 1;
      resolve();
    });
  });
};

const releaseGeminiSlot = () => {
  geminiActiveRequests = Math.max(0, geminiActiveRequests - 1);
  const next = geminiQueue.shift();
  next?.();
};

const withGeminiSlot = async <T>(runner: () => Promise<T>) => {
  await acquireGeminiSlot();
  try {
    return await runner();
  } finally {
    releaseGeminiSlot();
  }
};

const buildGeminiFallback = (prompt: string) => {
  if (prompt.includes("Buy Agent")) {
    return "매수 관점은 지금 응답이 지연되어 잠시 비워두었습니다.";
  }
  if (prompt.includes("Wait Agent")) {
    return "대기 관점은 지금 응답이 지연되어 잠시 비워두었습니다.";
  }
  if (prompt.includes("Route Guidance Agent")) {
    return "경로 가이드는 지금 응답이 지연되어 잠시 단순 경로만 유지합니다.";
  }
  if (prompt.includes("Stock Guidance Agent")) {
    return "주식 가이드는 지금 응답이 지연되어 잠시 핵심 수치만 기준으로 유지합니다.";
  }
  if (prompt.includes("Dynamic Portfolio View")) {
    return "동적 포트폴리오 의견은 지금 응답이 지연되어 잠시 비워두었습니다.";
  }
  if (prompt.includes("Macbook")) {
    return "Macbook 의견은 지금 응답이 지연되어 직전 학습 상태만 유지합니다.";
  }
  if (prompt.includes("Buffett") || prompt.includes("Druckenmiller") || prompt.includes("Lynch") || prompt.includes("Dalio")) {
    return "투자자 관점 의견은 지금 응답이 지연되어 잠시 비워두었습니다.";
  }
  return "모델 응답이 지연되어 이 의견 블록은 잠시 비워두었습니다.";
};

export const callGemini = async (prompt: string) => {
  const apiKey = process.env.GEMINI_API_KEY;
  const modelName = process.env.GEMINI_MODEL || "gemini-2.5-flash";
  const timeoutMs = Math.max(8_000, Number(process.env.GEMINI_TIMEOUT_MS || 28_000));
  const retryCount = Math.max(1, Number(process.env.GEMINI_RETRY_COUNT || 2));

  if (!apiKey) {
    return "Gemini API key is not configured.";
  }

  const url = `https://generativelanguage.googleapis.com/v1beta/models/${modelName}:generateContent?key=${apiKey}`;
  const cacheKey = getGeminiCacheKey(modelName, prompt);
  const cachedEntry = geminiResponseCache.get(cacheKey);

  if (cachedEntry && cachedEntry.expiresAt > Date.now()) {
    return cachedEntry.value;
  }
  if (cachedEntry) {
    geminiResponseCache.delete(cacheKey);
  }

  const inFlight = geminiInFlightRequests.get(cacheKey);
  if (inFlight) {
    return await inFlight;
  }

  const requestPromise = withGeminiSlot(async () => {
    for (let attempt = 0; attempt < retryCount; attempt += 1) {
      const attemptTimeoutMs =
        attempt === 0 ? timeoutMs : Math.round(timeoutMs * 1.45);

      try {
        const res = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: AbortSignal.timeout(attemptTimeoutMs),
          body: JSON.stringify({
            contents: [{ parts: [{ text: prompt }] }],
          }),
        });

        const data = await res.json();

        if (data.error) {
          console.error("❌ Gemini Error:", data.error.message);
          if (data.error.code === 429 && attempt + 1 < retryCount) {
            await sleep(900 * (attempt + 1));
            continue;
          }
          if (data.error.code === 429) {
            return "현재 요청이 많아 의견 생성이 잠시 지연되고 있습니다.";
          }
          return buildGeminiFallback(prompt);
        }

        const text =
          data?.candidates?.[0]?.content?.parts?.[0]?.text?.trim() ||
          "응답이 비어있습니다.";

        geminiResponseCache.set(cacheKey, {
          expiresAt: Date.now() + GEMINI_CACHE_TTL_MS,
          value: text,
        });
        pruneGeminiCache();

        return text;
      } catch (error: any) {
        const isTimeout =
          error?.name === "TimeoutError" ||
          error?.name === "AbortError" ||
          String(error?.message || "").toLowerCase().includes("timed out");
        if ((isTimeout || error?.message) && attempt + 1 < retryCount) {
          await sleep(700 * (attempt + 1));
          continue;
        }
        if (isTimeout) {
          return buildGeminiFallback(prompt);
        }
        return buildGeminiFallback(prompt);
      }
    }

    return buildGeminiFallback(prompt);
  });

  geminiInFlightRequests.set(cacheKey, requestPromise);

  try {
    return await requestPromise;
  } finally {
    geminiInFlightRequests.delete(cacheKey);
  }
};

export const runBuyAgent = async (data: string) => {
  const prompt = `Context: ${data}\n\nTask: As a Buy Agent, explain 2 reasons to BUY this token now. Keep it under 2 sentences.`;
  return await callGemini(prompt);
};

export const runWaitAgent = async (data: string) => {
  const prompt = `Context: ${data}\n\nTask: As a Wait Agent, explain 2 reasons to WAIT for a better entry point. Keep it under 2 sentences.`;
  return await callGemini(prompt);
};

const extractContextValue = (data: string, label: string) => {
  const match = data.match(new RegExp(`${label}:\\s*(.+)`, "i"));
  return match?.[1]?.trim() || "";
};

const condenseRoutePath = (rawPath: string) => {
  const segments = rawPath
    .split("|")
    .map((segment) => segment.trim())
    .filter(Boolean);

  const tokens: string[] = [];

  segments.forEach((segment) => {
    const parts = segment
      .split("->")
      .map((part) => part.trim())
      .filter(Boolean);

    parts.forEach((part, index) => {
      if (tokens.length === 0 || index === 0 && tokens[tokens.length - 1] !== part) {
        tokens.push(part);
      } else if (index > 0 && tokens[tokens.length - 1] !== part) {
        tokens.push(part);
      }
    });
  });

  return tokens.join("→");
};

export const runRouteGuidanceAgent = async (data: string) => {
  const marketMode = extractContextValue(data, "Market Mode").toLowerCase();
  const inputSymbol = extractContextValue(data, "Input Symbol") || "A";
  const outputSymbol = extractContextValue(data, "Output Symbol") || "B";
  const tokenSymbol = extractContextValue(data, "Token Symbol") || outputSymbol || inputSymbol || "UNKNOWN";
  const bestRoutePath = extractContextValue(data, "Best Route Path");
  const condensedPath = condenseRoutePath(bestRoutePath);
  const routeLegs = Number(extractContextValue(data, "Route Legs") || 0);
  const hasWaypoint = condensedPath.split("→").filter(Boolean).length > 2 || bestRoutePath.includes("|");
  const uncertaintyRatio = extractContextValue(data, "Uncertainty Ratio") || "unknown";
  const maxUpsidePct = extractContextValue(data, "Max Upside Pct") || "unknown";
  const turnoverPotential = extractContextValue(data, "Turnover Potential") || "unknown";
  const seasonalityHeadline = extractContextValue(data, "Seasonality Headline") || "unknown";
  const darkHorseScore = extractContextValue(data, "Selected Dark Horse Score") || "unknown";
  const darkHorseLabel = extractContextValue(data, "Selected Dark Horse Label") || "unknown";

  if (marketMode === "sp500") {
    if (!process.env.GEMINI_API_KEY) {
      return `모델의 Uncertainty와 Upside, Turnover 가능성을 봤을때 현재 최고의 주식은 ${tokenSymbol}이며, 불확실성은 ${uncertaintyRatio}, 기대 upside는 ${maxUpsidePct}, turnover 가능성은 ${turnoverPotential} 수준이고 seasonality 관점에서는 ${seasonalityHeadline} 이며 symmetry 기준으로는 ${darkHorseLabel} (${darkHorseScore}) 입니다.`;
    }

    const prompt = `Context: ${data}

Task: You are a Stock Guidance Agent.
Write exactly one sentence in Korean.

Output rules:
- The sentence must start with "모델의 Uncertainty와 Upside, Turnover 가능성을 봤을때 현재 최고의 주식은".
- Name the concrete stock from the context.
- Briefly justify it using uncertainty, upside, and turnover potential.
- Use seasonality context briefly if it exists.
- If symmetry-based dark-horse context exists, mention whether this stock looks like an underfollowed dark horse or a consensus leader.
- Keep it to exactly one sentence.
`;
    return await callGemini(prompt);
  }

  if (!process.env.GEMINI_API_KEY) {
    if (hasWaypoint && condensedPath) {
      return `현재는 ${inputSymbol}을 ${outputSymbol}로 바꿀 때 직행보다 ${condensedPath} 경로가 수수료와 체결 효율 면에서 더 유리합니다.`;
    }

    if (routeLegs > 1 && condensedPath) {
      return `현재는 ${inputSymbol}에서 ${outputSymbol}로 갈 때 ${condensedPath}처럼 분산된 경로가 직행보다 비용 방어에 유리합니다.`;
    }

    return `현재는 ${inputSymbol}을 ${outputSymbol}로 바꿀 때 직행 경로가 가장 단순하고 비용도 안정적입니다.`;
  }

  const prompt = `Context: ${data}

Task: You are a Route Guidance Agent.
Write exactly one line in Korean that explains whether converting the input token directly into the output token is better, or whether routing through an intermediate token is cheaper right now.

Output rules:
- Keep it to exactly one sentence.
- Mention the concrete route if an intermediate path exists.
- Compare direct swap vs routed swap in plain Korean.
- Focus on fees and execution efficiency, not prediction timing.
`;
  return await callGemini(prompt);
};

export const runDynamicPortfolioViewAgent = async (data: string) => {
  const marketMode = extractContextValue(data, "Market Mode").toLowerCase();
  const holdingsCount = extractContextValue(data, "Portfolio Holdings Count") || "unknown";
  const weightedUpsidePct = extractContextValue(data, "Portfolio Weighted Upside Pct") || "unknown";
  const weightedUncertaintyPct =
    extractContextValue(data, "Portfolio Weighted Uncertainty Pct") || "unknown";
  const weightedVolatilityPct =
    extractContextValue(data, "Portfolio Weighted Volatility Pct") || "unknown";
  const weightedDrawdownLingerDays =
    extractContextValue(data, "Portfolio Weighted Drawdown Linger Days") || "unknown";
  const weightedPersistencePct =
    extractContextValue(data, "Portfolio Weighted Persistence Pct") || "unknown";
  const weightedRegimeRiskPct =
    extractContextValue(data, "Portfolio Weighted Regime Risk Pct") || "unknown";
  const portfolioMethodologyObjective =
    extractContextValue(data, "Portfolio Methodology Objective") || "unknown";
  const portfolioAllocationMethodology =
    extractContextValue(data, "Portfolio Allocation Methodology") || "unknown";
  const portfolioGeometryMethod =
    extractContextValue(data, "Portfolio Geometry Method") || "unknown";
  const portfolioGeometryRiskProfile =
    extractContextValue(data, "Portfolio Geometry Risk Profile") || "unknown";
  const portfolioGeometryAlignmentScore =
    extractContextValue(data, "Portfolio Geometry Alignment Score") || "unknown";
  const portfolioGeometryDistance =
    extractContextValue(data, "Portfolio Geometry Distance") || "unknown";
  const portfolioManifoldMethod =
    extractContextValue(data, "Portfolio Manifold Method") || "unknown";
  const portfolioManifoldContinuityScore =
    extractContextValue(data, "Portfolio Manifold Continuity Score") || "unknown";
  const portfolioManifoldTargetDistance =
    extractContextValue(data, "Portfolio Manifold Target Distance") || "unknown";
  const portfolioManifoldBridgeMode =
    extractContextValue(data, "Portfolio Manifold Bridge Mode") || "unknown";
  const championPortfolioLabel =
    extractContextValue(data, "Champion Portfolio Label") || "unknown";
  const championPortfolioProfile =
    extractContextValue(data, "Champion Portfolio Profile") || "unknown";
  const championPortfolioScore =
    extractContextValue(data, "Champion Portfolio Score") || "unknown";
  const championPortfolioRationale =
    extractContextValue(data, "Champion Portfolio Rationale") || "unknown";
  const weightedDarkHorseScore =
    extractContextValue(data, "Portfolio Weighted Dark Horse Score") || "unknown";
  const weightedSmallCapTailScore =
    extractContextValue(data, "Portfolio Weighted Small Cap Tail Score") || "unknown";
  const weightedHeavyTailScore =
    extractContextValue(data, "Portfolio Weighted Heavy Tail Score") || "unknown";
  const weightedHeavyTailPremium =
    extractContextValue(data, "Portfolio Weighted Heavy Tail Premium") || "unknown";
  const weightedLongTailScore =
    extractContextValue(data, "Portfolio Weighted Long Tail Score") || "unknown";
  const weightedLeftTailRiskScore =
    extractContextValue(data, "Portfolio Weighted Left Tail Risk Score") || "unknown";
  const redditSmallCapHeat =
    extractContextValue(data, "Portfolio Reddit Small Cap Heat Score") || "unknown";
  const redditSmallCapRegime =
    extractContextValue(data, "Portfolio Reddit Small Cap Regime") || "unknown";
  const redditSmallCapTopTickers =
    extractContextValue(data, "Portfolio Reddit Small Cap Top Tickers") || "unknown";
  const koreanSurgeHeat =
    extractContextValue(data, "Portfolio Korean Surge Heat Score") || "unknown";
  const koreanSurgeRegime =
    extractContextValue(data, "Portfolio Korean Surge Regime") || "unknown";
  const koreanSurgeTopTickers =
    extractContextValue(data, "Portfolio Korean Surge Top Tickers") || "unknown";
  const selectedDarkHorseScore =
    extractContextValue(data, "Selected Dark Horse Score") || "unknown";
  const selectedDarkHorseLabel =
    extractContextValue(data, "Selected Dark Horse Label") || "unknown";
  const portfolioWeightedBeliefScore =
    extractContextValue(data, "Portfolio Weighted Belief Score") || "unknown";
  const portfolioWeightedBeliefAgreement =
    extractContextValue(data, "Portfolio Weighted Belief Agreement") || "unknown";
  const portfolioWeightedBeliefPolarization =
    extractContextValue(data, "Portfolio Weighted Belief Polarization") || "unknown";
  const selectedBeliefScore =
    extractContextValue(data, "Selected Belief Score") || "unknown";
  const selectedBeliefLabel =
    extractContextValue(data, "Selected Belief Label") || "unknown";
  const selectedPrivateSignalPct =
    extractContextValue(data, "Selected Private Signal Pct") || "unknown";
  const selectedCrowdBeliefPct =
    extractContextValue(data, "Selected Crowd Belief Pct") || "unknown";
  const selectedBeliefAgreement =
    extractContextValue(data, "Selected Belief Agreement") || "unknown";
  const selectedBeliefConsensusAction =
    extractContextValue(data, "Selected Belief Consensus Action") || "unknown";
  const portfolioSleeves = extractContextValue(data, "Portfolio Sleeves") || "unknown";
  const portfolioSectorMix = extractContextValue(data, "Portfolio Sector Mix") || "unknown";
  const portfolioTopHoldings = extractContextValue(data, "Portfolio Top Holdings") || "unknown";
  const macroSummary = extractContextValue(data, "Macro Summary") || "거시 환경 확인 필요";

  if (marketMode !== "sp500" && marketMode !== "stock") {
    return "Dynamic portfolio view: 주식 모드에서만 활성화됩니다.";
  }

  if (!process.env.GEMINI_API_KEY) {
    return `동적 포트폴리오 뷰: 현재 포트폴리오는 ${championPortfolioLabel}이 ${championPortfolioProfile} 프로필로 선택된 상태이며 score ${championPortfolioScore}, manifold 연속성 ${portfolioManifoldContinuityScore}, target 거리 ${portfolioManifoldTargetDistance}를 기준으로 ${portfolioGeometryRiskProfile} 성향의 리스크를 관리하고 있습니다. ${holdingsCount}개 종목 기준으로 upside ${weightedUpsidePct}, uncertainty ${weightedUncertaintyPct}, belief ${portfolioWeightedBeliefScore}, belief 합의 ${portfolioWeightedBeliefAgreement}, belief spread ${portfolioWeightedBeliefPolarization}, 변동성 ${weightedVolatilityPct}, 눌림 지속 ${weightedDrawdownLingerDays}일, dark-horse exposure ${weightedDarkHorseScore}, small-cap tail ${weightedSmallCapTailScore}, heavy-tail ${weightedHeavyTailScore}, tail premium ${weightedHeavyTailPremium}, long-tail bias ${weightedLongTailScore}, left-tail stress ${weightedLeftTailRiskScore} 수준이며 small-cap pulse는 ${redditSmallCapRegime} (${redditSmallCapHeat}), Korean surge pulse는 ${koreanSurgeRegime} (${koreanSurgeHeat})로 ${redditSmallCapTopTickers}와 ${koreanSurgeTopTickers}를 함께 주시하고 있고 선택 종목은 belief ${selectedBeliefScore} (${selectedBeliefLabel}), private ${selectedPrivateSignalPct}, crowd ${selectedCrowdBeliefPct}, agreement ${selectedBeliefAgreement}, consensus ${selectedBeliefConsensusAction}, ${selectedDarkHorseLabel} (${selectedDarkHorseScore}) 문맥에 놓여 있으며, 핵심 배분은 ${portfolioSleeves}, 섹터 축은 ${portfolioSectorMix}, 상위 보유는 ${portfolioTopHoldings} 쪽에 기울어 있습니다.`;
  }

  const prompt = `Context: ${data}

Task: You are a Dynamic Portfolio View Agent.
Write exactly one short paragraph in Korean under 2 sentences that summarizes the current stock portfolio as one integrated dynamic-analysis view.

Output rules:
- Start with "동적 포트폴리오 뷰:"
- Treat the portfolio as one live view, not a list of disconnected metrics.
- Mention the current risk posture using uncertainty, volatility, drawdown linger, persistence, and regime risk.
- Mention the portfolio-level belief posture briefly when it is present.
- Treat belief as a parallel learning signal when context includes private signal, crowd belief, agreement, or consensus.
- Mention the geometry target/alignment briefly if present.
- Mention the champion portfolio agent and temporal manifold briefly if present.
- Mention symmetry-based dark-horse context briefly when it exists.
- Mention the selected stock belief context briefly when it exists.
- Mention small-cap heavy-tail optionality briefly when it exists.
- Mention small-cap pulse-board context briefly when it exists.
- Mention Korean surge pulse context briefly when it exists.
- Mention sleeve allocation and sector mix briefly if present.
- Mention the most important holdings bias briefly if present.
- Use the macro backdrop as context, not as a standalone lecture.
- Keep it concise, practical, and readable to an investor reviewing one dashboard card.
- If a champion rationale exists, compress its meaning into plain investor language instead of quoting it.
`;
  return await callGemini(prompt);
};

export const runNextActionDateAgent = async (data: string) => {
  const suggestedDateMatch = data.match(/Suggested Send Date:\s*(.+)/i);
  const suggestedDate = suggestedDateMatch?.[1]?.trim();

  if (!process.env.GEMINI_API_KEY) {
    return suggestedDate
      ? `${suggestedDate}에 보내면 좋겠다. 현재 예측 신호와 저점 대기 시간을 기준으로 잡은 날짜다.`
      : "오늘 12시 전후에 보내면 좋겠다. 현재 입력만으로는 더 나은 시간 근거가 약하다.";
  }

  const prompt = `Context: ${data}

Task: You are a Next Action Date Agent.
Write the answer in Korean.

Output rules:
- The first sentence must use this exact shape: "YYYY년 M월 D일 HH시 전후에 보내면 좋겠다."
- Use the precomputed suggested_send_date from the context if it exists.
- Add at most one short supporting sentence explaining why that time window is appropriate.
- Keep the whole answer within 2 sentences.
`;
  return await callGemini(prompt);
};

export const runMomentumSummaryAgent = async (data: string) => {
  const firstMoment = Number(extractContextValue(data, "First Moment Pct Per Hour"));
  const secondMoment = Number(extractContextValue(data, "Second Moment Pct Per Hour2"));
  const secondMomentMagnitude = extractContextValue(data, "Second Moment Magnitude");

  if (!process.env.GEMINI_API_KEY) {
    const firstTone =
      !Number.isFinite(firstMoment) || Math.abs(firstMoment) < 0.0001
        ? "1차 모멘트는 거의 중립"
        : firstMoment > 0
          ? "1차 모멘트는 상승 쪽"
          : "1차 모멘트는 하락 쪽";
    const secondTone =
      !Number.isFinite(secondMoment) || Math.abs(secondMoment) < 0.0001
        ? "2차 모멘트도 중립"
        : secondMoment > 0
          ? "2차 모멘트는 가속"
          : "2차 모멘트는 감속";
    const secondMagnitude =
      secondMomentMagnitude && secondMomentMagnitude !== "unknown"
        ? ` 크기는 ${secondMomentMagnitude} 수준입니다`
        : "";
    return `${firstTone}이고 ${secondTone}이며${secondMagnitude} 현재 Prophet 곡선의 속도와 가속 방향을 함께 확인할 필요가 있습니다.`;
  }

  const prompt = `Context: ${data}

Task: You are a Momentum Agent.
Explain the Prophet first moment and second moment in Korean.

Output rules:
- Write exactly one sentence.
- Mention both the first moment and second moment.
- Reflect the size of the second moment if the context provides a magnitude label.
- Explain whether momentum is rising, fading, accelerating, or decelerating.
- Keep the wording practical for a trading decision.
`;
  return await callGemini(prompt);
};

export const runSeasonalitySummaryAgent = async (data: string) => {
  const headline = extractContextValue(data, "Seasonality Headline");
  const strongestComponent = extractContextValue(data, "Seasonality Strongest Component");
  const weeklySummary = extractContextValue(data, "Seasonality Weekly Summary");
  const yearlySummary = extractContextValue(data, "Seasonality Yearly Summary");
  const monthlySummary = extractContextValue(data, "Seasonality Monthly Summary");
  const quarterlySummary = extractContextValue(data, "Seasonality Quarterly Summary");

  if (!process.env.GEMINI_API_KEY) {
    if (!headline || headline === "unknown") {
      return "현재 입력만으로는 뚜렷한 seasonality 패턴을 특정하기 어렵습니다.";
    }

    const strongest =
      strongestComponent && strongestComponent !== "unknown"
        ? `${strongestComponent} 주기가 가장 두드러지고`
        : "반복 주기 신호가 보이며";
    const weekly =
      weeklySummary && weeklySummary !== "unknown" ? ` 주간 패턴은 ${weeklySummary}` : "";
    const yearly =
      yearlySummary && yearlySummary !== "unknown" ? ` 연간 패턴은 ${yearlySummary}` : "";

    return `${headline} ${strongest}${weekly}.${yearly}`.replace(/\s+\./g, ".").trim();
  }

  const prompt = `Context: ${data}

Task: You are a Seasonality Agent.
Write exactly one sentence in Korean that explains the stock's recurring seasonality structure.

Output rules:
- Mention the strongest recurring component if it exists.
- Use the weekly/yearly/monthly/quarterly summaries when available.
- Explain whether seasonality acts more like a timing tailwind or headwind right now.
- Keep it to exactly one sentence.
`;
  return await callGemini(prompt);
};

export const runTimingWindowAgent = async (data: string) => {
  const optimalBuyTimestamp = extractContextValue(data, "Optimal Buy Timestamp");
  const optimalSellTimestamp = extractContextValue(data, "Optimal Sell Timestamp");
  const riseWindow = extractContextValue(data, "Rise Window (human)");
  const dropWindow = extractContextValue(data, "Drop Window (human)");
  const drawdownLinger = extractContextValue(data, "Drawdown Linger (human)");
  const drawdownRecovery = extractContextValue(data, "Drawdown Recovery Timestamp");

  if (!process.env.GEMINI_API_KEY) {
    if (optimalBuyTimestamp || optimalSellTimestamp) {
      if (riseWindow && riseWindow !== "unknown") {
        return `${optimalBuyTimestamp || "매수 시점은 아직 불명확"} 전후가 매수 후보이고 ${optimalSellTimestamp || "매도 시점은 아직 불명확"} 전후가 매도 후보이며, 저점에서 고점까지는 대략 ${riseWindow} 정도로 보이고 하락 후 눌림은 ${drawdownLinger || "unknown"} 정도 이어질 수 있습니다.`;
      }
      if (dropWindow && dropWindow !== "unknown") {
        return `${optimalSellTimestamp || "매도 시점은 아직 불명확"} 전후가 고점 후보이고 ${optimalBuyTimestamp || "매수 시점은 아직 불명확"} 전후가 저점 후보이며, 고점에서 저점까지는 대략 ${dropWindow} 정도로 보이고 이후 눌림은 ${drawdownLinger || "unknown"} 정도 지속될 수 있습니다.`;
      }
      return `${optimalBuyTimestamp || "매수 시점은 아직 불명확"} 전후가 매수 후보이고 ${optimalSellTimestamp || "매도 시점은 아직 불명확"} 전후가 매도 후보이며, 눌림 해소 시점은 ${drawdownRecovery || "아직 불명확"}로 추정됩니다.`;
    }
    return "현재 입력만으로는 최적 매수·매도 시기를 뚜렷하게 특정하기 어렵습니다.";
  }

  const prompt = `Context: ${data}

Task: You are a Timing Agent.
Write exactly one sentence in Korean that explains the best buy timing and best sell timing from the Prophet forecast.

Output rules:
- Mention both the buy timing and the sell timing if they exist.
- If the context includes a rise window or drop window, mention how many days or hours it is expected to take from low to high or high to low.
- If the context includes drawdown linger or drawdown recovery timing, mention how long the post-drop drag could last.
- Keep it to exactly one sentence.
- Focus on timing windows, not route fees.
`;
  return await callGemini(prompt);
};

export const runSpikeSustainAgent = async (data: string) => {
  const spikeSustain = extractContextValue(data, "Spike Sustain (human)");
  const spikeConsensus = extractContextValue(data, "Spike Sustain Consensus (human)");
  const spikeConsensusSource = extractContextValue(data, "Spike Consensus Source");
  const spikePeakTimestamp = extractContextValue(data, "Spike Peak Timestamp");
  const spikeFadeTimestamp = extractContextValue(data, "Spike Fade Timestamp");
  const spikeFadeInHorizon = extractContextValue(data, "Spike Fade In Horizon").toLowerCase();
  const maxSpikePct = extractContextValue(data, "Max Spike Pct");
  const timesfmSpikeSustain = extractContextValue(data, "TimesFM Spike Sustain (human)");
  const prophetSpikeWeight = extractContextValue(data, "Prophet Spike Model Weight") || "1.000";
  const timesfmSpikeWeight = extractContextValue(data, "TimesFM Spike Model Weight") || "1.000";
  const spikeLeader = extractContextValue(data, "Spike Sustain Leader") || "unknown";

  if (!process.env.GEMINI_API_KEY) {
    const resolvedSustain =
      spikeConsensus && spikeConsensus !== "unknown" ? spikeConsensus : spikeSustain;
    if (resolvedSustain && resolvedSustain !== "unknown") {
      if (spikeFadeInHorizon === "false") {
        return `상승 스파이크는 최소 ${resolvedSustain} 정도 이어질 가능성이 높고 현재 horizon 안에서는 뚜렷한 페이드가 보이지 않으며, ${spikeLeader} 모델 우위와 ${spikeConsensusSource || "consensus"} 기준으로 해석하면 추격 매수보다 지속 구간 관리가 더 중요합니다.`;
      }
      return `상승 스파이크는 대략 ${resolvedSustain} 유지될 수 있고 정점 후보는 ${spikePeakTimestamp || "아직 불명확"}, 페이드 시점 후보는 ${spikeFadeTimestamp || "아직 불명확"}이며 TimesFM은 ${timesfmSpikeSustain || "unknown"} 정도를 보면서 Prophet ${prophetSpikeWeight}, TimesFM ${timesfmSpikeWeight} weight가 같이 반영됩니다.`;
    }
    return `현재 Prophet과 TimesFM 곡선만으로는 상승 스파이크 지속 시간을 뚜렷하게 특정하기 어렵고, 최대 예상 상방 스파이크는 ${maxSpikePct || "unknown"} 수준입니다.`;
  }

  const prompt = `Context: ${data}

Task: You are a Spike Sustain Agent.
Write exactly one sentence in Korean that explains how long an upside spike could persist after a meaningful upward burst.

Output rules:
- Mention the consensus spike sustain estimate when it exists.
- Mention the likely peak timing and fade timing if they exist.
- Briefly mention whether the spike appears to fade inside the current forecast horizon.
- If TimesFM and Prophet are both present, mention which side currently has more learned weight.
- Keep it to exactly one sentence.
`;
  return await callGemini(prompt);
};

export const runDrawdownLingerAgent = async (data: string) => {
  const drawdownLinger = extractContextValue(data, "Drawdown Linger (human)");
  const drawdownConsensus = extractContextValue(data, "Drawdown Linger Consensus (human)");
  const drawdownConsensusSource = extractContextValue(data, "Drawdown Consensus Source");
  const drawdownRecovery = extractContextValue(data, "Drawdown Recovery Timestamp");
  const drawdownRecoveryInHorizonRaw = extractContextValue(
    data,
    "Drawdown Recovery In Horizon"
  );
  const drawdownRecoveryInHorizon = drawdownRecoveryInHorizonRaw.toLowerCase();
  const maxDrawdownPct = extractContextValue(data, "Max Drawdown Pct");
  const timesfmStatus = extractContextValue(data, "TimesFM Status");
  const timesfmLinger = extractContextValue(data, "TimesFM Drawdown Linger (human)");

  if (!process.env.GEMINI_API_KEY) {
    const resolvedLinger =
      drawdownConsensus && drawdownConsensus !== "unknown"
        ? drawdownConsensus
        : drawdownLinger;
    if (resolvedLinger && resolvedLinger !== "unknown") {
      if (drawdownRecoveryInHorizon === "false") {
        return `한번 크게 떨어지면 눌림은 최소 ${resolvedLinger} 이어질 가능성이 높고, 현재 horizon 안에서는 회복 시점이 아직 보이지 않으며 ${drawdownConsensusSource || "prophet"} 기준으로 보수적으로 해석하는 편이 맞습니다.`;
      }
      if (timesfmStatus === "ok" && timesfmLinger && timesfmLinger !== "unknown") {
        return `한번 크게 떨어지면 눌림은 대략 ${resolvedLinger} 이어질 수 있고 TimesFM은 ${timesfmLinger} 정도의 눌림을 시사하며, 회복 시점 후보는 ${drawdownRecovery || "아직 불명확"}입니다.`;
      }
      return `한번 크게 떨어지면 눌림은 대략 ${resolvedLinger} 이어질 수 있고, 회복 시점 후보는 ${drawdownRecovery || "아직 불명확"}입니다.`;
    }
    return `현재 Prophet 곡선만으로는 하락 후 눌림 지속 시간을 뚜렷하게 특정하기 어렵고, 최대 예상 drawdown은 ${maxDrawdownPct || "unknown"} 수준입니다.`;
  }

  const prompt = `Context: ${data}

Task: You are a Drawdown Linger Agent.
Write exactly one sentence in Korean that explains how long a post-drop drag could last after a meaningful decline.

Output rules:
- Mention how long the post-drop linger could last if the context provides it.
- Prefer the consensus linger estimate when both Prophet and TimesFM estimates are available.
- If TimesFM is available, briefly note whether it agrees with or extends the linger estimate.
- Mention whether recovery appears inside the current forecast horizon.
- If available, mention the expected recovery timestamp or max drawdown magnitude briefly.
- Keep it to exactly one sentence.
`;
  return await callGemini(prompt);
};

export const runRegretAgent = async (data: string) => {
  const regretAction = extractContextValue(data, "Regret Agent Action") || "unknown";
  const regretConfidence = extractContextValue(data, "Regret Agent Confidence") || "unknown";
  const regretRiskScore = extractContextValue(data, "Regret Risk Score") || "unknown";
  const regretBias = extractContextValue(data, "Regret Bias") || "unknown";
  const buyRegretScore = extractContextValue(data, "Buy Regret Score") || "unknown";
  const sellRegretScore = extractContextValue(data, "Sell Regret Score") || "unknown";
  const drawdownLinger = extractContextValue(data, "Drawdown Linger Consensus (human)") ||
    extractContextValue(data, "Drawdown Linger (human)") ||
    "unknown";
  const spikeSustain = extractContextValue(data, "Spike Sustain Consensus (human)") ||
    extractContextValue(data, "Spike Sustain (human)") ||
    "unknown";
  const uncertaintyRatio = extractContextValue(data, "Uncertainty Ratio") || "unknown";
  const maxUpsidePct = extractContextValue(data, "Max Upside Pct") || "unknown";

  if (!process.env.GEMINI_API_KEY) {
    if (regretRiskScore !== "unknown") {
      if (regretBias === "buy_regret") {
        return `후회 agent 기준으로는 BUY 후 더 싼 자리나 긴 눌림을 겪을 후회가 더 크며 regret score ${regretRiskScore}, confidence ${regretConfidence}, drawdown linger ${drawdownLinger}, uncertainty ${uncertaintyRatio}를 감안하면 action은 ${regretAction} 쪽으로 해석하는 편이 맞습니다.`;
      }
      if (regretBias === "sell_regret") {
        return `후회 agent 기준으로는 SELL 후 더 긴 상승을 놓칠 후회가 더 크며 regret score ${regretRiskScore}, confidence ${regretConfidence}, spike sustain ${spikeSustain}, upside ${maxUpsidePct}를 감안하면 action은 ${regretAction} 쪽으로 해석하는 편이 맞습니다.`;
      }
      return `후회 agent는 buy regret ${buyRegretScore}, sell regret ${sellRegretScore}가 비슷한 균형 구간으로 보고 있으며 regret score ${regretRiskScore}, confidence ${regretConfidence} 기준으로는 ${regretAction} 쪽이 과도한 후회를 줄이는 선택입니다.`;
    }
    return "후회 agent 관점에서는 지금 판단을 늦게 후회할지, 너무 빨리 후회할지의 균형이 아직 뚜렷하지 않습니다.";
  }

  const prompt = `Context: ${data}

Task: You are a Regret Agent.
Write exactly one sentence in Korean that explains the likely regret profile of the current action.

Output rules:
- Start with "후회 agent:"
- Mention whether the larger risk is buy regret, sell regret, or a balanced regret profile.
- Use the regret action, regret confidence, regret risk score, buy regret score, and sell regret score from the context.
- Briefly tie buy regret to drawdown linger / uncertainty and sell regret to spike sustain / upside when relevant.
- Keep it to exactly one sentence.
`;
  return await callGemini(prompt);
};

const buildInvestorFallback = (
  label: string,
  tokenSymbol: string,
  uncertaintyRatio: string,
  maxUpsidePct: string,
  turnoverPotential: string,
  macroSummary: string,
  lensWeight: string,
  overshootBias: string,
  overshootReachPct: string,
  overshootSustainHuman: string,
  overshootConfidence: string,
  portfolioGeometryMethod: string,
  geometryAlignmentScore: string,
  geometryDistance: string,
  seasonalityHeadline: string,
  koreanSurgeHeat: string,
  koreanSurgeRegime: string,
  selectedBeliefScore: string,
  selectedBeliefLabel: string,
  selectedBeliefAgreement: string,
  selectedBeliefConsensusAction: string
) =>
  `${label} 관점에서는 ${tokenSymbol}에 대해 불확실성 ${uncertaintyRatio}, upside ${maxUpsidePct}, turnover ${turnoverPotential}, 현재 lens weight ${lensWeight}를 감안해 ${macroSummary} 국면에서 ${
    overshootBias === "upside"
      ? `상방 overshooting은 ${overshootReachPct} 정도까지 열려 있고 대략 ${overshootSustainHuman} 이어질 가능성이 ${overshootConfidence} 수준`
      : overshootBias === "downside"
        ? `하방 overshooting은 ${overshootReachPct} 정도까지 열려 있고 대략 ${overshootSustainHuman} 이어질 가능성이 ${overshootConfidence} 수준`
        : `overshooting 지속성은 아직 뚜렷하지 않고 신뢰도는 ${overshootConfidence} 수준`
  }이며 seasonality 관점에서는 ${seasonalityHeadline || "unknown"}이고, Korean surge pulse는 ${koreanSurgeRegime} (${koreanSurgeHeat}), 현재 belief는 ${selectedBeliefScore} (${selectedBeliefLabel}), 합의는 ${selectedBeliefAgreement}, consensus는 ${selectedBeliefConsensusAction} 수준이며 ${portfolioGeometryMethod || "geometry optimization"} 기준으로 현재 종목의 정렬도는 ${geometryAlignmentScore}, 목표점 거리감은 ${geometryDistance}라 무리한 추격보다 선별적으로 접근하는 편이 낫습니다.`;

const buildMacbookFallback = (
  tokenSymbol: string,
  uncertaintyRatio: string,
  maxUpsidePct: string,
  turnoverPotential: string,
  championPortfolioLabel: string,
  championPortfolioProfile: string,
  championPortfolioScore: string,
  manifoldContinuityScore: string,
  manifoldTargetDistance: string,
  geometryAlignmentScore: string,
  weightedBeliefScore: string,
  weightedBeliefAgreement: string,
  selectedBeliefScore: string,
  selectedBeliefLabel: string,
  selectedBeliefAgreement: string,
  selectedBeliefConsensusAction: string,
  weightedDrawdownLingerDays: string,
  weightedPersistencePct: string,
  weightedRegimeRiskPct: string,
  macbookWeight: string,
  macbookAvgReward: string,
  macbookHitRate: string,
  macbookRewardCount: string,
  macbookLastReward: string,
  macbookLastRealizedReturnPct: string,
  macbookLastCoverageRatio: string,
  macbookChampionAvgReward: string,
  macbookChampionAlignmentScore: string,
  macbookChampionRewardCount: string,
  macbookChampionPreferredCps: string,
  weightedSmallCapTailScore: string,
  weightedHeavyTailScore: string,
  weightedHeavyTailPremium: string,
  redditSmallCapHeat: string,
  redditSmallCapRegime: string,
  koreanSurgeHeat: string,
  koreanSurgeRegime: string,
  macroSummary: string
) =>
  `Macbook view: ${tokenSymbol}은 현재 ${championPortfolioLabel || "champion portfolio"}의 ${championPortfolioProfile || "adaptive"} 프로필 위에서 score ${championPortfolioScore}, manifold 연속성 ${manifoldContinuityScore}, target 거리 ${manifoldTargetDistance}, geometry 정렬도 ${geometryAlignmentScore}, belief ${selectedBeliefScore} (${selectedBeliefLabel}), agreement ${selectedBeliefAgreement}, consensus ${selectedBeliefConsensusAction}를 보이는 종목이며, 가상 포트폴리오를 다음 거래일에 자동 채점한 Macbook agent는 weight ${macbookWeight}, 평균 보상 ${macbookAvgReward}, 적중률 ${macbookHitRate}, 누적 평가 ${macbookRewardCount}회와 최근 실현 수익률 ${macbookLastRealizedReturnPct}, 최근 reward ${macbookLastReward}, 최근 커버리지 ${macbookLastCoverageRatio}에 더해 Champion Prophet 평균 보상 ${macbookChampionAvgReward}, 정렬도 ${macbookChampionAlignmentScore}, reward count ${macbookChampionRewardCount}, 선호 changepoint ${macbookChampionPreferredCps}를 함께 읽고 있어 ${macroSummary} 국면에서도 포트폴리오 belief ${weightedBeliefScore}, agreement ${weightedBeliefAgreement}, 불확실성 ${uncertaintyRatio}, upside ${maxUpsidePct}, turnover ${turnoverPotential}, 눌림 ${weightedDrawdownLingerDays}일, persistence ${weightedPersistencePct}, regime risk ${weightedRegimeRiskPct}, small-cap tail ${weightedSmallCapTailScore}, heavy-tail ${weightedHeavyTailScore}, tail premium ${weightedHeavyTailPremium}, small-cap pulse ${redditSmallCapRegime} (${redditSmallCapHeat}), Korean surge pulse ${koreanSurgeRegime} (${koreanSurgeHeat})를 감안하면 선별 보유 쪽의 근거가 남아 있다고 판단합니다.`;

const runInvestorLensAgent = async (
  data: string,
  lens: "buffett" | "druckenmiller" | "lynch" | "dalio"
) => {
  const marketMode = extractContextValue(data, "Market Mode").toLowerCase();
  const tokenSymbol = extractContextValue(data, "Token Symbol") || "UNKNOWN";
  const uncertaintyRatio = extractContextValue(data, "Uncertainty Ratio") || "unknown";
  const maxUpsidePct = extractContextValue(data, "Max Upside Pct") || "unknown";
  const turnoverPotential = extractContextValue(data, "Turnover Potential") || "unknown";
  const macroSummary = extractContextValue(data, "Macro Summary") || "거시 환경 확인 필요";
  const overshootBias = extractContextValue(data, "Overshoot Bias") || "balanced";
  const overshootReachPct = extractContextValue(data, "Overshoot Reach Pct") || "unknown";
  const overshootSustainHuman =
    extractContextValue(data, "Overshoot Sustain (human)") || "unknown";
  const overshootConfidence =
    extractContextValue(data, "Overshoot Confidence") || "unknown";
  const portfolioGeometryMethod =
    extractContextValue(data, "Portfolio Geometry Method") || "unknown";
  const geometryAlignmentScore =
    extractContextValue(data, "Selected Geometry Alignment Score") ||
    extractContextValue(data, "Portfolio Geometry Alignment Score") ||
    "unknown";
  const geometryDistance =
    extractContextValue(data, "Selected Geometry Distance") ||
    extractContextValue(data, "Portfolio Geometry Distance") ||
    "unknown";
  const seasonalityHeadline =
    extractContextValue(data, "Seasonality Headline") || "unknown";
  const koreanSurgeHeat =
    extractContextValue(data, "Portfolio Korean Surge Heat Score") || "unknown";
  const koreanSurgeRegime =
    extractContextValue(data, "Portfolio Korean Surge Regime") || "unknown";
  const selectedBeliefScore =
    extractContextValue(data, "Selected Belief Score") || "unknown";
  const selectedBeliefLabel =
    extractContextValue(data, "Selected Belief Label") || "unknown";
  const selectedBeliefAgreement =
    extractContextValue(data, "Selected Belief Agreement") || "unknown";
  const selectedBeliefConsensusAction =
    extractContextValue(data, "Selected Belief Consensus Action") || "unknown";
  const lensWeight =
    lens === "buffett"
      ? extractContextValue(data, "Buffett Lens Weight") || "1.000"
      : lens === "druckenmiller"
        ? extractContextValue(data, "Druckenmiller Lens Weight") || "1.000"
        : lens === "dalio"
          ? extractContextValue(data, "Dalio Lens Weight") || "1.000"
          : extractContextValue(data, "Lynch Lens Weight") || "1.000";

  const fallbackPrefix =
    lens === "buffett"
      ? "Buffett-style"
      : lens === "druckenmiller"
        ? "Druckenmiller-style"
        : lens === "dalio"
          ? "Dalio-style"
          : "Lynch-style";

  if (marketMode !== "sp500" && marketMode !== "stock") {
    return `${fallbackPrefix} view: 주식 모드에서만 활성화됩니다.`;
  }

  if (!process.env.GEMINI_API_KEY) {
    return buildInvestorFallback(
      fallbackPrefix,
      tokenSymbol,
      uncertaintyRatio,
      maxUpsidePct,
      turnoverPotential,
      macroSummary,
      lensWeight,
      overshootBias,
      overshootReachPct,
      overshootSustainHuman,
      overshootConfidence,
      portfolioGeometryMethod,
      geometryAlignmentScore,
      geometryDistance,
      seasonalityHeadline,
      koreanSurgeHeat,
      koreanSurgeRegime,
      selectedBeliefScore,
      selectedBeliefLabel,
      selectedBeliefAgreement,
      selectedBeliefConsensusAction
    );
  }

  const personaPrompt =
    lens === "buffett"
      ? `Use a Buffett-style lens: durable business quality, valuation discipline, capital efficiency, and patience. Mention M2 liquidity and rates only as background, not as a trading trigger.`
      : lens === "druckenmiller"
        ? `Use a Druckenmiller-style lens: top-down macro, liquidity, rates, trend persistence, regime shifts, and asymmetric upside/downside.`
        : lens === "dalio"
          ? `Use a Dalio-style lens: macro regime, debt-cycle pressure, liquidity, rates, diversification, and balance between upside capture and drawdown control.`
          : `Use a Lynch-style lens: understandable story, category growth, earnings runway, and whether the market is overreacting relative to the business path.`;

  const label =
    lens === "buffett"
      ? "Buffett-style view"
      : lens === "druckenmiller"
        ? "Druckenmiller-style view"
        : lens === "dalio"
          ? "Dalio-style view"
          : "Lynch-style view";

  const prompt = `Context: ${data}

Task: ${personaPrompt}
Write exactly one short paragraph in Korean under 2 sentences.

Output rules:
- Start with "${label}:"
- Mention the concrete stock symbol.
- Use the macro backdrop (M2 and rates) plus the model's uncertainty, upside, and turnover context.
- Use the spike sustain context when it is present.
- Use stock seasonality context when it is present.
- Use the uncertainty-adjusted geometry / KL-minimizing portfolio context when it is present.
- Use Korean surge pulse context when it is present.
- Use the current belief score/label when it is present.
- If aggregate human symbol-attention bias is present, mention it briefly as a crowd-attention signal.
- If belief agreement or consensus is present, reflect it briefly as a social-learning signal.
- Use symmetry-based dark-horse context when it is present.
- Respect the current lens reliability weight from the context as a confidence prior.
- Explicitly mention how far the current overshooting could extend and roughly how long it may persist.
- Mention whether this stock sits close to or far from the current geometry target point.
- Give a practical stance, not a biography or generic explanation.
`;
  return await callGemini(prompt);
};

export const runBuffettViewAgent = async (data: string) =>
  runInvestorLensAgent(data, "buffett");

export const runDruckenmillerViewAgent = async (data: string) =>
  runInvestorLensAgent(data, "druckenmiller");

export const runLynchViewAgent = async (data: string) =>
  runInvestorLensAgent(data, "lynch");

export const runDalioViewAgent = async (data: string) =>
  runInvestorLensAgent(data, "dalio");

export const runMacbookViewAgent = async (data: string) => {
  const marketMode = extractContextValue(data, "Market Mode").toLowerCase();
  const tokenSymbol = extractContextValue(data, "Token Symbol") || "UNKNOWN";
  const uncertaintyRatio = extractContextValue(data, "Uncertainty Ratio") || "unknown";
  const maxUpsidePct = extractContextValue(data, "Max Upside Pct") || "unknown";
  const turnoverPotential = extractContextValue(data, "Turnover Potential") || "unknown";
  const championPortfolioLabel =
    extractContextValue(data, "Champion Portfolio Label") || "unknown";
  const championPortfolioProfile =
    extractContextValue(data, "Champion Portfolio Profile") || "unknown";
  const championPortfolioScore =
    extractContextValue(data, "Champion Portfolio Score") || "unknown";
  const manifoldContinuityScore =
    extractContextValue(data, "Portfolio Manifold Continuity Score") || "unknown";
  const manifoldTargetDistance =
    extractContextValue(data, "Portfolio Manifold Target Distance") || "unknown";
  const geometryAlignmentScore =
    extractContextValue(data, "Selected Geometry Alignment Score") ||
    extractContextValue(data, "Portfolio Geometry Alignment Score") ||
    "unknown";
  const weightedDrawdownLingerDays =
    extractContextValue(data, "Portfolio Weighted Drawdown Linger Days") || "unknown";
  const weightedPersistencePct =
    extractContextValue(data, "Portfolio Weighted Persistence Pct") || "unknown";
  const weightedRegimeRiskPct =
    extractContextValue(data, "Portfolio Weighted Regime Risk Pct") || "unknown";
  const weightedBeliefScore =
    extractContextValue(data, "Portfolio Weighted Belief Score") || "unknown";
  const weightedBeliefAgreement =
    extractContextValue(data, "Portfolio Weighted Belief Agreement") || "unknown";
  const selectedBeliefScore =
    extractContextValue(data, "Selected Belief Score") || "unknown";
  const selectedBeliefLabel =
    extractContextValue(data, "Selected Belief Label") || "unknown";
  const selectedBeliefAgreement =
    extractContextValue(data, "Selected Belief Agreement") || "unknown";
  const selectedBeliefConsensusAction =
    extractContextValue(data, "Selected Belief Consensus Action") || "unknown";
  const weightedSmallCapTailScore =
    extractContextValue(data, "Portfolio Weighted Small Cap Tail Score") || "unknown";
  const weightedHeavyTailScore =
    extractContextValue(data, "Portfolio Weighted Heavy Tail Score") || "unknown";
  const weightedHeavyTailPremium =
    extractContextValue(data, "Portfolio Weighted Heavy Tail Premium") || "unknown";
  const redditSmallCapHeat =
    extractContextValue(data, "Portfolio Reddit Small Cap Heat Score") || "unknown";
  const redditSmallCapRegime =
    extractContextValue(data, "Portfolio Reddit Small Cap Regime") || "unknown";
  const koreanSurgeHeat =
    extractContextValue(data, "Portfolio Korean Surge Heat Score") || "unknown";
  const koreanSurgeRegime =
    extractContextValue(data, "Portfolio Korean Surge Regime") || "unknown";
  const macbookWeight = extractContextValue(data, "Macbook Agent Weight") || "1.000";
  const macbookAvgReward =
    extractContextValue(data, "Macbook Agent Avg Reward") || "0.000000";
  const macbookHitRate = extractContextValue(data, "Macbook Agent Hit Rate") || "0.000000";
  const macbookRewardCount =
    extractContextValue(data, "Macbook Agent Reward Count") || "0";
  const macbookLastReward =
    extractContextValue(data, "Macbook Agent Last Reward") || "unknown";
  const macbookLastRealizedReturnPct =
    extractContextValue(data, "Macbook Agent Last Realized Return Pct") || "unknown";
  const macbookLastCoverageRatio =
    extractContextValue(data, "Macbook Agent Last Coverage Ratio") || "unknown";
  const macbookChampionAvgReward =
    extractContextValue(data, "Macbook Agent Champion Avg Reward") || "unknown";
  const macbookChampionAlignmentScore =
    extractContextValue(data, "Macbook Agent Champion Alignment Score") || "unknown";
  const macbookChampionRewardCount =
    extractContextValue(data, "Macbook Agent Champion Reward Count") || "unknown";
  const macbookChampionPreferredCps =
    extractContextValue(data, "Macbook Agent Champion Preferred CPS") || "unknown";
  const macroSummary = extractContextValue(data, "Macro Summary") || "거시 환경 확인 필요";

  if (marketMode !== "sp500" && marketMode !== "stock") {
    return "Macbook view: 주식 모드에서만 활성화됩니다.";
  }

  if (!process.env.GEMINI_API_KEY) {
    return buildMacbookFallback(
      tokenSymbol,
      uncertaintyRatio,
      maxUpsidePct,
      turnoverPotential,
      championPortfolioLabel,
      championPortfolioProfile,
      championPortfolioScore,
      manifoldContinuityScore,
      manifoldTargetDistance,
      geometryAlignmentScore,
      weightedBeliefScore,
      weightedBeliefAgreement,
      selectedBeliefScore,
      selectedBeliefLabel,
      selectedBeliefAgreement,
      selectedBeliefConsensusAction,
      weightedDrawdownLingerDays,
      weightedPersistencePct,
      weightedRegimeRiskPct,
      macbookWeight,
      macbookAvgReward,
      macbookHitRate,
      macbookRewardCount,
      macbookLastReward,
      macbookLastRealizedReturnPct,
      macbookLastCoverageRatio,
      macbookChampionAvgReward,
      macbookChampionAlignmentScore,
      macbookChampionRewardCount,
      macbookChampionPreferredCps,
      weightedSmallCapTailScore,
      weightedHeavyTailScore,
      weightedHeavyTailPremium,
      redditSmallCapHeat,
      redditSmallCapRegime,
      koreanSurgeHeat,
      koreanSurgeRegime,
      macroSummary
    );
  }

  const prompt = `Context: ${data}

Task: You are the Macbook portfolio feedback agent.
Interpret the current stock through a virtual-portfolio lens that automatically checks the portfolio on the next trading day and learns from whether it was right or wrong.
Write exactly one short paragraph in Korean under 2 sentences.

Output rules:
- Start with "Macbook view:"
- Mention the concrete stock symbol.
- Use the champion portfolio profile, manifold continuity, target distance, and geometry alignment as the structural context.
- Use the Macbook agent learning state from the context: weight, average reward, hit rate, reward count, last reward, last realized return, and last coverage ratio.
- Use the connected Champion Prophet context from the Macbook state: champion average reward, champion alignment score, champion reward count, and preferred changepoint prior.
- Use the current spike-sustain feedback loop context when it is present.
- Use symmetry-based dark-horse context when it is present.
- Use the small-cap heavy-tail context when it is present.
- Use the small-cap pulse-board context when it is present.
- Use the Korean surge pulse context when it is present.
- Explain whether the agent's recent virtual portfolio feedback plus Champion Prophet reinforcement supports pressing the position, holding selectively, or staying cautious.
- Mention uncertainty, upside, turnover, and drawdown linger briefly.
- Mention the portfolio-level belief and the stock-level belief briefly.
- If aggregate human symbol-attention bias is present, mention it briefly as a crowd-attention signal.
- If belief agreement or consensus is present, reflect it briefly as a social-learning confidence signal.
- Use the macro backdrop as context, not as a separate lecture.
- Keep it practical and investor-readable.
`;
  return await callGemini(prompt);
};

export const runPortfolioRebalanceChatAgent = async (
  context: string,
  userPrompt: string
) => {
  const overlapWeightPct =
    extractContextValue(context, "Current Portfolio Overlap Weight Pct") || "unknown";
  const weightedUpsidePct =
    extractContextValue(context, "Current Portfolio Weighted Upside Pct") || "unknown";
  const weightedUncertaintyPct =
    extractContextValue(context, "Current Portfolio Weighted Uncertainty Pct") || "unknown";
  const weightedDarkHorseScore =
    extractContextValue(context, "Current Portfolio Weighted Dark Horse Score") || "unknown";
  const keepList = extractContextValue(context, "Keep Candidates") || "none";
  const reduceList = extractContextValue(context, "Reduce Candidates") || "none";
  const exitList = extractContextValue(context, "Exit Candidates") || "none";
  const addList = extractContextValue(context, "Add Candidates") || "none";
  const macroSummary = extractContextValue(context, "Macro Summary") || "거시 환경 확인 필요";

  if (!process.env.GEMINI_API_KEY) {
    return `재구성 제안: 현재 CSV 포트폴리오는 최적 포트폴리오와 겹치는 비중이 ${overlapWeightPct}, 가중 upside ${weightedUpsidePct}, 가중 uncertainty ${weightedUncertaintyPct}, dark-horse exposure ${weightedDarkHorseScore} 수준입니다. 우선 ${keepList}는 유지하고, ${reduceList}는 축소, ${exitList}는 정리, ${addList}는 추가 검토하는 편이 좋으며 ${macroSummary} 환경에서는 불필요하게 겹치는 노출보다 품질 높은 핵심 보유와 숨은 후보를 함께 가져가는 구성이 더 낫습니다.`;
  }

  const prompt = `Context: ${context}

User request: ${userPrompt || "업로드한 포트폴리오를 어떻게 재구성하면 좋을지 알려줘."}

Task: You are a portfolio rebalancing chat agent.
Write the answer in Korean as one short, practical advisory paragraph under 5 sentences.

Output rules:
- Treat the uploaded CSV as the user's current portfolio.
- Compare it against the in-app optimized portfolio, the information map, and the dark-horse symmetry candidates.
- Mention what to keep, what to trim, what to exit, and what to add.
- Use overlap, upside, uncertainty, drawdown/spike context, and dark-horse exposure when available.
- Use the macro backdrop as context, not as a separate lecture.
- Be specific and actionable, not generic.
`;
  return await callGemini(prompt);
};
