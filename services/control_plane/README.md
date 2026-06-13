# Control Plane API

Connector · MCP · Skill 통합 관리 백엔드 (MVP: MCP 서버 관리).
설계 문서: [`docs/control-plane-design.md`](../../docs/control-plane-design.md)

## 실행

```bash
cd services/control_plane
pip install -r requirements.txt
uvicorn main:app --reload --port 8787
```

API 문서(자동): http://127.0.0.1:8787/docs

## 주요 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/mcp/servers` | 목록 |
| POST | `/api/mcp/servers` | 등록 |
| GET/PUT/DELETE | `/api/mcp/servers/{id}` | 단건 조회/수정/삭제 |
| POST | `/api/mcp/servers/{id}/check` | 연결 점검 |
| POST | `/api/mcp/import` | 루트 `.mcp.json` 임포트 |

레지스트리는 `data/control_plane/registry.json`에 저장됩니다(gitignore 권장).
시크릿 값은 응답에서 항상 `***`로 마스킹됩니다.
