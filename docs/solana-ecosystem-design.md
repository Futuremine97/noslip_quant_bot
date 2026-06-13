# NoSlip Solana 생태계 설계 (NSQ)

> noslip의 AI 생태계(에이전트·봇·스쿼드·연합·Purpose)와 토큰을 엮는 **Solana 기반** 설계.
> SPL 토큰 **NSQ**를 중심으로 4가지 유틸리티를 온체인 프로그램으로 제공한다.
>
> ⚠️ **법적 자세(필수)**: NSQ는 **유틸리티 토큰**이다. 수익·배당·이자·원금보장·트레이딩 수익
> 자동분배를 제공하지 않으며, 투자 계약/증권이 아니다. 모든 배포·공개 분배 전 **법률 검토**가
> 선행되어야 한다. 본 문서·코드는 그 자체로 투자 권유가 아니다. (기존 EVM 계약의 자세 계승)

---

## 1. 왜 Solana

- 프로젝트가 이미 Solana 자산(Jupiter 스왑, Birdeye 시세, solders)을 REST로 사용 중.
- 사용량 결제·스테이킹처럼 **소액·고빈도** 트랜잭션이 많아 저수수료·고처리량이 유리.
- 토큰은 표준 **SPL Token**으로 발행하고, 부가 로직은 **Anchor 프로그램** 하나로 묶는다.

## 2. 토큰 (SPL Mint)

| 항목 | 값(초기 제안, 조정 가능) |
|------|--------------------------|
| 이름/심볼 | NoSlip Quant Token / **NSQ** |
| decimals | 9 (Solana 관례) |
| 최대 공급 | 1,000,000,000 NSQ (mint authority를 거버넌스/소각으로 제한) |
| mint authority | 초기 운영 멀티시그 → 단계적 탈중앙화 |
| 분배(예시) | 생태계 보상 40% · 커뮤니티/에어드롭 20% · 팀(베스팅) 15% · 트레저리 15% · 유동성 10% |

> 공급/분배는 **법률·세무 검토 후** 확정. 표는 설계용 placeholder.

## 3. 온체인 프로그램: `noslip_ecosystem` (Anchor)

하나의 프로그램에 4개 유틸리티를 PDA 계정으로 구현한다.

### 3.1 계정(PDA) 설계
| 계정 | seeds | 내용 |
|------|-------|------|
| `Config` | `["config"]` | admin, nsq_mint, treasury, usage_fee_per_unit, min_stake_for_priority |
| `Stake` | `["stake", user]` | user, amount, priority_weight, last_update |
| `Reputation` | `["rep", bot_id]` | bot_id(해시), score, votes, last_update |
| `Federation` | `["fed", proposal_id]` | proposal_id(해시), members_hash, mode, status, created_squad, ts |
| `Vault` (treasury ATA) | - | NSQ 토큰 보관(사용량 결제 수취/소각 대상) |

### 3.2 인스트럭션
| 인스트럭션 | 유틸리티 | 설명 |
|------------|----------|------|
| `initialize(config)` | - | 최초 1회 설정(admin, mint, treasury, fee 파라미터) |
| `stake(amount)` | 스테이킹/우선순위 | NSQ를 Vault로 전송, `Stake.amount`·priority_weight 갱신 |
| `unstake(amount)` | 스테이킹 | 잠금 해제(쿨다운 옵션) 후 반환 |
| `pay_for_usage(units)` | AI 사용량 결제 | `units * usage_fee_per_unit` 만큼 NSQ를 트레저리로 전송 또는 **소각**, 이벤트 emit |
| `submit_reputation(bot_id, delta)` | 봇/연합 평판 | 스테이킹 가중 투표로 평판 점수 갱신(가중치 = priority_weight) |
| `register_federation(proposal_id, members_hash, mode, squad)` | 연합 레지스트리 | 승인된 연합을 온체인 기록(감사/투명성) |
| `set_federation_status(proposal_id, status)` | 연합 레지스트리 | 상태 전이 기록(approved/executed 등) |

### 3.3 우선순위 가중치
`priority_weight = f(staked_amount)` (예: sqrt 스케일로 고래 편중 완화). 오프체인 스케줄러
(컨트롤 플레인 squad_runner)가 이 값을 읽어 봇/스쿼드 실행 우선순위·동시성 한도를 정한다.

## 4. 오프체인 ↔ 온체인 연동

```
컨트롤 플레인(FastAPI)                    Solana
─────────────────────────                ─────────────────────
purpose/squad/agent 실행 요청  ──결제──▶  pay_for_usage(units)   (사용량 차감)
연합 승인(federation.decide)   ──기록──▶  register_federation(...) (감사 로그)
봇 투표 집계                    ──반영──▶  submit_reputation(...)   (평판)
스케줄러                       ◀──조회──   Stake.priority_weight    (우선순위)
```

- MVP에서는 프로그램 + TS 클라이언트 헬퍼(`lib/solana/`)를 제공하고, 컨트롤 플레인 연동 지점은
  **인터페이스만** 노출(실 결제 연결은 토큰 배포·법률 검토 후 활성화).
- 키/시크릿은 클라이언트가 환경변수/지갑에서 주입. 프로그램은 서명자 검증만.

## 5. 보안/안전

1. **권한 분리**: config 변경은 admin(멀티시그)만. 사용량/스테이킹은 사용자 서명 필요.
2. **체크드 산술**: 모든 토큰 계산 `checked_*`/`require!` 가드.
3. **재진입/오버플로우**: Anchor 계정 검증 + `has_one`/seeds 제약으로 위조 PDA 차단.
4. **소각 옵션**: 사용량 결제는 트레저리 수취 또는 소각 선택(디플레 설계는 법률 검토 후).
5. **유틸리티 한정**: 어떤 인스트럭션도 수익/배당을 분배하지 않는다.
6. **테스트넷 우선**: devnet 배포·감사 전 메인넷 금지.

## 6. 디렉터리 (신규)

```
contracts/solana/
  Anchor.toml
  Cargo.toml                      # 워크스페이스
  programs/noslip_ecosystem/
    Cargo.toml
    src/lib.rs                    # Anchor 프로그램
  tests/noslip_ecosystem.ts       # anchor mocha 테스트
  scripts/create_mint.ts          # SPL 민트 생성
  package.json / tsconfig.json
  README.md
lib/solana/
  ecosystem-client.ts             # TS 클라이언트 헬퍼(인터페이스)
```

## 7. 빌드/배포 (로컬 필요)

```bash
# 사전: Rust, Solana CLI, Anchor 설치
cd contracts/solana
yarn install          # @coral-xyz/anchor, @solana/web3.js, @solana/spl-token
anchor build
anchor test           # 로컬 검증
# devnet 배포
solana config set --url devnet
anchor deploy
# SPL 민트 생성
ts-node scripts/create_mint.ts
```

## 8. 로드맵
| 단계 | 내용 |
|------|------|
| MVP(이번) | Anchor 프로그램(4유틸) + 민트 스크립트 + 테스트 + 클라이언트 인터페이스 |
| 다음 | devnet 배포, 컨트롤 플레인 사용량 결제 실연동, 우선순위 스케줄러 반영 |
| 이후 | 거버넌스(스테이킹 투표), 멀티시그/탈중앙화, 보안 감사, 메인넷 |
