import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  CreditLedger,
  InsufficientCreditBalanceError,
} from "@/server/creditLedger";

describe("CreditLedger", () => {
  let directory: string;
  let ledger: CreditLedger;

  beforeEach(async () => {
    directory = await mkdtemp(path.join(tmpdir(), "noslip-ledger-"));
    ledger = new CreditLedger(path.join(directory, "ledger.json"));
  });

  afterEach(async () => {
    await rm(directory, { recursive: true, force: true });
  });

  it("starts a new account at zero credits", async () => {
    const account = await ledger.getAccount("test-user");
    expect(account.balance).toBe(0);
  });

  it("increases balance when credits are granted", async () => {
    const result = await ledger.applyTransaction({
      userId: "test-user",
      amount: 25,
      reason: "test_grant",
    });
    expect(result.account.balance).toBe(25);
    expect(result.transaction.amount).toBe(25);
  });

  it("rejects a debit when balance is insufficient", async () => {
    await expect(
      ledger.applyTransaction({
        userId: "test-user",
        amount: -1,
        reason: "test_debit",
      })
    ).rejects.toEqual(
      expect.objectContaining<Partial<InsufficientCreditBalanceError>>({
        required: 1,
        balance: 0,
      })
    );
  });

  it("debits an account with enough balance", async () => {
    await ledger.applyTransaction({
      userId: "test-user",
      amount: 10,
      reason: "test_grant",
    });
    const result = await ledger.applyTransaction({
      userId: "test-user",
      amount: -3,
      reason: "test_debit",
    });
    expect(result.account.balance).toBe(7);
  });
});
