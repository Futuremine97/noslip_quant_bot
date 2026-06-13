"""로컬 CLI 에이전트 실행기.

보안: shell을 절대 쓰지 않고 argv 리스트로만 실행하여 인젝션을 차단한다.
타임아웃/출력 캡으로 폭주를 방지한다.
"""
from __future__ import annotations

import queue
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

try:
    from .models import ChatAgent, PromptMode, Status
except ImportError:  # 단독 실행
    from models import ChatAgent, PromptMode, Status  # type: ignore

_ROOT = Path(__file__).resolve().parents[2]
MAX_OUTPUT = 64 * 1024  # 64KB 캡


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_agent(agent: ChatAgent) -> tuple[Status, str, str]:
    """CLI 설치/실행 가능 여부 점검. (status, detail, checked_at)"""
    checked_at = _now()
    cmd = (agent.command or "").strip()
    if not cmd:
        return Status.error, "command가 비어 있습니다.", checked_at
    if "/" in cmd or "\\" in cmd:
        ok = Path(cmd).expanduser().exists()
    else:
        ok = shutil.which(cmd) is not None
    if ok:
        return Status.ok, f"실행 파일 확인됨: {cmd}", checked_at
    return Status.error, f"실행 파일을 찾을 수 없음: {cmd} (설치/PATH 확인)", checked_at


def _build_prompt(message: str, history: list[dict]) -> str:
    """직전 히스토리를 텍스트로 합쳐 컨텍스트 구성 (MVP)."""
    if not history:
        return message
    lines = []
    for turn in history[-10:]:  # 최근 10턴
        role = "User" if turn.get("role") == "user" else "Assistant"
        lines.append(f"{role}: {turn.get('content', '')}")
    lines.append(f"User: {message}")
    lines.append("Assistant:")
    return "\n".join(lines)


def run_agent(agent: ChatAgent, message: str, history: list[dict] | None = None) -> dict:
    """에이전트 CLI를 1회성으로 실행하고 결과를 반환.

    반환: {ok, output, error, exit_code, elapsed_ms}
    """
    history = history or []
    prompt = _build_prompt(message, history)

    cmd = (agent.command or "").strip()
    if not cmd:
        return {"ok": False, "output": "", "error": "command가 비어 있습니다.", "exit_code": None, "elapsed_ms": 0}
    if "/" not in cmd and "\\" not in cmd and shutil.which(cmd) is None:
        return {"ok": False, "output": "", "error": f"실행 파일을 찾을 수 없음: {cmd}", "exit_code": None, "elapsed_ms": 0}

    argv = [cmd, *agent.args]
    stdin_data = None
    if agent.prompt_mode == PromptMode.stdin:
        stdin_data = prompt
    else:  # arg
        argv.append(prompt)

    cwd = agent.cwd or str(_ROOT)
    started = datetime.now(timezone.utc)
    try:
        proc = subprocess.run(
            argv,
            input=stdin_data,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=agent.timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "output": "",
            "error": f"{agent.timeout_s}초 타임아웃 초과",
            "exit_code": None,
            "elapsed_ms": agent.timeout_s * 1000,
        }
    except FileNotFoundError:
        return {"ok": False, "output": "", "error": f"실행 실패: {cmd} 없음", "exit_code": None, "elapsed_ms": 0}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "output": "", "error": f"실행 예외: {e}", "exit_code": None, "elapsed_ms": 0}

    elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    out = (proc.stdout or "")[:MAX_OUTPUT]
    err = (proc.stderr or "")[:MAX_OUTPUT]
    ok = proc.returncode == 0
    return {
        "ok": ok,
        "output": out.strip(),
        "error": "" if ok else (err.strip() or f"종료 코드 {proc.returncode}"),
        "exit_code": proc.returncode,
        "elapsed_ms": elapsed_ms,
    }


def stream_run(
    agent: ChatAgent, message: str, history: list[dict] | None = None
) -> Iterator[dict]:
    """에이전트 CLI를 실행하며 stdout을 증분(스트리밍)으로 yield.

    이벤트:
      {"type":"chunk","text": "..."}            stdout 일부
      {"type":"error","error": "..."}           실행 불가/타임아웃
      {"type":"done","ok":bool,"error":str,"exit_code":int,"elapsed_ms":int}
    """
    history = history or []
    prompt = _build_prompt(message, history)

    cmd = (agent.command or "").strip()
    if not cmd:
        yield {"type": "error", "error": "command가 비어 있습니다."}
        return
    if "/" not in cmd and "\\" not in cmd and shutil.which(cmd) is None:
        yield {"type": "error", "error": f"실행 파일을 찾을 수 없음: {cmd}"}
        return

    argv = [cmd, *agent.args]
    stdin_data = None
    if agent.prompt_mode == PromptMode.stdin:
        stdin_data = prompt
    else:
        argv.append(prompt)

    cwd = agent.cwd or str(_ROOT)
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except Exception as e:  # noqa: BLE001
        yield {"type": "error", "error": f"실행 실패: {e}"}
        return

    if stdin_data is not None and proc.stdin:
        try:
            proc.stdin.write(stdin_data)
            proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass

    q: "queue.Queue[tuple[str, str]]" = queue.Queue()

    def _reader():
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                q.put(("chunk", line))
        finally:
            q.put(("eof", ""))

    threading.Thread(target=_reader, daemon=True).start()

    deadline = started + agent.timeout_s
    total = 0
    timed_out = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            proc.kill()
            timed_out = True
            break
        try:
            kind, payload = q.get(timeout=min(remaining, 1.0))
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue
        if kind == "eof":
            break
        total += len(payload)
        yield {"type": "chunk", "text": payload}
        if total > MAX_OUTPUT:
            proc.kill()
            break

    if timed_out:
        yield {"type": "error", "error": f"{agent.timeout_s}초 타임아웃 초과"}
        return

    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        proc.kill()
    err = ""
    if proc.stderr:
        try:
            err = (proc.stderr.read() or "")[:4096]
        except Exception:  # noqa: BLE001
            err = ""
    rc = proc.returncode
    ok = rc == 0
    yield {
        "type": "done",
        "ok": ok,
        "error": "" if ok else (err.strip() or f"종료 코드 {rc}"),
        "exit_code": rc,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
