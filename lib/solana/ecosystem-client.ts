/**
 * NoSlip Solana 생태계 클라이언트 헬퍼 (인터페이스).
 *
 * 컨트롤 플레인/웹이 온체인 생태계 프로그램과 상호작용하기 위한 얇은 래퍼.
 * 실제 결제 연결은 토큰 devnet 배포 + 법률 검토 후 활성화한다(MVP에서는 PDA 도출/타입만 안정 제공).
 *
 * 의존성: @solana/web3.js (contracts/solana 에서 설치). 루트 빌드에는 포함되지 않는다.
 */
import { PublicKey } from "@solana/web3.js";

export const ECOSYSTEM_PROGRAM_ID = new PublicKey(
  "Nosxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
);

export const SEEDS = {
  config: "config",
  stake: "stake",
  rep: "rep",
  fed: "fed",
} as const;

/** 32바이트로 패딩한 식별자(프로그램 seed/필드용). */
export function toBytes32(s: string): Buffer {
  const b = Buffer.alloc(32);
  Buffer.from(s).copy(b);
  return b;
}

export function configPda(programId: PublicKey = ECOSYSTEM_PROGRAM_ID): PublicKey {
  return PublicKey.findProgramAddressSync([Buffer.from(SEEDS.config)], programId)[0];
}

export function stakePda(user: PublicKey, programId: PublicKey = ECOSYSTEM_PROGRAM_ID): PublicKey {
  return PublicKey.findProgramAddressSync(
    [Buffer.from(SEEDS.stake), user.toBuffer()],
    programId,
  )[0];
}

export function reputationPda(botId: string, programId: PublicKey = ECOSYSTEM_PROGRAM_ID): PublicKey {
  return PublicKey.findProgramAddressSync(
    [Buffer.from(SEEDS.rep), toBytes32(botId)],
    programId,
  )[0];
}

export function federationPda(proposalId: string, programId: PublicKey = ECOSYSTEM_PROGRAM_ID): PublicKey {
  return PublicKey.findProgramAddressSync(
    [Buffer.from(SEEDS.fed), toBytes32(proposalId)],
    programId,
  )[0];
}

export const SQUAD_MODE_CODE: Record<string, number> = {
  pipeline: 0,
  parallel: 1,
  roundtable: 2,
};

export const FEDERATION_STATUS = {
  proposed: 0,
  approved: 1,
  executed: 2,
} as const;
