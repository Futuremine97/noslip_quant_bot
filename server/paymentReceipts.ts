import { randomBytes, randomUUID } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";

import {
  CREDIT_PACKAGES,
  getPaymentChainName,
  getPaymentMode,
  type CreditPackageId,
} from "@/lib/web3/basePayment";
import type { BaseChainName } from "@/lib/web3/base";
import { CreditLedger, creditLedger } from "@/server/creditLedger";

export type PaymentIntent = {
  id: string;
  userId: string;
  walletAddress?: string;
  chain: BaseChainName;
  asset: "USDC";
  amountUsd: number;
  creditAmount: number;
  status: "pending" | "confirmed" | "failed";
  txHash?: string;
  createdAt: string;
  confirmedAt?: string;
};

type PaymentState = {
  version: 1;
  intents: Record<string, PaymentIntent>;
};

type PaymentReceiptStoreOptions = {
  filePath?: string;
  ledger?: CreditLedger;
  paymentMode?: "mock" | "production";
  chain?: BaseChainName;
};

export class PaymentReceiptStore {
  private operation: Promise<void> = Promise.resolve();
  private readonly filePath: string;
  private readonly ledger: CreditLedger;
  private readonly paymentMode: "mock" | "production";
  private readonly chain: BaseChainName;

  constructor(options: PaymentReceiptStoreOptions = {}) {
    this.filePath =
      options.filePath ||
      path.join(
        process.cwd(),
        "data",
        "runtime",
        "web3-payment-intents.json"
      );
    this.ledger = options.ledger || creditLedger;
    this.paymentMode = options.paymentMode || getPaymentMode();
    this.chain = options.chain || getPaymentChainName();
  }

  private runExclusive<T>(work: () => Promise<T>): Promise<T> {
    const result = this.operation.then(work, work);
    this.operation = result.then(
      () => undefined,
      () => undefined
    );
    return result;
  }

  private async readState(): Promise<PaymentState> {
    try {
      const raw = await readFile(this.filePath, "utf8");
      const parsed = JSON.parse(raw) as PaymentState;
      if (parsed.version !== 1 || !parsed.intents) {
        throw new Error("Unsupported payment intent format");
      }
      return parsed;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        return { version: 1, intents: {} };
      }
      throw error;
    }
  }

  private async writeState(state: PaymentState) {
    await mkdir(path.dirname(this.filePath), { recursive: true });
    const temporaryPath = `${this.filePath}.${process.pid}.${randomUUID()}.tmp`;
    await writeFile(temporaryPath, `${JSON.stringify(state, null, 2)}\n`, "utf8");
    await rename(temporaryPath, this.filePath);
  }

  async createIntent(options: {
    userId: string;
    walletAddress?: string;
    packageId: CreditPackageId;
  }): Promise<PaymentIntent> {
    return this.runExclusive(async () => {
      const creditPackage = CREDIT_PACKAGES[options.packageId];
      const now = new Date().toISOString();
      const intent: PaymentIntent = {
        id: randomUUID(),
        userId: options.userId,
        ...(options.walletAddress
          ? { walletAddress: options.walletAddress.toLowerCase() }
          : {}),
        chain: this.chain,
        asset: "USDC",
        amountUsd: creditPackage.amountUsd,
        creditAmount: creditPackage.creditAmount,
        status: "pending",
        createdAt: now,
      };
      const state = await this.readState();
      state.intents[intent.id] = intent;
      await this.writeState(state);
      return structuredClone(intent);
    });
  }

  async getIntent(intentId: string) {
    return this.runExclusive(async () => {
      const state = await this.readState();
      const intent = state.intents[intentId];
      return intent ? structuredClone(intent) : null;
    });
  }

  async confirmIntent(options: {
    intentId: string;
    userId: string;
    txHash?: string;
  }): Promise<{ intent: PaymentIntent; balance: number }> {
    return this.runExclusive(async () => {
      const state = await this.readState();
      const intent = state.intents[options.intentId];
      if (!intent || intent.userId !== options.userId) {
        throw new Error("Payment intent not found");
      }
      if (intent.status === "failed") {
        throw new Error("Payment intent has failed");
      }

      if (this.paymentMode !== "mock") {
        // TODO: Verify Base USDC contract, recipient, amount, confirmations,
        // chain ID, and tx uniqueness (or an x402 receipt) before crediting.
        throw new Error("Production USDC/x402 verification is not implemented");
      }

      const txHash =
        options.txHash ||
        `0x${randomBytes(32).toString("hex")}`;
      if (!/^0x[a-fA-F0-9]{64}$/.test(txHash)) {
        throw new Error("Invalid transaction hash");
      }

      const result = await this.ledger.applyTransaction({
        userId: intent.userId,
        walletAddress: intent.walletAddress,
        amount: intent.creditAmount,
        reason: "payment:usdc",
        metadata: {
          paymentIntentId: intent.id,
          chain: intent.chain,
          asset: intent.asset,
          amountUsd: intent.amountUsd,
          txHash,
          paymentMode: this.paymentMode,
        },
        idempotencyKey: `payment:${intent.id}`,
      });

      if (intent.status !== "confirmed") {
        intent.status = "confirmed";
        intent.txHash = txHash.toLowerCase();
        intent.confirmedAt = new Date().toISOString();
        state.intents[intent.id] = intent;
        await this.writeState(state);
      }

      return {
        intent: structuredClone(intent),
        balance: result.account.balance,
      };
    });
  }
}

export const paymentReceiptStore = new PaymentReceiptStore();
