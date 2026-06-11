import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { CreditLedger } from "@/server/creditLedger";
import {
  checkPremiumAccess,
  InsufficientCreditsError,
  requireCredits,
} from "@/server/featureGate";

describe("premium feature gates", () => {
  let directory: string;
  let ledger: CreditLedger;

  beforeEach(async () => {
    directory = await mkdtemp(path.join(tmpdir(), "noslip-gate-"));
    ledger = new CreditLedger(path.join(directory, "ledger.json"));
  });

  afterEach(async () => {
    await rm(directory, { recursive: true, force: true });
  });

  it("blocks an unpaid premium feature", async () => {
    const access = await checkPremiumAccess({
      userId: "gate-user",
      feature: "personal_forecast",
      ledger,
    });
    expect(access.allowed).toBe(false);
    await expect(
      requireCredits({
        userId: "gate-user",
        feature: "personal_forecast",
        ledger,
      })
    ).rejects.toBeInstanceOf(InsufficientCreditsError);
  });

  it("allows and debits a paid premium feature", async () => {
    await ledger.applyTransaction({
      userId: "gate-user",
      amount: 5,
      reason: "test_grant",
    });
    const result = await requireCredits({
      userId: "gate-user",
      feature: "personal_forecast",
      ledger,
    });
    expect(result.required).toBe(3);
    expect(result.balance).toBe(2);
  });
});
