/**
 * NSQ SPL 토큰 민트 생성 스크립트.
 *
 * 사전: solana CLI 지갑(~/.config/solana/id.json), devnet SOL.
 * 실행: ts-node scripts/create_mint.ts
 *
 * ⚠️ 유틸리티 토큰. 메인넷 배포·공개 분배 전 법률 검토 필수.
 */
import {
  Connection,
  Keypair,
  clusterApiUrl,
  LAMPORTS_PER_SOL,
} from "@solana/web3.js";
import {
  createMint,
  getOrCreateAssociatedTokenAccount,
  mintTo,
} from "@solana/spl-token";
import { readFileSync } from "fs";
import { homedir } from "os";
import { join } from "path";

const DECIMALS = 9;
const INITIAL_SUPPLY = 100_000_000; // 초기 발행(예시) — 설계 문서 참고, 법률 검토 후 확정

function loadKeypair(): Keypair {
  const path =
    process.env.SOLANA_KEYPAIR || join(homedir(), ".config", "solana", "id.json");
  const secret = JSON.parse(readFileSync(path, "utf-8"));
  return Keypair.fromSecretKey(Uint8Array.from(secret));
}

async function main() {
  const cluster = (process.env.SOLANA_CLUSTER as "devnet" | "mainnet-beta") || "devnet";
  const connection = new Connection(clusterApiUrl(cluster), "confirmed");
  const payer = loadKeypair();

  console.log(`클러스터: ${cluster}`);
  console.log(`지급자: ${payer.publicKey.toBase58()}`);

  const bal = await connection.getBalance(payer.publicKey);
  console.log(`잔액: ${(bal / LAMPORTS_PER_SOL).toFixed(3)} SOL`);
  if (bal === 0 && cluster === "devnet") {
    console.log("잔액 0 — devnet airdrop 시도…");
    const sig = await connection.requestAirdrop(payer.publicKey, LAMPORTS_PER_SOL);
    await connection.confirmTransaction(sig, "confirmed");
  }

  // mint authority = payer (추후 멀티시그/거버넌스로 이양 권장)
  const mint = await createMint(connection, payer, payer.publicKey, payer.publicKey, DECIMALS);
  console.log(`\n✅ NSQ Mint 생성: ${mint.toBase58()}`);

  const ata = await getOrCreateAssociatedTokenAccount(connection, payer, mint, payer.publicKey);
  await mintTo(
    connection,
    payer,
    mint,
    ata.address,
    payer,
    BigInt(INITIAL_SUPPLY) * BigInt(10 ** DECIMALS),
  );
  console.log(`✅ 초기 발행 ${INITIAL_SUPPLY.toLocaleString()} NSQ → ${ata.address.toBase58()}`);
  console.log("\n다음: 운영 멀티시그로 mint authority 이양, Anchor 프로그램 initialize 호출.");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
