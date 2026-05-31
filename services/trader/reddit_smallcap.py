#!/usr/bin/env python3

from __future__ import annotations

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
REDDIT_SMALLCAP_CACHE_PATH = MODEL_CACHE_DIR / "reddit_smallcap_daytrading.json"
REDDIT_SMALLCAP_CACHE_MAX_AGE_SECONDS = int(
    os.getenv("REDDIT_SMALLCAP_CACHE_MAX_AGE_SECONDS", str(60 * 45))
)
REDDIT_SMALLCAP_POST_LIMIT = int(os.getenv("REDDIT_SMALLCAP_POST_LIMIT", "40"))
REDDIT_DAYTRADING_JSON_URL = "https://www.reddit.com/r/Daytrading/.json"
REDDIT_DAYTRADING_OLD_JSON_URL = "https://old.reddit.com/r/Daytrading/.json"
USER_AGENT = os.getenv(
    "REDDIT_FETCH_USER_AGENT",
    "no-slip/1.0 (portfolio small-cap analyzer)",
)

COMMON_UPPERCASE_WORDS = {
    "A",
    "AI",
    "ALL",
    "AND",
    "ARE",
    "ATH",
    "BTC",
    "CEO",
    "CPI",
    "DAY",
    "DD",
    "DJT",
    "ETF",
    "EV",
    "FDA",
    "FED",
    "FOMO",
    "GDP",
    "GOAT",
    "HODL",
    "IMO",
    "IRAN",
    "IRAQ",
    "IPO",
    "ITM",
    "LLM",
    "LOW",
    "LOL",
    "LFG",
    "MOON",
    "MSTR",
    "NEW",
    "NO",
    "NQ",
    "NVDA",
    "OK",
    "OTC",
    "PDT",
    "PM",
    "PNL",
    "QQQ",
    "ROI",
    "RSI",
    "SEC",
    "SMB",
    "SPX",
    "THAT",
    "THE",
    "THIS",
    "TLDR",
    "TODAY",
    "USA",
    "USD",
    "VIX",
    "VWAP",
    "YOLO",
}

KEYWORD_GROUPS = {
    "small_cap": ("small cap", "small-cap", "micro cap", "micro-cap", "nano cap", "nano-cap"),
    "low_float": ("low float", "low-float", "float rotation"),
    "squeeze": ("squeeze", "short squeeze", "gamma squeeze"),
    "momentum": ("breakout", "runner", "parabolic", "momentum", "halt", "gapper"),
}


def utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def safe_float(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return None
    return numeric


def load_cached_reddit_smallcap() -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(REDDIT_SMALLCAP_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    fetched_at = str(payload.get("fetchedAt") or "")
    try:
        fetched_ts = datetime.fromisoformat(fetched_at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    if (time.time() - fetched_ts) > REDDIT_SMALLCAP_CACHE_MAX_AGE_SECONDS:
        return None
    return payload


def persist_cached_reddit_smallcap(payload: Dict[str, Any]) -> None:
    try:
        MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        REDDIT_SMALLCAP_CACHE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        return


def _fetch_json(url: str, limit: int) -> Dict[str, Any]:
    query = urllib.parse.urlencode({"limit": max(5, min(limit, 80)), "raw_json": 1})
    request = urllib.request.Request(
        f"{url}?{query}",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        body = response.read().decode("utf-8", errors="replace")
    return json.loads(body)


def _extract_tickers(text: str) -> List[str]:
    tickers = []
    for match in re.findall(r"\b[A-Z]{2,5}\b", text or ""):
        if match in COMMON_UPPERCASE_WORDS:
            continue
        tickers.append(match)
    return tickers


def _keyword_hits(text: str) -> Counter:
    lowered = (text or "").lower()
    hits = Counter()
    for label, keywords in KEYWORD_GROUPS.items():
        for keyword in keywords:
            if keyword in lowered:
                hits[label] += 1
    return hits


def build_reddit_smallcap_snapshot(force_refresh: bool = False) -> Dict[str, Any]:
    if not force_refresh:
        cached = load_cached_reddit_smallcap()
        if cached is not None:
            return cached

    last_error = None
    payload: Optional[Dict[str, Any]] = None
    for url in (REDDIT_DAYTRADING_JSON_URL, REDDIT_DAYTRADING_OLD_JSON_URL):
        try:
            payload = _fetch_json(url, REDDIT_SMALLCAP_POST_LIMIT)
            break
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            continue

    if payload is None:
        result = {
            "status": "unavailable",
            "source": "reddit",
            "subreddit": "Daytrading",
            "fetchedAt": utc_now_iso(),
            "heatScore": None,
            "regime": "unknown",
            "postsAnalyzed": 0,
            "smallCapPosts": 0,
            "topTickers": [],
            "topThemes": [],
            "error": last_error or "Unable to load Reddit Daytrading JSON",
            "path": str(REDDIT_SMALLCAP_CACHE_PATH),
        }
        persist_cached_reddit_smallcap(result)
        return result

    posts = (((payload or {}).get("data") or {}).get("children") or [])
    ticker_counter: Counter = Counter()
    theme_counter: Counter = Counter()
    small_cap_posts = 0
    low_float_posts = 0
    squeeze_posts = 0
    momentum_posts = 0
    sample_posts: List[Dict[str, Any]] = []

    for raw_post in posts[: max(5, min(len(posts), REDDIT_SMALLCAP_POST_LIMIT))]:
        data = raw_post.get("data") or {}
        title = str(data.get("title") or "")
        selftext = str(data.get("selftext") or "")
        permalink = str(data.get("permalink") or "")
        score = int(data.get("score") or 0)
        comments = int(data.get("num_comments") or 0)
        combined = f"{title}\n{selftext}"
        keyword_hits = _keyword_hits(combined)
        if keyword_hits:
            small_cap_posts += 1
        low_float_posts += keyword_hits.get("low_float", 0) > 0
        squeeze_posts += keyword_hits.get("squeeze", 0) > 0
        momentum_posts += keyword_hits.get("momentum", 0) > 0

        for ticker in _extract_tickers(title):
            ticker_counter[ticker] += 2
        for ticker in _extract_tickers(selftext):
            ticker_counter[ticker] += 1
        for key, count in keyword_hits.items():
            theme_counter[key] += count

        if keyword_hits and len(sample_posts) < 8:
            sample_posts.append(
                {
                    "title": title,
                    "score": score,
                    "comments": comments,
                    "permalink": f"https://www.reddit.com{permalink}" if permalink else None,
                    "themes": [key for key, count in keyword_hits.items() if count > 0],
                }
            )

    analyzed = max(1, min(len(posts), REDDIT_SMALLCAP_POST_LIMIT))
    theme_intensity = (
        small_cap_posts * 0.45
        + low_float_posts * 0.22
        + squeeze_posts * 0.20
        + momentum_posts * 0.13
    ) / analyzed
    top_tickers = [
        {"symbol": symbol, "mentions": mentions}
        for symbol, mentions in ticker_counter.most_common(8)
    ]
    top_themes = [
        {"theme": theme, "hits": hits}
        for theme, hits in theme_counter.most_common(6)
    ]
    heat_score = max(0.0, min(1.0, theme_intensity))
    regime = (
        "small-cap risk-on"
        if heat_score >= 0.56
        else "small-cap active"
        if heat_score >= 0.34
        else "small-cap muted"
    )

    result = {
        "status": "ok",
        "source": "reddit",
        "subreddit": "Daytrading",
        "fetchedAt": utc_now_iso(),
        "heatScore": heat_score,
        "regime": regime,
        "postsAnalyzed": analyzed,
        "smallCapPosts": small_cap_posts,
        "lowFloatPosts": low_float_posts,
        "squeezePosts": squeeze_posts,
        "momentumPosts": momentum_posts,
        "topTickers": top_tickers,
        "topThemes": top_themes,
        "samplePosts": sample_posts,
        "path": str(REDDIT_SMALLCAP_CACHE_PATH),
        "error": None,
    }
    persist_cached_reddit_smallcap(result)
    return result

