"""NoSlip Control Plane API (FastAPI).

실행:
    cd services/control_plane
    uvicorn main:app --reload --port 8787

기본 127.0.0.1 바인딩 권장(원격 노출 시 인증 추가 필요).
"""
from __future__ import annotations

import json
import os
from typing import Iterator, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

try:  # 패키지로 실행될 때
    from . import (
        chat_registry,
        companion,
        federation,
        purpose_engine,
        registry,
        resource_catalog,
        squad_registry,
        squad_runner,
        tokenization,
    )
    from .agent_runner import check_agent, run_agent, stream_run
    from .models import (
        AGENT_PRESETS,
        BotCreate,
        BotUpdate,
        ChatAgentCreate,
        ChatAgentUpdate,
        MCPServerCreate,
        MCPServerUpdate,
        SquadCreate,
        SquadUpdate,
    )
    from .mcp_probe import probe
except ImportError:  # uvicorn main:app 처럼 단독 실행될 때
    import chat_registry  # type: ignore
    import companion  # type: ignore
    import federation  # type: ignore
    import purpose_engine  # type: ignore
    import registry  # type: ignore
    import resource_catalog  # type: ignore
    import squad_registry  # type: ignore
    import squad_runner  # type: ignore
    import tokenization  # type: ignore
    from agent_runner import check_agent, run_agent, stream_run  # type: ignore
    from models import (  # type: ignore
        AGENT_PRESETS,
        BotCreate,
        BotUpdate,
        ChatAgentCreate,
        ChatAgentUpdate,
        MCPServerCreate,
        MCPServerUpdate,
        SquadCreate,
        SquadUpdate,
    )
    from mcp_probe import probe  # type: ignore

from pydantic import BaseModel

app = FastAPI(title="NoSlip Control Plane", version="0.1.0")

# 프런트(Next.js dev :3000)에서 직접 호출 허용
_origins = os.getenv("CONTROL_PLANE_CORS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _envelope(data=None, error=None):
    return {"data": data, "error": error}


@app.on_event("startup")
def _bootstrap() -> None:
    # 최초 기동 시 .mcp.json 임포트
    registry.import_mcp_json()


@app.get("/health")
def health():
    return _envelope({"status": "ok"})


# ───────────────────────────── MCP 서버 ─────────────────────────────
@app.get("/api/mcp/servers")
def list_servers():
    return _envelope([s.masked().model_dump() for s in registry.list_servers()])


@app.post("/api/mcp/servers", status_code=201)
def create_server(payload: MCPServerCreate):
    try:
        payload.validate_transport()
        server = registry.create_server(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _envelope(server.masked().model_dump())


@app.get("/api/mcp/servers/{server_id}")
def get_server(server_id: str):
    server = registry.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="서버를 찾을 수 없습니다.")
    return _envelope(server.masked().model_dump())


@app.put("/api/mcp/servers/{server_id}")
def update_server(server_id: str, patch: MCPServerUpdate):
    server = registry.update_server(server_id, patch)
    if not server:
        raise HTTPException(status_code=404, detail="서버를 찾을 수 없습니다.")
    return _envelope(server.masked().model_dump())


@app.delete("/api/mcp/servers/{server_id}")
def delete_server(server_id: str):
    if not registry.delete_server(server_id):
        raise HTTPException(status_code=404, detail="서버를 찾을 수 없습니다.")
    return _envelope({"deleted": server_id})


@app.post("/api/mcp/servers/{server_id}/check")
def check_server(server_id: str):
    server = registry.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="서버를 찾을 수 없습니다.")
    status, detail, checked_at = probe(server)
    registry.set_status(server_id, status.value, checked_at)
    return _envelope({"id": server_id, "status": status.value, "detail": detail, "checked_at": checked_at})


@app.post("/api/mcp/import")
def import_servers():
    added = registry.import_mcp_json()
    return _envelope({"imported": added})


# ───────────────────────────── Chat 에이전트 ─────────────────────────────
@app.get("/api/chat/presets")
def chat_presets():
    return _envelope(AGENT_PRESETS)


@app.get("/api/chat/agents")
def list_agents():
    return _envelope([a.model_dump() for a in chat_registry.list_agents()])


@app.post("/api/chat/agents", status_code=201)
def create_agent(payload: ChatAgentCreate):
    try:
        payload.validate_runnable()
        agent = chat_registry.create_agent(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _envelope(agent.model_dump())


@app.get("/api/chat/agents/{agent_id}")
def get_agent(agent_id: str):
    agent = chat_registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="에이전트를 찾을 수 없습니다.")
    return _envelope(agent.model_dump())


@app.put("/api/chat/agents/{agent_id}")
def update_agent(agent_id: str, patch: ChatAgentUpdate):
    agent = chat_registry.update_agent(agent_id, patch)
    if not agent:
        raise HTTPException(status_code=404, detail="에이전트를 찾을 수 없습니다.")
    return _envelope(agent.model_dump())


@app.delete("/api/chat/agents/{agent_id}")
def delete_agent(agent_id: str):
    if not chat_registry.delete_agent(agent_id):
        raise HTTPException(status_code=404, detail="에이전트를 찾을 수 없습니다.")
    return _envelope({"deleted": agent_id})


@app.post("/api/chat/agents/{agent_id}/check")
def check_chat_agent(agent_id: str):
    agent = chat_registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="에이전트를 찾을 수 없습니다.")
    status, detail, checked_at = check_agent(agent)
    chat_registry.set_status(agent_id, status.value, checked_at)
    return _envelope({"id": agent_id, "status": status.value, "detail": detail, "checked_at": checked_at})


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatSendRequest(BaseModel):
    agent_id: str
    message: str
    history: list[ChatTurn] = []


@app.post("/api/chat/send")
def chat_send(req: ChatSendRequest):
    agent = chat_registry.get_agent(req.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="에이전트를 찾을 수 없습니다.")
    if not agent.enabled:
        raise HTTPException(status_code=400, detail="비활성화된 에이전트입니다.")
    result = run_agent(
        agent,
        req.message,
        [t.model_dump() for t in req.history],
    )
    tokenization.meter("agent_run")
    return _envelope(result)


def _sse(events: Iterator[dict]) -> Iterator[str]:
    """dict 이벤트들을 SSE(text/event-stream) 형식으로 직렬화."""
    for ev in events:
        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@app.post("/api/chat/stream")
def chat_stream(req: ChatSendRequest):
    agent = chat_registry.get_agent(req.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="에이전트를 찾을 수 없습니다.")
    if not agent.enabled:
        raise HTTPException(status_code=400, detail="비활성화된 에이전트입니다.")
    history = [t.model_dump() for t in req.history]
    tokenization.meter("agent_run")
    return StreamingResponse(
        _sse(stream_run(agent, req.message, history)),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# ───────────────────────────── Bots ─────────────────────────────
@app.get("/api/bots")
def list_bots():
    return _envelope([b.model_dump() for b in squad_registry.list_bots()])


@app.post("/api/bots", status_code=201)
def create_bot(payload: BotCreate):
    try:
        bot = squad_registry.create_bot(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _envelope(bot.model_dump())


@app.put("/api/bots/{bot_id}")
def update_bot(bot_id: str, patch: BotUpdate):
    bot = squad_registry.update_bot(bot_id, patch)
    if not bot:
        raise HTTPException(status_code=404, detail="봇을 찾을 수 없습니다.")
    return _envelope(bot.model_dump())


@app.delete("/api/bots/{bot_id}")
def delete_bot(bot_id: str):
    if not squad_registry.delete_bot(bot_id):
        raise HTTPException(status_code=404, detail="봇을 찾을 수 없습니다.")
    return _envelope({"deleted": bot_id})


# ───────────────────────────── Squads ─────────────────────────────
@app.get("/api/squads")
def list_squads():
    return _envelope([s.model_dump() for s in squad_registry.list_squads()])


@app.post("/api/squads", status_code=201)
def create_squad(payload: SquadCreate):
    try:
        squad = squad_registry.create_squad(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _envelope(squad.model_dump())


@app.put("/api/squads/{squad_id}")
def update_squad(squad_id: str, patch: SquadUpdate):
    squad = squad_registry.update_squad(squad_id, patch)
    if not squad:
        raise HTTPException(status_code=404, detail="스쿼드를 찾을 수 없습니다.")
    return _envelope(squad.model_dump())


@app.delete("/api/squads/{squad_id}")
def delete_squad(squad_id: str):
    if not squad_registry.delete_squad(squad_id):
        raise HTTPException(status_code=404, detail="스쿼드를 찾을 수 없습니다.")
    return _envelope({"deleted": squad_id})


class SquadRunRequest(BaseModel):
    input: str


@app.post("/api/squads/{squad_id}/run")
def run_squad(squad_id: str, req: SquadRunRequest):
    result = squad_runner.run_squad(squad_id, req.input)
    if result.get("error") == "스쿼드를 찾을 수 없습니다.":
        raise HTTPException(status_code=404, detail=result["error"])
    tokenization.meter("squad_run_per_bot", max(1, len(result.get("turns", []))))
    return _envelope(result)


# ───────────────────────────── Purpose 엔진 ─────────────────────────────
@app.get("/api/purpose/resources")
def purpose_resources():
    return _envelope(resource_catalog.build_catalog())


class PurposeRequest(BaseModel):
    purpose: str
    agent_id: Optional[str] = None
    squad_id: Optional[str] = None


@app.post("/api/purpose/plan")
def purpose_plan(req: PurposeRequest):
    # 스쿼드 지정 시 멀티봇 pipeline으로 심화, 아니면 단일 에이전트
    if req.squad_id:
        result = squad_runner.run_squad(req.squad_id, req.purpose)
        tokenization.meter("squad_run_per_bot", max(1, len(result.get("turns", []))))
        return _envelope({"mode": "squad", **result})
    result = purpose_engine.plan(req.purpose, agent_id=req.agent_id)
    tokenization.meter("purpose_plan")
    return _envelope({"mode": "single", **result})


@app.post("/api/purpose/stream")
def purpose_stream(req: PurposeRequest):
    """단일 에이전트 Purpose 계획을 SSE 스트리밍. (squad는 배치 /plan 사용)"""
    prep = purpose_engine.prepare(req.purpose, agent_id=req.agent_id)
    if not prep["ok"]:
        raise HTTPException(status_code=400, detail=prep["error"])
    agent = prep["agent"]

    def events() -> Iterator[dict]:
        yield {"type": "meta", "agent": {"id": agent.id, "name": agent.name, "kind": agent.kind}}
        yield from stream_run(agent, prep["prompt"], [])

    tokenization.meter("purpose_plan")
    return StreamingResponse(_sse(events()), media_type="text/event-stream", headers=_SSE_HEADERS)


# ───────────────────────────── 연합 오케스트레이터 ─────────────────────────────
class ProposeRequest(BaseModel):
    goal: str
    agent_id: Optional[str] = None
    count: int = 3


class DecideRequest(BaseModel):
    decision: str  # approve | reject
    auto_run: bool = False
    input: Optional[str] = None


class FederationRunRequest(BaseModel):
    input: Optional[str] = None


@app.get("/api/federation/proposals")
def list_proposals():
    return _envelope([p.model_dump() for p in federation.list_proposals()])


@app.post("/api/federation/propose")
def federation_propose(req: ProposeRequest):
    result = federation.propose(req.goal, agent_id=req.agent_id, count=req.count)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    tokenization.meter("federation_propose")
    return _envelope(result)


@app.post("/api/federation/proposals/{pid}/poll")
def federation_poll(pid: str):
    result = federation.poll(pid)
    if not result["ok"]:
        code = 404 if result["error"] == "제안을 찾을 수 없습니다." else 400
        raise HTTPException(status_code=code, detail=result["error"])
    return _envelope(result)


@app.post("/api/federation/proposals/{pid}/decide")
def federation_decide(pid: str, req: DecideRequest):
    result = federation.decide(pid, req.decision, auto_run=req.auto_run, run_input=req.input)
    if not result["ok"]:
        code = 404 if result["error"] == "제안을 찾을 수 없습니다." else 400
        raise HTTPException(status_code=code, detail=result["error"])
    return _envelope(result)


@app.post("/api/federation/proposals/{pid}/run")
def federation_run(pid: str, req: FederationRunRequest):
    result = federation.run_approved(pid, req.input)
    if not result["ok"] and result.get("error") in ("제안을 찾을 수 없습니다.",):
        raise HTTPException(status_code=404, detail=result["error"])
    tokenization.meter("federation_run_per_bot", max(1, len(result.get("turns", []))))
    return _envelope(result)


@app.delete("/api/federation/proposals/{pid}")
def federation_delete(pid: str):
    if not federation.delete_proposal(pid):
        raise HTTPException(status_code=404, detail="제안을 찾을 수 없습니다.")
    return _envelope({"deleted": pid})


# ───────────────────────────── 동반 에이전트(역질문) ─────────────────────────────
class CompanionRequest(BaseModel):
    history: list[ChatTurn] = []
    agent_id: Optional[str] = None


@app.post("/api/companion/nudge")
def companion_nudge(req: CompanionRequest):
    result = companion.nudge([t.model_dump() for t in req.history], req.agent_id)
    if result.get("ok"):
        tokenization.meter("companion_nudge")
    return _envelope(result)


class CompanionSettings(BaseModel):
    enabled: Optional[bool] = None
    idle_seconds: Optional[int] = None
    prefer_local: Optional[bool] = None


@app.get("/api/companion/settings")
def companion_get_settings():
    return _envelope(companion.get_settings())


@app.put("/api/companion/settings")
def companion_put_settings(patch: CompanionSettings):
    return _envelope(companion.update_settings(patch.model_dump(exclude_unset=True)))


@app.get("/api/companion/log")
def companion_get_log(limit: int = 30):
    return _envelope(companion.get_log(limit))


# ───────────────────────────── 토큰화(사용량 → NSQ) ─────────────────────────────
@app.get("/api/token/config")
def token_get_config():
    return _envelope(tokenization.get_config())


class TokenConfigPatch(BaseModel):
    nsq_per_unit: Optional[float] = None
    initial_grant: Optional[float] = None
    actions: Optional[dict] = None


@app.put("/api/token/config")
def token_put_config(patch: TokenConfigPatch):
    return _envelope(tokenization.update_config(patch.model_dump(exclude_unset=True)))


@app.get("/api/token/account")
def token_account(account: str = "default", limit: int = 40):
    return _envelope(tokenization.get_account(account, limit))


@app.get("/api/token/usage")
def token_usage(account: str = "default"):
    return _envelope(tokenization.usage_summary(account))


class TokenGrant(BaseModel):
    account: str = "default"
    amount: float
    note: str = ""


@app.post("/api/token/grant")
def token_grant(req: TokenGrant):
    return _envelope(tokenization.grant(req.account, req.amount, req.note))


class TokenCharge(BaseModel):
    account: str = "default"
    action: str
    qty: int = 1


@app.post("/api/token/charge")
def token_charge(req: TokenCharge):
    return _envelope(tokenization.charge(req.account, req.action, req.qty))
