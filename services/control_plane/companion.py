"""동반 에이전트(Companion) — 사용자가 유휴 상태일 때 먼저 '역질문'을 던진다.

CLI가 일정 시간 입력이 없을 때 호출하면, 현재 맥락과 noslip 가용 자원을 바탕으로
사용자가 다음에 무엇을 하면 좋을지 묻는 짧은 역질문 1개를 생성한다.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from . import resource_catalog
    from .agent_runner import run_agent
    from .purpose_engine import _pick_agent
except ImportError:  # 단독 실행
    import resource_catalog  # type: ignore
    from agent_runner import run_agent  # type: ignore
    from purpose_engine import _pick_agent  # type: ignore

_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = _ROOT / "data" / "control_plane"
SETTINGS_PATH = DATA_DIR / "companion.json"
LOG_PATH = DATA_DIR / "companion_log.json"
_lock = threading.Lock()

DEFAULT_SETTINGS = {"enabled": True, "idle_seconds": 45, "prefer_local": True}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_settings() -> dict:
    _ensure()
    if not SETTINGS_PATH.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        d = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return {**DEFAULT_SETTINGS, **d}
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_SETTINGS)


def update_settings(patch: dict) -> dict:
    with _lock:
        cur = get_settings()
        for k in ("enabled", "idle_seconds", "prefer_local"):
            if k in patch and patch[k] is not None:
                cur[k] = patch[k]
        # 범위 보정
        cur["idle_seconds"] = max(10, min(3600, int(cur["idle_seconds"])))
        cur["enabled"] = bool(cur["enabled"])
        cur["prefer_local"] = bool(cur["prefer_local"])
        _ensure()
        SETTINGS_PATH.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        return cur


def get_log(limit: int = 30) -> list[dict]:
    if not LOG_PATH.exists():
        return []
    try:
        items = json.loads(LOG_PATH.read_text(encoding="utf-8"))
        return items[-limit:][::-1]  # 최신 우선
    except (json.JSONDecodeError, OSError):
        return []


def _append_log(entry: dict) -> None:
    with _lock:
        _ensure()
        items = []
        if LOG_PATH.exists():
            try:
                items = json.loads(LOG_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                items = []
        items.append(entry)
        items = items[-200:]  # 최대 200개 보관
        LOG_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

SYSTEM = """당신은 noslip의 '동반 에이전트'입니다.
사용자가 한동안 입력이 없을 때, 가만히 기다리지 말고 먼저 말을 거세요.
현재 맥락과 아래 가용 자원을 바탕으로, 사용자가 다음에 무엇을 하면 좋을지 묻는
'역질문'을 딱 1개만, 한국어로 1~2문장으로 짧게 던지세요.
- 구체적이어야 하고, 가능하면 실행 명령 예시(`noslip ...`)를 곁들이세요.
- 직전에 이미 한 질문과는 다른 각도로 물으세요.
- 질문 한 문장만 출력하세요. 머리말·설명·따옴표 없이."""


def nudge(history: list[dict], agent_id: Optional[str] = None) -> dict:
    settings = get_settings()
    if not settings["enabled"]:
        return {"ok": False, "error": "동반(역질문) 기능이 꺼져 있습니다.", "question": "", "disabled": True}

    agent = _pick_agent(agent_id, prefer_local=settings["prefer_local"])
    if not agent:
        return {
            "ok": False,
            "error": "사용 가능한 AI 에이전트가 없습니다. /manage/chat 에서 연결하세요.",
            "question": "",
        }

    catalog_text = resource_catalog.catalog_as_prompt(resource_catalog.build_catalog())
    convo = ""
    if history:
        lines = []
        for turn in history[-8:]:
            role = "사용자" if turn.get("role") == "user" else "에이전트"
            lines.append(f"{role}: {turn.get('content', '')}")
        convo = "\n## 최근 대화\n" + "\n".join(lines)

    prompt = (
        f"{SYSTEM}\n\n{catalog_text}{convo}\n\n"
        "사용자가 잠시 입력이 없습니다. 지금 던질 역질문 1개:"
    )
    res = run_agent(agent, prompt, history=[])
    if not res["ok"]:
        return {"ok": False, "error": res["error"], "question": ""}

    # 첫 비어있지 않은 줄만 사용(질문 1개 보장)
    question = ""
    for line in res["output"].splitlines():
        if line.strip():
            question = line.strip().lstrip("-•").strip().strip('"').strip()
            break
    question = question or res["output"].strip()[:300]

    _append_log({
        "ts": _now(),
        "question": question,
        "agent": agent.name,
        "agent_id": agent.id,
        "local": getattr(agent, "local", False),
    })
    return {
        "ok": True,
        "error": "",
        "question": question,
        "agent": {"id": agent.id, "name": agent.name, "local": getattr(agent, "local", False)},
    }
