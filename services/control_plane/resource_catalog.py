"""noslip 가용 자원 카탈로그.

Purpose 엔진이 "지금 이 프로젝트로 무엇을 할 수 있는가"를 근거 있게 판단하도록
현재 시스템의 자원을 수집한다. 시크릿/키는 절대 포함하지 않는다.
"""
from __future__ import annotations

from pathlib import Path

try:
    from . import chat_registry, registry, squad_registry
except ImportError:  # 단독 실행
    import chat_registry  # type: ignore
    import registry  # type: ignore
    import squad_registry  # type: ignore

_ROOT = Path(__file__).resolve().parents[2]

# noslip 퀀트/서비스 핵심 능력 (큐레이션 — 사용자 안내용 설명)
CURATED_CAPABILITIES = [
    {"key": "prophet_forecast", "name": "Prophet 시계열 예측", "desc": "종목 N일 가격 예측·신뢰구간·추세/계절성 분석", "cli": "noslip prophet <티커> --days <N>"},
    {"key": "consensus_analysis", "name": "6-Agent 컨센서스 분석", "desc": "다중 에이전트 합의로 매수/매도 신호 산출", "cli": "noslip analyze <티커>"},
    {"key": "portfolio", "name": "S&P500 가상 포트폴리오", "desc": "가상 자산 손익·포지션 현황", "cli": "noslip portfolio"},
    {"key": "broker", "name": "증권사 API 연동", "desc": "toss/kis/kiwoom/kb 등 OPEN API 연결·진단", "cli": "noslip broker [브로커] / noslip setup"},
    {"key": "cardnews", "name": "AI 카드뉴스 생성", "desc": "주제 기반 다국어 카드뉴스 이미지 생성", "cli": "noslip cardnews --topic <주제> --lang <ko|ja>"},
    {"key": "dashboard", "name": "웹 대시보드 + API", "desc": "Next.js 대시보드와 API 프록시", "cli": "noslip start"},
    {"key": "telegram_bot", "name": "텔레그램 인터랙티브 봇", "desc": "대화형 퀀트 봇 데몬", "cli": "noslip bot"},
]


def _scan_trader_modules(limit: int = 40) -> list[str]:
    """services/trader/*.py 파일명에서 능력 힌트를 수집(메타만)."""
    trader = _ROOT / "services" / "trader"
    if not trader.exists():
        return []
    names = []
    for p in sorted(trader.glob("*.py")):
        n = p.stem
        if n.startswith("_") or n in {"config", "__init__"}:
            continue
        names.append(n)
        if len(names) >= limit:
            break
    return names


def build_catalog() -> dict:
    """현재 가용 리소스 전체 카탈로그."""
    try:
        mcp = [
            {"id": s.id, "name": s.name, "transport": s.transport, "status": s.last_status, "enabled": s.enabled}
            for s in registry.list_servers()
        ]
    except Exception:  # noqa: BLE001
        mcp = []
    try:
        agents = [
            {"id": a.id, "name": a.name, "kind": a.kind, "status": a.last_status, "enabled": a.enabled}
            for a in chat_registry.list_agents()
        ]
    except Exception:  # noqa: BLE001
        agents = []
    try:
        bots = [{"id": b.id, "name": b.name, "role": b.role, "agent_id": b.agent_id} for b in squad_registry.list_bots()]
        squads = [{"id": s.id, "name": s.name, "mode": s.mode, "bots": s.bot_ids} for s in squad_registry.list_squads()]
    except Exception:  # noqa: BLE001
        bots, squads = [], []

    return {
        "capabilities": CURATED_CAPABILITIES,
        "mcp_servers": mcp,
        "agents": agents,
        "bots": bots,
        "squads": squads,
        "trader_modules": _scan_trader_modules(),
    }


def catalog_as_prompt(catalog: dict) -> str:
    """카탈로그를 LLM 프롬프트용 텍스트로 직렬화."""
    lines = ["## noslip 가용 자원\n", "### 핵심 능력"]
    for c in catalog["capabilities"]:
        lines.append(f"- {c['name']}: {c['desc']} (CLI: `{c['cli']}`)")

    if catalog["mcp_servers"]:
        lines.append("\n### 등록된 MCP 서버")
        for m in catalog["mcp_servers"]:
            lines.append(f"- {m['name']} ({m['transport']}, 상태={m['status']})")
    if catalog["agents"]:
        lines.append("\n### 연결된 AI 에이전트")
        for a in catalog["agents"]:
            lines.append(f"- {a['name']} (kind={a['kind']}, 상태={a['status']})")
    if catalog["bots"]:
        lines.append("\n### 등록된 봇")
        for b in catalog["bots"]:
            lines.append(f"- {b['name']} (역할={b['role'] or '미지정'})")
    if catalog["trader_modules"]:
        lines.append("\n### 보유 트레이딩/분석 모듈 (파일 기준)")
        lines.append(", ".join(catalog["trader_modules"]))
    return "\n".join(lines)
