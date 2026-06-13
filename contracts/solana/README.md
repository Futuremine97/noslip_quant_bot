# NoSlip Solana 생태계 (NSQ)

AI 생태계(에이전트·봇·스쿼드·연합)와 엮인 **Solana** 유틸리티 토큰 프로그램.
설계: [`docs/solana-ecosystem-design.md`](../../docs/solana-ecosystem-design.md)

> ⚠️ **유틸리티 토큰**. 수익·배당·원금보장 없음, 투자계약 아님. 메인넷 배포·공개 분배 전 **법률 검토·보안 감사 필수**. 본 코드는 투자 권유가 아니다.

## 구성
- `programs/noslip_ecosystem/src/lib.rs` — Anchor 프로그램(4 유틸리티)
  - `pay_for_usage` — AI 사용량 NSQ 결제(전송/소각)
  - `stake` / `unstake` — 스테이킹 → 우선순위 가중치(sqrt 스케일)
  - `submit_reputation` — 스테이크 가중 봇/연합 평판 투표
  - `register_federation` / `set_federation_status` — 연합 온체인 레지스트리
- `scripts/create_mint.ts` — NSQ SPL 민트 생성
- `tests/noslip_ecosystem.ts` — Anchor mocha 테스트(happy path)
- `../../lib/solana/ecosystem-client.ts` — PDA 도출 등 TS 클라이언트 헬퍼

## 사전 요구(로컬)
Rust, Solana CLI, Anchor(0.30.1), Node/Yarn. 샌드박스에는 툴체인이 없어 **로컬 빌드 필요**.

```bash
cd contracts/solana
yarn install
anchor build           # 프로그램 빌드 (program id 생성 후 lib.rs/Anchor.toml의 declare_id 교체)
anchor test            # 로컬넷 테스트
# devnet
solana config set --url devnet
anchor deploy --provider.cluster devnet
yarn mint              # NSQ 민트 생성 (devnet)
```

## program id
`declare_id!` 와 `Anchor.toml` 의 placeholder(`Nosxxxx...`)는 `anchor keys list` 로 생성한 실제 키로 교체하세요.

## 컨트롤 플레인 연동(다음 단계)
`federation.decide(approve)` → `register_federation`, 사용량 실행 → `pay_for_usage`,
스케줄러 → `Stake.priority_weight` 조회. MVP는 인터페이스만 제공하며, 실연동은 devnet 배포 후.
