"""Control Plane 데이터 스키마 (pydantic v2)."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Transport(str, Enum):
    stdio = "stdio"
    sse = "sse"
    http = "http"


class Status(str, Enum):
    unknown = "unknown"
    ok = "ok"
    error = "error"


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class MCPServerBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    transport: Transport = Transport.stdio
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    url: Optional[str] = None
    # env: {ENV_NAME: "secret://ref" 또는 평문(비시크릿)}.
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)

    @field_validator("command")
    @classmethod
    def _strip_command(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if isinstance(v, str) and v.strip() else None

    def validate_transport(self) -> None:
        """트랜스포트별 필수 필드 검증. 라우터에서 호출."""
        if self.transport == Transport.stdio:
            if not self.command:
                raise ValueError("stdio 트랜스포트는 command가 필요합니다.")
        else:  # sse / http
            if not self.url:
                raise ValueError(f"{self.transport.value} 트랜스포트는 url이 필요합니다.")


class MCPServerCreate(MCPServerBase):
    id: Optional[str] = None  # 미지정 시 name에서 slug 생성

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not _SLUG_RE.match(v):
            raise ValueError("id는 소문자/숫자/-/_ 만 허용(최대 64자, 영숫자로 시작).")
        return v


class MCPServerUpdate(BaseModel):
    name: Optional[str] = None
    transport: Optional[Transport] = None
    command: Optional[str] = None
    args: Optional[list[str]] = None
    url: Optional[str] = None
    env: Optional[dict[str, str]] = None
    enabled: Optional[bool] = None
    tags: Optional[list[str]] = None


class MCPServer(MCPServerBase):
    id: str
    last_status: Status = Status.unknown
    last_checked_at: Optional[str] = None
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    def masked(self) -> "MCPServer":
        """시크릿 참조/값을 마스킹한 사본 반환 (API 응답용)."""
        safe_env = {}
        for k, val in self.env.items():
            if val.startswith("secret://") or _looks_sensitive(k):
                safe_env[k] = "***"
            else:
                safe_env[k] = val
        return self.model_copy(update={"env": safe_env})


def _looks_sensitive(key: str) -> bool:
    k = key.lower()
    return any(t in k for t in ("token", "key", "secret", "password", "pat", "credential"))


def slugify(name: str, fallback: str = "item") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or fallback)[:64]


# ───────────────────────────── Chat 에이전트 ─────────────────────────────
class AgentKind(str, Enum):
    claude = "claude"
    codex = "codex"
    antigravity = "antigravity"
    custom = "custom"


class PromptMode(str, Enum):
    arg = "arg"      # 프롬프트를 argv 마지막 인자로
    stdin = "stdin"  # 프롬프트를 표준입력으로


# 프리셋 기본값 (등록 시 편집 가능)
AGENT_PRESETS: dict[str, dict] = {
    "claude": {"command": "claude", "args": ["-p"], "prompt_mode": "arg"},
    "codex": {"command": "codex", "args": ["exec"], "prompt_mode": "arg"},
    "antigravity": {"command": "antigravity", "args": [], "prompt_mode": "arg"},
    "custom": {"command": "", "args": [], "prompt_mode": "arg"},
}


class ChatAgentBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    kind: AgentKind = AgentKind.custom
    command: str = Field(default="")
    args: list[str] = Field(default_factory=list)
    prompt_mode: PromptMode = PromptMode.arg
    cwd: Optional[str] = None
    timeout_s: int = Field(default=120, ge=1, le=1800)
    enabled: bool = True


class ChatAgentCreate(ChatAgentBase):
    id: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not _SLUG_RE.match(v):
            raise ValueError("id는 소문자/숫자/-/_ 만 허용(최대 64자, 영숫자로 시작).")
        return v

    def validate_runnable(self) -> None:
        if not self.command.strip():
            raise ValueError("command가 필요합니다.")


class ChatAgentUpdate(BaseModel):
    name: Optional[str] = None
    kind: Optional[AgentKind] = None
    command: Optional[str] = None
    args: Optional[list[str]] = None
    prompt_mode: Optional[PromptMode] = None
    cwd: Optional[str] = None
    timeout_s: Optional[int] = None
    enabled: Optional[bool] = None


class ChatAgent(ChatAgentBase):
    id: str
    last_status: Status = Status.unknown
    last_checked_at: Optional[str] = None
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


# ───────────────────────────── 멀티봇 (Bot / Squad) ─────────────────────────────
class SquadMode(str, Enum):
    pipeline = "pipeline"      # 순차, 직전 출력이 다음 입력
    parallel = "parallel"      # 동시 실행(각자 thread/process)
    roundtable = "roundtable"  # 순차, 누적 발언 공유


class BotBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    role: str = Field(default="", max_length=200)
    agent_id: str = Field(..., min_length=1)  # 어떤 ChatAgent로 실행할지
    system_prompt: str = Field(default="")
    enabled: bool = True


class BotCreate(BotBase):
    id: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _SLUG_RE.match(v):
            raise ValueError("id는 소문자/숫자/-/_ 만 허용(최대 64자).")
        return v


class BotUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    agent_id: Optional[str] = None
    system_prompt: Optional[str] = None
    enabled: Optional[bool] = None


class Bot(BotBase):
    id: str
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class SquadBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    bot_ids: list[str] = Field(default_factory=list)
    mode: SquadMode = SquadMode.pipeline
    enabled: bool = True


class SquadCreate(SquadBase):
    id: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _SLUG_RE.match(v):
            raise ValueError("id는 소문자/숫자/-/_ 만 허용(최대 64자).")
        return v


class SquadUpdate(BaseModel):
    name: Optional[str] = None
    bot_ids: Optional[list[str]] = None
    mode: Optional[SquadMode] = None
    enabled: Optional[bool] = None


class Squad(SquadBase):
    id: str
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


# ───────────────────────────── 연합 오케스트레이터 ─────────────────────────────
class ProposalStatus(str, Enum):
    proposed = "proposed"
    approved = "approved"
    rejected = "rejected"


class VoteStance(str, Enum):
    agree = "agree"
    conditional = "conditional"
    decline = "decline"
    unknown = "unknown"


class BotVote(BaseModel):
    bot_id: str
    bot_name: str
    stance: VoteStance = VoteStance.unknown
    comment: str = ""


class FederationProposal(BaseModel):
    id: str
    goal: str
    name: str
    rationale: str = ""
    member_bot_ids: list[str] = Field(default_factory=list)
    mode: SquadMode = SquadMode.pipeline
    expected_synergy: str = ""
    status: ProposalStatus = ProposalStatus.proposed
    votes: list[BotVote] = Field(default_factory=list)
    created_squad_id: Optional[str] = None
    created_at: str = Field(default_factory=_now)
    decided_at: Optional[str] = None
    # 승인 후 실행 결과
    run_status: Optional[str] = None  # None | running | done | error
    run_input: Optional[str] = None
    run_turns: list[dict] = Field(default_factory=list)
    run_at: Optional[str] = None
