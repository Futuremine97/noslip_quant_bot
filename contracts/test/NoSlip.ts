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
});
