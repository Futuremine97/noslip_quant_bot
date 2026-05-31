'use server'

import os from 'os';
import { execFile } from 'child_process';
import { promisify } from 'util';
import fs from 'fs';
import path from 'path';
import { getStepDataForProphet, type ProphetDataPoint } from './birdeye';
import { getMacroBackdrop, type MacroBackdrop } from '@/services/llm/macro';

const execFileAsync = promisify(execFile);
const PROJECT_ROOT_MARKER = path.join('services', 'trader', 'predict_signal.py');
const REMOTE_PREDICTION_TIMEOUT_MS = 25_000;
const REMOTE_SYMBOL_PREDICTION_TIMEOUT_MS = 90_000;
const REMOTE_SP500_MAP_TIMEOUT_MS = 180_000;
const REMOTE_SP500_PORTFOLIO_TIMEOUT_MS = 240_000;
const PYTHON_PATH_FALLBACKS = [
    '/opt/homebrew/bin/python3',
    '/usr/local/bin/python3',
    '/usr/local/bin/python',
    '/usr/bin/python3',
    '/Library/Developer/CommandLineTools/usr/bin/python3',
    '/var/lang/bin/python3',
    '/var/lang/bin/python',
];

export interface ProphetComponentPoint {
    timestamp?: string | null;
    label?: string | null;
    value?: number | null;
}

export interface ProphetComponentSeries {
    title?: string | null;
    xAxisLabel?: string | null;
    yAxisLabel?: string | null;
    valueType?: 'price' | 'percent' | null;
    points?: ProphetComponentPoint[];
}

export interface ProphetForecastPlotPoint {
    timestamp?: string | null;
    yhat?: number | null;
    yhatLower?: number | null;
    yhatUpper?: number | null;
    actual?: number | null;
    trend?: number | null;
    isHistory?: boolean | null;
}

export interface ProphetForecastChangepoint {
    timestamp?: string | null;
    trend?: number | null;
    forecast?: number | null;
    magnitude?: number | null;
}

export interface ProphetForecastPlot {
    title?: string | null;
    xAxisLabel?: string | null;
    yAxisLabel?: string | null;
    uncertaintyEnabled?: boolean | null;
    historyEndTimestamp?: string | null;
    points?: ProphetForecastPlotPoint[];
    changepoints?: ProphetForecastChangepoint[];
}

export interface ProphetSeasonalitySummaryItem {
    title?: string | null;
    peakLabel?: string | null;
    peakValue?: number | null;
    troughLabel?: string | null;
    troughValue?: number | null;
    strength?: number | null;
    summary?: string | null;
}

export interface ProphetSeasonalitySummary {
    sourceRule?: string | null;
    headline?: string | null;
    strongestComponent?: string | null;
    weekly?: ProphetSeasonalitySummaryItem | null;
    yearly?: ProphetSeasonalitySummaryItem | null;
    monthly?: ProphetSeasonalitySummaryItem | null;
    quarterly?: ProphetSeasonalitySummaryItem | null;
}

export interface Sp500BeliefAgent {
    name?: string | null;
    label?: string | null;
    weight?: number | null;
    biasLabel?: string | null;
    beliefPct?: number | null;
    stance?: string | null;
}

export interface Sp500BeliefNetwork {
    model?: string | null;
    privateSignalPct?: number | null;
    crowdBeliefPct?: number | null;
    humanBiasPct?: number | null;
    humanBiasLabel?: string | null;
    attentionCountShort?: number | null;
    attentionCountLong?: number | null;
    attentionIntensityPct?: number | null;
    attentionSharePct?: number | null;
    attentionTrendScore?: number | null;
    centralBeliefPct?: number | null;
    agreementRatio?: number | null;
    polarizationScore?: number | null;
    consensusAction?: string | null;
    agentCount?: number | null;
    distributedAgents?: Sp500BeliefAgent[];
}

export interface FmkoreaStockSnapshot {
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
}

export interface HumanBiasSnapshot {
    status?: string | null;
    symbol?: string | null;
    marketMode?: string | null;
    updatedAt?: string | null;
    shortWindowDays?: number | null;
    longWindowDays?: number | null;
    shortCount?: number | null;
    longCount?: number | null;
    shortSharePct?: number | null;
    longSharePct?: number | null;
    activeDays?: number | null;
    recencyDays?: number | null;
    intensityPct?: number | null;
    trendScore?: number | null;
    score?: number | null;
    label?: string | null;
    rationale?: string | null;
}

export interface MoeExpertGate {
    expert?: string | null;
    enabled?: boolean | null;
    profile?: string | null;
    run?: boolean | null;
    reason?: string | null;
    score?: number | null;
    threshold?: number | null;
    signals?: Record<string, number | null | undefined>;
}

export interface MoeRuntime {
    enabled?: boolean | null;
    profile?: string | null;
    activeExperts?: string[];
    skippedExperts?: string[];
    experts?: Record<string, MoeExpertGate>;
    budget?: {
        maxHeavyInFlight?: number | null;
    };
}

export interface CorrelationPeerForecast {
    symbol?: string | null;
    name?: string | null;
    sector?: string | null;
    currentCorrelation?: number | null;
    predictedCorrelation?: number | null;
    confidence?: number | null;
    windowSpread?: number | null;
    observations?: number | null;
}

export interface SymbolCorrelationForecast {
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
}

export interface PortfolioCorrelationPairForecast extends CorrelationPeerForecast {
    leftSymbol?: string | null;
    rightSymbol?: string | null;
    leftWeightPct?: number | null;
    rightWeightPct?: number | null;
    pairWeightPct?: number | null;
}

export interface PortfolioHoldingCorrelationForecast {
    symbol?: string | null;
    portfolioWeightPct?: number | null;
    averagePredictedCorrelation?: number | null;
    diversificationSupportScore?: number | null;
    strongestCorrelationPeer?: string | null;
    strongestCorrelationValue?: number | null;
    strongestDiversifierPeer?: string | null;
    strongestDiversifierValue?: number | null;
    confidence?: number | null;
}

export interface PortfolioCorrelationForecast {
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
}

export interface TailDiagnostics {
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
}

export interface SupportedPredictionResult {
    supported: true;
    requestedSymbol: string;
    resolvedSymbol: string;
    source?: string;
    dataset?: string | null;
    analysisDate?: string | null;
    analysisTimestampLocal?: string | null;
    rows?: number;
    finalAction: 'BUY' | 'SELL' | 'HOLD';
    directionVote?: number;
    directionStrength: number;
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
    timesfmDrawdownStartTimestamp?: string | null;
    timesfmDrawdownRecoveryTimestamp?: string | null;
    timesfmDrawdownTroughTimestamp?: string | null;
    timesfmDrawdownTroughPrice?: number | null;
    timesfmDrawdownLingerSeconds?: number | null;
    timesfmDrawdownRecoveryInHorizon?: boolean | null;
    timesfmTroughToRecoverySeconds?: number | null;
    timesfmMaxDrawdownPct?: number | null;
    timesfmQuantileBandPct?: number | null;
    timesfmSpikeStartTimestamp?: string | null;
    timesfmSpikePeakTimestamp?: string | null;
    timesfmSpikePeakPrice?: number | null;
    timesfmSpikeSustainSeconds?: number | null;
    timesfmSpikeFadeTimestamp?: string | null;
    timesfmSpikeFadeInHorizon?: boolean | null;
    timesfmPeakToFadeSeconds?: number | null;
    timesfmMaxSpikePct?: number | null;
    timesfmStatus?: string | null;
    timesfmError?: string | null;
    timesfmUsed?: boolean | null;
    timesfmModelId?: string | null;
    timesfmMoeGate?: MoeExpertGate | null;
    moeRuntime?: MoeRuntime | null;
    spikeSustainConsensusSeconds?: number | null;
    peakToFadeConsensusSeconds?: number | null;
    spikeFadeConsensusInHorizon?: boolean | null;
    maxSpikeConsensusPct?: number | null;
    spikeConsensusSource?: string | null;
    prophetSpikeWeight?: number | null;
    timesfmSpikeWeight?: number | null;
    drawdownLingerConsensusSeconds?: number | null;
    troughToRecoveryConsensusSeconds?: number | null;
    drawdownRecoveryConsensusInHorizon?: boolean | null;
    maxDrawdownConsensusPct?: number | null;
    drawdownConsensusSource?: string | null;
    trendCurve?: Array<{
        timestamp?: string | null;
        value?: number | null;
    }>;
    forecastPlot?: ProphetForecastPlot | null;
    trendComponent?: ProphetComponentSeries | null;
    seasonalityComponents?: Record<string, ProphetComponentSeries>;
    seasonalitySummary?: ProphetSeasonalitySummary | null;
    avgUncertaintyRatio?: number | null;
    geodesicState?: Record<string, unknown> | null;
    geodesicAvailable?: boolean | null;
    geodesicLabel?: string | null;
    geodesicActionBias?: string | null;
    geodesicHistoryCount?: number | null;
    geodesicPathLength?: number | null;
    geodesicCurvature?: number | null;
    geodesicAlignmentScore?: number | null;
    geodesicDeviationScore?: number | null;
    geodesicContinuationScore?: number | null;
    geodesicConfidence?: number | null;
    geodesicProjectedFirstCoordinateX?: number | null;
    geodesicProjectedFirstCoordinateY?: number | null;
    geodesicProjectedSecondCoordinateX?: number | null;
    geodesicProjectedSecondCoordinateY?: number | null;
    geodesicProjectedFirstCoordinateDrift?: number | null;
    geodesicProjectedSecondCoordinateDrift?: number | null;
    currentPrice: number;
    livePrice?: number | null;
    lastClosePrice?: number | null;
    targetPrice: number | null;
    targetTimestamp: string | null;
    timeToBelowCurrent: number | null;
    optimalBuyTimestamp?: string | null;
    optimalBuyPrice?: number | null;
    optimalSellTimestamp?: string | null;
    optimalSellPrice?: number | null;
    cadenceProfile?: string | null;
    cadenceRules?: Array<{ rule?: string; label?: string; weight?: number | null }>;
    runtimeSymbol?: string | null;
    humanBias?: HumanBiasSnapshot | null;
    correlationForecast?: SymbolCorrelationForecast | null;
    tailDiagnostics?: TailDiagnostics | null;
    championRefresh?: Record<string, unknown>;
    timingEnabled?: boolean;
    timeToBelowCurrentSeconds?: number | null;
    perRuleSummary?: Record<string, unknown>;
    wrapper?: Record<string, unknown> | null;
    recommendation: {
        summary: string;
        tone: 'positive' | 'neutral' | 'negative';
    };
}

export interface UnsupportedPredictionResult {
    supported: false;
    requestedSymbol: string;
    resolvedSymbol: string;
    reason: string;
    source?: string;
    dataset?: string | null;
}

export type PredictionResult =
    | SupportedPredictionResult
    | UnsupportedPredictionResult;

export interface Sp500InformationMapPoint {
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
    drawdownStartTimestamp?: string | null;
    drawdownRecoveryTimestamp?: string | null;
    drawdownTroughTimestamp?: string | null;
    drawdownTroughPrice?: number | null;
    drawdownLingerSeconds?: number | null;
    drawdownRecoveryInHorizon?: boolean | null;
    troughToRecoverySeconds?: number | null;
    maxDrawdownPct?: number | null;
    expectedReturnPct?: number | null;
    maxUpsidePct?: number | null;
    drawdownToBuyPct?: number | null;
    quadrant?: string | null;
    optimizationScore?: number | null;
    webNeuralScore?: number | null;
    webNeuralConfidence?: number | null;
    webNeuralLabel?: string | null;
    webNeuralNovelty?: number | null;
    darkHorseScore?: number | null;
    darkHorseLabel?: string | null;
    darkHorseRationale?: string | null;
    darkHorseRank?: number | null;
    beliefScore?: number | null;
    beliefLabel?: string | null;
    beliefRationale?: string | null;
    beliefNetwork?: Sp500BeliefNetwork | null;
    humanBiasScore?: number | null;
    humanBiasLabel?: string | null;
    humanBiasRationale?: string | null;
    humanBias?: HumanBiasSnapshot | null;
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
}

export interface Sp500InformationMapResult {
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
    fmkoreaStock?: FmkoreaStockSnapshot;
    humanBiasMarket?: {
        status?: string | null;
        marketMode?: string | null;
        updatedAt?: string | null;
        topSymbols?: HumanBiasSnapshot[];
    };
    points: Sp500InformationMapPoint[];
    topPicks: Sp500InformationMapPoint[];
    darkHorsePicks?: Sp500InformationMapPoint[];
    failures?: Array<{
        symbol: string;
        reason: string;
    }>;
    error?: string;
}

export interface Sp500PortfolioHolding {
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
    humanBiasScore?: number | null;
    humanBiasLabel?: string | null;
    humanBiasRationale?: string | null;
    humanBias?: HumanBiasSnapshot | null;
    webNeuralScore?: number | null;
    webNeuralConfidence?: number | null;
    webNeuralLabel?: string | null;
    maxDrawdownPct?: number | null;
    optimizationScore?: number | null;
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
}

export interface Sp500PortfolioGeometryPoint {
    label?: string;
    symbol?: string;
    x?: number | null;
    y?: number | null;
    weightPct?: number | null;
}

export interface Sp500PortfolioGeometryOverlay {
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
}

export interface Sp500PortfolioNaturalGradientOverlay {
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
}

export interface Sp500PortfolioSleeveRecommendation {
    label: string;
    weightPct: number;
    rationale: string;
}

export interface Sp500PortfolioSectorRecommendation {
    sector: string;
    portfolioWeightPct: number;
    withinUsEquitiesPct: number;
    weightedUpsidePct?: number | null;
    weightedUncertaintyPct?: number | null;
    weightedDrawdownLingerDays?: number | null;
    weightedSpikeSustainDays?: number | null;
    rationale?: string | null;
}

export interface Sp500PortfolioRegionRecommendation {
    label: string;
    portfolioWeightPct: number;
    withinInternationalEquitiesPct: number;
    rationale?: string | null;
}

export interface Sp500PortfolioAllocationOverlay {
    methodology?: string;
    macro?: MacroBackdrop | null;
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
        weightedDarkHorseScore?: number | null;
        weightedPersistencePct?: number | null;
        weightedRegimeRiskPct?: number | null;
        weightedKoreanSurgeScore?: number | null;
    };
}

export interface Sp500PortfolioResult {
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
        weightedPersistencePct?: number | null;
        weightedRegimeRiskPct?: number | null;
        weightedBeliefScore?: number | null;
        weightedBeliefAgreement?: number | null;
        weightedBeliefPolarization?: number | null;
        weightedHumanBiasScore?: number | null;
        weightedWebNeuralScore?: number | null;
        weightedWebNeuralConfidence?: number | null;
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
    allocation?: Sp500PortfolioAllocationOverlay;
    holdings: Sp500PortfolioHolding[];
    error?: string;
}

function relabelCoordinateSpace(
    space:
        | {
              label?: string;
              xAxis?: string;
              yAxis?: string;
          }
        | undefined,
    ordinal: "first" | "second"
) {
    const nextSpace = { ...(space || {}) };
    if (ordinal === "first") {
        if (!nextSpace.label || nextSpace.label === "m-coordinate map" || nextSpace.label === "Coordinate map") {
            nextSpace.label = "1st coordinate map";
        }
        if (!nextSpace.xAxis || nextSpace.xAxis === "m-coordinate x") {
            nextSpace.xAxis = "1st coordinate x";
        }
        if (!nextSpace.yAxis || nextSpace.yAxis === "m-coordinate y") {
            nextSpace.yAxis = "1st coordinate y";
        }
        return nextSpace;
    }

    if (!nextSpace.label || nextSpace.label === "e-coordinate map" || nextSpace.label === "Coordinate map") {
        nextSpace.label = "2nd coordinate map";
    }
    if (!nextSpace.xAxis || nextSpace.xAxis === "e-coordinate x") {
        nextSpace.xAxis = "2nd coordinate x";
    }
    if (!nextSpace.yAxis || nextSpace.yAxis === "e-coordinate y") {
        nextSpace.yAxis = "2nd coordinate y";
    }
    return nextSpace;
}

function normalizeSp500MapPoint(point: Sp500InformationMapPoint): Sp500InformationMapPoint {
    const legacyPoint = point as Sp500InformationMapPoint & {
        mCoordinate?: Sp500InformationMapPoint["firstCoordinateSpace"];
        eCoordinate?: Sp500InformationMapPoint["secondCoordinateSpace"];
        momentumSpace?: Sp500InformationMapPoint["firstCoordinateSpace"];
        convictionSpace?: Sp500InformationMapPoint["secondCoordinateSpace"];
    };

    return {
        ...point,
        firstCoordinateSpace:
            point.firstCoordinateSpace ||
            legacyPoint.momentumSpace ||
            legacyPoint.mCoordinate,
        secondCoordinateSpace:
            point.secondCoordinateSpace ||
            legacyPoint.convictionSpace ||
            legacyPoint.eCoordinate,
        symmetry: point.symmetry
            ? {
                  ...point.symmetry,
                  counterpartSymbol: point.symmetry.counterpartSymbol ?? null,
                  counterpartAction: point.symmetry.counterpartAction ?? null,
                  counterpartQuadrant: point.symmetry.counterpartQuadrant ?? null,
              }
            : undefined,
    };
}

function normalizeSp500InformationMapResult(
    payload: Sp500InformationMapResult
): Sp500InformationMapResult {
    const points = Array.isArray(payload?.points) ? payload.points.map(normalizeSp500MapPoint) : [];
    const topPicks = Array.isArray(payload?.topPicks)
        ? payload.topPicks.map(normalizeSp500MapPoint)
        : [];
    const darkHorsePicks = Array.isArray(payload?.darkHorsePicks)
        ? payload.darkHorsePicks.map(normalizeSp500MapPoint)
        : [];
    const legacyMapSpaces = (payload?.mapSpaces || {}) as Sp500InformationMapResult["mapSpaces"] & {
        momentum?: Sp500InformationMapResult["mapSpaces"] extends { firstCoordinate?: infer U }
            ? U
            : never;
        conviction?: Sp500InformationMapResult["mapSpaces"] extends { secondCoordinate?: infer U }
            ? U
            : never;
    };

    const generatedDate = payload?.generatedAt ? new Date(payload.generatedAt) : null;
    const derivedMapDate =
        payload?.mapDate ||
        (generatedDate && !Number.isNaN(generatedDate.getTime())
            ? generatedDate.toISOString().slice(0, 10)
            : undefined);

    return {
        ...payload,
        mapSpaces: {
            firstCoordinate: relabelCoordinateSpace(
                payload?.mapSpaces?.firstCoordinate || legacyMapSpaces.momentum,
                "first"
            ),
            secondCoordinate: relabelCoordinateSpace(
                payload?.mapSpaces?.secondCoordinate || legacyMapSpaces.conviction,
                "second"
            ),
        },
        mapDate: derivedMapDate,
        points,
        topPicks,
        darkHorsePicks,
    };
}

function clamp(value: number, minimum: number, maximum: number) {
    return Math.min(maximum, Math.max(minimum, value));
}

function normalizeAllocationWeights(
    entries: Array<{ label: string; weight: number; rationale: string }>
): Sp500PortfolioSleeveRecommendation[] {
    const positiveEntries = entries.map((entry) => ({
        ...entry,
        weight: Math.max(0, entry.weight),
    }));
    const total = positiveEntries.reduce((sum, entry) => sum + entry.weight, 0);
    if (total <= 0) {
        return positiveEntries.map((entry) => ({
            label: entry.label,
            weightPct: 0,
            rationale: entry.rationale,
        }));
    }

    return positiveEntries.map((entry) => ({
        label: entry.label,
        weightPct: (entry.weight / total) * 100,
        rationale: entry.rationale,
    }));
}

function weightedAverage<T>(
    holdings: T[],
    valueGetter: (holding: T) => number | null | undefined,
    weightGetter: (holding: T) => number | null | undefined
) {
    let weighted = 0;
    let weightTotal = 0;

    holdings.forEach((holding) => {
        const value = valueGetter(holding);
        const weight = weightGetter(holding);
        if (value == null || weight == null || !Number.isFinite(value) || !Number.isFinite(weight) || weight <= 0) {
            return;
        }
        weighted += value * weight;
        weightTotal += weight;
    });

    if (weightTotal <= 0) {
        return null;
    }
    return weighted / weightTotal;
}

function buildSp500PortfolioAllocation(
    result: Sp500PortfolioResult,
    macro: MacroBackdrop | null
): Sp500PortfolioAllocationOverlay | undefined {
    if (!result.ok || !Array.isArray(result.holdings) || result.holdings.length === 0) {
        return undefined;
    }

    const holdings = result.holdings;
    const weightedUncertainty =
        result.summary?.weightedUncertaintyPct ??
        weightedAverage(holdings, (holding) => holding.uncertaintyRatio ?? null, (holding) => holding.weight ?? null) ??
        0;
    const weightedVolatilityPct =
        result.summary?.weightedVolatilityPct ??
        weightedAverage(holdings, (holding) => holding.annualizedVolatilityPct ?? null, (holding) => holding.weight ?? null) ??
        0;
    const weightedDrawdownLingerDays =
        result.summary?.weightedDrawdownLingerDays ??
        weightedAverage(
            holdings,
            (holding) =>
                holding.drawdownLingerSeconds != null
                    ? holding.drawdownLingerSeconds / 86_400
                    : null,
            (holding) => holding.weight ?? null
        ) ??
        0;
    const weightedMaxDrawdownPct =
        result.summary?.weightedMaxDrawdownPct ??
        weightedAverage(
            holdings,
            (holding) =>
                holding.maxDrawdownPct != null ? Math.abs(Math.min(0, holding.maxDrawdownPct)) : null,
            (holding) => holding.weight ?? null
        ) ??
        0;
    const weightedPersistencePct =
        result.summary?.weightedPersistencePct ??
        weightedAverage(
            holdings,
            (holding) => holding.trajectory?.persistenceScore ?? null,
            (holding) => holding.weight ?? null
        ) ??
        0;
    const weightedRegimeRiskPct =
        result.summary?.weightedRegimeRiskPct ??
        weightedAverage(
            holdings,
            (holding) => holding.trajectory?.regimeShiftRisk ?? null,
            (holding) => holding.weight ?? null
        ) ??
        0;
    const weightedHeavyTailPremium =
        result.summary?.weightedHeavyTailPremium ?? 0;
    const weightedHeavyTailScore =
        result.summary?.weightedHeavyTailScore ?? 0;
    const redditSmallCapHeat =
        result.summary?.redditSmallCapHeatScore ??
        result.redditSmallCap?.heatScore ??
        0;
    const koreanSurgeHeat =
        result.summary?.fmkoreaStockHeatScore ??
        result.fmkoreaStock?.heatScore ??
        0;
    const weightedKoreanSurgeScore =
        result.summary?.weightedFmkoreaSurgeScore ?? 0;

    const liquidityTilt =
        macro?.liquidityRegime === "유동성 확장"
            ? 0.06
            : macro?.liquidityRegime === "유동성 수축"
              ? -0.08
              : 0;
    const rateTilt =
        macro?.rateRegime === "금리 완화"
            ? 0.05
            : macro?.rateRegime === "금리 긴축"
              ? -0.05
              : 0;

    const riskPenalty = clamp(
        weightedUncertainty * 0.7 +
            (weightedVolatilityPct / 100) * 0.2 +
            (weightedDrawdownLingerDays / 45) * 0.16 +
            weightedMaxDrawdownPct * 0.5 +
            weightedRegimeRiskPct * 0.18 -
            weightedPersistencePct * 0.12,
        0.05,
        0.42
    );

    const internationalTilt = clamp(
        0.11 +
            (liquidityTilt > 0 ? 0.04 : 0) +
            (rateTilt > 0 ? 0.02 : 0) +
            weightedHeavyTailPremium * 0.55 +
            weightedHeavyTailScore * 0.06 +
            redditSmallCapHeat * 0.05 -
            koreanSurgeHeat * 0.02 -
            weightedRegimeRiskPct * 0.08 -
            weightedMaxDrawdownPct * 0.18,
        0.06,
        0.26
    );

    let usEquities = clamp(0.52 + liquidityTilt + rateTilt - riskPenalty, 0.22, 0.74);
    let internationalEquities = internationalTilt;
    let treasuries = clamp(
        0.16 +
            weightedRegimeRiskPct * 0.12 +
            (weightedDrawdownLingerDays / 60) * 0.08 +
            (macro?.rateRegime === "금리 완화" ? 0.04 : 0),
        0.08,
        0.34
    );
    let gold = clamp(
        0.08 +
            weightedUncertainty * 0.08 +
            weightedMaxDrawdownPct * 0.08 +
            (macro?.liquidityRegime === "유동성 수축" ? 0.03 : 0),
        0.05,
        0.18
    );
    let cash = Math.max(0.04, 1 - usEquities - treasuries - gold);

    const overAllocated = usEquities + internationalEquities + treasuries + gold + cash - 1;
    if (overAllocated > 0) {
        const usRoom = Math.max(0, usEquities - 0.22);
        const intlRoom = Math.max(0, internationalEquities - 0.06);
        const roomTotal = usRoom + intlRoom;
        if (roomTotal > 0) {
            usEquities = Math.max(0.22, usEquities - overAllocated * (usRoom / roomTotal));
            internationalEquities = Math.max(
                0.06,
                internationalEquities - overAllocated * (intlRoom / roomTotal)
            );
        }
    }

    const sleeves = normalizeAllocationWeights([
        {
            label: "U.S. equities",
            weight: usEquities,
            rationale:
                "모델의 현재 알파와 persistence를 반영한 핵심 위험자산 슬리브입니다. uncertainty, drawdown linger, regime risk가 높아질수록 축소됩니다.",
        },
        {
            label: "International equities",
            weight: internationalEquities,
            rationale:
                "한국·중국·일본 등 해외 주식 익스포저입니다. 미국 단일 편중을 줄이고, 글로벌 정책·제조업·소형주 tail regime이 열릴 때 점진적으로 확대합니다.",
        },
        {
            label: "Treasuries / IG bonds",
            weight: treasuries,
            rationale:
                "금리와 유동성 국면, 그리고 주식 쪽 눌림 기간이 길어질수록 방어형 채권 비중을 높입니다.",
        },
        {
            label: "Gold / real assets",
            weight: gold,
            rationale:
                "불확실성과 최대 낙폭, 유동성 수축 신호가 커질수록 헤지 슬리브를 늘립니다.",
        },
        {
            label: "Cash / short duration",
            weight: Math.max(0.04, 1 - usEquities - internationalEquities - treasuries - gold),
            rationale:
                "회전 타이밍이 좋지 않거나 drawdown linger가 길 때 재진입 여력을 남기기 위한 대기 자금입니다.",
        },
    ]);

    const usEquitySleevePct =
        sleeves.find((sleeve) => sleeve.label === "U.S. equities")?.weightPct ?? 0;
    const internationalEquitySleevePct =
        sleeves.find((sleeve) => sleeve.label === "International equities")?.weightPct ?? 0;

    const sectorBuckets = new Map<
        string,
        {
            rawWeight: number;
            upside: number;
            uncertainty: number;
            drawdownLingerDays: number;
        }
    >();

    holdings.forEach((holding) => {
        const sector = (holding.sector || "Other").trim() || "Other";
        const weight = Math.max(0, holding.weight ?? 0);
        const upside = Math.max(0, holding.maxUpsidePct ?? 0);
        const uncertainty = Math.max(0, holding.uncertaintyRatio ?? 0);
        const persistence = Math.max(0, holding.trajectory?.persistenceScore ?? 0);
        const regimeRisk = Math.max(0, holding.trajectory?.regimeShiftRisk ?? 0);
        const drawdownLingerDays = Math.max(
            0,
            holding.drawdownLingerSeconds != null ? holding.drawdownLingerSeconds / 86_400 : 0
        );
        const lingerPenalty = clamp(drawdownLingerDays / 30, 0, 0.45);
        const sectorScore = weight * Math.max(
            0.05,
            1 +
                upside * 1.3 +
                persistence * 0.45 -
                uncertainty * 0.75 -
                regimeRisk * 0.55 -
                lingerPenalty
        );

        const bucket = sectorBuckets.get(sector) || {
            rawWeight: 0,
            upside: 0,
            uncertainty: 0,
            drawdownLingerDays: 0,
        };
        bucket.rawWeight += sectorScore;
        bucket.upside += weight * upside;
        bucket.uncertainty += weight * uncertainty;
        bucket.drawdownLingerDays += weight * drawdownLingerDays;
        sectorBuckets.set(sector, bucket);
    });

    const sectorTotal = Array.from(sectorBuckets.values()).reduce((sum, bucket) => sum + bucket.rawWeight, 0);
    const sectorMix = Array.from(sectorBuckets.entries())
        .map(([sector, bucket]) => {
            const withinUs = sectorTotal > 0 ? (bucket.rawWeight / sectorTotal) * 100 : 0;
            const portfolioWeightPct = (withinUs / 100) * usEquitySleevePct;
            return {
                sector,
                portfolioWeightPct,
                withinUsEquitiesPct: withinUs,
                weightedUpsidePct: bucket.upside,
                weightedUncertaintyPct: bucket.uncertainty,
                weightedDrawdownLingerDays: bucket.drawdownLingerDays,
                rationale:
                    `${sector} keeps ${(withinUs).toFixed(1)}% of the U.S. sleeve because upside and persistence outweigh ` +
                    `uncertainty and drop-linger pressure here.`,
            };
        })
        .sort((left, right) => right.portfolioWeightPct - left.portfolioWeightPct)
        .slice(0, 8);

    const developedBoost =
        macro?.rateRegime === "금리 완화" ? 0.03 : macro?.rateRegime === "금리 긴축" ? -0.02 : 0;
    const chinaCaution =
        weightedRegimeRiskPct * 0.06 + weightedMaxDrawdownPct * 0.08 - liquidityTilt * 0.04;
    const koreaTailBoost =
        weightedHeavyTailPremium * 0.42 +
        redditSmallCapHeat * 0.18 +
        koreanSurgeHeat * 0.16 +
        weightedKoreanSurgeScore * 0.22 +
        weightedPersistencePct * 0.04;
    const japanQualityBoost =
        (weightedPersistencePct * 0.06) + (macro?.rateRegime === "금리 완화" ? 0.03 : 0);
    const internationalMix = normalizeAllocationWeights([
        {
            label: "Japan equities",
            weight: clamp(0.34 + developedBoost + japanQualityBoost, 0.18, 0.44),
            rationale:
                "일본은 국제 주식 슬리브의 코어입니다. 상대적으로 안정적인 선진국 익스포저와 정책 완화/지배구조 개선 모멘텀을 반영합니다.",
        },
        {
            label: "Korea equities",
            weight: clamp(0.18 + koreaTailBoost, 0.10, 0.28),
            rationale:
                "한국은 반도체·제조업 민감도와 소형주 tail optionality를 반영한 전술 비중입니다. AI/수출 사이클이 좋을수록 확대합니다.",
        },
        {
            label: "China equities",
            weight: clamp(0.14 + liquidityTilt * 0.35 - chinaCaution, 0.06, 0.22),
            rationale:
                "중국은 정책/유동성 반응이 큰 할인 자산 바스켓으로 보고, drawdown 및 regime risk가 높아질수록 보수적으로 유지합니다.",
        },
        {
            label: "Other developed ex-U.S.",
            weight: clamp(0.22 + developedBoost, 0.14, 0.32),
            rationale:
                "미국 외 선진국 분산 효과를 위한 기본 바스켓입니다. 달러 편중과 단일 국가 리스크를 낮춰줍니다.",
        },
        {
            label: "Emerging ex-China",
            weight: clamp(0.12 + weightedHeavyTailScore * 0.12 - weightedRegimeRiskPct * 0.05, 0.06, 0.18),
            rationale:
                "고베타 신흥국 바스켓입니다. tail premium과 추세가 살아 있을 때만 제한적으로 확대하는 위성 슬리브입니다.",
        },
    ]).map((region) => ({
        ...region,
        portfolioWeightPct: (region.weightPct / 100) * internationalEquitySleevePct,
        withinInternationalEquitiesPct: region.weightPct,
    }));

    const enrichedHoldings = holdings.map((holding) => ({
        ...holding,
        portfolioWeightPct: (holding.weightPct ?? ((holding.weight ?? 0) * 100)) * (usEquitySleevePct / 100),
    }));

    result.holdings = enrichedHoldings;

    return {
        methodology:
            "정적 배분 레이어는 information map, drawdown linger, persistence, regime risk, heavy-tail optionality, Korean surge pulse, 그리고 FRED 기반 유동성/금리 국면을 함께 사용해 U.S. equity sleeve, 국제 주식 sleeve, 그리고 방어 자산 비중을 추천합니다.",
        macro,
        sleeves,
        sectorMix,
        internationalMix,
        riskInputs: {
            weightedUncertaintyPct: weightedUncertainty * 100,
            weightedVolatilityPct,
            weightedDrawdownLingerDays,
            weightedMaxDrawdownPct: weightedMaxDrawdownPct * 100,
            weightedPersistencePct: weightedPersistencePct * 100,
            weightedRegimeRiskPct: weightedRegimeRiskPct * 100,
            weightedKoreanSurgeScore: weightedKoreanSurgeScore * 100,
        },
    };
}

async function enrichSp500PortfolioResult(
    result: Sp500PortfolioResult
): Promise<Sp500PortfolioResult> {
    if (!result.ok) {
        return result;
    }

    try {
        const macro = await getMacroBackdrop();
        return {
            ...result,
            allocation: buildSp500PortfolioAllocation(result, macro),
        };
    } catch (error) {
        console.warn("[Predictor] Failed to load macro backdrop for portfolio:", error);
        return {
            ...result,
            allocation: buildSp500PortfolioAllocation(result, null),
        };
    }
}

function extractJsonLine(stdout: string): string {
    const lines = stdout
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);

    const jsonLine = [...lines].reverse().find((line) => line.startsWith("{") && line.endsWith("}"));

    if (!jsonLine) {
        throw new Error(`Prediction script returned no JSON payload. Raw stdout: ${stdout.trim() || "(empty)"}`);
    }

    return jsonLine;
}

function normalizeExecutionError(symbol: string, error: unknown): UnsupportedPredictionResult {
    const typedError = error as {
        message?: string;
        stdout?: string;
        stderr?: string;
        code?: number;
    };

    const stderr = typedError?.stderr?.trim();
    const stdout = typedError?.stdout?.trim();
    const message = typedError?.message?.trim();
    const reason =
        stderr ||
        stdout ||
        message ||
        "Prediction process failed before returning a result.";

    console.error(`[Predictor] Execution error for ${symbol}: ${reason}`);

    return {
        supported: false,
        requestedSymbol: symbol,
        resolvedSymbol: symbol,
        reason,
        source: "python_exec",
        dataset: null,
    };
}

function isExecutableFile(filePath: string): boolean {
    try {
        fs.accessSync(filePath, fs.constants.X_OK);
        return true;
    } catch {
        return false;
    }
}

function resolveProjectRoot(startDir: string = process.cwd()): string {
    let currentDir = path.resolve(startDir);

    for (let depth = 0; depth < 8; depth += 1) {
        if (fs.existsSync(path.join(currentDir, PROJECT_ROOT_MARKER))) {
            return currentDir;
        }

        const parentDir = path.dirname(currentDir);
        if (parentDir === currentDir) {
            break;
        }

        currentDir = parentDir;
    }

    return path.resolve(startDir);
}

function buildAugmentedPath(projectRoot: string): string {
    const entries = new Set<string>([
        path.join(projectRoot, 'services', 'trader', '.venv', 'bin'),
        '/opt/homebrew/bin',
        '/usr/local/bin',
        '/usr/bin',
        '/bin',
        '/Library/Developer/CommandLineTools/usr/bin',
        '/var/lang/bin',
    ]);

    const existingPath = process.env.PATH || '';
    existingPath
        .split(path.delimiter)
        .map((entry) => entry.trim())
        .filter(Boolean)
        .forEach((entry) => entries.add(entry));

    return Array.from(entries).join(path.delimiter);
}

function resolvePythonBinary(projectRoot: string): string | null {
    const candidates: string[] = [];
    const configuredBin = process.env.PREDICTION_PYTHON_BIN?.trim();

    if (configuredBin) {
        candidates.push(
            path.isAbsolute(configuredBin)
                ? configuredBin
                : path.resolve(projectRoot, configuredBin)
        );
    }

    candidates.push(
        path.join(projectRoot, 'services', 'trader', '.venv', 'bin', 'python'),
        path.join(projectRoot, 'services', 'trader', '.venv', 'bin', 'python3'),
        ...PYTHON_PATH_FALLBACKS
    );

    const pathEntries = buildAugmentedPath(projectRoot)
        .split(path.delimiter)
        .map((entry) => entry.trim())
        .filter(Boolean);

    pathEntries.forEach((entry) => {
        candidates.push(path.join(entry, 'python3'));
        candidates.push(path.join(entry, 'python'));
    });

    const seen = new Set<string>();
    for (const candidate of candidates) {
        if (!candidate || seen.has(candidate)) {
            continue;
        }

        seen.add(candidate);
        if (isExecutableFile(candidate)) {
            return candidate;
        }
    }

    return null;
}

/**
 * 서버 터미널에 최종 권장 사항을 기록합니다.
 */
export async function reportFinalRecommendation(
    avgWaitTime: number,
    currentLoss: number,
    predictedLoss: number
) {
    console.log("\n================================================");
    console.log("FINAL ROUTE RECOMMENDATION (Server)");
    console.log("================================================");
    if (avgWaitTime > 0) {
        const minutes = Math.floor(avgWaitTime / 60);
        const seconds = Math.floor(avgWaitTime % 60);
        console.log(`Average Optimal Window: ${minutes}m ${seconds}s from now`);
        console.log(`Predicted Lower Slippage: ${predictedLoss.toFixed(8)} SOL`);
        console.log(`Action: WAIT for average low-point alignment`);
    } else {
        console.log(`Average Optimal Window: NOW`);
        console.log(`Predicted Lower Slippage: ${currentLoss.toFixed(8)} SOL`);
        console.log(`Action: EXECUTE (Current conditions are favorable)`);
    }
    console.log("================================================\n");
}

function getFallbackResult(
    symbol: string,
    reason: string,
    source: UnsupportedPredictionResult["source"] = "python_exec"
): UnsupportedPredictionResult {
    return {
        supported: false,
        requestedSymbol: symbol,
        resolvedSymbol: symbol,
        reason,
        source,
        dataset: null,
    };
}

function buildPredictionServiceUrl(baseUrl: string, resource: string = 'predict-step'): string {
    return `${baseUrl.replace(/\/+$/, '')}/${resource.replace(/^\/+/, '')}`;
}

async function fetchPredictionApiJson<T>(
    url: string,
    init: RequestInit,
    timeoutMs: number,
    failurePayload: () => T,
    errorPrefix: string,
    retries: number = 0
): Promise<T> {
    let attempt = 0;

    while (true) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), timeoutMs);

        try {
            const response = await fetch(url, {
                ...init,
                signal: controller.signal,
            });

            if (!response.ok) {
                const errorText = (await response.text()).trim();
                return failurePayloadFromMessage(
                    failurePayload,
                    `${errorPrefix} (${response.status}): ${errorText || 'Empty response body'}`
                );
            }

            return (await response.json()) as T;
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            const aborted =
                message === 'This operation was aborted' ||
                message === 'The operation was aborted.' ||
                (error instanceof DOMException && error.name === 'AbortError');

            if (aborted && attempt < retries) {
                attempt += 1;
                console.warn(`[Predictor] Retrying ${url} after timeout (${attempt}/${retries})...`);
                continue;
            }

            return failurePayloadFromMessage(
                failurePayload,
                `${errorPrefix}: ${message}`
            );
        } finally {
            clearTimeout(timeout);
        }
    }
}

function failurePayloadFromMessage<T>(builder: () => T, message: string): T {
    const payload = builder();
    if (payload && typeof payload === 'object') {
        if ('reason' in payload) {
            (payload as { reason?: string }).reason = message;
        }
        if ('error' in payload) {
            (payload as { error?: string }).error = message;
        }
    }
    return payload;
}

async function runRemotePrediction(
    baseUrl: string,
    symbol: string,
    inputMint: string,
    outputMint: string,
    data: ProphetDataPoint[]
): Promise<PredictionResult> {
    const headers: Record<string, string> = {
        'Content-Type': 'application/json',
    };

    const apiToken = process.env.PREDICTION_API_TOKEN?.trim();
    if (apiToken) {
        headers.Authorization = `Bearer ${apiToken}`;
    }

    return fetchPredictionApiJson<PredictionResult>(
        buildPredictionServiceUrl(baseUrl, 'predict-step'),
        {
            method: 'POST',
            headers,
            body: JSON.stringify({
                symbol,
                inputMint,
                outputMint,
                data,
            }),
            cache: 'no-store',
        },
        REMOTE_PREDICTION_TIMEOUT_MS,
        () => getFallbackResult(symbol, 'Prediction API request failed.', 'prediction_api'),
        'Prediction API request failed',
        1
    );
}

async function runRemoteSymbolPrediction(
    baseUrl: string,
    symbol: string,
    marketMode: 'crypto' | 'sp500' = 'sp500'
): Promise<PredictionResult> {
    const headers: Record<string, string> = {
        'Content-Type': 'application/json',
    };

    const apiToken = process.env.PREDICTION_API_TOKEN?.trim();
    if (apiToken) {
        headers.Authorization = `Bearer ${apiToken}`;
    }

    return fetchPredictionApiJson<PredictionResult>(
        buildPredictionServiceUrl(baseUrl, 'predict-step'),
        {
            method: 'POST',
            headers,
            body: JSON.stringify({
                symbol,
                marketMode,
                trackHumanBias: marketMode === 'sp500',
                humanBiasSource: 'predict_symbol',
                data: [],
            }),
            cache: 'no-store',
        },
        REMOTE_SYMBOL_PREDICTION_TIMEOUT_MS,
        () => getFallbackResult(symbol, 'Prediction API request failed.', 'prediction_api'),
        'Prediction API request failed',
        1
    );
}

async function runRemoteSp500InformationMap(
    baseUrl: string,
    forceRefresh: boolean
): Promise<Sp500InformationMapResult> {
    const headers: Record<string, string> = {
        'Content-Type': 'application/json',
    };

    const apiToken = process.env.PREDICTION_API_TOKEN?.trim();
    if (apiToken) {
        headers.Authorization = `Bearer ${apiToken}`;
    }

    const result = await fetchPredictionApiJson<Sp500InformationMapResult>(
        buildPredictionServiceUrl(baseUrl, 'sp500-map'),
        {
            method: 'POST',
            headers,
            body: JSON.stringify({
                forceRefresh,
            }),
            cache: 'no-store',
        },
        REMOTE_SP500_MAP_TIMEOUT_MS,
        () => ({
            ok: false,
            points: [],
            topPicks: [],
            error: 'Prediction API request failed.',
        }),
        'Prediction API request failed',
        1
    );
    return normalizeSp500InformationMapResult(result);
}

async function runRemoteSp500Portfolio(
    baseUrl: string,
    forceRefresh: boolean
): Promise<Sp500PortfolioResult> {
    const headers: Record<string, string> = {
        'Content-Type': 'application/json',
    };

    const apiToken = process.env.PREDICTION_API_TOKEN?.trim();
    if (apiToken) {
        headers.Authorization = `Bearer ${apiToken}`;
    }

    return fetchPredictionApiJson<Sp500PortfolioResult>(
        buildPredictionServiceUrl(baseUrl, 'sp500-portfolio'),
        {
            method: 'POST',
            headers,
            body: JSON.stringify({
                forceRefresh,
            }),
            cache: 'no-store',
        },
        REMOTE_SP500_PORTFOLIO_TIMEOUT_MS,
        () => ({
            ok: false,
            holdings: [],
            error: 'Prediction API request failed.',
        }),
        'Prediction API request failed',
        1
    );
}

/**
 * 특정 스왑 단계에 대해 Prophet 예측 모델을 실행합니다.
 */
export async function predictStep(
    inputMint: string,
    outputMint: string,
    symbol: string
): Promise<PredictionResult> {
    const projectRoot = resolveProjectRoot();
    const predictionApiUrl = process.env.PREDICTION_API_URL?.trim();

    let filePath = '';

    try {
        const data = await getStepDataForProphet(inputMint, outputMint, 110, symbol);

        if (!data || data.length < 100) {
            const reason = `Insufficient Birdeye data for ${symbol} (${data?.length || 0} rows, need at least 100).`;
            console.warn(`[Predictor] ${reason}`);

            if (predictionApiUrl) {
                console.log(`[Predictor] Falling through to remote prediction API for ${symbol} despite missing Birdeye data.`);
                return await runRemotePrediction(predictionApiUrl, symbol, inputMint, outputMint, data ?? []);
            }

            const scriptPath = path.join(projectRoot, 'services', 'trader', 'predict_signal.py');
            const pythonBin = resolvePythonBinary(projectRoot);

            if (fs.existsSync(scriptPath) && pythonBin) {
                console.log(`[Predictor] Falling through to local Python fallback for ${symbol}.`);
                const { stdout } = await execFileAsync(
                    pythonBin,
                    [scriptPath, '--symbol', symbol],
                    {
                        cwd: projectRoot,
                        maxBuffer: 1024 * 1024 * 16,
                        env: {
                            ...process.env,
                            PATH: buildAugmentedPath(projectRoot),
                            PYTHONPATH: projectRoot,
                            PYTHONWARNINGS: 'ignore',
                        }
                    }
                );

                return JSON.parse(extractJsonLine(stdout)) as PredictionResult;
            }

            return getFallbackResult(symbol, reason, 'birdeye');
        }

        if (predictionApiUrl) {
            console.log(`[Predictor] Using remote prediction API for ${symbol} via ${predictionApiUrl}`);
            return await runRemotePrediction(predictionApiUrl, symbol, inputMint, outputMint, data);
        }

        const scriptPath = path.join(projectRoot, 'services', 'trader', 'predict_signal.py');
        if (!fs.existsSync(scriptPath)) {
            const reason = `Prediction script not found at ${scriptPath}.`;
            console.error(`[Predictor] ${reason}`);
            return getFallbackResult(symbol, reason);
        }

        const pythonBin = resolvePythonBinary(projectRoot);
        if (!pythonBin) {
            const reason = 'No Python runtime found. Install services/trader/.venv or set PREDICTION_PYTHON_BIN.';
            console.error(`[Predictor] ${reason}`);
            return getFallbackResult(symbol, reason);
        }

        const tempDir = path.join(os.tmpdir(), 'trader-temp');
        if (!fs.existsSync(tempDir)) fs.mkdirSync(tempDir, { recursive: true });

        const fileName = `step_${inputMint.slice(0, 4)}_${outputMint.slice(0, 4)}_${Date.now()}.csv`;
        filePath = path.join(tempDir, fileName);

        const csvContent = [
            'ds,open,high,low,close,volume',
            ...data.map((d) => `${d.ds},${d.open},${d.high},${d.low},${d.close},${d.volume}`)
        ].join('\n');

        fs.writeFileSync(filePath, csvContent);

        console.log(`[Predictor] Running inference for ${symbol} with ${pythonBin}...`);

        const { stdout } = await execFileAsync(pythonBin, [
            scriptPath,
            '--symbol',
            symbol,
            '--csv',
            filePath,
        ], {
            cwd: projectRoot,
            maxBuffer: 1024 * 1024 * 16,
            env: {
                ...process.env,
                PATH: buildAugmentedPath(projectRoot),
                PYTHONPATH: projectRoot,
                PYTHONWARNINGS: 'ignore',
            }
        });

        const result = JSON.parse(extractJsonLine(stdout)) as PredictionResult;

        if (!result.supported) {
            console.warn(`[Predictor] ${symbol}: ${result.reason}`);
        }

        return result;
    } catch (error) {
        return normalizeExecutionError(symbol, error);
    } finally {
        if (filePath && fs.existsSync(filePath)) {
            try {
                fs.unlinkSync(filePath);
            } catch (error) {
                console.error("Failed to delete temp file:", error);
            }
        }
    }
}

export async function predictSymbol(symbol: string): Promise<PredictionResult> {
    const projectRoot = resolveProjectRoot();
    const predictionApiUrl = process.env.PREDICTION_API_URL?.trim();

    try {
        if (predictionApiUrl) {
            console.log(`[Predictor] Using remote symbol prediction API for ${symbol} via ${predictionApiUrl}`);
            return await runRemoteSymbolPrediction(predictionApiUrl, symbol, 'sp500');
        }

        const scriptPath = path.join(projectRoot, 'services', 'trader', 'predict_signal.py');
        if (!fs.existsSync(scriptPath)) {
            const reason = `Prediction script not found at ${scriptPath}.`;
            console.error(`[Predictor] ${reason}`);
            return getFallbackResult(symbol, reason);
        }

        const pythonBin = resolvePythonBinary(projectRoot);
        if (!pythonBin) {
            const reason = 'No Python runtime found. Install services/trader/.venv or set PREDICTION_PYTHON_BIN.';
            console.error(`[Predictor] ${reason}`);
            return getFallbackResult(symbol, reason);
        }

        console.log(`[Predictor] Running direct symbol inference for ${symbol} with ${pythonBin}...`);

        const { stdout } = await execFileAsync(
            pythonBin,
            [
                scriptPath,
                '--symbol',
                symbol,
                '--market-mode',
                'sp500',
                '--track-human-bias',
                '--human-bias-source',
                'predict_symbol',
            ],
            {
                cwd: projectRoot,
                maxBuffer: 1024 * 1024 * 16,
                env: {
                    ...process.env,
                    PATH: buildAugmentedPath(projectRoot),
                    PYTHONPATH: projectRoot,
                    PYTHONWARNINGS: 'ignore',
                }
            }
        );

        const result = JSON.parse(extractJsonLine(stdout)) as PredictionResult;

        if (!result.supported) {
            console.warn(`[Predictor] ${symbol}: ${result.reason}`);
        }

        return result;
    } catch (error) {
        return normalizeExecutionError(symbol, error);
    }
}

export async function buildSp500InformationMap(
    forceRefresh: boolean = false
): Promise<Sp500InformationMapResult> {
    const projectRoot = resolveProjectRoot();
    const predictionApiUrl = process.env.PREDICTION_API_URL?.trim();

    try {
        if (predictionApiUrl) {
            console.log(`[Predictor] Using remote S&P500 map API via ${predictionApiUrl}`);
            return await runRemoteSp500InformationMap(predictionApiUrl, forceRefresh);
        }

        const scriptPath = path.join(projectRoot, 'services', 'trader', 'sp500_information_map.py');
        if (!fs.existsSync(scriptPath)) {
            return {
                ok: false,
                points: [],
                topPicks: [],
                error: `S&P500 information map script not found at ${scriptPath}.`,
            };
        }

        const pythonBin = resolvePythonBinary(projectRoot);
        if (!pythonBin) {
            return {
                ok: false,
                points: [],
                topPicks: [],
                error: 'No Python runtime found. Install services/trader/.venv or set PREDICTION_PYTHON_BIN.',
            };
        }

        const args = [scriptPath];
        if (forceRefresh) {
            args.push('--force-refresh');
        } else {
            args.push('--cache-max-age-hours', '999999');
        }

        const { stdout } = await execFileAsync(
            pythonBin,
            args,
            {
                cwd: projectRoot,
                maxBuffer: 1024 * 1024 * 16,
                env: {
                    ...process.env,
                    PATH: buildAugmentedPath(projectRoot),
                    PYTHONPATH: projectRoot,
                    PYTHONWARNINGS: 'ignore',
                }
            }
        );

        return normalizeSp500InformationMapResult(
            JSON.parse(extractJsonLine(stdout)) as Sp500InformationMapResult
        );
    } catch (error) {
        const typedError = error as { message?: string; stderr?: string; stdout?: string };
        const reason = typedError?.stderr?.trim() || typedError?.stdout?.trim() || typedError?.message || 'Failed to build the S&P500 information map.';
        return {
            ok: false,
            points: [],
            topPicks: [],
            error: reason,
        };
    }
}

export async function buildSp500Portfolio(
    forceRefresh: boolean = false
): Promise<Sp500PortfolioResult> {
    const projectRoot = resolveProjectRoot();
    const predictionApiUrl = process.env.PREDICTION_API_URL?.trim();

    try {
        if (predictionApiUrl) {
            console.log(`[Predictor] Using remote S&P500 portfolio API via ${predictionApiUrl}`);
            const remoteResult = await runRemoteSp500Portfolio(predictionApiUrl, forceRefresh);
            return await enrichSp500PortfolioResult(remoteResult);
        }

        const scriptPath = path.join(projectRoot, 'services', 'trader', 'sp500_portfolio.py');
        if (!fs.existsSync(scriptPath)) {
            return {
                ok: false,
                holdings: [],
                error: `S&P500 portfolio script not found at ${scriptPath}.`,
            };
        }

        const pythonBin = resolvePythonBinary(projectRoot);
        if (!pythonBin) {
            return {
                ok: false,
                holdings: [],
                error: 'No Python runtime found. Install services/trader/.venv or set PREDICTION_PYTHON_BIN.',
            };
        }

        const args = [scriptPath];
        if (forceRefresh) {
            args.push('--force-refresh');
        } else {
            args.push('--cache-max-age-hours', '999999');
        }

        const { stdout } = await execFileAsync(
            pythonBin,
            args,
            {
                cwd: projectRoot,
                maxBuffer: 1024 * 1024 * 16,
                env: {
                    ...process.env,
                    PATH: buildAugmentedPath(projectRoot),
                    PYTHONPATH: projectRoot,
                    PYTHONWARNINGS: 'ignore',
                }
            }
        );

        const parsed = JSON.parse(extractJsonLine(stdout)) as Sp500PortfolioResult;
        return await enrichSp500PortfolioResult(parsed);
    } catch (error) {
        const typedError = error as { message?: string; stderr?: string; stdout?: string };
        const reason = typedError?.stderr?.trim() || typedError?.stdout?.trim() || typedError?.message || 'Failed to build the S&P500 portfolio.';
        return {
            ok: false,
            holdings: [],
            error: reason,
        };
    }
}
