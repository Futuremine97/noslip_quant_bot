"""MCP 서버 연결 점검.

MVP 수준: 트랜스포트별로 도달 가능한지 가볍게 확인한다.
- stdio : command 실행 파일이 PATH/경로에 존재하고 실행 가능한지 확인
- http  : 엔드포인트 GET/HEAD 200~405 응답 여부
- sse   : http와 동일하게 도달성만 확인
완전한 MCP initialize/tools/list 핸드셰이크는 로드맵 2단계에서 구현.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

try:  # 패키지로 임포트될 때
    from .models import MCPServer, Status
except ImportError:  # 폴더 안에서 단독 실행될 때
    from models import MCPServer, Status  # type: ignore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def probe(server: MCPServer) -> tuple[Status, str, str]:
    """(status, detail, checked_at) 반환."""
    checked_at = _now()
    try:
        if server.transport == "stdio":
            status, detail = _probe_stdio(server)
        else:
            status, detail = _probe_url(server)
    except Exception as e:  # noqa: BLE001
        status, detail = Status.error, f"점검 중 예외: {e}"
    return status, detail, checked_at


def _probe_stdio(server: MCPServer) -> tuple[Status, str]:
    cmd = server.command or ""
    # 절대/상대 경로면 파일 존재 확인, 아니면 PATH 탐색
    if "/" in cmd or "\\" in cmd:
        ok = Path(cmd).expanduser().exists()
    else:
        ok = shutil.which(cmd) is not None
    if ok:
        return Status.ok, f"실행 파일 확인됨: {cmd}"
    return Status.error, f"실행 파일을 찾을 수 없음: {cmd}"


def _probe_url(server: MCPServer) -> tuple[Status, str]:
    url = server.url or ""
    if not url.startswith(("http://", "https://")):
        return Status.error, f"유효하지 않은 URL: {url}"
    try:
        import urllib.request

        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            code = resp.status
        if code < 500:
            return Status.ok, f"HTTP {code}"
        return Status.error, f"HTTP {code}"
    except Exception as e:  # noqa: BLE001
        return Status.error, f"도달 실패: {e}"
