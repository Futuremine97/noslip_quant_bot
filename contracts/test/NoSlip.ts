import assert from "node:assert/strict";
import { describe, it } from "node:test";

import hre from "hardhat";
import { parseEther } from "viem";

const { viem } = await hre.network.create();

describe("NoSlip Web3 contracts", function () {
  it("deploys NSQToken with the expected name, symbol, supply, and cap", async function () {
    const [owner] = await viem.getWalletClients();
    const token = await viem.deployContract("NSQToken", [
      parseEther("100000000"),
      parseEther("1000000000"),
      owner.account.address,
    ]);

    assert.equal(await token.read.name(), "NoSlip Quant Token");
    assert.equal(await token.read.symbol(), "NSQ");
    assert.equal(await token.read.totalSupply(), parseEther("100000000"));
    assert.equal(await token.read.cap(), parseEther("1000000000"));
  });

  it("emits a credit purchase receipt event", async function () {
    const [owner, user] = await viem.getWalletClients();
    const receipt = await viem.deployContract("NoSlipCreditsReceipt", [
      owner.account.address,
    ]);

    await receipt.write.recordPurchase([
      user.account.address,
      500n,
      5_000_000n,
      "USDC",
    ]);
    const events = await receipt.getEvents.CreditsPurchased();

    assert.equal(events.length, 1);
    assert.equal(
      events[0].args.user?.toLowerCase(),
      user.account.address.toLowerCase()
    );
    assert.equal(events[0].args.receiptId, 1n);
    assert.equal(events[0].args.creditAmount, 500n);
    assert.equal(events[0].args.asset, "USDC");
  });

  it("mints and updates a non-transferable access pass", async function () {
    const [owner, user] = await viem.getWalletClients();
    const pass = await viem.deployContract("NoSlipAccessPass", [
      owner.account.address,
    ]);

    await pass.write.mint([user.account.address, 1]);
    assert.equal(
      (await pass.read.ownerOf([1n])).toLowerCase(),
      user.account.address.toLowerCase()
    );
    assert.equal(await pass.read.tierOf([1n]), 1);

    await pass.write.setTier([1n, 2]);
    assert.equal(await pass.read.tierOf([1n]), 2);

    await assert.rejects(
      pass.write.transferFrom(
        [user.account.address, owner.account.address, 1n],
        { account: user.account }
      )
    );
  });

  it("blocks token transfers while paused and allows them after unpause", async function () {
    const [owner, user] = await viem.getWalletClients();
    const token = await viem.deployContract("NSQToken", [
      parseEther("100"),
      parseEther("1000"),
      owner.account.address,
    ]);

    await token.write.pause();
    await assert.rejects(
      token.write.transfer([user.account.address, parseEther("1")])
    );

    await token.write.unpause();
    await token.write.transfer([user.account.address, parseEther("1")]);
    assert.equal(
      await token.read.balanceOf([user.account.address]),
      parseEther("1")
    );
  });

  it("handles NoSlipAccessPass tier prices, public purchase with refund, and withdrawal", async function () {
    const [owner, user] = await viem.getWalletClients();
    const pass = await viem.deployContract("NoSlipAccessPass", [
      owner.account.address,
    ]);
    const publicClient = await viem.getPublicClient();

    // 1. Verify default prices
    assert.equal(await pass.read.tierPrices([0]), 0n); // FREE
    assert.equal(await pass.read.tierPrices([1]), parseEther("0.01")); // PRO
    assert.equal(await pass.read.tierPrices([2]), parseEther("0.05")); // RESEARCH
    assert.equal(await pass.read.tierPrices([3]), parseEther("0.1")); // CREATOR

    // 2. Set new price as owner
    await pass.write.setTierPrice([1, parseEther("0.02")]);
    assert.equal(await pass.read.tierPrices([1]), parseEther("0.02"));

    // 3. Reject setTierPrice from non-owner
    await assert.rejects(
      pass.write.setTierPrice([1, parseEther("0.03")], { account: user.account })
    );

    // 4. Reject publicPurchasePass with insufficient payment
    await assert.rejects(
      pass.write.publicPurchasePass([1], {
        account: user.account,
        value: parseEther("0.019"),
      })
    );

    // 5. Successful publicPurchasePass with refund
    // Required: 0.02 ETH. Sent: 0.025 ETH. Refund expected: 0.005 ETH. Contract gets: 0.02 ETH.
    await pass.write.publicPurchasePass([1], {
      account: user.account,
      value: parseEther("0.025"),
    });

    // Verify tokenId 1 was minted to user and tier is PRO (1)
    assert.equal(
      (await pass.read.ownerOf([1n])).toLowerCase(),
      user.account.address.toLowerCase()
    );
    assert.equal(await pass.read.tierOf([1n]), 1);

    // Check contract balance is exactly 0.02 ETH
    assert.equal(
      await publicClient.getBalance({ address: pass.address }),
      parseEther("0.02")
    );

    // 6. Non-owner cannot withdraw
    await assert.rejects(
      pass.write.withdraw({ account: user.account })
    );

    // 7. Owner can withdraw
    await pass.write.withdraw();
    assert.equal(
      await publicClient.getBalance({ address: pass.address }),
      0n
    );
  });

  it("handles NoSlipCreditsReceipt credit prices, direct purchase with refund, and withdrawal", async function () {
    const [owner, user] = await viem.getWalletClients();
    const receipt = await viem.deployContract("NoSlipCreditsReceipt", [
      owner.account.address,
    ]);
    const publicClient = await viem.getPublicClient();

    // 1. Verify default price (2 * 10^13 wei = 0.00002 ETH)
    assert.equal(await receipt.read.creditPriceInWei(), 20_000_000_000_000n);

    // 2. Set new price as owner (3 * 10^13 wei)
    await receipt.write.setCreditPrice([30_000_000_000_000n]);
    assert.equal(await receipt.read.creditPriceInWei(), 30_000_000_000_000n);

    // 3. Reject setCreditPrice from non-owner
    await assert.rejects(
      receipt.write.setCreditPrice([40_000_000_000_000n], { account: user.account })
    );

    // 4. Reject purchaseCredits with insufficient payment
    // 100 credits * 3 * 10^13 wei = 3 * 10^15 wei (0.003 ETH). Sending 0.002 ETH.
    await assert.rejects(
      receipt.write.purchaseCredits([100n], {
        account: user.account,
        value: parseEther("0.002"),
      })
    );

    // 5. Successful purchaseCredits with refund
    // Required: 0.003 ETH. Sent: 0.005 ETH. Refund expected: 0.002 ETH. Contract gets: 0.003 ETH.
    await receipt.write.purchaseCredits([100n], {
      account: user.account,
      value: parseEther("0.005"),
    });

    // Verify CreditsPurchased event
    const events = await receipt.getEvents.CreditsPurchased();
    assert.equal(events.length, 1);
    assert.equal(
      events[0].args.user?.toLowerCase(),
      user.account.address.toLowerCase()
    );
    assert.equal(events[0].args.creditAmount, 100n);
    assert.equal(events[0].args.amountPaid, 3_000_000_000_000_000n); // 0.003 ETH
    assert.equal(events[0].args.asset, "ETH");

    // Check contract balance is exactly 0.003 ETH
    assert.equal(
      await publicClient.getBalance({ address: receipt.address }),
      3_000_000_000_000_000n
    );

    // 6. Non-owner cannot withdraw
    await assert.rejects(
      receipt.write.withdraw({ account: user.account })
    );

    // 7. Owner can withdraw
    await receipt.write.withdraw();
    assert.equal(
      await publicClient.getBalance({ address: receipt.address }),
      0n
    );
  });
});

