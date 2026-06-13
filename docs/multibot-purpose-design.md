# 멀티봇 + Purpose 엔진 설계

> 두 가지를 추가한다.
> 1. **멀티봇(Squad)** — 여러 봇을 process/thread 단위로 조립·커스터마이즈해 실행.
> 2. **Purpose 엔진(`--purpose`)** — 사용자의 전략·의도를 읽고, noslip의 가용 자원을 스스로
>    파악해 **상담 → 전략 수립 → 구축 상세 가이드**를 산출. CLI·웹 양쪽 제공.
>
> 앞서 만든 채팅 에이전트(로컬 CLI)·MCP 레지스트리·Control Plane을 기반으로 한다.

---

## 1. 멀티봇 (Squad)

### 개념 계층
```
Agent (로컬 CLI 백엔드: claude/codex/antigravity)   ← 이미 구현
  └─ Bot (역할 + 페르소나/시스템 프롬프트 + 어떤 Agent로 실행할지)
        └─ Squad (여러 Bot의 조립: 순서/실행 모드)
              └─ Run (Squad 1회 실행 = Thread들의 집합)
```

- **Bot**: `{id, name, role, agent_id, system_prompt, model_hint}`
  - 같은 Agent(claude) 위에 서로 다른 역할의 Bot 여러 개를 올릴 수 있다(전략가/아키텍트/빌더 등).
- **Squad (조립)**: `{id, name, bot_ids[], mode}`
  - `mode`:
    - `pipeline` — Bot을 순서대로 실행, 직전 출력이 다음 입력 컨텍스트로 전달(조립 라인).
    - `parallel` — 모든 Bot을 **동시 실행**(각자 별도 thread에서 별도 process spawn).
    - `roundtable` — 각 Bot이 이전까지의 모든 발언을 보며 순차 발언.

### process / thread 모델
- **process 별**: Bot 1회 실행 = 백엔드가 해당 Agent CLI를 **subprocess(process)** 로 spawn.
  → 봇마다 독립 프로세스라 격리·병렬·타임아웃이 자연스럽다.
- **thread 별**: `parallel`/`roundtable` 실행 시 `ThreadPoolExecutor`로 Bot들을 **thread**에 분산,
  각 thread가 자기 process를 관리. 즉 **"thread가 process를 든다"** 구조.
- 커스터마이즈: 어떤 Bot을, 어떤 순서로, 어떤 mode로 조립할지 사용자가 자유 구성.

### Squad 실행 API
| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET/POST | `/api/bots` | Bot 목록/등록 |
| PUT/DELETE | `/api/bots/{id}` | Bot 수정/삭제 |
| GET/POST | `/api/squads` | Squad 목록/등록 |
| PUT/DELETE | `/api/squads/{id}` | Squad 수정/삭제 |
| POST | `/api/squads/{id}/run` | `{input}` → mode대로 Bot들 실행, 턴별 결과 반환 |

---

## 2. Purpose 엔진 (`--purpose`)

사용자의 한 줄~문단 분량 **전략·의도(purpose)** 를 입력받아, AI 에이전트가
noslip의 가용 자원을 근거로 실행계획을 산출한다.

### 파이프라인
```
purpose(사용자 전략·의도)
   │
   ▼  1) 리소스 디스커버리 (resource_catalog.py)
   │     • MCP 서버/도구  • 연결된 Agent  • noslip 퀀트 능력(예측/백테스트/브로커/센티먼트 등)
   ▼  2) 프롬프트 합성 (purpose_engine.py)
   │     • 역할: "noslip 가용 자원만으로 달성하는 컨설턴트"
   │     • 출력 규격 강제: ① 상담/현황진단 ② 전략 수립 ③ 구축 상세 가이드(단계·명령·리소스 매핑)
   ▼  3) 실행
   │     • 단일 Agent 모드: 지정 Agent(claude 우선)로 1회 실행
   │     • Squad 모드: 전략가→아키텍트→빌더 pipeline으로 심화
   ▼
산출물(섹션 구조 텍스트) → CLI 출력 / 웹 렌더
```

### 출력 섹션 규격
1. **상담 (Consultation)** — 사용자 의도 요약, 현 자원으로의 적합성·리스크 진단.
2. **전략 (Strategy)** — 목표를 noslip 자원에 매핑한 접근법, 우선순위.
3. **구축 가이드 (Build Guide)** — 단계별 실행 절차, 사용할 CLI 명령/MCP 도구/서비스, 검증 방법.

### 리소스 카탈로그 소스
- MCP 레지스트리(`registry.py`) → 등록된 서버·트랜스포트.
- Agent 레지스트리(`chat_registry.py`) → 사용 가능한 AI 백엔드.
- noslip 퀀트 능력: `services/trader/*.py` 스캔 + 큐레이션된 설명(Prophet 예측, 6-Agent 컨센서스,
  백테스트, 브로커 연동, 카드뉴스, 온체인/고래 알림 등).
- CLI 명령 목록(`noslip ...`).

### API
| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/purpose/resources` | 현재 가용 리소스 카탈로그 |
| POST | `/api/purpose/plan` | `{purpose, agent_id?, squad_id?, mode?}` → 섹션 산출물 |

### CLI
```bash
noslip purpose --purpose "<전략·의도>" [--agent <id>] [--squad <id>] [--json]
noslip purpose --resources          # 가용 리소스 카탈로그만 출력
noslip squad run <squad_id> --input "<...>"
```

### 웹
- `/manage/purpose` — purpose 입력, 실행, 섹션 탭(상담/전략/가이드), 사용된 리소스 표시.
- `/manage/bots` — Bot/Squad 조립(드래그 대신 체크 + 순서 + mode 선택).

---

## 3. 보안/운영
- Squad/Purpose 실행도 채팅과 동일하게 **셸 미사용 subprocess + 타임아웃 + 출력 캡**.
- parallel 동시 실행 수 제한(기본 4 thread)으로 자원 폭주 방지.
- 리소스 카탈로그는 파일명·메타만 노출, 시크릿/키 미포함.

## 4. 로드맵
| 단계 | 내용 |
|------|------|
| MVP(이번) | 카탈로그 + purpose plan(단일/Squad pipeline) + Bot/Squad CRUD + CLI + 웹 |
| 다음 | 스트리밍, Squad 실행 시 MCP 도구 자동 주입, 산출물→파일/PR 자동화 |
| 이후 | 봇 간 토론(roundtable) 합의·투표, 산출 가이드의 단계 자동 실행(에이전트 액션) |
