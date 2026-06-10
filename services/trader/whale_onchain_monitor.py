#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""On-chain whale deposit monitor & shake predictor for No Slip Quant.

Watches BTC/ETH blockchains (Blockchair free API) for transactions above a
USD threshold (default $1M), tags known exchange deposit addresses, scores
each event's "shake risk" (probability that the whale inflow precedes a
sharp price move), and learns from realized outcomes over time.

Why exchange inflows matter: whales moving large sums INTO exchanges often
precede sell pressure ("흔들기"); large OUTFLOWS to cold storage are usually
accumulation. This module turns that heuristic into a measurable, self-
calibrating signal.

Interfaces
----------
CLI      : whale_onchain_monitor.py scan | report | evaluate | daemon [--interval 300]
Telegram : /onchain (/온체인) -> generate_onchain_report()
Config   : whale_config.json -> {"onchain_whale": {"threshold_usd": 1000000, "alert_score": 70}}
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT_DIR = Path(__file__).resolve().parents[2]
DB_PATH = ROOT_DIR / "services" / "trader" / "model_cache" / "onchain_whales.sqlite3"
CONFIG_PATH = ROOT_DIR / "services" / "trader" / "model_cache" / "whale_config.json"

BLOCKCHAIR = "https://api.blockchair.com"
CHAINS = {"bitcoin": "BTC-USD", "ethereum": "ETH-USD"}

# Curated, well-known exchange hot/deposit wallets (extend freely).
KNOWN_EXCHANGE_ADDRESSES = {
    # --- Bitcoin ---
    "34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo": "Binance",
    "bc1qgdjqv0av3q56jvd82tkdjpy7gdp9ut8tlqmgrpmv24sq90ecnvqqjwvw97": "Binance",
    "3M219KR5vEneNb47ewrPfWyb5jQ2DjxRP6": "Binance",
    "1P5ZEDWTKTFGxQjZphgWPQUpe554WKDfHQ": "Binance",
    "3FupZp77ySr7jwoLYEJ9mwzJpvoNBXsBnE": "Huobi",
    "1LQv8aKtQoiY5M5zkaG8RWL7LMwNzNsLfb": "OKX",
    "3JZq4atUahhuA9rLhXLMhhTo133J9rF97j": "Bitfinex",
    "bc1qjasf9z3h7w3jspkhtgatgpyvvzgpa2wwd2lr0eh5tx44reyn2k7sfc27a4": "Coinbase",
    "395vMb5GfBe3GAWBzZcdgVMqduD86n7B6E": "Upbit",
    # --- Ethereum (lowercase) ---
    "0x28c6c06298d514db089934071355e5743bf21d60": "Binance",
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "Binance",
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": "Binance",
    "0x56eddb7aa87536c09ccc2793473599fd21a8b17f": "Binance",
    "0x71660c4005ba85c37ccec55d0c4493e66fe775d3": "Coinbase",
    "0x503828976d22510aad0201ac7ec88293211d23da": "Coinbase",
    "0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43": "Coinbase",
    "0x1522900b6dafac587d499a862861c0869be6e428": "Bitfinex",
    "0x742d35cc6634c0532925a3b844bc454e4438f44e": "Bitfinex",
    "0x5a52e96bacdabb82fd05763e25335261b270efcb": "Binance",
    "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "OKX",
    "0x98ec059dc3adfbdd63429454aeb0c990fba4a128": "Upbit",
    "0xba826fec90cefdf6706858e5fbafcb27a290fbe0": "Upbit",
}


# ----------------- Config / DB -----------------

def get_onchain_config() -> dict:
    cfg = {"threshold_usd": 1_000_000, "alert_score": 70, "lookback_limit": 30}
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r") as f:
                cfg.update(json.load(f).get("onchain_whale", {}))
    except Exception:
        pass
    return cfg


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS whale_events (
                tx_hash TEXT PRIMARY KEY,
                chain TEXT NOT NULL,
                symbol TEXT NOT NULL,
                usd_value REAL NOT NULL,
                recipient TEXT,
                exchange TEXT,
                direction TEXT,           -- EXCHANGE_IN / UNKNOWN
                shake_score REAL,
                detected_at TEXT NOT NULL,
                price_at_event REAL,
                price_after_4h REAL,
                outcome_drop_pct REAL,
                outcome_labeled INTEGER DEFAULT 0
            )
        """)
        conn.commit()


# ----------------- Fetch Layer -----------------

def fetch_large_transactions(chain: str, threshold_usd: float, limit: int = 30) -> list[dict]:
    """Fetch recent transactions above threshold from Blockchair (no API key)."""
    url = f"{BLOCKCHAIR}/{chain}/transactions"
    params = {
        "q": f"value_usd({int(threshold_usd)}..)",
        "s": "time(desc)",
        "limit": min(limit, 100),
    }
    res = requests.get(url, params=params, timeout=20)
    res.raise_for_status()
    rows = res.json().get("data", [])
    out = []
    for r in rows:
        out.append({
            "tx_hash": r.get("hash") or r.get("transaction_hash", ""),
            "chain": chain,
            "usd_value": float(r.get("value_usd") or r.get("output_total_usd") or 0),
            "recipient": (r.get("recipient") or "").strip(),
            "time": r.get("time", ""),
        })
    return out


def fetch_eth_large_transactions(threshold_usd: float, limit: int = 30) -> list[dict]:
    """Ethereum variant (different field names on Blockchair)."""
    url = f"{BLOCKCHAIR}/ethereum/transactions"
    params = {
        "q": f"value_usd({int(threshold_usd)}..)",
        "s": "time(desc)",
        "limit": min(limit, 100),
    }
    res = requests.get(url, params=params, timeout=20)
    res.raise_for_status()
    rows = res.json().get("data", [])
    out = []
    for r in rows:
        out.append({
            "tx_hash": r.get("hash", ""),
            "chain": "ethereum",
            "usd_value": float(r.get("value_usd") or 0),
            "recipient": (r.get("recipient") or "").lower().strip(),
            "time": r.get("time", ""),
        })
    return out


def demo_transactions() -> list[dict]:
    """Synthetic events for offline testing (--demo)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return [
        {"tx_hash": "demo_btc_1", "chain": "bitcoin", "usd_value": 48_500_000,
         "recipient": "34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo", "time": now},
        {"tx_hash": "demo_btc_2", "chain": "bitcoin", "usd_value": 3_200_000,
         "recipient": "bc1qunknownwallet", "time": now},
        {"tx_hash": "demo_eth_1", "chain": "ethereum", "usd_value": 12_700_000,
         "recipient": "0x28c6c06298d514db089934071355e5743bf21d60", "time": now},
        {"tx_hash": "demo_eth_2", "chain": "ethereum", "usd_value": 1_900_000,
         "recipient": "0x98ec059dc3adfbdd63429454aeb0c990fba4a128", "time": now},
    ]


def get_spot_price(symbol: str) -> float | None:
    try:
        import yfinance as yf
        df = yf.download(symbol, period="1d", interval="1m", progress=False, auto_adjust=True)
        if df is not None and not df.empty:
            close = df["Close"]
            if hasattr(close, "iloc"):
                v = close.iloc[-1]
                return float(v.iloc[0]) if hasattr(v, "iloc") else float(v)
    except Exception:
        pass
    return None


# ----------------- Scoring & Prediction -----------------

def classify_event(ev: dict) -> dict:
    addr = ev.get("recipient", "")
    exchange = KNOWN_EXCHANGE_ADDRESSES.get(addr) or KNOWN_EXCHANGE_ADDRESSES.get(addr.lower())
    ev["exchange"] = exchange
    ev["direction"] = "EXCHANGE_IN" if exchange else "UNKNOWN"
    return ev


def shake_score(ev: dict, threshold_usd: float, recent_count: int) -> float:
    """Heuristic 0~100 risk score for '고래 흔들기' (whale-driven shakeout).

    Components: size vs threshold (log-scaled), exchange-inflow bonus,
    burst bonus when multiple whale txs land in the same scan window.
    """
    import math
    size_ratio = max(ev["usd_value"] / max(threshold_usd, 1), 1.0)
    size_pts = min(45.0, 15.0 * math.log10(size_ratio * 10))          # $1M→15, $10M→30, $100M→45
    exch_pts = 35.0 if ev["direction"] == "EXCHANGE_IN" else 5.0      # inflow to exchange = sell-side risk
    burst_pts = min(20.0, 4.0 * max(recent_count - 1, 0))             # clustered whale activity
    return round(min(100.0, size_pts + exch_pts + burst_pts), 1)


def predict_shake_probability(score: float) -> tuple[float, int]:
    """Data-driven P(drop >= 1% within 4h | score bucket) from labeled history.

    Laplace-smoothed; falls back to score/100 prior when history is thin.
    Returns (probability_pct, sample_count).
    """
    init_db()
    bucket_lo = (score // 20) * 20
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("""
            SELECT COUNT(*) AS n,
                   SUM(CASE WHEN outcome_drop_pct >= 1.0 THEN 1 ELSE 0 END) AS hits
            FROM whale_events
            WHERE outcome_labeled = 1 AND shake_score >= ? AND shake_score < ?
        """, (bucket_lo, bucket_lo + 20)).fetchone()
    n, hits = (row[0] or 0), (row[1] or 0)
    prior = score / 100.0
    prob = (hits + 2 * prior) / (n + 2)   # Laplace smoothing toward heuristic prior
    return round(prob * 100, 1), n


def evaluate_outcomes() -> int:
    """Label past events: did price drop >=1% within 4h of detection?
    Run periodically (daemon does this automatically) to calibrate predictions."""
    init_db()
    labeled = 0
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT tx_hash, symbol, detected_at, price_at_event FROM whale_events
            WHERE outcome_labeled = 0 AND detected_at <= ? AND price_at_event IS NOT NULL
        """, (cutoff,)).fetchall()
        if not rows:
            return 0
        try:
            import yfinance as yf
        except Exception:
            return 0
        for r in rows:
            try:
                t0 = datetime.fromisoformat(r["detected_at"])
                df = yf.download(r["symbol"], start=t0.strftime("%Y-%m-%d"),
                                 interval="1h", progress=False, auto_adjust=True)
                if df is None or df.empty:
                    continue
                if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
                    df.columns = df.columns.get_level_values(0)
                idx = df.index.tz_localize(None) if df.index.tz is not None else df.index
                t0n = t0.replace(tzinfo=None)
                window = df[(idx > t0n) & (idx <= t0n + timedelta(hours=4))]
                if window.empty:
                    continue
                low = float(window["Close"].min())
                drop_pct = (1 - low / float(r["price_at_event"])) * 100
                conn.execute("""
                    UPDATE whale_events
                    SET price_after_4h = ?, outcome_drop_pct = ?, outcome_labeled = 1
                    WHERE tx_hash = ?
                """, (low, round(drop_pct, 3), r["tx_hash"]))
                labeled += 1
            except Exception as e:
                print(f"⚠️ outcome label failed for {r['tx_hash']}: {e}")
        conn.commit()
    return labeled


# ----------------- Scan & Report -----------------

def scan_once(demo: bool = False) -> list[dict]:
    """Fetch, classify, score, persist. Returns newly inserted events."""
    cfg = get_onchain_config()
    threshold = float(cfg["threshold_usd"])
    init_db()

    if demo:
        raw = demo_transactions()
    else:
        raw = []
        try:
            raw += fetch_large_transactions("bitcoin", threshold, cfg["lookback_limit"])
        except Exception as e:
            print(f"⚠️ BTC fetch failed: {e}")
        try:
            raw += fetch_eth_large_transactions(threshold, cfg["lookback_limit"])
        except Exception as e:
            print(f"⚠️ ETH fetch failed: {e}")

    new_events = []
    prices: dict[str, float | None] = {}
    with sqlite3.connect(DB_PATH) as conn:
        per_chain_counts: dict[str, int] = {}
        for ev in raw:
            per_chain_counts[ev["chain"]] = per_chain_counts.get(ev["chain"], 0) + 1
        for ev in raw:
            if not ev["tx_hash"]:
                continue
            exists = conn.execute("SELECT 1 FROM whale_events WHERE tx_hash = ?",
                                  (ev["tx_hash"],)).fetchone()
            if exists:
                continue
            ev = classify_event(ev)
            ev["symbol"] = CHAINS.get(ev["chain"], "BTC-USD")
            ev["shake_score"] = shake_score(ev, threshold, per_chain_counts[ev["chain"]])
            if ev["symbol"] not in prices:
                prices[ev["symbol"]] = None if demo else get_spot_price(ev["symbol"])
            conn.execute("""
                INSERT OR IGNORE INTO whale_events
                (tx_hash, chain, symbol, usd_value, recipient, exchange, direction,
                 shake_score, detected_at, price_at_event)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ev["tx_hash"], ev["chain"], ev["symbol"], ev["usd_value"],
                  ev["recipient"], ev["exchange"], ev["direction"], ev["shake_score"],
                  datetime.now(timezone.utc).isoformat(), prices[ev["symbol"]]))
            new_events.append(ev)
        conn.commit()
    return new_events


def generate_onchain_report(html: bool = True, demo: bool = False) -> str:
    """Telegram-ready report: latest whale events + calibrated shake predictions."""
    cfg = get_onchain_config()
    new_events = scan_once(demo=demo)
    evaluate_outcomes()

    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM whale_events ORDER BY detected_at DESC LIMIT 8
        """).fetchall()

    b, _b = ("<b>", "</b>") if html else ("", "")
    code, _code = ("<code>", "</code>") if html else ("", "")
    lines = [f"🐋 {b}온체인 고래 감지 리포트{_b} (임계값 ${cfg['threshold_usd']:,.0f}+)",
             "=" * 35]
    if not rows:
        lines.append("최근 감지된 고래 트랜잭션이 없습니다.")
    for r in rows:
        prob, n = predict_shake_probability(r["shake_score"])
        coin = "BTC" if r["chain"] == "bitcoin" else "ETH"
        where = f"{r['exchange']} 입금" if r["exchange"] else "미상 지갑"
        risk = "🚨" if r["shake_score"] >= cfg["alert_score"] else ("⚠️" if r["shake_score"] >= 50 else "👀")
        lines.append(f"{risk} {b}{coin}{_b} ${r['usd_value']:,.0f} → {where}")
        lines.append(f"    흔들기 점수 {code}{r['shake_score']:.0f}/100{_code}"
                     f" | 4h 내 -1% 확률 {code}{prob:.0f}%{_code}"
                     f" (학습표본 {n}건)")
        if r["outcome_labeled"]:
            lines.append(f"    실현 결과: 4h 최대 하락 {r['outcome_drop_pct']:+.2f}%")
    lines.append("=" * 35)
    lines.append(f"신규 감지 {len(new_events)}건 | 거래소 입금 = 매도압력 위험 신호")
    lines.append("※ 자동 수집·학습 기반 참고 지표이며 투자 자문이 아닙니다.")
    return "\n".join(lines)


def send_telegram_alert(text: str):
    from dotenv import load_dotenv
    load_dotenv(ROOT_DIR / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_ids = [c.strip() for c in os.getenv("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
    if not token or not chat_ids:
        return
    for chat_id in chat_ids:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                          timeout=10)
        except Exception as e:
            print(f"❌ Telegram alert failed: {e}")


def run_daemon(interval: int = 300):
    """Poll chains, alert on high-score events, periodically label outcomes."""
    cfg = get_onchain_config()
    print(f"🐋 On-chain whale daemon started (every {interval}s, "
          f"threshold ${cfg['threshold_usd']:,.0f}, alert score {cfg['alert_score']})")
    cycle = 0
    while True:
        try:
            events = scan_once()
            for ev in events:
                if ev["shake_score"] >= cfg["alert_score"]:
                    prob, n = predict_shake_probability(ev["shake_score"])
                    coin = "BTC" if ev["chain"] == "bitcoin" else "ETH"
                    where = f"{ev['exchange']} 입금" if ev["exchange"] else "미상 지갑"
                    send_telegram_alert(
                        f"🚨 <b>고래 흔들기 경보</b>\n"
                        f"🐋 {coin} <b>${ev['usd_value']:,.0f}</b> → {where}\n"
                        f"흔들기 점수 <code>{ev['shake_score']:.0f}/100</code> | "
                        f"4h 내 -1% 확률 <code>{prob:.0f}%</code> (표본 {n}건)\n"
                        f"tx: <code>{ev['tx_hash'][:24]}...</code>"
                    )
            cycle += 1
            if cycle % 4 == 0:  # label outcomes every ~4 cycles
                labeled = evaluate_outcomes()
                if labeled:
                    print(f"📐 labeled {labeled} past events for calibration")
        except Exception as e:
            print(f"⚠️ daemon cycle error: {e}")
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="On-chain whale deposit monitor (No Slip Quant)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_scan = sub.add_parser("scan", help="One-shot scan & persist")
    p_scan.add_argument("--demo", action="store_true")
    p_rep = sub.add_parser("report", help="Print whale report")
    p_rep.add_argument("--demo", action="store_true")
    sub.add_parser("evaluate", help="Label past event outcomes (calibration)")
    p_d = sub.add_parser("daemon", help="Continuous monitor + Telegram alerts")
    p_d.add_argument("--interval", type=int, default=300)
    args = parser.parse_args()

    if args.cmd == "scan":
        events = scan_once(demo=args.demo)
        print(f"🐋 {len(events)} new whale events")
        for ev in events:
            print(f"  {ev['chain']} ${ev['usd_value']:,.0f} -> "
                  f"{ev['exchange'] or 'unknown'} (score {ev['shake_score']})")
    elif args.cmd == "report":
        print(generate_onchain_report(html=False, demo=args.demo))
    elif args.cmd == "evaluate":
        print(f"📐 labeled {evaluate_outcomes()} events")
    elif args.cmd == "daemon":
        run_daemon(args.interval)


if __name__ == "__main__":
    main()
