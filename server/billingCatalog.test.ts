import { describe, expect, it } from "vitest";

import { resolveTossPurchase } from "@/server/billingCatalog";

describe("Toss billing catalog", () => {
  const now = 1_800_000_000_000;

  it("resolves a fixed credit package", () => {
    expect(
      resolveTossPurchase(
        `user__wallet:0x1234__credits__500__${now}`,
        5_000,
        now
      )
    ).toMatchObject({
      userId: "wallet:0x1234",
      type: "credits",
      value: "500",
      amountKrw: 5_000,
      creditAmount: 500,
    });
  });

  it("rejects a client-controlled amount mismatch", () => {
    expect(() =>
      resolveTossPurchase(
        `user__local:browser__credits__1000__${now}`,
        100,
        now
      )
    ).toThrow("does not match");
  });

  it("rejects unsupported plans and credit packages", () => {
    expect(() =>
      resolveTossPurchase(
        `user__local:browser__plan__basic__${now}`,
        1,
        now
      )
    ).toThrow("Unsupported");
    expect(() =>
      resolveTossPurchase(
        `user__local:browser__credits__999999__${now}`,
        10_000,
        now
      )
    ).toThrow("Unsupported");
  });

  it("rejects expired and malformed orders", () => {
    expect(() =>
      resolveTossPurchase(
        `user__local:browser__credits__200__${now - 86_400_001}`,
        2_000,
        now
      )
    ).toThrow("Expired");
    expect(() =>
      resolveTossPurchase(`user__bad user__credits__200__${now}`, 2_000, now)
    ).toThrow("user");
  });
});
