# NoSlip Control Plane — 통합 관리 시스템 설계

> Connector · MCP · Skill을 한 화면에서 등록·연결·관리하는 웹앱.
> omo(오픈소스 엔터프라이즈 검색)의 "셀프서비스 커넥터 UI + API-first" 패턴을 차용하되,
> 관리 대상을 **데이터 소스(Connector)** 에서 **MCP 서버 / Skill** 까지 확장한다.
> 기존 `no-slip-saas`(Next.js 16 + FastAPI + Python 서비스) 위에 모듈로 얹는다.

---

## 1. 목표와 범위

### 1.1 한 줄 정의
사용자가 외부 데이터 소스(Connector), MCP 서버, Skill을 **셀프서비스로 등록하고, 연결 상태를 모니터링하고, 한 곳에서 통합 관리**하는 컨트롤 플레인 웹앱.

### 1.2 설계 원칙 (omo에서 차용)
1. **API-first** — 모든 관리 동작은 FastAPI 엔드포인트로 노출. UI는 그 위의 한 클라이언트일 뿐.
2. **선언적 레지스트리** — 등록 대상은 단일 레지스트리(JSON/DB)에 선언적으로 저장. 기존 `.mcp.json`과 호환.
3. **벤더 비종속** — MCP 트랜스포트(stdio/SSE/HTTP), 커넥터 종류, Skill 출처에 무관하게 동일한 추상화로 다룬다.
4. **셀프 호스팅 / 보안 우선** — 시크릿은 화면에 노출·로그·평문 저장하지 않는다(기존 `SECURITY_BROKER.md` 원칙 계승).

### 1.3 MVP 범위 (이번 단계)
- **MCP 서버 등록/관리** 모듈만 우선 구현 (선택된 우선순위).
- CRUD(추가/조회/수정/삭제) + 연결 상태 점검 + 도구(tools) 목록 조회.
- Connector / Skill 은 동일 패턴으로 후속 확장 (스키마/라우트만 미리 예약).

---

## 2. 전체 아키텍처

```
┌──────────────────────────────────────────────────────────────┐
│  Next.js 16 (App Router, Tailwind)  — 기존 app/                │
│                                                                │
│   app/manage/                ← 통합 관리 대시보드 (신규)        │
│     ├─ page.tsx              ← 개요(상태판: MCP/Connector/Skill)│
│     ├─ mcp/page.tsx          ← MCP 서버 목록·등록·상태           │
│     ├─ connectors/page.tsx   ← (예약) 데이터 소스 연결           │
│     └─ skills/page.tsx       ← (예약) Skill 관리                │
│                                                                │
│   app/api/manage/[...]/route.ts  ← BFF 프록시(인증/CORS 흡수)   │
└───────────────────────────────┬──────────────────────────────┘
                                 │ HTTP (JSON)
┌───────────────────────────────▼──────────────────────────────┐
│  Control Plane API  —  FastAPI (services/control_plane)  신규  │
│                                                                │
│   /api/mcp/servers            CRUD                             │
│   /api/mcp/servers/{id}/status  연결 점검(spawn/probe)          │
│   /api/mcp/servers/{id}/tools   도구 목록(MCP initialize)       │
│   /api/connectors/*           (예약)                           │
│   /api/skills/*               (예약)                           │
│                                                                │
│   registry.py  ←→  store (JSON: data/control_plane/registry.json│
│                            → 후속 SQLite/Postgres 전환 가능)    │
│   secrets.py   ←→  .env / OS keychain (평문 저장 금지)          │
└───────────────────────────────┬──────────────────────────────┘
                                 │ stdio / SSE / HTTP
        ┌────────────────────────┼────────────────────────┐
        ▼                        ▼                         ▼
  NoSlipQuant MCP          figma MCP (npx)          외부 MCP 서버…
  (services/trader/         pinterest MCP …          (사용자 등록)
   mcp_server.py)
```

기존 자산과의 연결:
- `.mcp.json` → 레지스트리의 **부트스트랩 소스**로 임포트(첫 기동 시 마이그레이션).
- `services/trader/mcp_server.py` → "NoSlipQuant" 항목으로 자동 등록.
- 기존 FastAPI(`prediction_api.py`)와 동일 런타임/의존성(requirements.txt) 재사용.

---

## 3. 데이터 모델

### 3.1 MCPServer (MVP 핵심)
| 필드 | 타입 | 설명 |
|------|------|------|
| `id` | string (slug) | 고유 식별자. 예: `noslipquant`, `figma` |
| `name` | string | 표시 이름 |
| `transport` | enum | `stdio` \| `sse` \| `http` |
| `command` | string? | stdio용 실행 파일 (예: `npx`, python 경로) |
| `args` | string[]? | stdio 인자 |
| `url` | string? | sse/http용 엔드포인트 |
| `env` | {key: ref}? | 환경변수. 값은 **시크릿 참조**(`secret://figma_pat`)만 저장 |
| `enabled` | bool | 활성/비활성 |
| `tags` | string[] | 분류용 |
| `created_at` / `updated_at` | ISO ts | |
| `last_status` | enum | `unknown`\|`ok`\|`error` (점검 결과 캐시) |
| `last_checked_at` | ISO ts? | |

### 3.2 후속 확장 (스키마만 예약)
- **Connector**: `id, name, kind(google_drive/notion/...), auth(oauth/api_key), status, sync_state`
- **Skill**: `id, name, source(local/url/zip), entrypoint, enabled, version`

세 엔티티는 공통 베이스(`ManagedResource`: id/name/enabled/status/timestamps)를 공유 → 대시보드 상태판을 단일 컴포넌트로 렌더.

---

## 4. API 명세 (MVP)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/api/mcp/servers` | 등록된 MCP 서버 목록 |
| `POST` | `/api/mcp/servers` | 신규 등록 (검증 후 저장) |
| `GET` | `/api/mcp/servers/{id}` | 단건 조회 |
| `PUT` | `/api/mcp/servers/{id}` | 수정 |
| `DELETE` | `/api/mcp/servers/{id}` | 삭제 |
| `POST` | `/api/mcp/servers/{id}/check` | 연결 점검(프로세스 spawn/HTTP probe → status 갱신) |
| `GET` | `/api/mcp/servers/{id}/tools` | MCP `initialize`+`tools/list` 결과 |
| `POST` | `/api/mcp/import` | `.mcp.json` 임포트 |

모든 응답은 `{ data, error }` 봉투(envelope). 시크릿 값은 절대 응답에 포함하지 않고 `"***"`로 마스킹.

---

## 5. 보안 설계

1. **시크릿 분리** — 등록 폼의 토큰/키는 레지스트리에 평문 저장하지 않고, `.env` 또는 OS keychain에 저장하고 레지스트리에는 `secret://<name>` 참조만 둔다.
2. **마스킹** — 조회·목록 응답에서 시크릿은 항상 `***`. (기존 `setup_broker.js`의 마스킹 정책과 동일)
3. **로컬 바인딩** — Control Plane API는 기본 `127.0.0.1`만 listen. 원격 노출 시 토큰 인증 필수.
4. **명령 실행 가드** — stdio MCP `command`는 허용 목록/사용자 확인을 거쳐 spawn(임의 명령 실행 방지).
5. **감사 로그** — 등록/수정/삭제/점검 이벤트를 `data/control_plane/audit.log`에 남김(시크릿 제외).

---

## 6. 단계별 로드맵

| 단계 | 내용 | 상태 |
|------|------|------|
| **0. 설계** | 본 문서 | ✅ 이번 |
| **1. MCP MVP** | 레지스트리 CRUD + 상태 점검 + 대시보드 | 🔨 이번 |
| 2. 연결 점검 고도화 | 실제 MCP `initialize`/`tools/list` 핸드셰이크, SSE/HTTP 지원 | ⏭ |
| 3. Connector | OAuth 연결(Google Drive/Notion), 동기화 상태 | ⏭ |
| 4. Skill | 업로드/활성화/실행 관리 | ⏭ |
| 5. 통합 대시보드 | 세 리소스 상태판 + 검색 + 감사 로그 뷰 | ⏭ |
| 6. 영속화/멀티테넌시 | JSON→SQLite/Postgres, 사용자별 분리 | ⏭ |

---

## 7. 디렉터리 레이아웃 (신규 추가분)

```
services/control_plane/
  __init__.py
  main.py            # FastAPI app (uvicorn 진입점)
  models.py          # pydantic 스키마
  registry.py        # JSON 레지스트리 store + .mcp.json 임포트
  mcp_probe.py       # 연결 점검(spawn/probe)
  requirements.txt   # fastapi, uvicorn, pydantic
  README.md

app/manage/
  page.tsx           # 통합 대시보드 개요
  mcp/page.tsx       # MCP 관리 화면
app/api/manage/
  mcp/route.ts             # BFF 프록시 (목록/생성)
  mcp/[id]/route.ts        # BFF 프록시 (단건/수정/삭제)

data/control_plane/
  registry.json      # 런타임 생성(gitignore)
```

### 실행
```bash
# 백엔드
cd services/control_plane && uvicorn main:app --reload --port 8787
# 프런트 (기존)
npm run dev   # http://localhost:3000/manage/mcp
```
