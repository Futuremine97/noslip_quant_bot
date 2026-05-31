export type ExecutionRiskTier = "LOW" | "MEDIUM" | "HIGH" | "EXTREME";

export type ExecutionActionMode = "MARKET" | "TWAP" | "PRIVATE" | "HALT";

type DirectionAgentSummary = {
  action?: string;
  score?: number | null;
  uncertaintyRatio?: number | null;
  weight?: number | null;
};

type DirectionRuleSummary = {
  agents?: DirectionAgentSummary[];
};

type PerRuleSummary = {
  direction?: DirectionRuleSummary | null;
};

type WrapperSummary = {
  executionAllowed?: boolean;
};

type StepPredictionLike = {
  supported?: boolean;
  perRuleSummary?: Record<string, PerRuleSummary>;
  wrapper?: WrapperSummary | null;
};

export type ExecutionRiskAction = {
  mode: ExecutionActionMode;
  label: string;
  description: string;
  enabled: boolean;
  recommended: boolean;
  reason: string;
};

export type ExecutionRiskAssessment = {
  score: number;
  tier: ExecutionRiskTier;
  tone: "positive" | "neutral" | "negative";
  entropyScore: number;
  routeEntropyScore: number;
  actionEntropyScore: number | null;
  concentrationPenalty: number;
  whaleDominance: number;
  apparentDepthUsd: number;
  effectiveDepthUsd: number;
  effectiveDepthRatio: number;
  priceImpactScore: number;
  wrapperBlocked: boolean;
  recommendedMode: ExecutionActionMode;
  policySummary: string;
  actions: ExecutionRiskAction[];
  reasons: string[];
};

type AssessExecutionRiskInput = {
  routePlan: any[];
  priceImpactPct?: number | string | null;
  activePrediction?: StepPredictionLike | null;
  tradeNotionalUsd?: number | null;
};

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

const safeNumber = (value: unknown) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
};

const toProbabilities = (weights: number[]) => {
  const cleaned = weights.filter((weight) => Number.isFinite(weight) && weight > 0);
  if (cleaned.length === 0) {
    return [1];
  }

  const total = cleaned.reduce((sum, value) => sum + value, 0);
  if (total <= 0) {
    return cleaned.map(() => 1 / cleaned.length);
  }

  return cleaned.map((weight) => weight / total);
};

const shannonEntropy = (probabilities: number[]) =>
  probabilities.reduce((sum, probability) => {
    if (probability <= 0) {
      return sum;
    }

    return sum - probability * Math.log2(probability);
  }, 0);

const normalizeEntropy = (probabilities: number[]) => {
  if (probabilities.length <= 1) {
    return 0;
  }

  return clamp(shannonEntropy(probabilities) / Math.log2(probabilities.length), 0, 1);
};

const normalizeHhi = (probabilities: number[]) => {
  if (probabilities.length <= 1) {
    return 1;
  }

  const hhi = probabilities.reduce((sum, probability) => sum + probability ** 2, 0);
  const minimum = 1 / probabilities.length;
  return clamp((hhi - minimum) / (1 - minimum), 0, 1);
};

const extractRouteWeights = (routePlan: any[]) => {
  const weights = routePlan
    .map((leg) => {
      if (leg?.percent != null) {
        return safeNumber(leg.percent);
      }

      if (leg?.bps != null) {
        return safeNumber(leg.bps) / 100;
      }

      return 1;
    })
    .filter((weight) => weight > 0);

  return weights.length > 0 ? weights : [1];
};

const extractActionEntropy = (activePrediction?: StepPredictionLike | null) => {
  const actionBuckets = new Map<string, number>();
  const perRuleSummary = activePrediction?.perRuleSummary || {};

  Object.values(perRuleSummary).forEach((summary) => {
    summary?.direction?.agents?.forEach((agent) => {
      const action = agent?.action || "HOLD";
      const baseWeight = Math.max(0.15, safeNumber(agent?.weight) || 1);
      const confidence = Math.max(0.15, Math.abs(safeNumber(agent?.score)) || 0.25);
      const uncertaintyBoost = 1 + clamp(safeNumber(agent?.uncertaintyRatio), 0, 1.5);
      const previous = actionBuckets.get(action) || 0;
      actionBuckets.set(action, previous + baseWeight * confidence * uncertaintyBoost);
    });
  });

  if (actionBuckets.size === 0) {
    return null;
  }

  return normalizeEntropy(toProbabilities(Array.from(actionBuckets.values())));
};

const inferRiskTier = (score: number): ExecutionRiskTier => {
  if (score >= 85) {
    return "EXTREME";
  }

  if (score >= 65) {
    return "HIGH";
  }

  if (score >= 35) {
    return "MEDIUM";
  }

  return "LOW";
};

const toneFromTier = (
  tier: ExecutionRiskTier
): ExecutionRiskAssessment["tone"] => {
  if (tier === "LOW") {
    return "positive";
  }

  if (tier === "MEDIUM") {
    return "neutral";
  }

  return "negative";
};

const recommendedModeFromTier = (
  tier: ExecutionRiskTier,
  wrapperBlocked: boolean
): ExecutionActionMode => {
  if (wrapperBlocked || tier === "EXTREME") {
    return "HALT";
  }

  if (tier === "HIGH") {
    return "PRIVATE";
  }

  if (tier === "MEDIUM") {
    return "TWAP";
  }

  return "MARKET";
};

const buildPolicySummary = (
  tier: ExecutionRiskTier,
  mode: ExecutionActionMode,
  wrapperBlocked: boolean
) => {
  if (wrapperBlocked) {
    return "Wrapper guardrails are vetoing execution, so automation should halt until route quality improves.";
  }

  if (mode === "MARKET") {
    return "Entropy is controlled and effective depth is healthy enough for immediate market routing.";
  }

  if (mode === "TWAP") {
    return "Risk is elevated but manageable, so splitting the order across time reduces exposure to unstable fills.";
  }

  if (mode === "PRIVATE") {
    return "Risk is high enough that private routing is safer than broadcasting a large public order into thin flow.";
  }

  return tier === "EXTREME"
    ? "A black-swan style regime is forming, so the safest action is to halt all automated execution."
    : "Execution should remain paused until route quality recovers.";
};

export function assessExecutionRisk({
  routePlan,
  priceImpactPct,
  activePrediction,
  tradeNotionalUsd,
}: AssessExecutionRiskInput): ExecutionRiskAssessment {
  const routeProbabilities = toProbabilities(extractRouteWeights(routePlan));
  const routeEntropyScore = normalizeEntropy(routeProbabilities);
  const actionEntropyScore = extractActionEntropy(activePrediction);
  const entropyScore =
    actionEntropyScore == null
      ? routeEntropyScore
      : clamp(actionEntropyScore * 0.65 + routeEntropyScore * 0.35, 0, 1);

  const concentrationPenalty = normalizeHhi(routeProbabilities);
  const whaleDominance = clamp(Math.max(...routeProbabilities, 1 / routeProbabilities.length), 0, 1);

  const routeUsdDepth = routePlan.reduce(
    (sum, leg) => sum + Math.max(0, safeNumber(leg?.usdValue)),
    0
  );
  const apparentDepthUsd = Math.max(routeUsdDepth, safeNumber(tradeNotionalUsd), 0);
  const effectiveDepthUsd = apparentDepthUsd * (1 - concentrationPenalty);
  const notionalBase = Math.max(safeNumber(tradeNotionalUsd), apparentDepthUsd, 1);
  const effectiveDepthRatio = effectiveDepthUsd / notionalBase;
  const normalizedEffectiveDepth = clamp(Math.log1p(effectiveDepthRatio) / Math.log(2), 0, 1);

  const wrapperBlocked = activePrediction?.wrapper?.executionAllowed === false;
  const priceImpactScore = clamp(safeNumber(priceImpactPct) / 5, 0, 1);
  const depthAdjustedEntropy = entropyScore / Math.max(normalizedEffectiveDepth, 0.18);
  const normalizedDepthRisk = clamp(depthAdjustedEntropy / 3, 0, 1);

  let score =
    (normalizedDepthRisk * 0.6 +
      concentrationPenalty * 0.2 +
      priceImpactScore * 0.15 +
      (wrapperBlocked ? 0.05 : 0)) *
    100;

  if (wrapperBlocked) {
    score = Math.max(score, 82);
  }

  score = clamp(score, 0, 100);

  const tier = inferRiskTier(score);
  const recommendedMode = recommendedModeFromTier(tier, wrapperBlocked);
  const reasons: string[] = [];

  if (entropyScore >= 0.66) {
    reasons.push("Directional entropy is elevated, so the flow is information-dense and harder to execute cleanly.");
  } else if (entropyScore <= 0.33) {
    reasons.push("Directional entropy is contained, which means route-step votes are relatively aligned.");
  } else {
    reasons.push("Directional entropy is mixed, so execution quality can change quickly if flow shifts.");
  }

  if (concentrationPenalty >= 0.6) {
    reasons.push("Liquidity is concentrated in a small part of the route, so whale withdrawal could erase visible depth.");
  } else if (concentrationPenalty <= 0.25) {
    reasons.push("Route flow is distributed across legs well enough that effective depth remains resilient.");
  } else {
    reasons.push("Route concentration is moderate, so effective depth is below the headline notional.");
  }

  if (priceImpactScore >= 0.5) {
    reasons.push("Current price impact is already meaningful, which increases the chance of adverse slippage.");
  }

  if (wrapperBlocked) {
    reasons.push("Wrapper guardrails already veto execution, so the safest mode is to halt automation.");
  }

  const actions: ExecutionRiskAction[] = [
    {
      mode: "MARKET",
      label: "Market route",
      description: "Execute immediately through the current Jupiter route.",
      enabled: !wrapperBlocked && tier === "LOW",
      recommended: recommendedMode === "MARKET",
      reason: "Only appropriate when entropy is low and effective depth is healthy.",
    },
    {
      mode: "TWAP",
      label: "Split with TWAP",
      description: "Slice the order into smaller intervals to absorb uncertainty.",
      enabled: !wrapperBlocked && tier !== "EXTREME",
      recommended: recommendedMode === "TWAP",
      reason: "Best when risk is medium and the route still supports staged execution.",
    },
    {
      mode: "PRIVATE",
      label: "Private routing",
      description: "Use a private submission path to reduce signalling risk.",
      enabled: !wrapperBlocked && (tier === "MEDIUM" || tier === "HIGH"),
      recommended: recommendedMode === "PRIVATE",
      reason: "Best when visible liquidity is fragile and public signalling becomes expensive.",
    },
    {
      mode: "HALT",
      label: "Halt automation",
      description: "Pause all automated execution and wait for the regime to stabilize.",
      enabled: true,
      recommended: recommendedMode === "HALT",
      reason: "Use this when the route enters a black-swan or vetoed state.",
    },
  ];

  return {
    score,
    tier,
    tone: toneFromTier(tier),
    entropyScore,
    routeEntropyScore,
    actionEntropyScore,
    concentrationPenalty,
    whaleDominance,
    apparentDepthUsd,
    effectiveDepthUsd,
    effectiveDepthRatio,
    priceImpactScore,
    wrapperBlocked,
    recommendedMode,
    policySummary: buildPolicySummary(tier, recommendedMode, wrapperBlocked),
    actions,
    reasons,
  };
}
