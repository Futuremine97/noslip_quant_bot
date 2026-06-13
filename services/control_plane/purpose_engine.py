"""Purpose 엔진 — 사용자의 전략·의도를 읽어 noslip 자원으로 실행계획을 산출.

상담(Consultation) → 전략(Strategy) → 구축 가이드(Build Guide) 3섹션을
연결된 AI 에이전트(claude 우선)를 통해 생성한다.
"""
from __future__ import annotations

from typing import Optional

try:
    from . import chat_registry, resource_catalog
    from .agent_runner import run_agent
    from .models import ChatAgent
except ImportError:  # 단독 실행
    import chat_registry  # type: ignore
    import resource_catalog  # type: ignore
    from agent_runner import run_agent  # type: ignore
    from models import ChatAgent  # type: ignore

SYSTEM = """당신은 'noslip' 프로젝트 전속 솔루션 컨설턴트입니다.
noslip은 퀀트 트레이딩/예측/브로커 연동/대시보드/멀티 AI 에이전트를 갖춘 시스템입니다.
사용자의 전략·의도를 깊이 이해하고, 아래 '가용 자원'만을 근거로 실현 가능한 계획을 제시하세요.
없는 기능을 지어내지 말고, 부족하면 무엇을 추가로 구축해야 하는지 명시하세요."""

OUTPUT_SPEC = """반드시 아래 3개 섹션을 한국어 마크다운으로, 이 제목 그대로 작성하세요:

## 1. 상담 (Consultation)
- 사용자 의도 요약, 현재 자원으로의 적합성·전제·리스크 진단.

## 2. 전략 (Strategy)
- 목표를 noslip 자원에 매핑한 구체적 접근법과 우선순위(번호 목록).

## 3. 구축 상세 가이드 (Build Guide)
- 단계별 실행 절차. 각 단계마다 사용할 CLI 명령(`noslip ...`), MCP 도구/에이전트,
  관련 서비스 모듈, 그리고 완료 검증 방법을 구체적으로 적으세요."""


def build_prompt(purpose: str, catalog_text: str, role_prompt: str = "") -> str:
    parts = [SYSTEM]
    if role_prompt:
        parts.append(f"\n[추가 역할 지침]\n{role_prompt}")
    parts.append(f"\n{catalog_text}")
    parts.append(f"\n## 사용자의 전략·의도(purpose)\n{purpose}")
    parts.append(f"\n{OUTPUT_SPEC}")
    return "\n".join(parts)


def _pick_agent(agent_id: Optional[str]) -> Optional[ChatAgent]:
    agents = chat_registry.list_agents()
    if agent_id:
        return next((a for a in agents if a.id == agent_id), None)
    # claude 우선 → enabled 첫 번째
    enabled = [a for a in agents if a.enabled]
    for kind in ("claude", "codex", "antigravity"):
        for a in enabled:
            if a.kind == kind:
                return a
    return enabled[0] if enabled else None


def prepare(purpose: str, agent_id: Optional[str] = None) -> dict:
    """스트리밍용: (agent, prompt)를 준비. 실행은 호출측에서 stream_run으로.

    반환: {ok, error, agent, prompt}
    """
    if not purpose.strip():
        return {"ok": False, "error": "purpose가 비어 있습니다.", "agent": None, "prompt": ""}
    agent = _pick_agent(agent_id)
    if not agent:
        return {
            "ok": False,
            "error": "사용 가능한 AI 에이전트가 없습니다. 먼저 /manage/chat 에서 claude 등을 연결하세요.",
            "agent": None,
            "prompt": "",
        }
    catalog = resource_catalog.build_catalog()
    prompt = build_prompt(purpose, resource_catalog.catalog_as_prompt(catalog))
    return {"ok": True, "error": "", "agent": agent, "prompt": prompt}


def plan(purpose: str, agent_id: Optional[str] = None, role_prompt: str = "") -> dict:
    """단일 에이전트로 purpose 계획 산출."""
    if not purpose.strip():
        return {"ok": False, "error": "purpose가 비어 있습니다.", "output": ""}

    agent = _pick_agent(agent_id)
    if not agent:
        return {
            "ok": False,
            "error": "사용 가능한 AI 에이전트가 없습니다. 먼저 /manage/chat 에서 claude 등을 연결하세요.",
            "output": "",
        }

    catalog = resource_catalog.build_catalog()
    prompt = build_prompt(purpose, resource_catalog.catalog_as_prompt(catalog), role_prompt)
    result = run_agent(agent, prompt, history=[])
    return {
        "ok": result["ok"],
        "error": result["error"],
        "output": result["output"],
        "agent": {"id": agent.id, "name": agent.name, "kind": agent.kind},
        "elapsed_ms": result["elapsed_ms"],
        "resources_used": {
            "capabilities": len(catalog["capabilities"]),
            "mcp_servers": len(catalog["mcp_servers"]),
            "agents": len(catalog["agents"]),
        },
    }
