"""Chat 에이전트 레지스트리 (JSON 파일 store).

MCP 레지스트리(registry.py)와 같은 패턴. 저장 키만 다르다.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from .models import ChatAgent, ChatAgentCreate, ChatAgentUpdate, slugify
except ImportError:  # 단독 실행
    from models import ChatAgent, ChatAgentCreate, ChatAgentUpdate, slugify  # type: ignore

_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = _ROOT / "data" / "control_plane"
AGENTS_PATH = DATA_DIR / "agents.json"

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not AGENTS_PATH.exists():
        AGENTS_PATH.write_text('{"agents": []}', encoding="utf-8")


def _load() -> dict:
    _ensure()
    try:
        return json.loads(AGENTS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"agents": []}


def _save(data: dict) -> None:
    _ensure()
    tmp = AGENTS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, AGENTS_PATH)


def list_agents() -> list[ChatAgent]:
    return [ChatAgent(**a) for a in _load().get("agents", [])]


def get_agent(agent_id: str) -> Optional[ChatAgent]:
    for a in _load().get("agents", []):
        if a.get("id") == agent_id:
            return ChatAgent(**a)
    return None


def _unique_id(desired: str, existing: set[str]) -> str:
    sid, n = desired, 2
    while sid in existing:
        sid, n = f"{desired}-{n}", n + 1
    return sid


def create_agent(payload: ChatAgentCreate) -> ChatAgent:
    with _lock:
        data = _load()
        ids = {a["id"] for a in data["agents"]}
        sid = payload.id or slugify(payload.name, fallback="agent")
        if payload.id and payload.id in ids:
            raise ValueError(f"id '{payload.id}' 가 이미 존재합니다.")
        sid = _unique_id(sid, ids)
        agent = ChatAgent(id=sid, **payload.model_dump(exclude={"id"}))
        data["agents"].append(agent.model_dump())
        _save(data)
        return agent


def update_agent(agent_id: str, patch: ChatAgentUpdate) -> Optional[ChatAgent]:
    with _lock:
        data = _load()
        for i, a in enumerate(data["agents"]):
            if a.get("id") == agent_id:
                merged = ChatAgent(**a).model_copy(
                    update=patch.model_dump(exclude_unset=True)
                )
                merged.updated_at = _now()
                data["agents"][i] = merged.model_dump()
                _save(data)
                return merged
        return None


def set_status(agent_id: str, status: str, checked_at: str) -> Optional[ChatAgent]:
    with _lock:
        data = _load()
        for i, a in enumerate(data["agents"]):
            if a.get("id") == agent_id:
                a["last_status"], a["last_checked_at"] = status, checked_at
                data["agents"][i] = a
                _save(data)
                return ChatAgent(**a)
        return None


def delete_agent(agent_id: str) -> bool:
    with _lock:
        data = _load()
        before = len(data["agents"])
        data["agents"] = [a for a in data["agents"] if a.get("id") != agent_id]
        if len(data["agents"]) == before:
            return False
        _save(data)
        return True
