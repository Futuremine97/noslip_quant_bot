import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { CreditLedger } from "@/server/creditLedger";
import { PaymentReceiptStore } from "@/server/paymentReceipts";

describe("PaymentReceiptStore", () => {
  let directory: string;
  let ledger: CreditLedger;
  let store: PaymentReceiptStore;

  beforeEach(async () => {
    directory = await mkdtemp(path.join(tmpdir(), "noslip-payments-"));
    ledger = new CreditLedger(path.join(directory, "ledger.json"));
    store = new PaymentReceiptStore({
      filePath: path.join(directory, "payments.json"),
      ledger,
      paymentMode: "mock",
      chain: "base-sepolia",
    });
  });

  afterEach(async () => {
    await rm(directory, { recursive: true, force: true });
  });

  it("creates a pending payment intent", async () => {
    const intent = await store.createIntent({
      userId: "payment-user",
      packageId: "starter",
    });
    expect(intent.status).toBe("pending");
    expect(intent.chain).toBe("base-sepolia");
    expect(intent.asset).toBe("USDC");
  });

  it("confirms a mock payment and credits the user once", async () => {
    const intent = await store.createIntent({
      userId: "payment-user",
      packageId: "starter",
    });
    const first = await store.confirmIntent({
      intentId: intent.id,
      userId: "payment-user",
    });
    const second = await store.confirmIntent({
      intentId: intent.id,
      userId: "payment-user",
    });

    expect(first.intent.status).toBe("confirmed");
    expect(first.balance).toBe(500);
    expect(second.balance).toBe(500);
  });
});
