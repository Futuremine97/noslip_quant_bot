import {
  BASE_CHAINS,
  type BaseChainName,
} from "@/lib/web3/base";

export const CREDIT_PACKAGES = {
  starter: {
    id: "starter",
    amountUsd: 5,
    creditAmount: 500,
  },
  research: {
    id: "research",
    amountUsd: 10,
    creditAmount: 1100,
  },
  team: {
    id: "team",
    amountUsd: 25,
    creditAmount: 3000,
  },
} as const;

export type CreditPackageId = keyof typeof CREDIT_PACKAGES;

export function isCreditPackageId(value: unknown): value is CreditPackageId {
  return (
    typeof value === "string" &&
    Object.prototype.hasOwnProperty.call(CREDIT_PACKAGES, value)
  );
}

export function getPaymentMode() {
  return process.env.NOSLIP_PAYMENT_MODE === "production"
    ? "production"
    : "mock";
}

export function getPaymentChainName(): BaseChainName {
  const requested =
    process.env.NOSLIP_CHAIN_MODE === "base" ? "base" : "base-sepolia";

  if (
    requested === "base" &&
    process.env.NOSLIP_ALLOW_BASE_MAINNET !== "true"
  ) {
    throw new Error(
      "Base mainnet payments are disabled. Set NOSLIP_ALLOW_BASE_MAINNET=true only after production review."
    );
  }

  return requested;
}

export function getPaymentChain() {
  return BASE_CHAINS[getPaymentChainName()];
}
