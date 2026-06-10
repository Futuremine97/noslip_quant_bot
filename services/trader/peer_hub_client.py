#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Client for the Peer Hub: connects Claude Code plugin users through the
central prediction API (presence registry + shared alpha-signal feed).

Used by mcp_server.py tools and the Claude Code /squad command.
"""
import hashlib
import json
import os
import platform
import uuid
from pathlib import Path

import requests

from leaderboard_sync import get_prediction_api_url, get_headers

ROOT_DIR = Path(__file__).resolve().parents[2]
PEER_IDENTITY_PATH = ROOT_DIR / "services" / "trader" / "model_cache" / "peer_identity.json"


def get_local_peer_identity() -> dict:
    """Stable local identity: persisted random peer_id + editable nickname."""
    if PEER_IDENTITY_PATH.exists():
        try:
            with open(PEER_IDENTITY_PATH, "r", encoding="utf-8") as f:
                ident = json.load(f)
            if ident.get("peer_id"):
                return ident
        except Exception:
            pass
    seed = f"{platform.node()}-{uuid.getnode()}"
    peer_id = "peer_" + hashlib.sha256(seed.encode()).hexdigest()[:12]
    ident = {"peer_id": peer_id, "nickname": os.getenv("NOSLIP_NICKNAME", platform.node() or "anon"), "bio": ""}
    save_local_peer_identity(ident)
    return ident


def save_local_peer_identity(ident: dict):
    PEER_IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PEER_IDENTITY_PATH, "w", encoding="utf-8") as f:
        json.dump(ident, f, ensure_ascii=False, indent=2)


def register_peer(nickname: str = "", bio: str = "") -> str:
    """Register/heartbeat this installation on the Peer Hub."""
    ident = get_local_peer_identity()
    if nickname.strip():
        ident["nickname"] = nickname.strip()[:40]
    if bio.strip():
        ident["bio"] = bio.strip()[:200]
    save_local_peer_identity(ident)
    url = f"{get_prediction_api_url()}/peers/register"
    try:
        res = requests.post(url, json=ident, headers=get_headers(), timeout=10)
        res.raise_for_status()
        data = res.json()
        return (f"✅ Peer Hub 등록 완료: '{ident['nickname']}' (peer_id: {ident['peer_id']})\n"
                f"이제 시그널 공유(share_alpha_signal)와 피어 목록(list_peers)을 사용할 수 있습니다.")
    except Exception as e:
        return f"⚠️ Peer Hub 등록 실패 ({url}): {e}"


def list_peers() -> str:
    """Human-readable roster of connected plugin users with presence + ranking."""
    url = f"{get_prediction_api_url()}/peers"
    try:
        res = requests.get(url, headers=get_headers(), timeout=10)
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        return f"⚠️ 피어 목록 조회 실패 ({url}): {e}"
    peers = data.get("peers", [])
    if not peers:
        return "👥 아직 등록된 피어가 없습니다. register_peer로 첫 번째 멤버가 되어 보세요!"
    lines = [f"👥 No Slip Quant Squad — 총 {len(peers)}명 "
             f"(최근 {data.get('online_window_min', 10)}분 내 활동 = 🟢)"]
    lines.append("=" * 40)
    for p in peers:
        dot = "🟢" if p.get("online") else "⚪"
        score = p.get("best_leaderboard_score")
        score_str = f" | 🏆 best score {score:.4f}" if isinstance(score, (int, float)) else ""
        bio = f" — {p['bio']}" if p.get("bio") else ""
        lines.append(f"{dot} {p['nickname']}{score_str}{bio}")
        lines.append(f"    last seen: {p.get('last_seen', '?')}")
    return "\n".join(lines)


def share_alpha_signal(symbol: str, direction: str, confidence: float, thesis: str = "") -> str:
    """Broadcast an alpha signal to every connected plugin user."""
    ident = get_local_peer_identity()
    payload = {
        "peer_id": ident["peer_id"],
        "nickname": ident["nickname"],
        "symbol": symbol,
        "direction": direction,
        "confidence": confidence,
        "thesis": thesis,
    }
    url = f"{get_prediction_api_url()}/signals/share"
    try:
        res = requests.post(url, json=payload, headers=get_headers(), timeout=10)
        res.raise_for_status()
        return f"📡 시그널 공유 완료: {direction.upper()} {symbol} (확신도 {confidence:.0f}%)"
    except Exception as e:
        return f"⚠️ 시그널 공유 실패 ({url}): {e}"


def view_signal_feed(symbol: str = "", limit: int = 20) -> str:
    """Read the shared alpha-signal feed with per-symbol consensus."""
    url = f"{get_prediction_api_url()}/signals"
    params = {"limit": limit}
    if symbol.strip():
        params["symbol"] = symbol.strip()
    try:
        res = requests.get(url, params=params, headers=get_headers(), timeout=10)
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        return f"⚠️ 시그널 피드 조회 실패 ({url}): {e}"
    signals = data.get("signals", [])
    if not signals:
        return "📭 공유된 시그널이 아직 없습니다. share_alpha_signal로 첫 시그널을 올려 보세요!"
    emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}
    lines = [f"📡 Alpha Signal Feed (최근 {len(signals)}건)", "=" * 40]
    for s in signals:
        e = emoji.get(s["direction"], "⚪")
        lines.append(f"{e} [{s['direction']}] {s['symbol']} ({s['confidence']:.0f}%) — {s['nickname']}")
        if s.get("thesis"):
            lines.append(f"    💬 {s['thesis']}")
        lines.append(f"    🕒 {s['created_at']}")
    consensus = data.get("consensus", {})
    if consensus:
        lines.append("=" * 40)
        lines.append("🤝 심볼별 컨센서스:")
        for sym, c in consensus.items():
            lines.append(f"  • {sym}: BUY {c.get('BUY', 0)} / SELL {c.get('SELL', 0)} / HOLD {c.get('HOLD', 0)}")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="No Slip Peer Hub client")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_reg = sub.add_parser("register"); p_reg.add_argument("--nickname", default=""); p_reg.add_argument("--bio", default="")
    sub.add_parser("peers")
    p_sig = sub.add_parser("share")
    p_sig.add_argument("symbol"); p_sig.add_argument("direction", choices=["BUY", "SELL", "HOLD", "buy", "sell", "hold"])
    p_sig.add_argument("confidence", type=float); p_sig.add_argument("--thesis", default="")
    p_feed = sub.add_parser("feed"); p_feed.add_argument("--symbol", default=""); p_feed.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    if args.cmd == "register":
        print(register_peer(args.nickname, args.bio))
    elif args.cmd == "peers":
        print(list_peers())
    elif args.cmd == "share":
        print(share_alpha_signal(args.symbol, args.direction, args.confidence, args.thesis))
    elif args.cmd == "feed":
        print(view_signal_feed(args.symbol, args.limit))
