#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Korean securities cross-broker arbitrage engine (READ/PREVIEW ONLY).

Scans price discrepancies across Korean brokerage OPEN APIs (KIS, Kiwoom,
KB, Shinhan, NH, Hana, Toss, Yuanta — via services/trader/brokers/*) and
finds three classes of opportunities:

  1. two_way   (2자간)   : 동일 종목을 서로 다른 증권사/체결 경로(KRX vs NXT
                           대체거래소 라우팅, 시세 지연)에서 매수/매도
  2. route     (3자간)   : 설정된 다리(leg) 체인 — 예: ETF ↔ 구성 바스켓 NAV,
                           현물 ↔ 선물 베이시스 (whale_config.json 라우트)
  3. multi     (N자간)   : 가격 그래프에서 Bellman-Ford 음수 사이클 탐지 —
                           임의 개수의 브로커/종목을 거치는 순환 차익

SAFETY (SECURITY_BROKER.md 준수):
  * 이 모듈은 시세 조회와 주문 '프리뷰'(prepare_order)만 생성한다.
  * submit_order / cancel_order 를 절대 호출하지 않는다 — 실제 주문 제출은
    계좌 소유자가 증권사 HTS/MTS 또는 별도의 3중 게이트 경로로 직접 수행.
  * 수수료·증권거래세·슬리피지를 차감한 net edge 기준으로만 기회를 보고.

CLI:
  korea_arbitrage.py scan [--mode two|route|multi|all] [--demo]
  korea_arbitrage.py report [--demo]      # Telegram-ready HTML report
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT_DIR / "services" / "trader" / "model_cache" / "whale_config.json"

DEFAULT_CONFIG = {
    "providers": ["kis", "kiwoom", "kb", "shinhan", "nh", "hana"],
    "watchlist": ["005930", "000660", "035420", "373220", "005380"],
    # 비용 모델 (basis points, 1bp = 0.01%)
    "commission_bps": 1.5,        # 온라인 위탁수수료 (브로커별 override 가능)
    "commission_bps_overrides": {},
    "sell_tax_bps": 15.0,         # 증권거래세+농특세 (매도 시)
    "slippage_bps": 2.0,          # 체결 슬리피지 가정 (leg당)
    "min_edge_bps": 5.0,          # 이 순마진(bps) 미만 기회는 무시
    # 3자간/멀티 라우트: 명시적 변환 다리 (예: ETF<->바스켓, 현물<->선물)
    # ratio: from 1단위 -> to ratio단위, fee_bps: 변환 비용(괴리 청산 비용)
    "routes": [],
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r") as f:
                cfg.update(json.load(f).get("korea_arbitrage", {}))
    except Exception:
        pass
    return cfg


# ----------------- Quote Layer -----------------

@dataclass
class Quote:
    provider: str
    symbol: str
    price: float


def fetch_quote_matrix(providers: list[str], symbols: list[str]) -> tuple[dict, list[str]]:
    """{symbol: {provider: price}} from live broker OPEN APIs. Read-only.
    Providers that are disabled/unconfigured are skipped (errors collected)."""
    from brokers.service import get_broker

    matrix: dict[str, dict[str, float]] = {s: {} for s in symbols}
    errors: list[str] = []
    for p in providers:
        try:
            client = get_broker(p)
            payload = client.get_prices(list(symbols))
            rows = payload.get("result") or payload.get("prices") or []
            for row in rows:
                sym = str(row.get("symbol", "")).strip()
                px = row.get("lastPrice") or row.get("price")
                if sym in matrix and px is not None:
                    matrix[sym][p] = float(px)
        except Exception as e:
            errors.append(f"{p}: {type(e).__name__}: {e}")
    return matrix, errors


def demo_quote_matrix(symbols: list[str] | None = None) -> tuple[dict, list[str]]:
    """Synthetic matrix with KRX/NXT-style micro-discrepancies (offline tests)."""
    base = {"005930": 72000.0, "000660": 198000.0, "035420": 215000.0,
            "373220": 412000.0, "005380": 252000.0}
    # KRX vs NXT 라우팅 괴리 이벤트 가정 (intraday spike 수준)
    bumps = {"kis": 0.0, "kiwoom": +0.0045, "kb": -0.0035, "shinhan": +0.0008,
             "nh": -0.0006, "hana": +0.0002}
    symbols = symbols or list(base)
    matrix = {}
    for s in symbols:
        px = base.get(s, 50000.0)
        matrix[s] = {p: round(px * (1 + b), 0) for p, b in bumps.items()}
    return matrix, []


# ----------------- Cost Model -----------------

def leg_cost_bps(cfg: dict, provider: str, side: str) -> float:
    comm = float(cfg.get("commission_bps_overrides", {}).get(provider,
                 cfg.get("commission_bps", 1.5)))
    slip = float(cfg.get("slippage_bps", 2.0))
    tax = float(cfg.get("sell_tax_bps", 15.0)) if side.upper() == "SELL" else 0.0
    return comm + slip + tax


# ----------------- 1. Two-way (2자간) -----------------

def scan_two_way(matrix: dict, cfg: dict) -> list[dict]:
    opps = []
    for symbol, quotes in matrix.items():
        if len(quotes) < 2:
            continue
        buy_p, buy_px = min(quotes.items(), key=lambda kv: kv[1])
        sell_p, sell_px = max(quotes.items(), key=lambda kv: kv[1])
        if buy_p == sell_p:
            continue
        buy_cost = leg_cost_bps(cfg, buy_p, "BUY") / 10000.0
        sell_cost = leg_cost_bps(cfg, sell_p, "SELL") / 10000.0
        net = sell_px * (1 - sell_cost) - buy_px * (1 + buy_cost)
        edge_bps = net / buy_px * 10000.0
        if edge_bps >= float(cfg.get("min_edge_bps", 5.0)):
            opps.append({
                "mode": "two_way", "symbol": symbol,
                "buy": {"provider": buy_p, "price": buy_px},
                "sell": {"provider": sell_p, "price": sell_px},
                "gross_spread_bps": round((sell_px - buy_px) / buy_px * 10000, 2),
                "net_edge_bps": round(edge_bps, 2),
                "note": "동일 KRX 종목 — 브로커 간 시세/체결경로(KRX·NXT) 괴리",
            })
    return sorted(opps, key=lambda o: -o["net_edge_bps"])


# ----------------- 2. Configured routes (3자간+) -----------------

def scan_routes(matrix: dict, cfg: dict) -> list[dict]:
    """Evaluate explicit leg chains from config. Each route:
    {"name": "...", "legs": [{"provider","symbol","side","ratio"}...]}
    Start with 1.0 KRW notional; multiply through legs net of costs."""
    opps = []
    for route in cfg.get("routes", []):
        legs = route.get("legs", [])
        if len(legs) < 2:
            continue
        value = 1.0
        detail = []
        feasible = True
        for leg in legs:
            p, s, side = leg.get("provider"), leg.get("symbol"), str(leg.get("side", "BUY")).upper()
            ratio = float(leg.get("ratio", 1.0))
            px = matrix.get(s, {}).get(p)
            if px is None or px <= 0:
                feasible = False
                break
            cost = leg_cost_bps(cfg, p, side) / 10000.0
            if side == "BUY":     # KRW -> asset
                value = value / (px * (1 + cost)) * ratio
            else:                 # asset -> KRW
                value = value * px * (1 - cost) * ratio
            detail.append({"provider": p, "symbol": s, "side": side,
                           "price": px, "ratio": ratio})
        if not feasible:
            continue
        edge_bps = (value - 1.0) * 10000.0
        if edge_bps >= float(cfg.get("min_edge_bps", 5.0)):
            opps.append({"mode": "route", "name": route.get("name", "route"),
                         "legs": detail, "net_edge_bps": round(edge_bps, 2)})
    return sorted(opps, key=lambda o: -o["net_edge_bps"])


# ----------------- 3. Multi-party negative cycles (N자간) -----------------

def _build_graph(matrix: dict, cfg: dict) -> list[tuple[str, str, float, dict]]:
    """Directed edges (u, v, -log(net_rate), meta). Nodes: 'KRW' and
    '<symbol>@<provider>'. Cross-provider transfer edges allow a position
    bought at one venue to be sold at another (동일 종목 계좌 대체)."""
    edges = []
    for symbol, quotes in matrix.items():
        for p, px in quotes.items():
            node = f"{symbol}@{p}"
            bcost = leg_cost_bps(cfg, p, "BUY") / 10000.0
            scost = leg_cost_bps(cfg, p, "SELL") / 10000.0
            rate_buy = 1.0 / (px * (1 + bcost))       # KRW -> asset
            rate_sell = px * (1 - scost)              # asset -> KRW
            edges.append(("KRW", node, -math.log(rate_buy),
                          {"action": "BUY", "provider": p, "symbol": symbol, "price": px}))
            edges.append((node, "KRW", -math.log(rate_sell),
                          {"action": "SELL", "provider": p, "symbol": symbol, "price": px}))
        # 동일 종목 브로커 간 이동 (대체출고 — 비용 0 가정, 시간 리스크는 note)
        provs = list(quotes)
        for a in provs:
            for b in provs:
                if a != b:
                    edges.append((f"{symbol}@{a}", f"{symbol}@{b}", 0.0,
                                  {"action": "TRANSFER", "symbol": symbol,
                                   "from": a, "to": b}))
    for route in cfg.get("routes", []):  # 라우트 다리도 그래프에 편입
        for leg in route.get("legs", []):
            if leg.get("edge_from") and leg.get("edge_to"):
                ratio = float(leg.get("ratio", 1.0))
                fee = float(leg.get("fee_bps", 0.0)) / 10000.0
                rate = ratio * (1 - fee)
                if rate > 0:
                    edges.append((leg["edge_from"], leg["edge_to"], -math.log(rate),
                                  {"action": "CONVERT", **leg}))
    return edges


def scan_negative_cycles(matrix: dict, cfg: dict, max_cycles: int = 3) -> list[dict]:
    """Bellman-Ford negative-cycle detection on -log(rate) graph."""
    edges = _build_graph(matrix, cfg)
    nodes = {u for u, _, _, _ in edges} | {v for _, v, _, _ in edges}
    dist = {n: 0.0 for n in nodes}   # all-zero init finds any reachable cycle
    pred: dict[str, tuple] = {}
    cycle_node = None
    for i in range(len(nodes)):
        updated = False
        for u, v, w, meta in edges:
            if dist[u] + w < dist[v] - 1e-12:
                dist[v] = dist[u] + w
                pred[v] = (u, meta)
                updated = True
                if i == len(nodes) - 1:
                    cycle_node = v
        if not updated:
            break
    if cycle_node is None:
        return []
    # walk back to isolate the cycle
    x = cycle_node
    for _ in range(len(nodes)):
        x = pred[x][0]
    cycle, seen = [], set()
    cur = x
    while cur not in seen:
        seen.add(cur)
        u, meta = pred[cur]
        cycle.append((u, cur, meta))
        cur = u
    cycle.reverse()
    log_sum = 0.0
    steps = []
    for u, v, meta in cycle:
        w = next(w for (eu, ev, w, em) in edges if eu == u and ev == v and em == meta)
        log_sum += w
        steps.append(meta)
    edge_bps = (math.exp(-log_sum) - 1.0) * 10000.0
    if edge_bps < float(cfg.get("min_edge_bps", 5.0)):
        return []
    return [{"mode": "multi", "cycle_length": len(steps), "steps": steps,
             "net_edge_bps": round(edge_bps, 2),
             "note": "Bellman-Ford 음수 사이클 — 다자간 순환 차익 후보"}]


# ----------------- Execution PLAN (preview only — never submits) -----------------

def build_execution_plan(opp: dict, notional_krw: float = 10_000_000) -> dict:
    """Translate an opportunity into broker order PREVIEWS via prepare_order.
    THIS NEVER SUBMITS ORDERS. Each leg is a validated preview the account
    owner must place manually (or through the separately gated live path)."""
    from brokers.service import prepare_broker_order

    legs = []
    if opp["mode"] == "two_way":
        qty = max(int(notional_krw // opp["buy"]["price"]), 1)
        leg_specs = [
            (opp["buy"]["provider"], opp["symbol"], "BUY", opp["buy"]["price"], qty),
            (opp["sell"]["provider"], opp["symbol"], "SELL", opp["sell"]["price"], qty),
        ]
    elif opp["mode"] == "route":
        leg_specs = [(l["provider"], l["symbol"], l["side"], l["price"],
                      max(int(notional_krw // l["price"]), 1)) for l in opp["legs"]]
    else:  # multi: only BUY/SELL steps are orderable
        leg_specs = [(s["provider"], s["symbol"], s["action"], s["price"],
                      max(int(notional_krw // s["price"]), 1))
                     for s in opp["steps"] if s.get("action") in ("BUY", "SELL")]

    for provider, symbol, side, price, qty in leg_specs:
        try:
            preview = prepare_broker_order(
                provider=provider, symbol=symbol, side=side,
                order_type="LIMIT", quantity=qty, price=price,
            )
            legs.append({"provider": provider, "symbol": symbol, "side": side,
                         "qty": qty, "price": price, "preview": preview})
        except Exception as e:
            legs.append({"provider": provider, "symbol": symbol, "side": side,
                         "qty": qty, "price": price,
                         "preview_error": f"{type(e).__name__}: {e}"})
    return {
        "opportunity": opp,
        "notional_krw": notional_krw,
        "legs": legs,
        "WARNING": ("⚠️ 프리뷰 전용 — 본 시스템은 주문을 제출하지 않습니다. "
                    "실제 체결은 계좌 소유자가 각 증권사에서 직접 수행하고, "
                    "레그 간 체결 시차/부분체결 리스크를 반드시 관리하세요."),
    }


# ----------------- Scan Orchestrator & Report -----------------

def run_scan(mode: str = "all", demo: bool = False,
             symbols: list[str] | None = None) -> dict:
    cfg = load_config()
    syms = symbols or cfg["watchlist"]
    if demo:
        matrix, errors = demo_quote_matrix(syms)
        cfg = {**cfg, "routes": cfg.get("routes") or _demo_routes()}
    else:
        matrix, errors = fetch_quote_matrix(cfg["providers"], syms)

    result = {"scanned_at": datetime.now().isoformat(), "mode": mode,
              "symbols": syms, "provider_errors": errors, "opportunities": []}
    if mode in ("two", "all"):
        result["opportunities"] += scan_two_way(matrix, cfg)
    if mode in ("route", "three", "all"):
        result["opportunities"] += scan_routes(matrix, cfg)
    if mode in ("multi", "all"):
        result["opportunities"] += scan_negative_cycles(matrix, cfg)
    result["opportunities"].sort(key=lambda o: -o["net_edge_bps"])
    return result


def _demo_routes() -> list[dict]:
    return [{
        "name": "ETF-바스켓 NAV 괴리 (예시)",
        "legs": [
            {"provider": "kis", "symbol": "005930", "side": "BUY", "ratio": 1.0},
            {"provider": "kiwoom", "symbol": "005930", "side": "SELL", "ratio": 1.0},
        ],
    }]


def generate_arbitrage_report(html: bool = True, demo: bool = False) -> str:
    r = run_scan("all", demo=demo)
    b, _b = ("<b>", "</b>") if html else ("", "")
    code, _code = ("<code>", "</code>") if html else ("", "")
    lines = [f"⚖️ {b}한국 증권사 차익거래 스캔{_b} (2자간/3자간/다자간)", "=" * 35]
    if not r["opportunities"]:
        lines.append("현재 비용(수수료+거래세+슬리피지) 차감 후 유효한 기회가 없습니다.")
    for o in r["opportunities"][:6]:
        if o["mode"] == "two_way":
            lines.append(f"🔁 {b}[2자간] {o['symbol']}{_b} — net {code}{o['net_edge_bps']:.1f}bp{_code}")
            lines.append(f"    매수 {o['buy']['provider'].upper()} @{o['buy']['price']:,.0f} → "
                         f"매도 {o['sell']['provider'].upper()} @{o['sell']['price']:,.0f}")
        elif o["mode"] == "route":
            lines.append(f"🔺 {b}[3자간 라우트] {o.get('name','')}{_b} — net {code}{o['net_edge_bps']:.1f}bp{_code}")
            for l in o["legs"]:
                lines.append(f"    {l['side']} {l['symbol']} @{l['provider'].upper()} {l['price']:,.0f}")
        else:
            lines.append(f"🔗 {b}[다자간 사이클 x{o['cycle_length']}]{_b} — net {code}{o['net_edge_bps']:.1f}bp{_code}")
            for s in o["steps"][:6]:
                lines.append(f"    {s.get('action')} {s.get('symbol','')} {s.get('provider', s.get('from',''))}")
    if r["provider_errors"]:
        lines.append(f"⚠️ 미연결 브로커 {len(r['provider_errors'])}곳 (자격증명/활성화 필요)")
    lines.append("=" * 35)
    lines.append("※ 조회·프리뷰 전용 — 주문 제출은 하지 않습니다. 투자 자문이 아닙니다.")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="Korean cross-broker arbitrage scanner (read-only)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--mode", default="all", choices=["two", "route", "three", "multi", "all"])
    s.add_argument("--demo", action="store_true")
    s.add_argument("--symbols", default="")
    r = sub.add_parser("report")
    r.add_argument("--demo", action="store_true")
    pl = sub.add_parser("plan")
    pl.add_argument("--demo", action="store_true")
    pl.add_argument("--notional", type=float, default=10_000_000)
    a = p.parse_args()

    if a.cmd == "scan":
        syms = [x.strip() for x in a.symbols.split(",") if x.strip()] or None
        print(json.dumps(run_scan(a.mode, demo=a.demo, symbols=syms),
                         ensure_ascii=False, indent=2))
    elif a.cmd == "report":
        print(generate_arbitrage_report(html=False, demo=a.demo))
    elif a.cmd == "plan":
        res = run_scan("all", demo=a.demo)
        if not res["opportunities"]:
            print("기회 없음 — plan을 생성하지 않습니다.")
            sys.exit(0)
        print(json.dumps(build_execution_plan(res["opportunities"][0], a.notional),
                         ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
