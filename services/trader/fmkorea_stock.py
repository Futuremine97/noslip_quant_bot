#!/usr/bin/env python3

from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT_DIR = Path(__file__).resolve().parents[2]
MODEL_CACHE_DIR = ROOT_DIR / "services" / "trader" / "model_cache"
FMKOREA_STOCK_CACHE_PATH = MODEL_CACHE_DIR / "fmkorea_stock_surge.json"
FMKOREA_STOCK_CACHE_MAX_AGE_SECONDS = int(
    os.getenv("FMKOREA_STOCK_CACHE_MAX_AGE_SECONDS", str(60 * 45))
)
FMKOREA_STOCK_POST_LIMIT = int(os.getenv("FMKOREA_STOCK_POST_LIMIT", "60"))
FMKOREA_STOCK_URLS = (
    "https://www.fmkorea.com/index.php?mid=stock&act=rss",
    "https://m.fmkorea.com/index.php?mid=stock",
    "https://www.fmkorea.com/stock",
)
USER_AGENT = os.getenv(
    "FMKOREA_FETCH_USER_AGENT",
    "Mozilla/5.0 (compatible; no-slip/1.0; Korean retail surge analyzer)",
)

COMMON_UPPERCASE_WORDS = {
    "AI",
    "ALL",
    "AND",
    "CEO",
    "CPI",
    "ETF",
    "EV",
    "FED",
    "FOMC",
    "GDP",
    "IPO",
    "IR",
    "KRW",
    "KOSDAQ",
    "KOSPI",
    "NASDAQ",
    "NO",
    "OK",
    "PM",
    "Q",
    "QQQ",
    "SEC",
    "SPY",
    "THE",
    "USD",
    "VIX",
}

KOREAN_STOPWORDS = {
    "급등",
    "급등주",
    "상한가",
    "하한가",
    "오늘",
    "내일",
    "주식",
    "국장",
    "미장",
    "종목",
    "증시",
    "코스피",
    "코스닥",
    "반등",
    "매수",
    "매도",
    "단타",
    "뉴스",
    "속보",
}

SURGE_KEYWORDS = {
    "surge": ("급등", "폭등", "상한가", "따상", "불기둥", "날라", "날아", "펌핑"),
    "momentum": ("돌파", "신고가", "vi", "VI", "갭상", "갭상승", "강세", "랠리"),
    "squeeze": ("숏스퀴즈", "스퀴즈", "공매도", "숏커버", "쇼트커버"),
    "theme": ("테마", "재료", "수급", "호재", "뉴스", "공시"),
}


def utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def load_cached_fmkorea_stock() -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(FMKOREA_STOCK_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    fetched_at = str(payload.get("fetchedAt") or "")
    try:
        fetched_ts = datetime.fromisoformat(fetched_at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    if (time.time() - fetched_ts) > FMKOREA_STOCK_CACHE_MAX_AGE_SECONDS:
        return None
    return payload


def persist_cached_fmkorea_stock(payload: Dict[str, Any]) -> None:
    try:
        MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        FMKOREA_STOCK_CACHE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        return


def _fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        content_type = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(content_type, errors="replace")


def _clean_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_rss_titles(body: str) -> List[str]:
    titles = re.findall(r"<title[^>]*>(.*?)</title>", body or "", flags=re.I | re.S)
    cleaned = [_clean_text(title) for title in titles]
    return [title for title in cleaned if title and "FMKorea" not in title][:FMKOREA_STOCK_POST_LIMIT]


def _extract_html_titles(body: str) -> List[str]:
    candidates = re.findall(
        r"<a[^>]+href=[\"'][^\"']*(?:document_srl|/stock/)[^\"']*[\"'][^>]*>(.*?)</a>",
        body or "",
        flags=re.I | re.S,
    )
    cleaned: List[str] = []
    seen = set()
    for candidate in candidates:
        title = _clean_text(candidate)
        title = re.sub(r"\[[0-9]+\]\s*$", "", title).strip()
        if len(title) < 2 or title in seen:
            continue
        seen.add(title)
        cleaned.append(title)
        if len(cleaned) >= FMKOREA_STOCK_POST_LIMIT:
            break
    return cleaned


def _extract_titles(body: str) -> List[str]:
    titles = _extract_rss_titles(body)
    if len(titles) >= 5:
        return titles
    html_titles = _extract_html_titles(body)
    return html_titles or titles


def _keyword_hits(text: str) -> Counter:
    lowered = (text or "").lower()
    hits = Counter()
    for label, keywords in SURGE_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in lowered:
                hits[label] += 1
    return hits


def _extract_us_tickers(text: str) -> List[str]:
    tickers: List[str] = []
    for match in re.findall(r"(?<![A-Za-z0-9])\$?([A-Z]{1,5})(?![A-Za-z0-9])", text or ""):
        if match in COMMON_UPPERCASE_WORDS:
            continue
        tickers.append(match)
    return tickers


def _extract_kr_keywords(text: str) -> List[str]:
    tokens: List[str] = []
    cleaned = re.sub(r"[\[\](){}/:|,.'\"!?~·+%0-9A-Za-z]", " ", text or "")
    for token in re.findall(r"[가-힣]{2,12}", cleaned):
        if token in KOREAN_STOPWORDS:
            continue
        if any(stop in token for stop in ("합니다", "있나요", "같네요", "이유", "오늘")):
            continue
        tokens.append(token)
    return tokens[:6]


def fmkorea_symbol_heat(snapshot: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    normalized = str(symbol or "").upper()
    top_tickers = snapshot.get("topTickers") or []
    mentions = 0
    for entry in top_tickers:
        if str(entry.get("symbol") or "").upper() == normalized:
            mentions += int(entry.get("mentions") or 0)
    heat = max(0.0, min(1.0, float(snapshot.get("heatScore") or 0.0)))
    direct_score = max(0.0, min(1.0, mentions / 5.0))
    surge_score = max(direct_score, heat * 0.28 if mentions > 0 else 0.0)
    label = "direct Korean retail surge" if mentions >= 2 else "Korean retail watch" if mentions else "no direct Korean surge"
    return {
        "score": surge_score,
        "mentions": mentions,
        "label": label,
    }


def build_fmkorea_stock_snapshot(force_refresh: bool = False) -> Dict[str, Any]:
    if not force_refresh:
        cached = load_cached_fmkorea_stock()
        if cached is not None:
            return cached

    titles: List[str] = []
    source_url = None
    last_error = None
    for url in FMKOREA_STOCK_URLS:
        try:
            body = _fetch_text(url)
            titles = _extract_titles(body)
            if titles:
                source_url = url
                break
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, UnicodeDecodeError) as exc:
            last_error = str(exc)
            continue

    if not titles:
        result = {
            "status": "unavailable",
            "source": "fmkorea",
            "board": "stock",
            "sourceUrl": "https://www.fmkorea.com/stock",
            "fetchedAt": utc_now_iso(),
            "heatScore": None,
            "regime": "unknown",
            "postsAnalyzed": 0,
            "surgePosts": 0,
            "topTickers": [],
            "topKeywords": [],
            "topThemes": [],
            "samplePosts": [],
            "error": last_error or "Unable to load FMKorea stock board",
            "path": str(FMKOREA_STOCK_CACHE_PATH),
        }
        persist_cached_fmkorea_stock(result)
        return result

    ticker_counter: Counter = Counter()
    keyword_counter: Counter = Counter()
    theme_counter: Counter = Counter()
    surge_posts = 0
    sample_posts: List[Dict[str, Any]] = []

    for title in titles[: max(5, min(len(titles), FMKOREA_STOCK_POST_LIMIT))]:
        hits = _keyword_hits(title)
        is_surge = bool(hits)
        if is_surge:
            surge_posts += 1
        for ticker in _extract_us_tickers(title):
            ticker_counter[ticker] += 2 if is_surge else 1
        if is_surge:
            for keyword in _extract_kr_keywords(title):
                keyword_counter[keyword] += 1
        for key, count in hits.items():
            theme_counter[key] += count
        if is_surge and len(sample_posts) < 8:
            sample_posts.append(
                {
                    "title": title[:160],
                    "themes": [key for key, count in hits.items() if count > 0],
                }
            )

    analyzed = max(1, min(len(titles), FMKOREA_STOCK_POST_LIMIT))
    theme_intensity = (
        surge_posts * 0.58
        + theme_counter.get("momentum", 0) * 0.16
        + theme_counter.get("squeeze", 0) * 0.14
        + theme_counter.get("theme", 0) * 0.12
    ) / analyzed
    heat_score = max(0.0, min(1.0, theme_intensity))
    regime = (
        "Korean retail surge-on"
        if heat_score >= 0.52
        else "Korean retail active"
        if heat_score >= 0.28
        else "Korean retail muted"
    )

    result = {
        "status": "ok",
        "source": "fmkorea",
        "board": "stock",
        "sourceUrl": source_url or "https://www.fmkorea.com/stock",
        "fetchedAt": utc_now_iso(),
        "heatScore": heat_score,
        "regime": regime,
        "postsAnalyzed": analyzed,
        "surgePosts": surge_posts,
        "topTickers": [
            {"symbol": symbol, "mentions": mentions}
            for symbol, mentions in ticker_counter.most_common(8)
        ],
        "topKeywords": [
            {"keyword": keyword, "mentions": mentions}
            for keyword, mentions in keyword_counter.most_common(8)
        ],
        "topThemes": [
            {"theme": theme, "hits": hits}
            for theme, hits in theme_counter.most_common(6)
        ],
        "samplePosts": sample_posts,
        "path": str(FMKOREA_STOCK_CACHE_PATH),
        "error": None,
    }
    persist_cached_fmkorea_stock(result)
    return result

