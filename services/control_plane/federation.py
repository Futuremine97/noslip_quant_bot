"""연합 오케스트레이터.

bot들을 묶어 연합 전략을 '역제안'하고, 후보 봇에게 의향을 묻고,
사람이 승인하면 Squad로 확정한다. 승인 전이는 오직 decide(사람)로만 가능.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from . import chat_registry, resource_catalog, squad_registry, squad_runner
    from .agent_runner import run_agent
    from .models import (
        BotVote, FederationProposal, ProposalStatus, SquadCreate, SquadMode, VoteStance,
    )
    from .purpose_engine import _pick_agent
except ImportError:  # 단독 실행
    import chat_registry  # type: ignore
    import resource_catalog  # type: ignore
    import squad_registry  # type: ignore
    import squad_runner  # type: ignore
    from agent_runner import run_agent  # type: ignore
    from models import (  # type: ignore
        BotVote, FederationProposal, ProposalStatus, SquadCreate, SquadMode, VoteStance,
    )
    from purpose_engine import _pick_agent  # type: ignore

_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = _ROOT / "data" / "control_plane"
PATH = DATA_DIR / "federation.json"
_lock = threading.Lock()
MAX_PARALLEL = 4

ORCH_SYSTEM = """당신은 'noslip'의 연합(Federation) 오케스트레이터입니다.
등록된 봇들의 역할을 보고, 주어진 목표를 달성하기 위해 봇들을 묶는 '연합 전략'을 먼저 제안하세요.
시너지가 분명한 조합을 2~3개 제안합니다. 실행 모드는 pipeline(순차)/parallel(동시)/roundtable(토론) 중 선택.
당신은 제안만 하며, 최종 채택은 사람이 결정합니다."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── store ──
def _ensure() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not PATH.exists():
        PATH.write_text('{"proposals": []}', encoding="utf-8")


def _load() -> dict:
    _ensure()
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
        d.setdefault("proposals", [])
        return d
    except (json.JSONDecodeError, OSError):
        return {"proposals": []}


def _save(data: dict) -> None:
    _ensure()
    tmp = PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, PATH)


def list_proposals() -> list[FederationProposal]:
    return [FederationProposal(**p) for p in _load()["proposals"]]


def get_proposal(pid: str) -> Optional[FederationProposal]:
    for p in _load()["proposals"]:
        if p.get("id") == pid:
            return FederationProposal(**p)
    return None


def _upsert(proposal: FederationProposal) -> None:
    with _lock:
        data = _load()
        for i, p in enumerate(data["proposals"]):
            if p.get("id") == proposal.id:
                data["proposals"][i] = proposal.model_dump()
                _save(data)
                return
        data["proposals"].insert(0, proposal.model_dump())
        _save(data)


def delete_proposal(pid: str) -> bool:
    with _lock:
        data = _load()
        before = len(data["proposals"])
        data["proposals"] = [p for p in data["proposals"] if p.get("id") != pid]
        if len(data["proposals"]) == before:
            return False
        _save(data)
        return True


# ── ① 역제안 ──
def _extract_json_array(text: str):
    """LLM 출력에서 JSON 배열을 견고하게 추출."""
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        pass
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            return None
    return None


def propose(goal: str, agent_id: Optional[str] = None, count: int = 3) -> dict:
    if not goal.strip():
        return {"ok": False, "error": "목표(goal)가 비어 있습니다.", "proposals": []}

    bots = [b for b in squad_registry.list_bots()]
    if not bots:
        return {"ok": False, "error": "등록된 봇이 없습니다. 먼저 /manage/bots 에서 봇을 만드세요.", "proposals": []}

    agent = _pick_agent(agent_id)
    if not agent:
        return {"ok": False, "error": "사용 가능한 AI 에이전트가 없습니다. /manage/chat 에서 연결하세요.", "proposals": []}

    bot_lines = "\n".join(f'- id="{b.id}", 이름="{b.name}", 역할="{b.role or "미지정"}"' for b in bots)
    valid_ids = {b.id for b in bots}
    prompt = (
        f"{ORCH_SYSTEM}\n\n"
        f"{resource_catalog.catalog_as_prompt(resource_catalog.build_catalog())}\n\n"
        f"## 사용 가능한 봇\n{bot_lines}\n\n"
        f"## 목표\n{goal}\n\n"
        f"## 출력 (반드시 JSON 배열만, 최대 {count}개)\n"
        '[{"name":"연합 이름","rationale":"제안 근거","member_bots":["봇id",...],'
        '"mode":"pipeline|parallel|roundtable","expected_synergy":"기대 시너지"}]\n'
        "member_bots에는 위 목록의 id만 사용하세요. 설명 없이 JSON만 출력."
    )

    res = run_agent(agent, prompt, history=[])
    if not res["ok"]:
        return {"ok": False, "error": res["error"] or "에이전트 실행 실패", "proposals": []}

    parsed = _extract_json_array(res["output"])
    created: list[FederationProposal] = []

    if isinstance(parsed, list) and parsed:
        for item in parsed[:count]:
            if not isinstance(item, dict):
                continue
            members = [m for m in item.get("member_bots", []) if m in valid_ids]
            if not members:
                continue
            mode = item.get("mode", "pipeline")
            if mode not in {m.value for m in SquadMode}:
                mode = "pipeline"
            proposal = FederationProposal(
                id=uuid.uuid4().hex[:12],
                goal=goal,
                name=str(item.get("name", "연합"))[:120],
                rationale=str(item.get("rationale", "")),
                member_bot_ids=members,
                mode=mode,
                expected_synergy=str(item.get("expected_synergy", "")),
            )
            _upsert(proposal)
            created.append(proposal)

    if not created:
        # 파싱 실패: 원문을 단일 제안 근거로 보존(멤버 없음 → 사람이 검토)
        proposal = FederationProposal(
            id=uuid.uuid4().hex[:12],
            goal=goal,
            name="검토 필요(파싱 실패)",
            rationale=res["output"][:4000],
            member_bot_ids=[],
            mode="pipeline",
        )
        _upsert(proposal)
        created.append(proposal)

    return {"ok": True, "error": "", "proposals": [p.model_dump() for p in created],
            "agent": {"id": agent.id, "name": agent.name}}


# ── ② 봇 투표(폴링) ──
_STANCE_HINTS = {
    VoteStance.agree: ["찬성", "동의", "참여하겠", "agree", "join", "수락"],
    VoteStance.conditional: ["조건부", "다만", "단,", "conditional", "if "],
    VoteStance.decline: ["거절", "반대", "불참", "decline", "거부"],
}


def _detect_stance(text: str) -> VoteStance:
    low = text.lower()
    head = low[:200]
    for stance in (VoteStance.decline, VoteStance.conditional, VoteStance.agree):
        for kw in _STANCE_HINTS[stance]:
            if kw.lower() in head:
                return stance
    return VoteStance.unknown


def _poll_one(bot, proposal: FederationProposal, member_names: str) -> BotVote:
    agent = chat_registry.get_agent(bot.agent_id)
    if not agent:
        return BotVote(bot_id=bot.id, bot_name=bot.name, stance=VoteStance.unknown,
                       comment=f"에이전트 '{bot.agent_id}' 없음")
    prompt = (
        (bot.system_prompt + "\n\n" if bot.system_prompt else "")
        + f"[당신의 역할] {bot.role or '미지정'}\n"
        f"연합 '{proposal.name}' 참여 제안입니다.\n"
        f"- 목표: {proposal.goal}\n- 멤버: {member_names}\n- 실행 모드: {proposal.mode}\n"
        f"- 근거: {proposal.rationale}\n\n"
        "이 연합에 참여하겠습니까? 첫 줄에 '찬성/조건부/거절' 중 하나로 분명히 답하고, "
        "이어서 당신의 기여와 우려를 3~5문장으로 간결히 적으세요."
    )
    res = run_agent(agent, prompt, history=[])
    text = res["output"] if res["ok"] else f"[실행 실패] {res['error']}"
    return BotVote(bot_id=bot.id, bot_name=bot.name, stance=_detect_stance(text), comment=text)


def poll(pid: str) -> dict:
    proposal = get_proposal(pid)
    if not proposal:
        return {"ok": False, "error": "제안을 찾을 수 없습니다.", "votes": []}
    if proposal.status != ProposalStatus.proposed:
        return {"ok": False, "error": "이미 결정된 제안입니다.", "votes": []}

    bots = [squad_registry.get_bot(bid) for bid in proposal.member_bot_ids]
    bots = [b for b in bots if b]
    if not bots:
        return {"ok": False, "error": "멤버 봇이 없습니다.", "votes": []}

    member_names = ", ".join(b.name for b in bots)
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(bots))) as ex:
        votes = list(ex.map(lambda b: _poll_one(b, proposal, member_names), bots))

    proposal.votes = votes
    _upsert(proposal)
    return {"ok": True, "error": "", "votes": [v.model_dump() for v in votes]}


# ── ③ 사람 결정 게이트 ──
def decide(pid: str, decision: str, auto_run: bool = False, run_input: Optional[str] = None) -> dict:
    proposal = get_proposal(pid)
    if not proposal:
        return {"ok": False, "error": "제안을 찾을 수 없습니다."}
    if proposal.status != ProposalStatus.proposed:
        return {"ok": False, "error": f"이미 '{proposal.status}' 상태입니다."}

    if decision == "reject":
        proposal.status = ProposalStatus.rejected
        proposal.decided_at = _now()
        _upsert(proposal)
        return {"ok": True, "proposal": proposal.model_dump()}

    if decision == "approve":
        if not proposal.member_bot_ids:
            return {"ok": False, "error": "멤버 봇이 없어 확정할 수 없습니다."}
        # ④ 승인 시에만 Squad 생성
        squad = squad_registry.create_squad(
            SquadCreate(name=proposal.name, bot_ids=proposal.member_bot_ids, mode=proposal.mode)
        )
        proposal.status = ProposalStatus.approved
        proposal.created_squad_id = squad.id
        proposal.decided_at = _now()
        _upsert(proposal)

        # ⑤ (선택) 승인 후 자동 실행
        if auto_run:
            run_res = run_approved(proposal.id, run_input)
            return {"ok": True, "proposal": (get_proposal(proposal.id) or proposal).model_dump(),
                    "squad_id": squad.id, "run": run_res}
        return {"ok": True, "proposal": proposal.model_dump(), "squad_id": squad.id}

    return {"ok": False, "error": "decision은 approve 또는 reject 여야 합니다."}


# ── 승인된 연합 실행 ──
def run_approved(pid: str, run_input: Optional[str] = None) -> dict:
    """승인되어 스쿼드가 생성된 제안을 실행하고 결과를 제안에 기록."""
    proposal = get_proposal(pid)
    if not proposal:
        return {"ok": False, "error": "제안을 찾을 수 없습니다."}
    if proposal.status != ProposalStatus.approved or not proposal.created_squad_id:
        return {"ok": False, "error": "승인되어 스쿼드가 생성된 제안만 실행할 수 있습니다."}

    user_input = (run_input or "").strip() or proposal.goal
    proposal.run_status = "running"
    proposal.run_input = user_input
    _upsert(proposal)

    result = squad_runner.run_squad(proposal.created_squad_id, user_input)

    proposal.run_turns = result.get("turns", [])
    proposal.run_status = "done" if result.get("ok") else "error"
    proposal.run_at = _now()
    _upsert(proposal)
    return {"ok": result.get("ok", False), "error": result.get("error", ""),
            "turns": result.get("turns", [])}
