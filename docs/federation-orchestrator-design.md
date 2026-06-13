# 연합 오케스트레이터 (Federation Orchestrator) 설계

> bot들끼리 **연결해 연합 전략을 짜자고 먼저 역제안하는** 에이전트.
> 오케스트레이터가 봇 조합(연합)을 제안 → 각 후보 봇에게 참여 의향을 묻고(투표/코멘트)
> → **사람이 승인해야만** 실제 스쿼드로 확정된다. 사람의 결정이 최종 게이트.

---

## 1. 핵심 흐름

```
목표(goal) 입력 (사람) 또는 컨텍스트
        │
        ▼  ① 역제안 (Propose) — 오케스트레이터 LLM
        │     등록된 봇들의 역할/능력을 보고, 시너지 있는 "연합" 2~3개를 스스로 제안.
        │     각 연합 = {이름, 목표, 멤버 봇, 실행 모드, 근거, 기대 시너지}
        ▼  ② 봇 투표 (Poll)  — 각 후보 멤버 봇에게 제안
        │     "이 연합에 참여하겠는가? 기여/우려는?" → 봇별 stance(찬성/조건부/거절)+코멘트 수집
        ▼  ③ 사람 결정 게이트 (Decide)  ★ 최종 결정
        │     사람이 제안+봇 의견을 보고 승인/거부. (자동 확정 절대 없음)
        ▼  ④ 확정 (on approve)
              승인된 연합 → Squad로 생성(squad_registry). 이후 기존 스쿼드 실행 파이프라인 사용.
```

- **역제안(proactive)**: 사용자가 조합을 지정하지 않아도 오케스트레이터가 먼저 제안한다.
- **봇에게 제안**: 후보 멤버 봇 각각을 실제 실행해 참여 의향을 받는다(자문).
- **인간 게이트**: 제안/투표는 의견일 뿐, `approved` 전이는 오직 사람의 명시적 결정으로만.

---

## 2. 데이터 모델

### BotVote
| 필드 | 설명 |
|------|------|
| `bot_id`, `bot_name` | 투표한 봇 |
| `stance` | `agree` / `conditional` / `decline` / `unknown` |
| `comment` | 봇의 자유 코멘트(기여·우려) |

### FederationProposal
| 필드 | 설명 |
|------|------|
| `id` | 제안 id |
| `goal` | 연합이 달성할 목표 |
| `name` | 연합 이름 |
| `rationale` | 오케스트레이터의 제안 근거 |
| `member_bot_ids` | 멤버 봇 |
| `mode` | `pipeline`/`parallel`/`roundtable` (확정 시 Squad mode) |
| `expected_synergy` | 기대 시너지 |
| `status` | `proposed` → `approved`/`rejected` |
| `votes` | `BotVote[]` (폴링 결과) |
| `created_squad_id` | 승인 시 생성된 Squad id |
| `created_at`, `decided_at` | |

저장: `data/control_plane/federation.json`.

---

## 3. API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/federation/propose` | `{goal, agent_id?, count?}` → 오케스트레이터가 연합 제안 생성(status=proposed) |
| GET | `/api/federation/proposals` | 제안 목록 |
| POST | `/api/federation/proposals/{id}/poll` | 멤버 봇들에게 참여 의향 질의 → votes 채움 |
| POST | `/api/federation/proposals/{id}/decide` | `{decision: approve\|reject}` ★사람 게이트. approve 시 Squad 생성 |
| DELETE | `/api/federation/proposals/{id}` | 제안 삭제 |

응답 봉투 `{data,error}`. 제안 생성은 연결된 AI 에이전트(claude 우선) 사용.

### 오케스트레이터 출력 규격 (LLM → JSON)
```json
[
  {"name":"리서치 연합","rationale":"...","member_bots":["strategist","analyst"],
   "mode":"pipeline","expected_synergy":"..."}
]
```
파싱 실패 시 원문을 단일 제안 rationale로 보존(견고성).

---

## 4. 보안/안전 설계
- **인간 게이트 불변식**: `approved`는 `/decide`(사람 호출)로만 가능. 제안/투표는 상태를 바꾸지 않음.
- 봇 폴링·확정 실행 모두 기존 셸 미사용 subprocess + 타임아웃 + 출력 캡 재사용.
- 승인 전에는 어떤 Squad/리소스도 생성·실행하지 않음(제안은 순수 데이터).
- 멤버 봇 폴링은 동시 실행 수 제한.

## 5. CLI / 웹
```bash
noslip federation propose --goal "<목표>" [--agent <id>]
noslip federation list
noslip federation poll <id>
noslip federation approve <id>   # 사람 결정
noslip federation reject <id>
```
- 웹 `/manage/federation`: 목표 입력 → 제안 카드(멤버·근거·시너지) → "봇 의견 수집" → 봇 투표 표시
  → **승인 / 거부** 버튼(사람 게이트). 승인 시 스쿼드 생성 안내.

## 6. 로드맵
| 단계 | 내용 |
|------|------|
| MVP(이번) | 제안 + 봇 투표 + 인간 승인 → 스쿼드 확정 |
| 다음 | 승인 후 즉시 실행, 투표 기반 멤버 자동 보정, 제안 비교/랭킹 |
| 이후 | 다자 협상 라운드(봇 간 합의 도출), 연합 성과 피드백 학습 |
