"""Bot / Squad 레지스트리 (JSON 파일 store).

MCP/Agent 레지스트리와 같은 패턴. bots + squads를 한 파일에 보관한다.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from .models import (
        Bot, BotCreate, BotUpdate, Squad, SquadCreate, SquadUpdate, slugify,
    )
except ImportError:  # 단독 실행
    from models import (  # type: ignore
        Bot, BotCreate, BotUpdate, Squad, SquadCreate, SquadUpdate, slugify,
    )

_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = _ROOT / "data" / "control_plane"
PATH = DATA_DIR / "squads.json"

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not PATH.exists():
        PATH.write_text('{"bots": [], "squads": []}', encoding="utf-8")


def _load() -> dict:
    _ensure()
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
        d.setdefault("bots", [])
        d.setdefault("squads", [])
        return d
    except (json.JSONDecodeError, OSError):
        return {"bots": [], "squads": []}


def _save(data: dict) -> None:
    _ensure()
    tmp = PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, PATH)


def _unique_id(desired: str, existing: set[str]) -> str:
    sid, n = desired, 2
    while sid in existing:
        sid, n = f"{desired}-{n}", n + 1
    return sid


# ── Bots ──
def list_bots() -> list[Bot]:
    return [Bot(**b) for b in _load()["bots"]]


def get_bot(bot_id: str) -> Optional[Bot]:
    for b in _load()["bots"]:
        if b.get("id") == bot_id:
            return Bot(**b)
    return None


def create_bot(payload: BotCreate) -> Bot:
    with _lock:
        data = _load()
        ids = {b["id"] for b in data["bots"]}
        sid = _unique_id(payload.id or slugify(payload.name, "bot"), ids)
        if payload.id and payload.id in ids:
            raise ValueError(f"id '{payload.id}' 가 이미 존재합니다.")
        bot = Bot(id=sid, **payload.model_dump(exclude={"id"}))
        data["bots"].append(bot.model_dump())
        _save(data)
        return bot


def update_bot(bot_id: str, patch: BotUpdate) -> Optional[Bot]:
    with _lock:
        data = _load()
        for i, b in enumerate(data["bots"]):
            if b.get("id") == bot_id:
                merged = Bot(**b).model_copy(update=patch.model_dump(exclude_unset=True))
                merged.updated_at = _now()
                data["bots"][i] = merged.model_dump()
                _save(data)
                return merged
        return None


def delete_bot(bot_id: str) -> bool:
    with _lock:
        data = _load()
        before = len(data["bots"])
        data["bots"] = [b for b in data["bots"] if b.get("id") != bot_id]
        # 스쿼드에서도 제거
        for sq in data["squads"]:
            sq["bot_ids"] = [x for x in sq.get("bot_ids", []) if x != bot_id]
        if len(data["bots"]) == before:
            return False
        _save(data)
        return True


# ── Squads ──
def list_squads() -> list[Squad]:
    return [Squad(**s) for s in _load()["squads"]]


def get_squad(squad_id: str) -> Optional[Squad]:
    for s in _load()["squads"]:
        if s.get("id") == squad_id:
            return Squad(**s)
    return None


def create_squad(payload: SquadCreate) -> Squad:
    with _lock:
        data = _load()
        ids = {s["id"] for s in data["squads"]}
        sid = _unique_id(payload.id or slugify(payload.name, "squad"), ids)
        if payload.id and payload.id in ids:
            raise ValueError(f"id '{payload.id}' 가 이미 존재합니다.")
        squad = Squad(id=sid, **payload.model_dump(exclude={"id"}))
        data["squads"].append(squad.model_dump())
        _save(data)
        return squad


def update_squad(squad_id: str, patch: SquadUpdate) -> Optional[Squad]:
    with _lock:
        data = _load()
        for i, s in enumerate(data["squads"]):
            if s.get("id") == squad_id:
                merged = Squad(**s).model_copy(update=patch.model_dump(exclude_unset=True))
                merged.updated_at = _now()
                data["squads"][i] = merged.model_dump()
                _save(data)
                return merged
        return None


def delete_squad(squad_id: str) -> bool:
    with _lock:
        data = _load()
        before = len(data["squads"])
        data["squads"] = [s for s in data["squads"] if s.get("id") != squad_id]
        if len(data["squads"]) == before:
            return False
        _save(data)
        return True
