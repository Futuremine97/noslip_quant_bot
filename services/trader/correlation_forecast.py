#!/usr/bin/env python3

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

from services.trader.sp500_information_map import (
    build_symbol_metadata,
    ensure_sp500_matrix,
    safe_float,
)

DEFAULT_LOOKBACK_DAYS = int(os.getenv("SP500_CORRELATION_LOOKBACK_DAYS", "252"))
WINDOWS: tuple[int, ...] = (20, 60, 120)
WINDOW_WEIGHTS = {20: 0.45, 60: 0.35, 120: 0.20}
MIN_PAIR_OBSERVATIONS = int(os.getenv("SP500_CORRELATION_MIN_OBS", "45"))
TOP_PEERS = int(os.getenv("SP500_CORRELATION_TOP_PEERS", "5"))


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper().replace(".", "-")


def _empty_payload(symbol: str, reason: str) -> Dict[str, Any]:
    return {
        "status": "unavailable",
        "symbol": normalize_symbol(symbol),
        "reason": reason,
        "methodology": (
            "Blended forward estimate from rolling 20D/60D/120D return correlations "
            "with short-horizon trend extrapolation and stability weighting."
        ),
    }


def _prepare_returns(close_matrix: pd.DataFrame, lookback_days: int) -> pd.DataFrame:
    if "ds" not in close_matrix.columns:
        return pd.DataFrame()

    numeric_matrix = close_matrix.drop(columns=["ds"], errors="ignore").copy()
    if numeric_matrix.empty:
        return pd.DataFrame()

    numeric_matrix = numeric_matrix.apply(pd.to_numeric, errors="coerce")
    numeric_matrix = numeric_matrix.tail(max(lookback_days + max(WINDOWS), 180))
    returns = numeric_matrix.pct_change().replace([np.inf, -np.inf], np.nan)
    return returns


def _window_signal(pair_returns: pd.DataFrame, window: int) -> Optional[Dict[str, Any]]:
    valid = pair_returns.dropna()
    if len(valid) < max(window, 12):
        return None

    rolling = valid.iloc[:, 0].rolling(window).corr(valid.iloc[:, 1]).dropna()
    if rolling.empty:
        latest_corr = safe_float(valid.iloc[:, 0].tail(window).corr(valid.iloc[:, 1].tail(window)))
        if latest_corr is None:
            return None
        return {
            "window": window,
            "currentCorrelation": latest_corr,
            "predictedCorrelation": latest_corr,
            "trendDelta": 0.0,
            "confidence": min(1.0, len(valid) / float(window * 3)),
            "observations": int(len(valid)),
        }

    latest_corr = safe_float(rolling.iloc[-1])
    if latest_corr is None:
        return None

    anchor = rolling.tail(min(6, len(rolling))).to_numpy(dtype=float)
    if len(anchor) >= 4:
        split = max(2, len(anchor) // 2)
        trend_delta = float(np.mean(anchor[-split:]) - np.mean(anchor[:split]))
    elif len(anchor) >= 2:
        trend_delta = float(anchor[-1] - anchor[0])
    else:
        trend_delta = 0.0

    predicted_corr = float(np.clip(latest_corr + trend_delta * 0.35, -0.98, 0.98))
    stability = max(0.0, 1.0 - min(1.0, float(np.std(anchor)) / 0.65))
    coverage = min(1.0, len(valid) / float(window * 3))
    confidence = max(0.0, min(1.0, coverage * 0.55 + stability * 0.45))

    return {
        "window": window,
        "currentCorrelation": latest_corr,
        "predictedCorrelation": predicted_corr,
        "trendDelta": trend_delta,
        "confidence": confidence,
        "observations": int(len(valid)),
    }


def _blend_pair_signals(signals: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not signals:
        return None

    blended_weights = []
    predicted_values = []
    current_values = []
    observations = []
    confidences = []
    for signal in signals:
        base_weight = WINDOW_WEIGHTS.get(int(signal.get("window") or 0), 0.0)
        confidence = max(0.05, safe_float(signal.get("confidence")) or 0.0)
        blended_weights.append(base_weight * confidence)
        predicted_values.append(float(signal["predictedCorrelation"]))
        current_values.append(float(signal["currentCorrelation"]))
        observations.append(int(signal.get("observations") or 0))
        confidences.append(confidence)

    total_weight = sum(blended_weights)
    if total_weight <= 0:
        return None

    predicted = float(np.clip(np.average(predicted_values, weights=blended_weights), -0.98, 0.98))
    current = float(np.clip(np.average(current_values, weights=blended_weights), -0.98, 0.98))
    spread = float(max(predicted_values) - min(predicted_values)) if len(predicted_values) > 1 else 0.0
    stability = max(0.0, 1.0 - min(1.0, spread / 1.25))
    coverage = min(1.0, float(np.mean(observations)) / float(max(WINDOWS) * 2.5))
    window_confidence = float(np.average(confidences, weights=blended_weights))
    confidence = max(
        0.0,
        min(1.0, window_confidence * 0.42 + stability * 0.33 + coverage * 0.25),
    )

    return {
        "currentCorrelation": current,
        "predictedCorrelation": predicted,
        "confidence": confidence,
        "windowSpread": spread,
        "observations": int(max(observations) if observations else 0),
        "windows": list(signals),
    }


def _pair_summary(
    base_symbol: str,
    peer_symbol: str,
    returns: pd.DataFrame,
    metadata: Optional[Dict[str, Dict[str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    if base_symbol not in returns.columns or peer_symbol not in returns.columns:
        return None

    pair_returns = returns[[base_symbol, peer_symbol]].dropna()
    if len(pair_returns) < MIN_PAIR_OBSERVATIONS:
        return None

    signals = []
    for window in WINDOWS:
        signal = _window_signal(pair_returns, window)
        if signal:
            signals.append(signal)

    blended = _blend_pair_signals(signals)
    if not blended:
        return None

    peer_meta = (metadata or {}).get(peer_symbol, {})
    return {
        "symbol": peer_symbol,
        "name": peer_meta.get("name") or peer_symbol,
        "sector": peer_meta.get("sector") or None,
        **blended,
    }


def _classify_symbol_network(
    average_predicted: Optional[float],
    inverse_share: float,
) -> str:
    if average_predicted is None:
        return "unknown"
    if average_predicted >= 0.55:
        return "crowded cluster"
    if average_predicted >= 0.28:
        return "sector-linked"
    if inverse_share >= 0.12:
        return "diversified edge"
    return "mixed network"


def build_symbol_correlation_forecast(
    symbol: str,
    close_matrix: pd.DataFrame,
    *,
    metadata: Optional[Dict[str, Dict[str, str]]] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    top_n: int = TOP_PEERS,
) -> Dict[str, Any]:
    normalized_symbol = normalize_symbol(symbol)
    returns = _prepare_returns(close_matrix, lookback_days)
    if returns.empty or normalized_symbol not in returns.columns:
        return _empty_payload(symbol, "symbol_missing_from_matrix")

    pair_summaries: List[Dict[str, Any]] = []
    for peer_symbol in returns.columns:
        peer_symbol = normalize_symbol(peer_symbol)
        if peer_symbol == normalized_symbol:
            continue
        summary = _pair_summary(normalized_symbol, peer_symbol, returns, metadata=metadata)
        if summary:
            pair_summaries.append(summary)

    if not pair_summaries:
        return _empty_payload(symbol, "insufficient_overlap")

    predicted_values = np.asarray(
        [safe_float(item.get("predictedCorrelation")) or 0.0 for item in pair_summaries],
        dtype=float,
    )
    confidences = np.asarray(
        [max(0.05, safe_float(item.get("confidence")) or 0.0) for item in pair_summaries],
        dtype=float,
    )
    average_predicted = safe_float(float(np.average(predicted_values, weights=confidences)))
    median_predicted = safe_float(float(np.median(predicted_values)))
    positive_share = safe_float(float(np.mean(predicted_values > 0)))
    inverse_share = safe_float(float(np.mean(predicted_values < 0)))
    network_label = _classify_symbol_network(average_predicted, inverse_share or 0.0)

    pair_summaries.sort(
        key=lambda item: (
            safe_float(item.get("predictedCorrelation")) or -999.0,
            safe_float(item.get("confidence")) or 0.0,
        ),
        reverse=True,
    )
    top_correlated = pair_summaries[: max(1, top_n)]
    top_diversifiers = sorted(
        pair_summaries,
        key=lambda item: (
            safe_float(item.get("predictedCorrelation")) or 999.0,
            -(safe_float(item.get("confidence")) or 0.0),
        ),
    )[: max(1, top_n)]

    ds_values = close_matrix.get("ds")
    as_of_date = None
    if ds_values is not None and len(ds_values) > 0:
        as_of_date = str(pd.Timestamp(ds_values.iloc[-1]).date())

    return {
        "status": "ok",
        "symbol": normalized_symbol,
        "asOfDate": as_of_date,
        "lookbackDays": int(lookback_days),
        "peerUniverse": len(pair_summaries),
        "averagePredictedCorrelation": average_predicted,
        "medianPredictedCorrelation": median_predicted,
        "positiveShare": positive_share,
        "inverseShare": inverse_share,
        "networkLabel": network_label,
        "methodology": (
            "Blended forward estimate from rolling 20D/60D/120D return correlations "
            "with short-horizon trend extrapolation and stability weighting."
        ),
        "topCorrelatedPeers": top_correlated,
        "topDiversifiers": top_diversifiers,
    }


def build_sp500_symbol_correlation_forecast(symbol: str) -> Dict[str, Any]:
    constituents, close_matrix = ensure_sp500_matrix()
    metadata = build_symbol_metadata(constituents)
    return build_symbol_correlation_forecast(symbol, close_matrix, metadata=metadata)


def _pair_key(left_symbol: str, right_symbol: str) -> str:
    ordered = sorted([normalize_symbol(left_symbol), normalize_symbol(right_symbol)])
    return f"{ordered[0]}::{ordered[1]}"


def build_portfolio_correlation_forecast(
    holdings: Sequence[Dict[str, Any]],
    close_matrix: pd.DataFrame,
    *,
    metadata: Optional[Dict[str, Dict[str, str]]] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> Dict[str, Any]:
    normalized_holdings = [
        {
            **holding,
            "symbol": normalize_symbol(holding.get("symbol") or ""),
            "weight": max(0.0, safe_float(holding.get("weight")) or 0.0),
            "weightPct": safe_float(holding.get("weightPct")),
        }
        for holding in holdings
        if normalize_symbol(holding.get("symbol") or "")
    ]
    if len(normalized_holdings) < 2:
        return {
            "status": "unavailable",
            "reason": "insufficient_holdings",
            "holdingCount": len(normalized_holdings),
        }

    returns = _prepare_returns(close_matrix, lookback_days)
    if returns.empty:
        return {
            "status": "unavailable",
            "reason": "missing_return_matrix",
            "holdingCount": len(normalized_holdings),
        }

    pair_summaries: List[Dict[str, Any]] = []
    holding_pairs: Dict[str, List[Dict[str, Any]]] = {holding["symbol"]: [] for holding in normalized_holdings}

    for index, left_holding in enumerate(normalized_holdings):
        left_symbol = left_holding["symbol"]
        for right_holding in normalized_holdings[index + 1 :]:
            right_symbol = right_holding["symbol"]
            if left_symbol not in returns.columns or right_symbol not in returns.columns:
                continue

            pair_summary = _pair_summary(left_symbol, right_symbol, returns, metadata=metadata)
            if not pair_summary:
                continue

            pair_weight = max(0.0, float(left_holding["weight"]) * float(right_holding["weight"]))
            pair_record = {
                "leftSymbol": left_symbol,
                "rightSymbol": right_symbol,
                "leftWeightPct": safe_float(left_holding.get("weightPct")),
                "rightWeightPct": safe_float(right_holding.get("weightPct")),
                "pairWeightPct": safe_float(pair_weight * 100.0),
                **pair_summary,
            }
            pair_summaries.append(pair_record)

            left_peer_view = {
                "peerSymbol": right_symbol,
                "peerWeightPct": safe_float(right_holding.get("weightPct")),
                **pair_summary,
            }
            right_peer_view = {
                "peerSymbol": left_symbol,
                "peerWeightPct": safe_float(left_holding.get("weightPct")),
                **pair_summary,
            }
            holding_pairs[left_symbol].append(left_peer_view)
            holding_pairs[right_symbol].append(right_peer_view)

    if not pair_summaries:
        return {
            "status": "unavailable",
            "reason": "insufficient_pair_history",
            "holdingCount": len(normalized_holdings),
        }

    pair_weights = np.asarray(
        [max(0.001, safe_float(item.get("pairWeightPct")) or 0.0) for item in pair_summaries],
        dtype=float,
    )
    predicted_values = np.asarray(
        [safe_float(item.get("predictedCorrelation")) or 0.0 for item in pair_summaries],
        dtype=float,
    )
    abs_values = np.abs(predicted_values)
    average_predicted = safe_float(float(np.average(predicted_values, weights=pair_weights)))
    average_absolute = safe_float(float(np.average(abs_values, weights=pair_weights)))
    average_positive = safe_float(
        float(
            np.average(
                np.maximum(predicted_values, 0.0),
                weights=pair_weights,
            )
        )
    )
    diversification_score = safe_float(
        float(
            max(
                0.0,
                min(
                    1.0,
                    0.72 - max(0.0, average_positive or 0.0) * 0.95 - max(0.0, (average_absolute or 0.0) - 0.35) * 0.35,
                ),
            )
        )
    )
    crowded_pair_risk = safe_float(
        float(
            np.max(
                np.asarray(
                    [
                        max(0.0, safe_float(item.get("predictedCorrelation")) or 0.0)
                        * (max(0.0, safe_float(item.get("pairWeightPct")) or 0.0) / 100.0)
                        * max(0.1, safe_float(item.get("confidence")) or 0.0)
                        for item in pair_summaries
                    ],
                    dtype=float,
                )
            )
        )
    )
    if (average_predicted or 0.0) >= 0.48:
        concentration_label = "crowded"
    elif (average_predicted or 0.0) >= 0.24:
        concentration_label = "balanced cluster"
    else:
        concentration_label = "well diversified"

    top_crowded_pairs = sorted(
        pair_summaries,
        key=lambda item: (
            (safe_float(item.get("predictedCorrelation")) or -999.0)
            * max(0.1, safe_float(item.get("pairWeightPct")) or 0.0)
            * max(0.1, safe_float(item.get("confidence")) or 0.0),
            safe_float(item.get("predictedCorrelation")) or -999.0,
        ),
        reverse=True,
    )[:3]
    top_diversifying_pairs = sorted(
        pair_summaries,
        key=lambda item: (
            safe_float(item.get("predictedCorrelation")) or 999.0,
            -(safe_float(item.get("pairWeightPct")) or 0.0),
        ),
    )[:3]

    per_holding = []
    for holding in normalized_holdings:
        symbol = holding["symbol"]
        peer_views = holding_pairs.get(symbol) or []
        if not peer_views:
            continue

        peer_weights = np.asarray(
            [max(0.001, safe_float(item.get("peerWeightPct")) or 0.0) for item in peer_views],
            dtype=float,
        )
        peer_predictions = np.asarray(
            [safe_float(item.get("predictedCorrelation")) or 0.0 for item in peer_views],
            dtype=float,
        )
        average_peer_corr = safe_float(float(np.average(peer_predictions, weights=peer_weights)))
        strongest_peer = max(
            peer_views,
            key=lambda item: safe_float(item.get("predictedCorrelation")) or -999.0,
        )
        diversifier_peer = min(
            peer_views,
            key=lambda item: safe_float(item.get("predictedCorrelation")) or 999.0,
        )
        diversification_support = safe_float(
            float(max(0.0, min(1.0, 0.62 - max(0.0, average_peer_corr or 0.0) * 0.95)))
        )
        per_holding.append(
            {
                "symbol": symbol,
                "portfolioWeightPct": safe_float(holding.get("weightPct")),
                "averagePredictedCorrelation": average_peer_corr,
                "diversificationSupportScore": diversification_support,
                "strongestCorrelationPeer": strongest_peer.get("peerSymbol"),
                "strongestCorrelationValue": safe_float(
                    strongest_peer.get("predictedCorrelation")
                ),
                "strongestDiversifierPeer": diversifier_peer.get("peerSymbol"),
                "strongestDiversifierValue": safe_float(
                    diversifier_peer.get("predictedCorrelation")
                ),
                "confidence": safe_float(
                    np.average(
                        np.asarray(
                            [safe_float(item.get("confidence")) or 0.0 for item in peer_views],
                            dtype=float,
                        ),
                        weights=peer_weights,
                    )
                ),
            }
        )

    ds_values = close_matrix.get("ds")
    as_of_date = None
    if ds_values is not None and len(ds_values) > 0:
        as_of_date = str(pd.Timestamp(ds_values.iloc[-1]).date())

    return {
        "status": "ok",
        "asOfDate": as_of_date,
        "methodology": (
            "Blended forward estimate from rolling 20D/60D/120D return correlations "
            "with short-horizon trend extrapolation and stability weighting."
        ),
        "holdingCount": len(normalized_holdings),
        "pairCount": len(pair_summaries),
        "averagePredictedCorrelation": average_predicted,
        "averageAbsoluteCorrelation": average_absolute,
        "averagePositiveCorrelation": average_positive,
        "diversificationScore": diversification_score,
        "crowdedPairRiskScore": crowded_pair_risk,
        "concentrationRiskLabel": concentration_label,
        "topCrowdedPairs": top_crowded_pairs,
        "topDiversifyingPairs": top_diversifying_pairs,
        "perHolding": per_holding,
    }
