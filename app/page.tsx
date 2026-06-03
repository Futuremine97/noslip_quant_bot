"use client";

import { useEffect, useState } from "react";
import { searchTokens, type TokenSearchResult } from "./actions/tokens";
import { searchSp500Equities, type EquitySearchResult } from "./actions/equities";
import { getJupiterQuote } from "./actions/jupiter";
import { reportFinalRecommendation } from "./actions/prediction";
import {
  assessExecutionRisk,
  type ExecutionActionMode,
} from "../services/risk/execution-risk";

const SOL_MINT = "So11111111111111111111111111111111111111112";
const INPUT_SOL_AMOUNT_LAMPORTS = "1000000000";
const FALLBACK_TOKEN_ICON =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='18' fill='%2312192f'/%3E%3Cpath d='M20 21h24l-4 6H16l4-6Zm4 16h24l-4 6H20l4-6Z' fill='%23f8fafc'/%3E%3C/svg%3E";
const ANALYSIS_CONCURRENCY = 2;
const ANALYSIS_STAGGER_MS = 350;
const PORTFOLIO_CHAT_MAX_FILE_BYTES = 256 * 1024;
const WRAPPER_BASE_WEIGHTS: Record<string, number> = {
  final_action_agent: 1.0,
  time_to_below_agent: 1.0,
  em_regime_agent: 1.06,
  minimax_prior_agent: 1.04,
  spike_sustain_agent: 1.02,
  drawdown_linger_agent: 1.05,
  regret_agent: 1.07,
  conservative_gold_agent: 1.1,
  execution_cost_agent: 1.2,
};
const SP500_ONLY_ENV = process.env.NEXT_PUBLIC_SP500_ONLY === "true";
const LOCAL_SP500_ONLY_HOSTS = new Set(["localhost", "127.0.0.1", "::1"]);

function shouldLockToSp500OnThisHost() {
  if (SP500_ONLY_ENV) {
    return true;
  }

  if (typeof window === "undefined") {
    return false;
  }

  return LOCAL_SP500_ONLY_HOSTS.has(window.location.hostname);
}

type Tone =
  | "idle"
  | "ready"
  | "active"
  | "positive"
  | "negative"
  | "neutral"
  | "muted";

type MarketMode = "crypto" | "sp500";

type RouteAnalysisStep = {
  inputMint: string;
  outputMint: string;
  symbol: string;
  stepKey: string;
};

type AgentEvent = {
  id: string;
  title: string;
  detail: string;
  tone: Tone;
  nodeId: string;
  createdAt: string | null;
};

type PortfolioChatSuggestion = {
  symbol: string;
  rationale: string;
};

type PortfolioChatResponse = {
  ok: boolean;
  assistant: string;
  summary?: {
    holdingsCount?: number;
    recognizedHoldingsCount?: number;
    overlapWeightPct?: number | null;
    weightedUpsidePct?: number | null;
    weightedUncertaintyPct?: number | null;
    weightedDrawdownLingerDays?: number | null;
    weightedSpikeSustainDays?: number | null;
    weightedDarkHorseScore?: number | null;
    unknownSymbols?: string[];
  };
  suggestions?: {
    keep?: PortfolioChatSuggestion[];
    reduce?: PortfolioChatSuggestion[];
    exit?: PortfolioChatSuggestion[];
    add?: PortfolioChatSuggestion[];
  };
  security?: {
    processedInMemory?: boolean;
    filePersisted?: boolean;
    maxFileBytes?: number;
    rowsProcessed?: number;
    columnHeaders?: string[];
  };
  error?: string;
};

type PortfolioChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  fileName?: string | null;
  analysis?: PortfolioChatResponse | null;
};

type RouteSummaryLeg = {
  id: string;
  stepKey: string;
  label: string;
  share: string;
  path: string;
  usdValue: string;
  inputMintLabel: string;
  outputMintLabel: string;
};

type DirectionAgentSummary = {
  agent?: string;
  action?: string;
  score?: number | null;
  uncertaintyRatio?: number | null;
  weight?: number | null;
  gold?: number | null;
};

type DirectionRuleSummary = {
  finalAction?: string | null;
  weightedScore?: number | null;
  currentPrice?: number | null;
  currentTimestamp?: string | null;
  firstBelowCurrentTimestamp?: string | null;
  timeToBelowCurrentSeconds?: number | null;
  firstMomentPricePerHour?: number | null;
  firstMomentPctPerHour?: number | null;
  secondMomentPricePerHour2?: number | null;
  secondMomentPctPerHour2?: number | null;
  agents?: DirectionAgentSummary[];
};

type TimingRuleSummary = {
  predictedTimestamp?: string | null;
  predictedPrice?: number | null;
};

type PerRuleSummary = {
  ruleLabel?: string | null;
  direction?: DirectionRuleSummary | null;
  lowTiming?: TimingRuleSummary | null;
  highTiming?: TimingRuleSummary | null;
};

type WrapperAgentOutput = {
  name?: string;
  action?: string;
  confidence?: number | null;
  voteValue?: number | null;
  allowExecution?: boolean;
  reasons?: string[];
  localMetrics?: Record<string, unknown>;
};

type WrapperBaggingSummary = {
  enabled?: boolean;
  iterations?: number | null;
  sampleSize?: number | null;
  sampleFraction?: number | null;
  blendAlpha?: number | null;
  action?: string | null;
  meanAction?: string | null;
  meanVote?: number | null;
  voteStd?: number | null;
  baseWeightedVote?: number | null;
  blendedVote?: number | null;
  stability?: number | null;
  executionAllowedProbability?: number | null;
  voteInterval?: {
    lower?: number | null;
    upper?: number | null;
  };
  actionProbabilities?: Record<string, number>;
};

type WrapperSummary = {
  finalAction?: string | null;
  weightedVote?: number | null;
  executionAllowed?: boolean;
  yesExecutionVotes?: number | null;
  weights?: Record<string, number>;
  weightSource?: string | null;
  feedback?: {
    symbol?: string;
    referenceTimestamp?: string;
    realizedTimestamp?: string;
    referencePrice?: number;
    realizedPrice?: number;
    realizedReturnPct?: number;
    realizedAction?: string;
    elapsedSeconds?: number;
  } | null;
  feedbackCount?: number | null;
  rationale?: string[];
  byzantine?: {
    enabled?: boolean;
    toleratedFaults?: number | null;
    consensusAction?: string | null;
    consensusRatio?: number | null;
    medianSignedVote?: number | null;
    trustedAgents?: string[];
    flaggedAgents?: Array<{
      name?: string;
      action?: string;
      confidence?: number | null;
      signedVote?: number | null;
      anomalyScore?: number | null;
      reasons?: string[];
    }>;
  } | null;
  bagging?: WrapperBaggingSummary | null;
  agentOutputs?: WrapperAgentOutput[];
  consensusGraphBase64?: string | null;
};

type StepPrediction = {
  supported: boolean;
  requestedSymbol: string;
  resolvedSymbol: string;
  source?: string;
  dataset?: string | null;
  analysisDate?: string | null;
  analysisTimestampLocal?: string | null;
  rows?: number;
  currentPrice?: number;
  livePrice?: number | null;
  lastClosePrice?: number | null;
  finalAction?: "BUY" | "SELL" | "HOLD";
  directionVote?: number;
  directionStrength?: number;
  firstMomentPricePerHour?: number | null;
  firstMomentPctPerHour?: number | null;
  secondMomentPricePerHour2?: number | null;
  secondMomentPctPerHour2?: number | null;
  timeToOptimalBuySeconds?: number | null;
  timeToOptimalSellSeconds?: number | null;
  riseWindowSeconds?: number | null;
  dropWindowSeconds?: number | null;
  spikeStartTimestamp?: string | null;
  spikePeakTimestamp?: string | null;
  spikePeakPrice?: number | null;
  spikeSustainSeconds?: number | null;
  spikeFadeTimestamp?: string | null;
  spikeFadeInHorizon?: boolean | null;
  peakToFadeSeconds?: number | null;
  maxSpikePct?: number | null;
  drawdownStartTimestamp?: string | null;
  drawdownRecoveryTimestamp?: string | null;
  drawdownTroughTimestamp?: string | null;
  drawdownTroughPrice?: number | null;
  drawdownLingerSeconds?: number | null;
  drawdownRecoveryInHorizon?: boolean | null;
  troughToRecoverySeconds?: number | null;
  maxDrawdownPct?: number | null;
  timesfmSpikeStartTimestamp?: string | null;
  timesfmSpikePeakTimestamp?: string | null;
  timesfmSpikePeakPrice?: number | null;
  timesfmSpikeSustainSeconds?: number | null;
  timesfmSpikeFadeTimestamp?: string | null;
  timesfmSpikeFadeInHorizon?: boolean | null;
  timesfmPeakToFadeSeconds?: number | null;
  timesfmMaxSpikePct?: number | null;
  timesfmMoeGate?: MoeExpertGate | null;
  moeRuntime?: MoeRuntime | null;
  spikeSustainConsensusSeconds?: number | null;
  peakToFadeConsensusSeconds?: number | null;
  spikeFadeConsensusInHorizon?: boolean | null;
  maxSpikeConsensusPct?: number | null;
  spikeConsensusSource?: string | null;
  prophetSpikeWeight?: number | null;
  timesfmSpikeWeight?: number | null;
  trendCurve?: Array<{
    timestamp?: string | null;
    value?: number | null;
  }>;
  forecastPlot?: ProphetForecastPlot | null;
  trendComponent?: ProphetComponentSeries | null;
  seasonalityComponents?: Record<string, ProphetComponentSeries>;
  seasonalitySummary?: ProphetSeasonalitySummary | null;
  targetTimestamp?: string | null;
  targetPrice?: number | null;
  timingEnabled?: boolean;
  timeToBelowCurrent?: number | null;
  optimalBuyTimestamp?: string | null;
  optimalBuyPrice?: number | null;
  optimalSellTimestamp?: string | null;
  optimalSellPrice?: number | null;
  cadenceProfile?: string | null;
  cadenceRules?: Array<{
    rule?: string | null;
    label?: string | null;
    weight?: number | null;
  }>;
  runtimeSymbol?: string | null;
  correlationForecast?: SymbolCorrelationForecast | null;
  tailDiagnostics?: TailDiagnostics | null;
  championRefresh?: Record<string, unknown>;
  recommendation?: {
    shouldBuyWithSol?: boolean;
    tone?: Tone | string;
    summary?: string;
  };
  perRuleSummary?: Record<string, PerRuleSummary>;
  wrapper?: WrapperSummary | null;
  reason?: string;
  symbol: string;
  stepKey: string;
};

type ProphetComponentPoint = {
  timestamp?: string | null;
  label?: string | null;
  value?: number | null;
};

type ProphetForecastPlotPoint = {
  timestamp?: string | null;
  yhat?: number | null;
  yhatLower?: number | null;
  yhatUpper?: number | null;
  actual?: number | null;
  trend?: number | null;
  isHistory?: boolean | null;
};

type ProphetForecastChangepoint = {
  timestamp?: string | null;
  trend?: number | null;
  forecast?: number | null;
  magnitude?: number | null;
};

type ProphetForecastPlot = {
  title?: string | null;
  xAxisLabel?: string | null;
  yAxisLabel?: string | null;
  uncertaintyEnabled?: boolean | null;
  historyEndTimestamp?: string | null;
  points?: ProphetForecastPlotPoint[];
  changepoints?: ProphetForecastChangepoint[];
};

type ProphetComponentSeries = {
  title?: string | null;
  xAxisLabel?: string | null;
  yAxisLabel?: string | null;
  valueType?: "price" | "percent" | null;
  points?: ProphetComponentPoint[];
};

type ProphetSeasonalitySummaryItem = {
  title?: string | null;
  peakLabel?: string | null;
  peakValue?: number | null;
  troughLabel?: string | null;
  troughValue?: number | null;
  strength?: number | null;
  summary?: string | null;
};

type ProphetSeasonalitySummary = {
  sourceRule?: string | null;
  headline?: string | null;
  strongestComponent?: string | null;
  weekly?: ProphetSeasonalitySummaryItem | null;
  yearly?: ProphetSeasonalitySummaryItem | null;
  monthly?: ProphetSeasonalitySummaryItem | null;
  quarterly?: ProphetSeasonalitySummaryItem | null;
};

type TailDiagnostics = {
  status?: string | null;
  lookbackDays?: number | null;
  sampleSize?: number | null;
  skewness?: number | null;
  excessKurtosis?: number | null;
  hillTailIndex?: number | null;
  tailConcentration?: number | null;
  extremeMoveRate?: number | null;
  upsideTailShare?: number | null;
  downsideTailShare?: number | null;
  longTailScore?: number | null;
  heavyTailScore?: number | null;
  leftTailRiskScore?: number | null;
  regimeLabel?: string | null;
  rationale?: string | null;
};

type Sp500BeliefAgent = {
  name?: string | null;
  label?: string | null;
  weight?: number | null;
  biasLabel?: string | null;
  beliefPct?: number | null;
  stance?: string | null;
};

type Sp500BeliefNetwork = {
  model?: string | null;
  privateSignalPct?: number | null;
  crowdBeliefPct?: number | null;
  centralBeliefPct?: number | null;
  agreementRatio?: number | null;
  polarizationScore?: number | null;
  consensusAction?: string | null;
  agentCount?: number | null;
  distributedAgents?: Sp500BeliefAgent[];
};

type FmkoreaStockSnapshot = {
  status?: string;
  source?: string;
  board?: string;
  sourceUrl?: string;
  fetchedAt?: string;
  heatScore?: number | null;
  regime?: string | null;
  postsAnalyzed?: number;
  surgePosts?: number;
  topTickers?: Array<{ symbol?: string; mentions?: number }>;
  topKeywords?: Array<{ keyword?: string; mentions?: number }>;
  topThemes?: Array<{ theme?: string; hits?: number }>;
  samplePosts?: Array<{
    title?: string;
    themes?: string[];
  }>;
  error?: string | null;
  path?: string;
};

type CorrelationPeerForecast = {
  symbol?: string | null;
  name?: string | null;
  sector?: string | null;
  currentCorrelation?: number | null;
  predictedCorrelation?: number | null;
  confidence?: number | null;
  windowSpread?: number | null;
  observations?: number | null;
};

type MoeExpertGate = {
  expert?: string | null;
  enabled?: boolean | null;
  profile?: string | null;
  run?: boolean | null;
  reason?: string | null;
  score?: number | null;
  threshold?: number | null;
  signals?: Record<string, number | null | undefined>;
};

type MoeRuntime = {
  enabled?: boolean | null;
  profile?: string | null;
  activeExperts?: string[];
  skippedExperts?: string[];
  experts?: Record<string, MoeExpertGate>;
  budget?: {
    maxHeavyInFlight?: number | null;
  };
};

type SymbolCorrelationForecast = {
  status?: string | null;
  symbol?: string | null;
  asOfDate?: string | null;
  reason?: string | null;
  lookbackDays?: number | null;
  peerUniverse?: number | null;
  averagePredictedCorrelation?: number | null;
  medianPredictedCorrelation?: number | null;
  positiveShare?: number | null;
  inverseShare?: number | null;
  networkLabel?: string | null;
  methodology?: string | null;
  moeGate?: MoeExpertGate | null;
  topCorrelatedPeers?: CorrelationPeerForecast[];
  topDiversifiers?: CorrelationPeerForecast[];
};

type PortfolioCorrelationPairForecast = CorrelationPeerForecast & {
  leftSymbol?: string | null;
  rightSymbol?: string | null;
  leftWeightPct?: number | null;
  rightWeightPct?: number | null;
  pairWeightPct?: number | null;
};

type PortfolioHoldingCorrelationForecast = {
  symbol?: string | null;
  portfolioWeightPct?: number | null;
  averagePredictedCorrelation?: number | null;
  diversificationSupportScore?: number | null;
  strongestCorrelationPeer?: string | null;
  strongestCorrelationValue?: number | null;
  strongestDiversifierPeer?: string | null;
  strongestDiversifierValue?: number | null;
  confidence?: number | null;
};

type PortfolioCorrelationForecast = {
  status?: string | null;
  reason?: string | null;
  asOfDate?: string | null;
  methodology?: string | null;
  holdingCount?: number | null;
  pairCount?: number | null;
  averagePredictedCorrelation?: number | null;
  averageAbsoluteCorrelation?: number | null;
  averagePositiveCorrelation?: number | null;
  diversificationScore?: number | null;
  crowdedPairRiskScore?: number | null;
  concentrationRiskLabel?: string | null;
  topCrowdedPairs?: PortfolioCorrelationPairForecast[];
  topDiversifyingPairs?: PortfolioCorrelationPairForecast[];
  perHolding?: PortfolioHoldingCorrelationForecast[];
};

type Sp500InformationMapPoint = {
  symbol: string;
  analysisDate?: string | null;
  analysisTimestampLocal?: string | null;
  name?: string;
  sector?: string;
  cadenceProfile?: string | null;
  screeningRule?: string | null;
  currentPrice?: number | null;
  lastClosePrice?: number | null;
  finalAction?: "BUY" | "SELL" | "HOLD" | null;
  directionScore?: number | null;
  uncertaintyRatio?: number | null;
  firstMomentPctPerHour?: number | null;
  secondMomentPctPerHour2?: number | null;
  firstMomentPctPerDay?: number | null;
  secondMomentBpPerDay2?: number | null;
  firstCoordinateSpace?: {
    x?: number | null;
    y?: number | null;
    xLabel?: string | null;
    yLabel?: string | null;
  };
  secondCoordinateSpace?: {
    x?: number | null;
    y?: number | null;
    uncertaintyScale?: number | null;
    xLabel?: string | null;
    yLabel?: string | null;
  };
  optimalBuyTimestamp?: string | null;
  optimalBuyPrice?: number | null;
  optimalSellTimestamp?: string | null;
  optimalSellPrice?: number | null;
  timeToOptimalBuySeconds?: number | null;
  timeToOptimalSellSeconds?: number | null;
  spikeStartTimestamp?: string | null;
  spikePeakTimestamp?: string | null;
  spikePeakPrice?: number | null;
  spikeSustainSeconds?: number | null;
  spikeFadeTimestamp?: string | null;
  spikeFadeInHorizon?: boolean | null;
  peakToFadeSeconds?: number | null;
  maxSpikePct?: number | null;
  expectedReturnPct?: number | null;
  maxUpsidePct?: number | null;
  drawdownToBuyPct?: number | null;
  quadrant?: string | null;
  optimizationScore?: number | null;
  darkHorseScore?: number | null;
  darkHorseLabel?: string | null;
  darkHorseRationale?: string | null;
  darkHorseRank?: number | null;
  beliefScore?: number | null;
  beliefLabel?: string | null;
  beliefRationale?: string | null;
  beliefNetwork?: Sp500BeliefNetwork | null;
  smallCapTailProxyScore?: number | null;
  heavyTailProxyScore?: number | null;
  longTailScore?: number | null;
  heavyTailStatScore?: number | null;
  leftTailRiskScore?: number | null;
  tailRegimeLabel?: string | null;
  tailRationale?: string | null;
  tailDiagnostics?: TailDiagnostics | null;
  heavyTailLabel?: string | null;
  heavyTailRationale?: string | null;
  fmkoreaSurgeScore?: number | null;
  fmkoreaMentionCount?: number | null;
  fmkoreaSurgeLabel?: string | null;
  fmkoreaSurgeContext?: {
    source?: string | null;
    board?: string | null;
    score?: number | null;
    mentions?: number | null;
    label?: string | null;
    heatScore?: number | null;
    regime?: string | null;
  } | null;
  symmetry?: {
    counterpartSymbol?: string | null;
    counterpartAction?: string | null;
    counterpartQuadrant?: string | null;
    residualScore?: number | null;
    qualityScore?: number | null;
    underfollowedScore?: number | null;
    recoveryBiasScore?: number | null;
    mirrorStressScore?: number | null;
    darkHorseScore?: number | null;
    label?: string | null;
    rationale?: string | null;
  };
  trajectory?: {
    daysObserved?: number | null;
    stabilityScore?: number | null;
    persistenceScore?: number | null;
    regimeShiftRisk?: number | null;
    continuationBias?: number | null;
    signConsistency?: number | null;
    firstVelocityPctPerDay?: number | null;
    secondVelocityBpPerDay2PerDay?: number | null;
    firstFlipRate?: number | null;
    secondFlipRate?: number | null;
    regimeLabel?: string | null;
    firstCoordinateSpaceDrift?: {
      x?: number | null;
      y?: number | null;
    };
    secondCoordinateSpaceDrift?: {
      x?: number | null;
      y?: number | null;
    };
  };
};

type Sp500InformationMapResult = {
  ok: boolean;
  generatedAt?: string;
  mapDate?: string;
  webNeuralModel?: {
    status?: string;
    updatedAt?: string | null;
    trainingRows?: number;
    validationRows?: number;
    featureCount?: number;
    featureNames?: string[];
    targetHorizon?: string;
    fitMode?: string;
    hiddenDim?: number;
    epochs?: number;
    learningRate?: number;
    trainingMae?: number | null;
    validationMae?: number | null;
    validationRmse?: number | null;
    coverageRatio?: number | null;
    mapDates?: number;
    symbols?: number;
    error?: string | null;
    path?: string;
  };
  featureBenchmark?: {
    status?: string;
    updatedAt?: string | null;
    rows?: number;
    trainingRows?: number;
    validationRows?: number;
    symbols?: number;
    mapDates?: number;
    featureCount?: number;
    featureNames?: string[];
    targetHorizon?: string;
    downstreamModel?: string;
    methodsCompared?: string[];
    recommendedMethod?: {
      method?: string;
      latentDim?: number;
      validationMae?: number | null;
      validationRmse?: number | null;
      [key: string]: unknown;
    } | null;
    bestByMethod?: Array<{
      method?: string;
      latentDim?: number;
      validationMae?: number | null;
      validationRmse?: number | null;
      [key: string]: unknown;
    }>;
    summary?: string | null;
    error?: string | null;
    path?: string;
  };
  cache?: {
    used?: boolean;
    ageSeconds?: number;
    path?: string;
  };
  universe?: {
    evaluatedSymbols?: number;
    failedSymbols?: number;
    limit?: number;
  };
  optimization?: {
    xAxis?: string;
    yAxis?: string;
    method?: string;
  };
  mapSpaces?: {
    firstCoordinate?: {
      label?: string;
      xAxis?: string;
      yAxis?: string;
    };
    secondCoordinate?: {
      label?: string;
      xAxis?: string;
      yAxis?: string;
    };
  };
  history?: {
    datedPath?: string;
    latestPath?: string;
  };
  points: Sp500InformationMapPoint[];
  topPicks: Sp500InformationMapPoint[];
  darkHorsePicks?: Sp500InformationMapPoint[];
  fmkoreaStock?: FmkoreaStockSnapshot;
  failures?: Array<{
    symbol: string;
    reason: string;
  }>;
};

type Sp500PortfolioHolding = {
  symbol: string;
  name?: string;
  sector?: string;
  finalAction?: "BUY" | "SELL" | "HOLD" | null;
  weight?: number | null;
  weightPct?: number | null;
  currentPrice?: number | null;
  livePrice?: number | null;
  lastClosePrice?: number | null;
  uncertaintyRatio?: number | null;
  maxUpsidePct?: number | null;
  expectedReturnPct?: number | null;
  annualizedVolatilityPct?: number | null;
  turnoverPotential?: string | null;
  drawdownLingerSeconds?: number | null;
  drawdownRecoveryInHorizon?: boolean | null;
  spikeSustainSeconds?: number | null;
  spikeFadeInHorizon?: boolean | null;
  maxSpikePct?: number | null;
  darkHorseScore?: number | null;
  darkHorseLabel?: string | null;
  beliefScore?: number | null;
  beliefLabel?: string | null;
  beliefRationale?: string | null;
  beliefNetwork?: Sp500BeliefNetwork | null;
  marketCap?: number | null;
  marketCapBucket?: string | null;
  smallCapTailScore?: number | null;
  heavyTailScore?: number | null;
  heavyTailPremium?: number | null;
  longTailScore?: number | null;
  leftTailRiskScore?: number | null;
  heavyTailLabel?: string | null;
  heavyTailRationale?: string | null;
  tailRegimeLabel?: string | null;
  tailSkewness?: number | null;
  tailExcessKurtosis?: number | null;
  tailConcentration?: number | null;
  tailExtremeMoveRate?: number | null;
  tailDiagnostics?: TailDiagnostics | null;
  fmkoreaStock?: FmkoreaStockSnapshot;
  fmkoreaSurgeScore?: number | null;
  fmkoreaMentionCount?: number | null;
  fmkoreaSurgeLabel?: string | null;
  naturalGradientTargetWeightPct?: number | null;
  naturalGradientBoundWeightPct?: number | null;
  naturalGradientUtilityScore?: number | null;
  naturalGradientLiftPct?: number | null;
  maxDrawdownPct?: number | null;
  optimizationScore?: number | null;
  geometryDistance?: number | null;
  geometryKlDivergence?: number | null;
  geometryAlignmentScore?: number | null;
  averagePredictedCorrelation?: number | null;
  diversificationSupportScore?: number | null;
  strongestCorrelationPeer?: string | null;
  strongestCorrelationValue?: number | null;
  strongestDiversifierPeer?: string | null;
  strongestDiversifierValue?: number | null;
  correlationConfidence?: number | null;
  rationale?: string | null;
  optimalBuyTimestamp?: string | null;
  optimalSellTimestamp?: string | null;
  portfolioWeightPct?: number | null;
  trajectory?: Sp500InformationMapPoint["trajectory"];
};

type Sp500PortfolioGeometryPoint = {
  label?: string;
  symbol?: string;
  x?: number | null;
  y?: number | null;
  weightPct?: number | null;
};

type Sp500PortfolioGeometryOverlay = {
  space?: string;
  method?: string;
  riskProfile?: string;
  targetPoint?: Sp500PortfolioGeometryPoint;
  portfolioPoint?: Sp500PortfolioGeometryPoint;
  projectionLine?: Sp500PortfolioGeometryPoint[];
  frontierLine?: Sp500PortfolioGeometryPoint[];
  portfolioKlDivergence?: number | null;
  portfolioDistance?: number | null;
  alignmentScore?: number | null;
};

type Sp500PortfolioNaturalGradientOverlay = {
  method?: string;
  metric?: string;
  iterations?: number;
  stepSize?: number | null;
  temperature?: number | null;
  upperBoundScore?: number | null;
  liveDistanceToTarget?: number | null;
  boundDistanceToTarget?: number | null;
  liveDistanceToBound?: number | null;
  liveEntropy?: number | null;
  boundEntropy?: number | null;
  fisherTrace?: number | null;
  fisherCurvature?: number | null;
  riskEnvelopeStrength?: number | null;
  targetConcentration?: number | null;
  boundConcentration?: number | null;
};

type Sp500PortfolioSleeveRecommendation = {
  label: string;
  weightPct: number;
  rationale: string;
};

type Sp500PortfolioSectorRecommendation = {
  sector: string;
  portfolioWeightPct: number;
  withinUsEquitiesPct: number;
  weightedUpsidePct?: number | null;
  weightedUncertaintyPct?: number | null;
  weightedDrawdownLingerDays?: number | null;
  weightedSpikeSustainDays?: number | null;
  rationale?: string | null;
};

type Sp500PortfolioRegionRecommendation = {
  label: string;
  portfolioWeightPct: number;
  withinInternationalEquitiesPct: number;
  rationale?: string | null;
};

type Sp500PortfolioResult = {
  ok: boolean;
  generatedAt?: string;
  mapDate?: string;
  cache?: {
    used?: boolean;
    ageSeconds?: number;
    path?: string;
  };
  summary?: {
    holdingsCount?: number;
    weightedUpsidePct?: number | null;
    weightedUncertaintyPct?: number | null;
    weightedVolatilityPct?: number | null;
    weightedDrawdownLingerDays?: number | null;
    weightedMaxDrawdownPct?: number | null;
    weightedSpikeSustainDays?: number | null;
    weightedMaxSpikePct?: number | null;
    weightedDarkHorseScore?: number | null;
    weightedBeliefScore?: number | null;
    weightedBeliefAgreement?: number | null;
    weightedBeliefPolarization?: number | null;
    weightedPersistencePct?: number | null;
    weightedRegimeRiskPct?: number | null;
    weightedSmallCapTailScore?: number | null;
    weightedHeavyTailScore?: number | null;
    weightedHeavyTailPremium?: number | null;
    weightedLongTailScore?: number | null;
    weightedLeftTailRiskScore?: number | null;
    redditSmallCapHeatScore?: number | null;
    redditSmallCapRegime?: string | null;
    fmkoreaStockHeatScore?: number | null;
    fmkoreaStockRegime?: string | null;
    weightedFmkoreaSurgeScore?: number | null;
    averagePredictedCorrelation?: number | null;
    averageAbsoluteCorrelation?: number | null;
    averagePositiveCorrelation?: number | null;
    diversificationScore?: number | null;
    crowdedPairRiskScore?: number | null;
    concentrationRiskLabel?: string | null;
    turnoverMix?: Record<string, number>;
    sectorCount?: number;
  };
  methodology?: {
    objective?: string;
    candidateLimit?: number;
    holdings?: number;
    trailingDays?: number;
    maxPerSector?: number;
    weightBounds?: {
      min?: number;
      max?: number;
    };
    heavyTailMethod?: string;
  };
  manifold?: {
    method?: string;
    historyCount?: number;
    rank?: number;
    stateDimension?: number;
    singularValues?: number[];
    explainedVariance?: number[];
    currentLatent?: number[];
    forecastLatent?: number[];
    continuityScore?: number | null;
    targetDistance?: number | null;
    projectedTarget?: {
      weightedUpsidePct?: number | null;
      weightedUncertaintyPct?: number | null;
      weightedVolatilityPct?: number | null;
      weightedDrawdownLingerDays?: number | null;
      weightedSpikeSustainDays?: number | null;
      weightedPersistencePct?: number | null;
      weightedRegimeRiskPct?: number | null;
      geometryAlignmentScore?: number | null;
      geometryDistance?: number | null;
      usEquitiesPct?: number | null;
      concentrationHhi?: number | null;
    };
    currentState?: Record<string, number | null | undefined>;
    submanifoldLabels?: string[];
    neuralBridge?: {
      mode?: string;
      hiddenDim?: number;
      epochs?: number;
      loss?: number | null;
    };
  };
  redditSmallCap?: {
    status?: string;
    source?: string;
    subreddit?: string;
    fetchedAt?: string;
    heatScore?: number | null;
    regime?: string | null;
    postsAnalyzed?: number;
    smallCapPosts?: number;
    lowFloatPosts?: number;
    squeezePosts?: number;
    momentumPosts?: number;
    topTickers?: Array<{ symbol?: string; mentions?: number }>;
    topThemes?: Array<{ theme?: string; hits?: number }>;
    samplePosts?: Array<{
      title?: string;
      score?: number;
      comments?: number;
      permalink?: string | null;
      themes?: string[];
    }>;
    error?: string | null;
    path?: string;
  };
  fmkoreaStock?: FmkoreaStockSnapshot;
  championAgent?: {
    name?: string;
    method?: string;
    selectedProfile?: string;
    selectedLabel?: string;
    score?: number | null;
    continuityScore?: number | null;
    targetDistance?: number | null;
    projectedTarget?: {
      weightedUpsidePct?: number | null;
      weightedUncertaintyPct?: number | null;
      weightedVolatilityPct?: number | null;
      weightedDrawdownLingerDays?: number | null;
      weightedSpikeSustainDays?: number | null;
      weightedPersistencePct?: number | null;
      weightedRegimeRiskPct?: number | null;
      geometryAlignmentScore?: number | null;
      geometryDistance?: number | null;
      usEquitiesPct?: number | null;
      concentrationHhi?: number | null;
    };
    historyCount?: number;
    rank?: number;
    rationale?: string;
    candidateScores?: Array<{
      profile?: string;
      label?: string;
      score?: number | null;
      continuityScore?: number | null;
      targetDistance?: number | null;
    }>;
  };
  geometry?: Sp500PortfolioGeometryOverlay;
  naturalGradient?: Sp500PortfolioNaturalGradientOverlay;
  correlationForecast?: PortfolioCorrelationForecast | null;
  allocation?: {
    methodology?: string;
    macro?: {
      source?: string;
      m2LatestDate?: string | null;
      m2LevelBillions?: number | null;
      m2ThreeMonthPct?: number | null;
      m2YearPct?: number | null;
      policyRateLatestDate?: string | null;
      policyRatePct?: number | null;
      policyRateThreeMonthChangeBps?: number | null;
      policyRateYearChangeBps?: number | null;
      liquidityRegime?: string;
      rateRegime?: string;
      summary?: string;
    } | null;
    sleeves?: Sp500PortfolioSleeveRecommendation[];
    sectorMix?: Sp500PortfolioSectorRecommendation[];
    internationalMix?: Sp500PortfolioRegionRecommendation[];
    riskInputs?: {
      weightedUncertaintyPct?: number | null;
      weightedVolatilityPct?: number | null;
      weightedDrawdownLingerDays?: number | null;
      weightedMaxDrawdownPct?: number | null;
      weightedSpikeSustainDays?: number | null;
      weightedMaxSpikePct?: number | null;
      weightedPersistencePct?: number | null;
      weightedRegimeRiskPct?: number | null;
      weightedKoreanSurgeScore?: number | null;
    };
  };
  holdings: Sp500PortfolioHolding[];
  error?: string;
};

type InformationMapViewMode = "raw" | "firstCoordinate" | "secondCoordinate";

function getInformationMapViewMeta(viewMode: InformationMapViewMode) {
  if (viewMode === "firstCoordinate") {
    return {
      cardLabel: "Coordinate map",
      title: "1st coordinate map",
      subtitle: "Compressed market-geometry view",
    };
  }

  if (viewMode === "secondCoordinate") {
    return {
      cardLabel: "Coordinate map",
      title: "2nd coordinate map",
      subtitle: "Uncertainty-adjusted geometry",
    };
  }

  return {
    cardLabel: "Moment map",
    title: "1st vs 2nd moment map",
    subtitle: "Raw Prophet moment space",
  };
}

type GraphCluster = {
  id: string;
  label: string;
  caption: string;
  x: number;
  y: number;
  width: number;
  height: number;
};

type GraphNode = {
  id: string;
  x: number;
  y: number;
  eyebrow: string;
  title: string;
  stat: string;
  meta: string;
  status: Tone;
  description: string;
  highlights: { label: string; value: string }[];
  bullets: string[];
};

type GraphEdge = {
  from: string;
  to: string;
  tone: Tone;
  dashed?: boolean;
};

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function runWithConcurrency<T, R>(
  items: T[],
  limit: number,
  worker: (item: T, index: number) => Promise<R>
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let cursor = 0;

  async function runner() {
    while (true) {
      const currentIndex = cursor;
      cursor += 1;

      if (currentIndex >= items.length) {
        return;
      }

      results[currentIndex] = await worker(items[currentIndex], currentIndex);
    }
  }

  const runnerCount = Math.min(limit, items.length);
  await Promise.all(Array.from({ length: runnerCount }, () => runner()));
  return results;
}

function buildRouteKey(inputMint?: string | null, outputMint?: string | null) {
  return `${inputMint || "unknown"}:${outputMint || "unknown"}`;
}

function shortenAddress(value?: string | null) {
  if (!value) {
    return "Unknown";
  }

  if (value.length <= 10) {
    return value;
  }

  return `${value.slice(0, 4)}...${value.slice(-4)}`;
}

function formatNumber(value?: number | null, digits = 4) {
  if (value == null || !Number.isFinite(value)) {
    return "--";
  }

  return value.toLocaleString(undefined, {
    maximumFractionDigits: digits,
  });
}

function formatPercent(value?: number | string | null, digits = 4) {
  if (value == null || value === "") {
    return "--";
  }

  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return String(value);
  }

  return `${numeric.toLocaleString(undefined, {
    maximumFractionDigits: digits,
  })}%`;
}

function formatBasisPoints(value?: number | string | null) {
  if (value == null || value === "") {
    return "--";
  }

  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return String(value);
  }

  return `${numeric.toLocaleString(undefined, {
    maximumFractionDigits: 0,
  })} bps`;
}

function buildEquityRecommendationSummary(
  action?: StepPrediction["finalAction"],
  symbol?: string
) {
  const resolvedSymbol = symbol || "this equity";

  if (action === "BUY") {
    return `Model identifies positive momentum for ${resolvedSymbol} (Constructive Momentum).`;
  }

  if (action === "SELL") {
    return `Model identifies selling pressure or downside pressure for ${resolvedSymbol} (Exit Zone).`;
  }

  return `Model identifies range-bound dynamics for ${resolvedSymbol} (Risk Cooling).`;
}


function buildManualEquitySelection(
  symbol: string,
  name?: string,
  sector?: string
): EquitySearchResult {
  return {
    id: symbol,
    symbol,
    name: name || `Manual ticker ${symbol}`,
    sector: sector || "Information map",
    icon: FALLBACK_TOKEN_ICON,
  };
}

function getEquitySelectionCaption(equity?: EquitySearchResult | null) {
  if (!equity) {
    return "";
  }

  const normalizedSymbol = equity.symbol.trim().toUpperCase();
  const normalizedName = (equity.name || "").trim();
  const normalizedNameUpper = normalizedName.toUpperCase();

  if (!normalizedName) {
    return equity.sector || "";
  }

  if (
    normalizedNameUpper === normalizedSymbol ||
    normalizedNameUpper === `MANUAL TICKER ${normalizedSymbol}`
  ) {
    return equity.sector && equity.sector !== "Manual lookup" ? equity.sector : "";
  }

  return normalizedName;
}

function formatFractionPercent(value?: number | null, digits = 1) {
  if (value == null || !Number.isFinite(value)) {
    return "--";
  }

  return `${(value * 100).toLocaleString(undefined, {
    maximumFractionDigits: digits,
  })}%`;
}

function formatConfidence(value?: number | null) {
  if (value == null || !Number.isFinite(value)) {
    return "--";
  }

  return `${(value * 100).toLocaleString(undefined, {
    maximumFractionDigits: 1,
  })}%`;
}

function formatRegimeLabel(value?: unknown) {
  const normalized = String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, "_");

  if (!normalized) {
    return "--";
  }
  if (normalized === "bull") {
    return "Bull";
  }
  if (normalized === "bear") {
    return "Bear";
  }
  if (normalized === "neutral") {
    return "Neutral";
  }

  return normalized
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatUsdValue(value?: number | string | null) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "--";
  }

  return numeric.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
}

function formatDuration(seconds?: number | null) {
  if (seconds == null || !Number.isFinite(seconds)) {
    return "--";
  }

  if (seconds <= 0) {
    return "NOW";
  }

  if (seconds < 60) {
    return `${Math.floor(seconds)}s`;
  }

  if (seconds < 3600) {
    return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
  }

  if (seconds < 86400) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${minutes}m`;
  }

  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  return `${days}d ${hours}h`;
}

function formatEventTime(isoValue?: string | null) {
  if (!isoValue) {
    return "--";
  }

  const parsed = new Date(isoValue);
  if (Number.isNaN(parsed.getTime())) {
    return isoValue;
  }

  return parsed.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatTimestamp(value?: string | null) {
  if (!value) {
    return "--";
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function toToneFromAction(action?: string | null): Tone {
  if (action === "BUY") {
    return "positive";
  }

  if (action === "SELL") {
    return "negative";
  }

  if (action === "HOLD") {
    return "neutral";
  }

  return "active";
}

function formatActionLabel(action?: string | null): string {
  if (!action) return "Idle";
  const upper = action.toUpperCase();
  if (upper === "BUY") return "Constructive Momentum";
  if (upper === "SELL") return "Exit Zone";
  if (upper === "HOLD") return "Risk Cooling";
  return action;
}


function getPredictionTone(prediction?: StepPrediction | null): Tone {
  if (prediction && prediction.supported === false) {
    return "neutral";
  }

  if (!prediction?.supported) {
    return "neutral";
  }

  return toToneFromAction(
    prediction.recommendation?.tone?.toUpperCase?.() === "POSITIVE"
      ? "BUY"
      : prediction.recommendation?.tone?.toUpperCase?.() === "NEGATIVE"
        ? "SELL"
        : prediction.finalAction
  );
}

function findWrapperAgent(wrapper: WrapperSummary | null | undefined, agentName: string) {
  if (!wrapper?.agentOutputs) {
    return null;
  }

  return wrapper.agentOutputs.find((agent) => agent?.name === agentName) || null;
}

function getWrapperBaseWeight(agentName?: string | null) {
  if (!agentName) {
    return null;
  }

  return WRAPPER_BASE_WEIGHTS[agentName] ?? null;
}

function getWrapperLearnedWeight(
  wrapper: WrapperSummary | null | undefined,
  agentName?: string | null
) {
  if (!wrapper || !agentName) {
    return null;
  }

  return wrapper.weights?.[agentName] ?? null;
}

function formatSignedRatio(value?: number | null, digits = 4, unit = "") {
  if (value == null || !Number.isFinite(value)) {
    return "--";
  }

  const prefix = value > 0 ? "+" : "";
  return `${prefix}${value.toLocaleString(undefined, {
    maximumFractionDigits: digits,
  })}${unit}`;
}

function formatMomentPercentPerHour(value?: number | null) {
  if (value == null || !Number.isFinite(value)) {
    return "--";
  }

  return formatSignedRatio(value * 100, 3, "%/h");
}

function formatMomentPercentPerHour2(value?: number | null) {
  if (value == null || !Number.isFinite(value)) {
    return "--";
  }

  const magnitudeBpPerDay2 = Math.abs(value) * 10_000 * 24 * 24;
  const direction = value > 0 ? "accelerating" : "decelerating";

  let magnitudeLabel = "micro";
  if (magnitudeBpPerDay2 >= 2) {
    magnitudeLabel = "strong";
  } else if (magnitudeBpPerDay2 >= 0.75) {
    magnitudeLabel = "moderate";
  } else if (magnitudeBpPerDay2 >= 0.15) {
    magnitudeLabel = "light";
  }

  const signedBpPerDay2 = value * 10_000 * 24 * 24;
  return `${direction} ${magnitudeLabel} (${formatSignedRatio(signedBpPerDay2, 2, " bp/day²")})`;
}

function getPredictionLastClose(prediction?: StepPrediction | null) {
  const value = Number(prediction?.lastClosePrice ?? prediction?.currentPrice);
  return Number.isFinite(value) ? value : null;
}

function getPredictionLivePrice(prediction?: StepPrediction | null) {
  const value = Number(
    prediction?.livePrice ?? prediction?.lastClosePrice ?? prediction?.currentPrice
  );
  return Number.isFinite(value) ? value : null;
}

function buildTrendChartModel(prediction?: StepPrediction | null) {
  const forecastPoints = (prediction?.trendCurve || [])
    .map((point) => {
      const timestamp = point?.timestamp ? new Date(point.timestamp) : null;
      const value = Number(point?.value);
      if (!timestamp || Number.isNaN(timestamp.getTime()) || !Number.isFinite(value)) {
        return null;
      }
      return {
        timestamp,
        value,
      };
    })
    .filter((point): point is { timestamp: Date; value: number } => Boolean(point));

  const currentPrice = getPredictionLastClose(prediction);
  if (!forecastPoints.length || currentPrice == null || !Number.isFinite(currentPrice)) {
    return null;
  }

  const currentTimestamp = new Date();
  const points = [
    {
      kind: "current" as const,
      timestamp: currentTimestamp,
      value: currentPrice,
    },
    ...forecastPoints.map((point) => ({
      kind: "forecast" as const,
      timestamp: point.timestamp,
      value: point.value,
    })),
  ];

  const minValue = Math.min(...points.map((point) => point.value));
  const maxValue = Math.max(...points.map((point) => point.value));
  const paddedMin = minValue === maxValue ? minValue * 0.995 : minValue - (maxValue - minValue) * 0.08;
  const paddedMax = minValue === maxValue ? maxValue * 1.005 : maxValue + (maxValue - minValue) * 0.08;

  const width = 700;
  const height = 240;
  const paddingX = 18;
  const paddingY = 18;
  const innerWidth = width - paddingX * 2;
  const innerHeight = height - paddingY * 2;

  const startTime = points[0].timestamp.getTime();
  const endTime = points[points.length - 1].timestamp.getTime();
  const timeSpan = Math.max(1, endTime - startTime);
  const valueSpan = Math.max(1e-9, paddedMax - paddedMin);

  const projectX = (timestamp: Date) =>
    paddingX + ((timestamp.getTime() - startTime) / timeSpan) * innerWidth;
  const projectY = (value: number) =>
    paddingY + innerHeight - ((value - paddedMin) / valueSpan) * innerHeight;

  const linePath = points
    .map((point, index) => {
      const x = projectX(point.timestamp);
      const y = projectY(point.value);
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");

  const buyTimestamp = prediction?.optimalBuyTimestamp
    ? new Date(prediction.optimalBuyTimestamp)
    : null;
  const sellTimestamp = prediction?.optimalSellTimestamp
    ? new Date(prediction.optimalSellTimestamp)
    : null;
  const buyValid = buyTimestamp && !Number.isNaN(buyTimestamp.getTime());
  const sellValid = sellTimestamp && !Number.isNaN(sellTimestamp.getTime());

  const yTicks = [paddedMax, (paddedMax + paddedMin) / 2, paddedMin];

  return {
    width,
    height,
    linePath,
    currentPoint: {
      x: projectX(points[0].timestamp),
      y: projectY(points[0].value),
      value: points[0].value,
    },
    lastPoint: {
      x: projectX(points[points.length - 1].timestamp),
      y: projectY(points[points.length - 1].value),
      value: points[points.length - 1].value,
    },
    buyMarker:
      buyValid && prediction?.optimalBuyPrice != null
        ? {
            x: projectX(buyTimestamp),
            y: projectY(Number(prediction.optimalBuyPrice)),
            label: "Best buy",
            value: Number(prediction.optimalBuyPrice),
          }
        : null,
    sellMarker:
      sellValid && prediction?.optimalSellPrice != null
        ? {
            x: projectX(sellTimestamp),
            y: projectY(Number(prediction.optimalSellPrice)),
            label: "Best sell",
            value: Number(prediction.optimalSellPrice),
          }
        : null,
    currentLineY: projectY(currentPrice),
    yTicks,
  };
}

function buildStockForecastChartModel(prediction?: StepPrediction | null) {
  const points = (prediction?.forecastPlot?.points || [])
    .map((point) => {
      if (!point?.timestamp) {
        return null;
      }
      const timestamp = new Date(point.timestamp);
      if (Number.isNaN(timestamp.getTime())) {
        return null;
      }
      const yhat = Number(point.yhat);
      const yhatLower = Number(point.yhatLower);
      const yhatUpper = Number(point.yhatUpper);
      const actual = Number(point.actual);
      const trend = Number(point.trend);
      if (!Number.isFinite(yhat)) {
        return null;
      }
      return {
        timestamp,
        yhat,
        yhatLower: Number.isFinite(yhatLower) ? yhatLower : null,
        yhatUpper: Number.isFinite(yhatUpper) ? yhatUpper : null,
        actual: Number.isFinite(actual) ? actual : null,
        trend: Number.isFinite(trend) ? trend : null,
        isHistory: Boolean(point.isHistory),
      };
    })
    .filter(
      (
        point
      ): point is {
        timestamp: Date;
        yhat: number;
        yhatLower: number | null;
        yhatUpper: number | null;
        actual: number | null;
        trend: number | null;
        isHistory: boolean;
      } => Boolean(point)
    );

  if (points.length < 2) {
    return null;
  }

  const width = 700;
  const height = 280;
  const paddingX = 18;
  const paddingY = 18;
  const innerWidth = width - paddingX * 2;
  const innerHeight = height - paddingY * 2;

  const allValues = points.flatMap((point) =>
    [point.yhat, point.yhatLower, point.yhatUpper, point.actual].filter(
      (value): value is number => Number.isFinite(value as number)
    )
  );
  const minValue = Math.min(...allValues);
  const maxValue = Math.max(...allValues);
  const paddedMin = minValue === maxValue ? minValue * 0.995 : minValue - (maxValue - minValue) * 0.08;
  const paddedMax = minValue === maxValue ? maxValue * 1.005 : maxValue + (maxValue - minValue) * 0.08;

  const startTime = points[0].timestamp.getTime();
  const endTime = points[points.length - 1].timestamp.getTime();
  const timeSpan = Math.max(1, endTime - startTime);
  const valueSpan = Math.max(1e-9, paddedMax - paddedMin);

  const projectX = (timestamp: Date) =>
    paddingX + ((timestamp.getTime() - startTime) / timeSpan) * innerWidth;
  const projectY = (value: number) =>
    paddingY + innerHeight - ((value - paddedMin) / valueSpan) * innerHeight;

  const forecastPath = points
    .map((point, index) => {
      const x = projectX(point.timestamp);
      const y = projectY(point.yhat);
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");

  const actualPoints = points.filter((point) => point.actual != null);
  const actualPath =
    actualPoints.length >= 2
      ? actualPoints
          .map((point, index) => {
            const x = projectX(point.timestamp);
            const y = projectY(point.actual as number);
            return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
          })
          .join(" ")
      : "";

  const upperBand = points
    .filter((point) => point.yhatUpper != null)
    .map((point) => `${projectX(point.timestamp).toFixed(2)} ${projectY(point.yhatUpper as number).toFixed(2)}`);
  const lowerBand = [...points]
    .reverse()
    .filter((point) => point.yhatLower != null)
    .map((point) => `${projectX(point.timestamp).toFixed(2)} ${projectY(point.yhatLower as number).toFixed(2)}`);
  const bandPath =
    upperBand.length >= 2 && lowerBand.length >= 2
      ? `M ${upperBand.join(" L ")} L ${lowerBand.join(" L ")} Z`
      : "";

  const changepoints = (prediction?.forecastPlot?.changepoints || [])
    .map((point) => {
      if (!point?.timestamp) {
        return null;
      }
      const timestamp = new Date(point.timestamp);
      if (Number.isNaN(timestamp.getTime())) {
        return null;
      }
      return {
        x: projectX(timestamp),
        label: timestamp.toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
        }),
        magnitude: Number(point.magnitude),
      };
    })
    .filter(
      (
        point
      ): point is {
        x: number;
        label: string;
        magnitude: number;
      } => Boolean(point)
    );

  const buyTimestamp = prediction?.optimalBuyTimestamp
    ? new Date(prediction.optimalBuyTimestamp)
    : null;
  const sellTimestamp = prediction?.optimalSellTimestamp
    ? new Date(prediction.optimalSellTimestamp)
    : null;
  const buyValid = buyTimestamp && !Number.isNaN(buyTimestamp.getTime());
  const sellValid = sellTimestamp && !Number.isNaN(sellTimestamp.getTime());

  const yTicks = [paddedMax, (paddedMax + paddedMin) / 2, paddedMin];
  const tickIndices =
    points.length <= 6
      ? points.map((_, index) => index)
      : Array.from(
          new Set([
            0,
            Math.floor((points.length - 1) / 4),
            Math.floor(((points.length - 1) * 2) / 4),
            Math.floor(((points.length - 1) * 3) / 4),
            points.length - 1,
          ])
        );

  return {
    width,
    height,
    forecastPath,
    actualPath,
    bandPath,
    hasActualPath: Boolean(actualPath),
    changepoints,
    yTicks,
    xTicks: tickIndices.map((index) => ({
      x: projectX(points[index].timestamp),
      label: points[index].timestamp.toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
      }),
    })),
    currentLineY: projectY(activePredictionLastCloseValue(prediction)),
    currentValue: activePredictionLastCloseValue(prediction),
    lastForecastValue: points[points.length - 1].yhat,
    buyMarker:
      buyValid && prediction?.optimalBuyPrice != null
        ? {
            x: projectX(buyTimestamp),
            y: projectY(Number(prediction.optimalBuyPrice)),
            label: "Best buy",
            value: Number(prediction.optimalBuyPrice),
          }
        : null,
    sellMarker:
      sellValid && prediction?.optimalSellPrice != null
        ? {
            x: projectX(sellTimestamp),
            y: projectY(Number(prediction.optimalSellPrice)),
            label: "Best sell",
            value: Number(prediction.optimalSellPrice),
          }
        : null,
  };
}

function activePredictionLastCloseValue(prediction?: StepPrediction | null) {
  const candidate = prediction?.lastClosePrice ?? prediction?.currentPrice;
  return Number.isFinite(Number(candidate)) ? Number(candidate) : 0;
}

function formatSeasonalityValue(
  value: number | null | undefined,
  valueType: ProphetComponentSeries["valueType"]
) {
  if (value == null || !Number.isFinite(value)) {
    return "--";
  }
  if (valueType === "percent") {
    return formatSignedRatio(value * 100, 2, "%");
  }
  return formatNumber(value, 2);
}

function buildComponentChartModel(component?: ProphetComponentSeries | null) {
  const points = (component?.points || [])
    .map((point) => {
      const value = Number(point?.value);
      if (!Number.isFinite(value)) {
        return null;
      }
      return {
        value,
        label:
          typeof point?.label === "string" && point.label
            ? point.label
            : point?.timestamp
              ? new Date(point.timestamp).toLocaleDateString(undefined, {
                  month: "short",
                  day: "numeric",
                })
              : "",
      };
    })
    .filter((point): point is { value: number; label: string } => Boolean(point));

  if (points.length < 2) {
    return null;
  }

  const width = 700;
  const height = 190;
  const paddingX = 18;
  const paddingY = 18;
  const innerWidth = width - paddingX * 2;
  const innerHeight = height - paddingY * 2;

  const minValue = Math.min(...points.map((point) => point.value));
  const maxValue = Math.max(...points.map((point) => point.value));
  const pad =
    minValue === maxValue
      ? Math.max(1e-6, Math.abs(minValue) * 0.15 || 0.01)
      : (maxValue - minValue) * 0.12;
  const paddedMin = minValue - pad;
  const paddedMax = maxValue + pad;
  const valueSpan = Math.max(1e-9, paddedMax - paddedMin);

  const projectX = (index: number) =>
    paddingX + (index / Math.max(1, points.length - 1)) * innerWidth;
  const projectY = (value: number) =>
    paddingY + innerHeight - ((value - paddedMin) / valueSpan) * innerHeight;

  const linePath = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${projectX(index).toFixed(2)} ${projectY(point.value).toFixed(2)}`)
    .join(" ");

  const tickIndices =
    points.length <= 8
      ? points.map((_, index) => index)
      : Array.from(
          new Set([
            0,
            Math.floor((points.length - 1) / 3),
            Math.floor(((points.length - 1) * 2) / 3),
            points.length - 1,
          ])
        );

  const yTicks = [paddedMax, (paddedMax + paddedMin) / 2, paddedMin];
  const peakPoint = points.reduce((best, point) => (point.value > best.value ? point : best), points[0]);
  const troughPoint = points.reduce((best, point) => (point.value < best.value ? point : best), points[0]);

  return {
    width,
    height,
    linePath,
    valueType: component?.valueType || "percent",
    title: component?.title || "Component",
    xAxisLabel: component?.xAxisLabel || "Axis",
    yAxisLabel: component?.yAxisLabel || "Value",
    xTicks: tickIndices.map((index) => ({
      x: projectX(index),
      label: points[index]?.label || "",
    })),
    yTicks,
    peakPoint: {
      x: projectX(points.indexOf(peakPoint)),
      y: projectY(peakPoint.value),
      label: peakPoint.label,
      value: peakPoint.value,
    },
    troughPoint: {
      x: projectX(points.indexOf(troughPoint)),
      y: projectY(troughPoint.value),
      label: troughPoint.label,
      value: troughPoint.value,
    },
  };
}

function buildStockSeasonalityCharts(prediction?: StepPrediction | null) {
  const charts: Array<ReturnType<typeof buildComponentChartModel> & { key: string }> = [];

  const trendChart = buildComponentChartModel(prediction?.trendComponent || null);
  if (trendChart) {
    charts.push({
      ...trendChart,
      key: "trend",
    });
  }

  const componentOrder = ["weekly", "yearly", "monthly", "quarterly"];
  componentOrder.forEach((key) => {
    const chart = buildComponentChartModel(prediction?.seasonalityComponents?.[key] || null);
    if (chart) {
      charts.push({
        ...chart,
        key,
      });
    }
  });

  return charts;
}

function buildInformationMapChartModel(
  informationMap?: Sp500InformationMapResult | null,
  highlightedSymbol?: string | null,
  viewMode: InformationMapViewMode = "raw",
  geometry?: Sp500PortfolioGeometryOverlay | null
) {
  const sourcePoints =
    informationMap?.points?.length
      ? informationMap.points
      : informationMap?.topPicks?.length
        ? informationMap.topPicks
        : [];

  const points = sourcePoints
    .map((point) => {
      let x: number;
      let y: number;
      if (viewMode === "secondCoordinate") {
        const space = point.secondCoordinateSpace || {};
        x = Number(space.x);
        y = Number(space.y);
      } else if (viewMode === "firstCoordinate") {
        const space = point.firstCoordinateSpace || {};
        x = Number(space.x);
        y = Number(space.y);
      } else {
        x = Number(point.firstMomentPctPerDay ?? point.directionScore);
        y = Number(point.secondMomentBpPerDay2 ?? point.optimizationScore);
      }
      const score = Number(point.optimizationScore);
      if (!Number.isFinite(x) || !Number.isFinite(y)) {
        return null;
      }
      return {
        ...point,
        x,
        y,
        score: Number.isFinite(score) ? score : 0,
      };
    })
    .filter(
      (
        point
      ): point is Sp500InformationMapPoint & { x: number; y: number; score: number } =>
        Boolean(point)
    );

  if (!points.length) {
    return null;
  }

  const width = 700;
  const height = 360;
  const padding = 42;
  const innerWidth = width - padding * 2;
  const innerHeight = height - padding * 2;

  const extraGeometryX =
    viewMode === "secondCoordinate"
      ? [Number(geometry?.targetPoint?.x), Number(geometry?.portfolioPoint?.x)].filter((value) =>
          Number.isFinite(value)
        )
      : [];
  const extraGeometryY =
    viewMode === "secondCoordinate"
      ? [Number(geometry?.targetPoint?.y), Number(geometry?.portfolioPoint?.y)].filter((value) =>
          Number.isFinite(value)
        )
      : [];
  const xValues = [...points.map((point) => point.x), ...extraGeometryX];
  const yValues = [...points.map((point) => point.y), ...extraGeometryY];
  const rawMinX = Math.min(...xValues);
  const rawMaxX = Math.max(...xValues);
  const rawMinY = Math.min(...yValues);
  const rawMaxY = Math.max(...yValues);

  const padX = rawMinX === rawMaxX ? Math.max(0.1, Math.abs(rawMinX) * 0.2) : (rawMaxX - rawMinX) * 0.12;
  const padY = rawMinY === rawMaxY ? Math.max(0.1, Math.abs(rawMinY) * 0.2) : (rawMaxY - rawMinY) * 0.12;
  const minX = rawMinX - padX;
  const maxX = rawMaxX + padX;
  const minY = rawMinY - padY;
  const maxY = rawMaxY + padY;

  const projectX = (value: number) =>
    padding + ((value - minX) / Math.max(1e-9, maxX - minX)) * innerWidth;
  const projectY = (value: number) =>
    padding + innerHeight - ((value - minY) / Math.max(1e-9, maxY - minY)) * innerHeight;

  const xZero = projectX(0);
  const yZero = projectY(0);
  const topPickSymbols = new Set((informationMap?.topPicks || []).map((point) => point.symbol));
  const geometryTarget =
    viewMode === "secondCoordinate" && geometry?.targetPoint
      ? {
          x: Number(geometry.targetPoint.x),
          y: Number(geometry.targetPoint.y),
          label: geometry.targetPoint.label || "Geometry target",
        }
      : null;
  const geometryPortfolio =
    viewMode === "secondCoordinate" && geometry?.portfolioPoint
      ? {
          x: Number(geometry.portfolioPoint.x),
          y: Number(geometry.portfolioPoint.y),
          label: geometry.portfolioPoint.label || "Optimized portfolio",
        }
      : null;
  const geometryTargetValid =
    geometryTarget && Number.isFinite(geometryTarget.x) && Number.isFinite(geometryTarget.y)
      ? {
          ...geometryTarget,
          cx: projectX(geometryTarget.x),
          cy: projectY(geometryTarget.y),
        }
      : null;
  const geometryPortfolioValid =
    geometryPortfolio && Number.isFinite(geometryPortfolio.x) && Number.isFinite(geometryPortfolio.y)
      ? {
          ...geometryPortfolio,
          cx: projectX(geometryPortfolio.x),
          cy: projectY(geometryPortfolio.y),
        }
      : null;
  const projectionLinePath =
    geometryTargetValid && geometryPortfolioValid
      ? `M ${geometryPortfolioValid.cx.toFixed(2)} ${geometryPortfolioValid.cy.toFixed(2)} L ${geometryTargetValid.cx.toFixed(2)} ${geometryTargetValid.cy.toFixed(2)}`
      : null;
  const frontierPoints =
    viewMode === "secondCoordinate" && Array.isArray(geometry?.frontierLine)
      ? geometry.frontierLine
          .map((point) => {
            const x = Number(point.x);
            const y = Number(point.y);
            if (!Number.isFinite(x) || !Number.isFinite(y)) {
              return null;
            }
            return {
              ...point,
              cx: projectX(x),
              cy: projectY(y),
            };
          })
          .filter(Boolean) as Array<Sp500PortfolioGeometryPoint & { cx: number; cy: number }>
      : [];
  const frontierLinePath =
    frontierPoints.length >= 2
      ? frontierPoints
          .map((point, index) => `${index === 0 ? "M" : "L"} ${point.cx.toFixed(2)} ${point.cy.toFixed(2)}`)
          .join(" ")
      : null;

  return {
    width,
    height,
    viewMode,
    xZero,
    yZero,
    xTicks: [minX, 0, maxX],
    yTicks: [maxY, 0, minY],
    title:
      viewMode === "secondCoordinate"
        ? "2nd coordinate map"
        : viewMode === "firstCoordinate"
          ? "1st coordinate map"
          : "1st vs 2nd moment map",
    subtitle:
      viewMode === "secondCoordinate"
        ? "Uncertainty-adjusted geometry"
        : viewMode === "firstCoordinate"
          ? "Compressed market-geometry view"
          : "Raw Prophet moment space",
    xAxisLabel:
      viewMode === "secondCoordinate"
        ? informationMap?.mapSpaces?.secondCoordinate?.xAxis || "2nd coordinate x"
        : viewMode === "firstCoordinate"
          ? informationMap?.mapSpaces?.firstCoordinate?.xAxis || "1st coordinate x"
          : "1st moment (%/day)",
    yAxisLabel:
      viewMode === "secondCoordinate"
        ? informationMap?.mapSpaces?.secondCoordinate?.yAxis || "2nd coordinate y"
        : viewMode === "firstCoordinate"
          ? informationMap?.mapSpaces?.firstCoordinate?.yAxis || "1st coordinate y"
          : "2nd moment (bp/day²)",
    xTickUnit: viewMode === "raw" ? "%/day" : "",
    yTickUnit: viewMode === "raw" ? " bp/day²" : "",
    projectionLinePath,
    frontierLinePath,
    geometryTarget: geometryTargetValid,
    geometryPortfolio: geometryPortfolioValid,
    points: points.map((point) => ({
      ...point,
      cx: projectX(point.x),
      cy: projectY(point.y),
      radius: 4 + Math.max(0, Math.min(4, point.score + 2)),
      highlighted: point.symbol === highlightedSymbol,
      topPick: topPickSymbols.has(point.symbol),
    })),
  };
}

function summarizeRoutePlan(
  routePlan: any[],
  selectedToken: TokenSearchResult | null,
  tokenMap: Record<string, any>
): RouteSummaryLeg[] {
  if (!Array.isArray(routePlan) || routePlan.length === 0) {
    return [];
  }

  const formatMintLabel = (mint?: string | null) => {
    if (!mint) {
      return "Unknown";
    }

    if (mint === SOL_MINT) {
      return "SOL";
    }

    if (selectedToken?.id === mint || selectedToken?.symbol === tokenMap[mint]?.symbol) {
      return selectedToken?.symbol || tokenMap[mint]?.symbol || shortenAddress(mint);
    }

    return tokenMap[mint]?.symbol || shortenAddress(mint);
  };

  return routePlan.map((leg, index) => {
    const markets = Array.isArray(leg?.marketInfos)
      ? leg.marketInfos
      : leg?.swapInfo
        ? [leg.swapInfo]
        : [];
    const firstMarket = markets[0];
    const lastMarket = markets[markets.length - 1];
    const inputMint = firstMarket?.inputMint ?? leg?.swapInfo?.inputMint;
    const outputMint = lastMarket?.outputMint ?? leg?.swapInfo?.outputMint;
    const stepKey = buildRouteKey(inputMint, outputMint);
    const labels = markets.map((market: any) => market?.label).filter(Boolean);
    const path = markets
      .map((market: any) => {
        const inputLabel = formatMintLabel(market?.inputMint);
        const outputLabel = formatMintLabel(market?.outputMint);
        return inputLabel && outputLabel ? `${inputLabel} -> ${outputLabel}` : "";
      })
      .filter(Boolean)
      .join(" | ");

    return {
      id: `route-leg-${index}`,
      stepKey,
      label: labels.join(" -> ") || leg?.label || `Leg ${index + 1}`,
      share:
        leg?.percent != null
          ? formatPercent(leg.percent, 2)
          : leg?.bps != null
            ? formatPercent(Number(leg.bps) / 100, 2)
            : "",
      path,
      usdValue: formatUsdValue(leg?.usdValue),
      inputMintLabel: formatMintLabel(inputMint),
      outputMintLabel: formatMintLabel(outputMint),
    };
  });
}

function createAgentEvent(
  title: string,
  detail: string,
  tone: Tone,
  nodeId: string
): AgentEvent {
  return {
    id: `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
    title,
    detail,
    tone,
    nodeId,
    createdAt: new Date().toISOString(),
  };
}

const INITIAL_AGENT_EVENT: AgentEvent = {
  id: "workflow-armed",
  title: "S&P500 workflow armed",
  detail: "Search an S&P500 ticker and run Analyze to light up the prediction graph.",
  tone: "neutral",
  nodeId: "source",
  createdAt: null,
};

const INITIAL_PORTFOLIO_CHAT_MESSAGE: PortfolioChatMessage = {
  id: "portfolio-chat-intro",
  role: "assistant",
  text:
    "CSV 포트폴리오를 올리면 현재 정보맵, 최적 포트폴리오, 다크호스 후보와 비교해서 keep / reduce / exit / add 관점으로 재구성 제안을 드립니다.",
  fileName: null,
  analysis: null,
};

function createPortfolioChatMessageId() {
  return `portfolio-chat-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
}

export default function Page() {
  const [sp500OnlyMode, setSp500OnlyMode] = useState(true);
  const [marketMode, setMarketMode] = useState<MarketMode>("sp500");
  const [userProfile, setUserProfile] = useState<{ userId: string; credits: number; plan: string } | null>(null);
  const [isChargingCredits, setIsChargingCredits] = useState(false);

  const fetchUserProfile = async () => {
    try {
      const res = await fetch("/api/user/profile");
      if (res.ok) {
        const profile = await res.json();
        setUserProfile(profile);
      }
    } catch (err) {
      console.error("Failed to fetch user profile:", err);
    }
  };

  const handleTossPayment = async (type: "plan" | "credits", value: string | number, amountKrw: number) => {
    setIsChargingCredits(true);
    try {
      const clientKey = process.env.NEXT_PUBLIC_TOSS_CLIENT_KEY || "test_ck_Ba5PzR0ArnBjgwDN611orvmYnNeD";
      const { loadTossPayments } = await import("@tosspayments/payment-sdk");
      const tossPayments = await loadTossPayments(clientKey);

      const userId = "default-saas-user";
      const orderId = `user__${userId}__${type}__${value}__${Date.now()}`;
      const orderName = type === "plan" ? `${value} Plan Subscription` : `Prophet ${value} Credits Top-up`;

      await tossPayments.requestPayment("카드", {
        amount: amountKrw,
        orderId,
        orderName,
        successUrl: `${window.location.origin}/api/billing/toss-success`,
        failUrl: `${window.location.origin}/api/billing/toss-fail`,
      });
    } catch (error) {
      console.error("Toss Payments checkout error:", error);
      alert("Failed to initialize Toss Payments.");
    } finally {
      setIsChargingCredits(false);
    }
  };

  const handleSimulatePayment = async (type: string, details: any) => {
    setIsChargingCredits(true);
    try {
      const res = await fetch("/api/billing/webhook", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          type,
          ...details,
          userId: 'default-saas-user'
        }),
      });
      if (res.ok) {
        fetchUserProfile();
      } else {
        alert("Failed to process payment.");
      }
    } catch (error) {
      console.error("Payment error:", error);
    } finally {
      setIsChargingCredits(false);
    }
  };
  const [query, setQuery] = useState("");
  const [tokens, setTokens] = useState<TokenSearchResult[]>([]);
  const [equities, setEquities] = useState<EquitySearchResult[]>([]);
  const [selectedToken, setSelectedToken] = useState<TokenSearchResult | null>(null);
  const [selectedEquity, setSelectedEquity] = useState<EquitySearchResult | null>(null);
  const [tokenMap, setTokenMap] = useState<Record<string, any>>({});
  const [routes, setRoutes] = useState<any[]>([]);
  const [priceImpact, setPriceImpact] = useState<string | null>(null);
  const [predictedLoss, setPredictedLoss] = useState<number | null>(null);
  const [optimalEta, setOptimalEta] = useState<number | null>(null);
  const [startTime, setStartTime] = useState<number | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysisMessage, setAnalysisMessage] = useState(
    "Search an S&P500 ticker to light up the local Prophet workflow."
  );
  const [analysisError, setAnalysisError] = useState("");
  const [stepPredictions, setStepPredictions] = useState<StepPrediction[]>([]);
  const [focusedStepKey, setFocusedStepKey] = useState<string | null>(null);
  const [analysisMeta, setAnalysisMeta] = useState({
    duplicateCount: 0,
    uniqueStepCount: 0,
  });
  const [selectedGraphNodeId, setSelectedGraphNodeId] = useState("decision");
  const [agentEvents, setAgentEvents] = useState<AgentEvent[]>([INITIAL_AGENT_EVENT]);
  const [llm, setLlm] = useState<Record<string, string> | null>(null);
  const [sp500InformationMap, setSp500InformationMap] =
    useState<Sp500InformationMapResult | null>(null);
  const [isBuildingSp500Map, setIsBuildingSp500Map] = useState(false);
  const [sp500InformationMapError, setSp500InformationMapError] = useState("");
  const [sp500Portfolio, setSp500Portfolio] =
    useState<Sp500PortfolioResult | null>(null);
  const [isBuildingSp500Portfolio, setIsBuildingSp500Portfolio] = useState(false);
  const [sp500PortfolioError, setSp500PortfolioError] = useState("");
  const [portfolioChatPrompt, setPortfolioChatPrompt] = useState("");
  const [portfolioChatFile, setPortfolioChatFile] = useState<File | null>(null);
  const [portfolioChatInputKey, setPortfolioChatInputKey] = useState(0);
  const [isPortfolioChatting, setIsPortfolioChatting] = useState(false);
  const [portfolioChatError, setPortfolioChatError] = useState("");
  const [portfolioChatMessages, setPortfolioChatMessages] = useState<PortfolioChatMessage[]>([
    INITIAL_PORTFOLIO_CHAT_MESSAGE,
  ]);
  const [selectedInformationMapSymbol, setSelectedInformationMapSymbol] =
    useState<string | null>(null);
  const [selectedExecutionMode, setSelectedExecutionMode] =
    useState<ExecutionActionMode | null>(null);
  const [, setTicker] = useState(0);

  useEffect(() => {
    const shouldLock = shouldLockToSp500OnThisHost();
    setSp500OnlyMode(shouldLock);

    if (shouldLock) {
      setMarketMode("sp500");
      setSelectedToken(null);
      setTokens([]);
      setAnalysisMessage(
        "Localhost S&P500-only mode is active. Search a ticker such as AAPL, MSFT, NVDA, or TSLA."
      );
    }
    fetchUserProfile();

    // Check payment URL query parameters
    const params = new URLSearchParams(window.location.search);
    const paymentStatus = params.get("payment");
    if (paymentStatus === "success") {
      const type = params.get("type");
      const value = params.get("value");
      alert(
        type === "plan"
          ? `Subscribed successfully to the ${value} plan!`
          : `Charged ${value} credits successfully!`
      );
      // Clean up the URL query parameters
      window.history.replaceState({}, document.title, window.location.pathname);
    } else if (paymentStatus === "fail") {
      const code = params.get("code") || "ERROR";
      const message = params.get("message") || "Payment failed";
      alert(`Payment failed: [${code}] ${message}`);
      window.history.replaceState({}, document.title, window.location.pathname);
    }
  }, []);

  useEffect(() => {
    if (marketMode !== "crypto" || sp500OnlyMode) {
      return;
    }

    const fetchMap = async () => {
      try {
        const res = await fetch("https://cache.jup.ag/tokens", { cache: "force-cache" });
        const data = await res.json();
        const nextMap: Record<string, any> = {};
        data.forEach((token: any) => {
          nextMap[token.address] = token;
        });
        setTokenMap(nextMap);
      } catch (error) {
        console.error("Failed to fetch token map:", error);
      }
    };

    fetchMap();
  }, [marketMode, sp500OnlyMode]);

  useEffect(() => {
    const run = async () => {
      if (marketMode === "crypto") {
        if (query.length < 2 || query === selectedToken?.symbol) {
          setTokens([]);
          return;
        }

        const result = await searchTokens(query);
        setTokens(result);
        setEquities([]);
        return;
      }

      if (query.length < 1 || query === selectedEquity?.symbol) {
        setEquities([]);
        return;
      }

      const result = await searchSp500Equities(query);
      setEquities(result);
      setTokens([]);
    };

    run();
  }, [marketMode, query, selectedToken?.symbol, selectedEquity?.symbol]);

  useEffect(() => {
    if (!optimalEta || !startTime) {
      return;
    }

    const intervalId = window.setInterval(() => {
      setTicker((value) => value + 1);
    }, 1000);

    return () => window.clearInterval(intervalId);
  }, [optimalEta, startTime]);

  const appendEvent = (title: string, detail: string, tone: Tone, nodeId: string) => {
    setAgentEvents((currentEvents) => [
      createAgentEvent(title, detail, tone, nodeId),
      ...currentEvents,
    ].slice(0, 24));
  };

  const resetAnalysisSurface = (message: string) => {
    setRoutes([]);
    setPriceImpact(null);
    setPredictedLoss(null);
    setOptimalEta(null);
    setStartTime(null);
    setAnalysisError("");
    setAnalysisMessage(message);
    setStepPredictions([]);
    setFocusedStepKey(null);
    setAnalysisMeta({
      duplicateCount: 0,
      uniqueStepCount: 0,
    });
    setSelectedGraphNodeId("source");
    setLlm(null);
    setSelectedExecutionMode(null);
    setPortfolioChatFile(null);
    setPortfolioChatError("");
    setPortfolioChatInputKey((value) => value + 1);
  };

  const handleMarketModeChange = (nextMode: MarketMode) => {
    if (sp500OnlyMode && nextMode !== "sp500") {
      setMarketMode("sp500");
      setAnalysisMessage("Localhost is locked to S&P500-only mode.");
      appendEvent(
        "S&P500-only lock",
        "Crypto routes are disabled on localhost so the local setup only runs S&P500 functions.",
        "neutral",
        "source"
      );
      return;
    }

    setMarketMode(nextMode);
    setQuery("");
    setTokens([]);
    setEquities([]);
    setSelectedToken(null);
    setSelectedEquity(null);
    resetAnalysisSurface(
      nextMode === "crypto"
        ? "Search for a token to map its Jupiter route and AI workflow."
        : "Search an S&P500 ticker or ETF to run a direct Prophet signal."
    );
    appendEvent(
      "Market mode changed",
      nextMode === "crypto"
        ? "Crypto route mode is active."
        : "S&P500 signal mode is active.",
      "active",
      "source"
    );
  };

  const getSymbol = (mint?: string | null) => {
    if (!mint) {
      return "Unknown";
    }

    if (mint === SOL_MINT) {
      return "SOL";
    }

    return tokenMap[mint]?.symbol || shortenAddress(mint);
  };

  const getLiveEta = () => {
    if (!optimalEta || !startTime) {
      return "--";
    }

    const elapsedSeconds = (Date.now() - startTime) / 1000;
    return formatDuration(Math.max(0, optimalEta - elapsedSeconds));
  };

  const handleSelectToken = (token: TokenSearchResult) => {
    setSelectedToken(token);
    setSelectedEquity(null);
    setQuery(token.symbol);
    setTokens([]);
    setEquities([]);
    resetAnalysisSurface(`${token.symbol} selected. Analyze to inspect the route graph.`);
    appendEvent(
      "Token selected",
      `${token.symbol} is now the active target for route analysis.`,
      "active",
      "source"
    );
  };

  const handleSelectEquity = (equity: EquitySearchResult) => {
    setSelectedEquity(equity);
    setSelectedToken(null);
    setQuery(equity.symbol);
    setEquities([]);
    setTokens([]);
    setSelectedInformationMapSymbol(equity.symbol);
    resetAnalysisSurface(`${equity.symbol} selected. Analyze to inspect the direct signal graph.`);
    appendEvent(
      "Equity selected",
      `${equity.symbol} is now the active S&P500-focused target for direct analysis.`,
      "active",
      "source"
    );
  };

  const handleAnalyze = async () => {
    if (!selectedToken) {
      return;
    }

    setIsAnalyzing(true);
    setAnalysisError("");
    setAnalysisMessage(`Fetching Jupiter routes for ${selectedToken.symbol}...`);
    setPredictedLoss(null);
    setOptimalEta(null);
    setStartTime(Date.now());
    setStepPredictions([]);
    setFocusedStepKey(null);
    setSelectedGraphNodeId("route");
    setLlm(null);
    setSelectedExecutionMode(null);

    try {
      const quote = await getJupiterQuote(
        SOL_MINT,
        selectedToken.id,
        INPUT_SOL_AMOUNT_LAMPORTS
      );

      if (!quote) {
        setRoutes([]);
        setPriceImpact(null);
        setAnalysisMessage(`No Jupiter quote was returned for ${selectedToken.symbol}.`);
        appendEvent(
          "Quote unavailable",
          `Jupiter did not return a route for ${selectedToken.symbol}.`,
          "negative",
          "route"
        );
        return;
      }

      const routePlan = Array.isArray(quote.routePlan) ? quote.routePlan : [];
      const impactPct = parseFloat(quote.priceImpactPct || "0");
      const currentLoss = 150 * (impactPct / 100);
      const routeNotionalUsd = routePlan.reduce(
        (sum: number, leg: any) => sum + Math.max(0, Number(leg?.usdValue) || 0),
        0
      );

      setRoutes(routePlan);
      setPriceImpact(quote.priceImpactPct || "0");
      appendEvent(
        "Route loaded",
        `${routePlan.length} route entries arrived from Jupiter for ${selectedToken.symbol}.`,
        "positive",
        "route"
      );

      if (currentLoss < 0.0000001) {
        const riskSnapshot = assessExecutionRisk({
          routePlan,
          priceImpactPct: impactPct,
          activePrediction: null,
          tradeNotionalUsd: routeNotionalUsd,
        });
        setPredictedLoss(currentLoss);
        setOptimalEta(0);
        setAnalysisMessage(
          `Current route impact is negligible for ${selectedToken.symbol}.`
        );
        setSelectedGraphNodeId("risk");
        appendEvent(
          "Execute now",
          "Current route impact is effectively zero, so there is no reason to wait.",
          "positive",
          "route"
        );
        appendEvent(
          "Risk score computed",
          `${selectedToken.symbol} opened at ${Math.round(
            riskSnapshot.score
          )}/100 with a ${riskSnapshot.tier.toLowerCase()} execution regime.`,
          riskSnapshot.tone,
          "risk"
        );
        await reportFinalRecommendation(0, currentLoss, currentLoss);
        return;
      }

      const seenPairs = new Set<string>();
      const uniqueSteps: RouteAnalysisStep[] = routePlan.reduce(
        (steps: RouteAnalysisStep[], step: any) => {
          const inputMint = step?.swapInfo?.inputMint;
          const outputMint = step?.swapInfo?.outputMint;

          if (!inputMint || !outputMint) {
            return steps;
          }

          const stepKey = buildRouteKey(inputMint, outputMint);
          if (seenPairs.has(stepKey)) {
            return steps;
          }

          seenPairs.add(stepKey);
          steps.push({
            inputMint,
            outputMint,
            symbol: `${getSymbol(inputMint)}→${getSymbol(outputMint)}`,
            stepKey,
          });
          return steps;
        },
        []
      );

      const duplicateCount = routePlan.length - uniqueSteps.length;
      setAnalysisMeta({
        duplicateCount,
        uniqueStepCount: uniqueSteps.length,
      });

      console.log(
        `[Analysis] Starting constrained analysis for ${uniqueSteps.length} unique steps ` +
          `(from ${routePlan.length} route entries, concurrency ${ANALYSIS_CONCURRENCY}).`
      );

      if (duplicateCount > 0) {
        console.log(
          `[Analysis] Collapsed ${duplicateCount} duplicate route entries before prediction.`
        );
      }

      const results = await runWithConcurrency(
        uniqueSteps,
        ANALYSIS_CONCURRENCY,
        async (step, index) => {
          if (index > 0) {
            await sleep(ANALYSIS_STAGGER_MS);
          }

          appendEvent(
            "Prediction queued",
            `${step.symbol} entered the Prophet queue.`,
            "active",
            "decision"
          );

          try {
            const response = await fetch("/api/predict-step", {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
              },
              body: JSON.stringify({
                inputMint: step.inputMint,
                outputMint: step.outputMint,
                symbol: step.symbol,
              }),
            });

            if (!response.ok) {
              const errorBody = await response.text();
              appendEvent(
                "Prediction failed",
                `${step.symbol} returned ${response.status}: ${errorBody}`,
                "negative",
                "decision"
              );
              return null;
            }

            const prediction = (await response.json()) as Omit<
              StepPrediction,
              "symbol" | "stepKey"
            >;

            if (!prediction?.supported) {
              const unavailablePrediction: StepPrediction = {
                ...prediction,
                supported: false,
                symbol: step.symbol,
                stepKey: step.stepKey,
              };

              appendEvent(
                "Prediction unavailable",
                `${step.symbol}: ${prediction?.reason ?? "Unknown reason"}`,
                "negative",
                "decision"
              );
              return unavailablePrediction;
            }

            const enrichedPrediction: StepPrediction = {
              ...prediction,
              symbol: step.symbol,
              stepKey: step.stepKey,
            };

            appendEvent(
              "Prediction ready",
              `${step.symbol} resolved to ${prediction.finalAction} with strength ${formatNumber(
                prediction.directionStrength,
                4
              )}.`,
              toToneFromAction(prediction.finalAction),
              "decision"
            );

            return enrichedPrediction;
          } catch (error) {
            const reason =
              error instanceof Error ? error.message : "Unknown request error";

            appendEvent(
              "Prediction error",
              `${step.symbol}: ${reason}`,
              "negative",
              "decision"
            );
            return {
              supported: false,
              requestedSymbol: step.symbol,
              resolvedSymbol: step.symbol,
              reason,
              source: "prediction_request",
              dataset: null,
              symbol: step.symbol,
              stepKey: step.stepKey,
            };
          }
        }
      );

      const allPredictions = results.filter(
        (prediction): prediction is StepPrediction => Boolean(prediction)
      );
      const validPredictions = allPredictions.filter((prediction) => prediction.supported);

      setStepPredictions(allPredictions);
      setFocusedStepKey(allPredictions[0]?.stepKey ?? null);

      if (allPredictions.length === 0) {
        setAnalysisMessage(
          `Route loaded for ${selectedToken.symbol}, but no valid step predictions were produced.`
        );
        return;
      }

      if (validPredictions.length === 0) {
        setAnalysisMessage(
          `Route loaded for ${selectedToken.symbol}, but prediction data is unavailable for the current route steps.`
        );
        setSelectedGraphNodeId("decision");
        return;
      }

      try {
        const routeSummaryForLlm = summarizeRoutePlan(routePlan, selectedToken, tokenMap);
        const bestRoutePath =
          routeSummaryForLlm
            .map((leg) => leg.path || `${leg.inputMintLabel} -> ${leg.outputMintLabel}`)
            .filter(Boolean)
            .join(" || ") || `SOL -> ${selectedToken.symbol}`;

        const llmResponse = await fetch("/api/analyze", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            marketMode: "crypto",
            token: selectedToken.symbol,
            predictions: validPredictions,
            currentLoss,
            inputSymbol: "SOL",
            outputSymbol: selectedToken.symbol,
            bestRoutePath,
            routeLegs: routeSummaryForLlm.length,
            routePriceImpactPct: impactPct,
          }),
        });

        if (llmResponse.ok) {
          const llmPayload = (await llmResponse.json()) as {
            llm?: Record<string, string>;
          };
          setLlm(llmPayload.llm ?? null);
        }
      } catch (error) {
        console.error("LLM API failure:", error);
      }

      let totalWaitTime = 0;
      let totalImprovement = 0;

      validPredictions.forEach((prediction) => {
        if (prediction.timeToBelowCurrent) {
          totalWaitTime += prediction.timeToBelowCurrent;
        }

        if (
          prediction.targetPrice != null &&
          prediction.currentPrice != null &&
          prediction.currentPrice !== 0
        ) {
          const diff = prediction.currentPrice - prediction.targetPrice;
          const improvementPct = diff / prediction.currentPrice;
          if (improvementPct > 0) {
            totalImprovement += improvementPct;
          }
        }
      });

      const averageWaitTime = totalWaitTime / validPredictions.length;
      const cappedImprovement = Math.min(totalImprovement, 0.99);
      const predictedLowerSlippage =
        totalImprovement > 0 ? currentLoss * (1 - cappedImprovement) : currentLoss;

      await reportFinalRecommendation(
        averageWaitTime,
        currentLoss,
        predictedLowerSlippage
      );

      setPredictedLoss(predictedLowerSlippage);
      setOptimalEta(averageWaitTime);
      setSelectedGraphNodeId("risk");
      setAnalysisMessage(
        averageWaitTime > 0
          ? `Average low-point alignment suggests waiting ${formatDuration(
              averageWaitTime
            )} for ${selectedToken.symbol}.`
          : `Current route is already favorable for ${selectedToken.symbol}.`
      );
      appendEvent(
        "Recommendation computed",
        averageWaitTime > 0
          ? `Average low-point ETA is ${formatDuration(averageWaitTime)}.`
          : "Current route is already favorable and does not require waiting.",
        averageWaitTime > 0 ? "neutral" : "positive",
        "decision"
      );

      const riskSnapshot = assessExecutionRisk({
        routePlan,
        priceImpactPct: impactPct,
        activePrediction: validPredictions[0],
        tradeNotionalUsd: routeNotionalUsd,
      });
      appendEvent(
        "Risk score computed",
        `${selectedToken.symbol} now sits at ${Math.round(
          riskSnapshot.score
        )}/100. Recommended mode: ${riskSnapshot.recommendedMode}.`,
        riskSnapshot.tone,
        "risk"
      );
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Route analysis failed";
      setAnalysisError(message);
      appendEvent("Analysis failed", message, "negative", "route");
    } finally {
      setIsAnalyzing(false);
    }
  };

  const handleBuildSp500InformationMap = async (forceRefresh: boolean) => {
    if (marketMode !== "sp500") {
      return;
    }

    setIsBuildingSp500Map(true);
    setSp500InformationMapError("");
    setAnalysisError("");
    setAnalysisMessage(
      forceRefresh
        ? "Refreshing the S&P500 information map with the latest Prophet screening..."
        : "Building the S&P500 information map from Prophet screening signals..."
    );

    appendEvent(
      "Information map queued",
      forceRefresh
        ? "Refreshing the full S&P500 information map."
        : "Queued a full S&P500 information map build.",
      "active",
      "source"
    );

    try {
      const response = await fetch("/api/sp500-map", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          forceRefresh,
        }),
      });

      if (!response.ok) {
        const errorBody = await response.text();
        const message = errorBody || "Failed to build the S&P500 information map.";
        setSp500InformationMapError(message);
        setAnalysisError(message);
        appendEvent("Information map failed", message, "negative", "source");
        return;
      }

      const payload = (await response.json()) as Sp500InformationMapResult;
      setSp500InformationMap(payload);
      const defaultSymbol =
        selectedEquity?.symbol || payload.topPicks?.[0]?.symbol || payload.points?.[0]?.symbol || null;
      setSelectedInformationMapSymbol(defaultSymbol);
      setAnalysisMessage(
        `Information map ready: ${payload.universe?.evaluatedSymbols || payload.points.length} symbols screened and ${payload.topPicks?.length || 0} top ideas ranked.`
      );
      appendEvent(
        "Information map ready",
        `${payload.universe?.evaluatedSymbols || payload.points.length} S&P500 symbols were screened and optimized into a top-10 list.`,
        "positive",
        "source"
      );
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Failed to build the S&P500 information map.";
      setSp500InformationMapError(message);
      setAnalysisError(message);
      appendEvent("Information map failed", message, "negative", "source");
    } finally {
      setIsBuildingSp500Map(false);
    }
  };

  const handleBuildSp500Portfolio = async (forceRefresh: boolean) => {
    if (marketMode !== "sp500") {
      return;
    }

    setIsBuildingSp500Portfolio(true);
    setSp500PortfolioError("");
    setAnalysisError("");
    setAnalysisMessage(
      forceRefresh
        ? "Refreshing the explainable S&P500 portfolio with live market inputs..."
        : "Building an explainable S&P500 portfolio from map scores and live market inputs..."
    );

    appendEvent(
      "Portfolio queued",
      forceRefresh
        ? "Refreshing the optimized S&P500 portfolio."
        : "Queued an explainable S&P500 portfolio build.",
      "active",
      "source"
    );

    try {
      const response = await fetch("/api/sp500-portfolio", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          forceRefresh,
        }),
      });

      if (!response.ok) {
        const errorBody = await response.text();
        const message = errorBody || "Failed to build the S&P500 portfolio.";
        setSp500PortfolioError(message);
        setAnalysisError(message);
        appendEvent("Portfolio failed", message, "negative", "source");
        return;
      }

      const payload = (await response.json()) as Sp500PortfolioResult;
      setSp500Portfolio(payload);
      setAnalysisMessage(
        `Portfolio ready: ${payload.holdings?.length || 0} holdings optimized with weight bounds and sector diversification.`
      );
      appendEvent(
        "Portfolio ready",
        `${payload.holdings?.length || 0} S&P500 holdings were optimized into a weighted portfolio.`,
        "positive",
        "source"
      );
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Failed to build the S&P500 portfolio.";
      setSp500PortfolioError(message);
      setAnalysisError(message);
      appendEvent("Portfolio failed", message, "negative", "source");
    } finally {
      setIsBuildingSp500Portfolio(false);
    }
  };

  const handleAnalyzeEquity = async () => {
    if (!selectedEquity) {
      return;
    }

    setIsAnalyzing(true);
    setAnalysisError("");
    setAnalysisMessage(`Running direct Prophet signal for ${selectedEquity.symbol}...`);
    setPredictedLoss(null);
    setOptimalEta(null);
    setStartTime(Date.now());
    setStepPredictions([]);
    setFocusedStepKey(null);
    setSelectedGraphNodeId("decision");
    setLlm(null);
    setSelectedExecutionMode(null);
    setRoutes([]);
    setPriceImpact(null);
    setAnalysisMeta({
      duplicateCount: 0,
      uniqueStepCount: 1,
    });

    try {
      appendEvent(
        "Prediction queued",
        `${selectedEquity.symbol} entered the Prophet queue.`,
        "active",
        "decision"
      );

      const response = await fetch("/api/predict-symbol", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          symbol: selectedEquity.symbol,
        }),
      });

      if (!response.ok) {
        const errorBody = await response.text();
        let displayError = errorBody || "S&P500 prediction request failed.";
        try {
          const parsed = JSON.parse(errorBody);
          if (parsed.error) displayError = parsed.error;
        } catch {}

        setAnalysisError(displayError);
        appendEvent(
          "Prediction failed",
          `${selectedEquity.symbol} prediction failed: ${displayError}`,
          "negative",
          "decision"
        );
        fetchUserProfile(); // Refresh profile credits
        return;
      }

      const prediction = (await response.json()) as Omit<StepPrediction, "symbol" | "stepKey"> & { remainingCredits?: number };
      if (prediction.remainingCredits !== undefined) {
        setUserProfile(prev => prev ? { ...prev, credits: prediction.remainingCredits! } : null);
      }
      const enrichedPrediction: StepPrediction = prediction.supported
        ? {
            ...prediction,
            symbol: selectedEquity.symbol,
            stepKey: selectedEquity.symbol,
            recommendation: {
              shouldBuyWithSol: false,
              tone:
                prediction.finalAction === "BUY"
                  ? "positive"
                  : prediction.finalAction === "SELL"
                    ? "negative"
                    : "neutral",
              summary: buildEquityRecommendationSummary(
                prediction.finalAction,
                selectedEquity.symbol
              ),
            },
          }
        : {
            ...prediction,
            supported: false,
            symbol: selectedEquity.symbol,
            stepKey: selectedEquity.symbol,
          };

      setStepPredictions([enrichedPrediction]);
      setFocusedStepKey(selectedEquity.symbol);

      if (!prediction.supported) {
        setAnalysisMessage(
          `Signal for ${selectedEquity.symbol} is unavailable right now.`
        );
        appendEvent(
          "Prediction unavailable",
          `${selectedEquity.symbol}: ${prediction.reason ?? "Unknown reason"}`,
          "negative",
          "decision"
        );
        return;
      }

      setAnalysisMessage(
        `${selectedEquity.symbol} resolved to ${prediction.finalAction}. Review the graph for cadence and wrapper details.`
      );
      appendEvent(
        "Prediction ready",
        `${selectedEquity.symbol} resolved to ${prediction.finalAction} with strength ${formatNumber(
          prediction.directionStrength,
          4
        )}.`,
        toToneFromAction(prediction.finalAction),
        "decision"
      );

      try {
        const llmResponse = await fetch("/api/analyze", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            marketMode: "sp500",
            token: selectedEquity.symbol,
            predictions: [enrichedPrediction],
            currentLoss: 0,
            inputSymbol: selectedEquity.symbol,
            outputSymbol: selectedEquity.symbol,
            bestRoutePath: selectedEquity.symbol,
            routeLegs: 1,
            routePriceImpactPct: 0,
            portfolioGeometry: sp500Portfolio?.geometry || null,
            portfolioNaturalGradient: sp500Portfolio?.naturalGradient || null,
            portfolioManifold: sp500Portfolio?.manifold || null,
            portfolioChampionAgent: sp500Portfolio?.championAgent || null,
            portfolioSummary: sp500Portfolio?.summary || null,
            portfolioAllocation: sp500Portfolio?.allocation || null,
            portfolioMethodology: sp500Portfolio?.methodology || null,
            portfolioTopHoldings: (sp500Portfolio?.holdings || []).slice(0, 5),
            informationMapNeuralModel: sp500InformationMap?.webNeuralModel || null,
            informationMapFeatureBenchmark: sp500InformationMap?.featureBenchmark || null,
            selectedPortfolioHolding:
              sp500Portfolio?.holdings?.find((holding) => holding.symbol === selectedEquity.symbol) ||
              null,
            selectedMapPoint:
              sp500InformationMap?.points?.find((point) => point.symbol === selectedEquity.symbol) ||
              sp500InformationMap?.topPicks?.find((point) => point.symbol === selectedEquity.symbol) ||
              null,
          }),
        });

        if (llmResponse.ok) {
          const llmPayload = (await llmResponse.json()) as {
            llm?: Record<string, string>;
          };
          setLlm(llmPayload.llm ?? null);
          appendEvent(
            "LLM synthesis ready",
            `${selectedEquity.symbol} narrative guidance is now available.`,
            "active",
            "llm"
          );
        }
      } catch (error) {
        console.error("LLM API failure:", error);
      }
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "S&P500 analysis failed";
      setAnalysisError(message);
      appendEvent("Analysis failed", message, "negative", "decision");
    } finally {
      setIsAnalyzing(false);
    }
  };

  const handlePortfolioChatAnalyze = async () => {
    if (marketMode !== "sp500") {
      return;
    }

    if (!portfolioChatFile) {
      setPortfolioChatError("포트폴리오 CSV 파일을 먼저 선택해 주세요.");
      return;
    }

    const trimmedPrompt = portfolioChatPrompt.trim();
    const userMessage: PortfolioChatMessage = {
      id: createPortfolioChatMessageId(),
      role: "user",
      text: trimmedPrompt || "이 포트폴리오를 어떻게 재구성하면 좋을지 분석해줘.",
      fileName: portfolioChatFile.name,
      analysis: null,
    };

    setPortfolioChatError("");
    setIsPortfolioChatting(true);
    setPortfolioChatMessages((currentMessages) => [...currentMessages, userMessage]);
    setAnalysisMessage(`Analyzing uploaded portfolio CSV for ${portfolioChatFile.name}...`);
    appendEvent(
      "Portfolio chat queued",
      `${portfolioChatFile.name} is being compared against the S&P500 map and optimized portfolio.`,
      "active",
      "llm"
    );

    try {
      const formData = new FormData();
      formData.append("file", portfolioChatFile);
      formData.append("prompt", trimmedPrompt);

      const response = await fetch("/api/portfolio-chat", {
        method: "POST",
        body: formData,
      });

      const payload = (await response.json()) as PortfolioChatResponse;
      if (!response.ok || payload.error) {
        const message =
          payload.error || "업로드한 포트폴리오 CSV를 분석하지 못했습니다.";
        setPortfolioChatError(message);
        setPortfolioChatMessages((currentMessages) => [
          ...currentMessages,
          {
            id: createPortfolioChatMessageId(),
            role: "assistant",
            text: `분석 실패: ${message}`,
            fileName: null,
            analysis: null,
          },
        ]);
        appendEvent("Portfolio chat failed", message, "negative", "llm");
        return;
      }

      setPortfolioChatMessages((currentMessages) => [
        ...currentMessages,
        {
          id: createPortfolioChatMessageId(),
          role: "assistant",
          text: payload.assistant,
          fileName: portfolioChatFile.name,
          analysis: payload,
        },
      ]);
      setPortfolioChatPrompt("");
      setPortfolioChatFile(null);
      setPortfolioChatInputKey((value) => value + 1);
      setAnalysisMessage(
        `Portfolio CSV analysis ready for ${userMessage.fileName || "uploaded holdings"}.`
      );
      appendEvent(
        "Portfolio chat ready",
        `${userMessage.fileName || "Uploaded portfolio"} now has keep / reduce / exit / add guidance.`,
        "positive",
        "llm"
      );
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : "업로드한 포트폴리오 CSV를 분석하지 못했습니다.";
      setPortfolioChatError(message);
      setPortfolioChatMessages((currentMessages) => [
        ...currentMessages,
        {
          id: createPortfolioChatMessageId(),
          role: "assistant",
          text: `분석 실패: ${message}`,
          fileName: null,
          analysis: null,
        },
      ]);
      appendEvent("Portfolio chat failed", message, "negative", "llm");
    } finally {
      setIsPortfolioChatting(false);
    }
  };

  const routeSummary =
    marketMode === "crypto" ? summarizeRoutePlan(routes, selectedToken, tokenMap) : [];
  const predictionTone = getPredictionTone(stepPredictions[0] || null);
  const predictionMap = stepPredictions.reduce<Record<string, StepPrediction>>(
    (result, prediction) => {
      result[prediction.stepKey] = prediction;
      return result;
    },
    {}
  );
  const activePrediction =
    (focusedStepKey ? predictionMap[focusedStepKey] : null) || stepPredictions[0] || null;
  const routeNotionalUsd = routes.reduce(
    (sum, leg) => sum + Math.max(0, Number(leg?.usdValue) || 0),
    0
  );
  const executionRisk =
    marketMode === "crypto" && routes.length > 0
      ? assessExecutionRisk({
          routePlan: routes,
          priceImpactPct: priceImpact,
          activePrediction,
          tradeNotionalUsd: routeNotionalUsd,
        })
      : null;
  const activeExecutionMode =
    selectedExecutionMode || executionRisk?.recommendedMode || null;
  const activeExecutionAction =
    executionRisk?.actions.find((action) => action.mode === activeExecutionMode) || null;
  const activePredictionTone = getPredictionTone(activePrediction);
  const activePredictionHeadline = activePrediction?.symbol || "Choose a route step";
  const activePredictionBadge = activePrediction
    ? activePrediction.supported
      ? formatActionLabel(activePrediction.finalAction)
      : "unavailable"
    : "idle";
  const activePredictionSummary = activePrediction
    ? activePrediction.supported
      ? marketMode === "sp500"
        ? buildEquityRecommendationSummary(
            activePrediction.finalAction,
            activePrediction.symbol
          )
        : activePrediction.recommendation?.summary ||
          "Inspect the route graph to understand how the active step was scored."
      : activePrediction.reason ||
        "Prediction is unavailable for this route step."
    : marketMode === "sp500"
      ? "Analyze an S&P500-focused ticker to inspect its direct signal and graph."
      : "Analyze a token to inspect the active step recommendation and its graph.";
  const perRuleSummary = activePrediction?.perRuleSummary || {};
  const cadenceEntries = Object.entries(perRuleSummary).map(([rule, summary]) => ({
    rule,
    label: summary?.ruleLabel || rule,
    direction: summary?.direction || null,
    lowTiming: summary?.lowTiming || null,
    highTiming: summary?.highTiming || null,
  }));
  const directionEntries = cadenceEntries.filter((entry) => entry.direction);
  const directionSlow = directionEntries[0] || null;
  const directionMid = directionEntries[1] || null;
  const directionFast = directionEntries[2] || null;
  const lowTimingCandidates = Object.entries(perRuleSummary)
    .map(([rule, summary]) => ({
      rule: summary?.ruleLabel || rule,
      ...summary?.lowTiming,
    }))
    .filter((candidate) => candidate.predictedTimestamp);
  const highTimingCandidates = Object.entries(perRuleSummary)
    .map(([rule, summary]) => ({
      rule: summary?.ruleLabel || rule,
      ...summary?.highTiming,
    }))
    .filter((candidate) => candidate.predictedTimestamp);

  lowTimingCandidates.sort(
    (left, right) =>
      new Date(left.predictedTimestamp || 0).getTime() -
      new Date(right.predictedTimestamp || 0).getTime()
  );
  highTimingCandidates.sort(
    (left, right) =>
      new Date(left.predictedTimestamp || 0).getTime() -
      new Date(right.predictedTimestamp || 0).getTime()
  );

  const wrapperResult = activePrediction?.wrapper || null;
  const wrapperBagging = wrapperResult?.bagging || null;
  const wrapperFinalAgent = findWrapperAgent(wrapperResult, "final_action_agent");
  const wrapperTimeToBelowAgent = findWrapperAgent(
    wrapperResult,
    "time_to_below_agent"
  );
  const wrapperEMRegimeAgent = findWrapperAgent(wrapperResult, "em_regime_agent");
  const wrapperMinimaxAgent = findWrapperAgent(wrapperResult, "minimax_prior_agent");
  const wrapperSpikeSustainAgent = findWrapperAgent(
    wrapperResult,
    "spike_sustain_agent"
  );
  const wrapperDrawdownLingerAgent = findWrapperAgent(
    wrapperResult,
    "drawdown_linger_agent"
  );
  const wrapperRegretAgent = findWrapperAgent(wrapperResult, "regret_agent");
  const wrapperConservativeAgent = findWrapperAgent(
    wrapperResult,
    "conservative_gold_agent"
  );
  const wrapperCostAgent = findWrapperAgent(
    wrapperResult,
    "execution_cost_agent"
  );
  const trendChart = buildTrendChartModel(activePrediction);
  const stockForecastChart = buildStockForecastChartModel(activePrediction);
  const stockSeasonalityCharts = buildStockSeasonalityCharts(activePrediction);
  const stockSeasonalitySummary = activePrediction?.seasonalitySummary || null;
  const stockCorrelationForecast = activePrediction?.correlationForecast || null;
  const stockTailDiagnostics = activePrediction?.tailDiagnostics || null;
  const stockMoeRuntime = activePrediction?.moeRuntime || null;
  const activePredictionLastClose = getPredictionLastClose(activePrediction);
  const activePredictionLivePrice = getPredictionLivePrice(activePrediction);
  const hideStockOnlyLlmRouteFields = marketMode === "sp500";
  const informationMapFocusSymbol = selectedInformationMapSymbol || selectedEquity?.symbol || null;
  const informationMapCards = (["raw", "firstCoordinate", "secondCoordinate"] as InformationMapViewMode[]).map(
    (viewMode) => ({
      viewMode,
      meta: getInformationMapViewMeta(viewMode),
      chart: buildInformationMapChartModel(
        sp500InformationMap,
        informationMapFocusSymbol,
        viewMode,
        sp500Portfolio?.geometry || null
      ),
    })
  );
  const originalInformationMapCards = ([
    "raw",
    "firstCoordinate",
    "secondCoordinate",
  ] as InformationMapViewMode[]).map((viewMode) => ({
    viewMode,
    meta: getInformationMapViewMeta(viewMode),
    chart: buildInformationMapChartModel(
      sp500InformationMap,
      informationMapFocusSymbol,
      viewMode,
      null
    ),
  }));
  const hasOriginalInformationMapCharts = originalInformationMapCards.some(
    ({ chart }) => Boolean(chart)
  );
  const renderOriginalInformationMapChartCard = ({
    viewMode,
    meta,
    chart,
  }: {
    viewMode: InformationMapViewMode;
    meta: ReturnType<typeof getInformationMapViewMeta>;
    chart: ReturnType<typeof buildInformationMapChartModel>;
  }) => {
    if (!chart) {
      return null;
    }

    return (
      <div key={`original-${meta.title}`} className="information-map-chart-card original-map-card">
        <div className="trend-header">
          <div>
            <p className="card-label">
              {viewMode === "raw" ? "Original moment map" : "Original coordinate map"}
            </p>
            <h4>{meta.title}</h4>
          </div>
          <span className="signal-meta">{meta.subtitle}</span>
        </div>

        <svg
          viewBox={`0 0 ${chart.width} ${chart.height}`}
          className="information-map-chart"
          role="img"
          aria-label={`Original ${meta.title}`}
        >
          <rect
            x="42"
            y="42"
            width={String(chart.width - 84)}
            height={String(chart.height - 84)}
            rx="28"
            className="info-map-surface"
          />
          <line
            x1="42"
            x2={String(chart.width - 42)}
            y1={String(chart.yZero)}
            y2={String(chart.yZero)}
            className="info-map-axis zero"
          />
          <line
            x1={String(chart.xZero)}
            x2={String(chart.xZero)}
            y1="42"
            y2={String(chart.height - 42)}
            className="info-map-axis zero"
          />

          {chart.xTicks.map((tick, index) => {
            const x =
              index === 0 ? 42 : index === 1 ? chart.xZero : chart.width - 42;
            return (
              <g key={`original-${meta.title}-x-${index}`}>
                <line
                  x1={String(x)}
                  x2={String(x)}
                  y1="42"
                  y2={String(chart.height - 42)}
                  className="info-map-axis"
                />
                <text
                  x={String(x)}
                  y={String(chart.height - 16)}
                  textAnchor={index === 0 ? "start" : index === 2 ? "end" : "middle"}
                  className="info-map-axis-label"
                >
                  {formatSignedRatio(tick, 2, chart.xTickUnit)}
                </text>
              </g>
            );
          })}

          {chart.yTicks.map((tick, index) => {
            const y =
              index === 0 ? 42 : index === 1 ? chart.yZero : chart.height - 42;
            return (
              <g key={`original-${meta.title}-y-${index}`}>
                <line
                  x1="42"
                  x2={String(chart.width - 42)}
                  y1={String(y)}
                  y2={String(y)}
                  className="info-map-axis"
                />
                <text x="46" y={String(y - 8)} className="info-map-axis-label">
                  {formatSignedRatio(tick, chart.yTickUnit ? 1 : 2, chart.yTickUnit)}
                </text>
              </g>
            );
          })}

          {chart.points.map((point) => (
            <g
              key={`original-${meta.title}-${point.symbol}`}
              className={`info-map-point-group ${
                point.finalAction === "BUY"
                  ? "positive"
                  : point.finalAction === "SELL"
                    ? "negative"
                    : "neutral"
              } ${point.highlighted ? "highlighted" : ""}`}
              onClick={() => setSelectedInformationMapSymbol(point.symbol)}
            >
              <circle
                cx={String(point.cx)}
                cy={String(point.cy)}
                r={String(point.radius + (point.highlighted ? 1.5 : 0))}
                className={`info-map-point ${point.topPick ? "top-pick" : ""}`}
              />
              {point.highlighted ? (
                <text
                  x={String(point.cx + 8)}
                  y={String(point.cy - 10)}
                  className="info-map-point-label"
                >
                  {point.symbol}
                </text>
              ) : null}
            </g>
          ))}
        </svg>

        <div className="trend-legend information-map-legend">
          <div>
            <span>X axis</span>
            <strong>{chart.xAxisLabel}</strong>
          </div>
          <div>
            <span>Y axis</span>
            <strong>{chart.yAxisLabel}</strong>
          </div>
          <div>
            <span>Selected</span>
            <strong>{informationMapFocusSymbol || "--"}</strong>
          </div>
          <div>
            <span>Snapshot</span>
            <strong>{sp500InformationMap?.mapDate || "--"}</strong>
          </div>
        </div>
      </div>
    );
  };
  const topInformationPicks = sp500InformationMap?.topPicks || [];
  const darkHorseInformationPicks = sp500InformationMap?.darkHorsePicks || [];
  const informationMapNeuralModel = sp500InformationMap?.webNeuralModel || null;
  const informationMapFeatureBenchmark = sp500InformationMap?.featureBenchmark || null;
  const recommendedFeatureMethod = informationMapFeatureBenchmark?.recommendedMethod || null;
  const topPortfolioHoldings = sp500Portfolio?.holdings || [];
  const portfolioAllocation = sp500Portfolio?.allocation;
  const portfolioGeometry = sp500Portfolio?.geometry || null;
  const portfolioNaturalGradient = sp500Portfolio?.naturalGradient || null;
  const portfolioCorrelationForecast = sp500Portfolio?.correlationForecast || null;
  const portfolioManifold = sp500Portfolio?.manifold || null;
  const portfolioChampionAgent = sp500Portfolio?.championAgent || null;
  const portfolioSleeves = portfolioAllocation?.sleeves || [];
  const portfolioSectorMix = portfolioAllocation?.sectorMix || [];
  const portfolioInternationalMix = portfolioAllocation?.internationalMix || [];
  const portfolioRiskInputs = portfolioAllocation?.riskInputs;
  const portfolioMacro = portfolioAllocation?.macro;
  const selectedAssetSymbol =
    marketMode === "sp500" ? selectedEquity?.symbol : selectedToken?.symbol;
  const selectedAssetName =
    marketMode === "sp500"
      ? selectedEquity?.name || selectedEquity?.symbol
      : selectedToken?.symbol;
  const executionRiskTone =
    executionRisk?.tone || (routes.length > 0 ? "ready" : "idle");

  const handleExecutionModeSelect = (mode: ExecutionActionMode) => {
    if (!executionRisk) {
      return;
    }

    const action = executionRisk.actions.find((candidate) => candidate.mode === mode);
    if (!action || !action.enabled) {
      return;
    }

    setSelectedExecutionMode(mode);
    setSelectedGraphNodeId("risk");
    setAnalysisMessage(`${action.label} selected. ${action.reason}`);
    appendEvent(
      "Execution policy updated",
      `${selectedAssetSymbol || "Route"} set to ${action.label}. ${action.reason}`,
      mode === "HALT" ? "negative" : mode === "MARKET" ? "positive" : "neutral",
      "risk"
    );
  };

  const graphSummary = activePrediction?.supported
    ? `${activePrediction.symbol} currently resolves to ${
        activePrediction.finalAction
      }, and the graph exposes how cadence models, timing, and wrapper guards shaped that outcome.`
    : selectedAssetSymbol
      ? `Run Analyze to light up the ${selectedAssetSymbol} ${
          marketMode === "sp500" ? "signal" : "route"
        } graph.`
      : marketMode === "sp500"
        ? "Search an S&P500-focused ticker to start direct equity analysis."
        : "Search a token to start routing data through the graph.";

  const graphClusters: GraphCluster[] = [
    {
      id: "runtime-cluster",
      label: "Prophet runtime",
      caption: "Market candles, cached models, and cadence-level forecasters",
      x: 28,
      y: 28,
      width: 760,
      height: 664,
    },
    {
      id: "wrapper-cluster",
      label: "AI wrappers",
      caption: "Guardrails that confirm, soften, or block the core signal",
      x: 814,
      y: 28,
      width: 420,
      height: 820,
    },
    {
      id: "route-cluster",
      label: marketMode === "sp500" ? "Execution context" : "Route analysis",
      caption:
        marketMode === "crypto"
          ? "Jupiter path quality, execution risk, and final recommendation context"
          : "Ticker execution context and final recommendation context",
      x: 1260,
      y: 28,
      width: 320,
      height: 664,
    },
  ];

  const llmHeadline =
    (typeof llm?.final === "string" && llm.final) ||
    (typeof llm?.dynamicPortfolioView === "string" && llm.dynamicPortfolioView) ||
    (typeof llm?.seasonalitySummary === "string" && llm.seasonalitySummary) ||
    (typeof llm?.momentumSummary === "string" && llm.momentumSummary) ||
    (typeof llm?.timingSummary === "string" && llm.timingSummary) ||
    (typeof llm?.spikeSustainSummary === "string" && llm.spikeSustainSummary) ||
    (typeof llm?.drawdownLingerSummary === "string" && llm.drawdownLingerSummary) ||
    (typeof llm?.regretSummary === "string" && llm.regretSummary) ||
    (!hideStockOnlyLlmRouteFields &&
      typeof llm?.routeGuidance === "string" &&
      llm.routeGuidance) ||
    (!hideStockOnlyLlmRouteFields &&
      typeof llm?.nextActionDate === "string" &&
      llm.nextActionDate) ||
    (!hideStockOnlyLlmRouteFields && typeof llm?.buy === "string" && llm.buy) ||
    (typeof llm?.wait === "string" && llm.wait) ||
    "LLM note pending";
  const llmRouteGuidance =
    typeof llm?.routeGuidance === "string" && llm.routeGuidance
      ? llm.routeGuidance
      : "--";
  const llmDynamicPortfolioView =
    typeof llm?.dynamicPortfolioView === "string" && llm.dynamicPortfolioView
      ? llm.dynamicPortfolioView
      : "--";
  const llmNextActionDate =
    typeof llm?.nextActionDate === "string" && llm.nextActionDate
      ? llm.nextActionDate
      : "--";
  const llmMomentumSummary =
    typeof llm?.momentumSummary === "string" && llm.momentumSummary
      ? llm.momentumSummary
      : "--";
  const llmSeasonalitySummary =
    typeof llm?.seasonalitySummary === "string" && llm.seasonalitySummary
      ? llm.seasonalitySummary
      : "--";
  const llmTimingSummary =
    typeof llm?.timingSummary === "string" && llm.timingSummary
      ? llm.timingSummary
      : "--";
  const llmSpikeSustainSummary =
    typeof llm?.spikeSustainSummary === "string" && llm.spikeSustainSummary
      ? llm.spikeSustainSummary
      : "--";
  const llmDrawdownLingerSummary =
    typeof llm?.drawdownLingerSummary === "string" && llm.drawdownLingerSummary
      ? llm.drawdownLingerSummary
      : "--";
  const llmRegretSummary =
    typeof llm?.regretSummary === "string" && llm.regretSummary
      ? llm.regretSummary
      : "--";
  const llmMacroBackdrop =
    typeof llm?.macroBackdrop === "string" && llm.macroBackdrop
      ? llm.macroBackdrop
      : "--";
  const llmBuffettView =
    typeof llm?.buffettView === "string" && llm.buffettView
      ? llm.buffettView
      : "--";
  const llmDruckenmillerView =
    typeof llm?.druckenmillerView === "string" && llm.druckenmillerView
      ? llm.druckenmillerView
      : "--";
  const llmLynchView =
    typeof llm?.lynchView === "string" && llm.lynchView
      ? llm.lynchView
      : "--";
  const llmDalioView =
    typeof llm?.dalioView === "string" && llm.dalioView
      ? llm.dalioView
      : "--";
  const llmMacbookView =
    typeof llm?.macbookView === "string" && llm.macbookView
      ? llm.macbookView
      : "--";

  const graphNodes: GraphNode[] = [
    {
      id: "source",
      x: 146,
      y: 146,
      eyebrow: "Data",
      title: selectedAssetSymbol ? `${selectedAssetSymbol} market feed` : "Market data feed",
      stat: activePrediction?.rows ? `${activePrediction.rows} rows` : "candles + quote",
      meta: marketMode === "crypto"
        ? selectedToken
          ? `Historical candles and step-level route data for ${selectedToken.symbol}`
          : "Search a token to activate the graph"
        : selectedEquity
          ? `Historical candles and direct symbol data for ${selectedEquity.symbol}`
          : "Search an S&P500-focused ticker to activate the graph",
      status: activePrediction?.supported ? "active" : selectedAssetSymbol ? "ready" : "idle",
      description:
        marketMode === "crypto"
          ? "This source node blends the selected token context, Birdeye OHLCV history for route steps, and Jupiter quote data."
          : "This source node blends the selected equity context, fallback market history, and direct Prophet inference state.",
      highlights: [
        {
          label: marketMode === "crypto" ? "Selected token" : "Selected ticker",
          value: selectedAssetSymbol || "--",
        },
        { label: "Rows", value: String(activePrediction?.rows ?? "--") },
        { label: "Prediction source", value: activePrediction?.source || "--" },
        {
          label: marketMode === "sp500" ? "Last close" : "Current price",
          value: formatNumber(
            marketMode === "sp500" ? activePredictionLastClose : activePrediction?.currentPrice,
            marketMode === "sp500" ? 2 : 6
          ),
        },
        ...(marketMode === "sp500"
          ? [
              {
                label: "Live price",
                value: formatNumber(activePredictionLivePrice, 2),
              },
            ]
          : []),
      ],
      bullets: [
        marketMode === "sp500"
          ? "Every ticker prediction starts from this S&P500 market context."
          : "Every route step prediction starts from this market context.",
        marketMode === "crypto"
          ? "Birdeye history powers route-step inference, while Jupiter provides the path itself."
          : "S&P500 mode skips route discovery and pushes the ticker directly into the Prophet runtime.",
        "If this node is idle, the rest of the graph has not been armed yet.",
      ],
    },
    {
      id: "cache",
      x: 146,
      y: 508,
      eyebrow: "Cache",
      title: "Model cache",
      stat: "warm-start ready",
      meta: "Cached Prophet artifacts support faster retraining and reuse",
      status: activePrediction?.supported ? "active" : "idle",
      description:
        "Main branch keeps Prophet model caches under services/trader/model_cache so repeated inference can reuse trained state.",
      highlights: [
        { label: "Store", value: "services/trader/model_cache" },
        { label: "Warm start", value: "enabled" },
        {
          label: "Cadences",
          value:
            activePrediction?.cadenceRules
              ?.map((rule) => rule.label || rule.rule)
              .filter(Boolean)
              .join(" / ") || "10m / 5m / 1m",
        },
        { label: "Focus step", value: activePrediction?.symbol || "--" },
      ],
      bullets: [
        marketMode === "sp500"
          ? "This node represents the reusable artifacts behind direct ticker inference."
          : "This node represents the reusable artifacts behind route-step inference.",
        "It helps the graph explain why repeated analyses feel faster than cold starts.",
        "If a fresh fit is required, the graph still routes through this cache layer first.",
      ],
    },
    {
      id: "dir10",
      x: 420,
      y: 120,
      eyebrow: "Agent",
      title: `Direction ${directionSlow?.label || "slow"}`,
      stat: directionSlow?.direction?.finalAction ? formatActionLabel(directionSlow.direction.finalAction) : "weight 0.45",
      meta: directionSlow?.direction
        ? `score ${formatNumber(directionSlow.direction.weightedScore, 5)}`
        : "Slow directional vote",
      status: directionSlow?.direction ? toToneFromAction(directionSlow.direction.finalAction) : "idle",
      description:
        "The slowest directional cadence contributes the highest weight and stabilizes noisy shorter-term movement.",
      highlights: [
        { label: "Cadence", value: directionSlow?.label || "--" },
        { label: "Rule action", value: directionSlow?.direction?.finalAction ? formatActionLabel(directionSlow.direction.finalAction) : "--" },
        { label: "Weighted score", value: formatNumber(directionSlow?.direction?.weightedScore, 5) },
        {
          label: "1st moment",
          value: formatMomentPercentPerHour(directionSlow?.direction?.firstMomentPctPerHour),
        },
        {
          label: "2nd moment",
          value: formatMomentPercentPerHour2(directionSlow?.direction?.secondMomentPctPerHour2),
        },
        { label: "Inner models", value: String(directionSlow?.direction?.agents?.length ?? "--") },
      ],
      bullets: [
        "This is the slowest and steadiest directional voter.",
        "Its score usually dominates when the route step has a clear trend.",
        "The graph uses it to anchor the final recommendation.",
      ],
    },
    {
      id: "dir5",
      x: 420,
      y: 280,
      eyebrow: "Agent",
      title: `Direction ${directionMid?.label || "mid"}`,
      stat: directionMid?.direction?.finalAction ? formatActionLabel(directionMid.direction.finalAction) : "weight 0.35",
      meta: directionMid?.direction
        ? `score ${formatNumber(directionMid.direction.weightedScore, 5)}`
        : "Swing directional vote",
      status: directionMid?.direction ? toToneFromAction(directionMid.direction.finalAction) : "idle",
      description:
        "The middle cadence reacts faster than the slow path and helps reconcile trend with recent motion.",
      highlights: [
        { label: "Cadence", value: directionMid?.label || "--" },
        { label: "Rule action", value: directionMid?.direction?.finalAction ? formatActionLabel(directionMid.direction.finalAction) : "--" },
        { label: "Weighted score", value: formatNumber(directionMid?.direction?.weightedScore, 5) },
        {
          label: "1st moment",
          value: formatMomentPercentPerHour(directionMid?.direction?.firstMomentPctPerHour),
        },
        {
          label: "2nd moment",
          value: formatMomentPercentPerHour2(directionMid?.direction?.secondMomentPctPerHour2),
        },
        { label: "Inner models", value: String(directionMid?.direction?.agents?.length ?? "--") },
      ],
      bullets: [
        "Acts as the bridge between slow trend and fast momentum.",
        "Useful for seeing whether the route step is accelerating into or away from trend.",
        "Often shifts the final action between HOLD and stronger directional calls.",
      ],
    },
    {
      id: "dir1",
      x: 420,
      y: 440,
      eyebrow: "Agent",
      title: `Direction ${directionFast?.label || "fast"}`,
      stat: directionFast?.direction?.finalAction ? formatActionLabel(directionFast.direction.finalAction) : "weight 0.20",
      meta: directionFast?.direction
        ? `score ${formatNumber(directionFast.direction.weightedScore, 5)}`
        : "Fast directional impulse",
      status: directionFast?.direction ? toToneFromAction(directionFast.direction.finalAction) : "idle",
      description:
        "The fastest cadence is the noisiest contributor, used for impulse confirmation rather than primary control.",
      highlights: [
        { label: "Cadence", value: directionFast?.label || "--" },
        { label: "Rule action", value: directionFast?.direction?.finalAction ? formatActionLabel(directionFast.direction.finalAction) : "--" },
        { label: "Weighted score", value: formatNumber(directionFast?.direction?.weightedScore, 5) },
        {
          label: "1st moment",
          value: formatMomentPercentPerHour(directionFast?.direction?.firstMomentPctPerHour),
        },
        {
          label: "2nd moment",
          value: formatMomentPercentPerHour2(directionFast?.direction?.secondMomentPctPerHour2),
        },
        { label: "Inner models", value: String(directionFast?.direction?.agents?.length ?? "--") },
      ],
      bullets: [
        "This node reacts first when micro-momentum changes.",
        "Its lower weight keeps it useful without letting it dominate the decision.",
        "When it strongly disagrees with slower paths, HOLD outcomes become more common.",
      ],
    },
    {
      id: "low",
      x: 676,
      y: 188,
      eyebrow: "Timing",
      title: "Low timing agents",
      stat: activePrediction?.timingEnabled ? `${lowTimingCandidates.length} targets` : "timing off",
      meta: lowTimingCandidates[0]?.predictedTimestamp
        ? `next ${formatTimestamp(lowTimingCandidates[0].predictedTimestamp)}`
        : "Forecasted low windows",
      status: activePrediction?.timingEnabled ? "active" : activePrediction ? "ready" : "idle",
      description:
        "Low timing agents estimate the next attractive dip and become especially important when BUY conditions are close.",
      highlights: [
        { label: "Timing", value: activePrediction?.timingEnabled ? "enabled" : "off" },
        { label: "Next low", value: formatTimestamp(activePrediction?.optimalBuyTimestamp || lowTimingCandidates[0]?.predictedTimestamp) },
        { label: "Predicted price", value: formatNumber(activePrediction?.optimalBuyPrice ?? lowTimingCandidates[0]?.predictedPrice, 4) },
        {
          label: "Below current",
          value: formatDuration(activePrediction?.timeToBelowCurrent ?? null),
        },
      ],
      bullets: [
        "Timing augments direction instead of replacing it.",
        "This node answers when a route step may become more attractive, not just whether it is good.",
        "If timing is off, the graph falls back to pure directional reasoning.",
      ],
    },
    {
      id: "high",
      x: 676,
      y: 362,
      eyebrow: "Timing",
      title: "High timing agents",
      stat: activePrediction?.timingEnabled ? `${highTimingCandidates.length} targets` : "timing off",
      meta: highTimingCandidates[0]?.predictedTimestamp
        ? `next ${formatTimestamp(highTimingCandidates[0].predictedTimestamp)}`
        : "Forecasted high windows",
      status: activePrediction?.timingEnabled ? "active" : activePrediction ? "ready" : "idle",
      description:
        "High timing agents map likely rebound or exit windows. They matter most when the route step is leaning bearish or overextended.",
      highlights: [
        { label: "Timing", value: activePrediction?.timingEnabled ? "enabled" : "off" },
        { label: "Next high", value: formatTimestamp(activePrediction?.optimalSellTimestamp || highTimingCandidates[0]?.predictedTimestamp) },
        { label: "Predicted price", value: formatNumber(activePrediction?.optimalSellPrice ?? highTimingCandidates[0]?.predictedPrice, 4) },
        {
          label: "Target price",
          value: formatNumber(activePrediction?.targetPrice, 4),
        },
      ],
      bullets: [
        "This path complements low timing by highlighting where upside may cap out.",
        "It helps explain why bearish or cautious calls appear even when current price looks attractive.",
        "When direction is neutral, this node usually becomes supporting context rather than a decider.",
      ],
    },
    {
      id: "decision",
      x: 676,
      y: 552,
      eyebrow: "Coordinator",
      title: "Decision coordinator",
      stat: formatActionLabel(activePrediction?.finalAction),
      meta:
        activePrediction?.recommendation?.summary ||
        "Run Analyze to aggregate route-step predictions into a decision surface",
      status: activePredictionTone,
      description:
        "The coordinator aggregates directional cadence votes and timing context into the route-step action shown in the UI.",
      highlights: [
        { label: "Action", value: formatActionLabel(activePrediction?.finalAction) },
        { label: "Vote", value: formatNumber(activePrediction?.directionVote, 4) },
        { label: "Strength", value: formatNumber(activePrediction?.directionStrength, 4) },
        {
          label: "1st moment",
          value: formatMomentPercentPerHour(activePrediction?.firstMomentPctPerHour),
        },
        {
          label: "2nd moment",
          value: formatMomentPercentPerHour2(activePrediction?.secondMomentPctPerHour2),
        },
        {
          label: "Spike sustain",
          value: formatDuration(
            activePrediction?.spikeSustainConsensusSeconds ?? activePrediction?.spikeSustainSeconds
          ),
        },
        { label: "Best buy", value: formatTimestamp(activePrediction?.optimalBuyTimestamp) },
        { label: "Best sell", value: formatTimestamp(activePrediction?.optimalSellTimestamp) },
        { label: "Drop linger", value: formatDuration(activePrediction?.drawdownLingerSeconds) },
      ],
      bullets: [
        "This node is the fastest way to read the current AI stance.",
        "Vote reflects consensus; strength reflects how forcefully the models lean.",
        "If timing or wrappers disagree, this node becomes the anchor for comparison.",
      ],
    },
    {
      id: "wrapper-final",
      x: 952,
      y: 136,
      eyebrow: "Wrapper",
      title: "Final action agent",
      stat: wrapperFinalAgent?.action || "waiting",
      meta: wrapperFinalAgent
        ? `confidence ${formatConfidence(wrapperFinalAgent.confidence)}`
        : "Mirrors the core action inside the council",
      status: wrapperFinalAgent ? toToneFromAction(wrapperFinalAgent.action) : "idle",
      description:
        "This wrapper replays the coordinator output so the council has a baseline vote before applying conservative guards.",
      highlights: [
        { label: "Action", value: wrapperFinalAgent?.action || "--" },
        { label: "Confidence", value: formatConfidence(wrapperFinalAgent?.confidence) },
        {
          label: "Base weight",
          value: formatNumber(getWrapperBaseWeight(wrapperFinalAgent?.name), 2),
        },
        {
          label: "Learned weight",
          value: formatNumber(
            getWrapperLearnedWeight(wrapperResult, wrapperFinalAgent?.name),
            3
          ),
        },
        { label: "Allow exec", value: wrapperFinalAgent?.allowExecution ? "yes" : "no" },
        {
          label: "Direction strength",
          value: formatNumber(
            Number(wrapperFinalAgent?.localMetrics?.direction_strength ?? NaN),
            4
          ),
        },
        {
          label: "1st moment",
          value: formatMomentPercentPerHour(
            Number(wrapperFinalAgent?.localMetrics?.first_moment_pct_per_hour ?? NaN)
          ),
        },
      ],
      bullets:
        wrapperFinalAgent?.reasons && wrapperFinalAgent.reasons.length > 0
          ? wrapperFinalAgent.reasons
          : [
              "Represents the unadjusted action from the coordinator.",
              "Useful as the baseline before other wrappers soften or block the trade.",
            ],
    },
    {
      id: "wrapper-ttb",
      x: 952,
      y: 292,
      eyebrow: "Wrapper",
      title: "Time-to-below agent",
      stat: wrapperTimeToBelowAgent?.action || "waiting",
      meta: wrapperTimeToBelowAgent
        ? `confidence ${formatConfidence(wrapperTimeToBelowAgent.confidence)}`
        : "Checks whether downside arrives too soon",
      status: wrapperTimeToBelowAgent
        ? toToneFromAction(wrapperTimeToBelowAgent.action)
        : "idle",
      description:
        "This wrapper asks whether the forecast dips below the current price too quickly, often turning fragile BUYs into HOLDs.",
      highlights: [
        { label: "Action", value: wrapperTimeToBelowAgent?.action || "--" },
        { label: "Confidence", value: formatConfidence(wrapperTimeToBelowAgent?.confidence) },
        {
          label: "Base weight",
          value: formatNumber(getWrapperBaseWeight(wrapperTimeToBelowAgent?.name), 2),
        },
        {
          label: "Learned weight",
          value: formatNumber(
            getWrapperLearnedWeight(wrapperResult, wrapperTimeToBelowAgent?.name),
            3
          ),
        },
        {
          label: "Drop ETA",
          value: formatDuration(
            Number(
              wrapperTimeToBelowAgent?.localMetrics?.time_to_below_current_seconds ?? NaN
            )
          ),
        },
        {
          label: "Best buy",
          value: formatTimestamp(
            String(wrapperTimeToBelowAgent?.localMetrics?.optimal_buy_timestamp ?? "")
          ),
        },
        { label: "Allow exec", value: wrapperTimeToBelowAgent?.allowExecution ? "yes" : "no" },
      ],
      bullets:
        wrapperTimeToBelowAgent?.reasons && wrapperTimeToBelowAgent.reasons.length > 0
          ? wrapperTimeToBelowAgent.reasons
          : [
              "Looks for fast downside risk that may justify waiting.",
              "Most informative when timing is enabled and a drop arrives soon.",
            ],
    },
    {
      id: "wrapper-em",
      x: 1170,
      y: 214,
      eyebrow: "Wrapper",
      title: "EM regime agent",
      stat: wrapperEMRegimeAgent?.action || "waiting",
      meta: wrapperEMRegimeAgent
        ? `${formatRegimeLabel(wrapperEMRegimeAgent?.localMetrics?.em_dominant_regime)} · confidence ${formatConfidence(wrapperEMRegimeAgent.confidence)}`
        : "Fits a latent bull/neutral/bear regime mixture before the council votes",
      status: wrapperEMRegimeAgent ? toToneFromAction(wrapperEMRegimeAgent.action) : "idle",
      description:
        "This wrapper runs an EM mixture over directional strength, timing, drawdown, spike, uncertainty, and geodesic evidence to infer whether the latent market regime is bull, neutral, or bear.",
      highlights: [
        { label: "Action", value: wrapperEMRegimeAgent?.action || "--" },
        { label: "Confidence", value: formatConfidence(wrapperEMRegimeAgent?.confidence) },
        {
          label: "Base weight",
          value: formatNumber(getWrapperBaseWeight(wrapperEMRegimeAgent?.name), 2),
        },
        {
          label: "Learned weight",
          value: formatNumber(
            getWrapperLearnedWeight(wrapperResult, wrapperEMRegimeAgent?.name),
            3
          ),
        },
        {
          label: "Dominant regime",
          value: formatRegimeLabel(wrapperEMRegimeAgent?.localMetrics?.em_dominant_regime),
        },
        {
          label: "Bull prob",
          value:
            wrapperEMRegimeAgent?.localMetrics?.em_bull_probability != null
              ? formatConfidence(
                  Number(wrapperEMRegimeAgent.localMetrics.em_bull_probability)
                )
              : "--",
        },
        {
          label: "Bear prob",
          value:
            wrapperEMRegimeAgent?.localMetrics?.em_bear_probability != null
              ? formatConfidence(
                  Number(wrapperEMRegimeAgent.localMetrics.em_bear_probability)
                )
              : "--",
        },
        {
          label: "Dominant prob",
          value:
            wrapperEMRegimeAgent?.localMetrics?.em_dominant_probability != null
              ? formatConfidence(
                  Number(wrapperEMRegimeAgent.localMetrics.em_dominant_probability)
                )
              : "--",
        },
        {
          label: "Signal mean",
          value: formatNumber(
            Number(wrapperEMRegimeAgent?.localMetrics?.em_weighted_signal_mean ?? NaN),
            3
          ),
        },
      ],
      bullets:
        wrapperEMRegimeAgent?.reasons && wrapperEMRegimeAgent.reasons.length > 0
          ? wrapperEMRegimeAgent.reasons
          : [
              "Uses EM to estimate latent market regimes instead of trusting a single observed signal.",
              "Useful when the directional score and the timing context disagree.",
            ],
    },
    {
      id: "wrapper-conservative",
      x: 952,
      y: 604,
      eyebrow: "Wrapper",
      title: "Conservative guard",
      stat: wrapperConservativeAgent?.action || "waiting",
      meta: wrapperConservativeAgent
        ? `confidence ${formatConfidence(wrapperConservativeAgent.confidence)}`
        : "Checks upside and uncertainty before allowing a BUY",
      status: wrapperConservativeAgent
        ? toToneFromAction(wrapperConservativeAgent.action)
        : "idle",
      description:
        "The conservative wrapper penalizes weak upside or high uncertainty and is often the reason optimistic signals get softened to HOLD.",
      highlights: [
        { label: "Action", value: wrapperConservativeAgent?.action || "--" },
        { label: "Confidence", value: formatConfidence(wrapperConservativeAgent?.confidence) },
        {
          label: "Base weight",
          value: formatNumber(getWrapperBaseWeight(wrapperConservativeAgent?.name), 2),
        },
        {
          label: "Learned weight",
          value: formatNumber(
            getWrapperLearnedWeight(wrapperResult, wrapperConservativeAgent?.name),
            3
          ),
        },
        {
          label: "Implied upside",
          value:
            wrapperConservativeAgent?.localMetrics?.implied_upside != null
              ? formatPercent(
                  Number(wrapperConservativeAgent.localMetrics.implied_upside) * 100,
                  2
                )
              : "--",
        },
        {
          label: "Best buy",
          value: formatTimestamp(
            String(wrapperConservativeAgent?.localMetrics?.optimal_buy_timestamp ?? "")
          ),
        },
        { label: "Allow exec", value: wrapperConservativeAgent?.allowExecution ? "yes" : "no" },
      ],
      bullets:
        wrapperConservativeAgent?.reasons &&
        wrapperConservativeAgent.reasons.length > 0
          ? wrapperConservativeAgent.reasons
          : [
              "Uses conservative thresholds to avoid chasing weak setups.",
              "Helpful when route quality is fine but predictive confidence is not.",
            ],
    },
    {
      id: "wrapper-regret",
      x: 1170,
      y: 546,
      eyebrow: "Wrapper",
      title: "Regret agent",
      stat: wrapperRegretAgent?.action || "waiting",
      meta: wrapperRegretAgent
        ? `confidence ${formatConfidence(wrapperRegretAgent.confidence)}`
        : "Checks whether acting now is likely to be regretted in the next leg",
      status: wrapperRegretAgent
        ? toToneFromAction(wrapperRegretAgent.action)
        : "idle",
      description:
        "This wrapper compares buy regret versus sell regret so the council can avoid obviously premature actions right before a better entry or a stronger upside extension.",
      highlights: [
        { label: "Action", value: wrapperRegretAgent?.action || "--" },
        {
          label: "Confidence",
          value: formatConfidence(wrapperRegretAgent?.confidence),
        },
        {
          label: "Base weight",
          value: formatNumber(getWrapperBaseWeight(wrapperRegretAgent?.name), 2),
        },
        {
          label: "Learned weight",
          value: formatNumber(
            getWrapperLearnedWeight(wrapperResult, wrapperRegretAgent?.name),
            3
          ),
        },
        {
          label: "Regret risk",
          value:
            wrapperRegretAgent?.localMetrics?.regret_risk_score != null
              ? formatPercent(
                  Number(wrapperRegretAgent.localMetrics.regret_risk_score) * 100,
                  1
                )
              : "--",
        },
        {
          label: "Bias",
          value: String(wrapperRegretAgent?.localMetrics?.regret_bias ?? "--"),
        },
        {
          label: "Buy regret",
          value:
            wrapperRegretAgent?.localMetrics?.buy_regret_score != null
              ? formatPercent(
                  Number(wrapperRegretAgent.localMetrics.buy_regret_score) * 100,
                  1
                )
              : "--",
        },
        {
          label: "Sell regret",
          value:
            wrapperRegretAgent?.localMetrics?.sell_regret_score != null
              ? formatPercent(
                  Number(wrapperRegretAgent.localMetrics.sell_regret_score) * 100,
                  1
                )
              : "--",
        },
      ],
      bullets:
        wrapperRegretAgent?.reasons && wrapperRegretAgent.reasons.length > 0
          ? wrapperRegretAgent.reasons
          : [
              "Balances regret from buying before a fast drop against regret from selling before a persistent spike.",
              "Best read alongside drawdown linger, spike sustain, and time-to-below timing.",
            ],
    },
    {
      id: "wrapper-cost",
      x: 952,
      y: 760,
      eyebrow: "Wrapper",
      title: "Execution cost agent",
      stat: wrapperCostAgent?.action || "waiting",
      meta: wrapperCostAgent
        ? `confidence ${formatConfidence(wrapperCostAgent.confidence)}`
        : "Guards against slippage and price impact that are too expensive",
      status: wrapperCostAgent ? toToneFromAction(wrapperCostAgent.action) : "idle",
      description:
        "This wrapper makes the route cost visible in the council and blocks trades when slippage or impact gets too expensive.",
      highlights: [
        { label: "Action", value: wrapperCostAgent?.action || "--" },
        { label: "Confidence", value: formatConfidence(wrapperCostAgent?.confidence) },
        {
          label: "Base weight",
          value: formatNumber(getWrapperBaseWeight(wrapperCostAgent?.name), 2),
        },
        {
          label: "Learned weight",
          value: formatNumber(
            getWrapperLearnedWeight(wrapperResult, wrapperCostAgent?.name),
            3
          ),
        },
        { label: "Slippage", value: formatBasisPoints(0) },
        {
          label: "Best sell",
          value: formatTimestamp(
            String(wrapperCostAgent?.localMetrics?.optimal_sell_timestamp ?? "")
          ),
        },
        { label: "Allow exec", value: wrapperCostAgent?.allowExecution ? "yes" : "no" },
      ],
      bullets:
        wrapperCostAgent?.reasons && wrapperCostAgent.reasons.length > 0
          ? wrapperCostAgent.reasons
          : [
              "Uses observed route cost to soften otherwise positive actions.",
              "Best read together with the Jupiter route panel below.",
            ],
    },
    {
      id: "wrapper-linger",
      x: 952,
      y: 448,
      eyebrow: "Wrapper",
      title: "Drawdown linger agent",
      stat: wrapperDrawdownLingerAgent?.action || "waiting",
      meta: wrapperDrawdownLingerAgent
        ? `confidence ${formatConfidence(wrapperDrawdownLingerAgent.confidence)}`
        : "Measures how long a hard drop could stay depressed",
      status: wrapperDrawdownLingerAgent
        ? toToneFromAction(wrapperDrawdownLingerAgent.action)
        : "idle",
      description:
        "This wrapper estimates how long a meaningful drop could stay underwater before the curve recovers, and blocks fragile BUYs when the drag looks too persistent.",
      highlights: [
        { label: "Action", value: wrapperDrawdownLingerAgent?.action || "--" },
        {
          label: "Confidence",
          value: formatConfidence(wrapperDrawdownLingerAgent?.confidence),
        },
        {
          label: "Base weight",
          value: formatNumber(getWrapperBaseWeight(wrapperDrawdownLingerAgent?.name), 2),
        },
        {
          label: "Learned weight",
          value: formatNumber(
            getWrapperLearnedWeight(wrapperResult, wrapperDrawdownLingerAgent?.name),
            3
          ),
        },
        {
          label: "Linger",
          value: formatDuration(
            Number(wrapperDrawdownLingerAgent?.localMetrics?.drawdown_linger_seconds ?? NaN)
          ),
        },
        {
          label: "Recovers",
          value:
            wrapperDrawdownLingerAgent?.localMetrics?.drawdown_recovery_in_horizon === true
              ? "yes"
              : wrapperDrawdownLingerAgent?.localMetrics?.drawdown_recovery_in_horizon === false
                ? "no"
                : "--",
        },
        { label: "Allow exec", value: wrapperDrawdownLingerAgent?.allowExecution ? "yes" : "no" },
      ],
      bullets:
        wrapperDrawdownLingerAgent?.reasons && wrapperDrawdownLingerAgent.reasons.length > 0
          ? wrapperDrawdownLingerAgent.reasons
          : [
              "Looks at how long the curve stays below the reference level after a drop.",
              "Most useful when BUY timing looks attractive but the recovery path is still sluggish.",
            ],
    },
    {
      id: "wrapper",
      x: 1170,
      y: 370,
      eyebrow: "Council",
      title: "Wrapper council",
      stat: formatActionLabel(wrapperResult?.finalAction) || "waiting",
      meta:
        wrapperResult != null
          ? `vote ${formatNumber(wrapperResult.weightedVote, 4)}`
          : "Aggregates wrapper votes",
      status: wrapperResult?.finalAction
        ? toToneFromAction(wrapperResult.finalAction)
        : "idle",
      description:
        "The council aggregates wrapper votes and decides whether the route-step signal should be trusted strongly enough to flow into recommendation and execution planning.",
      highlights: [
        { label: "Final action", value: formatActionLabel(wrapperResult?.finalAction) },
        { label: "Weighted vote", value: formatNumber(wrapperResult?.weightedVote, 4) },
        {
          label: "Bagged action",
          value: wrapperBagging?.action
            ? `${formatActionLabel(wrapperBagging.action)} · ${
                wrapperBagging.stability != null
                  ? formatPercent(Number(wrapperBagging.stability) * 100, 1)
                  : "--"
              }`
            : "--",
        },
        {
          label: "Bagged vote",
          value:
            wrapperBagging?.meanVote != null
              ? `${formatNumber(wrapperBagging.meanVote, 4)} ± ${formatNumber(
                  wrapperBagging.voteStd,
                  4
                )}`
              : "--",
        },
        {
          label: "Weight source",
          value: wrapperResult?.weightSource || "--",
        },
        {
          label: "Feedback count",
          value: String(wrapperResult?.feedbackCount ?? "--"),
        },
        {
          label: "Spike wrapper",
          value: wrapperSpikeSustainAgent?.action || "--",
        },
        {
          label: "Regret wrapper",
          value: wrapperRegretAgent?.action || "--",
        },
        {
          label: "EM wrapper",
          value:
            wrapperEMRegimeAgent?.localMetrics?.em_dominant_regime != null
              ? `${wrapperEMRegimeAgent?.action || "--"} · ${formatRegimeLabel(
                  wrapperEMRegimeAgent.localMetrics.em_dominant_regime
                )}`
              : wrapperEMRegimeAgent?.action || "--",
        },
        {
          label: "Consensus",
          value: wrapperResult?.byzantine?.consensusAction || "--",
        },
        {
          label: "Quorum",
          value:
            wrapperResult?.byzantine?.consensusRatio != null
              ? formatPercent(Number(wrapperResult.byzantine.consensusRatio) * 100, 1)
              : "--",
        },
        {
          label: "Trusted agents",
          value: String(wrapperResult?.byzantine?.trustedAgents?.length ?? "--"),
        },
        {
          label: "Flagged agents",
          value: String(wrapperResult?.byzantine?.flaggedAgents?.length ?? "--"),
        },
        {
          label: "Execution allowed",
          value: wrapperResult?.executionAllowed ? "yes" : "no",
        },
        {
          label: "Yes votes",
          value: String(wrapperResult?.yesExecutionVotes ?? "--"),
        },
        {
          label: "Bagging exec p",
          value:
            wrapperBagging?.executionAllowedProbability != null
              ? formatPercent(
                  Number(wrapperBagging.executionAllowedProbability) * 100,
                  1
                )
              : "--",
        },
      ],
      bullets:
        wrapperResult?.rationale && wrapperResult.rationale.length > 0
          ? [
              ...(wrapperResult.byzantine?.flaggedAgents?.map((item) => {
                const reasons = (item?.reasons || []).join(", ");
                return `${item?.name || "unknown"} flagged as Byzantine candidate${reasons ? `: ${reasons}` : ""}`;
              }) || []),
              ...wrapperResult.rationale,
            ]
          : [
              "Aggregates the wrapper layer with adaptive weights.",
              "A Byzantine-style filter removes outlier agents before the final vote.",
              "This is the last internal stop before route quality becomes the visible recommendation.",
            ],
    },
    {
      id: "route",
      x: 1420,
      y: 230,
      eyebrow: marketMode === "sp500" ? "Execution" : "Route",
      title: marketMode === "sp500" ? "Ticker execution context" : "Jupiter route plan",
      stat:
        marketMode === "sp500"
          ? formatActionLabel(activePrediction?.finalAction)
          : routeSummary.length
            ? `${routeSummary.length} legs`
            : "waiting",
      meta:
        marketMode === "sp500"
          ? selectedEquity
            ? `${selectedEquity.symbol} direct analysis`
            : "Search a ticker to expose execution context"
          : routeSummary.length > 0
          ? `${formatPercent(priceImpact)} impact`
          : "Analyze to expose leg-level route context",
      status:
        marketMode === "sp500"
          ? activePrediction?.supported
            ? "active"
            : selectedEquity
              ? "ready"
              : "idle"
          : routeSummary.length > 0
            ? "active"
            : "idle",
      description:
        marketMode === "sp500"
          ? "This node summarizes the practical execution context for a single S&P500 ticker after the Prophet and wrapper layers have voted."
          : "This node summarizes how Jupiter plans to route the swap, and acts as the bridge between AI recommendation and the real path quality you would execute on.",
      highlights: [
        {
          label: marketMode === "sp500" ? "Ticker" : "Legs",
          value: marketMode === "sp500" ? selectedEquity?.symbol || "--" : String(routeSummary.length || "--"),
        },
        {
          label: marketMode === "sp500" ? "Action" : "Price impact",
          value: marketMode === "sp500" ? formatActionLabel(activePrediction?.finalAction) : formatPercent(priceImpact),
        },
        {
          label: marketMode === "sp500" ? "Cadence" : "Duplicates collapsed",
          value: marketMode === "sp500"
            ? activePrediction?.cadenceProfile || "--"
            : String(analysisMeta.duplicateCount),
        },
        { label: "Active step", value: activePrediction?.symbol || "--" },
      ],
      bullets:
        marketMode === "sp500"
          ? [
              "Local S&P500-only mode does not call Jupiter or token routes.",
              "This panel keeps execution context focused on the selected ticker, price, timing, and wrapper consensus.",
              "Use the portfolio and information-map panels for wider universe context.",
            ]
          : [
              "Click a route leg below to focus the graph on that specific prediction.",
              "The route panel is where prediction quality meets actual execution context.",
              "Duplicate hop pairs are collapsed before running expensive inference.",
            ],
    },
    {
      id: "risk",
      x: 1420,
      y: 410,
      eyebrow: "Risk",
      title: "Execution risk",
      stat: executionRisk ? `${Math.round(executionRisk.score)}/100` : "waiting",
      meta: executionRisk
        ? `${executionRisk.tier} · ${executionRisk.recommendedMode}`
        : marketMode === "sp500"
          ? "Wrapper disagreement and timing fragility"
          : "Entropy divided by effective depth",
      status: executionRiskTone,
      description:
        marketMode === "sp500"
          ? "This node converts wrapper disagreement, timing fragility, and forecast uncertainty into a practical execution regime for the focused ticker."
          : "This node converts route entropy, whale-style concentration, effective depth, and wrapper vetoes into an execution regime for the focused step.",
      highlights: [
        { label: "Tier", value: executionRisk?.tier || "--" },
        { label: "Entropy", value: formatFractionPercent(executionRisk?.entropyScore, 1) },
        {
          label: "Effective depth",
          value: formatUsdValue(executionRisk?.effectiveDepthUsd),
        },
        {
          label: "Policy",
          value: activeExecutionAction?.label || executionRisk?.recommendedMode || "--",
        },
      ],
      bullets: executionRisk?.reasons || [
        "Run Analyze to measure entropy, whale concentration, and effective depth.",
        "The risk gauge translates those signals into an execution policy.",
      ],
    },
    {
      id: "llm",
      x: 1420,
      y: 590,
      eyebrow: "Narrative",
      title: "LLM synthesis",
      stat: llm ? "ready" : "pending",
      meta: llmHeadline,
      status: llm ? "active" : "idle",
      description:
        hideStockOnlyLlmRouteFields
          ? "After numeric predictions finish, the LLM layer explains the stock decision in plain language with macro, timing, seasonality, and portfolio context."
          : "After numeric predictions finish, the LLM layer explains the action in plain language and adds a dedicated next-action date suggestion.",
      highlights: [
        { label: "Final note", value: llmHeadline },
        ...(!hideStockOnlyLlmRouteFields
          ? [{ label: "Route guidance", value: llmRouteGuidance }]
          : []),
        { label: "Macro", value: llmMacroBackdrop },
        { label: "Momentum", value: llmMomentumSummary },
        { label: "Timing", value: llmTimingSummary },
        { label: "Spike sustain", value: llmSpikeSustainSummary },
        { label: "Drop linger", value: llmDrawdownLingerSummary },
        { label: "Regret", value: llmRegretSummary },
        { label: "Buffett-style", value: llmBuffettView },
        { label: "Druckenmiller-style", value: llmDruckenmillerView },
        { label: "Lynch-style", value: llmLynchView },
        { label: "Dalio-style", value: llmDalioView },
        { label: "Macbook view", value: llmMacbookView },
        ...(!hideStockOnlyLlmRouteFields ? [{ label: "Buy view", value: llm?.buy || "--" }] : []),
        { label: "Wait view", value: llm?.wait || "--" },
        ...(!hideStockOnlyLlmRouteFields
          ? [{ label: "Next action date", value: llmNextActionDate }]
          : []),
      ],
      bullets: [
        "This node adds narrative framing on top of the raw metrics.",
        "The stock-mode path now mixes live macro context from M2 and policy-rate data into the prompt.",
        "A dedicated momentum agent explains first and second moment behavior.",
        "A timing agent summarizes the best buy and sell windows from the Prophet curve.",
        "A spike-sustain agent explains how long an upside burst could keep running before fading.",
        "A drawdown-linger agent explains how long a sharp drop could remain depressed before recovery.",
        "A regret agent explains whether acting now is more likely to create buy regret or sell regret in the next phase.",
        "Five investor-lens agents now add Buffett-style, Druckenmiller-style, Lynch-style, Dalio-style, and Macbook portfolio-feedback takes for human comparison.",
        ...(!hideStockOnlyLlmRouteFields
          ? ["A dedicated date agent suggests when the next action message should be sent."]
          : []),
        "It is useful for human review, but the graph still functions even if the LLM call fails.",
        "The AI council remains grounded in route-step predictions before this summary appears.",
      ],
    },
  ];

  const graphEdges: GraphEdge[] = [
    { from: "source", to: "dir10", tone: "active" },
    { from: "source", to: "dir5", tone: "active" },
    { from: "source", to: "dir1", tone: "active" },
    { from: "source", to: "low", tone: "active" },
    { from: "source", to: "high", tone: "active" },
    { from: "cache", to: "dir10", tone: "muted", dashed: true },
    { from: "cache", to: "dir5", tone: "muted", dashed: true },
    { from: "cache", to: "dir1", tone: "muted", dashed: true },
    { from: "dir10", to: "decision", tone: "positive" },
    { from: "dir5", to: "decision", tone: "positive" },
    { from: "dir1", to: "decision", tone: "positive" },
    { from: "low", to: "decision", tone: "active" },
    { from: "high", to: "decision", tone: "active" },
    { from: "decision", to: "wrapper-final", tone: "active" },
    { from: "decision", to: "wrapper-ttb", tone: "active" },
    { from: "decision", to: "wrapper-em", tone: "active" },
    { from: "decision", to: "wrapper-linger", tone: "active" },
    { from: "decision", to: "wrapper-regret", tone: "active" },
    { from: "decision", to: "wrapper-conservative", tone: "active" },
    { from: "decision", to: "wrapper-cost", tone: "active" },
    { from: "wrapper-final", to: "wrapper", tone: "positive" },
    { from: "wrapper-ttb", to: "wrapper", tone: "active" },
    { from: "wrapper-em", to: "wrapper", tone: "active" },
    { from: "wrapper-linger", to: "wrapper", tone: "active" },
    { from: "wrapper-regret", to: "wrapper", tone: "active" },
    { from: "wrapper-conservative", to: "wrapper", tone: "active" },
    { from: "wrapper-cost", to: "wrapper", tone: "active" },
    { from: "wrapper", to: "route", tone: "active" },
    { from: "wrapper", to: "risk", tone: "active" },
    { from: "route", to: "risk", tone: "active" },
    { from: "decision", to: "llm", tone: "active" },
    { from: "risk", to: "llm", tone: "muted", dashed: true },
  ];

  const graphNodeMap = Object.fromEntries(
    graphNodes.map((node) => [node.id, node])
  ) as Record<string, GraphNode>;
  const selectedGraphNode =
    graphNodeMap[selectedGraphNodeId] ||
    graphNodes.find((node) => node.id === "decision") ||
    graphNodes[0];
  const recentNodePulseIds = new Set(
    agentEvents
      .filter((eventItem) => {
        if (!eventItem.createdAt) {
          return false;
        }
        const createdAt = new Date(eventItem.createdAt).getTime();
        return Number.isFinite(createdAt) && Date.now() - createdAt < 45000;
      })
      .map((eventItem) => eventItem.nodeId)
  );

  return (
    <main className="app-shell">
      {/* SaaS Credit Tracker & Header */}
      <header className="saas-header">
        <div className="saas-logo">
          <div className="saas-logo-icon"></div>
          <span className="saas-logo-text">No Slip SaaS</span>
          <span className="saas-badge-beta">PROPHET V2</span>
        </div>
        <div className="saas-user-widget">
          {userProfile ? (
            <div className="saas-credits-tracker">
              <span className={`plan-badge ${userProfile.plan}`}>
                {userProfile.plan} plan
              </span>
              <span>Credits: <strong className="credits-amount">{userProfile.credits}</strong></span>
              <button 
                onClick={fetchUserProfile} 
                className="saas-refresh-btn" 
                title="Refresh Credits"
                type="button"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/>
                </svg>
              </button>
            </div>
          ) : (
            <div className="saas-credits-tracker">Loading profile...</div>
          )}
          <button 
            type="button"
            className="saas-manage-plan-btn"
            onClick={() => {
              const pricingEl = document.getElementById("saas-pricing-section");
              if (pricingEl) {
                pricingEl.scrollIntoView({ behavior: "smooth" });
              }
            }}
          >
            Upgrade &amp; Buy Credits
          </button>
        </div>
      </header>

      <section className={`hero-panel ${marketMode === "sp500" ? "hero-panel-stock" : ""}`}>
        {marketMode === "sp500" ? (
          <div className="hero-copy">
            <p className="eyebrow">Local S&amp;P500 workspace</p>
            <h1>No Slip</h1>
            <p className="hero-text">
              Localhost is tuned for S&amp;P500-only analysis: direct Prophet signals,
              information maps, portfolio optimization, CSV portfolio review, and LLM
              agent synthesis without the crypto route workflow.
            </p>

            <div className="step-list">
              <div className="step-card">
                <span>1</span>
                Search an S&amp;P500 ticker such as AAPL, MSFT, NVDA, TSLA, or SPY.
              </div>
              <div className="step-card">
                <span>2</span>
                Run the direct Prophet signal and inspect trend, seasonality, regret, and
                wrapper agents.
              </div>
              <div className="step-card">
                <span>3</span>
                Build the information map or optimized portfolio when you want full-universe
                context.
              </div>
            </div>
          </div>
        ) : marketMode === "crypto" ? (
          <div className="hero-copy">
            <p className="eyebrow">Feature merge</p>
            <h1>No Slip</h1>
            <p className="hero-text">
              Main now carries the richer feature-branch experience: Jupiter route inspection,
              step-level Prophet predictions, an execution-risk gauge, and an interactive graph
              that shows how the AI workflow arrives at each route decision.
            </p>

            <div className="step-list">
              <div className="step-card">
                <span>1</span>
                Search a token to fetch the Jupiter route plan from SOL.
              </div>
              <div className="step-card">
                <span>2</span>
                Analyze unique route steps with constrained Prophet inference.
              </div>
              <div className="step-card">
                <span>3</span>
                Read the risk gauge to choose market, TWAP, private routing, or a hard halt based
                on entropy and effective depth.
              </div>
            </div>
          </div>
        ) : null}

        <div className={`swap-card ${marketMode === "sp500" ? "swap-card-stock" : ""}`}>
          <div className="card-header">
            <div>
              <p className="card-label">
                {marketMode === "crypto" ? "Route analysis" : "Equity analysis"}
              </p>
              <h2>
                {marketMode === "crypto"
                  ? "Crypto route workflow"
                  : "S&P500 direct signal workflow"}
              </h2>
            </div>
            <div className="action-stack header-action-stack">
              {marketMode === "sp500" ? (
                <>
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={() => handleBuildSp500InformationMap(Boolean(sp500InformationMap))}
                    disabled={isAnalyzing || isBuildingSp500Map || isBuildingSp500Portfolio}
                  >
                    {isBuildingSp500Map
                      ? "Building map..."
                      : sp500InformationMap
                        ? "Refresh info map"
                        : "Build info map"}
                  </button>
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={() => handleBuildSp500Portfolio(Boolean(sp500Portfolio))}
                    disabled={isAnalyzing || isBuildingSp500Map || isBuildingSp500Portfolio}
                  >
                    {isBuildingSp500Portfolio
                      ? "Building portfolio..."
                      : sp500Portfolio
                        ? "Refresh portfolio"
                        : "Build portfolio"}
                  </button>
                </>
              ) : null}
              <button
                className="primary-button"
                type="button"
                onClick={marketMode === "crypto" ? handleAnalyze : handleAnalyzeEquity}
                disabled={
                  marketMode === "crypto"
                    ? !selectedToken || isAnalyzing || isBuildingSp500Map || isBuildingSp500Portfolio
                    : !selectedEquity || isAnalyzing || isBuildingSp500Map || isBuildingSp500Portfolio
                }
              >
                {isAnalyzing ? "Analyzing..." : "Analyze"}
              </button>
            </div>
          </div>

          <p className="swap-intro">
            {marketMode === "crypto"
              ? "Run route-aware Prophet analysis for the selected token pair."
              : "Search a ticker, run a direct Prophet signal, then use the information map and portfolio panels below for broader context."}
          </p>

          <div className="mode-toggle" role="tablist" aria-label="Market mode">
            {!sp500OnlyMode ? (
              <button
                type="button"
                className={`mode-toggle-button ${marketMode === "crypto" ? "active" : ""}`}
                onClick={() => handleMarketModeChange("crypto")}
                aria-pressed={marketMode === "crypto"}
              >
                Crypto routes
              </button>
            ) : null}
            <button
              type="button"
              className={`mode-toggle-button ${marketMode === "sp500" ? "active" : ""}`}
              onClick={() => handleMarketModeChange("sp500")}
              aria-pressed={marketMode === "sp500"}
            >
              {sp500OnlyMode ? "S&P500 localhost mode" : "S&P500 signals"}
            </button>
          </div>

          {sp500OnlyMode ? (
            <div className="hero-inline-meta local-only-meta">
              <span className="hero-inline-meta-label">Local mode</span>
              <strong>Crypto routes disabled · S&amp;P500 APIs only</strong>
            </div>
          ) : null}

          <label className="field-label" htmlFor="token-search">
            {marketMode === "crypto" ? "Output token" : "S&P500 ticker"}
          </label>
          <div className="search-wrapper">
            <input
              id="token-search"
              className="text-input"
              placeholder={
                marketMode === "crypto"
                  ? "Search token symbol or mint"
                  : "Search ticker or company name (e.g. AAPL, MSFT, SPY)"
              }
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
              }}
              autoComplete="off"
            />

            {marketMode === "crypto" && tokens.length > 0 ? (
              <div className="dropdown">
                {tokens.slice(0, 8).map((token) => (
                  <button
                    key={token.id}
                    type="button"
                    className="dropdown-item"
                    onClick={() => handleSelectToken(token)}
                  >
                    <img
                      src={token.icon || FALLBACK_TOKEN_ICON}
                      alt=""
                      className="token-icon"
                      onError={(event) => {
                        event.currentTarget.src = FALLBACK_TOKEN_ICON;
                      }}
                    />
                    <span>
                      <strong>{token.symbol}</strong>
                      <small>{shortenAddress(token.id)}</small>
                    </span>
                  </button>
                ))}
              </div>
            ) : null}

            {marketMode === "sp500" && equities.length > 0 ? (
              <div className="dropdown">
                {equities.slice(0, 8).map((equity) => (
                  <button
                    key={equity.id}
                    type="button"
                    className="dropdown-item"
                    onClick={() => handleSelectEquity(equity)}
                  >
                    <img
                      src={equity.icon || FALLBACK_TOKEN_ICON}
                      alt=""
                      className="token-icon"
                    />
                    <span>
                      <strong>{equity.symbol}</strong>
                      <small>{equity.name}</small>
                    </span>
                  </button>
                ))}
              </div>
            ) : null}
          </div>

          {marketMode === "crypto" ? (
            <>
              <label className="field-label">Input route</label>
              <div className="amount-row">
                <div className="token-chip">
                  <img src={FALLBACK_TOKEN_ICON} alt="" className="token-icon tiny" />
                  <span>150 SOL</span>
                </div>
                <div className="token-chip">
                  <span>{`${analysisMeta.uniqueStepCount || "--"} unique steps`}</span>
                </div>
              </div>
            </>
          ) : (
            <div className="hero-inline-meta">
              <span className="hero-inline-meta-label">Mode</span>
              <strong>S&amp;P500 single-symbol Prophet signal</strong>
            </div>
          )}

          {marketMode === "crypto" && selectedToken ? (
            <div className="selection-card">
              <img
                src={selectedToken.icon || FALLBACK_TOKEN_ICON}
                alt=""
                className="token-icon large"
                onError={(event) => {
                  event.currentTarget.src = FALLBACK_TOKEN_ICON;
                }}
              />
              <div>
                <strong>{selectedToken.symbol}</strong>
                <p>{shortenAddress(selectedToken.id)}</p>
              </div>
            </div>
          ) : null}

          {marketMode === "sp500" && selectedEquity ? (
            <div className="selection-card">
              <img
                src={selectedEquity.icon || FALLBACK_TOKEN_ICON}
                alt=""
                className="token-icon large"
              />
              <div>
                <strong>{selectedEquity.symbol}</strong>
                {getEquitySelectionCaption(selectedEquity) ? (
                  <p>{getEquitySelectionCaption(selectedEquity)}</p>
                ) : null}
              </div>
            </div>
          ) : null}

          <div className={`signal-panel ${activePrediction ? activePredictionTone : ""}`}>
            <div className="signal-header">
              <div>
                <p className="card-label">
                  {marketMode === "sp500" ? "Focused ticker signal" : "Focused route step"}
                </p>
                <h3>{activePredictionHeadline}</h3>
              </div>
              <span className={`signal-badge ${activePredictionTone}`}>
                {activePredictionBadge}
              </span>
            </div>

            <p className="signal-summary">{activePredictionSummary}</p>

            <div className="signal-grid">
              <div>
                <span>Strength</span>
                <strong>{formatNumber(activePrediction?.directionStrength, 4)}</strong>
              </div>
              <div>
                <span>Vote</span>
                <strong>{formatNumber(activePrediction?.directionVote, 4)}</strong>
              </div>
              <div>
                <span>{marketMode === "sp500" ? "Live price" : "Current price"}</span>
                <strong>
                  {formatNumber(
                    marketMode === "sp500" ? activePredictionLivePrice : activePrediction?.currentPrice,
                    marketMode === "sp500" ? 2 : 6
                  )}
                </strong>
              </div>
              {marketMode === "sp500" ? (
                <div>
                  <span>Last close</span>
                  <strong>{formatNumber(activePredictionLastClose, 2)}</strong>
                </div>
              ) : null}
              <div>
                <span>Target price</span>
                <strong>{formatNumber(activePrediction?.targetPrice, marketMode === "sp500" ? 2 : 6)}</strong>
              </div>
              <div>
                <span>1st moment</span>
                <strong>{formatMomentPercentPerHour(activePrediction?.firstMomentPctPerHour)}</strong>
              </div>
              <div>
                <span>2nd moment</span>
                <strong>{formatMomentPercentPerHour2(activePrediction?.secondMomentPctPerHour2)}</strong>
              </div>
              {marketMode === "sp500" ? (
                <div>
                  <span>Seasonality</span>
                  <strong>{stockSeasonalitySummary?.headline || "--"}</strong>
                </div>
              ) : null}
              <div>
                <span>Drop linger</span>
                <strong>{formatDuration(activePrediction?.drawdownLingerSeconds)}</strong>
              </div>
              <div>
                <span>Spike sustain</span>
                <strong>
                  {formatDuration(
                    activePrediction?.spikeSustainConsensusSeconds ?? activePrediction?.spikeSustainSeconds
                  )}
                </strong>
              </div>
              <div>
                <span>Regret risk</span>
                <strong>
                  {wrapperRegretAgent?.localMetrics?.regret_risk_score != null
                    ? formatPercent(
                        Number(wrapperRegretAgent.localMetrics.regret_risk_score) * 100,
                        1
                      )
                    : "--"}
                </strong>
              </div>
              <div>
                <span>EM regime</span>
                <strong>
                  {wrapperEMRegimeAgent?.localMetrics?.em_dominant_regime != null
                    ? `${formatRegimeLabel(
                        wrapperEMRegimeAgent.localMetrics.em_dominant_regime
                      )} · ${formatConfidence(
                        Number(wrapperEMRegimeAgent.localMetrics.em_dominant_probability ?? 0)
                      )}`
                    : "--"}
                </strong>
              </div>
              <div>
                <span>Minimax guard</span>
                <strong>
                  {wrapperMinimaxAgent?.localMetrics?.minimax_worst_class != null
                    ? `${wrapperMinimaxAgent?.action || "--"} · worst ${String(
                        wrapperMinimaxAgent.localMetrics.minimax_worst_class
                      )}`
                    : wrapperMinimaxAgent?.action || "--"}
                </strong>
              </div>
              <div>
                <span>Compute route</span>
                <strong>
                  {stockMoeRuntime?.enabled
                    ? `${stockMoeRuntime.profile || "balanced"} · ${
                        stockMoeRuntime.activeExperts?.length || 0
                      } on / ${stockMoeRuntime.skippedExperts?.length || 0} paused`
                    : "all experts"}
                </strong>
              </div>
              <div>
                <span>Best buy</span>
                <strong>{formatTimestamp(activePrediction?.optimalBuyTimestamp)}</strong>
              </div>
              <div>
                <span>Best sell</span>
                <strong>{formatTimestamp(activePrediction?.optimalSellTimestamp)}</strong>
              </div>
            </div>

            {marketMode === "sp500" && stockForecastChart ? (
              <div className="trend-panel">
                <div className="trend-header">
                  <div>
                    <p className="card-label">Prophet forecast</p>
                    <h4>Forecast, uncertainty band, and changepoints</h4>
                  </div>
                  <span className="signal-meta">
                    {activePrediction?.cadenceProfile || "daily"} profile
                  </span>
                </div>

                <svg
                  viewBox={`0 0 ${stockForecastChart.width} ${stockForecastChart.height}`}
                  className="trend-chart"
                  role="img"
                  aria-label="Prophet forecast chart with changepoints"
                >
                  <line
                    x1="18"
                    x2={String(stockForecastChart.width - 18)}
                    y1={String(stockForecastChart.currentLineY)}
                    y2={String(stockForecastChart.currentLineY)}
                    className="trend-current-line"
                  />

                  {stockForecastChart.yTicks.map((tickValue, index) => {
                    const y =
                      18 +
                      ((stockForecastChart.height - 36) * index) /
                        (stockForecastChart.yTicks.length - 1 || 1);
                    return (
                      <g key={`stock-forecast-tick-${index}`}>
                        <line
                          x1="18"
                          x2={String(stockForecastChart.width - 18)}
                          y1={String(y)}
                          y2={String(y)}
                          className="trend-grid-line"
                        />
                        <text x="22" y={String(y - 6)} className="trend-axis-label">
                          {formatNumber(tickValue, 2)}
                        </text>
                      </g>
                    );
                  })}

                  {stockForecastChart.bandPath ? (
                    <path d={stockForecastChart.bandPath} className="forecast-band" />
                  ) : null}

                  {stockForecastChart.changepoints.map((point, index) => (
                    <g key={`changepoint-${index}`}>
                      <line
                        x1={String(point.x)}
                        x2={String(point.x)}
                        y1="18"
                        y2={String(stockForecastChart.height - 18)}
                        className="forecast-changepoint-line"
                      />
                    </g>
                  ))}

                  {stockForecastChart.hasActualPath ? (
                    <path d={stockForecastChart.actualPath} className="forecast-actual-line" />
                  ) : null}
                  <path d={stockForecastChart.forecastPath} className="trend-line" />

                  {stockForecastChart.buyMarker ? (
                    <g>
                      <circle
                        cx={String(stockForecastChart.buyMarker.x)}
                        cy={String(stockForecastChart.buyMarker.y)}
                        r="5"
                        className="trend-buy-point"
                      />
                      <text
                        x={String(stockForecastChart.buyMarker.x + 8)}
                        y={String(stockForecastChart.buyMarker.y - 10)}
                        className="trend-buy-label"
                      >
                        {stockForecastChart.buyMarker.label}
                      </text>
                    </g>
                  ) : null}

                  {stockForecastChart.sellMarker ? (
                    <g>
                      <circle
                        cx={String(stockForecastChart.sellMarker.x)}
                        cy={String(stockForecastChart.sellMarker.y)}
                        r="5"
                        className="trend-sell-point"
                      />
                      <text
                        x={String(stockForecastChart.sellMarker.x + 8)}
                        y={String(stockForecastChart.sellMarker.y - 10)}
                        className="trend-sell-label"
                      >
                        {stockForecastChart.sellMarker.label}
                      </text>
                    </g>
                  ) : null}

                  {stockForecastChart.xTicks.map((tick, index) => (
                    <text
                      key={`stock-forecast-x-${index}`}
                      x={String(tick.x)}
                      y={String(stockForecastChart.height - 10)}
                      textAnchor={
                        index === 0
                          ? "start"
                          : index === stockForecastChart.xTicks.length - 1
                            ? "end"
                            : "middle"
                      }
                      className="trend-axis-label"
                    >
                      {tick.label}
                    </text>
                  ))}
                </svg>

                <div className="trend-legend">
                  <div>
                    <span>Last close</span>
                    <strong>{formatNumber(stockForecastChart.currentValue, 2)}</strong>
                  </div>
                  <div>
                    <span>Forecast end</span>
                    <strong>{formatNumber(stockForecastChart.lastForecastValue, 2)}</strong>
                  </div>
                  <div>
                    <span>Changepoints</span>
                    <strong>{stockForecastChart.changepoints.length || 0}</strong>
                  </div>
                  <div>
                    <span>View</span>
                    <strong>Actual + yhat + uncertainty</strong>
                  </div>
                  <div>
                    <span>Rise window</span>
                    <strong>{formatDuration(activePrediction?.riseWindowSeconds)}</strong>
                  </div>
                  <div>
                    <span>Spike sustain</span>
                    <strong>
                      {formatDuration(
                        activePrediction?.spikeSustainConsensusSeconds ?? activePrediction?.spikeSustainSeconds
                      )}
                    </strong>
                  </div>
                  <div>
                    <span>Drop linger</span>
                    <strong>{formatDuration(activePrediction?.drawdownLingerSeconds)}</strong>
                  </div>
                  <div>
                    <span>Regret risk</span>
                    <strong>
                      {wrapperRegretAgent?.localMetrics?.regret_risk_score != null
                        ? formatPercent(
                            Number(wrapperRegretAgent.localMetrics.regret_risk_score) * 100,
                            1
                          )
                        : "--"}
                    </strong>
                  </div>
                </div>
              </div>
            ) : trendChart ? (
              <div className="trend-panel">
                <div className="trend-header">
                  <div>
                    <p className="card-label">Prophet trend</p>
                    <h4>Forecast curve and turning points</h4>
                  </div>
                  <span className="signal-meta">
                    {activePrediction?.cadenceProfile || "unknown"} profile
                  </span>
                </div>

                <svg
                  viewBox={`0 0 ${trendChart.width} ${trendChart.height}`}
                  className="trend-chart"
                  role="img"
                  aria-label="Prophet trend forecast chart"
                >
                  <line
                    x1="18"
                    x2={String(trendChart.width - 18)}
                    y1={String(trendChart.currentLineY)}
                    y2={String(trendChart.currentLineY)}
                    className="trend-current-line"
                  />

                  {trendChart.yTicks.map((tickValue, index) => {
                    const y = 18 + ((trendChart.height - 36) * index) / (trendChart.yTicks.length - 1 || 1);
                    return (
                      <g key={`tick-${index}`}>
                        <line
                          x1="18"
                          x2={String(trendChart.width - 18)}
                          y1={String(y)}
                          y2={String(y)}
                          className="trend-grid-line"
                        />
                        <text x="22" y={String(y - 6)} className="trend-axis-label">
                          {formatNumber(tickValue, marketMode === "sp500" ? 2 : 6)}
                        </text>
                      </g>
                    );
                  })}

                  <path d={trendChart.linePath} className="trend-line" />

                  <circle
                    cx={String(trendChart.currentPoint.x)}
                    cy={String(trendChart.currentPoint.y)}
                    r="5"
                    className="trend-current-point"
                  />
                  <circle
                    cx={String(trendChart.lastPoint.x)}
                    cy={String(trendChart.lastPoint.y)}
                    r="4"
                    className="trend-last-point"
                  />

                  {trendChart.buyMarker ? (
                    <g>
                      <circle
                        cx={String(trendChart.buyMarker.x)}
                        cy={String(trendChart.buyMarker.y)}
                        r="5"
                        className="trend-buy-point"
                      />
                      <text
                        x={String(trendChart.buyMarker.x + 8)}
                        y={String(trendChart.buyMarker.y - 10)}
                        className="trend-buy-label"
                      >
                        {trendChart.buyMarker.label}
                      </text>
                    </g>
                  ) : null}

                  {trendChart.sellMarker ? (
                    <g>
                      <circle
                        cx={String(trendChart.sellMarker.x)}
                        cy={String(trendChart.sellMarker.y)}
                        r="5"
                        className="trend-sell-point"
                      />
                      <text
                        x={String(trendChart.sellMarker.x + 8)}
                        y={String(trendChart.sellMarker.y - 10)}
                        className="trend-sell-label"
                      >
                        {trendChart.sellMarker.label}
                      </text>
                    </g>
                  ) : null}
                </svg>

                <div className="trend-legend">
                  <div>
                    <span>{marketMode === "sp500" ? "Last close" : "Current"}</span>
                    <strong>{formatNumber(marketMode === "sp500" ? activePredictionLastClose : activePrediction?.currentPrice, marketMode === "sp500" ? 2 : 6)}</strong>
                  </div>
                  <div>
                    <span>Forecast end</span>
                    <strong>{formatNumber(trendChart.lastPoint.value, marketMode === "sp500" ? 2 : 6)}</strong>
                  </div>
                  <div>
                    <span>Rise window</span>
                    <strong>{formatDuration(activePrediction?.riseWindowSeconds)}</strong>
                  </div>
                  <div>
                    <span>Spike sustain</span>
                    <strong>
                      {formatDuration(
                        activePrediction?.spikeSustainConsensusSeconds ?? activePrediction?.spikeSustainSeconds
                      )}
                    </strong>
                  </div>
                  <div>
                    <span>Drop window</span>
                    <strong>{formatDuration(activePrediction?.dropWindowSeconds)}</strong>
                  </div>
                  <div>
                    <span>Drop linger</span>
                    <strong>{formatDuration(activePrediction?.drawdownLingerSeconds)}</strong>
                  </div>
                </div>
              </div>
            ) : null}

            {marketMode === "sp500" && stockSeasonalityCharts.length ? (
              <div className="trend-panel seasonality-panel">
                <div className="trend-header">
                  <div>
                    <p className="card-label">Prophet components</p>
                    <h4>Trend and seasonality structure</h4>
                  </div>
                  <span className="signal-meta">
                    {stockSeasonalitySummary?.sourceRule || activePrediction?.cadenceProfile || "daily"} rule
                  </span>
                </div>

                <p className="route-plan-note">
                  {stockSeasonalitySummary?.headline ||
                    "Seasonality components show recurring weekly, yearly, monthly, and quarterly timing bias for this stock."}
                </p>

                <div className="seasonality-stack">
                  {stockSeasonalityCharts.map((chart) => (
                    <div key={chart.key} className="seasonality-chart-card">
                      <div className="trend-header">
                        <div>
                          <p className="card-label">Component</p>
                          <h4>{chart.title}</h4>
                        </div>
                        <span className="signal-meta">{chart.yAxisLabel}</span>
                      </div>

                      <svg
                        viewBox={`0 0 ${chart.width} ${chart.height}`}
                        className="trend-chart seasonality-chart"
                        role="img"
                        aria-label={chart.title}
                      >
                        {chart.yTicks.map((tickValue, index) => {
                          const y = 18 + ((chart.height - 36) * index) / (chart.yTicks.length - 1 || 1);
                          return (
                            <g key={`${chart.key}-y-${index}`}>
                              <line
                                x1="18"
                                x2={String(chart.width - 18)}
                                y1={String(y)}
                                y2={String(y)}
                                className="trend-grid-line"
                              />
                              <text x="22" y={String(y - 6)} className="trend-axis-label">
                                {formatSeasonalityValue(tickValue, chart.valueType)}
                              </text>
                            </g>
                          );
                        })}

                        <path d={chart.linePath} className="trend-line seasonality-line" />

                        <circle
                          cx={String(chart.peakPoint.x)}
                          cy={String(chart.peakPoint.y)}
                          r="4"
                          className="trend-buy-point"
                        />
                        <circle
                          cx={String(chart.troughPoint.x)}
                          cy={String(chart.troughPoint.y)}
                          r="4"
                          className="trend-sell-point"
                        />

                        {chart.xTicks.map((tick, index) => (
                          <text
                            key={`${chart.key}-x-${index}`}
                            x={String(tick.x)}
                            y={String(chart.height - 10)}
                            textAnchor={index === 0 ? "start" : index === chart.xTicks.length - 1 ? "end" : "middle"}
                            className="trend-axis-label"
                          >
                            {tick.label}
                          </text>
                        ))}
                      </svg>

                      <div className="trend-legend seasonality-legend">
                        <div>
                          <span>X axis</span>
                          <strong>{chart.xAxisLabel}</strong>
                        </div>
                        <div>
                          <span>Peak</span>
                          <strong>
                            {chart.peakPoint.label} · {formatSeasonalityValue(chart.peakPoint.value, chart.valueType)}
                          </strong>
                        </div>
                        <div>
                          <span>Trough</span>
                          <strong>
                            {chart.troughPoint.label} · {formatSeasonalityValue(chart.troughPoint.value, chart.valueType)}
                          </strong>
                        </div>
                        <div>
                          <span>Signal</span>
                          <strong>
                            {chart.key === "trend"
                              ? "Long-horizon drift"
                              : stockSeasonalitySummary?.[chart.key as "weekly" | "yearly" | "monthly" | "quarterly"]?.summary ||
                                "--"}
                          </strong>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {marketMode === "sp500" && stockCorrelationForecast?.status === "ok" ? (
              <div className="trend-panel correlation-panel">
                <div className="trend-header">
                  <div>
                    <p className="card-label">Cross-symbol correlation</p>
                    <h4>Predicted relationship map versus other stocks</h4>
                  </div>
                  <span className="signal-meta">
                    {stockCorrelationForecast.networkLabel || "mixed network"}
                  </span>
                </div>

                <p className="route-plan-note">
                  {stockCorrelationForecast.methodology ||
                    "Correlation forecast blends short, medium, and long rolling return correlation windows."}
                </p>

                <div className="route-metric-grid information-map-meta-grid">
                  <div>
                    <span>As of</span>
                    <strong>{stockCorrelationForecast.asOfDate || "--"}</strong>
                  </div>
                  <div>
                    <span>Peer universe</span>
                    <strong>{formatNumber(stockCorrelationForecast.peerUniverse, 0)}</strong>
                  </div>
                  <div>
                    <span>Avg predicted corr</span>
                    <strong>
                      {stockCorrelationForecast.averagePredictedCorrelation != null
                        ? formatPercent(stockCorrelationForecast.averagePredictedCorrelation * 100, 0)
                        : "--"}
                    </strong>
                  </div>
                  <div>
                    <span>Median corr</span>
                    <strong>
                      {stockCorrelationForecast.medianPredictedCorrelation != null
                        ? formatPercent(stockCorrelationForecast.medianPredictedCorrelation * 100, 0)
                        : "--"}
                    </strong>
                  </div>
                  <div>
                    <span>Positive share</span>
                    <strong>
                      {stockCorrelationForecast.positiveShare != null
                        ? formatPercent(stockCorrelationForecast.positiveShare * 100, 0)
                        : "--"}
                    </strong>
                  </div>
                  <div>
                    <span>Inverse share</span>
                    <strong>
                      {stockCorrelationForecast.inverseShare != null
                        ? formatPercent(stockCorrelationForecast.inverseShare * 100, 0)
                        : "--"}
                    </strong>
                  </div>
                </div>

                <div className="correlation-peer-grid">
                  <div className="correlation-peer-card">
                    <div className="trend-header">
                      <div>
                        <p className="card-label">Cluster peers</p>
                        <h4>Most correlated names</h4>
                      </div>
                      <span className="signal-meta">
                        {(stockCorrelationForecast.topCorrelatedPeers || []).length} peers
                      </span>
                    </div>
                    <div className="correlation-peer-list">
                      {(stockCorrelationForecast.topCorrelatedPeers || []).map((peer, index) => (
                        <div key={`corr-peer-${peer.symbol}-${index}`} className="correlation-peer-item">
                          <div>
                            <strong>{peer.symbol || "--"}</strong>
                            <small>{peer.name || peer.sector || "S&P500 peer"}</small>
                          </div>
                          <div className="correlation-peer-values">
                            <span>
                              Corr{" "}
                              {peer.predictedCorrelation != null
                                ? formatPercent(peer.predictedCorrelation * 100, 0)
                                : "--"}
                            </span>
                            <span>
                              Conf{" "}
                              {peer.confidence != null
                                ? formatPercent(peer.confidence * 100, 0)
                                : "--"}
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="correlation-peer-card">
                    <div className="trend-header">
                      <div>
                        <p className="card-label">Diversifiers</p>
                        <h4>Lowest-correlation names</h4>
                      </div>
                      <span className="signal-meta">
                        {(stockCorrelationForecast.topDiversifiers || []).length} peers
                      </span>
                    </div>
                    <div className="correlation-peer-list">
                      {(stockCorrelationForecast.topDiversifiers || []).map((peer, index) => (
                        <div key={`div-peer-${peer.symbol}-${index}`} className="correlation-peer-item">
                          <div>
                            <strong>{peer.symbol || "--"}</strong>
                            <small>{peer.name || peer.sector || "S&P500 peer"}</small>
                          </div>
                          <div className="correlation-peer-values">
                            <span>
                              Corr{" "}
                              {peer.predictedCorrelation != null
                                ? formatPercent(peer.predictedCorrelation * 100, 0)
                                : "--"}
                            </span>
                            <span>
                              Conf{" "}
                              {peer.confidence != null
                                ? formatPercent(peer.confidence * 100, 0)
                                : "--"}
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            ) : null}

            {stockTailDiagnostics?.status === "ok" ? (
              <div className="trend-panel correlation-panel">
                <div className="trend-header">
                  <div>
                    <p className="card-label">Tail regime</p>
                    <h4>Long-tail and heavy-tail distribution check</h4>
                  </div>
                  <span className="signal-meta">
                    {stockTailDiagnostics.regimeLabel || "tail-neutral"}
                  </span>
                </div>

                <p className="route-plan-note">
                  {stockTailDiagnostics.rationale ||
                    "Tail diagnostics summarize skew, kurtosis, and extreme-move concentration in recent returns."}
                </p>

                <div className="route-metric-grid information-map-meta-grid">
                  <div>
                    <span>Lookback</span>
                    <strong>
                      {stockTailDiagnostics.lookbackDays != null
                        ? `${formatNumber(stockTailDiagnostics.lookbackDays, 0)}d`
                        : "--"}
                    </strong>
                  </div>
                  <div>
                    <span>Samples</span>
                    <strong>{formatNumber(stockTailDiagnostics.sampleSize, 0)}</strong>
                  </div>
                  <div>
                    <span>Long-tail score</span>
                    <strong>
                      {stockTailDiagnostics.longTailScore != null
                        ? formatPercent(stockTailDiagnostics.longTailScore * 100, 0)
                        : "--"}
                    </strong>
                  </div>
                  <div>
                    <span>Heavy-tail score</span>
                    <strong>
                      {stockTailDiagnostics.heavyTailScore != null
                        ? formatPercent(stockTailDiagnostics.heavyTailScore * 100, 0)
                        : "--"}
                    </strong>
                  </div>
                  <div>
                    <span>Left-tail risk</span>
                    <strong>
                      {stockTailDiagnostics.leftTailRiskScore != null
                        ? formatPercent(stockTailDiagnostics.leftTailRiskScore * 100, 0)
                        : "--"}
                    </strong>
                  </div>
                  <div>
                    <span>Skew</span>
                    <strong>{formatNumber(stockTailDiagnostics.skewness, 2)}</strong>
                  </div>
                  <div>
                    <span>Excess kurtosis</span>
                    <strong>{formatNumber(stockTailDiagnostics.excessKurtosis, 2)}</strong>
                  </div>
                  <div>
                    <span>Tail concentration</span>
                    <strong>
                      {stockTailDiagnostics.tailConcentration != null
                        ? formatPercent(stockTailDiagnostics.tailConcentration * 100, 0)
                        : "--"}
                    </strong>
                  </div>
                  <div>
                    <span>Extreme move rate</span>
                    <strong>
                      {stockTailDiagnostics.extremeMoveRate != null
                        ? formatPercent(stockTailDiagnostics.extremeMoveRate * 100, 0)
                        : "--"}
                    </strong>
                  </div>
                  <div>
                    <span>Upside tail share</span>
                    <strong>
                      {stockTailDiagnostics.upsideTailShare != null
                        ? formatPercent(stockTailDiagnostics.upsideTailShare * 100, 0)
                        : "--"}
                    </strong>
                  </div>
                  <div>
                    <span>Downside tail share</span>
                    <strong>
                      {stockTailDiagnostics.downsideTailShare != null
                        ? formatPercent(stockTailDiagnostics.downsideTailShare * 100, 0)
                        : "--"}
                    </strong>
                  </div>
                  <div>
                    <span>Hill tail index</span>
                    <strong>{formatNumber(stockTailDiagnostics.hillTailIndex, 2)}</strong>
                  </div>
                </div>
              </div>
            ) : null}
          </div>

          <div className="status-box">
            <p>{analysisMessage}</p>
            {analysisError ? <p className="error-text">{analysisError}</p> : null}
          </div>

          <div className="detail-grid">
            {marketMode === "crypto" ? (
              <>
                <div>
                  <span>Current impact</span>
                  <strong>{formatPercent(priceImpact)}</strong>
                </div>
                <div>
                  <span>Predicted lower loss</span>
                  <strong>
                    {predictedLoss != null ? `${formatNumber(predictedLoss, 8)} SOL` : "--"}
                  </strong>
                </div>
                <div>
                  <span>Optimal ETA</span>
                  <strong>{getLiveEta()}</strong>
                </div>
                <div>
                  <span>Collapsed duplicates</span>
                  <strong>{analysisMeta.duplicateCount}</strong>
                </div>
                <div>
                  <span>Best buy</span>
                  <strong>{formatTimestamp(activePrediction?.optimalBuyTimestamp)}</strong>
                </div>
                <div>
                  <span>Best sell</span>
                  <strong>{formatTimestamp(activePrediction?.optimalSellTimestamp)}</strong>
                </div>
              </>
            ) : (
              <>
                <div>
                  <span>Selected symbol</span>
                  <strong>{selectedEquity?.symbol || "--"}</strong>
                </div>
                <div>
                  <span>Live price</span>
                  <strong>{formatNumber(activePredictionLivePrice, 2)}</strong>
                </div>
                <div>
                  <span>Last close</span>
                  <strong>{formatNumber(activePredictionLastClose, 2)}</strong>
                </div>
                <div>
                  <span>Target price</span>
                  <strong>{formatNumber(activePrediction?.targetPrice, 2)}</strong>
                </div>
                <div>
                  <span>Timing enabled</span>
                  <strong>{activePrediction?.timingEnabled ? "yes" : "no"}</strong>
                </div>
                <div>
                  <span>Cadence profile</span>
                  <strong>{activePrediction?.cadenceProfile || "--"}</strong>
                </div>
                <div>
                  <span>Analysis date</span>
                  <strong>{activePrediction?.analysisDate || "--"}</strong>
                </div>
                <div>
                  <span>Best buy</span>
                  <strong>{formatTimestamp(activePrediction?.optimalBuyTimestamp)}</strong>
                </div>
                <div>
                  <span>Best sell</span>
                  <strong>{formatTimestamp(activePrediction?.optimalSellTimestamp)}</strong>
                </div>
                <div>
                  <span>1st moment</span>
                  <strong>{formatMomentPercentPerHour(activePrediction?.firstMomentPctPerHour)}</strong>
                </div>
                <div>
                  <span>Seasonality</span>
                  <strong>{stockSeasonalitySummary?.headline || "--"}</strong>
                </div>
                <div>
                  <span>Strongest component</span>
                  <strong>{stockSeasonalitySummary?.strongestComponent || "--"}</strong>
                </div>
                <div>
                  <span>Drop linger</span>
                  <strong>{formatDuration(activePrediction?.drawdownLingerSeconds)}</strong>
                </div>
                <div>
                  <span>Regret guard</span>
                  <strong>{wrapperRegretAgent?.action || "--"}</strong>
                </div>
                <div>
                  <span>EM regime</span>
                  <strong>
                    {wrapperEMRegimeAgent?.localMetrics?.em_dominant_regime != null
                      ? formatRegimeLabel(
                          wrapperEMRegimeAgent.localMetrics.em_dominant_regime
                        )
                      : "--"}
                  </strong>
                </div>
                <div>
                  <span>Minimax focus</span>
                  <strong>
                    {wrapperMinimaxAgent?.localMetrics?.minimax_adversarial_focus != null
                      ? String(wrapperMinimaxAgent.localMetrics.minimax_adversarial_focus)
                      : "--"}
                  </strong>
                </div>
              </>
            )}
          </div>

          {marketMode === "sp500" ? (
            <div className="information-map-panel">
              <div className="route-plan-header">
                <div>
                  <p className="card-label">Information map</p>
                  <h3>S&amp;P500 first-vs-second moment optimization</h3>
                </div>
                <span className="route-plan-count">
                  {sp500InformationMap?.universe?.evaluatedSymbols || topInformationPicks.length || 0} names
                </span>
              </div>

              <p className="route-plan-note">
                The map screens the S&amp;P500 each trading day and keeps the raw 1st/2nd-moment view on screen.
              </p>

              <div className="route-metric-grid information-map-meta-grid">
                <div>
                  <span>Generated</span>
                  <strong>{formatTimestamp(sp500InformationMap?.generatedAt)}</strong>
                </div>
                <div>
                  <span>Map date</span>
                  <strong>{sp500InformationMap?.mapDate || "--"}</strong>
                </div>
                <div>
                  <span>Cache</span>
                  <strong>{sp500InformationMap?.cache?.used ? "warm" : sp500InformationMap ? "fresh" : "--"}</strong>
                </div>
                <div>
                  <span>Views</span>
                  <strong>moment</strong>
                </div>
                <div>
                  <span>Top 10 shown</span>
                  <strong>{topInformationPicks.length}</strong>
                </div>
                <div>
                  <span>Selection</span>
                  <strong>{informationMapFocusSymbol || "--"}</strong>
                </div>
                <div>
                  <span>Neural model</span>
                  <strong>{informationMapNeuralModel?.status || "--"}</strong>
                </div>
                <div>
                  <span>Feature benchmark</span>
                  <strong>{informationMapFeatureBenchmark?.status || "--"}</strong>
                </div>
                <div>
                  <span>Recommended</span>
                  <strong>
                    {recommendedFeatureMethod?.method
                      ? String(recommendedFeatureMethod.method).replace(/_/g, " ")
                      : "--"}
                  </strong>
                </div>
                <div>
                  <span>Latent dim</span>
                  <strong>{formatNumber(recommendedFeatureMethod?.latentDim, 0)}</strong>
                </div>
                <div>
                  <span>Val MAE</span>
                  <strong>{formatNumber(recommendedFeatureMethod?.validationMae, 4)}</strong>
                </div>
                <div>
                  <span>Labeled rows</span>
                  <strong>{formatNumber(informationMapFeatureBenchmark?.rows, 0)}</strong>
                </div>
              </div>

              {informationMapFeatureBenchmark ? (
                <p className="route-plan-note information-map-picks-note">
                  {informationMapFeatureBenchmark.summary ||
                    informationMapFeatureBenchmark.error ||
                    "The feature benchmark will start recommending a reduction method after enough labeled next-day-return history accumulates."}
                </p>
              ) : null}

              {informationMapCards.length ? (
                <div className="information-map-layout">
                  <div className="information-map-charts-grid">
                    {informationMapCards.map(({ viewMode, meta, chart }) =>
                      chart ? (
                      <div key={meta.title} className="information-map-chart-card">
                        <div className="trend-header">
                          <div>
                            <p className="card-label">
                              {viewMode === "raw" ? "Moment map" : "Coordinate map"}
                            </p>
                            <h4>{meta.title}</h4>
                          </div>
                          <span className="signal-meta">{meta.subtitle}</span>
                        </div>

                        <svg
                          viewBox={`0 0 ${chart.width} ${chart.height}`}
                          className="information-map-chart"
                          role="img"
                          aria-label={meta.title}
                        >
                          <rect
                            x="42"
                            y="42"
                            width={String(chart.width - 84)}
                            height={String(chart.height - 84)}
                            rx="28"
                            className="info-map-surface"
                          />
                          <line
                            x1="42"
                            x2={String(chart.width - 42)}
                            y1={String(chart.yZero)}
                            y2={String(chart.yZero)}
                            className="info-map-axis zero"
                          />
                          <line
                            x1={String(chart.xZero)}
                            x2={String(chart.xZero)}
                            y1="42"
                            y2={String(chart.height - 42)}
                            className="info-map-axis zero"
                          />

                          {chart.xTicks.map((tick, index) => {
                            const x =
                              index === 0 ? 42 : index === 1 ? chart.xZero : chart.width - 42;
                            return (
                              <g key={`${meta.title}-x-${index}`}>
                                <line
                                  x1={String(x)}
                                  x2={String(x)}
                                  y1="42"
                                  y2={String(chart.height - 42)}
                                  className="info-map-axis"
                                />
                                <text
                                  x={String(x)}
                                  y={String(chart.height - 16)}
                                  textAnchor={index === 0 ? "start" : index === 2 ? "end" : "middle"}
                                  className="info-map-axis-label"
                                >
                                  {formatSignedRatio(tick, 2, chart.xTickUnit)}
                                </text>
                              </g>
                            );
                          })}

                          {chart.yTicks.map((tick, index) => {
                            const y =
                              index === 0 ? 42 : index === 1 ? chart.yZero : chart.height - 42;
                            return (
                              <g key={`${meta.title}-y-${index}`}>
                                <line
                                  x1="42"
                                  x2={String(chart.width - 42)}
                                  y1={String(y)}
                                  y2={String(y)}
                                  className="info-map-axis"
                                />
                                <text
                                  x="46"
                                  y={String(y - 8)}
                                  className="info-map-axis-label"
                                >
                                  {formatSignedRatio(
                                    tick,
                                    chart.yTickUnit ? 1 : 2,
                                    chart.yTickUnit
                                  )}
                                </text>
                              </g>
                            );
                          })}

                          {chart.frontierLinePath ? (
                            <path
                              d={chart.frontierLinePath}
                              className="info-map-geometry-line frontier"
                            />
                          ) : null}

                          {chart.projectionLinePath ? (
                            <path
                              d={chart.projectionLinePath}
                              className="info-map-geometry-line projection"
                            />
                          ) : null}

                          {chart.points.map((point) => (
                            <g
                              key={`${meta.title}-${point.symbol}`}
                              className={`info-map-point-group ${
                                point.finalAction === "BUY"
                                  ? "positive"
                                  : point.finalAction === "SELL"
                                    ? "negative"
                                    : "neutral"
                              } ${point.highlighted ? "highlighted" : ""}`}
                              onClick={() => setSelectedInformationMapSymbol(point.symbol)}
                            >
                              <circle
                                cx={String(point.cx)}
                                cy={String(point.cy)}
                                r={String(point.radius + (point.highlighted ? 1.5 : 0))}
                                className={`info-map-point ${point.topPick ? "top-pick" : ""}`}
                              />
                              {point.highlighted ? (
                                <text
                                  x={String(point.cx + 8)}
                                  y={String(point.cy - 10)}
                                  className="info-map-point-label"
                                >
                                  {point.symbol}
                                </text>
                              ) : null}
                            </g>
                          ))}

                          {chart.geometryPortfolio ? (
                            <g className="info-map-geometry-marker portfolio">
                              <circle
                                cx={String(chart.geometryPortfolio.cx)}
                                cy={String(chart.geometryPortfolio.cy)}
                                r="7"
                              />
                              <text
                                x={String(chart.geometryPortfolio.cx + 10)}
                                y={String(chart.geometryPortfolio.cy + 4)}
                                className="info-map-point-label"
                              >
                                {chart.geometryPortfolio.label}
                              </text>
                            </g>
                          ) : null}

                          {chart.geometryTarget ? (
                            <g className="info-map-geometry-marker target">
                              <circle
                                cx={String(chart.geometryTarget.cx)}
                                cy={String(chart.geometryTarget.cy)}
                                r="6"
                              />
                              <text
                                x={String(chart.geometryTarget.cx + 10)}
                                y={String(chart.geometryTarget.cy - 8)}
                                className="info-map-point-label"
                              >
                                {chart.geometryTarget.label}
                              </text>
                            </g>
                          ) : null}
                        </svg>

                        <div className="trend-legend information-map-legend">
                          <div>
                            <span>X axis</span>
                            <strong>{chart.xAxisLabel}</strong>
                          </div>
                          <div>
                            <span>Y axis</span>
                            <strong>{chart.yAxisLabel}</strong>
                          </div>
                          <div>
                            <span>Selected</span>
                            <strong>{informationMapFocusSymbol || "--"}</strong>
                          </div>
                          <div>
                            <span>Snapshot</span>
                            <strong>{sp500InformationMap?.mapDate || "--"}</strong>
                          </div>
                          {viewMode === "secondCoordinate" && sp500Portfolio?.geometry ? (
                            <div>
                              <span>Geometry fit</span>
                              <strong>
                                {formatPercent(
                                  (sp500Portfolio.geometry.alignmentScore ?? 0) * 100,
                                  1
                                )}
                              </strong>
                            </div>
                          ) : null}
                        </div>
                      </div>
                    ) : null)}
                  </div>

                  <div className="information-map-picks-card">
                    <div className="trend-header">
                      <div>
                        <p className="card-label">Optimization output</p>
                        <h4>Top 10 stock recommendations</h4>
                      </div>
                      <span className="signal-meta">
                        {topInformationPicks.length} picks
                      </span>
                    </div>

                    <p className="route-plan-note information-map-picks-note">
                      These are the ten names the optimizer is actually ranking highest right now, so they are shown directly instead of staying hidden behind the scoring step.
                    </p>

                    <div className="information-map-picks-list">
                      {topInformationPicks.map((pick, index) => (
                        <button
                          key={pick.symbol}
                          type="button"
                          className={`information-map-pick ${
                            selectedInformationMapSymbol === pick.symbol ? "selected" : ""
                          }`}
                          onClick={() => {
                            setSelectedInformationMapSymbol(pick.symbol);
                            handleSelectEquity(
                              buildManualEquitySelection(
                                pick.symbol,
                                pick.name,
                                pick.sector
                              )
                            );
                          }}
                        >
                          <div className="information-map-pick-head">
                            <div>
                              <span className="information-map-rank">#{index + 1}</span>
                              <strong>{pick.symbol}</strong>
                              <small>{pick.name || pick.sector || "S&P500 constituent"}</small>
                            </div>
                            <span
                              className={`signal-badge ${
                                pick.finalAction === "BUY"
                                  ? "positive"
                                  : pick.finalAction === "SELL"
                                    ? "negative"
                                    : "neutral"
                              }`}
                            >
                              {formatActionLabel(pick.finalAction || "HOLD")}
                            </span>
                          </div>

                          <div className="route-metric-grid">
                            <div>
                              <span>Info score</span>
                              <strong>{formatNumber(pick.optimizationScore, 2)}</strong>
                            </div>
                            <div>
                              <span>1st moment</span>
                              <strong>{formatSignedRatio(pick.firstMomentPctPerDay, 2, "%/day")}</strong>
                            </div>
                            <div>
                              <span>2nd moment</span>
                              <strong>{formatSignedRatio(pick.secondMomentBpPerDay2, 1, " bp/day²")}</strong>
                            </div>
                            <div>
                              <span>Max upside</span>
                              <strong>
                                {pick.maxUpsidePct != null
                                  ? formatPercent(pick.maxUpsidePct * 100, 2)
                                  : "--"}
                              </strong>
                            </div>
                            <div>
                              <span>Persistence</span>
                              <strong>
                                {pick.trajectory?.persistenceScore != null
                                  ? formatPercent(pick.trajectory.persistenceScore * 100, 0)
                                  : "--"}
                              </strong>
                            </div>
                            <div>
                              <span>Regime risk</span>
                              <strong>
                                {pick.trajectory?.regimeShiftRisk != null
                                  ? formatPercent(pick.trajectory.regimeShiftRisk * 100, 0)
                                  : "--"}
                              </strong>
                            </div>
                          </div>

                          <p className="information-map-pick-note">
                            {(pick.trajectory?.regimeLabel || pick.quadrant || "unknown")} · best buy {formatTimestamp(pick.optimalBuyTimestamp)} · best sell {formatTimestamp(pick.optimalSellTimestamp)}
                          </p>
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="information-map-picks-card">
                    <div className="trend-header">
                      <div>
                        <p className="card-label">Symmetry discovery</p>
                        <h4>Dark-horse candidates</h4>
                      </div>
                      <span className="signal-meta">
                        {darkHorseInformationPicks.length} names
                      </span>
                    </div>

                    <p className="route-plan-note information-map-picks-note">
                      These candidates come from the symmetry engine, which looks for underfollowed recovery pockets whose mirrored counterparts still sit in stressed parts of the map.
                    </p>

                    <div className="information-map-picks-list">
                      {darkHorseInformationPicks.map((pick, index) => (
                        <button
                          key={`dark-horse-${pick.symbol}`}
                          type="button"
                          className={`information-map-pick ${
                            selectedInformationMapSymbol === pick.symbol ? "selected" : ""
                          }`}
                          onClick={() => {
                            setSelectedInformationMapSymbol(pick.symbol);
                            handleSelectEquity(
                              buildManualEquitySelection(
                                pick.symbol,
                                pick.name,
                                pick.sector
                              )
                            );
                          }}
                        >
                          <div className="information-map-pick-head">
                            <div>
                              <span className="information-map-rank">#{index + 1}</span>
                              <strong>{pick.symbol}</strong>
                              <small>{pick.darkHorseLabel || "symmetry candidate"}</small>
                            </div>
                            <span className="signal-badge ready">
                              {formatNumber(pick.darkHorseScore, 1)}
                            </span>
                          </div>

                          <div className="route-metric-grid">
                            <div>
                              <span>Dark-horse</span>
                              <strong>{formatNumber(pick.darkHorseScore, 1)}</strong>
                            </div>
                            <div>
                              <span>Mirror</span>
                              <strong>{pick.symmetry?.counterpartSymbol || "--"}</strong>
                            </div>
                            <div>
                              <span>Symmetry residual</span>
                              <strong>{formatNumber(pick.symmetry?.residualScore, 2)}</strong>
                            </div>
                            <div>
                              <span>Underfollowed</span>
                              <strong>
                                {pick.symmetry?.underfollowedScore != null
                                  ? formatPercent(pick.symmetry.underfollowedScore * 100, 0)
                                  : "--"}
                              </strong>
                            </div>
                            <div>
                              <span>Persistence</span>
                              <strong>
                                {pick.trajectory?.persistenceScore != null
                                  ? formatPercent(pick.trajectory.persistenceScore * 100, 0)
                                  : "--"}
                              </strong>
                            </div>
                            <div>
                              <span>Max upside</span>
                              <strong>
                                {pick.maxUpsidePct != null
                                  ? formatPercent(pick.maxUpsidePct * 100, 2)
                                  : "--"}
                              </strong>
                            </div>
                          </div>

                          <p className="information-map-pick-note">
                            {pick.darkHorseRationale ||
                              pick.symmetry?.rationale ||
                              `${pick.symbol} is being flagged as a symmetry-based dark horse candidate.`}
                          </p>
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="information-map-placeholder">
                  <strong>Build the information map to screen the full S&amp;P500.</strong>
                  <p>
                    The extra button runs a full-stock Prophet screen, keeps the raw first/second-moment map visible, adds the 1st coordinate and 2nd coordinate maps, and shows the top 10 optimized recommendations.
                  </p>
                </div>
              )}

              {sp500InformationMapError ? (
                <p className="error-text information-map-error">{sp500InformationMapError}</p>
              ) : null}
            </div>
          ) : null}

          {marketMode === "sp500" && hasOriginalInformationMapCharts ? (
            <div className="information-map-panel original-information-map-panel">
              <div className="route-plan-header">
                <div>
                  <p className="card-label">Original information map</p>
                  <h3>Raw coordinate and moment spaces</h3>
                </div>
                <span className="route-plan-count">3 original views</span>
              </div>

              <p className="route-plan-note">
                This keeps the original information-map spaces visible separately from the optimized portfolio overlays: 1st vs 2nd moment, 1st coordinate, and 2nd coordinate.
              </p>

              <div className="information-map-charts-grid original-information-map-grid">
                {originalInformationMapCards.map(renderOriginalInformationMapChartCard)}
              </div>
            </div>
          ) : null}

          {marketMode === "sp500" ? (
            <div className="information-map-panel">
              <div className="route-plan-header">
                <div>
                  <p className="card-label">Portfolio mix</p>
                  <h3>Explainable optimized stock portfolio</h3>
                </div>
                <span className="route-plan-count">
                  {sp500Portfolio?.summary?.holdingsCount || topPortfolioHoldings.length || 0} holdings
                </span>
              </div>

              <p className="route-plan-note">
                This portfolio blends your in-app Prophet map signals with live prices, recent volatility, drawdown linger timing, and macro regime inputs, then turns that into both stock weights and a broader allocation mix.
              </p>

              {topPortfolioHoldings.length ? (
                <div className="information-map-layout">
                  <div className="information-map-picks-card">
                    <div className="trend-header">
                      <div>
                        <p className="card-label">Portfolio summary</p>
                        <h4>Weight-optimized top ideas</h4>
                      </div>
                      <span className="signal-meta">
                        {sp500Portfolio?.methodology?.objective || "Map + volatility + turnover"}
                      </span>
                    </div>

                    <div className="route-metric-grid information-map-meta-grid">
                      <div>
                        <span>Generated</span>
                        <strong>{formatTimestamp(sp500Portfolio?.generatedAt)}</strong>
                      </div>
                      <div>
                        <span>Map date</span>
                        <strong>{sp500Portfolio?.mapDate || "--"}</strong>
                      </div>
                      <div>
                        <span>Weighted upside</span>
                        <strong>
                          {sp500Portfolio?.summary?.weightedUpsidePct != null
                            ? formatPercent(sp500Portfolio.summary.weightedUpsidePct * 100, 2)
                            : "--"}
                        </strong>
                      </div>
                      <div>
                        <span>Weighted uncertainty</span>
                        <strong>
                          {sp500Portfolio?.summary?.weightedUncertaintyPct != null
                            ? formatPercent(sp500Portfolio.summary.weightedUncertaintyPct * 100, 2)
                            : "--"}
                        </strong>
                      </div>
                      <div>
                        <span>Weighted volatility</span>
                        <strong>
                          {sp500Portfolio?.summary?.weightedVolatilityPct != null
                            ? formatPercent(sp500Portfolio.summary.weightedVolatilityPct, 1)
                            : "--"}
                        </strong>
                      </div>
                      <div>
                        <span>Weighted drop linger</span>
                        <strong>
                          {sp500Portfolio?.summary?.weightedDrawdownLingerDays != null
                            ? formatDuration(sp500Portfolio.summary.weightedDrawdownLingerDays * 86_400)
                            : "--"}
                        </strong>
                      </div>
                      <div>
                        <span>Sectors</span>
                        <strong>{sp500Portfolio?.summary?.sectorCount ?? "--"}</strong>
                      </div>
                      <div>
                        <span>Spike sustain</span>
                        <strong>
                          {sp500Portfolio?.summary?.weightedSpikeSustainDays != null
                            ? formatDuration(sp500Portfolio.summary.weightedSpikeSustainDays * 86_400)
                            : "--"}
                        </strong>
                      </div>
                      <div>
                        <span>Dark-horse exposure</span>
                        <strong>{formatNumber(sp500Portfolio?.summary?.weightedDarkHorseScore, 1)}</strong>
                      </div>
                      <div>
                        <span>Belief</span>
                        <strong>{formatNumber(sp500Portfolio?.summary?.weightedBeliefScore, 1)}</strong>
                      </div>
                      <div>
                        <span>Belief agreement</span>
                        <strong>
                          {sp500Portfolio?.summary?.weightedBeliefAgreement != null
                            ? formatPercent(sp500Portfolio.summary.weightedBeliefAgreement * 100, 0)
                            : "--"}
                        </strong>
                      </div>
                      <div>
                        <span>Belief spread</span>
                        <strong>
                          {sp500Portfolio?.summary?.weightedBeliefPolarization != null
                            ? formatPercent(sp500Portfolio.summary.weightedBeliefPolarization * 100, 0)
                            : "--"}
                        </strong>
                      </div>
                      <div>
                        <span>Small-cap tail</span>
                        <strong>
                          {sp500Portfolio?.summary?.weightedSmallCapTailScore != null
                            ? formatPercent(sp500Portfolio.summary.weightedSmallCapTailScore * 100, 0)
                            : "--"}
                        </strong>
                      </div>
                      <div>
                        <span>Heavy-tail premium</span>
                        <strong>
                          {sp500Portfolio?.summary?.weightedHeavyTailPremium != null
                            ? formatPercent(sp500Portfolio.summary.weightedHeavyTailPremium * 100, 1)
                            : "--"}
                        </strong>
                      </div>
                      <div>
                        <span>Long-tail bias</span>
                        <strong>
                          {sp500Portfolio?.summary?.weightedLongTailScore != null
                            ? formatPercent(sp500Portfolio.summary.weightedLongTailScore * 100, 0)
                            : "--"}
                        </strong>
                      </div>
                      <div>
                        <span>Left-tail stress</span>
                        <strong>
                          {sp500Portfolio?.summary?.weightedLeftTailRiskScore != null
                            ? formatPercent(sp500Portfolio.summary.weightedLeftTailRiskScore * 100, 0)
                            : "--"}
                        </strong>
                      </div>
                      <div>
                        <span>Small-cap pulse</span>
                        <strong>
                          {sp500Portfolio?.redditSmallCap?.heatScore != null
                            ? formatPercent(sp500Portfolio.redditSmallCap.heatScore * 100, 0)
                            : "--"}
                        </strong>
                      </div>
                      <div>
                        <span>Pulse regime</span>
                        <strong>{sp500Portfolio?.redditSmallCap?.regime || "--"}</strong>
                      </div>
                      <div>
                        <span>Korean surge</span>
                        <strong>
                          {sp500Portfolio?.fmkoreaStock?.heatScore != null
                            ? formatPercent(sp500Portfolio.fmkoreaStock.heatScore * 100, 0)
                            : "--"}
                        </strong>
                      </div>
                      <div>
                        <span>KR regime</span>
                        <strong>{sp500Portfolio?.fmkoreaStock?.regime || "--"}</strong>
                      </div>
                      <div>
                        <span>U.S. sleeve</span>
                        <strong>
                          {portfolioSleeves.length
                            ? formatPercent(
                                portfolioSleeves.find((sleeve) => sleeve.label === "U.S. equities")?.weightPct,
                                1
                              )
                            : "--"}
                        </strong>
                      </div>
                      <div>
                        <span>Intl sleeve</span>
                        <strong>
                          {portfolioSleeves.length
                            ? formatPercent(
                                portfolioSleeves.find((sleeve) => sleeve.label === "International equities")?.weightPct,
                                1
                              )
                            : "--"}
                        </strong>
                      </div>
                      <div>
                        <span>Avg correlation</span>
                        <strong>
                          {portfolioCorrelationForecast?.averagePredictedCorrelation != null
                            ? formatPercent(
                                portfolioCorrelationForecast.averagePredictedCorrelation * 100,
                                0
                              )
                            : "--"}
                        </strong>
                      </div>
                      <div>
                        <span>Diversification</span>
                        <strong>
                          {portfolioCorrelationForecast?.diversificationScore != null
                            ? formatPercent(
                                portfolioCorrelationForecast.diversificationScore * 100,
                                0
                              )
                            : "--"}
                        </strong>
                      </div>
                      <div>
                        <span>Crowded pair risk</span>
                        <strong>
                          {portfolioCorrelationForecast?.crowdedPairRiskScore != null
                            ? formatPercent(
                                portfolioCorrelationForecast.crowdedPairRiskScore * 100,
                                0
                              )
                            : "--"}
                        </strong>
                      </div>
                      <div>
                        <span>Correlation label</span>
                        <strong>
                          {portfolioCorrelationForecast?.concentrationRiskLabel || "--"}
                        </strong>
                      </div>
                    </div>

                    {portfolioCorrelationForecast?.status === "ok" ? (
                      <p className="route-plan-note information-map-picks-note">
                        Correlation forecast:{" "}
                        {portfolioCorrelationForecast.concentrationRiskLabel || "unknown"} · crowded
                        pairs{" "}
                        {(portfolioCorrelationForecast.topCrowdedPairs || [])
                          .slice(0, 2)
                          .map(
                            (entry) =>
                              `${entry.leftSymbol}/${entry.rightSymbol} ${
                                entry.predictedCorrelation != null
                                  ? formatPercent(entry.predictedCorrelation * 100, 0)
                                  : "--"
                              }`
                          )
                          .join(", ") || "--"}{" "}
                        · diversifiers{" "}
                        {(portfolioCorrelationForecast.topDiversifyingPairs || [])
                          .slice(0, 2)
                          .map(
                            (entry) =>
                              `${entry.leftSymbol}/${entry.rightSymbol} ${
                                entry.predictedCorrelation != null
                                  ? formatPercent(entry.predictedCorrelation * 100, 0)
                                  : "--"
                              }`
                          )
                          .join(", ") || "--"}
                      </p>
                    ) : null}

                    <div className="information-map-picks-list">
                      {topPortfolioHoldings.map((holding, index) => (
                        <button
                          key={`${holding.symbol}-${index}`}
                          type="button"
                          className="information-map-pick"
                          onClick={() => {
                            setSelectedEquity({
                              id: holding.symbol,
                              symbol: holding.symbol,
                              name: holding.name || holding.symbol,
                              icon: FALLBACK_TOKEN_ICON,
                              sector: holding.sector,
                            });
                            setQuery(holding.symbol);
                            setSelectedInformationMapSymbol(holding.symbol);
                            appendEvent(
                              "Portfolio holding selected",
                              `${holding.symbol} was selected from the optimized portfolio list.`,
                              "active",
                              "source"
                            );
                          }}
                        >
                          <div className="information-map-pick-head">
                            <div>
                              <span className="information-map-rank">#{index + 1}</span>
                              <strong>{holding.symbol}</strong>
                              <small>
                                {holding.name || holding.sector || "S&P500 constituent"}
                                {holding.weightPct != null
                                  ? ` · U.S. sleeve ${formatPercent(holding.weightPct, 1)}`
                                  : ""}
                              </small>
                            </div>
                            <span className="signal-badge positive">
                              {formatPercent(holding.portfolioWeightPct ?? holding.weightPct, 1)}
                            </span>
                          </div>

                          <div className="route-metric-grid">
                            <div>
                              <span>Portfolio wt</span>
                              <strong>{formatPercent(holding.portfolioWeightPct, 1)}</strong>
                            </div>
                            <div>
                              <span>Live price</span>
                              <strong>{formatNumber(holding.livePrice, 2)}</strong>
                            </div>
                            <div>
                              <span>Last close</span>
                              <strong>{formatNumber(holding.lastClosePrice ?? holding.currentPrice, 2)}</strong>
                            </div>
                            <div>
                              <span>Max upside</span>
                              <strong>
                                {holding.maxUpsidePct != null
                                  ? formatPercent(holding.maxUpsidePct * 100, 2)
                                  : "--"}
                              </strong>
                            </div>
                            <div>
                              <span>Uncertainty</span>
                              <strong>
                                {holding.uncertaintyRatio != null
                                  ? formatPercent(holding.uncertaintyRatio * 100, 2)
                                  : "--"}
                              </strong>
                            </div>
                            <div>
                              <span>Turnover</span>
                              <strong>{holding.turnoverPotential || "--"}</strong>
                            </div>
                            <div>
                              <span>Volatility</span>
                              <strong>{formatPercent(holding.annualizedVolatilityPct, 1)}</strong>
                            </div>
                            <div>
                              <span>Drop linger</span>
                              <strong>{formatDuration(holding.drawdownLingerSeconds)}</strong>
                            </div>
                            <div>
                              <span>Spike sustain</span>
                              <strong>{formatDuration(holding.spikeSustainSeconds)}</strong>
                            </div>
                            <div>
                              <span>Dark horse</span>
                              <strong>{formatNumber(holding.darkHorseScore, 1)}</strong>
                            </div>
                            <div>
                              <span>Belief</span>
                              <strong>{formatNumber(holding.beliefScore, 1)}</strong>
                            </div>
                            <div>
                              <span>Private signal</span>
                              <strong>{formatNumber(holding.beliefNetwork?.privateSignalPct, 1)}</strong>
                            </div>
                            <div>
                              <span>Crowd belief</span>
                              <strong>{formatNumber(holding.beliefNetwork?.crowdBeliefPct, 1)}</strong>
                            </div>
                            <div>
                              <span>Agreement</span>
                              <strong>
                                {holding.beliefNetwork?.agreementRatio != null
                                  ? formatPercent(holding.beliefNetwork.agreementRatio * 100, 0)
                                  : "--"}
                              </strong>
                            </div>
                            <div>
                              <span>Cap bucket</span>
                              <strong>{holding.marketCapBucket || "--"}</strong>
                            </div>
                            <div>
                              <span>Small-cap tail</span>
                              <strong>
                                {holding.smallCapTailScore != null
                                  ? formatPercent(holding.smallCapTailScore * 100, 0)
                                  : "--"}
                              </strong>
                            </div>
                            <div>
                              <span>Tail premium</span>
                              <strong>
                                {holding.heavyTailPremium != null
                                  ? formatPercent(holding.heavyTailPremium * 100, 1)
                                  : "--"}
                              </strong>
                            </div>
                            <div>
                              <span>Long-tail bias</span>
                              <strong>
                                {holding.longTailScore != null
                                  ? formatPercent(holding.longTailScore * 100, 0)
                                  : "--"}
                              </strong>
                            </div>
                            <div>
                              <span>Left-tail stress</span>
                              <strong>
                                {holding.leftTailRiskScore != null
                                  ? formatPercent(holding.leftTailRiskScore * 100, 0)
                                  : "--"}
                              </strong>
                            </div>
                            <div>
                              <span>Tail regime</span>
                              <strong>{holding.tailRegimeLabel || "--"}</strong>
                            </div>
                            <div>
                              <span>Tail skew</span>
                              <strong>{formatNumber(holding.tailSkewness, 2)}</strong>
                            </div>
                            <div>
                              <span>Excess kurtosis</span>
                              <strong>{formatNumber(holding.tailExcessKurtosis, 2)}</strong>
                            </div>
                            <div>
                              <span>Korean surge</span>
                              <strong>
                                {holding.fmkoreaSurgeScore != null
                                  ? formatPercent(holding.fmkoreaSurgeScore * 100, 0)
                                  : "--"}
                              </strong>
                            </div>
                            <div>
                              <span>KR mentions</span>
                              <strong>{formatNumber(holding.fmkoreaMentionCount, 0)}</strong>
                            </div>
                            <div>
                              <span>NG target wt</span>
                              <strong>{formatPercent(holding.naturalGradientTargetWeightPct, 1)}</strong>
                            </div>
                            <div>
                              <span>NG bound wt</span>
                              <strong>{formatPercent(holding.naturalGradientBoundWeightPct, 1)}</strong>
                            </div>
                            <div>
                              <span>NG lift</span>
                              <strong>{formatPercent(holding.naturalGradientLiftPct, 1)}</strong>
                            </div>
                            <div>
                              <span>Persistence</span>
                              <strong>
                                {holding.trajectory?.persistenceScore != null
                                  ? formatPercent(holding.trajectory.persistenceScore * 100, 0)
                                  : "--"}
                              </strong>
                            </div>
                            <div>
                              <span>Regime risk</span>
                              <strong>
                                {holding.trajectory?.regimeShiftRisk != null
                                  ? formatPercent(holding.trajectory.regimeShiftRisk * 100, 0)
                                  : "--"}
                              </strong>
                            </div>
                            <div>
                              <span>Geometry fit</span>
                              <strong>
                                {holding.geometryAlignmentScore != null
                                  ? formatPercent(holding.geometryAlignmentScore * 100, 0)
                                  : "--"}
                              </strong>
                            </div>
                            <div>
                              <span>Avg corr</span>
                              <strong>
                                {holding.averagePredictedCorrelation != null
                                  ? formatPercent(holding.averagePredictedCorrelation * 100, 0)
                                  : "--"}
                              </strong>
                            </div>
                            <div>
                              <span>Corr peer</span>
                              <strong>{holding.strongestCorrelationPeer || "--"}</strong>
                            </div>
                            <div>
                              <span>Diversifier</span>
                              <strong>{holding.strongestDiversifierPeer || "--"}</strong>
                            </div>
                          </div>

                          <p className="information-map-pick-note">
                            {holding.rationale ||
                              `${holding.symbol} weight was optimized from upside, persistence, uncertainty, turnover, diversification, and small-cap heavy-tail optionality.`}
                          </p>
                        </button>
                      ))}
                    </div>

                    {sp500Portfolio?.redditSmallCap ? (
                      <div className="information-map-pick-note" style={{ marginTop: 16 }}>
                        Small-cap pulse board:{" "}
                        {sp500Portfolio.redditSmallCap.regime || "unknown"} · heat{" "}
                        {sp500Portfolio.redditSmallCap.heatScore != null
                          ? formatPercent(sp500Portfolio.redditSmallCap.heatScore * 100, 0)
                          : "--"}{" "}
                        · top tickers{" "}
                        {(sp500Portfolio.redditSmallCap.topTickers || [])
                          .slice(0, 4)
                          .map((entry) => `${entry.symbol} ${entry.mentions ?? "--"}`)
                          .join(", ") || "--"}
                      </div>
                    ) : null}

                    {sp500Portfolio?.fmkoreaStock ? (
                      <div className="information-map-pick-note" style={{ marginTop: 10 }}>
                        Korean surge pulse:{" "}
                        {sp500Portfolio.fmkoreaStock.regime || "unknown"} · heat{" "}
                        {sp500Portfolio.fmkoreaStock.heatScore != null
                          ? formatPercent(sp500Portfolio.fmkoreaStock.heatScore * 100, 0)
                          : "--"}{" "}
                        · top tickers{" "}
                        {(sp500Portfolio.fmkoreaStock.topTickers || [])
                          .slice(0, 4)
                          .map((entry) => `${entry.symbol} ${entry.mentions ?? "--"}`)
                          .join(", ") || "--"}
                      </div>
                    ) : null}
                  </div>

                  {portfolioChampionAgent || portfolioManifold ? (
                    <div className="information-map-picks-card">
                      <div className="trend-header">
                        <div>
                          <p className="card-label">Champion agent</p>
                          <h4>
                            {portfolioChampionAgent?.selectedLabel || "Temporal submanifold champion"}
                          </h4>
                        </div>
                        <span className="signal-meta">
                          {portfolioManifold?.neuralBridge?.mode || "svd bridge"} · rank{" "}
                          {portfolioManifold?.rank ?? portfolioChampionAgent?.rank ?? "--"}
                        </span>
                      </div>

                      <p className="information-map-pick-note">
                        {portfolioChampionAgent?.rationale ||
                          "The portfolio engine compares multiple profile agents, projects them onto a temporal submanifold, and keeps the champion closest to the learned trajectory."}
                      </p>

                      <div className="route-metric-grid information-map-meta-grid">
                        <div>
                          <span>Selected profile</span>
                          <strong>{portfolioChampionAgent?.selectedProfile || "--"}</strong>
                        </div>
                        <div>
                          <span>Belief</span>
                          <strong>{formatNumber(sp500Portfolio?.summary?.weightedBeliefScore, 1)}</strong>
                        </div>
                        <div>
                          <span>Belief agreement</span>
                          <strong>
                            {sp500Portfolio?.summary?.weightedBeliefAgreement != null
                              ? formatPercent(sp500Portfolio.summary.weightedBeliefAgreement * 100, 0)
                              : "--"}
                          </strong>
                        </div>
                        <div>
                          <span>Belief spread</span>
                          <strong>
                            {sp500Portfolio?.summary?.weightedBeliefPolarization != null
                              ? formatPercent(sp500Portfolio.summary.weightedBeliefPolarization * 100, 0)
                              : "--"}
                          </strong>
                        </div>
                        <div>
                          <span>Champion score</span>
                          <strong>{formatNumber(portfolioChampionAgent?.score, 3)}</strong>
                        </div>
                        <div>
                          <span>Continuity</span>
                          <strong>
                            {portfolioChampionAgent?.continuityScore != null
                              ? formatFractionPercent(portfolioChampionAgent.continuityScore, 1)
                              : "--"}
                          </strong>
                        </div>
                        <div>
                          <span>Target distance</span>
                          <strong>{formatNumber(portfolioChampionAgent?.targetDistance, 3)}</strong>
                        </div>
                        <div>
                          <span>History snapshots</span>
                          <strong>
                            {portfolioManifold?.historyCount ??
                              portfolioChampionAgent?.historyCount ??
                              "--"}
                          </strong>
                        </div>
                        <div>
                          <span>Neural bridge</span>
                          <strong>{portfolioManifold?.neuralBridge?.mode || "--"}</strong>
                        </div>
                        <div>
                          <span>Bridge loss</span>
                          <strong>{formatNumber(portfolioManifold?.neuralBridge?.loss, 4)}</strong>
                        </div>
                        <div>
                          <span>State dimension</span>
                          <strong>{portfolioManifold?.stateDimension ?? "--"}</strong>
                        </div>
                        <div>
                          <span>Natural bound</span>
                          <strong>
                            {portfolioNaturalGradient?.upperBoundScore != null
                              ? formatNumber(portfolioNaturalGradient.upperBoundScore, 3)
                              : "--"}
                          </strong>
                        </div>
                        <div>
                          <span>Live → target</span>
                          <strong>
                            {formatNumber(portfolioNaturalGradient?.liveDistanceToTarget, 3)}
                          </strong>
                        </div>
                        <div>
                          <span>Live → bound</span>
                          <strong>
                            {formatNumber(portfolioNaturalGradient?.liveDistanceToBound, 3)}
                          </strong>
                        </div>
                        <div>
                          <span>Fisher trace</span>
                          <strong>{formatNumber(portfolioNaturalGradient?.fisherTrace, 3)}</strong>
                        </div>
                        <div>
                          <span>Fisher curvature</span>
                          <strong>{formatNumber(portfolioNaturalGradient?.fisherCurvature, 3)}</strong>
                        </div>
                        <div>
                          <span>Live entropy</span>
                          <strong>{formatNumber(portfolioNaturalGradient?.liveEntropy, 3)}</strong>
                        </div>
                      </div>

                      {portfolioManifold ? (
                        <p className="information-map-pick-note">
                          {portfolioManifold.method ||
                            "Temporal portfolio submanifold learning with residual neural bridge and SVD decoder"}
                          {portfolioManifold.submanifoldLabels?.length
                            ? ` Recent manifold points: ${portfolioManifold.submanifoldLabels
                                .slice(Math.max(0, portfolioManifold.submanifoldLabels.length - 4))
                                .join(" → ")}.`
                            : ""}
                        </p>
                      ) : null}

                      {portfolioNaturalGradient ? (
                        <p className="information-map-pick-note">
                          {portfolioNaturalGradient.method ||
                            "Fisher-simplex natural-gradient bound flow"}{" "}
                          keeps the live portfolio near a conservative risk envelope while still
                          moving toward the higher-upside target distribution.
                        </p>
                      ) : null}

                      {portfolioChampionAgent?.projectedTarget ? (
                        <div className="route-metric-grid information-map-meta-grid">
                          <div>
                            <span>Projected upside</span>
                            <strong>
                              {portfolioChampionAgent.projectedTarget.weightedUpsidePct != null
                                ? formatPercent(
                                    portfolioChampionAgent.projectedTarget.weightedUpsidePct * 100,
                                    2
                                  )
                                : "--"}
                            </strong>
                          </div>
                          <div>
                            <span>Projected uncertainty</span>
                            <strong>
                              {portfolioChampionAgent.projectedTarget.weightedUncertaintyPct != null
                                ? formatPercent(
                                    portfolioChampionAgent.projectedTarget.weightedUncertaintyPct * 100,
                                    2
                                  )
                                : "--"}
                            </strong>
                          </div>
                          <div>
                            <span>Projected linger</span>
                            <strong>
                              {portfolioChampionAgent.projectedTarget.weightedDrawdownLingerDays != null
                                ? formatDuration(
                                    portfolioChampionAgent.projectedTarget
                                      .weightedDrawdownLingerDays * 86_400
                                  )
                                : "--"}
                            </strong>
                          </div>
                          <div>
                            <span>Projected geometry fit</span>
                            <strong>
                              {portfolioChampionAgent.projectedTarget.geometryAlignmentScore != null
                                ? formatFractionPercent(
                                    portfolioChampionAgent.projectedTarget.geometryAlignmentScore,
                                    1
                                  )
                                : "--"}
                            </strong>
                          </div>
                        </div>
                      ) : null}

                      {portfolioChampionAgent?.candidateScores?.length ? (
                        <div className="information-map-picks-list">
                          {portfolioChampionAgent.candidateScores.slice(0, 4).map((candidate) => (
                            <div
                              key={`${candidate.profile || candidate.label || "candidate"}-${
                                candidate.score ?? "na"
                              }`}
                              className="information-map-pick"
                            >
                              <div className="information-map-pick-head">
                                <div>
                                  <strong>{candidate.label || candidate.profile || "Candidate"}</strong>
                                  <small>{candidate.profile || "portfolio profile"}</small>
                                </div>
                                <span className="signal-badge ready">
                                  {formatNumber(candidate.score, 3)}
                                </span>
                              </div>

                              <div className="route-metric-grid">
                                <div>
                                  <span>Continuity</span>
                                  <strong>
                                    {candidate.continuityScore != null
                                      ? formatFractionPercent(candidate.continuityScore, 1)
                                      : "--"}
                                  </strong>
                                </div>
                                <div>
                                  <span>Target distance</span>
                                  <strong>{formatNumber(candidate.targetDistance, 3)}</strong>
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ) : null}

                  {portfolioAllocation ? (
                    <div className="information-map-picks-card">
                      <div className="trend-header">
                        <div>
                          <p className="card-label">Static allocation</p>
                          <h4>Suggested total-portfolio mix</h4>
                        </div>
                        <span className="signal-meta">
                          {portfolioMacro
                            ? `${portfolioMacro.liquidityRegime} · ${portfolioMacro.rateRegime}`
                            : "Model-only overlay"}
                        </span>
                      </div>

                      <p className="information-map-pick-note">
                        {portfolioAllocation.methodology ||
                          "Allocation overlay blends map scores, drawdown linger, and macro regime data."}
                      </p>

                      <div className="route-metric-grid information-map-meta-grid">
                        <div>
                          <span>Macro backdrop</span>
                          <strong>
                            {portfolioMacro
                              ? `${portfolioMacro.liquidityRegime} · ${portfolioMacro.rateRegime}`
                              : "--"}
                          </strong>
                        </div>
                        <div>
                          <span>Weighted uncertainty</span>
                          <strong>{formatPercent(portfolioRiskInputs?.weightedUncertaintyPct, 1)}</strong>
                        </div>
                        <div>
                          <span>Weighted drop linger</span>
                          <strong>
                            {portfolioRiskInputs?.weightedDrawdownLingerDays != null
                              ? formatDuration(portfolioRiskInputs.weightedDrawdownLingerDays * 86_400)
                              : "--"}
                          </strong>
                        </div>
                        <div>
                          <span>Weighted max drawdown</span>
                          <strong>{formatPercent(portfolioRiskInputs?.weightedMaxDrawdownPct, 1)}</strong>
                        </div>
                        <div>
                          <span>Geometry fit</span>
                          <strong>{formatPercent((portfolioGeometry?.alignmentScore ?? 0) * 100, 1)}</strong>
                        </div>
                        <div>
                          <span>KL distance</span>
                          <strong>{formatNumber(portfolioGeometry?.portfolioKlDivergence, 3)}</strong>
                        </div>
                      </div>

                      {portfolioMacro?.summary ? (
                        <p className="information-map-pick-note">{portfolioMacro.summary}</p>
                      ) : null}

                      {portfolioGeometry ? (
                        <p className="information-map-pick-note">
                          {portfolioGeometry.method || "KL-minimizing geometry optimization"} targets{" "}
                          {portfolioGeometry.riskProfile || "a balanced defensive growth point"} in
                          the uncertainty-adjusted space, pulling the optimized portfolio toward{" "}
                          {formatNumber(portfolioGeometry.targetPoint?.x, 2)}/{formatNumber(
                            portfolioGeometry.targetPoint?.y,
                            2
                          )} from the current weighted point{" "}
                          {formatNumber(portfolioGeometry.portfolioPoint?.x, 2)}/{formatNumber(
                            portfolioGeometry.portfolioPoint?.y,
                            2
                          )}.
                        </p>
                      ) : null}

                      <div className="information-map-picks-list">
                        {portfolioSleeves.map((sleeve) => (
                          <div key={sleeve.label} className="information-map-pick">
                            <div className="information-map-pick-head">
                              <div>
                                <strong>{sleeve.label}</strong>
                                <small>Suggested total-portfolio sleeve</small>
                              </div>
                              <span className="signal-badge positive">
                                {formatPercent(sleeve.weightPct, 1)}
                              </span>
                            </div>
                            <p className="information-map-pick-note">{sleeve.rationale}</p>
                          </div>
                        ))}
                      </div>

                      {portfolioInternationalMix.length ? (
                        <div className="information-map-picks-list">
                          {portfolioInternationalMix.map((region) => (
                            <div key={region.label} className="information-map-pick">
                              <div className="information-map-pick-head">
                                <div>
                                  <strong>{region.label}</strong>
                                  <small>Recommended mix inside the international equity sleeve</small>
                                </div>
                                <span className="signal-badge ready">
                                  {formatPercent(region.portfolioWeightPct, 1)}
                                </span>
                              </div>

                              <div className="route-metric-grid">
                                <div>
                                  <span>Total portfolio</span>
                                  <strong>{formatPercent(region.portfolioWeightPct, 1)}</strong>
                                </div>
                                <div>
                                  <span>Within intl sleeve</span>
                                  <strong>{formatPercent(region.withinInternationalEquitiesPct, 1)}</strong>
                                </div>
                              </div>

                              <p className="information-map-pick-note">
                                {region.rationale ||
                                  `${region.label} receives this share inside the international equity sleeve from the macro regime and diversification overlay.`}
                              </p>
                            </div>
                          ))}
                        </div>
                      ) : null}

                      {portfolioSectorMix.length ? (
                        <div className="information-map-picks-list">
                          {portfolioSectorMix.map((sector) => (
                            <div key={sector.sector} className="information-map-pick">
                              <div className="information-map-pick-head">
                                <div>
                                  <strong>{sector.sector}</strong>
                                  <small>Recommended sector mix inside the U.S. equity sleeve</small>
                                </div>
                                <span className="signal-badge ready">
                                  {formatPercent(sector.portfolioWeightPct, 1)}
                                </span>
                              </div>

                              <div className="route-metric-grid">
                                <div>
                                  <span>Total portfolio</span>
                                  <strong>{formatPercent(sector.portfolioWeightPct, 1)}</strong>
                                </div>
                                <div>
                                  <span>Within U.S. sleeve</span>
                                  <strong>{formatPercent(sector.withinUsEquitiesPct, 1)}</strong>
                                </div>
                                <div>
                                  <span>Weighted upside</span>
                                  <strong>{formatPercent((sector.weightedUpsidePct ?? 0) * 100, 2)}</strong>
                                </div>
                                <div>
                                  <span>Drop linger</span>
                                  <strong>
                                    {sector.weightedDrawdownLingerDays != null
                                      ? formatDuration(sector.weightedDrawdownLingerDays * 86_400)
                                      : "--"}
                                  </strong>
                                </div>
                              </div>

                              <p className="information-map-pick-note">
                                {sector.rationale ||
                                  `${sector.sector} receives this sleeve weight from the combined upside, persistence, uncertainty, and drawdown-linger profile.`}
                              </p>
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              ) : (
                <div className="information-map-placeholder">
                  <strong>Build the portfolio list to generate weighted stock recommendations.</strong>
                  <p>
                    The portfolio engine combines information-map scores, online market prices, drawdown linger, and macro regime data to propose weighted stock ideas plus a broader allocation mix.
                  </p>
                </div>
              )}

              {sp500PortfolioError ? (
                <p className="error-text information-map-error">{sp500PortfolioError}</p>
              ) : null}
            </div>
          ) : null}

          {marketMode === "crypto" && executionRisk ? (
            <div className={`risk-panel ${executionRiskTone}`}>
              <div className="risk-header">
                <div>
                  <p className="card-label">Execution risk</p>
                  <h3>
                    {Math.round(executionRisk.score)}/100 · {executionRisk.tier}
                  </h3>
                </div>
                <span className={`signal-badge ${executionRiskTone}`}>
                  {activeExecutionAction?.label || executionRisk.recommendedMode}
                </span>
              </div>

              <p className="risk-formula">
                Risk ≈ entropy / effective depth. Entropy rises when route-step
                votes split and flow becomes noisy; effective depth falls when a
                small part of the route carries most of the visible liquidity.
              </p>

              <div className="risk-gauge">
                <div className="risk-gauge-track">
                  <div
                    className={`risk-gauge-fill ${executionRiskTone}`}
                    style={{ width: `${executionRisk.score}%` }}
                  />
                  <div
                    className={`risk-gauge-needle ${executionRiskTone}`}
                    style={{ left: `${Math.min(96, Math.max(4, executionRisk.score))}%` }}
                  />
                </div>
                <div className="risk-gauge-labels">
                  <span>stable</span>
                  <span>watch</span>
                  <span>fragile</span>
                  <span>halt</span>
                </div>
              </div>

              <div className="risk-architecture-grid">
                <div>
                  <span>Phase 1</span>
                  <strong>Route ingestion</strong>
                  <p>Jupiter route shares, impact, and step-level flow.</p>
                </div>
                <div>
                  <span>Phase 2</span>
                  <strong>Entropy engine</strong>
                  <p>Cadence disagreement and route dispersion become entropy.</p>
                </div>
                <div>
                  <span>Phase 3</span>
                  <strong>Whale topology</strong>
                  <p>Concentration penalizes headline depth into effective depth.</p>
                </div>
                <div>
                  <span>Phase 4</span>
                  <strong>Execution policy</strong>
                  <p>Score maps to market, TWAP, private, or halt behavior.</p>
                </div>
              </div>

              <div className="route-metric-grid risk-metric-grid">
                <div>
                  <span>Entropy</span>
                  <strong>{formatFractionPercent(executionRisk.entropyScore, 1)}</strong>
                </div>
                <div>
                  <span>Whale dominance</span>
                  <strong>{formatFractionPercent(executionRisk.whaleDominance, 1)}</strong>
                </div>
                <div>
                  <span>Apparent depth</span>
                  <strong>{formatUsdValue(executionRisk.apparentDepthUsd)}</strong>
                </div>
                <div>
                  <span>Effective depth</span>
                  <strong>{formatUsdValue(executionRisk.effectiveDepthUsd)}</strong>
                </div>
                <div>
                  <span>Concentration penalty</span>
                  <strong>{formatFractionPercent(executionRisk.concentrationPenalty, 1)}</strong>
                </div>
                <div>
                  <span>Depth ratio</span>
                  <strong>{formatNumber(executionRisk.effectiveDepthRatio, 2)}x</strong>
                </div>
              </div>

              <div className="risk-reasons">
                {executionRisk.reasons.map((reason) => (
                  <p key={reason}>{reason}</p>
                ))}
              </div>

              <div className="risk-action-grid">
                {executionRisk.actions.map((action) => {
                  const isSelected =
                    (selectedExecutionMode || executionRisk.recommendedMode) === action.mode;

                  return (
                    <button
                      key={action.mode}
                      type="button"
                      className={`risk-action-card ${
                        action.recommended ? "recommended" : ""
                      } ${isSelected ? "selected" : ""}`}
                      disabled={!action.enabled}
                      onClick={() => handleExecutionModeSelect(action.mode)}
                    >
                      <div className="risk-action-row">
                        <strong>{action.label}</strong>
                        <span>{action.recommended ? "recommended" : action.mode}</span>
                      </div>
                      <p>{action.description}</p>
                      <small>{action.reason}</small>
                    </button>
                  );
                })}
              </div>

              <div className="status-box risk-status-box">
                <p>{executionRisk.policySummary}</p>
                {activeExecutionAction ? (
                  <p className="risk-policy-note">
                    Active policy: {activeExecutionAction.label}.{" "}
                    {activeExecutionAction.description}
                  </p>
                ) : null}
              </div>
            </div>
          ) : null}

          {marketMode === "crypto" && routeSummary.length > 0 ? (
            <div className="route-plan-panel">
              <div className="route-plan-header">
                <div>
                  <p className="card-label">Route plan</p>
                  <h3>Focus a route leg to inspect its graph</h3>
                </div>
                <span className="route-plan-count">
                  {routeSummary.length} leg{routeSummary.length === 1 ? "" : "s"}
                </span>
              </div>
              <p className="route-plan-note">
                Duplicate mint pairs are collapsed before running inference, but
                the real Jupiter leg list stays visible here for context.
              </p>

              <div className="route-plan-list">
                {routeSummary.map((routeLeg) => {
                  const routePrediction = predictionMap[routeLeg.stepKey];
                  const tone = getPredictionTone(routePrediction);

                  return (
                    <button
                      key={routeLeg.id}
                      type="button"
                      className={`route-plan-item route-plan-button ${
                        focusedStepKey === routeLeg.stepKey ? "selected-route" : ""
                      }`}
                      onClick={() => {
                        setFocusedStepKey(routeLeg.stepKey);
                        setSelectedGraphNodeId("decision");
                        appendEvent(
                          "Route leg focused",
                          `${routeLeg.label} is now the active graph step.`,
                          "active",
                          "route"
                        );
                      }}
                    >
                      <div className="route-plan-title-row">
                        <strong>{routeLeg.label}</strong>
                        <span>{routeLeg.share || "shared"}</span>
                      </div>
                      <p>{routeLeg.path || "Path details were not included in this route response."}</p>
                      <div className="route-metric-grid">
                        <div>
                          <span>Flow</span>
                          <strong>
                            {routeLeg.inputMintLabel} {"->"} {routeLeg.outputMintLabel}
                          </strong>
                        </div>
                        <div>
                          <span>Swap value</span>
                          <strong>{routeLeg.usdValue}</strong>
                        </div>
                        <div>
                          <span>Predicted action</span>
                          <strong className={`route-action ${tone}`}>
                            {formatActionLabel(routePrediction?.finalAction || "pending")}
                          </strong>
                        </div>
                        <div>
                          <span>Strength</span>
                          <strong>{formatNumber(routePrediction?.directionStrength, 4)}</strong>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          ) : null}

          {marketMode === "sp500" ? (
            <div className="result-panel portfolio-chat-panel">
              <div className="trend-header">
                <div>
                  <p className="card-label">Portfolio chat</p>
                  <h3>Upload a portfolio CSV for restructuring advice</h3>
                </div>
                <span className="signal-meta">
                  in-memory only · {Math.round(PORTFOLIO_CHAT_MAX_FILE_BYTES / 1024)}KB max
                </span>
              </div>

              <p className="portfolio-chat-note">
                CSV는 메모리에서만 읽고 저장하지 않습니다. `symbol / ticker`, `weight`, `value`,
                `shares` 계열 컬럼만 사용하고, 수식처럼 보이는 입력과 비정상 심볼은 제외합니다.
              </p>

              <div className="portfolio-chat-controls">
                <textarea
                  className="portfolio-chat-textarea"
                  value={portfolioChatPrompt}
                  onChange={(event) => setPortfolioChatPrompt(event.target.value.slice(0, 500))}
                  placeholder="예: 기술주 비중을 줄이고 방어적으로 재구성한다면 어떻게 하는 게 좋을지 설명해줘."
                />

                <div className="portfolio-chat-toolbar">
                  <label className="secondary-button portfolio-chat-file-button">
                    <span>{portfolioChatFile ? "Change CSV" : "Choose CSV"}</span>
                    <input
                      key={portfolioChatInputKey}
                      className="portfolio-chat-file-input"
                      type="file"
                      accept=".csv,text/csv"
                      onChange={(event) => {
                        const file = event.target.files?.[0] || null;
                        setPortfolioChatFile(file);
                        setPortfolioChatError("");
                      }}
                    />
                  </label>

                  <button
                    type="button"
                    className="primary-button"
                    onClick={handlePortfolioChatAnalyze}
                    disabled={isPortfolioChatting || !portfolioChatFile}
                  >
                    {isPortfolioChatting ? "Analyzing CSV..." : "Analyze portfolio CSV"}
                  </button>
                </div>

                <div className="portfolio-chat-file-meta">
                  <span>
                    Selected file: {portfolioChatFile?.name || "none"}
                  </span>
                  <span>
                    Rows/size guard: up to 250 holdings, 12 columns, {Math.round(PORTFOLIO_CHAT_MAX_FILE_BYTES / 1024)}KB
                  </span>
                </div>
              </div>

              {portfolioChatError ? (
                <p className="error-text information-map-error">{portfolioChatError}</p>
              ) : null}

              <div className="portfolio-chat-thread">
                {portfolioChatMessages.map((message) => {
                  const messageSummary = message.analysis?.summary;
                  const messageSuggestions = message.analysis?.suggestions;
                  const suggestionGroups = [
                    {
                      label: "Keep",
                      items: messageSuggestions?.keep || [],
                    },
                    {
                      label: "Reduce",
                      items: messageSuggestions?.reduce || [],
                    },
                    {
                      label: "Exit",
                      items: messageSuggestions?.exit || [],
                    },
                    {
                      label: "Add",
                      items: messageSuggestions?.add || [],
                    },
                  ].filter((group) => group.items.length > 0);

                  return (
                    <article
                      key={message.id}
                      className={`portfolio-chat-message ${message.role}`}
                    >
                      <div className="portfolio-chat-message-head">
                        <strong>{message.role === "assistant" ? "Advisor" : "You"}</strong>
                        {message.fileName ? <span>{message.fileName}</span> : null}
                      </div>

                      <p className="portfolio-chat-message-text">{message.text}</p>

                      {messageSummary ? (
                        <div className="portfolio-chat-summary-grid">
                          <div>
                            <span>Recognized</span>
                            <strong>
                              {messageSummary.recognizedHoldingsCount ?? "--"} /{" "}
                              {messageSummary.holdingsCount ?? "--"}
                            </strong>
                          </div>
                          <div>
                            <span>Overlap</span>
                            <strong>{formatPercent(messageSummary.overlapWeightPct, 1)}</strong>
                          </div>
                          <div>
                            <span>Weighted upside</span>
                            <strong>
                              {messageSummary.weightedUpsidePct != null
                                ? formatPercent(messageSummary.weightedUpsidePct * 100, 2)
                                : "--"}
                            </strong>
                          </div>
                          <div>
                            <span>Weighted uncertainty</span>
                            <strong>
                              {messageSummary.weightedUncertaintyPct != null
                                ? formatPercent(messageSummary.weightedUncertaintyPct * 100, 2)
                                : "--"}
                            </strong>
                          </div>
                          <div>
                            <span>Drawdown linger</span>
                            <strong>
                              {messageSummary.weightedDrawdownLingerDays != null
                                ? formatDuration(
                                    messageSummary.weightedDrawdownLingerDays * 86_400
                                  )
                                : "--"}
                            </strong>
                          </div>
                          <div>
                            <span>Spike sustain</span>
                            <strong>
                              {messageSummary.weightedSpikeSustainDays != null
                                ? formatDuration(
                                    messageSummary.weightedSpikeSustainDays * 86_400
                                  )
                                : "--"}
                            </strong>
                          </div>
                          <div>
                            <span>Dark-horse exposure</span>
                            <strong>{formatNumber(messageSummary.weightedDarkHorseScore, 1)}</strong>
                          </div>
                          <div>
                            <span>Unknown symbols</span>
                            <strong>
                              {messageSummary.unknownSymbols?.length
                                ? messageSummary.unknownSymbols.join(", ")
                                : "none"}
                            </strong>
                          </div>
                        </div>
                      ) : null}

                      {suggestionGroups.length ? (
                        <div className="portfolio-chat-suggestion-grid">
                          {suggestionGroups.map((group) => (
                            <div key={group.label} className="portfolio-chat-suggestion-card">
                              <div className="portfolio-chat-suggestion-head">
                                <strong>{group.label}</strong>
                                <span>{group.items.length} names</span>
                              </div>
                              <div className="portfolio-chat-suggestion-list">
                                {group.items.map((item) => (
                                  <div
                                    key={`${group.label}-${item.symbol}`}
                                    className="portfolio-chat-suggestion-item"
                                  >
                                    <strong>{item.symbol}</strong>
                                    <span>{item.rationale}</span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : null}

                      {message.analysis?.security ? (
                        <div className="portfolio-chat-security-row">
                          <span>
                            Memory-only:{" "}
                            {message.analysis.security.processedInMemory ? "yes" : "no"}
                          </span>
                          <span>
                            Persisted: {message.analysis.security.filePersisted ? "yes" : "no"}
                          </span>
                          <span>
                            Rows: {message.analysis.security.rowsProcessed ?? "--"}
                          </span>
                          <span>
                            Headers: {message.analysis.security.columnHeaders?.join(", ") || "--"}
                          </span>
                        </div>
                      ) : null}
                    </article>
                  );
                })}
              </div>
            </div>
          ) : null}

          {llm ? (
            <div className="result-panel llm-glass-panel">
              <div className="llm-glass-header">
                <h3>LLM synthesis</h3>
                <span>Liquid-glass agent views</span>
              </div>
              <div className="route-metric-grid llm-opinion-grid">
                {!hideStockOnlyLlmRouteFields ? (
                  <div className="llm-opinion-card llm-opinion-hero">
                    <span>Route guidance</span>
                    <strong>{llmRouteGuidance}</strong>
                  </div>
                ) : null}
                <div className="llm-opinion-card">
                  <span>Dynamic portfolio view</span>
                  <strong>{llmDynamicPortfolioView}</strong>
                </div>
                <div className="llm-opinion-card">
                  <span>Macro backdrop</span>
                  <strong>{llmMacroBackdrop}</strong>
                </div>
                <div className="llm-opinion-card">
                  <span>Momentum</span>
                  <strong>{llmMomentumSummary}</strong>
                </div>
                <div className="llm-opinion-card">
                  <span>Seasonality</span>
                  <strong>{llmSeasonalitySummary}</strong>
                </div>
                <div className="llm-opinion-card">
                  <span>Timing</span>
                  <strong>{llmTimingSummary}</strong>
                </div>
                <div className="llm-opinion-card">
                  <span>Drop linger</span>
                  <strong>{llmDrawdownLingerSummary}</strong>
                </div>
                <div className="llm-opinion-card">
                  <span>Regret</span>
                  <strong>{llmRegretSummary}</strong>
                </div>
                <div className="llm-opinion-card">
                  <span>Spike sustain</span>
                  <strong>{llmSpikeSustainSummary}</strong>
                </div>
                {!hideStockOnlyLlmRouteFields ? (
                  <div className="llm-opinion-card">
                    <span>Next-action date</span>
                    <strong>{llmNextActionDate}</strong>
                  </div>
                ) : null}
                <div className="llm-opinion-card llm-opinion-emphasis">
                  <span>Final decision note</span>
                  <strong>{llm?.final || "--"}</strong>
                </div>
                {!hideStockOnlyLlmRouteFields ? (
                  <div className="llm-opinion-card">
                    <span>Buy agent</span>
                    <strong>{llm?.buy || "--"}</strong>
                  </div>
                ) : null}
                <div className="llm-opinion-card">
                  <span>Wait agent</span>
                  <strong>{llm?.wait || "--"}</strong>
                </div>
                <div className="llm-opinion-card">
                  <span>Buffett-style</span>
                  <strong>{llmBuffettView}</strong>
                </div>
                <div className="llm-opinion-card">
                  <span>Druckenmiller-style</span>
                  <strong>{llmDruckenmillerView}</strong>
                </div>
                <div className="llm-opinion-card">
                  <span>Lynch-style</span>
                  <strong>{llmLynchView}</strong>
                </div>
                <div className="llm-opinion-card">
                  <span>Dalio-style</span>
                  <strong>{llmDalioView}</strong>
                </div>
                <div className="llm-opinion-card llm-opinion-macbook">
                  <span>Macbook view</span>
                  <strong>{llmMacbookView}</strong>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </section>

      <section className="insight-panel">
        <div className="insight-header">
          <div>
            <p className="card-label">Agent graph</p>
            <h2>Interactive view of the AI workflow</h2>
          </div>
          <p className="insight-copy">{graphSummary}</p>
        </div>

        <div className="graph-legend">
          <span className="legend-chip ready">ready</span>
          <span className="legend-chip active">live path</span>
          <span className="legend-chip positive">confirmed</span>
          <span className="legend-chip negative">blocked</span>
          <span className="legend-chip neutral">
            focus {activePrediction?.symbol || "none"}
          </span>
        </div>

        <div className="agent-workbench">
          <div className="agent-graph-scroll">
            <div className="agent-graph-stage">
              {graphClusters.map((cluster) => (
                <div
                  key={cluster.id}
                  className="graph-cluster"
                  style={{
                    left: cluster.x,
                    top: cluster.y,
                    width: cluster.width,
                    height: cluster.height,
                  }}
                >
                  <div className="graph-cluster-label">{cluster.label}</div>
                  <p>{cluster.caption}</p>
                </div>
              ))}

              <svg
                className="agent-graph-svg"
                viewBox="0 0 1600 720"
                role="img"
                aria-label="Graph of route-step prediction agents and wrappers"
              >
                <defs>
                  <marker
                    id="graph-arrow"
                    markerWidth="10"
                    markerHeight="10"
                    refX="8"
                    refY="5"
                    orient="auto"
                    markerUnits="strokeWidth"
                  >
                    <path d="M0,0 L10,5 L0,10 z" fill="rgba(167, 188, 255, 0.52)" />
                  </marker>
                </defs>
                {graphEdges.map((edge) => {
                  const fromNode = graphNodeMap[edge.from];
                  const toNode = graphNodeMap[edge.to];

                  if (!fromNode || !toNode) {
                    return null;
                  }

                  return (
                    <line
                      key={`${edge.from}-${edge.to}`}
                      x1={fromNode.x}
                      y1={fromNode.y}
                      x2={toNode.x}
                      y2={toNode.y}
                      markerEnd="url(#graph-arrow)"
                      className={`graph-edge ${edge.tone} ${
                        edge.dashed ? "dashed" : ""
                      }`}
                    />
                  );
                })}
              </svg>

              <div className="agent-graph-flow-layer" aria-hidden="true">
                {graphEdges.map((edge, index) => {
                  const fromNode = graphNodeMap[edge.from];
                  const toNode = graphNodeMap[edge.to];

                  if (!fromNode || !toNode) {
                    return null;
                  }

                  const dx = toNode.x - fromNode.x;
                  const dy = toNode.y - fromNode.y;
                  const distance = Math.hypot(dx, dy);
                  const angle = (Math.atan2(dy, dx) * 180) / Math.PI;
                  const left = fromNode.x;
                  const top = fromNode.y;
                  const durationSeconds = Math.max(2.2, Math.min(4.8, distance / 180));
                  const delaySeconds = (index % 6) * 0.28;

                  return (
                    <div
                      key={`flow-${edge.from}-${edge.to}`}
                      className={`graph-edge-flow ${edge.tone} ${edge.dashed ? "dashed" : ""}`}
                      style={
                        {
                          left,
                          top,
                          width: distance,
                          transform: `translateY(-50%) rotate(${angle}deg)`,
                          ["--flow-duration" as string]: `${durationSeconds}s`,
                          ["--flow-delay" as string]: `${delaySeconds}s`,
                        } as React.CSSProperties
                      }
                    >
                      <span className="graph-edge-flow-trail" />
                      <span className="graph-edge-flow-packet" />
                    </div>
                  );
                })}
              </div>

              {graphNodes.map((node) => (
                <button
                  key={node.id}
                  type="button"
                  className={`graph-node ${node.status} ${
                    selectedGraphNode?.id === node.id ? "selected" : ""
                  } ${recentNodePulseIds.has(node.id) ? "streaming" : ""} ${
                    node.id.startsWith("wrapper") ? "wrapper-node" : ""
                  }`}
                  style={{ left: node.x, top: node.y }}
                  onClick={() => setSelectedGraphNodeId(node.id)}
                >
                  <span className="graph-node-eyebrow">{node.eyebrow}</span>
                  <strong>{node.title}</strong>
                  <span className="graph-node-stat">{node.stat}</span>
                  <small>{node.meta}</small>
                </button>
              ))}
            </div>
          </div>

          <aside className="agent-sidebar">
            <div className="agent-detail-card">
              <div className="agent-detail-header">
                <div>
                  <p className="card-label">Selected node</p>
                  <h3>{selectedGraphNode.title}</h3>
                </div>
                <span className={`legend-chip ${selectedGraphNode.status}`}>
                  {selectedGraphNode.status}
                </span>
              </div>
              <p className="agent-detail-copy">{selectedGraphNode.description}</p>

              <div className="agent-highlight-grid">
                {selectedGraphNode.highlights.map((highlight) => (
                  <div key={`${selectedGraphNode.id}-${highlight.label}`}>
                    <span>{highlight.label}</span>
                    <strong>{highlight.value}</strong>
                  </div>
                ))}
              </div>

              <div className="agent-detail-list">
                {selectedGraphNode.bullets.map((bullet) => (
                  <p key={`${selectedGraphNode.id}-${bullet}`}>{bullet}</p>
                ))}
              </div>

              {(selectedGraphNode.id === "wrapper" || selectedGraphNode.id === "decision") && activePrediction?.wrapper?.consensusGraphBase64 && (
                <div className="mt-4 p-2 bg-[#1a1a1a] rounded-xl border border-[#333333] overflow-hidden">
                  <p className="card-label mb-2" style={{ fontSize: "10px", color: "var(--sub-text)" }}>
                    Decision-Making Graph
                  </p>
                  <img
                    src={`data:image/png;base64,${activePrediction.wrapper.consensusGraphBase64}`}
                    alt="Wrapper Council Consensus Graph"
                    className="w-full h-auto rounded-lg"
                    style={{ border: "1px solid #333" }}
                  />
                </div>
              )}
            </div>

            <div className="agent-event-card">
              <div className="agent-detail-header">
                <div>
                  <p className="card-label">Timeline</p>
                  <h3>Recent interactions</h3>
                </div>
                <span className="timeline-count">{agentEvents.length} events</span>
              </div>

              <div className="agent-event-list">
                {agentEvents.map((eventItem) => (
                  <button
                    key={eventItem.id}
                    type="button"
                    className={`agent-event-item ${eventItem.tone}`}
                    onClick={() => setSelectedGraphNodeId(eventItem.nodeId)}
                  >
                    <div className="agent-event-title-row">
                      <strong>{eventItem.title}</strong>
                      <span>{formatEventTime(eventItem.createdAt)}</span>
                    </div>
                    <p>{eventItem.detail}</p>
                  </button>
                ))}
              </div>
            </div>
          </aside>
        </div>
      </section>
      {/* SaaS Pricing Plans & Credit Shop */}
      <section id="saas-pricing-section" className="saas-pricing-panel">
        <div className="saas-pricing-header">
          <h2>SaaS Commercialization &amp; Pricing Plans</h2>
          <p>
            Choose a plan that fits your trading intensity, or buy direct credit bundles.
            Each single-symbol Prophet calculation consumes exactly <strong>10 credits</strong>.
          </p>
        </div>

        <div className="saas-pricing-grid">
          {/* Basic Plan */}
          <div className="saas-price-card">
            <div className="price-card-header">
              <h3>Basic Plan</h3>
              <div className="price-amount">$0<span>/month</span></div>
              <p>Perfect for casual observers. General daily S&amp;P500 market overview maps.</p>
            </div>
            <div>
              <ul className="price-features-list">
                <li>Daily general S&amp;P500 market maps</li>
                <li>Static portfolio diversification analysis</li>
                <li>Initial 100 Prophet credits included</li>
              </ul>
              <button 
                type="button" 
                className="price-action-btn"
                disabled={userProfile?.plan === 'basic' || isChargingCredits}
                onClick={() => handleSimulatePayment('plan.subscribed', { plan: 'basic' })}
              >
                {userProfile?.plan === 'basic' ? 'Current Plan' : 'Select Basic'}
              </button>
            </div>
          </div>

          {/* Pro Plan */}
          <div className="saas-price-card featured">
            <div className="price-card-header">
              <h3>Pro Plan</h3>
              <div className="price-amount">$29<span>/month</span></div>
              <p>Ideal for regular traders. Includes generous prediction credits and live data views.</p>
            </div>
            <div>
              <ul className="price-features-list">
                <li><strong>+500 Prophet credits</strong> on subscribe</li>
                <li>Single-symbol Prophet forecasting</li>
                <li>Premium Cyberpunk UI customization</li>
                <li>Detailed wrapper agent cadence parameters</li>
              </ul>
              <button 
                type="button" 
                className="price-action-btn"
                disabled={userProfile?.plan === 'pro' || isChargingCredits}
                onClick={() => handleTossPayment('plan', 'pro', 29000)}
              >
                {userProfile?.plan === 'pro' ? 'Current Plan' : 'Upgrade to Pro'}
              </button>
            </div>
          </div>

          {/* Enterprise Plan */}
          <div className="saas-price-card">
            <div className="price-card-header">
              <h3>Enterprise Plan</h3>
              <div className="price-amount">$199<span>/month</span></div>
              <p>Designed for professional quants, firms, and high-frequency algorithms.</p>
            </div>
            <div>
              <ul className="price-features-list">
                <li><strong>+2000 Prophet credits</strong> on subscribe</li>
                <li>Custom wrapper agent priority thresholds</li>
                <li>Exclusive Slack integration</li>
              </ul>
              <button 
                type="button" 
                className="price-action-btn"
                disabled={userProfile?.plan === 'enterprise' || isChargingCredits}
                onClick={() => handleTossPayment('plan', 'enterprise', 199000)}
              >
                {userProfile?.plan === 'enterprise' ? 'Current Plan' : 'Select Enterprise'}
              </button>
            </div>
          </div>
        </div>

        {/* Tokens Top-up Packs */}
        <div className="saas-tokens-topup">
          <h4>Need More Credits? Top Up Instantly</h4>
          <div className="saas-tokens-grid">
            <button 
              type="button" 
              className="saas-token-pack-btn"
              disabled={isChargingCredits}
              onClick={() => handleTossPayment('credits', 200, 2000)} // $2 = 200 credits
            >
              <strong>200 Credits</strong>
              <span>$2.00 (Toss/Stripe)</span>
            </button>
            <button 
              type="button" 
              className="saas-token-pack-btn"
              disabled={isChargingCredits}
              onClick={() => handleTossPayment('credits', 500, 5000)} // $5 = 500 credits
            >
              <strong>500 Credits</strong>
              <span>$5.00 (Toss/Stripe)</span>
            </button>
            <button 
              type="button" 
              className="saas-token-pack-btn"
              disabled={isChargingCredits}
              onClick={() => handleTossPayment('credits', 1000, 10000)} // $10 = 1000 credits
            >
              <strong>1000 Credits</strong>
              <span>$10.00 (Toss/Stripe)</span>
            </button>
          </div>
        </div>
      </section>
    </main>
  );
}
