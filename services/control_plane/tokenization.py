"""사용량 토큰화(Tokenization) 계층 — 오프체인.

AI 액션(에이전트 실행·Purpose·스쿼드·연합·동반 역질문)의 사용량을 NSQ 토큰 단위로
환산·차감하고 원장(ledger)에 기록한다. 온체인 `pay_for_usage`(Solana)의 정산 전 단계로,
동일한 'units * fee_per_unit' 모델을 미러링한다.

⚠️ NSQ는 유틸리티 토큰. 본 원장은 사용량 회계용이며 투자/수익과 무관하다.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = _ROOT / "data" / "control_plane"
CONFIG_PATH = DATA_DIR / "token_config.json"
LEDGER_PATH = DATA_DIR / "token_ledger.json"
_lock = threading.Lock()

DEFAULT_ACCOUNT = "default"

# 액션별 사용량(units). 온체인 usage_fee_per_unit과 곱해 NSQ 산출.
DEFAULT_CONFIG = {
    "nsq_per_unit": 0.01,          # 1 unit = 0.01 NSQ
    "initial_grant": 100.0,        # 최초 계정 생성 시 지급(NSQ)
    "actions": {
        "agent_run": 1,            # 채팅/에이전트 1회 실행
        "purpose_plan": 3,         # Purpose 전략 산출
        "squad_run_per_bot": 2,    # 스쿼드 봇 1개당
        "federation_propose": 2,   # 연합 역제안
        "federation_run_per_bot": 2,
        "companion_nudge": 1,      # 동반 역질문
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── config ──
def get_config() -> dict:
    _ensure()
    if not CONFIG_PATH.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        d = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        merged = json.loads(json.dumps(DEFAULT_CONFIG))
        merged.update({k: v for k, v in d.items() if k != "actions"})
        merged["actions"].update(d.get("actions", {}))
        return merged
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(DEFAULT_CONFIG))


def update_config(patch: dict) -> dict:
    with _lock:
        cur = get_config()
        if "nsq_per_unit" in patch and patch["nsq_per_unit"] is not None:
            cur["nsq_per_unit"] = max(0.0, float(patch["nsq_per_unit"]))
        if "initial_grant" in patch and patch["initial_grant"] is not None:
            cur["initial_grant"] = max(0.0, float(patch["initial_grant"]))
        if isinstance(patch.get("actions"), dict):
            for k, v in patch["actions"].items():
                try:
                    cur["actions"][k] = max(0, int(v))
                except (TypeError, ValueError):
                    continue
        _ensure()
        CONFIG_PATH.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        return cur


# ── ledger ──
def _load() -> dict:
    _ensure()
    if not LEDGER_PATH.exists():
        return {"accounts": {}}
    try:
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"accounts": {}}


def _save(data: dict) -> None:
    _ensure()
    tmp = LEDGER_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, LEDGER_PATH)


def _account(data: dict, account: str) -> dict:
    accts = data.setdefault("accounts", {})
    if account not in accts:
        accts[account] = {"id": account, "entries": []}
        cfg = get_config()
        if cfg["initial_grant"] > 0:
            accts[account]["entries"].append({
                "ts": _now(), "type": "grant", "action": "initial",
                "units": 0, "nsq": round(cfg["initial_grant"], 9), "note": "초기 지급",
            })
    return accts[account]


def _balance(acct: dict) -> float:
    bal = 0.0
    for e in acct["entries"]:
        bal += e["nsq"] if e["type"] == "grant" else -e["nsq"]
    return round(bal, 9)


def grant(account: str, amount: float, note: str = "") -> dict:
    with _lock:
        data = _load()
        acct = _account(data, account)
        acct["entries"].append({
            "ts": _now(), "type": "grant", "action": "grant",
            "units": 0, "nsq": round(max(0.0, float(amount)), 9), "note": note or "수동 지급",
        })
        _save(data)
        return {"account": account, "balance": _balance(acct)}


def charge(account: str, action: str, qty: int = 1, note: str = "") -> dict:
    cfg = get_config()
    units = cfg["actions"].get(action, 1) * max(1, int(qty))
    nsq = round(units * cfg["nsq_per_unit"], 9)
    with _lock:
        data = _load()
        acct = _account(data, account)
        acct["entries"].append({
            "ts": _now(), "type": "charge", "action": action,
            "qty": int(qty), "units": units, "nsq": nsq, "note": note,
        })
        _save(data)
        bal = _balance(acct)
    return {"account": account, "action": action, "units": units, "nsq": nsq, "balance": bal}


def meter(action: str, qty: int = 1, account: str = DEFAULT_ACCOUNT) -> dict | None:
    """액션 사용량 계량(best-effort). 실패해도 절대 예외를 올리지 않는다."""
    try:
        return charge(account, action, qty, note="usage")
    except Exception:  # noqa: BLE001
        return None


def get_account(account: str = DEFAULT_ACCOUNT, limit: int = 40) -> dict:
    with _lock:
        data = _load()
        acct = _account(data, account)
        _save(data)  # 최초 생성 시 초기 지급 영속화
        bal = _balance(acct)
        granted = round(sum(e["nsq"] for e in acct["entries"] if e["type"] == "grant"), 9)
        spent = round(sum(e["nsq"] for e in acct["entries"] if e["type"] == "charge"), 9)
        entries = acct["entries"][-limit:][::-1]
    return {"id": account, "balance": bal, "granted": granted, "spent": spent, "entries": entries}


def usage_summary(account: str = DEFAULT_ACCOUNT) -> dict:
    with _lock:
        data = _load()
        acct = _account(data, account)
        _save(data)
        by_action: dict[str, dict] = {}
        for e in acct["entries"]:
            if e["type"] != "charge":
                continue
            a = e["action"]
            slot = by_action.setdefault(a, {"action": a, "count": 0, "units": 0, "nsq": 0.0})
            slot["count"] += 1
            slot["units"] += e.get("units", 0)
            slot["nsq"] = round(slot["nsq"] + e["nsq"], 9)
    return {"account": account, "by_action": sorted(by_action.values(), key=lambda x: -x["nsq"])}
