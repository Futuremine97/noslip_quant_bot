export const TOSS_PLAN_PURCHASES = {
  pro: {
    amountKrw: 29_000,
    bonusCredits: 500,
  },
  enterprise: {
    amountKrw: 199_000,
    bonusCredits: 2_000,
  },
} as const;

export const TOSS_CREDIT_PURCHASES = {
  "200": {
    amountKrw: 2_000,
    creditAmount: 200,
  },
  "500": {
    amountKrw: 5_000,
    creditAmount: 500,
  },
  "1000": {
    amountKrw: 10_000,
    creditAmount: 1_000,
  },
} as const;

type TossPlanName = keyof typeof TOSS_PLAN_PURCHASES;
type TossCreditAmount = keyof typeof TOSS_CREDIT_PURCHASES;

export type TossPurchase =
  | {
      userId: string;
      type: "plan";
      value: TossPlanName;
      amountKrw: number;
      bonusCredits: number;
      createdAtMs: number;
    }
  | {
      userId: string;
      type: "credits";
      value: TossCreditAmount;
      amountKrw: number;
      creditAmount: number;
      createdAtMs: number;
    };

const MAX_ORDER_AGE_MS = 24 * 60 * 60 * 1_000;
const MAX_CLOCK_SKEW_MS = 5 * 60 * 1_000;

function isRecordKey<T extends object>(
  value: string,
  record: T
): value is Extract<keyof T, string> {
  return Object.prototype.hasOwnProperty.call(record, value);
}

function parseOrderId(orderId: string) {
  const parts = orderId.split("__");
  if (parts.length < 5 || parts[0] !== "user") {
    throw new Error("Invalid Toss order ID");
  }

  const createdAtRaw = parts.at(-1) || "";
  const value = parts.at(-2) || "";
  const type = parts.at(-3) || "";
  const userId = parts.slice(1, -3).join("__");

  if (
    !userId ||
    userId.length > 160 ||
    !/^[A-Za-z0-9:_.@-]+$/.test(userId)
  ) {
    throw new Error("Invalid Toss order user");
  }

  if (!/^\d{13}$/.test(createdAtRaw)) {
    throw new Error("Invalid Toss order timestamp");
  }

  return {
    userId,
    type,
    value,
    createdAtMs: Number(createdAtRaw),
  };
}

export function resolveTossPurchase(
  orderId: string,
  amount: number,
  nowMs = Date.now()
): TossPurchase {
  if (!Number.isSafeInteger(amount) || amount <= 0) {
    throw new Error("Invalid Toss payment amount");
  }

  const order = parseOrderId(orderId);
  if (
    order.createdAtMs < nowMs - MAX_ORDER_AGE_MS ||
    order.createdAtMs > nowMs + MAX_CLOCK_SKEW_MS
  ) {
    throw new Error("Expired Toss order");
  }

  if (order.type === "plan" && isRecordKey(order.value, TOSS_PLAN_PURCHASES)) {
    const plan = TOSS_PLAN_PURCHASES[order.value];
    if (amount !== plan.amountKrw) {
      throw new Error("Toss payment amount does not match the plan price");
    }
    return {
      userId: order.userId,
      type: "plan",
      value: order.value,
      amountKrw: plan.amountKrw,
      bonusCredits: plan.bonusCredits,
      createdAtMs: order.createdAtMs,
    };
  }

  if (
    order.type === "credits" &&
    isRecordKey(order.value, TOSS_CREDIT_PURCHASES)
  ) {
    const creditPackage = TOSS_CREDIT_PURCHASES[order.value];
    if (amount !== creditPackage.amountKrw) {
      throw new Error("Toss payment amount does not match the credit price");
    }
    return {
      userId: order.userId,
      type: "credits",
      value: order.value,
      amountKrw: creditPackage.amountKrw,
      creditAmount: creditPackage.creditAmount,
      createdAtMs: order.createdAtMs,
    };
  }

  throw new Error("Unsupported Toss purchase");
}
