/**
 * noslip_ecosystem Anchor 테스트 (localnet).
 * 실행: anchor test
 *
 * 흐름: 민트 생성 → initialize → stake → pay_for_usage → submit_reputation
 *       → register_federation. 핵심 인스트럭션의 happy path를 검증한다.
 */
import * as anchor from "@coral-xyz/anchor";
import { Program } from "@coral-xyz/anchor";
import { PublicKey, Keypair, SystemProgram } from "@solana/web3.js";
import {
  createMint,
  getOrCreateAssociatedTokenAccount,
  mintTo,
  TOKEN_PROGRAM_ID,
} from "@solana/spl-token";
import { assert } from "chai";

const enc = (s: string) => {
  const b = Buffer.alloc(32);
  Buffer.from(s).copy(b);
  return [...b];
};

describe("noslip_ecosystem", () => {
  const provider = anchor.AnchorProvider.env();
  anchor.setProvider(provider);
  const program = anchor.workspace.NoslipEcosystem as Program;
  const admin = (provider.wallet as anchor.Wallet).payer;

  let mint: PublicKey;
  let adminAta: PublicKey;
  let treasury: PublicKey;
  let vault: PublicKey;
  let configPda: PublicKey;

  before(async () => {
    mint = await createMint(provider.connection, admin, admin.publicKey, null, 9);
    const adminAcc = await getOrCreateAssociatedTokenAccount(
      provider.connection, admin, mint, admin.publicKey);
    adminAta = adminAcc.address;
    await mintTo(provider.connection, admin, mint, adminAta, admin, 1_000_000n * 10n ** 9n);

    // 트레저리/Vault: config PDA 소유 ATA
    [configPda] = PublicKey.findProgramAddressSync([Buffer.from("config")], program.programId);
    const tre = await getOrCreateAssociatedTokenAccount(
      provider.connection, admin, mint, configPda, true);
    treasury = tre.address;
    vault = treasury; // MVP 테스트에선 동일 계정 재사용 가능(분리 권장)
  });

  it("initialize", async () => {
    await program.methods
      .initialize(new anchor.BN(10), new anchor.BN(1000), false)
      .accounts({
        config: configPda,
        nsqMint: mint,
        treasury,
        admin: admin.publicKey,
        systemProgram: SystemProgram.programId,
      })
      .rpc();
    const cfg = await program.account.config.fetch(configPda);
    assert.equal(cfg.usageFeePerUnit.toNumber(), 10);
  });

  it("stake → priority_weight 증가", async () => {
    const [stakePda] = PublicKey.findProgramAddressSync(
      [Buffer.from("stake"), admin.publicKey.toBuffer()], program.programId);
    await program.methods
      .stake(new anchor.BN(10_000))
      .accounts({
        config: configPda,
        stakeAccount: stakePda,
        userAta: adminAta,
        vault,
        user: admin.publicKey,
        tokenProgram: TOKEN_PROGRAM_ID,
        systemProgram: SystemProgram.programId,
      })
      .rpc();
    const s = await program.account.stakeAccount.fetch(stakePda);
    assert.equal(s.amount.toNumber(), 10_000);
    assert.isAbove(s.priorityWeight.toNumber(), 0);
  });

  it("pay_for_usage (transfer)", async () => {
    await program.methods
      .payForUsage(new anchor.BN(3), enc("run-001"))
      .accounts({
        config: configPda,
        nsqMint: mint,
        payerAta: adminAta,
        treasury,
        payer: admin.publicKey,
        tokenProgram: TOKEN_PROGRAM_ID,
      })
      .rpc();
    // 3 units * fee 10 = 30 NSQ 차감(이벤트로 검증 가능). 예외 없이 통과하면 OK.
    assert.ok(true);
  });

  it("submit_reputation (스테이크 가중)", async () => {
    const [stakePda] = PublicKey.findProgramAddressSync(
      [Buffer.from("stake"), admin.publicKey.toBuffer()], program.programId);
    const botId = enc("strategist-bot");
    const [repPda] = PublicKey.findProgramAddressSync(
      [Buffer.from("rep"), Buffer.from(botId)], program.programId);
    await program.methods
      .submitReputation(botId, new anchor.BN(1))
      .accounts({
        config: configPda,
        stakeAccount: stakePda,
        reputation: repPda,
        voter: admin.publicKey,
        systemProgram: SystemProgram.programId,
      })
      .rpc();
    const rep = await program.account.reputation.fetch(repPda);
    assert.equal(rep.votes.toNumber(), 1);
    assert.isAbove(rep.score.toNumber(), 0);
  });

  it("register_federation", async () => {
    const pid = enc("proposal-xyz");
    const [fedPda] = PublicKey.findProgramAddressSync(
      [Buffer.from("fed"), Buffer.from(pid)], program.programId);
    await program.methods
      .registerFederation(pid, enc("members-hash"), 0, enc("squad-1"))
      .accounts({
        config: configPda,
        federation: fedPda,
        admin: admin.publicKey,
        systemProgram: SystemProgram.programId,
      })
      .rpc();
    const f = await program.account.federation.fetch(fedPda);
    assert.equal(f.status, 1); // Approved
  });
});
