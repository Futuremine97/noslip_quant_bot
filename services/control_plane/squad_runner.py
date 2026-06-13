"""Squad 실행기 — 여러 Bot을 process/thread 단위로 조립 실행.

- 각 Bot 1회 실행 = 해당 ChatAgent CLI를 subprocess(process)로 spawn.
- parallel 모드 = ThreadPoolExecutor로 Bot들을 thread에 분산(각 thread가 자기 process 관리).
- pipeline = 순차, 직전 출력이 다음 입력 컨텍스트로.
- roundtable = 순차, 누적 발언을 모두 공유.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

try:
    from . import chat_registry, squad_registry
    from .agent_runner import run_agent
    from .models import Bot, SquadMode
except ImportError:  # 단독 실행
    import chat_registry  # type: ignore
    import squad_registry  # type: ignore
    from agent_runner import run_agent  # type: ignore
    from models import Bot, SquadMode  # type: ignore

MAX_PARALLEL = 4


def _compose_prompt(bot: Bot, base_input: str, context: str = "") -> str:
    parts = []
    if bot.system_prompt:
        parts.append(bot.system_prompt)
    if bot.role:
        parts.append(f"[당신의 역할] {bot.role}")
    if context:
        parts.append(f"[이전 단계 출력]\n{context}")
    parts.append(f"[과제]\n{base_input}")
    return "\n\n".join(parts)


def _run_one(bot: Bot, prompt: str) -> dict:
    agent = chat_registry.get_agent(bot.agent_id)
    if not agent:
        return {"bot_id": bot.id, "bot_name": bot.name, "ok": False,
                "output": "", "error": f"에이전트 '{bot.agent_id}' 없음"}
    res = run_agent(agent, prompt, history=[])
    return {
        "bot_id": bot.id,
        "bot_name": bot.name,
        "role": bot.role,
        "agent_id": bot.agent_id,
        "ok": res["ok"],
        "output": res["output"],
        "error": res["error"],
        "elapsed_ms": res["elapsed_ms"],
    }


def run_squad(squad_id: str, user_input: str) -> dict:
    squad = squad_registry.get_squad(squad_id)
    if not squad:
        return {"ok": False, "error": "스쿼드를 찾을 수 없습니다.", "turns": []}
    if not squad.bot_ids:
        return {"ok": False, "error": "스쿼드에 봇이 없습니다.", "turns": []}

    bots: list[Bot] = []
    for bid in squad.bot_ids:
        b = squad_registry.get_bot(bid)
        if b and b.enabled:
            bots.append(b)
    if not bots:
        return {"ok": False, "error": "실행 가능한(enabled) 봇이 없습니다.", "turns": []}

    turns: list[dict] = []

    if squad.mode == SquadMode.parallel:
        prompts = [(b, _compose_prompt(b, user_input)) for b in bots]
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(bots))) as ex:
            futures = [ex.submit(_run_one, b, p) for b, p in prompts]
            turns = [f.result() for f in futures]

    elif squad.mode == SquadMode.roundtable:
        transcript = ""
        for b in bots:
            prompt = _compose_prompt(b, user_input, transcript)
            t = _run_one(b, prompt)
            turns.append(t)
            transcript += f"\n--- {b.name} ({b.role}) ---\n{t['output']}\n"

    else:  # pipeline
        context = ""
        for b in bots:
            prompt = _compose_prompt(b, user_input, context)
            t = _run_one(b, prompt)
            turns.append(t)
            if t["ok"]:
                context = t["output"]

    return {
        "ok": all(t["ok"] for t in turns),
        "error": "" if all(t["ok"] for t in turns) else "일부 봇 실행 실패",
        "mode": squad.mode,
        "squad": {"id": squad.id, "name": squad.name},
        "turns": turns,
    }
