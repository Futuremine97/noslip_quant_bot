import { randomUUID } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";

export type CreditAccount = {
  userId: string;
  walletAddress?: string;
  balance: number;
  createdAt: string;
  updatedAt: string;
};

export type CreditTransaction = {
  id: string;
  userId: string;
  walletAddress?: string;
  amount: number;
  reason: string;
  createdAt: string;
  metadata?: Record<string, unknown>;
};

type LedgerState = {
  version: 1;
  accounts: Record<string, CreditAccount>;
  transactions: CreditTransaction[];
};

export type ApplyCreditTransactionInput = {
  userId: string;
  walletAddress?: string;
  amount: number;
  reason: string;
  metadata?: Record<string, unknown>;
  idempotencyKey?: string;
};

export class InsufficientCreditBalanceError extends Error {
  constructor(
    public readonly required: number,
    public readonly balance: number
  ) {
    super(`Insufficient credits: required ${required}, balance ${balance}`);
    this.name = "InsufficientCreditBalanceError";
  }
}

const EMPTY_LEDGER: LedgerState = {
  version: 1,
  accounts: {},
  transactions: [],
};

function normalizeUserId(userId: string) {
  const normalized = String(userId || "").trim();
  if (!normalized || normalized.length > 160) {
    throw new Error("Invalid userId");
  }
  return normalized;
}

function normalizeWalletAddress(walletAddress?: string) {
  const normalized = String(walletAddress || "").trim().toLowerCase();
  if (!normalized) {
    return undefined;
  }
  if (!/^0x[a-f0-9]{40}$/.test(normalized)) {
    throw new Error("Invalid walletAddress");
  }
  return normalized;
}

export class CreditLedger {
  private operation: Promise<void> = Promise.resolve();

  constructor(
    private readonly filePath = path.join(
      process.cwd(),
      "data",
      "runtime",
      "web3-credit-ledger.json"
    )
  ) {}

  private runExclusive<T>(work: () => Promise<T>): Promise<T> {
    const result = this.operation.then(work, work);
    this.operation = result.then(
      () => undefined,
      () => undefined
    );
    return result;
  }

  private async readState(): Promise<LedgerState> {
    try {
      const raw = await readFile(this.filePath, "utf8");
      const parsed = JSON.parse(raw) as LedgerState;
      if (
        parsed.version !== 1 ||
        !parsed.accounts ||
        !Array.isArray(parsed.transactions)
      ) {
        throw new Error("Unsupported credit ledger format");
      }
      return parsed;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        return structuredClone(EMPTY_LEDGER);
      }
      throw error;
    }
  }

  private async writeState(state: LedgerState) {
    await mkdir(path.dirname(this.filePath), { recursive: true });
    const temporaryPath = `${this.filePath}.${process.pid}.${randomUUID()}.tmp`;
    await writeFile(temporaryPath, `${JSON.stringify(state, null, 2)}\n`, "utf8");
    await rename(temporaryPath, this.filePath);
  }

  async getAccount(
    userId: string,
    walletAddress?: string
  ): Promise<CreditAccount> {
    return this.runExclusive(async () => {
      const normalizedUserId = normalizeUserId(userId);
      const normalizedWallet = normalizeWalletAddress(walletAddress);
      const state = await this.readState();
      const current = state.accounts[normalizedUserId];

      if (current) {
        if (normalizedWallet && current.walletAddress !== normalizedWallet) {
          current.walletAddress = normalizedWallet;
          current.updatedAt = new Date().toISOString();
          await this.writeState(state);
        }
        return structuredClone(current);
      }

      const now = new Date().toISOString();
      const account: CreditAccount = {
        userId: normalizedUserId,
        ...(normalizedWallet ? { walletAddress: normalizedWallet } : {}),
        balance: 0,
        createdAt: now,
        updatedAt: now,
      };
      state.accounts[normalizedUserId] = account;
      await this.writeState(state);
      return structuredClone(account);
    });
  }

  async getTransactions(
    userId: string,
    limit = 100
  ): Promise<CreditTransaction[]> {
    return this.runExclusive(async () => {
      const normalizedUserId = normalizeUserId(userId);
      const state = await this.readState();
      return state.transactions
        .filter((transaction) => transaction.userId === normalizedUserId)
        .slice(-Math.max(1, Math.min(500, Math.trunc(limit))))
        .reverse()
        .map((transaction) => structuredClone(transaction));
    });
  }

  async applyTransaction(
    input: ApplyCreditTransactionInput
  ): Promise<{ account: CreditAccount; transaction: CreditTransaction }> {
    return this.runExclusive(async () => {
      const userId = normalizeUserId(input.userId);
      const walletAddress = normalizeWalletAddress(input.walletAddress);
      const amount = Number(input.amount);
      if (!Number.isSafeInteger(amount) || amount === 0) {
        throw new Error("Credit amount must be a non-zero safe integer");
      }

      const reason = String(input.reason || "").trim();
      if (!reason || reason.length > 160) {
        throw new Error("Invalid credit transaction reason");
      }

      const state = await this.readState();
      const idempotencyKey = String(input.idempotencyKey || "").trim();
      if (idempotencyKey) {
        const existing = state.transactions.find(
          (transaction) =>
            transaction.userId === userId &&
            transaction.metadata?.idempotencyKey === idempotencyKey
        );
        if (existing) {
          const account = state.accounts[userId];
          if (!account) {
            throw new Error("Ledger account missing for existing transaction");
          }
          return {
            account: structuredClone(account),
            transaction: structuredClone(existing),
          };
        }
      }

      const now = new Date().toISOString();
      const account = state.accounts[userId] || {
        userId,
        balance: 0,
        createdAt: now,
        updatedAt: now,
      };
      const nextBalance = account.balance + amount;
      if (nextBalance < 0) {
        throw new InsufficientCreditBalanceError(Math.abs(amount), account.balance);
      }

      account.balance = nextBalance;
      account.updatedAt = now;
      if (walletAddress) {
        account.walletAddress = walletAddress;
      }
      state.accounts[userId] = account;

      const metadata = {
        ...(input.metadata || {}),
        ...(idempotencyKey ? { idempotencyKey } : {}),
        balanceAfter: nextBalance,
      };
      const transaction: CreditTransaction = {
        id: randomUUID(),
        userId,
        ...(account.walletAddress ? { walletAddress: account.walletAddress } : {}),
        amount,
        reason,
        createdAt: now,
        ...(Object.keys(metadata).length ? { metadata } : {}),
      };
      state.transactions.push(transaction);
      await this.writeState(state);

      return {
        account: structuredClone(account),
        transaction: structuredClone(transaction),
      };
    });
  }
}

export const creditLedger = new CreditLedger();
