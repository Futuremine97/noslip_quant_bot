import { runLLMPipeline } from "../../../services/llm/pipeline";
import { guardApiRequest, isSp500OnlyRequest, secureJson } from "@/app/api/_lib/security";

const toFiniteNumber = (value: unknown) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const normalizeAgentName = (value: unknown) =>
  String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, "_");

const firstDefined = (...values: unknown[]) => {
  for (const value of values) {
    if (value !== undefined && value !== null) {
      return value;
    }
  }
  return null;
};

const deriveRegretFallback = (
  targetData: any,
  uncertaintyRatio: number | null
) => {
  const baseAction = String(targetData?.finalAction || "HOLD").toUpperCase();
  const avgUncertainty =
    toFiniteNumber(targetData?.avgUncertaintyRatio) ?? uncertaintyRatio ?? 0;
  const cadenceProfile = String(targetData?.cadenceProfile || "daily").toLowerCase();
  const unitSeconds = cadenceProfile === "intraday" ? 3600 : 86400;
  const quickRegretSeconds = 6 * unitSeconds;
  const heavyRegretSeconds = 24 * unitSeconds;

  const timeToBelowCurrent = toFiniteNumber(
    firstDefined(targetData?.timeToBelowCurrentSeconds, targetData?.timeToBelowCurrent)
  );
  const drawdownLingerSeconds = toFiniteNumber(
    firstDefined(
      targetData?.drawdownLingerConsensusSeconds,
      targetData?.timesfmDrawdownLingerSeconds,
      targetData?.drawdownLingerSeconds
    )
  );
  const drawdownRecoveryInHorizon = firstDefined(
    targetData?.drawdownRecoveryConsensusInHorizon,
    targetData?.timesfmDrawdownRecoveryInHorizon,
    targetData?.drawdownRecoveryInHorizon
  );
  const maxDrawdownPct = toFiniteNumber(
    firstDefined(
      targetData?.maxDrawdownConsensusPct,
      targetData?.timesfmMaxDrawdownPct,
      targetData?.maxDrawdownPct
    )
  );
  const spikeSustainSeconds = toFiniteNumber(
    firstDefined(
      targetData?.spikeSustainConsensusSeconds,
      targetData?.timesfmSpikeSustainSeconds,
      targetData?.spikeSustainSeconds
    )
  );
  const spikeFadeInHorizon = firstDefined(
    targetData?.spikeFadeConsensusInHorizon,
    targetData?.timesfmSpikeFadeInHorizon,
    targetData?.spikeFadeInHorizon
  );
  const maxSpikePct = toFiniteNumber(
    firstDefined(
      targetData?.maxSpikeConsensusPct,
      targetData?.timesfmMaxSpikePct,
      targetData?.maxSpikePct
    )
  );
  const timeToOptimalBuySeconds = toFiniteNumber(targetData?.timeToOptimalBuySeconds);
  const timeToOptimalSellSeconds = toFiniteNumber(targetData?.timeToOptimalSellSeconds);
  const currentPrice = toFiniteNumber(
    firstDefined(targetData?.lastClosePrice, targetData?.currentPrice)
  );
  const targetPrice = toFiniteNumber(
    firstDefined(targetData?.targetPrice, targetData?.optimalSellPrice)
  );

  let impliedUpside: number | null = null;
  if (
    currentPrice != null &&
    targetPrice != null &&
    Number.isFinite(currentPrice) &&
    currentPrice !== 0
  ) {
    impliedUpside = targetPrice / currentPrice - 1;
  }

  let buyRegretScore = 0;
  let sellRegretScore = 0;

  if (timeToBelowCurrent != null) {
    if (timeToBelowCurrent <= quickRegretSeconds) {
      buyRegretScore += 0.32;
    } else if (timeToBelowCurrent <= heavyRegretSeconds) {
      buyRegretScore += 0.18;
    }
  }

  if (drawdownLingerSeconds != null) {
    if (drawdownLingerSeconds >= heavyRegretSeconds) {
      buyRegretScore += 0.26;
    } else if (drawdownLingerSeconds >= quickRegretSeconds) {
      buyRegretScore += 0.14;
    }
  }

  if (drawdownRecoveryInHorizon === false) {
    buyRegretScore += 0.18;
  }

  if (maxDrawdownPct != null) {
    buyRegretScore += Math.min(0.18, Math.abs(maxDrawdownPct) * 1.6);
  }

  buyRegretScore += Math.min(0.24, avgUncertainty * 4.5);

  if (impliedUpside != null && impliedUpside <= 0) {
    buyRegretScore += 0.14;
  }

  if (spikeSustainSeconds != null) {
    if (spikeSustainSeconds >= heavyRegretSeconds) {
      sellRegretScore += 0.28;
    } else if (spikeSustainSeconds >= quickRegretSeconds) {
      sellRegretScore += 0.16;
    }
  }

  if (spikeFadeInHorizon === false) {
    sellRegretScore += 0.18;
  }

  if (maxSpikePct != null) {
    sellRegretScore += Math.min(0.2, Math.max(0, maxSpikePct) * 1.8);
  }

  if (impliedUpside != null && impliedUpside > 0) {
    sellRegretScore += Math.min(0.18, impliedUpside * 1.5);
  }

  if (timeToOptimalSellSeconds != null && timeToOptimalSellSeconds <= quickRegretSeconds) {
    sellRegretScore += 0.1;
  }

  if (timeToOptimalBuySeconds != null && timeToOptimalBuySeconds <= quickRegretSeconds) {
    buyRegretScore += 0.08;
  }

  buyRegretScore = Math.max(0, Math.min(1, buyRegretScore));
  sellRegretScore = Math.max(0, Math.min(1, sellRegretScore));
  const regretRiskScore = Math.max(buyRegretScore, sellRegretScore);
  const regretBias =
    buyRegretScore > sellRegretScore + 0.08
      ? "buy_regret"
      : sellRegretScore > buyRegretScore + 0.08
        ? "sell_regret"
        : "balanced";

  let regretAction = baseAction;
  let regretConfidence = 0.58;

  if (baseAction === "BUY") {
    if (buyRegretScore >= 0.62) {
      regretAction = "HOLD";
      regretConfidence = Math.min(0.92, 0.58 + buyRegretScore * 0.42);
    } else {
      regretAction = "BUY";
      regretConfidence = 0.55 + Math.max(0, 0.35 - buyRegretScore * 0.25);
    }
  } else if (baseAction === "SELL") {
    if (sellRegretScore >= 0.58) {
      regretAction = "HOLD";
      regretConfidence = Math.min(0.9, 0.56 + sellRegretScore * 0.4);
    } else {
      regretAction = "SELL";
      regretConfidence = 0.56 + Math.max(0, 0.34 - sellRegretScore * 0.22);
    }
  } else {
    regretAction = "HOLD";
    regretConfidence = 0.55 + Math.abs(buyRegretScore - sellRegretScore) * 0.2;
  }

  return {
    regretAction,
    regretConfidence: Math.max(0, Math.min(1, regretConfidence)),
    regretRiskScore,
    regretBias,
    buyRegretScore,
    sellRegretScore,
  };
};

export async function POST(req: Request) {
  const guard = guardApiRequest(req, {
    routeKey: "analyze",
    maxBodyBytes: 2 * 1024 * 1024,
    allowedContentTypes: ["application/json"],
    rateLimit: {
      key: "analyze",
      limit: 18,
      windowMs: 60_000,
    },
  });
  if (guard) {
    return guard;
  }

  try {
    const {
      token,
      predictions,
      currentLoss,
      inputSymbol,
      outputSymbol,
      bestRoutePath,
      routeLegs,
      routePriceImpactPct,
      marketMode,
      portfolioGeometry,
      portfolioNaturalGradient,
      portfolioManifold,
      portfolioChampionAgent,
      portfolioSummary,
      portfolioAllocation,
      portfolioMethodology,
      portfolioTopHoldings,
      sp500Portfolio,
      informationMapNeuralModel,
      informationMapFeatureBenchmark,
      selectedPortfolioHolding,
      selectedMapPoint,
    } = await req.json();

    const normalizedMarketMode = String(marketMode ?? "sp500").trim().toLowerCase();
    if (isSp500OnlyRequest(req) && normalizedMarketMode !== "sp500") {
      return secureJson(
        { error: "Crypto LLM analysis is disabled in local S&P500-only mode." },
        { status: 404 }
      );
    }

    const targetData = (predictions && predictions.length > 0) 
      ? predictions[0] 
      : { currentPrice: 0, targetPrice: 0, timeToBelowCurrent: 0, directionStrength: 0 };

    const ruleSummaries = Object.values((targetData?.perRuleSummary as Record<string, any>) || {});
    const uncertaintySamples = ruleSummaries
      .flatMap((entry) => entry?.direction?.agents || [])
      .map((agent) => Number(agent?.uncertaintyRatio))
      .filter((value) => Number.isFinite(value));
    const uncertaintyRatio =
      uncertaintySamples.length > 0
        ? uncertaintySamples.reduce((sum, value) => sum + value, 0) / uncertaintySamples.length
        : null;
    const referencePrice =
      targetData.lastClosePrice ?? targetData.currentPrice ?? 0;
    const maxUpsidePct =
      targetData.optimalSellPrice != null && referencePrice
        ? targetData.optimalSellPrice / referencePrice - 1
        : null;
    const expectedReturnPct =
      targetData.targetPrice != null && referencePrice
        ? targetData.targetPrice / referencePrice - 1
        : null;
    const wrapperAgentOutputs = Array.isArray(targetData?.wrapper?.agentOutputs)
      ? targetData.wrapper.agentOutputs
      : [];
    const wrapperBagging = targetData?.wrapper?.bagging || {};
    const wrapperBaggingActionProbabilities =
      wrapperBagging?.actionProbabilities || {};
    const regretAgentOutput =
      wrapperAgentOutputs.find(
        (agent: any) => normalizeAgentName(agent?.name) === "regret_agent"
      ) || null;
    const emAgentOutput =
      wrapperAgentOutputs.find(
        (agent: any) => normalizeAgentName(agent?.name) === "em_regime_agent"
      ) || null;
    const minimaxAgentOutput =
      wrapperAgentOutputs.find(
        (agent: any) => normalizeAgentName(agent?.name) === "minimax_prior_agent"
      ) || null;
    const emLocalMetrics = emAgentOutput?.localMetrics || {};
    const minimaxLocalMetrics = minimaxAgentOutput?.localMetrics || {};
    const regretLocalMetrics = regretAgentOutput?.localMetrics || {};
    const fallbackRegret = deriveRegretFallback(targetData, uncertaintyRatio);

    const decisionInput = {
      market_mode: normalizedMarketMode,
      current_price: targetData.currentPrice ?? 0,
      live_price: targetData.livePrice ?? null,
      last_close_price: targetData.lastClosePrice ?? targetData.currentPrice ?? 0,
      target_price: targetData.targetPrice ?? 0,
      time_to_low: targetData.timeToBelowCurrent ?? 0,
      target_timestamp: targetData.targetTimestamp ?? null,
      direction_strength: targetData.directionStrength ?? 0,
      uncertainty_ratio: uncertaintyRatio,
      expected_return_pct: expectedReturnPct,
      max_upside_pct: maxUpsidePct,
      first_moment_pct_per_hour: targetData.firstMomentPctPerHour ?? null,
      second_moment_pct_per_hour2: targetData.secondMomentPctPerHour2 ?? null,
      seasonality_source_rule: targetData.seasonalitySummary?.sourceRule ?? null,
      seasonality_headline: targetData.seasonalitySummary?.headline ?? null,
      seasonality_strongest_component: targetData.seasonalitySummary?.strongestComponent ?? null,
      seasonality_weekly_summary: targetData.seasonalitySummary?.weekly?.summary ?? null,
      seasonality_yearly_summary: targetData.seasonalitySummary?.yearly?.summary ?? null,
      seasonality_monthly_summary: targetData.seasonalitySummary?.monthly?.summary ?? null,
      seasonality_quarterly_summary: targetData.seasonalitySummary?.quarterly?.summary ?? null,
      avg_uncertainty_ratio:
        targetData.avgUncertaintyRatio ?? uncertaintyRatio ?? null,
      geodesic_available: targetData.geodesicAvailable ?? null,
      geodesic_label: targetData.geodesicLabel ?? null,
      geodesic_action_bias: targetData.geodesicActionBias ?? null,
      geodesic_history_count: targetData.geodesicHistoryCount ?? null,
      geodesic_path_length: targetData.geodesicPathLength ?? null,
      geodesic_curvature: targetData.geodesicCurvature ?? null,
      geodesic_alignment_score: targetData.geodesicAlignmentScore ?? null,
      geodesic_deviation_score: targetData.geodesicDeviationScore ?? null,
      geodesic_continuation_score:
        targetData.geodesicContinuationScore ?? null,
      geodesic_confidence: targetData.geodesicConfidence ?? null,
      geodesic_projected_first_coordinate_x:
        targetData.geodesicProjectedFirstCoordinateX ?? null,
      geodesic_projected_first_coordinate_y:
        targetData.geodesicProjectedFirstCoordinateY ?? null,
      geodesic_projected_second_coordinate_x:
        targetData.geodesicProjectedSecondCoordinateX ?? null,
      geodesic_projected_second_coordinate_y:
        targetData.geodesicProjectedSecondCoordinateY ?? null,
      geodesic_projected_first_coordinate_drift:
        targetData.geodesicProjectedFirstCoordinateDrift ?? null,
      geodesic_projected_second_coordinate_drift:
        targetData.geodesicProjectedSecondCoordinateDrift ?? null,
      time_to_optimal_buy_seconds: targetData.timeToOptimalBuySeconds ?? null,
      time_to_optimal_sell_seconds: targetData.timeToOptimalSellSeconds ?? null,
      rise_window_seconds: targetData.riseWindowSeconds ?? null,
      drop_window_seconds: targetData.dropWindowSeconds ?? null,
      spike_start_timestamp: targetData.spikeStartTimestamp ?? null,
      spike_peak_timestamp: targetData.spikePeakTimestamp ?? null,
      spike_peak_price: targetData.spikePeakPrice ?? null,
      spike_sustain_seconds: targetData.spikeSustainSeconds ?? null,
      spike_fade_timestamp: targetData.spikeFadeTimestamp ?? null,
      spike_fade_in_horizon: targetData.spikeFadeInHorizon ?? null,
      peak_to_fade_seconds: targetData.peakToFadeSeconds ?? null,
      max_spike_pct: targetData.maxSpikePct ?? null,
      drawdown_start_timestamp: targetData.drawdownStartTimestamp ?? null,
      drawdown_recovery_timestamp: targetData.drawdownRecoveryTimestamp ?? null,
      drawdown_trough_timestamp: targetData.drawdownTroughTimestamp ?? null,
      drawdown_trough_price: targetData.drawdownTroughPrice ?? null,
      drawdown_linger_seconds: targetData.drawdownLingerSeconds ?? null,
      drawdown_recovery_in_horizon: targetData.drawdownRecoveryInHorizon ?? null,
      trough_to_recovery_seconds: targetData.troughToRecoverySeconds ?? null,
      max_drawdown_pct: targetData.maxDrawdownPct ?? null,
      timesfm_drawdown_start_timestamp: targetData.timesfmDrawdownStartTimestamp ?? null,
      timesfm_drawdown_recovery_timestamp: targetData.timesfmDrawdownRecoveryTimestamp ?? null,
      timesfm_drawdown_trough_timestamp: targetData.timesfmDrawdownTroughTimestamp ?? null,
      timesfm_drawdown_trough_price: targetData.timesfmDrawdownTroughPrice ?? null,
      timesfm_drawdown_linger_seconds: targetData.timesfmDrawdownLingerSeconds ?? null,
      timesfm_drawdown_recovery_in_horizon: targetData.timesfmDrawdownRecoveryInHorizon ?? null,
      timesfm_trough_to_recovery_seconds: targetData.timesfmTroughToRecoverySeconds ?? null,
      timesfm_max_drawdown_pct: targetData.timesfmMaxDrawdownPct ?? null,
      timesfm_quantile_band_pct: targetData.timesfmQuantileBandPct ?? null,
      timesfm_spike_start_timestamp: targetData.timesfmSpikeStartTimestamp ?? null,
      timesfm_spike_peak_timestamp: targetData.timesfmSpikePeakTimestamp ?? null,
      timesfm_spike_peak_price: targetData.timesfmSpikePeakPrice ?? null,
      timesfm_spike_sustain_seconds: targetData.timesfmSpikeSustainSeconds ?? null,
      timesfm_spike_fade_timestamp: targetData.timesfmSpikeFadeTimestamp ?? null,
      timesfm_spike_fade_in_horizon: targetData.timesfmSpikeFadeInHorizon ?? null,
      timesfm_peak_to_fade_seconds: targetData.timesfmPeakToFadeSeconds ?? null,
      timesfm_max_spike_pct: targetData.timesfmMaxSpikePct ?? null,
      timesfm_status: targetData.timesfmStatus ?? null,
      timesfm_error: targetData.timesfmError ?? null,
      timesfm_used: targetData.timesfmUsed ?? null,
      timesfm_model_id: targetData.timesfmModelId ?? null,
      moe_profile: targetData.moeRuntime?.profile ?? null,
      moe_enabled: targetData.moeRuntime?.enabled ?? null,
      moe_active_experts: targetData.moeRuntime?.activeExperts?.join(", ") ?? null,
      moe_skipped_experts: targetData.moeRuntime?.skippedExperts?.join(", ") ?? null,
      moe_timesfm_reason:
        targetData.moeRuntime?.experts?.timesfm?.reason ??
        targetData.timesfmMoeGate?.reason ??
        null,
      moe_timesfm_score:
        toFiniteNumber(
          targetData.moeRuntime?.experts?.timesfm?.score ?? targetData.timesfmMoeGate?.score
        ) ?? null,
      moe_correlation_reason:
        targetData.moeRuntime?.experts?.correlation?.reason ??
        targetData.correlationForecast?.moeGate?.reason ??
        null,
      moe_correlation_score:
        toFiniteNumber(
          targetData.moeRuntime?.experts?.correlation?.score ??
            targetData.correlationForecast?.moeGate?.score
        ) ?? null,
      spike_sustain_consensus_seconds: targetData.spikeSustainConsensusSeconds ?? null,
      peak_to_fade_consensus_seconds: targetData.peakToFadeConsensusSeconds ?? null,
      spike_fade_consensus_in_horizon: targetData.spikeFadeConsensusInHorizon ?? null,
      max_spike_consensus_pct: targetData.maxSpikeConsensusPct ?? null,
      spike_consensus_source: targetData.spikeConsensusSource ?? null,
      prophet_spike_weight: targetData.prophetSpikeWeight ?? null,
      timesfm_spike_weight: targetData.timesfmSpikeWeight ?? null,
      drawdown_linger_consensus_seconds: targetData.drawdownLingerConsensusSeconds ?? null,
      trough_to_recovery_consensus_seconds: targetData.troughToRecoveryConsensusSeconds ?? null,
      drawdown_recovery_consensus_in_horizon: targetData.drawdownRecoveryConsensusInHorizon ?? null,
      max_drawdown_consensus_pct: targetData.maxDrawdownConsensusPct ?? null,
      drawdown_consensus_source: targetData.drawdownConsensusSource ?? null,
      portfolio_geometry_space: portfolioGeometry?.space ?? null,
      portfolio_geometry_method: portfolioGeometry?.method ?? null,
      portfolio_geometry_risk_profile: portfolioGeometry?.riskProfile ?? null,
      portfolio_geometry_target_x: portfolioGeometry?.targetPoint?.x ?? null,
      portfolio_geometry_target_y: portfolioGeometry?.targetPoint?.y ?? null,
      portfolio_geometry_portfolio_x: portfolioGeometry?.portfolioPoint?.x ?? null,
      portfolio_geometry_portfolio_y: portfolioGeometry?.portfolioPoint?.y ?? null,
      portfolio_geometry_alignment_score: portfolioGeometry?.alignmentScore ?? null,
      portfolio_geometry_kl_divergence: portfolioGeometry?.portfolioKlDivergence ?? null,
      portfolio_geometry_distance: portfolioGeometry?.portfolioDistance ?? null,
      portfolio_natural_gradient_method: portfolioNaturalGradient?.method ?? null,
      portfolio_natural_gradient_metric: portfolioNaturalGradient?.metric ?? null,
      portfolio_natural_gradient_iterations: portfolioNaturalGradient?.iterations ?? null,
      portfolio_natural_gradient_step_size: portfolioNaturalGradient?.stepSize ?? null,
      portfolio_natural_gradient_temperature: portfolioNaturalGradient?.temperature ?? null,
      portfolio_natural_gradient_upper_bound_score:
        portfolioNaturalGradient?.upperBoundScore ?? null,
      portfolio_natural_gradient_live_distance_to_target:
        portfolioNaturalGradient?.liveDistanceToTarget ?? null,
      portfolio_natural_gradient_bound_distance_to_target:
        portfolioNaturalGradient?.boundDistanceToTarget ?? null,
      portfolio_natural_gradient_live_distance_to_bound:
        portfolioNaturalGradient?.liveDistanceToBound ?? null,
      portfolio_natural_gradient_live_entropy:
        portfolioNaturalGradient?.liveEntropy ?? null,
      portfolio_natural_gradient_bound_entropy:
        portfolioNaturalGradient?.boundEntropy ?? null,
      portfolio_natural_gradient_fisher_trace:
        portfolioNaturalGradient?.fisherTrace ?? null,
      portfolio_natural_gradient_fisher_curvature:
        portfolioNaturalGradient?.fisherCurvature ?? null,
      portfolio_natural_gradient_risk_envelope_strength:
        portfolioNaturalGradient?.riskEnvelopeStrength ?? null,
      portfolio_manifold_method: portfolioManifold?.method ?? null,
      portfolio_manifold_history_count: portfolioManifold?.historyCount ?? null,
      portfolio_manifold_rank: portfolioManifold?.rank ?? null,
      portfolio_manifold_state_dimension: portfolioManifold?.stateDimension ?? null,
      portfolio_manifold_continuity_score: portfolioManifold?.continuityScore ?? null,
      portfolio_manifold_target_distance: portfolioManifold?.targetDistance ?? null,
      portfolio_manifold_bridge_mode: portfolioManifold?.neuralBridge?.mode ?? null,
      portfolio_manifold_bridge_loss: portfolioManifold?.neuralBridge?.loss ?? null,
      champion_portfolio_name: portfolioChampionAgent?.name ?? null,
      champion_portfolio_method: portfolioChampionAgent?.method ?? null,
      champion_portfolio_profile: portfolioChampionAgent?.selectedProfile ?? null,
      champion_portfolio_label: portfolioChampionAgent?.selectedLabel ?? null,
      champion_portfolio_score: portfolioChampionAgent?.score ?? null,
      champion_portfolio_continuity_score: portfolioChampionAgent?.continuityScore ?? null,
      champion_portfolio_target_distance: portfolioChampionAgent?.targetDistance ?? null,
      champion_portfolio_rationale: portfolioChampionAgent?.rationale ?? null,
      portfolio_summary_holdings_count: portfolioSummary?.holdingsCount ?? null,
      portfolio_summary_weighted_upside_pct: portfolioSummary?.weightedUpsidePct ?? null,
      portfolio_summary_weighted_uncertainty_pct:
        portfolioSummary?.weightedUncertaintyPct ?? null,
      portfolio_summary_weighted_volatility_pct:
        portfolioSummary?.weightedVolatilityPct ?? null,
      portfolio_summary_weighted_drawdown_linger_days:
        portfolioSummary?.weightedDrawdownLingerDays ?? null,
      portfolio_summary_weighted_max_drawdown_pct:
        portfolioSummary?.weightedMaxDrawdownPct ?? null,
      portfolio_summary_weighted_dark_horse_score:
        portfolioSummary?.weightedDarkHorseScore ?? null,
      portfolio_summary_weighted_belief_score:
        portfolioSummary?.weightedBeliefScore ?? null,
      portfolio_summary_weighted_belief_agreement:
        portfolioSummary?.weightedBeliefAgreement ?? null,
      portfolio_summary_weighted_belief_polarization:
        portfolioSummary?.weightedBeliefPolarization ?? null,
      portfolio_summary_weighted_human_bias_score:
        portfolioSummary?.weightedHumanBiasScore ?? null,
      portfolio_summary_weighted_persistence_pct:
        portfolioSummary?.weightedPersistencePct ?? null,
      portfolio_summary_weighted_regime_risk_pct:
        portfolioSummary?.weightedRegimeRiskPct ?? null,
      portfolio_summary_weighted_web_neural_score:
        portfolioSummary?.weightedWebNeuralScore ?? null,
      portfolio_summary_weighted_web_neural_confidence:
        portfolioSummary?.weightedWebNeuralConfidence ?? null,
      portfolio_summary_reddit_small_cap_heat_score:
        portfolioSummary?.redditSmallCapHeatScore ?? null,
      portfolio_summary_reddit_small_cap_regime:
        portfolioSummary?.redditSmallCapRegime ?? null,
      portfolio_summary_fmkorea_stock_heat_score:
        portfolioSummary?.fmkoreaStockHeatScore ?? null,
      portfolio_summary_fmkorea_stock_regime:
        portfolioSummary?.fmkoreaStockRegime ?? null,
      portfolio_summary_weighted_fmkorea_surge_score:
        portfolioSummary?.weightedFmkoreaSurgeScore ?? null,
      portfolio_summary_weighted_small_cap_tail_score:
        portfolioSummary?.weightedSmallCapTailScore ?? null,
      portfolio_summary_weighted_heavy_tail_score:
        portfolioSummary?.weightedHeavyTailScore ?? null,
      portfolio_summary_weighted_heavy_tail_premium:
        portfolioSummary?.weightedHeavyTailPremium ?? null,
      portfolio_summary_weighted_long_tail_score:
        portfolioSummary?.weightedLongTailScore ?? null,
      portfolio_summary_weighted_left_tail_risk_score:
        portfolioSummary?.weightedLeftTailRiskScore ?? null,
      portfolio_summary_average_predicted_correlation:
        toFiniteNumber(
          sp500Portfolio?.correlationForecast?.averagePredictedCorrelation ??
            portfolioSummary?.averagePredictedCorrelation
        ) ?? null,
      portfolio_summary_average_absolute_correlation:
        toFiniteNumber(
          sp500Portfolio?.correlationForecast?.averageAbsoluteCorrelation ??
            portfolioSummary?.averageAbsoluteCorrelation
        ) ?? null,
      portfolio_summary_diversification_score:
        toFiniteNumber(
          sp500Portfolio?.correlationForecast?.diversificationScore ??
            portfolioSummary?.diversificationScore
        ) ?? null,
      portfolio_summary_crowded_pair_risk_score:
        toFiniteNumber(
          sp500Portfolio?.correlationForecast?.crowdedPairRiskScore ??
            portfolioSummary?.crowdedPairRiskScore
        ) ?? null,
      portfolio_summary_correlation_risk_label:
        sp500Portfolio?.correlationForecast?.concentrationRiskLabel ??
        portfolioSummary?.concentrationRiskLabel ??
        null,
      portfolio_summary_top_crowded_pairs: Array.isArray(
        sp500Portfolio?.correlationForecast?.topCrowdedPairs
      )
        ? sp500Portfolio.correlationForecast.topCrowdedPairs
            .slice(0, 3)
            .map(
              (entry: any) =>
                `${entry.leftSymbol}/${entry.rightSymbol} ${toFiniteNumber(entry.predictedCorrelation) ?? "--"}`
            )
            .join(" | ")
        : null,
      portfolio_reddit_small_cap_source: sp500Portfolio?.redditSmallCap?.source ?? null,
      portfolio_reddit_small_cap_subreddit: sp500Portfolio?.redditSmallCap?.subreddit ?? null,
      portfolio_reddit_small_cap_heat_score: sp500Portfolio?.redditSmallCap?.heatScore ?? null,
      portfolio_reddit_small_cap_regime: sp500Portfolio?.redditSmallCap?.regime ?? null,
      portfolio_reddit_small_cap_posts_analyzed:
        sp500Portfolio?.redditSmallCap?.postsAnalyzed ?? null,
      portfolio_reddit_small_cap_top_tickers: Array.isArray(
        sp500Portfolio?.redditSmallCap?.topTickers
      )
        ? sp500Portfolio.redditSmallCap.topTickers
            .slice(0, 5)
            .map((entry: any) => `${entry.symbol} ${entry.mentions}`)
            .join(" | ")
        : null,
      portfolio_reddit_small_cap_top_themes: Array.isArray(
        sp500Portfolio?.redditSmallCap?.topThemes
      )
        ? sp500Portfolio.redditSmallCap.topThemes
            .slice(0, 5)
            .map((entry: any) => `${entry.theme} ${entry.hits}`)
            .join(" | ")
        : null,
      portfolio_korean_surge_source: sp500Portfolio?.fmkoreaStock?.source ?? null,
      portfolio_korean_surge_board: sp500Portfolio?.fmkoreaStock?.board ?? null,
      portfolio_korean_surge_heat_score: sp500Portfolio?.fmkoreaStock?.heatScore ?? null,
      portfolio_korean_surge_regime: sp500Portfolio?.fmkoreaStock?.regime ?? null,
      portfolio_korean_surge_posts_analyzed:
        sp500Portfolio?.fmkoreaStock?.postsAnalyzed ?? null,
      portfolio_korean_surge_top_tickers: Array.isArray(
        sp500Portfolio?.fmkoreaStock?.topTickers
      )
        ? sp500Portfolio.fmkoreaStock.topTickers
            .slice(0, 5)
            .map((entry: any) => `${entry.symbol} ${entry.mentions}`)
            .join(" | ")
        : null,
      portfolio_korean_surge_top_keywords: Array.isArray(
        sp500Portfolio?.fmkoreaStock?.topKeywords
      )
        ? sp500Portfolio.fmkoreaStock.topKeywords
            .slice(0, 5)
            .map((entry: any) => `${entry.keyword} ${entry.mentions}`)
            .join(" | ")
        : null,
      portfolio_korean_surge_top_themes: Array.isArray(
        sp500Portfolio?.fmkoreaStock?.topThemes
      )
        ? sp500Portfolio.fmkoreaStock.topThemes
            .slice(0, 5)
            .map((entry: any) => `${entry.theme} ${entry.hits}`)
            .join(" | ")
        : null,
      portfolio_methodology_objective: portfolioMethodology?.objective ?? null,
      portfolio_allocation_methodology: portfolioAllocation?.methodology ?? null,
      portfolio_sleeves_summary: Array.isArray(portfolioAllocation?.sleeves)
        ? portfolioAllocation.sleeves
            .map((sleeve: any) => `${sleeve.label} ${sleeve.weightPct}%`)
            .join(" | ")
        : null,
      portfolio_sector_mix_summary: Array.isArray(portfolioAllocation?.sectorMix)
        ? portfolioAllocation.sectorMix
            .slice(0, 5)
            .map(
              (sector: any) =>
                `${sector.sector} ${sector.portfolioWeightPct}%`
            )
            .join(" | ")
        : null,
      portfolio_international_mix_summary: Array.isArray(portfolioAllocation?.internationalMix)
        ? portfolioAllocation.internationalMix
            .slice(0, 5)
            .map(
              (region: any) =>
                `${region.label} ${region.portfolioWeightPct}% (intl ${region.withinInternationalEquitiesPct}%)`
            )
            .join(" | ")
        : null,
      portfolio_top_holdings_summary: Array.isArray(portfolioTopHoldings)
        ? portfolioTopHoldings
            .slice(0, 5)
            .map(
              (holding: any) =>
                `${holding.symbol} ${holding.weightPct ?? "--"}% (upside ${
                  holding.maxUpsidePct ?? "--"
                }, uncertainty ${holding.uncertaintyRatio ?? "--"}, belief ${
                  holding.beliefScore ?? "--"
                }, human bias ${holding.humanBiasScore ?? "--"}, agreement ${
                  holding.beliefNetwork?.agreementRatio ?? "--"
                }, avg corr ${
                  holding.averagePredictedCorrelation ?? "--"
                })`
            )
            .join(" | ")
        : null,
      correlation_status: targetData?.correlationForecast?.status ?? null,
      correlation_average_predicted:
        toFiniteNumber(targetData?.correlationForecast?.averagePredictedCorrelation) ?? null,
      correlation_median_predicted:
        toFiniteNumber(targetData?.correlationForecast?.medianPredictedCorrelation) ?? null,
      correlation_positive_share:
        toFiniteNumber(targetData?.correlationForecast?.positiveShare) ?? null,
      correlation_inverse_share:
        toFiniteNumber(targetData?.correlationForecast?.inverseShare) ?? null,
      correlation_network_label: targetData?.correlationForecast?.networkLabel ?? null,
      correlation_top_peer_symbol:
        targetData?.correlationForecast?.topCorrelatedPeers?.[0]?.symbol ?? null,
      correlation_top_peer_value:
        toFiniteNumber(
          targetData?.correlationForecast?.topCorrelatedPeers?.[0]?.predictedCorrelation
        ) ?? null,
      correlation_top_diversifier_symbol:
        targetData?.correlationForecast?.topDiversifiers?.[0]?.symbol ?? null,
      correlation_top_diversifier_value:
        toFiniteNumber(
          targetData?.correlationForecast?.topDiversifiers?.[0]?.predictedCorrelation
        ) ?? null,
      selected_belief_score:
        selectedPortfolioHolding?.beliefScore ??
        selectedMapPoint?.beliefScore ??
        null,
      selected_belief_label:
        selectedPortfolioHolding?.beliefLabel ??
        selectedMapPoint?.beliefLabel ??
        null,
      selected_belief_rationale:
        selectedPortfolioHolding?.beliefRationale ??
        selectedMapPoint?.beliefRationale ??
        null,
      selected_private_signal_pct:
        selectedPortfolioHolding?.beliefNetwork?.privateSignalPct ??
        selectedMapPoint?.beliefNetwork?.privateSignalPct ??
        null,
      selected_crowd_belief_pct:
        selectedPortfolioHolding?.beliefNetwork?.crowdBeliefPct ??
        selectedMapPoint?.beliefNetwork?.crowdBeliefPct ??
        null,
      selected_belief_agreement:
        selectedPortfolioHolding?.beliefNetwork?.agreementRatio ??
        selectedMapPoint?.beliefNetwork?.agreementRatio ??
        null,
      selected_belief_polarization:
        selectedPortfolioHolding?.beliefNetwork?.polarizationScore ??
        selectedMapPoint?.beliefNetwork?.polarizationScore ??
        null,
      selected_belief_consensus_action:
        selectedPortfolioHolding?.beliefNetwork?.consensusAction ??
        selectedMapPoint?.beliefNetwork?.consensusAction ??
        null,
      selected_human_bias_score:
        selectedPortfolioHolding?.humanBiasScore ??
        selectedMapPoint?.humanBiasScore ??
        targetData.humanBias?.score ??
        null,
      selected_human_bias_label:
        selectedPortfolioHolding?.humanBiasLabel ??
        selectedMapPoint?.humanBiasLabel ??
        targetData.humanBias?.label ??
        null,
      selected_human_bias_rationale:
        selectedPortfolioHolding?.humanBiasRationale ??
        selectedMapPoint?.humanBiasRationale ??
        targetData.humanBias?.rationale ??
        null,
      selected_human_bias_short_count:
        selectedPortfolioHolding?.humanBias?.shortCount ??
        selectedMapPoint?.humanBias?.shortCount ??
        targetData.humanBias?.shortCount ??
        null,
      selected_human_bias_long_count:
        selectedPortfolioHolding?.humanBias?.longCount ??
        selectedMapPoint?.humanBias?.longCount ??
        targetData.humanBias?.longCount ??
        null,
      selected_human_bias_share_pct:
        selectedPortfolioHolding?.humanBias?.shortSharePct ??
        selectedMapPoint?.humanBias?.shortSharePct ??
        targetData.humanBias?.shortSharePct ??
        null,
      selected_human_bias_intensity_pct:
        selectedPortfolioHolding?.humanBias?.intensityPct ??
        selectedMapPoint?.humanBias?.intensityPct ??
        targetData.humanBias?.intensityPct ??
        null,
      selected_human_bias_trend_score:
        selectedPortfolioHolding?.humanBias?.trendScore ??
        selectedMapPoint?.humanBias?.trendScore ??
        targetData.humanBias?.trendScore ??
        null,
      selected_geometry_alignment_score: selectedPortfolioHolding?.geometryAlignmentScore ?? null,
      selected_geometry_kl_divergence: selectedPortfolioHolding?.geometryKlDivergence ?? null,
      selected_geometry_distance: selectedPortfolioHolding?.geometryDistance ?? null,
      selected_web_neural_score:
        selectedPortfolioHolding?.webNeuralScore ??
        selectedMapPoint?.webNeuralScore ??
        null,
      selected_web_neural_confidence:
        selectedPortfolioHolding?.webNeuralConfidence ??
        selectedMapPoint?.webNeuralConfidence ??
        null,
      selected_web_neural_label:
        selectedPortfolioHolding?.webNeuralLabel ??
        selectedMapPoint?.webNeuralLabel ??
        null,
      selected_market_cap: selectedPortfolioHolding?.marketCap ?? null,
      selected_market_cap_bucket: selectedPortfolioHolding?.marketCapBucket ?? null,
      selected_small_cap_tail_score:
        selectedPortfolioHolding?.smallCapTailScore ??
        selectedMapPoint?.smallCapTailProxyScore ??
        null,
      selected_heavy_tail_score:
        selectedPortfolioHolding?.heavyTailScore ??
        selectedMapPoint?.heavyTailProxyScore ??
        null,
      selected_heavy_tail_premium: selectedPortfolioHolding?.heavyTailPremium ?? null,
      selected_long_tail_score:
        selectedPortfolioHolding?.longTailScore ??
        selectedMapPoint?.tailDiagnostics?.longTailScore ??
        null,
      selected_left_tail_risk_score:
        selectedPortfolioHolding?.leftTailRiskScore ??
        selectedMapPoint?.tailDiagnostics?.leftTailRiskScore ??
        null,
      selected_heavy_tail_label:
        selectedPortfolioHolding?.heavyTailLabel ??
        selectedMapPoint?.heavyTailLabel ??
        null,
      selected_tail_regime_label:
        selectedPortfolioHolding?.tailRegimeLabel ??
        selectedMapPoint?.tailRegimeLabel ??
        selectedMapPoint?.tailDiagnostics?.regimeLabel ??
        null,
      selected_tail_skewness:
        selectedPortfolioHolding?.tailSkewness ??
        selectedMapPoint?.tailDiagnostics?.skewness ??
        null,
      selected_tail_excess_kurtosis:
        selectedPortfolioHolding?.tailExcessKurtosis ??
        selectedMapPoint?.tailDiagnostics?.excessKurtosis ??
        null,
      selected_heavy_tail_rationale:
        selectedPortfolioHolding?.heavyTailRationale ??
        selectedMapPoint?.heavyTailRationale ??
        null,
      selected_fmkorea_surge_score:
        selectedPortfolioHolding?.fmkoreaSurgeScore ??
        selectedMapPoint?.fmkoreaSurgeScore ??
        null,
      selected_fmkorea_mention_count:
        selectedPortfolioHolding?.fmkoreaMentionCount ??
        selectedMapPoint?.fmkoreaMentionCount ??
        null,
      selected_fmkorea_surge_label:
        selectedPortfolioHolding?.fmkoreaSurgeLabel ??
        selectedMapPoint?.fmkoreaSurgeLabel ??
        null,
      selected_average_predicted_correlation:
        toFiniteNumber(
          selectedPortfolioHolding?.averagePredictedCorrelation ??
            targetData?.correlationForecast?.averagePredictedCorrelation
        ) ?? null,
      selected_diversification_support_score:
        toFiniteNumber(selectedPortfolioHolding?.diversificationSupportScore) ?? null,
      selected_strongest_correlation_peer:
        selectedPortfolioHolding?.strongestCorrelationPeer ??
        targetData?.correlationForecast?.topCorrelatedPeers?.[0]?.symbol ??
        null,
      selected_strongest_correlation_value:
        toFiniteNumber(
          selectedPortfolioHolding?.strongestCorrelationValue ??
            targetData?.correlationForecast?.topCorrelatedPeers?.[0]?.predictedCorrelation
        ) ?? null,
      selected_strongest_diversifier_peer:
        selectedPortfolioHolding?.strongestDiversifierPeer ??
        targetData?.correlationForecast?.topDiversifiers?.[0]?.symbol ??
        null,
      selected_strongest_diversifier_value:
        toFiniteNumber(
          selectedPortfolioHolding?.strongestDiversifierValue ??
            targetData?.correlationForecast?.topDiversifiers?.[0]?.predictedCorrelation
        ) ?? null,
      selected_first_coordinate_x:
        selectedMapPoint?.firstCoordinateSpace?.x ??
        selectedMapPoint?.momentumSpace?.x ??
        null,
      selected_first_coordinate_y:
        selectedMapPoint?.firstCoordinateSpace?.y ??
        selectedMapPoint?.momentumSpace?.y ??
        null,
      selected_second_coordinate_x:
        selectedMapPoint?.secondCoordinateSpace?.x ??
        selectedMapPoint?.convictionSpace?.x ??
        null,
      selected_second_coordinate_y:
        selectedMapPoint?.secondCoordinateSpace?.y ??
        selectedMapPoint?.convictionSpace?.y ??
        null,
      selected_dark_horse_score: selectedMapPoint?.darkHorseScore ?? null,
      selected_dark_horse_label: selectedMapPoint?.darkHorseLabel ?? null,
      selected_dark_horse_rank: selectedMapPoint?.darkHorseRank ?? null,
      selected_dark_horse_rationale: selectedMapPoint?.darkHorseRationale ?? null,
      selected_symmetry_counterpart_symbol:
        selectedMapPoint?.symmetry?.counterpartSymbol ?? null,
      selected_symmetry_counterpart_action:
        selectedMapPoint?.symmetry?.counterpartAction ?? null,
      selected_symmetry_counterpart_quadrant:
        selectedMapPoint?.symmetry?.counterpartQuadrant ?? null,
      selected_symmetry_residual_score:
        selectedMapPoint?.symmetry?.residualScore ?? null,
      selected_symmetry_quality_score:
        selectedMapPoint?.symmetry?.qualityScore ?? null,
      selected_symmetry_underfollowed_score:
        selectedMapPoint?.symmetry?.underfollowedScore ?? null,
      website_neural_model_status: informationMapNeuralModel?.status ?? null,
      website_neural_model_updated_at: informationMapNeuralModel?.updatedAt ?? null,
      website_neural_model_training_rows:
        informationMapNeuralModel?.trainingRows ?? null,
      website_neural_model_validation_rows:
        informationMapNeuralModel?.validationRows ?? null,
      website_neural_model_feature_count:
        informationMapNeuralModel?.featureCount ?? null,
      website_neural_model_fit_mode:
        informationMapNeuralModel?.fitMode ?? null,
      website_neural_model_validation_mae:
        informationMapNeuralModel?.validationMae ?? null,
      website_neural_model_validation_rmse:
        informationMapNeuralModel?.validationRmse ?? null,
      feature_benchmark_status: informationMapFeatureBenchmark?.status ?? null,
      feature_benchmark_updated_at: informationMapFeatureBenchmark?.updatedAt ?? null,
      feature_benchmark_rows: informationMapFeatureBenchmark?.rows ?? null,
      feature_benchmark_training_rows:
        informationMapFeatureBenchmark?.trainingRows ?? null,
      feature_benchmark_validation_rows:
        informationMapFeatureBenchmark?.validationRows ?? null,
      feature_benchmark_methods_compared:
        Array.isArray(informationMapFeatureBenchmark?.methodsCompared)
          ? informationMapFeatureBenchmark.methodsCompared.length
          : null,
      feature_benchmark_recommended_method:
        informationMapFeatureBenchmark?.recommendedMethod?.method ?? null,
      feature_benchmark_recommended_latent_dim:
        toFiniteNumber(informationMapFeatureBenchmark?.recommendedMethod?.latentDim) ?? null,
      feature_benchmark_recommended_validation_mae:
        toFiniteNumber(informationMapFeatureBenchmark?.recommendedMethod?.validationMae) ?? null,
      feature_benchmark_recommended_validation_rmse:
        toFiniteNumber(informationMapFeatureBenchmark?.recommendedMethod?.validationRmse) ?? null,
      feature_benchmark_summary: informationMapFeatureBenchmark?.summary ?? null,
      feature_benchmark_error: informationMapFeatureBenchmark?.error ?? null,
      agent_bagging_enabled: wrapperBagging?.enabled ?? null,
      agent_bagging_action:
        firstDefined(wrapperBagging?.action, wrapperBagging?.meanAction) ?? null,
      agent_bagging_mean_action: wrapperBagging?.meanAction ?? null,
      agent_bagging_mean_vote:
        toFiniteNumber(firstDefined(wrapperBagging?.meanVote, wrapperBagging?.mean_vote)) ??
        null,
      agent_bagging_vote_std:
        toFiniteNumber(firstDefined(wrapperBagging?.voteStd, wrapperBagging?.vote_std)) ??
        null,
      agent_bagging_base_weighted_vote:
        toFiniteNumber(
          firstDefined(wrapperBagging?.baseWeightedVote, wrapperBagging?.base_weighted_vote)
        ) ?? null,
      agent_bagging_blended_vote:
        toFiniteNumber(
          firstDefined(wrapperBagging?.blendedVote, wrapperBagging?.blended_vote)
        ) ?? null,
      agent_bagging_stability:
        toFiniteNumber(wrapperBagging?.stability) ?? null,
      agent_bagging_execution_probability:
        toFiniteNumber(
          firstDefined(
            wrapperBagging?.executionAllowedProbability,
            wrapperBagging?.execution_allowed_probability
          )
        ) ?? null,
      agent_bagging_buy_probability:
        toFiniteNumber(wrapperBaggingActionProbabilities?.BUY) ?? null,
      agent_bagging_hold_probability:
        toFiniteNumber(wrapperBaggingActionProbabilities?.HOLD) ?? null,
      agent_bagging_sell_probability:
        toFiniteNumber(wrapperBaggingActionProbabilities?.SELL) ?? null,
      agent_bagging_iterations:
        toFiniteNumber(wrapperBagging?.iterations) ?? null,
      agent_bagging_sample_size:
        toFiniteNumber(firstDefined(wrapperBagging?.sampleSize, wrapperBagging?.sample_size)) ??
        null,
      em_agent_action: firstDefined(emAgentOutput?.action) ?? null,
      em_agent_confidence: toFiniteNumber(emAgentOutput?.confidence) ?? null,
      em_dominant_regime:
        firstDefined(
          emLocalMetrics?.em_dominant_regime,
          emLocalMetrics?.emDominantRegime
        ) ?? null,
      em_bull_probability:
        toFiniteNumber(
          firstDefined(
            emLocalMetrics?.em_bull_probability,
            emLocalMetrics?.emBullProbability
          )
        ) ?? null,
      em_neutral_probability:
        toFiniteNumber(
          firstDefined(
            emLocalMetrics?.em_neutral_probability,
            emLocalMetrics?.emNeutralProbability
          )
        ) ?? null,
      em_bear_probability:
        toFiniteNumber(
          firstDefined(
            emLocalMetrics?.em_bear_probability,
            emLocalMetrics?.emBearProbability
          )
        ) ?? null,
      em_regime_gap:
        toFiniteNumber(
          firstDefined(emLocalMetrics?.em_regime_gap, emLocalMetrics?.emRegimeGap)
        ) ?? null,
      em_dominant_probability:
        toFiniteNumber(
          firstDefined(
            emLocalMetrics?.em_dominant_probability,
            emLocalMetrics?.emDominantProbability
          )
        ) ?? null,
      em_weighted_signal_mean:
        toFiniteNumber(
          firstDefined(
            emLocalMetrics?.em_weighted_signal_mean,
            emLocalMetrics?.emWeightedSignalMean
          )
        ) ?? null,
      em_log_likelihood:
        toFiniteNumber(
          firstDefined(emLocalMetrics?.em_log_likelihood, emLocalMetrics?.emLogLikelihood)
        ) ?? null,
      em_iterations:
        toFiniteNumber(firstDefined(emLocalMetrics?.em_iterations, emLocalMetrics?.emIterations)) ??
        null,
      em_observation_count:
        toFiniteNumber(
          firstDefined(
            emLocalMetrics?.em_observation_count,
            emLocalMetrics?.emObservationCount
          )
        ) ?? null,
      minimax_agent_action: firstDefined(minimaxAgentOutput?.action) ?? null,
      minimax_agent_confidence:
        toFiniteNumber(firstDefined(minimaxAgentOutput?.confidence)) ?? null,
      minimax_selected_class:
        firstDefined(
          minimaxLocalMetrics?.minimax_selected_class,
          minimaxLocalMetrics?.minimaxSelectedClass
        ) ?? null,
      minimax_worst_class:
        firstDefined(
          minimaxLocalMetrics?.minimax_worst_class,
          minimaxLocalMetrics?.minimaxWorstClass
        ) ?? null,
      minimax_adversarial_focus:
        firstDefined(
          minimaxLocalMetrics?.minimax_adversarial_focus,
          minimaxLocalMetrics?.minimaxAdversarialFocus
        ) ?? null,
      minimax_robust_margin:
        toFiniteNumber(
          firstDefined(
            minimaxLocalMetrics?.minimax_robust_margin,
            minimaxLocalMetrics?.minimaxRobustMargin
          )
        ) ?? null,
      minimax_worst_loss:
        toFiniteNumber(
          firstDefined(
            minimaxLocalMetrics?.minimax_worst_loss,
            minimaxLocalMetrics?.minimaxWorstLoss
          )
        ) ?? null,
      minimax_buy_loss:
        toFiniteNumber(
          firstDefined(
            minimaxLocalMetrics?.minimax_buy_loss,
            minimaxLocalMetrics?.minimaxBuyLoss
          )
        ) ?? null,
      minimax_hold_loss:
        toFiniteNumber(
          firstDefined(
            minimaxLocalMetrics?.minimax_hold_loss,
            minimaxLocalMetrics?.minimaxHoldLoss
          )
        ) ?? null,
      minimax_sell_loss:
        toFiniteNumber(
          firstDefined(
            minimaxLocalMetrics?.minimax_sell_loss,
            minimaxLocalMetrics?.minimaxSellLoss
          )
        ) ?? null,
      minimax_buy_probability:
        toFiniteNumber(
          firstDefined(
            minimaxLocalMetrics?.minimax_buy_probability,
            minimaxLocalMetrics?.minimaxBuyProbability
          )
        ) ?? null,
      minimax_hold_probability:
        toFiniteNumber(
          firstDefined(
            minimaxLocalMetrics?.minimax_hold_probability,
            minimaxLocalMetrics?.minimaxHoldProbability
          )
        ) ?? null,
      minimax_sell_probability:
        toFiniteNumber(
          firstDefined(
            minimaxLocalMetrics?.minimax_sell_probability,
            minimaxLocalMetrics?.minimaxSellProbability
          )
        ) ?? null,
      minimax_adversarial_prior_buy:
        toFiniteNumber(
          firstDefined(
            minimaxLocalMetrics?.minimax_adversarial_prior_buy,
            minimaxLocalMetrics?.minimaxAdversarialPriorBuy
          )
        ) ?? null,
      minimax_adversarial_prior_hold:
        toFiniteNumber(
          firstDefined(
            minimaxLocalMetrics?.minimax_adversarial_prior_hold,
            minimaxLocalMetrics?.minimaxAdversarialPriorHold
          )
        ) ?? null,
      minimax_adversarial_prior_sell:
        toFiniteNumber(
          firstDefined(
            minimaxLocalMetrics?.minimax_adversarial_prior_sell,
            minimaxLocalMetrics?.minimaxAdversarialPriorSell
          )
        ) ?? null,
      minimax_tail_regime_label:
        firstDefined(
          minimaxLocalMetrics?.tail_regime_label,
          minimaxLocalMetrics?.tailRegimeLabel
        ) ?? null,
      regret_agent_action:
        firstDefined(regretAgentOutput?.action, fallbackRegret.regretAction) ?? null,
      regret_agent_confidence:
        toFiniteNumber(
          firstDefined(regretAgentOutput?.confidence, fallbackRegret.regretConfidence)
        ) ?? null,
      regret_risk_score:
        toFiniteNumber(
          firstDefined(
            regretLocalMetrics?.regret_risk_score,
            regretLocalMetrics?.regretRiskScore,
            fallbackRegret.regretRiskScore
          )
        ) ?? null,
      regret_bias:
        firstDefined(
          regretLocalMetrics?.regret_bias,
          regretLocalMetrics?.regretBias,
          fallbackRegret.regretBias
        ) ?? null,
      buy_regret_score:
        toFiniteNumber(
          firstDefined(
            regretLocalMetrics?.buy_regret_score,
            regretLocalMetrics?.buyRegretScore,
            fallbackRegret.buyRegretScore
          )
        ) ?? null,
      sell_regret_score:
        toFiniteNumber(
          firstDefined(
            regretLocalMetrics?.sell_regret_score,
            regretLocalMetrics?.sellRegretScore,
            fallbackRegret.sellRegretScore
          )
        ) ?? null,
      optimal_buy_timestamp: targetData.optimalBuyTimestamp ?? null,
      optimal_buy_price: targetData.optimalBuyPrice ?? null,
      optimal_sell_timestamp: targetData.optimalSellTimestamp ?? null,
      optimal_sell_price: targetData.optimalSellPrice ?? null,
      cadence_profile: targetData.cadenceProfile ?? "unknown",
      token_symbol: token,
      input_symbol: inputSymbol ?? "SOL",
      output_symbol: outputSymbol ?? token,
      current_loss: currentLoss,
      best_route_path: bestRoutePath ?? "unknown",
      route_legs: routeLegs ?? 0,
      route_price_impact_pct: routePriceImpactPct ?? 0,
      as_of_timestamp: new Date().toISOString(),
    };

    // Pipeline 실행
    const result = await runLLMPipeline(decisionInput);

    // 🔥 여기서 최종 응답 직전 로그 확인
    console.log("📤 [API Route] 클라이언트로 전송될 데이터:", JSON.stringify(result).slice(0, 100) + "...");

    return secureJson({ llm: result });

  } catch (error: any) {
    console.error("❌ [API Route] Critical Error:", error);
    return secureJson({ 
      llm: {
        buy: "Error",
        wait: "Error",
        routeGuidance: "Error",
        dynamicPortfolioView: "Error",
        momentumSummary: "Error",
        seasonalitySummary: "Error",
        timingSummary: "Error",
        spikeSustainSummary: "Error",
        drawdownLingerSummary: "Error",
        regretSummary: "Error",
        nextActionDate: "Error",
        macbookView: "Error",
        final: "분석 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
      } 
    }, { status: 500 });
  }
}
