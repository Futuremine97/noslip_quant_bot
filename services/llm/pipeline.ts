import { createHash } from "node:crypto";

import {
  runBuffettViewAgent,
  runBuyAgent,
  runDynamicPortfolioViewAgent,
  runDrawdownLingerAgent,
  runDalioViewAgent,
  runDruckenmillerViewAgent,
  runLynchViewAgent,
  runMacbookViewAgent,
  runMomentumSummaryAgent,
  runNextActionDateAgent,
  runRegretAgent,
  runRouteGuidanceAgent,
  runSeasonalitySummaryAgent,
  runSpikeSustainAgent,
  runTimingWindowAgent,
  runWaitAgent,
} from "./agents";
import { runDebate } from "./debate";
import { getMacroBackdrop, type MacroBackdrop } from "./macro";
import { getInvestorLensSnapshot } from "./reinforcement";

const KOREAN_DATE_PARTS_FORMAT = new Intl.DateTimeFormat("ko-KR", {
  year: "numeric",
  month: "numeric",
  day: "numeric",
  hour: "numeric",
  hourCycle: "h23",
  timeZone: "America/Chicago",
});

const formatKoreanSendWindow = (value: Date) => {
  const parts = KOREAN_DATE_PARTS_FORMAT.formatToParts(value);
  const lookup = (type: string) =>
    parts.find((part) => part.type === type)?.value || "";

  const year = lookup("year");
  const month = lookup("month");
  const day = lookup("day");
  const hour = lookup("hour").padStart(2, "0");

  return `${year}년 ${month}월 ${day}일 ${hour}시 전후`;
};

const formatKoreanDurationWindow = (secondsValue: unknown) => {
  const seconds = Number(secondsValue);
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return "unknown";
  }

  const totalMinutes = Math.round(seconds / 60);
  const days = Math.floor(totalMinutes / (60 * 24));
  const hours = Math.floor((totalMinutes % (60 * 24)) / 60);
  const minutes = totalMinutes % 60;
  const parts: string[] = [];

  if (days > 0) {
    parts.push(`${days}일`);
  }
  if (hours > 0) {
    parts.push(`${hours}시간`);
  }
  if (days === 0 && minutes > 0) {
    parts.push(`${minutes}분`);
  }

  return parts.join(" ") || "1시간 이내";
};

const formatSecondMomentMagnitude = (value: unknown) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "unknown";
  }

  const absBpPerDay2 = Math.abs(numeric) * 10_000 * 24 * 24;
  const direction = numeric > 0 ? "가속" : numeric < 0 ? "감속" : "중립";

  let magnitude = "미약";
  if (absBpPerDay2 >= 2) {
    magnitude = "강함";
  } else if (absBpPerDay2 >= 0.75) {
    magnitude = "보통";
  } else if (absBpPerDay2 >= 0.15) {
    magnitude = "약함";
  }

  const signedBpPerDay2 = numeric * 10_000 * 24 * 24;
  const prefix = signedBpPerDay2 > 0 ? "+" : "";
  return `${direction} ${magnitude} (${prefix}${signedBpPerDay2.toFixed(2)} bp/day²)`;
};

type StringTask = {
  key: string;
  run: () => Promise<string>;
  enabled?: boolean;
  fallback?: string;
};

const LLM_PIPELINE_CACHE_TTL_MS = Math.max(
  30_000,
  Number(process.env.LLM_PIPELINE_CACHE_TTL_MS || 2 * 60 * 1000)
);
const LLM_PIPELINE_CACHE_MAX_ENTRIES = Math.max(
  16,
  Number(process.env.LLM_PIPELINE_CACHE_MAX_ENTRIES || 128)
);

const llmPipelineCache = new Map<
  string,
  {
    expiresAt: number;
    value: any;
  }
>();
const llmPipelineInFlight = new Map<string, Promise<any>>();

const DEFAULT_NON_STOCK_MACRO: MacroBackdrop = {
  source: "skipped",
  m2LatestDate: null,
  m2LevelBillions: null,
  m2ThreeMonthPct: null,
  m2YearPct: null,
  policyRateLatestDate: null,
  policyRatePct: null,
  policyRateThreeMonthChangeBps: null,
  policyRateYearChangeBps: null,
  liquidityRegime: "비주식 모드",
  rateRegime: "비주식 모드",
  summary: "현재 모드는 주식 직접 분석이 아니어서 M2·금리 기반 거시 컨텍스트 호출을 생략했습니다.",
};

const buildPipelineCacheKey = (decision: any) =>
  createHash("sha1").update(JSON.stringify(decision)).digest("hex");

const pruneLlmPipelineCache = () => {
  const now = Date.now();

  for (const [key, entry] of llmPipelineCache.entries()) {
    if (entry.expiresAt <= now) {
      llmPipelineCache.delete(key);
    }
  }

  while (llmPipelineCache.size > LLM_PIPELINE_CACHE_MAX_ENTRIES) {
    const oldestKey = llmPipelineCache.keys().next().value;
    if (!oldestKey) {
      break;
    }
    llmPipelineCache.delete(oldestKey);
  }
};

const runStringTaskBatches = async (
  tasks: StringTask[],
  batchSize = 4
): Promise<Record<string, string>> => {
  const output: Record<string, string> = {};
  const safeBatchSize = Math.max(1, batchSize);

  for (let index = 0; index < tasks.length; index += safeBatchSize) {
    const batch = tasks.slice(index, index + safeBatchSize);
    const runnableBatch = batch.filter((task) => task.enabled !== false);
    const values = await Promise.all(
      runnableBatch.map(async (task) => {
        try {
          return await task.run();
        } catch (error: any) {
          return `분석 실패: ${error?.message || "unknown error"}`;
        }
      })
    );

    let valueIndex = 0;
    batch.forEach((task) => {
      if (task.enabled === false) {
        output[task.key] = task.fallback ?? "";
        return;
      }
      output[task.key] = values[valueIndex];
      valueIndex += 1;
    });
  }

  return output;
};

const resolveSuggestedSendDate = (decision: any) => {
  const targetTimestamp =
    typeof decision.target_timestamp === "string" ? new Date(decision.target_timestamp) : null;

  if (targetTimestamp && !Number.isNaN(targetTimestamp.getTime())) {
    return formatKoreanSendWindow(targetTimestamp);
  }

  const baseDate =
    typeof decision.as_of_timestamp === "string"
      ? new Date(decision.as_of_timestamp)
      : new Date();
  const safeBaseDate = Number.isNaN(baseDate.getTime()) ? new Date() : baseDate;
  const waitSeconds = Number(decision.time_to_low ?? 0);

  if (Number.isFinite(waitSeconds) && waitSeconds > 0) {
    return formatKoreanSendWindow(
      new Date(safeBaseDate.getTime() + waitSeconds * 1000)
    );
  }

  return formatKoreanSendWindow(safeBaseDate);
};

const classifyTurnoverPotential = (decision: any) => {
  const candidates = [
    Number(decision.time_to_optimal_buy_seconds),
    Number(decision.time_to_optimal_sell_seconds),
    Number(decision.rise_window_seconds),
    Number(decision.drop_window_seconds),
  ].filter((value) => Number.isFinite(value) && value > 0);

  if (candidates.length === 0) {
    return "unknown";
  }

  const bestSeconds = Math.min(...candidates);
  if (bestSeconds <= 5 * 24 * 3600) {
    return "high";
  }
  if (bestSeconds <= 20 * 24 * 3600) {
    return "medium";
  }
  return "low";
};

const toFiniteNumber = (value: unknown) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
};

const formatSignedPct = (value: number | null) => {
  if (value == null) {
    return "unknown";
  }
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${(value * 100).toFixed(2)}%`;
};

const classifyOvershootConfidence = (
  uncertaintyRatio: number | null,
  secondMomentPctPerHour2: number | null
) => {
  const absSecondMomentBpPerDay2 =
    secondMomentPctPerHour2 == null
      ? null
      : Math.abs(secondMomentPctPerHour2) * 10_000 * 24 * 24;

  if (
    uncertaintyRatio != null &&
    uncertaintyRatio <= 0.02 &&
    absSecondMomentBpPerDay2 != null &&
    absSecondMomentBpPerDay2 >= 0.75
  ) {
    return "high";
  }

  if (
    (uncertaintyRatio != null && uncertaintyRatio <= 0.05) ||
    (absSecondMomentBpPerDay2 != null && absSecondMomentBpPerDay2 >= 0.15)
  ) {
    return "medium";
  }

  return "low";
};

const buildOvershootContext = (decision: any) => {
  const livePrice = toFiniteNumber(decision.live_price);
  const currentPrice = toFiniteNumber(decision.current_price);
  const lastClosePrice = toFiniteNumber(decision.last_close_price);
  const referencePrice = livePrice ?? currentPrice ?? lastClosePrice;
  const optimalBuyPrice = toFiniteNumber(decision.optimal_buy_price);
  const optimalSellPrice = toFiniteNumber(decision.optimal_sell_price);
  const directionStrength = toFiniteNumber(decision.direction_strength) ?? 0;
  const uncertaintyRatio = toFiniteNumber(decision.uncertainty_ratio);
  const secondMoment = toFiniteNumber(decision.second_moment_pct_per_hour2);

  const upsideReachPct =
    referencePrice != null &&
    optimalSellPrice != null &&
    referencePrice > 0
      ? optimalSellPrice / referencePrice - 1
      : null;
  const downsideReachPct =
    referencePrice != null &&
    optimalBuyPrice != null &&
    referencePrice > 0
      ? optimalBuyPrice / referencePrice - 1
      : null;

  const timeToOptimalSellSeconds = toFiniteNumber(decision.time_to_optimal_sell_seconds);
  const timeToOptimalBuySeconds = toFiniteNumber(decision.time_to_optimal_buy_seconds);
  const riseWindowSeconds = toFiniteNumber(decision.rise_window_seconds);
  const dropWindowSeconds = toFiniteNumber(decision.drop_window_seconds);

  const prefersUpside =
    (directionStrength >= 0 && upsideReachPct != null && upsideReachPct > 0) ||
    (upsideReachPct != null &&
      downsideReachPct != null &&
      Math.abs(upsideReachPct) >= Math.abs(downsideReachPct));

  if (prefersUpside && upsideReachPct != null && upsideReachPct > 0) {
    return {
      bias: "upside",
      reachPct: upsideReachPct,
      reachText: formatSignedPct(upsideReachPct),
      sustainSeconds: timeToOptimalSellSeconds,
      sustainHuman: formatKoreanDurationWindow(timeToOptimalSellSeconds),
      fullWindowSeconds: riseWindowSeconds,
      fullWindowHuman: formatKoreanDurationWindow(riseWindowSeconds),
      confidence: classifyOvershootConfidence(uncertaintyRatio, secondMoment),
    };
  }

  if (downsideReachPct != null && downsideReachPct < 0) {
    return {
      bias: "downside",
      reachPct: downsideReachPct,
      reachText: formatSignedPct(downsideReachPct),
      sustainSeconds: timeToOptimalBuySeconds,
      sustainHuman: formatKoreanDurationWindow(timeToOptimalBuySeconds),
      fullWindowSeconds: dropWindowSeconds,
      fullWindowHuman: formatKoreanDurationWindow(dropWindowSeconds),
      confidence: classifyOvershootConfidence(uncertaintyRatio, secondMoment),
    };
  }

  return {
    bias: "balanced",
    reachPct: null,
    reachText: "unknown",
    sustainSeconds: null,
    sustainHuman: "unknown",
    fullWindowSeconds: null,
    fullWindowHuman: "unknown",
    confidence: classifyOvershootConfidence(uncertaintyRatio, secondMoment),
  };
};

const buildInput = (
  decision: any,
  macroBackdrop: Awaited<ReturnType<typeof getMacroBackdrop>>,
  investorLensSnapshot: Awaited<ReturnType<typeof getInvestorLensSnapshot>>
) => {
  const suggestedSendDate = resolveSuggestedSendDate(decision);
  const turnoverPotential = classifyTurnoverPotential(decision);
  const overshoot = buildOvershootContext(decision);
  const lensWeight = (lens: string) =>
    investorLensSnapshot.lenses.find((item) => item.lens === lens)?.weight ?? 1;

  return `
Market Mode: ${decision.market_mode ?? "crypto"}
Input Symbol: ${decision.input_symbol ?? "SOL"}
Output Symbol: ${decision.output_symbol ?? decision.token_symbol}
Token Symbol: ${decision.token_symbol}
As Of Timestamp: ${decision.as_of_timestamp ?? "unknown"}
Current Price: ${decision.current_price}
Live Price: ${decision.live_price ?? "unknown"}
Last Close Price: ${decision.last_close_price ?? decision.current_price}
Target Price: ${decision.target_price}
Time to Low (seconds): ${decision.time_to_low}
Time to Optimal Buy (seconds): ${decision.time_to_optimal_buy_seconds ?? "unknown"}
Time to Optimal Sell (seconds): ${decision.time_to_optimal_sell_seconds ?? "unknown"}
Rise Window (seconds): ${decision.rise_window_seconds ?? "unknown"}
Rise Window (human): ${formatKoreanDurationWindow(decision.rise_window_seconds)}
Drop Window (seconds): ${decision.drop_window_seconds ?? "unknown"}
Drop Window (human): ${formatKoreanDurationWindow(decision.drop_window_seconds)}
Spike Sustain (seconds): ${decision.spike_sustain_seconds ?? "unknown"}
Spike Sustain (human): ${formatKoreanDurationWindow(decision.spike_sustain_seconds)}
Spike Sustain Consensus (seconds): ${decision.spike_sustain_consensus_seconds ?? "unknown"}
Spike Sustain Consensus (human): ${formatKoreanDurationWindow(decision.spike_sustain_consensus_seconds)}
Spike Consensus Source: ${decision.spike_consensus_source ?? "unknown"}
Spike Start Timestamp: ${decision.spike_start_timestamp ?? "unknown"}
Spike Peak Timestamp: ${decision.spike_peak_timestamp ?? "unknown"}
Spike Peak Price: ${decision.spike_peak_price ?? "unknown"}
Spike Fade Timestamp: ${decision.spike_fade_timestamp ?? "unknown"}
Spike Fade In Horizon: ${decision.spike_fade_in_horizon ?? "unknown"}
Peak To Fade (seconds): ${decision.peak_to_fade_seconds ?? "unknown"}
Peak To Fade (human): ${formatKoreanDurationWindow(decision.peak_to_fade_seconds)}
Max Spike Pct: ${decision.max_spike_pct ?? "unknown"}
Drawdown Linger (seconds): ${decision.drawdown_linger_seconds ?? "unknown"}
Drawdown Linger (human): ${formatKoreanDurationWindow(decision.drawdown_linger_seconds)}
Drawdown Linger Consensus (seconds): ${decision.drawdown_linger_consensus_seconds ?? "unknown"}
Drawdown Linger Consensus (human): ${formatKoreanDurationWindow(decision.drawdown_linger_consensus_seconds)}
Drawdown Consensus Source: ${decision.drawdown_consensus_source ?? "unknown"}
Portfolio Geometry Space: ${decision.portfolio_geometry_space ?? "unknown"}
Portfolio Geometry Method: ${decision.portfolio_geometry_method ?? "unknown"}
Portfolio Geometry Risk Profile: ${decision.portfolio_geometry_risk_profile ?? "unknown"}
Portfolio Geometry Target X: ${decision.portfolio_geometry_target_x ?? "unknown"}
Portfolio Geometry Target Y: ${decision.portfolio_geometry_target_y ?? "unknown"}
Portfolio Geometry Point X: ${decision.portfolio_geometry_portfolio_x ?? "unknown"}
Portfolio Geometry Point Y: ${decision.portfolio_geometry_portfolio_y ?? "unknown"}
Portfolio Geometry Alignment Score: ${decision.portfolio_geometry_alignment_score ?? "unknown"}
Portfolio Geometry KL Divergence: ${decision.portfolio_geometry_kl_divergence ?? "unknown"}
Portfolio Geometry Distance: ${decision.portfolio_geometry_distance ?? "unknown"}
Portfolio Natural Gradient Method: ${decision.portfolio_natural_gradient_method ?? "unknown"}
Portfolio Natural Gradient Metric: ${decision.portfolio_natural_gradient_metric ?? "unknown"}
Portfolio Natural Gradient Iterations: ${decision.portfolio_natural_gradient_iterations ?? "unknown"}
Portfolio Natural Gradient Step Size: ${decision.portfolio_natural_gradient_step_size ?? "unknown"}
Portfolio Natural Gradient Temperature: ${decision.portfolio_natural_gradient_temperature ?? "unknown"}
Portfolio Natural Gradient Upper Bound Score: ${decision.portfolio_natural_gradient_upper_bound_score ?? "unknown"}
Portfolio Natural Gradient Live Distance To Target: ${decision.portfolio_natural_gradient_live_distance_to_target ?? "unknown"}
Portfolio Natural Gradient Bound Distance To Target: ${decision.portfolio_natural_gradient_bound_distance_to_target ?? "unknown"}
Portfolio Natural Gradient Live Distance To Bound: ${decision.portfolio_natural_gradient_live_distance_to_bound ?? "unknown"}
Portfolio Natural Gradient Live Entropy: ${decision.portfolio_natural_gradient_live_entropy ?? "unknown"}
Portfolio Natural Gradient Bound Entropy: ${decision.portfolio_natural_gradient_bound_entropy ?? "unknown"}
Portfolio Natural Gradient Fisher Trace: ${decision.portfolio_natural_gradient_fisher_trace ?? "unknown"}
Portfolio Natural Gradient Fisher Curvature: ${decision.portfolio_natural_gradient_fisher_curvature ?? "unknown"}
Portfolio Natural Gradient Risk Envelope Strength: ${decision.portfolio_natural_gradient_risk_envelope_strength ?? "unknown"}
Portfolio Manifold Method: ${decision.portfolio_manifold_method ?? "unknown"}
Portfolio Manifold History Count: ${decision.portfolio_manifold_history_count ?? "unknown"}
Portfolio Manifold Rank: ${decision.portfolio_manifold_rank ?? "unknown"}
Portfolio Manifold State Dimension: ${decision.portfolio_manifold_state_dimension ?? "unknown"}
Portfolio Manifold Continuity Score: ${decision.portfolio_manifold_continuity_score ?? "unknown"}
Portfolio Manifold Target Distance: ${decision.portfolio_manifold_target_distance ?? "unknown"}
Portfolio Manifold Bridge Mode: ${decision.portfolio_manifold_bridge_mode ?? "unknown"}
Portfolio Manifold Bridge Loss: ${decision.portfolio_manifold_bridge_loss ?? "unknown"}
Champion Portfolio Name: ${decision.champion_portfolio_name ?? "unknown"}
Champion Portfolio Method: ${decision.champion_portfolio_method ?? "unknown"}
Champion Portfolio Profile: ${decision.champion_portfolio_profile ?? "unknown"}
Champion Portfolio Label: ${decision.champion_portfolio_label ?? "unknown"}
Champion Portfolio Score: ${decision.champion_portfolio_score ?? "unknown"}
Champion Portfolio Continuity Score: ${decision.champion_portfolio_continuity_score ?? "unknown"}
Champion Portfolio Target Distance: ${decision.champion_portfolio_target_distance ?? "unknown"}
Champion Portfolio Rationale: ${decision.champion_portfolio_rationale ?? "unknown"}
Portfolio Holdings Count: ${decision.portfolio_summary_holdings_count ?? "unknown"}
Portfolio Weighted Upside Pct: ${decision.portfolio_summary_weighted_upside_pct ?? "unknown"}
Portfolio Weighted Uncertainty Pct: ${decision.portfolio_summary_weighted_uncertainty_pct ?? "unknown"}
Portfolio Weighted Volatility Pct: ${decision.portfolio_summary_weighted_volatility_pct ?? "unknown"}
Portfolio Weighted Drawdown Linger Days: ${decision.portfolio_summary_weighted_drawdown_linger_days ?? "unknown"}
Portfolio Weighted Max Drawdown Pct: ${decision.portfolio_summary_weighted_max_drawdown_pct ?? "unknown"}
Portfolio Weighted Dark Horse Score: ${decision.portfolio_summary_weighted_dark_horse_score ?? "unknown"}
Portfolio Weighted Belief Score: ${decision.portfolio_summary_weighted_belief_score ?? "unknown"}
Portfolio Weighted Belief Agreement: ${decision.portfolio_summary_weighted_belief_agreement ?? "unknown"}
Portfolio Weighted Belief Polarization: ${decision.portfolio_summary_weighted_belief_polarization ?? "unknown"}
Portfolio Weighted Persistence Pct: ${decision.portfolio_summary_weighted_persistence_pct ?? "unknown"}
Portfolio Weighted Regime Risk Pct: ${decision.portfolio_summary_weighted_regime_risk_pct ?? "unknown"}
Portfolio Weighted Web Neural Score: ${decision.portfolio_summary_weighted_web_neural_score ?? "unknown"}
Portfolio Weighted Web Neural Confidence: ${decision.portfolio_summary_weighted_web_neural_confidence ?? "unknown"}
Portfolio Summary Reddit Small Cap Heat Score: ${decision.portfolio_summary_reddit_small_cap_heat_score ?? "unknown"}
Portfolio Summary Reddit Small Cap Regime: ${decision.portfolio_summary_reddit_small_cap_regime ?? "unknown"}
Portfolio Summary Korean Surge Heat Score: ${decision.portfolio_summary_fmkorea_stock_heat_score ?? "unknown"}
Portfolio Summary Korean Surge Regime: ${decision.portfolio_summary_fmkorea_stock_regime ?? "unknown"}
Portfolio Weighted Korean Surge Score: ${decision.portfolio_summary_weighted_fmkorea_surge_score ?? "unknown"}
Portfolio Weighted Small Cap Tail Score: ${decision.portfolio_summary_weighted_small_cap_tail_score ?? "unknown"}
Portfolio Weighted Heavy Tail Score: ${decision.portfolio_summary_weighted_heavy_tail_score ?? "unknown"}
Portfolio Weighted Heavy Tail Premium: ${decision.portfolio_summary_weighted_heavy_tail_premium ?? "unknown"}
Portfolio Weighted Long Tail Score: ${decision.portfolio_summary_weighted_long_tail_score ?? "unknown"}
Portfolio Weighted Left Tail Risk Score: ${decision.portfolio_summary_weighted_left_tail_risk_score ?? "unknown"}
Portfolio Average Predicted Correlation: ${decision.portfolio_summary_average_predicted_correlation ?? "unknown"}
Portfolio Average Absolute Correlation: ${decision.portfolio_summary_average_absolute_correlation ?? "unknown"}
Portfolio Diversification Score: ${decision.portfolio_summary_diversification_score ?? "unknown"}
Portfolio Crowded Pair Risk Score: ${decision.portfolio_summary_crowded_pair_risk_score ?? "unknown"}
Portfolio Correlation Risk Label: ${decision.portfolio_summary_correlation_risk_label ?? "unknown"}
Portfolio Top Crowded Pairs: ${decision.portfolio_summary_top_crowded_pairs ?? "unknown"}
Portfolio Reddit Small Cap Source: ${decision.portfolio_reddit_small_cap_source ?? "unknown"}
Portfolio Reddit Small Cap Subreddit: ${decision.portfolio_reddit_small_cap_subreddit ?? "unknown"}
Portfolio Reddit Small Cap Heat Score: ${decision.portfolio_reddit_small_cap_heat_score ?? "unknown"}
Portfolio Reddit Small Cap Regime: ${decision.portfolio_reddit_small_cap_regime ?? "unknown"}
Portfolio Reddit Small Cap Posts Analyzed: ${decision.portfolio_reddit_small_cap_posts_analyzed ?? "unknown"}
Portfolio Reddit Small Cap Top Tickers: ${decision.portfolio_reddit_small_cap_top_tickers ?? "unknown"}
Portfolio Reddit Small Cap Top Themes: ${decision.portfolio_reddit_small_cap_top_themes ?? "unknown"}
Portfolio Korean Surge Source: ${decision.portfolio_korean_surge_source ?? "unknown"}
Portfolio Korean Surge Board: ${decision.portfolio_korean_surge_board ?? "unknown"}
Portfolio Korean Surge Heat Score: ${decision.portfolio_korean_surge_heat_score ?? "unknown"}
Portfolio Korean Surge Regime: ${decision.portfolio_korean_surge_regime ?? "unknown"}
Portfolio Korean Surge Posts Analyzed: ${decision.portfolio_korean_surge_posts_analyzed ?? "unknown"}
Portfolio Korean Surge Top Tickers: ${decision.portfolio_korean_surge_top_tickers ?? "unknown"}
Portfolio Korean Surge Top Keywords: ${decision.portfolio_korean_surge_top_keywords ?? "unknown"}
Portfolio Korean Surge Top Themes: ${decision.portfolio_korean_surge_top_themes ?? "unknown"}
Portfolio Methodology Objective: ${decision.portfolio_methodology_objective ?? "unknown"}
Portfolio Allocation Methodology: ${decision.portfolio_allocation_methodology ?? "unknown"}
Portfolio Sleeves: ${decision.portfolio_sleeves_summary ?? "unknown"}
Portfolio Sector Mix: ${decision.portfolio_sector_mix_summary ?? "unknown"}
Portfolio International Mix: ${decision.portfolio_international_mix_summary ?? "unknown"}
Portfolio Top Holdings: ${decision.portfolio_top_holdings_summary ?? "unknown"}
Portfolio Weighted Human Bias Score: ${decision.portfolio_summary_weighted_human_bias_score ?? "unknown"}
Cross-Symbol Correlation Status: ${decision.correlation_status ?? "unknown"}
Cross-Symbol Average Predicted Correlation: ${decision.correlation_average_predicted ?? "unknown"}
Cross-Symbol Median Predicted Correlation: ${decision.correlation_median_predicted ?? "unknown"}
Cross-Symbol Positive Share: ${decision.correlation_positive_share ?? "unknown"}
Cross-Symbol Inverse Share: ${decision.correlation_inverse_share ?? "unknown"}
Cross-Symbol Network Label: ${decision.correlation_network_label ?? "unknown"}
Top Correlated Peer: ${decision.correlation_top_peer_symbol ?? "unknown"}
Top Correlated Peer Value: ${decision.correlation_top_peer_value ?? "unknown"}
Top Diversifier Peer: ${decision.correlation_top_diversifier_symbol ?? "unknown"}
Top Diversifier Value: ${decision.correlation_top_diversifier_value ?? "unknown"}
Selected Geometry Alignment Score: ${decision.selected_geometry_alignment_score ?? "unknown"}
Selected Geometry KL Divergence: ${decision.selected_geometry_kl_divergence ?? "unknown"}
Selected Geometry Distance: ${decision.selected_geometry_distance ?? "unknown"}
Selected Belief Score: ${decision.selected_belief_score ?? "unknown"}
Selected Belief Label: ${decision.selected_belief_label ?? "unknown"}
Selected Belief Rationale: ${decision.selected_belief_rationale ?? "unknown"}
Selected Private Signal Pct: ${decision.selected_private_signal_pct ?? "unknown"}
Selected Crowd Belief Pct: ${decision.selected_crowd_belief_pct ?? "unknown"}
Selected Belief Agreement: ${decision.selected_belief_agreement ?? "unknown"}
Selected Belief Polarization: ${decision.selected_belief_polarization ?? "unknown"}
Selected Belief Consensus Action: ${decision.selected_belief_consensus_action ?? "unknown"}
Selected Human Bias Score: ${decision.selected_human_bias_score ?? "unknown"}
Selected Human Bias Label: ${decision.selected_human_bias_label ?? "unknown"}
Selected Human Bias Rationale: ${decision.selected_human_bias_rationale ?? "unknown"}
Selected Human Bias Short Count: ${decision.selected_human_bias_short_count ?? "unknown"}
Selected Human Bias Long Count: ${decision.selected_human_bias_long_count ?? "unknown"}
Selected Human Bias Share Pct: ${decision.selected_human_bias_share_pct ?? "unknown"}
Selected Human Bias Intensity Pct: ${decision.selected_human_bias_intensity_pct ?? "unknown"}
Selected Human Bias Trend Score: ${decision.selected_human_bias_trend_score ?? "unknown"}
Selected Web Neural Score: ${decision.selected_web_neural_score ?? "unknown"}
Selected Web Neural Confidence: ${decision.selected_web_neural_confidence ?? "unknown"}
Selected Web Neural Label: ${decision.selected_web_neural_label ?? "unknown"}
Selected Market Cap: ${decision.selected_market_cap ?? "unknown"}
Selected Market Cap Bucket: ${decision.selected_market_cap_bucket ?? "unknown"}
Selected Small Cap Tail Score: ${decision.selected_small_cap_tail_score ?? "unknown"}
Selected Heavy Tail Score: ${decision.selected_heavy_tail_score ?? "unknown"}
Selected Heavy Tail Premium: ${decision.selected_heavy_tail_premium ?? "unknown"}
Selected Long Tail Score: ${decision.selected_long_tail_score ?? "unknown"}
Selected Left Tail Risk Score: ${decision.selected_left_tail_risk_score ?? "unknown"}
Selected Heavy Tail Label: ${decision.selected_heavy_tail_label ?? "unknown"}
Selected Tail Regime Label: ${decision.selected_tail_regime_label ?? "unknown"}
Selected Tail Skewness: ${decision.selected_tail_skewness ?? "unknown"}
Selected Tail Excess Kurtosis: ${decision.selected_tail_excess_kurtosis ?? "unknown"}
Selected Heavy Tail Rationale: ${decision.selected_heavy_tail_rationale ?? "unknown"}
Selected Korean Surge Score: ${decision.selected_fmkorea_surge_score ?? "unknown"}
Selected Korean Surge Mentions: ${decision.selected_fmkorea_mention_count ?? "unknown"}
Selected Korean Surge Label: ${decision.selected_fmkorea_surge_label ?? "unknown"}
Selected Average Predicted Correlation: ${decision.selected_average_predicted_correlation ?? "unknown"}
Selected Diversification Support Score: ${decision.selected_diversification_support_score ?? "unknown"}
Selected Strongest Correlation Peer: ${decision.selected_strongest_correlation_peer ?? "unknown"}
Selected Strongest Correlation Value: ${decision.selected_strongest_correlation_value ?? "unknown"}
Selected Strongest Diversifier Peer: ${decision.selected_strongest_diversifier_peer ?? "unknown"}
Selected Strongest Diversifier Value: ${decision.selected_strongest_diversifier_value ?? "unknown"}
Selected 1st Coordinate X: ${decision.selected_first_coordinate_x ?? "unknown"}
Selected 1st Coordinate Y: ${decision.selected_first_coordinate_y ?? "unknown"}
Selected 2nd Coordinate X: ${decision.selected_second_coordinate_x ?? "unknown"}
Selected 2nd Coordinate Y: ${decision.selected_second_coordinate_y ?? "unknown"}
Selected Dark Horse Score: ${decision.selected_dark_horse_score ?? "unknown"}
Selected Dark Horse Label: ${decision.selected_dark_horse_label ?? "unknown"}
Selected Dark Horse Rank: ${decision.selected_dark_horse_rank ?? "unknown"}
Selected Dark Horse Rationale: ${decision.selected_dark_horse_rationale ?? "unknown"}
Selected Symmetry Counterpart Symbol: ${decision.selected_symmetry_counterpart_symbol ?? "unknown"}
Selected Symmetry Counterpart Action: ${decision.selected_symmetry_counterpart_action ?? "unknown"}
Selected Symmetry Counterpart Quadrant: ${decision.selected_symmetry_counterpart_quadrant ?? "unknown"}
Selected Symmetry Residual Score: ${decision.selected_symmetry_residual_score ?? "unknown"}
Selected Symmetry Quality Score: ${decision.selected_symmetry_quality_score ?? "unknown"}
Selected Symmetry Underfollowed Score: ${decision.selected_symmetry_underfollowed_score ?? "unknown"}
Website Neural Model Status: ${decision.website_neural_model_status ?? "unknown"}
Website Neural Model Updated At: ${decision.website_neural_model_updated_at ?? "unknown"}
Website Neural Model Training Rows: ${decision.website_neural_model_training_rows ?? "unknown"}
Website Neural Model Validation Rows: ${decision.website_neural_model_validation_rows ?? "unknown"}
Website Neural Model Feature Count: ${decision.website_neural_model_feature_count ?? "unknown"}
Website Neural Model Fit Mode: ${decision.website_neural_model_fit_mode ?? "unknown"}
Website Neural Model Validation MAE: ${decision.website_neural_model_validation_mae ?? "unknown"}
Website Neural Model Validation RMSE: ${decision.website_neural_model_validation_rmse ?? "unknown"}
Feature Benchmark Status: ${decision.feature_benchmark_status ?? "unknown"}
Feature Benchmark Updated At: ${decision.feature_benchmark_updated_at ?? "unknown"}
Feature Benchmark Rows: ${decision.feature_benchmark_rows ?? "unknown"}
Feature Benchmark Training Rows: ${decision.feature_benchmark_training_rows ?? "unknown"}
Feature Benchmark Validation Rows: ${decision.feature_benchmark_validation_rows ?? "unknown"}
Feature Benchmark Methods Compared: ${decision.feature_benchmark_methods_compared ?? "unknown"}
Feature Benchmark Recommended Method: ${decision.feature_benchmark_recommended_method ?? "unknown"}
Feature Benchmark Recommended Latent Dim: ${decision.feature_benchmark_recommended_latent_dim ?? "unknown"}
Feature Benchmark Recommended Validation MAE: ${decision.feature_benchmark_recommended_validation_mae ?? "unknown"}
Feature Benchmark Recommended Validation RMSE: ${decision.feature_benchmark_recommended_validation_rmse ?? "unknown"}
Feature Benchmark Summary: ${decision.feature_benchmark_summary ?? "unknown"}
Feature Benchmark Error: ${decision.feature_benchmark_error ?? "unknown"}
Agent Bagging Enabled: ${decision.agent_bagging_enabled ?? "unknown"}
Agent Bagging Action: ${decision.agent_bagging_action ?? "unknown"}
Agent Bagging Mean Action: ${decision.agent_bagging_mean_action ?? "unknown"}
Agent Bagging Mean Vote: ${decision.agent_bagging_mean_vote ?? "unknown"}
Agent Bagging Vote Std: ${decision.agent_bagging_vote_std ?? "unknown"}
Agent Bagging Base Weighted Vote: ${decision.agent_bagging_base_weighted_vote ?? "unknown"}
Agent Bagging Blended Vote: ${decision.agent_bagging_blended_vote ?? "unknown"}
Agent Bagging Stability: ${decision.agent_bagging_stability ?? "unknown"}
Agent Bagging Execution Probability: ${decision.agent_bagging_execution_probability ?? "unknown"}
Agent Bagging Buy Probability: ${decision.agent_bagging_buy_probability ?? "unknown"}
Agent Bagging Hold Probability: ${decision.agent_bagging_hold_probability ?? "unknown"}
Agent Bagging Sell Probability: ${decision.agent_bagging_sell_probability ?? "unknown"}
Agent Bagging Iterations: ${decision.agent_bagging_iterations ?? "unknown"}
Agent Bagging Sample Size: ${decision.agent_bagging_sample_size ?? "unknown"}
Regret Agent Action: ${decision.regret_agent_action ?? "unknown"}
Regret Agent Confidence: ${decision.regret_agent_confidence ?? "unknown"}
Regret Risk Score: ${decision.regret_risk_score ?? "unknown"}
Regret Bias: ${decision.regret_bias ?? "unknown"}
Buy Regret Score: ${decision.buy_regret_score ?? "unknown"}
Sell Regret Score: ${decision.sell_regret_score ?? "unknown"}
EM Agent Action: ${decision.em_agent_action ?? "unknown"}
EM Agent Confidence: ${decision.em_agent_confidence ?? "unknown"}
EM Dominant Regime: ${decision.em_dominant_regime ?? "unknown"}
EM Bull Probability: ${decision.em_bull_probability ?? "unknown"}
EM Neutral Probability: ${decision.em_neutral_probability ?? "unknown"}
EM Bear Probability: ${decision.em_bear_probability ?? "unknown"}
EM Dominant Probability: ${decision.em_dominant_probability ?? "unknown"}
EM Regime Gap: ${decision.em_regime_gap ?? "unknown"}
EM Weighted Signal Mean: ${decision.em_weighted_signal_mean ?? "unknown"}
EM Log Likelihood: ${decision.em_log_likelihood ?? "unknown"}
EM Iterations: ${decision.em_iterations ?? "unknown"}
EM Observation Count: ${decision.em_observation_count ?? "unknown"}
Minimax Agent Action: ${decision.minimax_agent_action ?? "unknown"}
Minimax Agent Confidence: ${decision.minimax_agent_confidence ?? "unknown"}
Minimax Selected Class: ${decision.minimax_selected_class ?? "unknown"}
Minimax Worst Class: ${decision.minimax_worst_class ?? "unknown"}
Minimax Adversarial Focus: ${decision.minimax_adversarial_focus ?? "unknown"}
Minimax Robust Margin: ${decision.minimax_robust_margin ?? "unknown"}
Minimax Worst Loss: ${decision.minimax_worst_loss ?? "unknown"}
Minimax Buy Loss: ${decision.minimax_buy_loss ?? "unknown"}
Minimax Hold Loss: ${decision.minimax_hold_loss ?? "unknown"}
Minimax Sell Loss: ${decision.minimax_sell_loss ?? "unknown"}
Minimax Buy Probability: ${decision.minimax_buy_probability ?? "unknown"}
Minimax Hold Probability: ${decision.minimax_hold_probability ?? "unknown"}
Minimax Sell Probability: ${decision.minimax_sell_probability ?? "unknown"}
Minimax Adversarial Prior Buy: ${decision.minimax_adversarial_prior_buy ?? "unknown"}
Minimax Adversarial Prior Hold: ${decision.minimax_adversarial_prior_hold ?? "unknown"}
Minimax Adversarial Prior Sell: ${decision.minimax_adversarial_prior_sell ?? "unknown"}
Minimax Tail Regime Label: ${decision.minimax_tail_regime_label ?? "unknown"}
Drawdown Start Timestamp: ${decision.drawdown_start_timestamp ?? "unknown"}
Drawdown Recovery Timestamp: ${decision.drawdown_recovery_timestamp ?? "unknown"}
Drawdown Recovery In Horizon: ${decision.drawdown_recovery_in_horizon ?? "unknown"}
Drawdown Trough Timestamp: ${decision.drawdown_trough_timestamp ?? "unknown"}
Drawdown Trough Price: ${decision.drawdown_trough_price ?? "unknown"}
Trough To Recovery (seconds): ${decision.trough_to_recovery_seconds ?? "unknown"}
Trough To Recovery (human): ${formatKoreanDurationWindow(decision.trough_to_recovery_seconds)}
Max Drawdown Pct: ${decision.max_drawdown_pct ?? "unknown"}
TimesFM Status: ${decision.timesfm_status ?? "unknown"}
TimesFM Used: ${decision.timesfm_used ?? "unknown"}
TimesFM Model ID: ${decision.timesfm_model_id ?? "unknown"}
TimesFM Error: ${decision.timesfm_error ?? "unknown"}
Resource MoE Profile: ${decision.moe_profile ?? "unknown"}
Resource MoE Enabled: ${decision.moe_enabled ?? "unknown"}
Resource MoE Active Experts: ${decision.moe_active_experts ?? "unknown"}
Resource MoE Skipped Experts: ${decision.moe_skipped_experts ?? "unknown"}
Resource MoE TimesFM Gate: ${decision.moe_timesfm_reason ?? "unknown"} / score ${decision.moe_timesfm_score ?? "unknown"}
Resource MoE Correlation Gate: ${decision.moe_correlation_reason ?? "unknown"} / score ${decision.moe_correlation_score ?? "unknown"}
TimesFM Spike Sustain (seconds): ${decision.timesfm_spike_sustain_seconds ?? "unknown"}
TimesFM Spike Sustain (human): ${formatKoreanDurationWindow(decision.timesfm_spike_sustain_seconds)}
TimesFM Spike Fade In Horizon: ${decision.timesfm_spike_fade_in_horizon ?? "unknown"}
TimesFM Peak To Fade (seconds): ${decision.timesfm_peak_to_fade_seconds ?? "unknown"}
TimesFM Peak To Fade (human): ${formatKoreanDurationWindow(decision.timesfm_peak_to_fade_seconds)}
TimesFM Max Spike Pct: ${decision.timesfm_max_spike_pct ?? "unknown"}
TimesFM Drawdown Linger (seconds): ${decision.timesfm_drawdown_linger_seconds ?? "unknown"}
TimesFM Drawdown Linger (human): ${formatKoreanDurationWindow(decision.timesfm_drawdown_linger_seconds)}
TimesFM Drawdown Recovery In Horizon: ${decision.timesfm_drawdown_recovery_in_horizon ?? "unknown"}
TimesFM Trough To Recovery (seconds): ${decision.timesfm_trough_to_recovery_seconds ?? "unknown"}
TimesFM Trough To Recovery (human): ${formatKoreanDurationWindow(decision.timesfm_trough_to_recovery_seconds)}
TimesFM Max Drawdown Pct: ${decision.timesfm_max_drawdown_pct ?? "unknown"}
TimesFM Quantile Band Pct: ${decision.timesfm_quantile_band_pct ?? "unknown"}
Target Timestamp: ${decision.target_timestamp ?? "unknown"}
Direction Strength: ${decision.direction_strength}
Uncertainty Ratio: ${decision.uncertainty_ratio ?? "unknown"}
Expected Return Pct: ${decision.expected_return_pct ?? "unknown"}
Max Upside Pct: ${decision.max_upside_pct ?? "unknown"}
Turnover Potential: ${turnoverPotential}
First Moment Pct Per Hour: ${decision.first_moment_pct_per_hour ?? "unknown"}
Second Moment Pct Per Hour2: ${decision.second_moment_pct_per_hour2 ?? "unknown"}
Second Moment Magnitude: ${formatSecondMomentMagnitude(decision.second_moment_pct_per_hour2)}
Seasonality Source Rule: ${decision.seasonality_source_rule ?? "unknown"}
Seasonality Headline: ${decision.seasonality_headline ?? "unknown"}
Seasonality Strongest Component: ${decision.seasonality_strongest_component ?? "unknown"}
Seasonality Weekly Summary: ${decision.seasonality_weekly_summary ?? "unknown"}
Seasonality Yearly Summary: ${decision.seasonality_yearly_summary ?? "unknown"}
Seasonality Monthly Summary: ${decision.seasonality_monthly_summary ?? "unknown"}
Seasonality Quarterly Summary: ${decision.seasonality_quarterly_summary ?? "unknown"}
Avg Uncertainty Ratio: ${decision.avg_uncertainty_ratio ?? "unknown"}
Geodesic Available: ${decision.geodesic_available ?? "unknown"}
Geodesic Label: ${decision.geodesic_label ?? "unknown"}
Geodesic Action Bias: ${decision.geodesic_action_bias ?? "unknown"}
Geodesic History Count: ${decision.geodesic_history_count ?? "unknown"}
Geodesic Path Length: ${decision.geodesic_path_length ?? "unknown"}
Geodesic Curvature: ${decision.geodesic_curvature ?? "unknown"}
Geodesic Alignment Score: ${decision.geodesic_alignment_score ?? "unknown"}
Geodesic Deviation Score: ${decision.geodesic_deviation_score ?? "unknown"}
Geodesic Continuation Score: ${decision.geodesic_continuation_score ?? "unknown"}
Geodesic Confidence: ${decision.geodesic_confidence ?? "unknown"}
Geodesic Projected 1st Coordinate X: ${decision.geodesic_projected_first_coordinate_x ?? "unknown"}
Geodesic Projected 1st Coordinate Y: ${decision.geodesic_projected_first_coordinate_y ?? "unknown"}
Geodesic Projected 2nd Coordinate X: ${decision.geodesic_projected_second_coordinate_x ?? "unknown"}
Geodesic Projected 2nd Coordinate Y: ${decision.geodesic_projected_second_coordinate_y ?? "unknown"}
Geodesic Projected 1st Coordinate Drift: ${decision.geodesic_projected_first_coordinate_drift ?? "unknown"}
Geodesic Projected 2nd Coordinate Drift: ${decision.geodesic_projected_second_coordinate_drift ?? "unknown"}
Optimal Buy Timestamp: ${decision.optimal_buy_timestamp ?? "unknown"}
Optimal Buy Price: ${decision.optimal_buy_price ?? "unknown"}
Optimal Sell Timestamp: ${decision.optimal_sell_timestamp ?? "unknown"}
Optimal Sell Price: ${decision.optimal_sell_price ?? "unknown"}
Cadence Profile: ${decision.cadence_profile ?? "unknown"}
Loss: ${decision.current_loss}
Best Route Path: ${decision.best_route_path ?? "unknown"}
Route Legs: ${decision.route_legs ?? "unknown"}
Route Price Impact Pct: ${decision.route_price_impact_pct ?? "unknown"}
Suggested Send Date: ${suggestedSendDate}
Overshoot Bias: ${overshoot.bias}
Overshoot Reach Pct: ${overshoot.reachText}
Overshoot Sustain (seconds): ${overshoot.sustainSeconds ?? "unknown"}
Overshoot Sustain (human): ${overshoot.sustainHuman}
Overshoot Full Window (seconds): ${overshoot.fullWindowSeconds ?? "unknown"}
Overshoot Full Window (human): ${overshoot.fullWindowHuman}
Overshoot Confidence: ${overshoot.confidence}
Investor Lens Leader: ${investorLensSnapshot.leader}
Buffett Lens Weight: ${lensWeight("buffett").toFixed(3)}
Druckenmiller Lens Weight: ${lensWeight("druckenmiller").toFixed(3)}
Lynch Lens Weight: ${lensWeight("lynch").toFixed(3)}
Dalio Lens Weight: ${lensWeight("dalio").toFixed(3)}
Macbook Agent Name: ${investorLensSnapshot.macbookAgent.name}
Macbook Agent Weight: ${investorLensSnapshot.macbookAgent.weight.toFixed(3)}
Macbook Agent Avg Reward: ${investorLensSnapshot.macbookAgent.avgReward.toFixed(6)}
Macbook Agent Reward Count: ${investorLensSnapshot.macbookAgent.rewardCount}
Macbook Agent Hit Count: ${investorLensSnapshot.macbookAgent.hitCount}
Macbook Agent Hit Rate: ${investorLensSnapshot.macbookAgent.hitRate.toFixed(6)}
Macbook Agent Last Reward: ${investorLensSnapshot.macbookAgent.lastReward ?? "unknown"}
Macbook Agent Last Reference Date: ${investorLensSnapshot.macbookAgent.lastReferenceDate ?? "unknown"}
Macbook Agent Last Realized Date: ${investorLensSnapshot.macbookAgent.lastRealizedDate ?? "unknown"}
Macbook Agent Last Realized Return Pct: ${investorLensSnapshot.macbookAgent.lastRealizedReturnPct ?? "unknown"}
Macbook Agent Last Coverage Ratio: ${investorLensSnapshot.macbookAgent.lastCoverageRatio ?? "unknown"}
Macbook Agent Champion Avg Reward: ${investorLensSnapshot.macbookAgent.championAvgReward ?? "unknown"}
Macbook Agent Champion Alignment Score: ${investorLensSnapshot.macbookAgent.championAlignmentScore ?? "unknown"}
Macbook Agent Champion Reward Count: ${investorLensSnapshot.macbookAgent.championRewardCount ?? "unknown"}
Macbook Agent Champion Preferred CPS: ${investorLensSnapshot.macbookAgent.championPreferredCps ?? "unknown"}
Macbook Agent Updated At: ${investorLensSnapshot.macbookAgent.updatedAt ?? "unknown"}
Spike Sustain Leader: ${investorLensSnapshot.spikeSustainAgent.leader}
Prophet Spike Model Weight: ${(
  investorLensSnapshot.spikeSustainAgent.models.find((item) => item.model === "prophet")?.weight ?? 1
).toFixed(3)}
Prophet Spike Model Avg Reward: ${(
  investorLensSnapshot.spikeSustainAgent.models.find((item) => item.model === "prophet")?.avgReward ?? 0
).toFixed(6)}
Prophet Spike Model Hit Rate: ${(
  investorLensSnapshot.spikeSustainAgent.models.find((item) => item.model === "prophet")?.hitRate ?? 0
).toFixed(6)}
TimesFM Spike Model Weight: ${(
  investorLensSnapshot.spikeSustainAgent.models.find((item) => item.model === "timesfm")?.weight ?? 1
).toFixed(3)}
TimesFM Spike Model Avg Reward: ${(
  investorLensSnapshot.spikeSustainAgent.models.find((item) => item.model === "timesfm")?.avgReward ?? 0
).toFixed(6)}
TimesFM Spike Model Hit Rate: ${(
  investorLensSnapshot.spikeSustainAgent.models.find((item) => item.model === "timesfm")?.hitRate ?? 0
).toFixed(6)}
Macro Source: ${macroBackdrop.source}
M2 Latest Date: ${macroBackdrop.m2LatestDate ?? "unknown"}
M2 Level Billions: ${macroBackdrop.m2LevelBillions ?? "unknown"}
M2 3M Change Pct: ${macroBackdrop.m2ThreeMonthPct ?? "unknown"}
M2 1Y Change Pct: ${macroBackdrop.m2YearPct ?? "unknown"}
Policy Rate Latest Date: ${macroBackdrop.policyRateLatestDate ?? "unknown"}
Policy Rate Pct: ${macroBackdrop.policyRatePct ?? "unknown"}
Policy Rate 3M Change Bps: ${macroBackdrop.policyRateThreeMonthChangeBps ?? "unknown"}
Policy Rate 1Y Change Bps: ${macroBackdrop.policyRateYearChangeBps ?? "unknown"}
Liquidity Regime: ${macroBackdrop.liquidityRegime}
Rate Regime: ${macroBackdrop.rateRegime}
Macro Summary: ${macroBackdrop.summary}
`;
};
export const runLLMPipeline = async (decision: any) => {
  const cacheKey = buildPipelineCacheKey(decision);
  const cached = llmPipelineCache.get(cacheKey);

  if (cached && cached.expiresAt > Date.now()) {
    return cached.value;
  }
  if (cached) {
    llmPipelineCache.delete(cacheKey);
  }

  const inFlight = llmPipelineInFlight.get(cacheKey);
  if (inFlight) {
    return await inFlight;
  }

  const pipelinePromise = (async () => {
    const marketMode = String(decision.market_mode ?? "crypto").toLowerCase();
    const isStockMode = marketMode === "sp500" || marketMode === "stock";
    const hideStockOnlyLlmRouteFields = marketMode === "sp500";

    const [macroBackdrop, investorLensSnapshot] = await Promise.all([
      isStockMode
        ? getMacroBackdrop()
        : Promise.resolve(DEFAULT_NON_STOCK_MACRO),
      getInvestorLensSnapshot(),
    ]);

    const data = buildInput(decision, macroBackdrop, investorLensSnapshot);
    const [buy, wait] = await Promise.all([runBuyAgent(data), runWaitAgent(data)]);

    const summaryResults = await runStringTaskBatches(
      [
        {
          key: "routeGuidance",
          enabled: !hideStockOnlyLlmRouteFields,
          fallback: "주식 직접 분석 모드에서는 route guidance 호출을 생략했습니다.",
          run: () => runRouteGuidanceAgent(data),
        },
        { key: "dynamicPortfolioView", run: () => runDynamicPortfolioViewAgent(data) },
        { key: "momentumSummary", run: () => runMomentumSummaryAgent(data) },
        { key: "seasonalitySummary", run: () => runSeasonalitySummaryAgent(data) },
        { key: "timingSummary", run: () => runTimingWindowAgent(data) },
        { key: "spikeSustainSummary", run: () => runSpikeSustainAgent(data) },
        { key: "drawdownLingerSummary", run: () => runDrawdownLingerAgent(data) },
        { key: "regretSummary", run: () => runRegretAgent(data) },
        {
          key: "nextActionDate",
          enabled: !hideStockOnlyLlmRouteFields,
          fallback: "주식 직접 분석 모드에서는 next-action date 호출을 생략했습니다.",
          run: () => runNextActionDateAgent(data),
        },
      ],
      4
    );

    const investorResults = await runStringTaskBatches(
      [
        { key: "buffettView", run: () => runBuffettViewAgent(data) },
        { key: "druckenmillerView", run: () => runDruckenmillerViewAgent(data) },
        { key: "lynchView", run: () => runLynchViewAgent(data) },
        { key: "dalioView", run: () => runDalioViewAgent(data) },
        { key: "macbookView", run: () => runMacbookViewAgent(data) },
      ],
      3
    );

    const final = await runDebate(buy, wait);

    const result = {
      buy,
      wait,
      routeGuidance: summaryResults.routeGuidance,
      dynamicPortfolioView: summaryResults.dynamicPortfolioView,
      momentumSummary: summaryResults.momentumSummary,
      seasonalitySummary: summaryResults.seasonalitySummary,
      timingSummary: summaryResults.timingSummary,
      spikeSustainSummary: summaryResults.spikeSustainSummary,
      drawdownLingerSummary: summaryResults.drawdownLingerSummary,
      regretSummary: summaryResults.regretSummary,
      nextActionDate: summaryResults.nextActionDate,
      macroBackdrop: macroBackdrop.summary,
      investorLensSnapshot,
      buffettView: investorResults.buffettView,
      druckenmillerView: investorResults.druckenmillerView,
      lynchView: investorResults.lynchView,
      dalioView: investorResults.dalioView,
      macbookView: investorResults.macbookView,
      final,
    };

    llmPipelineCache.set(cacheKey, {
      expiresAt: Date.now() + LLM_PIPELINE_CACHE_TTL_MS,
      value: result,
    });
    pruneLlmPipelineCache();

    return result;
  })();

  llmPipelineInFlight.set(cacheKey, pipelinePromise);

  try {
    return await pipelinePromise;
  } finally {
    llmPipelineInFlight.delete(cacheKey);
  }
};
