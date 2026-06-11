import {
  creditLedger,
  type CreditAccount,
  type CreditTransaction,
} from "@/server/creditLedger";

type CreditMutationOptions = {
  userId: string;
  walletAddress?: string;
  amount: number;
  reason: string;
  metadata?: Record<string, unknown>;
  idempotencyKey?: string;
};

export async function getCreditAccount(
  userId: string,
  walletAddress?: string
): Promise<CreditAccount> {
  return creditLedger.getAccount(userId, walletAddress);
}

export async function getCreditBalance(
  userId: string,
  walletAddress?: string
): Promise<number> {
  return (await getCreditAccount(userId, walletAddress)).balance;
}

export async function getCreditTransactions(
  userId: string,
  limit?: number
): Promise<CreditTransaction[]> {
  return creditLedger.getTransactions(userId, limit);
}

export async function grantCredits(options: CreditMutationOptions) {
  if (options.amount <= 0) {
    throw new Error("Grant amount must be positive");
  }
  return creditLedger.applyTransaction(options);
}

export async function debitCredits(options: CreditMutationOptions) {
  if (options.amount <= 0) {
    throw new Error("Debit amount must be positive");
  }
  return creditLedger.applyTransaction({
    ...options,
    amount: -options.amount,
  });
}
