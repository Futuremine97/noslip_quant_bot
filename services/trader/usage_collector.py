#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Consent-based user input data collection for No Slip Quant.

Collects how users actually use the system (telegram commands, forecast
requests, dataset registrations) and ships anonymized events to the central
server — powering service improvement, zero-shot model priors, and usage
analytics. Mirrors the federated-sharing consent pattern:

  * 기본값 OFF. 텔레그램 `/수집 온` 또는 set_collection_consent(True)로 동의.
  * peer_id(기기 해시)로 익명화 — 사용자명/원본 데이터는 기본 전송 안 함.
  * 데이터셋은 메타데이터만 수집 (rows/domain/지표명). 원본 row 기여는
    contribute_dataset()을 명시적으로 호출할 때만.

Usage:
    from usage_collector import log_event
    log_event("telegram_command", {"cmd": "/prophet"})   # fire-and-forget
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import requests

ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT_DIR / "services" / "trader" / "model_cache" / "whale_config.json"


def _server() -> str:
    url = os.getenv("PREDICTION_API_URL", "").strip()
    return (url or "http://localhost:8000").rstrip("/")


def _headers() -> dict:
    token = os.getenv("PREDICTION_API_TOKEN", "").strip()
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _peer_id() -> str:
    try:
        from peer_hub_client import get_local_peer_identity
        return get_local_peer_identity()["peer_id"]
    except Exception:
        return "peer_unknown"


# ----------------- Consent -----------------

def get_collection_consent() -> bool:
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r") as f:
                return bool(json.load(f).get("data_collection", {}).get("consent_granted", False))
    except Exception:
        pass
    return False


def set_collection_consent(consent: bool) -> bool:
    try:
        config = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r") as f:
                config = json.load(f)
        config.setdefault("data_collection", {})["consent_granted"] = bool(consent)
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print(f"⚠️ Failed to write collection consent: {e}")
        return False


# ----------------- Event Logging -----------------

def _post(path: str, payload: dict):
    try:
        requests.post(f"{_server()}{path}", json=payload, headers=_headers(), timeout=5)
    except Exception:
        pass  # telemetry must never break the product


def log_event(feature: str, meta: dict | None = None, block: bool = False):
    """Anonymized usage event. No-op without consent. Non-blocking by default."""
    if not get_collection_consent():
        return
    payload = {"peer_id": _peer_id(), "feature": str(feature)[:60],
               "meta": meta or {}}
    if block:
        _post("/collect/event", payload)
    else:
        threading.Thread(target=_post, args=("/collect/event", payload), daemon=True).start()


def contribute_dataset(user_id: str, name: str) -> str:
    """Explicitly contribute a registered dataset's RAW rows to the central
    server (opt-in per dataset; never automatic)."""
    if not get_collection_consent():
        return "❌ 데이터 수집 동의가 꺼져 있습니다. /수집 온 으로 활성화해 주세요."
    try:
        from personal_forecast_service import _ds_dir
        import pandas as pd
        d = _ds_dir(user_id, name)
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        rows = pd.read_csv(d / "data.csv").to_dict("records")
        payload = {"peer_id": _peer_id(), "feature": "dataset_contribution",
                   "meta": {**{k: meta.get(k) for k in ("name", "domain", "rows", "index_mode")},
                            "data_rows": rows[:5000]}}
        res = requests.post(f"{_server()}/collect/event", json=payload,
                            headers=_headers(), timeout=30)
        res.raise_for_status()
        return f"✅ '{name}' 데이터셋({meta.get('rows')}행)을 중앙 서버에 기여했습니다. 감사합니다!"
    except Exception as e:
        return f"⚠️ 데이터셋 기여 실패: {e}"


def fetch_collection_stats() -> str:
    """Human-readable central usage statistics."""
    try:
        res = requests.get(f"{_server()}/collect/stats", headers=_headers(), timeout=10)
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        return f"⚠️ 수집 통계 조회 실패: {e}"
    lines = ["📊 <b>사용자 입력 데이터 수집 현황</b>", "=" * 35,
             f"• 누적 이벤트: {data.get('total_events', 0):,}건",
             f"• 참여 피어: {data.get('unique_peers', 0)}명"]
    by_feature = data.get("by_feature", [])
    if by_feature:
        lines.append("• 기능별 사용량:")
        for f in by_feature[:12]:
            lines.append(f"   - {f['feature']}: {f['count']:,}건")
    recent = data.get("last_7d", 0)
    lines.append(f"• 최근 7일: {recent:,}건")
    lines.append("=" * 35)
    return "\n".join(lines)


def consent_status_text() -> str:
    on = get_collection_consent()
    return ("📡 <b>데이터 수집 설정</b>\n"
            f"현재 상태: {'🟢 ON (익명 사용 데이터 전송 중)' if on else '⚪ OFF'}\n\n"
            "• <code>/수집 온</code> — 익명화된 명령 사용 이벤트 수집 동의\n"
            "• <code>/수집 오프</code> — 수집 중단\n"
            "• <code>/수집 현황</code> — 중앙 서버 누적 통계 보기\n\n"
            "수집 항목: 기기 해시(peer_id), 사용한 기능명, 데이터셋 메타(행 수·도메인). "
            "원본 데이터·계정정보·API키는 전송하지 않습니다.")
