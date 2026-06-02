#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sector correlation learning + daily recommended-sector message.

Tracks the 11 GICS sectors via SPDR sector ETFs. Each run:
  1) downloads recent daily prices,
  2) computes momentum / relative-strength and a today correlation matrix,
  3) blends the correlation matrix and per-sector momentum into a persisted
     EWMA "learned" state (so structure is smoothed/accumulated across days),
  4) ranks sectors and builds a Telegram recommendation message.

Run directly to push the daily message; import build_sector_report() to get
the message text on demand (e.g. from the Telegram bot).
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if not ROOT_DIR.exists() or not (ROOT_DIR / "services" / "trader").exists():
    ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=ROOT_DIR / ".env")
except Exception:
    pass

STATE_PATH = ROOT_DIR / "services" / "trader" / "model_cache" / "sector_correlation.json"
EWMA_ALPHA = 0.30  # weight on the newest observation when blending into learned state

# GICS sector -> (SPDR ETF, Korean label)
SECTOR_ETFS = {
    "XLK": "기술",
    "XLF": "금융",
    "XLV": "헬스케어",
    "XLE": "에너지",
    "XLI": "산업재",
    "XLY": "자유소비재",
    "XLP": "필수소비재",
    "XLU": "유틸리티",
    "XLB": "소재",
    "XLRE": "부동산",
    "XLC": "커뮤니케이션",
}
BENCHMARK = "SPY"


def _pair_key(a: str, b: str) -> str:
    return "|".join(sorted([a, b]))


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"sample_count": 0, "corr_ewma": {}, "momentum_ewma": {}}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _download_closes():
    """Return a DataFrame of daily closes for all sector ETFs + benchmark, or None."""
    import pandas as pd
    import yfinance as yf
    tickers = list(SECTOR_ETFS.keys()) + [BENCHMARK]
    data = yf.download(tickers, period="6mo", interval="1d", progress=False, auto_adjust=True)
    if data is None or len(data) == 0:
        return None
    # With multiple tickers yfinance returns a column MultiIndex (field, ticker).
    if isinstance(data.columns, pd.MultiIndex):
        if "Close" not in data.columns.get_level_values(0):
            return None
        closes = data["Close"]
    else:
        closes = data
    return closes.dropna(how="all").ffill()


def compute_and_learn() -> dict:
    """Compute today's sector metrics, blend into the persisted EWMA state, and
    return a snapshot dict with per-sector metrics and the learned correlation."""
    import numpy as np
    import pandas as pd

    closes = _download_closes()
    if closes is None or closes.empty:
        return {"error": "섹터 가격 데이터를 가져오지 못했습니다."}

    available = [t for t in SECTOR_ETFS if t in closes.columns]
    if len(available) < 3 or BENCHMARK not in closes.columns:
        return {"error": "충분한 섹터 데이터가 없습니다."}

    returns = closes[available + [BENCHMARK]].pct_change().dropna(how="all")
    recent = returns.tail(60)  # ~3 months of trading days for correlation

    # --- Today correlation matrix (sectors only) ---
    today_corr = recent[available].corr()

    # --- Per-sector momentum / relative strength / trend ---
    def _mom(series, lookback):
        s = series.dropna()
        if len(s) <= lookback:
            return 0.0
        return float(s.iloc[-1] / s.iloc[-1 - lookback] - 1.0)

    spy_mom20 = _mom(closes[BENCHMARK], 20)
    metrics = {}
    for t in available:
        s = closes[t].dropna()
        mom20 = _mom(closes[t], 20)
        mom60 = _mom(closes[t], 60)
        rs = mom20 - spy_mom20  # relative strength vs market
        sma50 = float(s.tail(50).mean()) if len(s) >= 50 else float(s.mean())
        price = float(s.iloc[-1])
        above_sma = price > sma50
        score = 0.6 * mom20 + 0.4 * rs + (0.01 if above_sma else -0.01)
        metrics[t] = {
            "label": SECTOR_ETFS[t],
            "price": price,
            "mom20": mom20,
            "mom60": mom60,
            "rs": rs,
            "above_sma50": above_sma,
            "score": score,
        }

    # --- Blend into persisted learned state (EWMA) ---
    state = _load_state()
    a = EWMA_ALPHA
    first = state.get("sample_count", 0) == 0

    corr_ewma = state.get("corr_ewma", {})
    for i, t1 in enumerate(available):
        for t2 in available[i + 1:]:
            k = _pair_key(t1, t2)
            today_val = float(today_corr.loc[t1, t2])
            if np.isnan(today_val):
                continue
            prev = corr_ewma.get(k)
            corr_ewma[k] = today_val if (prev is None) else (a * today_val + (1 - a) * prev)

    momentum_ewma = state.get("momentum_ewma", {})
    for t in available:
        prev = momentum_ewma.get(t)
        cur = metrics[t]["mom20"]
        momentum_ewma[t] = cur if (prev is None) else (a * cur + (1 - a) * prev)

    state.update({
        "updated_at": datetime.now().isoformat(),
        "sample_count": state.get("sample_count", 0) + 1,
        "corr_ewma": corr_ewma,
        "momentum_ewma": momentum_ewma,
    })
    _save_state(state)

    return {
        "metrics": metrics,
        "corr_ewma": corr_ewma,
        "sample_count": state["sample_count"],
        "spy_mom20": spy_mom20,
        "first_run": first,
    }


def _lowest_corr_peer(target: str, peers: list, corr_ewma: dict):
    """Return (peer_ticker, corr) with the lowest learned correlation to target."""
    best = None
    for p in peers:
        if p == target:
            continue
        c = corr_ewma.get(_pair_key(target, p))
        if c is None:
            continue
        if best is None or c < best[1]:
            best = (p, c)
    return best


def build_sector_report() -> str:
    """Run the learning step and return an HTML Telegram message string."""
    snap = compute_and_learn()
    if "error" in snap:
        return f"⚠️ 섹터 분석 실패: {snap['error']}"

    metrics = snap["metrics"]
    corr_ewma = snap["corr_ewma"]
    date_str = datetime.now().strftime("%Y-%m-%d")
    ranked = sorted(metrics.items(), key=lambda kv: kv[1]["score"], reverse=True)
    tickers = [t for t, _ in ranked]

    lines = [
        f"🧭 <b>[No Slip] 오늘의 추천 섹터 ({date_str})</b>",
        f"<i>11개 GICS 섹터 상관관계 학습 누적 {snap['sample_count']}일 · 벤치마크 SPY {snap['spy_mom20']*100:+.1f}%(20일)</i>",
        "=" * 40,
        "🟢 <b>비중확대 추천 (모멘텀·상대강도 상위)</b>",
    ]
    for rank, (t, m) in enumerate(ranked[:3], start=1):
        trend = "↑" if m["above_sma50"] else "↓"
        lines.append(
            f"  {rank}. <b>{m['label']} ({t})</b> — 20일 {m['mom20']*100:+.1f}% | "
            f"상대강도 {m['rs']*100:+.1f}%p | 추세 {trend}"
        )

    # Diversification hint using the learned correlation matrix.
    top_t = tickers[0]
    peer = _lowest_corr_peer(top_t, tickers, corr_ewma)
    if peer:
        p_label = metrics[peer[0]]["label"]
        lines.append("")
        lines.append("🛡️ <b>분산 힌트 (학습된 상관관계)</b>")
        lines.append(
            f"  • 1위 {metrics[top_t]['label']}와 상관 최저: "
            f"<b>{p_label} ({peer[0]})</b> (ρ={peer[1]:+.2f}) → 동반 편입 시 변동성 분산 효과"
        )

    lines.append("")
    lines.append("🔴 <b>비중축소 / 관망 (하위 섹터)</b>")
    for t, m in ranked[-2:]:
        lines.append(f"  • {m['label']} ({t}) — 20일 {m['mom20']*100:+.1f}% | 상대강도 {m['rs']*100:+.1f}%p")

    lines.append("=" * 40)
    lines.append("※ 섹터 ETF 모멘텀·상대강도 및 누적 학습 상관행렬 기반. 투자 참고용입니다.")
    return "\n".join(lines)


def send_telegram_message(text: str) -> bool:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_ids_str = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_ids_str:
        print("⚠️ Telegram 설정 누락 (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")
        return False

    import urllib.request
    chat_ids = [cid.strip() for cid in chat_ids_str.split(",") if cid.strip()]
    ok = True
    for cid in chat_ids:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": cid, "text": text, "parse_mode": "HTML"}
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
                print(f"✅ [Telegram] {cid} 전송 완료!")
        except Exception as e:
            print(f"❌ [Telegram] {cid} 전송 실패: {e}")
            ok = False
    return ok


def main():
    report = build_sector_report()
    print("\n--- Generated Sector Message ---")
    print(report)
    print("--------------------------------\n")
    send_telegram_message(report)


if __name__ == "__main__":
    main()
