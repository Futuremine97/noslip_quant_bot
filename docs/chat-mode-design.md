# Chat 모드 설계 — 로컬 CLI 에이전트 연결

> Control Plane에 **채팅 모드**를 추가한다.
> 사용자는 로컬에 설치된 AI CLI 에이전트(Claude Code · Codex · Antigravity 등)를
> "에이전트"로 등록·연결한 뒤, 웹 채팅방에서 선택해 대화한다.
> MCP 서버 관리와 **동일한 레지스트리 패턴**을 재사용한다.

---

## 1. 개념

- **Agent** = 로컬에서 실행 가능한 AI CLI 프로세스. (MCP 서버와 형제 격 리소스)
- 채팅 메시지를 보내면 백엔드가 해당 CLI를 **non-interactive(1회성)** 모드로 spawn하여
  프롬프트를 전달하고 표준출력을 응답으로 받아 돌려준다.
- 키(secret)가 필요 없다 — 인증은 각 CLI가 로컬에서 이미 처리(로그인 상태).

## 2. 지원 에이전트(프리셋)

| 종류 | command | 기본 args | 프롬프트 전달 | 비고 |
|------|---------|-----------|---------------|------|
| `claude` | `claude` | `-p` | 마지막 인자 | Claude Code print 모드 (`claude -p "..."`) |
| `codex` | `codex` | `exec` | 마지막 인자 | OpenAI Codex 비대화 실행 (`codex exec "..."`) |
| `antigravity` | `antigravity` | (없음) | 마지막 인자 | 헤드리스 CLI 유무는 환경에 따라 다름 → 사용자 조정 가능 |
| `custom` | 사용자 지정 | 사용자 지정 | `arg` 또는 `stdin` | 임의 CLI |

> 프리셋은 **편집 가능한 기본값**일 뿐이다. 실제 명령/인자/모드는 등록 시 조정한다.

## 3. 프롬프트 전달 모드

- `arg` — 프롬프트를 argv의 **마지막 인자**로 추가. (`claude -p "<prompt>"`)
- `stdin` — 프롬프트를 프로세스 **표준입력**으로 write 후 close.

대화 맥락은 MVP에서 매 요청에 직전 히스토리를 텍스트로 합쳐 전달(간단). 세션 유지(`--continue` 등)는 로드맵.

## 4. 데이터 모델 — ChatAgent

| 필드 | 타입 | 설명 |
|------|------|------|
| `id` | slug | 고유 id |
| `name` | string | 표시 이름 |
| `kind` | enum | `claude`\|`codex`\|`antigravity`\|`custom` |
| `command` | string | 실행 파일 |
| `args` | string[] | 프리픽스 인자 |
| `prompt_mode` | enum | `arg`\|`stdin` |
| `cwd` | string? | 작업 디렉터리(기본: 저장소 루트) |
| `timeout_s` | int | 실행 타임아웃(기본 120) |
| `enabled` | bool | |
| `last_status` | enum | `unknown`\|`ok`\|`error` |

저장: `data/control_plane/agents.json` (MCP 레지스트리와 같은 store 패턴).

## 5. API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/chat/agents` | 등록된 에이전트 목록 |
| POST | `/api/chat/agents` | 등록 |
| GET/PUT/DELETE | `/api/chat/agents/{id}` | 단건 |
| POST | `/api/chat/agents/{id}/check` | CLI 설치/실행 가능 여부 점검 |
| POST | `/api/chat/send` | `{agent_id, message, history[]}` → CLI 실행 후 응답 텍스트 |

응답 봉투: `{ data, error }`. 에러 시 stderr 요약 포함.

## 6. 실행/보안 설계

1. **shell 미사용** — `subprocess.run([command, *args, prompt])` 리스트 인자로만 실행 → 셸 인젝션 차단.
2. **타임아웃** — `timeout_s` 초과 시 강제 종료, `error` 반환.
3. **출력 제한** — stdout/stderr 각 64KB 캡(폭주 방지).
4. **허용 명령** — 등록된 에이전트의 command만 실행. 임의 명령 실행 엔드포인트 없음.
5. **로컬 바인딩** — Control Plane은 `127.0.0.1` 기본. 채팅 응답에 시스템 경로 노출 주의.

## 7. UI (`app/manage/chat`)

- 상단: 연결된 에이전트 **선택 드롭다운** + "에이전트 연결" 버튼(슬라이드오버, 프리셋 선택).
- 본문: 메시지 스레드(사용자/에이전트 버블), 자동 스크롤, 실행 중 로딩.
- 하단: 입력창(Enter 전송, Shift+Enter 줄바꿈).
- 에이전트 미연결 시: 빈 상태에서 "claude/codex/antigravity 연결" CTA.

## 8. 로드맵

| 단계 | 내용 |
|------|------|
| MVP (이번) | 에이전트 등록 + 1회성 실행 + 채팅방 |
| 다음 | 스트리밍 응답(SSE), 세션 유지(`--continue`), 첨부/파일 컨텍스트 |
| 이후 | 에이전트 ↔ MCP 서버 연동(등록한 MCP를 에이전트에 주입), 멀티 에이전트 라운드테이블 |
