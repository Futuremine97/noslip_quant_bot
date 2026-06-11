import {
  CreditLedger,
  InsufficientCreditBalanceError,
  creditLedger,
  type CreditTransaction,
} from "@/server/creditLedger";

export const FEATURE_COSTS = {
  personal_forecast: 3,
  zero_shot_forecast: 10,
  premium_whale_report: 2,
  premium_signal_feed: 1,
  strategy_tournament: 5,
  api_usage: 1,
} as const;

export type PremiumFeature = keyof typeof FEATURE_COSTS;

export class InsufficientCreditsError extends Error {
  readonly code = "INSUFFICIENT_CREDITS";

  constructor(
    public readonly required: number,
    public readonly balance: number
  ) {
    super(`Insufficient credits: required ${required}, balance ${balance}`);
    this.name = "InsufficientCreditsError";
  }
}

export function isPremiumFeature(value: unknown): value is PremiumFeature {
  return (
    typeof value === "string" &&
    Object.prototype.hasOwnProperty.call(FEATURE_COSTS, value)
  );
}

export function estimateFeatureCost(feature: PremiumFeature) {
  return FEATURE_COSTS[feature];
}

export async function checkPremiumAccess(options: {
  userId: string;
  walletAddress?: string;
  feature: PremiumFeature;
  ledger?: CreditLedger;
}) {
  const required = estimateFeatureCost(options.feature);
  const balance = (
    await (options.ledger || creditLedger).getAccount(
      options.userId,
      options.walletAddress
    )
  ).balance;
  return {
    allowed: balance >= required,
    required,
    balance,
  };
}

export async function requireCredits(options: {
  userId: string;
  walletAddress?: string;
  feature: PremiumFeature;
  metadata?: Record<string, unknown>;
  ledger?: CreditLedger;
}): Promise<{
  required: number;
  balance: number;
  transaction: CreditTransaction;
}> {
  const required = estimateFeatureCost(options.feature);

  try {
    const result = await (options.ledger || creditLedger).applyTransaction({
      userId: options.userId,
      walletAddress: options.walletAddress,
      amount: -required,
      reason: `feature:${options.feature}`,
      metadata: {
        feature: options.feature,
        ...(options.metadata || {}),
      },
    });
    return {
      required,
      balance: result.account.balance,
      transaction: result.transaction,
    };
  } catch (error) {
    if (error instanceof InsufficientCreditBalanceError) {
      throw new InsufficientCreditsError(error.required, error.balance);
    }
    throw error;
  }
}
