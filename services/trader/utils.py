import math
import time
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def ensure_raw_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "ds" not in out.columns:
        raise ValueError("CSV must contain column 'ds'")

    out["ds"] = pd.to_datetime(out["ds"], utc=True, errors="coerce")
    out = out.dropna(subset=["ds"]).sort_values("ds").reset_index(drop=True)

    if "close" not in out.columns and "y" not in out.columns:
        raise ValueError("CSV must contain 'close' or 'y'")

    if "close" not in out.columns and "y" in out.columns:
        out["close"] = pd.to_numeric(out["y"], errors="coerce")

    out["close"] = pd.to_numeric(out["close"], errors="coerce")

    if "open" not in out.columns:
        out["open"] = out["close"]
    else:
        out["open"] = pd.to_numeric(out["open"], errors="coerce")

    if "high" not in out.columns:
        out["high"] = out["close"]
    else:
        out["high"] = pd.to_numeric(out["high"], errors="coerce")

    if "low" not in out.columns:
        out["low"] = out["close"]
    else:
        out["low"] = pd.to_numeric(out["low"], errors="coerce")

    out = out.dropna(subset=["ds", "open", "high", "low", "close"]).copy()
    if len(out) < 100:
        raise ValueError("Need at least ~100 rows of historical data")
    return out


def resample_ohlc(raw_df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = ensure_raw_df(raw_df).set_index("ds").sort_index()
    bars = (
        out[["open", "high", "low", "close"]]
        .resample(rule)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
        .reset_index()
    )
    return bars


def build_training_views_for_rule(raw_df: pd.DataFrame, rule: str) -> Dict[str, pd.DataFrame]:
    bars = resample_ohlc(raw_df, rule)
    return {
        "bars": bars,
        "direction_df": bars[["ds", "close"]].rename(columns={"close": "y"}).copy(),
        "low_df": bars[["ds", "low"]].rename(columns={"low": "y"}).copy(),
        "high_df": bars[["ds", "high"]].rename(columns={"high": "y"}).copy(),
    }


def build_future_grid(last_timestamp: pd.Timestamp, rule: str, steps: int) -> pd.DataFrame:
    future_ds = pd.date_range(
        start=last_timestamp + pd.Timedelta(rule), periods=steps, freq=rule, tz="UTC"
    )
    return pd.DataFrame({"ds": future_ds})


def weighted_timestamp(ts_weight_pairs: List[Tuple[pd.Timestamp, float]]) -> pd.Timestamp:
    total_w = sum(w for _, w in ts_weight_pairs) or 1.0
    avg_ns = int(sum(pd.Timestamp(ts).value * w for ts, w in ts_weight_pairs) / total_w)
    return pd.to_datetime(avg_ns, utc=True)


def wait_until_target(target_ts: pd.Timestamp, lead_seconds: int) -> None:
    now = pd.Timestamp.now(tz="UTC")
    delta = (target_ts - now).total_seconds() - lead_seconds
    if delta > 0:
        print(f"Waiting {delta:.1f}s until target window...")
        time.sleep(delta)


def choose_best_candidate(candidates: List[dict]) -> dict:
    rows = []
    for c in candidates:
        rows.append(
            {
                "requestId": c["requestId"],
                "outAmount": c.get("outAmount", 0) or 0,
                "priceImpact": c.get("priceImpact", 999999) or 999999,
                "totalTime": c.get("totalTime", 999999) or 999999,
            }
        )

    sdf = pd.DataFrame(rows)
    sdf["rank_out"] = sdf["outAmount"].rank(ascending=False, method="average")
    sdf["rank_impact"] = sdf["priceImpact"].rank(ascending=True, method="average")
    sdf["rank_time"] = sdf["totalTime"].rank(ascending=True, method="average")
    sdf["final_score"] = sdf["rank_out"] + sdf["rank_impact"] + sdf["rank_time"]

    best_request_id = sdf.sort_values("final_score", ascending=True).iloc[0]["requestId"]
    return next(c for c in candidates if c["requestId"] == best_request_id)


def add_exec_order_features(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["fetched_at"] = pd.to_datetime(out["fetched_at"], utc=True, errors="coerce")
    out = out.sort_values("fetched_at").reset_index(drop=True)
    out["pair"] = out["inputMint"].astype(str) + "->" + out["outputMint"].astype(str)
    return out
