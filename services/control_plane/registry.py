"""JSON 파일 기반 MCP 서버 레지스트리 store.

후속 단계에서 동일 인터페이스로 SQLite/Postgres 백엔드로 교체 가능하도록
순수 CRUD 함수만 노출한다. 동시 쓰기 보호를 위해 파일 락을 사용한다.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:  # 패키지로 임포트될 때
    from .models import MCPServer, MCPServerCreate, MCPServerUpdate, slugify
except ImportError:  # 폴더 안에서 단독 실행될 때 (uvicorn main:app)
    from models import MCPServer, MCPServerCreate, MCPServerUpdate, slugify  # type: ignore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

# data/control_plane/registry.json  (repo 루트 기준)
_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = _ROOT / "data" / "control_plane"
REGISTRY_PATH = DATA_DIR / "registry.json"
MCP_JSON_PATH = _ROOT / ".mcp.json"

_lock = threading.Lock()


def _ensure() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not REGISTRY_PATH.exists():
        REGISTRY_PATH.write_text('{"mcp_servers": []}', encoding="utf-8")


def _load_raw() -> dict:
    _ensure()
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"mcp_servers": []}


def _save_raw(data: dict) -> None:
    _ensure()
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, REGISTRY_PATH)


def list_servers() -> list[MCPServer]:
    return [MCPServer(**s) for s in _load_raw().get("mcp_servers", [])]


def get_server(server_id: str) -> Optional[MCPServer]:
    for s in _load_raw().get("mcp_servers", []):
        if s.get("id") == server_id:
            return MCPServer(**s)
    return None


def _unique_id(desired: str, existing: set[str]) -> str:
    sid, n = desired, 2
    while sid in existing:
        sid = f"{desired}-{n}"
        n += 1
    return sid


def create_server(payload: MCPServerCreate) -> MCPServer:
    with _lock:
        data = _load_raw()
        ids = {s["id"] for s in data["mcp_servers"]}
        sid = payload.id or slugify(payload.name)
        if payload.id and payload.id in ids:
            raise ValueError(f"id '{payload.id}' 가 이미 존재합니다.")
        sid = _unique_id(sid, ids)
        server = MCPServer(id=sid, **payload.model_dump(exclude={"id"}))
        data["mcp_servers"].append(server.model_dump())
        _save_raw(data)
        return server


def update_server(server_id: str, patch: MCPServerUpdate) -> Optional[MCPServer]:
    with _lock:
        data = _load_raw()
        for i, s in enumerate(data["mcp_servers"]):
            if s.get("id") == server_id:
                current = MCPServer(**s)
                updates = patch.model_dump(exclude_unset=True)
                merged = current.model_copy(update=updates)
                merged.updated_at = _now()  # refresh ts
                data["mcp_servers"][i] = merged.model_dump()
                _save_raw(data)
                return merged
        return None


def set_status(server_id: str, status: str, checked_at: str) -> Optional[MCPServer]:
    with _lock:
        data = _load_raw()
        for i, s in enumerate(data["mcp_servers"]):
            if s.get("id") == server_id:
                s["last_status"] = status
                s["last_checked_at"] = checked_at
                data["mcp_servers"][i] = s
                _save_raw(data)
                return MCPServer(**s)
        return None


def delete_server(server_id: str) -> bool:
    with _lock:
        data = _load_raw()
        before = len(data["mcp_servers"])
        data["mcp_servers"] = [s for s in data["mcp_servers"] if s.get("id") != server_id]
        if len(data["mcp_servers"]) == before:
            return False
        _save_raw(data)
        return True


def import_mcp_json() -> int:
    """루트 .mcp.json 을 레지스트리로 임포트. 이미 있는 id는 건너뜀. 추가 개수 반환."""
    if not MCP_JSON_PATH.exists():
        return 0
    try:
        mj = json.loads(MCP_JSON_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0
    servers = mj.get("mcpServers", {})
    with _lock:
        data = _load_raw()
        ids = {s["id"] for s in data["mcp_servers"]}
        added = 0
        for name, cfg in servers.items():
            sid = slugify(name)
            if sid in ids:
                continue
            transport = "stdio" if cfg.get("command") else ("http" if cfg.get("url") else "stdio")
            server = MCPServer(
                id=sid,
                name=name,
                transport=transport,
                command=cfg.get("command"),
                args=cfg.get("args", []),
                url=cfg.get("url"),
                env={k: "secret://" + k for k in cfg.get("env", {})},
                tags=["imported"],
            )
            data["mcp_servers"].append(server.model_dump())
            ids.add(sid)
            added += 1
        if added:
            _save_raw(data)
        return added
