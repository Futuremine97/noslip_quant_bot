from __future__ import annotations

import math
import os
import re
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from services.trader.map_store import MODEL_CACHE_DIR, today_market_date, today_market_timestamp_iso

HUMAN_BIAS_DB_PATH = MODEL_CACHE_DIR / "human_bias.sqlite3"
SHORT_WINDOW_DAYS = max(2, int(os.getenv("HUMAN_BIAS_SHORT_WINDOW_DAYS", "7")))
LONG_WINDOW_DAYS = max(SHORT_WINDOW_DAYS + 3, int(os.getenv("HUMAN_BIAS_LONG_WINDOW_DAYS", "45")))
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._/\- ]{0,79}$")
VALID_MARKET_MODES = {"sp500", "crypto"}


def _clip01(value: float) -> float:
    return float(max(0.0, min(1.0, value)))


def _safe_float(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def normalize_interest_symbol(raw_symbol: Any) -> str:
    normalized = " ".join(str(raw_symbol or "").strip().upper().split())
    if not normalized or not SYMBOL_RE.fullmatch(normalized):
        return ""
    return normalized


def normalize_market_mode(raw_market_mode: Any) -> str:
    normalized = str(raw_market_mode or "sp500").strip().lower()
    return normalized if normalized in VALID_MARKET_MODES else "sp500"


def _cutoff_date(days: int) -> str:
    current = datetime.fromisoformat(f"{today_market_date()}T00:00:00").date()
    return (current - timedelta(days=max(0, days - 1))).isoformat()


def ensure_human_bias_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS symbol_interest_daily (
            market_mode TEXT NOT NULL,
            symbol TEXT NOT NULL,
            event_date TEXT NOT NULL,
            interest_count INTEGER NOT NULL DEFAULT 0,
            last_source TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (market_mode, symbol, event_date)
        );

        CREATE INDEX IF NOT EXISTS idx_symbol_interest_daily_market_date
            ON symbol_interest_daily(market_mode, event_date DESC);

        CREATE INDEX IF NOT EXISTS idx_symbol_interest_daily_market_symbol
            ON symbol_interest_daily(market_mode, symbol, event_date DESC);
        """
    )


def human_bias_label(score: Optional[float]) -> str:
    numeric = _safe_float(score) or 0.0
    if numeric >= 76.0:
        return "attention crowded"
    if numeric >= 62.0:
        return "attention elevated"
    if numeric >= 48.0:
        return "attention building"
    if numeric >= 34.0:
        return "attention tentative"
    return "attention diffuse"


def _empty_snapshot(symbol: str, market_mode: str, *, status: str = "warming-up") -> Dict[str, Any]:
    return {
        "status": status,
        "symbol": symbol,
        "marketMode": market_mode,
        "updatedAt": today_market_timestamp_iso(),
        "shortWindowDays": SHORT_WINDOW_DAYS,
        "longWindowDays": LONG_WINDOW_DAYS,
        "shortCount": 0,
        "longCount": 0,
        "shortSharePct": 0.0,
        "longSharePct": 0.0,
        "activeDays": 0,
        "recencyDays": None,
        "intensityPct": 0.0,
        "trendScore": 0.5,
        "score": 0.0,
        "label": human_bias_label(0.0),
        "rationale": "Aggregated user symbol attention is still warming up.",
    }


def record_symbol_interest(
    symbol: Any,
    *,
    market_mode: Any = "sp500",
    source: Any = "direct_symbol_analysis",
) -> Dict[str, Any]:
    normalized_symbol = normalize_interest_symbol(symbol)
    normalized_market_mode = normalize_market_mode(market_mode)
    if not normalized_symbol:
        return _empty_snapshot("", normalized_market_mode, status="invalid-symbol")

    now = today_market_timestamp_iso()
    event_date = today_market_date()
    normalized_source = str(source or "direct_symbol_analysis").strip().lower()[:64]

    HUMAN_BIAS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(HUMAN_BIAS_DB_PATH) as conn:
        ensure_human_bias_schema(conn)
        conn.execute(
            """
            INSERT INTO symbol_interest_daily (
                market_mode, symbol, event_date, interest_count, last_source, updated_at
            )
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(market_mode, symbol, event_date)
            DO UPDATE SET
                interest_count = interest_count + 1,
                last_source = excluded.last_source,
                updated_at = excluded.updated_at
            """,
            (
                normalized_market_mode,
                normalized_symbol,
                event_date,
                normalized_source,
                now,
            ),
        )
        conn.commit()

    return load_symbol_interest_snapshot(
        normalized_symbol,
        market_mode=normalized_market_mode,
    )


def _fetch_market_rows(
    conn: sqlite3.Connection,
    *,
    market_mode: str,
    symbols: Optional[Iterable[str]] = None,
) -> List[sqlite3.Row]:
    cutoff = _cutoff_date(LONG_WINDOW_DAYS)
    conn.row_factory = sqlite3.Row
    normalized_symbols = [symbol for symbol in (symbols or []) if symbol]
    if normalized_symbols:
        placeholders = ", ".join("?" for _ in normalized_symbols)
        query = (
            "SELECT market_mode, symbol, event_date, interest_count, updated_at "
            "FROM symbol_interest_daily "
            f"WHERE market_mode = ? AND event_date >= ? AND symbol IN ({placeholders}) "
            "ORDER BY event_date DESC, symbol ASC"
        )
        params: List[Any] = [market_mode, cutoff, *normalized_symbols]
    else:
        query = (
            "SELECT market_mode, symbol, event_date, interest_count, updated_at "
            "FROM symbol_interest_daily "
            "WHERE market_mode = ? AND event_date >= ? "
            "ORDER BY event_date DESC, symbol ASC"
        )
        params = [market_mode, cutoff]
    return conn.execute(query, params).fetchall()


def _rows_to_snapshot(
    symbol: str,
    market_mode: str,
    symbol_rows: List[sqlite3.Row],
    all_rows: List[sqlite3.Row],
) -> Dict[str, Any]:
    if not symbol_rows or not all_rows:
        return _empty_snapshot(symbol, market_mode)

    short_cutoff = _cutoff_date(SHORT_WINDOW_DAYS)
    short_count = float(
        sum(int(row["interest_count"]) for row in symbol_rows if str(row["event_date"]) >= short_cutoff)
    )
    long_count = float(sum(int(row["interest_count"]) for row in symbol_rows))
    total_short = float(
        sum(int(row["interest_count"]) for row in all_rows if str(row["event_date"]) >= short_cutoff)
    )
    total_long = float(sum(int(row["interest_count"]) for row in all_rows))
    active_days = len({str(row["event_date"]) for row in symbol_rows if int(row["interest_count"]) > 0})
    last_seen_date = max((str(row["event_date"]) for row in symbol_rows), default=None)

    recency_days: Optional[int] = None
    if last_seen_date:
        current_date = date.fromisoformat(today_market_date())
        recency_days = max(0, (current_date - date.fromisoformat(last_seen_date)).days)

    short_share = short_count / total_short if total_short > 0 else 0.0
    long_share = long_count / total_long if total_long > 0 else 0.0
    short_daily = short_count / max(1.0, float(SHORT_WINDOW_DAYS))
    long_daily = long_count / max(1.0, float(LONG_WINDOW_DAYS))
    trend_delta = short_daily - long_daily
    trend_score = _clip01(0.5 + 0.5 * math.tanh(trend_delta * 0.85))
    intensity = _clip01(math.log1p(short_count) / math.log(10.0))
    persistence = _clip01(math.log1p(long_count) / math.log(28.0))
    freshness = (
        _clip01(math.exp(-float(recency_days) / 18.0))
        if recency_days is not None
        else 0.0
    )
    share_strength = _clip01(short_share / 0.12)

    score = (
        share_strength * 0.36
        + intensity * 0.22
        + persistence * 0.18
        + freshness * 0.12
        + trend_score * 0.12
    ) * 100.0
    label = human_bias_label(score)
    rationale = (
        f"Aggregated user attention watched {symbol} {int(short_count)} times over the last "
        f"{SHORT_WINDOW_DAYS}d and {int(long_count)} times over the last {LONG_WINDOW_DAYS}d, "
        f"with short-window share {short_share * 100:.1f}% and trend score {trend_score * 100:.0f}%."
    )

    return {
        "status": "ok",
        "symbol": symbol,
        "marketMode": market_mode,
        "updatedAt": today_market_timestamp_iso(),
        "shortWindowDays": SHORT_WINDOW_DAYS,
        "longWindowDays": LONG_WINDOW_DAYS,
        "shortCount": int(short_count),
        "longCount": int(long_count),
        "shortSharePct": short_share * 100.0,
        "longSharePct": long_share * 100.0,
        "activeDays": active_days,
        "recencyDays": recency_days,
        "intensityPct": intensity * 100.0,
        "trendScore": trend_score,
        "score": score,
        "label": label,
        "rationale": rationale,
    }


def load_symbol_interest_snapshot(
    symbol: Any,
    *,
    market_mode: Any = "sp500",
) -> Dict[str, Any]:
    normalized_symbol = normalize_interest_symbol(symbol)
    normalized_market_mode = normalize_market_mode(market_mode)
    if not normalized_symbol:
        return _empty_snapshot("", normalized_market_mode, status="invalid-symbol")
    if not HUMAN_BIAS_DB_PATH.exists():
        return _empty_snapshot(normalized_symbol, normalized_market_mode)

    with sqlite3.connect(HUMAN_BIAS_DB_PATH) as conn:
        ensure_human_bias_schema(conn)
        all_rows = _fetch_market_rows(conn, market_mode=normalized_market_mode)
        if not all_rows:
            return _empty_snapshot(normalized_symbol, normalized_market_mode)
        symbol_rows = [row for row in all_rows if str(row["symbol"]) == normalized_symbol]
        return _rows_to_snapshot(
            normalized_symbol,
            normalized_market_mode,
            symbol_rows,
            all_rows,
        )


def load_symbol_interest_map(
    symbols: Iterable[str],
    *,
    market_mode: Any = "sp500",
) -> Dict[str, Dict[str, Any]]:
    normalized_market_mode = normalize_market_mode(market_mode)
    normalized_symbols = [normalize_interest_symbol(symbol) for symbol in symbols]
    normalized_symbols = [symbol for symbol in normalized_symbols if symbol]
    if not normalized_symbols:
        return {}
    if not HUMAN_BIAS_DB_PATH.exists():
        return {
            symbol: _empty_snapshot(symbol, normalized_market_mode)
            for symbol in normalized_symbols
        }

    with sqlite3.connect(HUMAN_BIAS_DB_PATH) as conn:
        ensure_human_bias_schema(conn)
        all_rows = _fetch_market_rows(conn, market_mode=normalized_market_mode)
        rows_by_symbol: Dict[str, List[sqlite3.Row]] = {}
        for row in all_rows:
            symbol = str(row["symbol"])
            if symbol in normalized_symbols:
                rows_by_symbol.setdefault(symbol, []).append(row)

        return {
            symbol: _rows_to_snapshot(
                symbol,
                normalized_market_mode,
                rows_by_symbol.get(symbol, []),
                all_rows,
            )
            for symbol in normalized_symbols
        }


def load_market_interest_overview(
    *,
    market_mode: Any = "sp500",
    limit: int = 10,
) -> Dict[str, Any]:
    normalized_market_mode = normalize_market_mode(market_mode)
    if not HUMAN_BIAS_DB_PATH.exists():
        return {
            "status": "warming-up",
            "marketMode": normalized_market_mode,
            "topSymbols": [],
            "updatedAt": today_market_timestamp_iso(),
        }

    with sqlite3.connect(HUMAN_BIAS_DB_PATH) as conn:
        ensure_human_bias_schema(conn)
        all_rows = _fetch_market_rows(conn, market_mode=normalized_market_mode)
        if not all_rows:
            return {
                "status": "warming-up",
                "marketMode": normalized_market_mode,
                "topSymbols": [],
                "updatedAt": today_market_timestamp_iso(),
            }

        grouped: Dict[str, List[sqlite3.Row]] = {}
        for row in all_rows:
            grouped.setdefault(str(row["symbol"]), []).append(row)

        ranked = sorted(
            (
                _rows_to_snapshot(symbol, normalized_market_mode, rows, all_rows)
                for symbol, rows in grouped.items()
            ),
            key=lambda item: (_safe_float(item.get("score")) or 0.0, item.get("shortCount") or 0),
            reverse=True,
        )

        return {
            "status": "ok",
            "marketMode": normalized_market_mode,
            "updatedAt": today_market_timestamp_iso(),
            "topSymbols": ranked[: max(1, int(limit))],
        }
