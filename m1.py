# jupiter_trading_bot_integrated.py
# pip install pandas numpy requests prophet solders yfinance scikit-learn

import os
import time
import math
import json
import base64
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from sklearn.linear_model import LinearRegression

from prophet import Prophet
from prophet.serialize import model_to_json, model_from_json

from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.transaction import VersionedTransaction


# =========================================================
# CONFIG
# =========================================================

SWAP_BASE_URL = "https://api.jup.ag/swap/v2"
ORDER_URL = f"{SWAP_BASE_URL}/order"
EXECUTE_URL = f"{SWAP_BASE_URL}/execute"

JUPITER_API_KEY = os.getenv("JUPITER_API_KEY", "")
SOLANA_PRIVATE_KEY_B58 = os.getenv("SOLANA_PRIVATE_KEY_B58", "")
PRICE_HISTORY_CSV = os.getenv("PRICE_HISTORY_CSV", "historical_price.csv")

EXECUTE_TRADES = os.getenv("EXECUTE_TRADES", "false").lower() == "true"
WAIT_FOR_TARGET = os.getenv("WAIT_FOR_TARGET", "false").lower() == "true"
ALLOW_BOOTSTRAP_EXECUTION = os.getenv("ALLOW_BOOTSTRAP_EXECUTION", "false").lower() == "true"
MIN_REALTIME_BARS_FOR_EXECUTION = int(os.getenv("MIN_REALTIME_BARS_FOR_EXECUTION", "100"))
INTRADAY_MAX_BAR_SECONDS = int(os.getenv("INTRADAY_MAX_BAR_SECONDS", "600"))  # 10 minutes

# 캐싱 및 모델 관리 설정
MODEL_CACHE_DIR = os.getenv("MODEL_CACHE_DIR", "model_cache")
FORCE_RETRAIN = os.getenv("FORCE_RETRAIN", "false").lower() == "true"

# 엔진 선택 (PROPHET 또는 LR) 및 yfinance 폴백용 타겟 심볼
MODEL_ENGINE = os.getenv("MODEL_ENGINE", "PROPHET").upper()
TARGET_COIN_SYMBOL = os.getenv("TARGET_COIN_SYMBOL", "SOL")

# Pair used for quote polling and execution
INPUT_MINT_FOR_PRICE = os.getenv("INPUT_MINT_FOR_PRICE", "So11111111111111111111111111111111111111112")   # SOL
OUTPUT_MINT_FOR_PRICE = os.getenv("OUTPUT_MINT_FOR_PRICE", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v") # USDC
INPUT_DECIMALS = int(os.getenv("INPUT_DECIMALS", "9"))
OUTPUT_DECIMALS = int(os.getenv("OUTPUT_DECIMALS", "6"))

# Quote-only poll size for implied price
QUOTE_AMOUNT_IN_SMALLEST_UNIT = int(os.getenv("QUOTE_AMOUNT_IN_SMALLEST_UNIT", "100000000"))  # 0.1 SOL

# Actual execution sizes
BUY_AMOUNT_USDC = int(os.getenv("BUY_AMOUNT_USDC", "10000000"))       # 10 USDC
SELL_AMOUNT_SOL = int(os.getenv("SELL_AMOUNT_SOL", "100000000"))      # 0.1 SOL

# Loop / polling
POLL_EVERY_SECONDS = float(os.getenv("POLL_EVERY_SECONDS", "5.0"))
QUOTE_BURST_COUNT = int(os.getenv("QUOTE_BURST_COUNT", "3"))
QUOTE_BURST_SLEEP_SECONDS = float(os.getenv("QUOTE_BURST_SLEEP_SECONDS", "1.0"))
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "10"))
TARGET_LEAD_SECONDS = int(os.getenv("TARGET_LEAD_SECONDS", "60"))

REQUIRE_ULTRA_FOR_TRAINING = os.getenv("REQUIRE_ULTRA_FOR_TRAINING", "true").lower() == "true"

CADENCE_RULES = ("10min", "5min", "1min")
CADENCE_WEIGHTS = {"10min": 0.45, "5min": 0.35, "1min": 0.20}
HORIZON_STEPS = {
    "10min": int(os.getenv("HORIZON_STEPS_10MIN", "6")),
    "5min": int(os.getenv("HORIZON_STEPS_5MIN", "12")),
    "1min": int(os.getenv("HORIZON_STEPS_1MIN", "30")),
}

BUY_THRESHOLD = float(os.getenv("BUY_THRESHOLD", "0.003"))
SELL_THRESHOLD = float(os.getenv("SELL_THRESHOLD", "-0.003"))
MAX_UNCERTAINTY_RATIO = float(os.getenv("MAX_UNCERTAINTY_RATIO", "0.03"))


# =========================================================
# BASIC HELPERS & DATA FETCHING
# =========================================================

def require_api_key():
    if not JUPITER_API_KEY:
        raise ValueError("Missing JUPITER_API_KEY")

def get_wallet() -> Keypair:
    if not SOLANA_PRIVATE_KEY_B58:
        raise ValueError("Missing SOLANA_PRIVATE_KEY_B58")
    return Keypair.from_base58_string(SOLANA_PRIVATE_KEY_B58)

def fetch_fallback_data(symbol: str, start_date: str = '2023-01-01') -> pd.DataFrame:
    """로컬 CSV가 없을 때 yfinance에서 데이터를 자동으로 다운로드합니다."""
    ticker = f"{symbol}-USD"
    print(f"\n[Data Fetcher] 로컬 데이터가 없습니다. yfinance에서 '{ticker}' 데이터를 다운로드합니다...")
    df = yf.download(ticker, start=start_date, end=pd.to_datetime('today'))
    if df.empty:
        raise ValueError(f"yfinance에서 {ticker} 데이터를 찾을 수 없습니다.")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    df = df.rename(columns={'Date': 'ds', 'Datetime': 'ds', 'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close'})
    df['y'] = df['close']
    print(f"[Data Fetcher] 데이터 다운로드 완료: 총 {len(df)}행")
    return df

def ensure_raw_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "ds" not in out.columns: raise ValueError("CSV must contain column 'ds'")
    out["ds"] = pd.to_datetime(out["ds"], utc=True, errors="coerce")
    out = out.dropna(subset=["ds"]).sort_values("ds").reset_index(drop=True)
    if "close" not in out.columns and "y" not in out.columns: raise ValueError("CSV must contain 'close' or 'y'")
    if "close" not in out.columns and "y" in out.columns: out["close"] = pd.to_numeric(out["y"], errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    if "open" not in out.columns: out["open"] = out["close"]
    else: out["open"] = pd.to_numeric(out["open"], errors="coerce")
    if "high" not in out.columns: out["high"] = out["close"]
    else: out["high"] = pd.to_numeric(out["high"], errors="coerce")
    if "low" not in out.columns: out["low"] = out["close"]
    else: out["low"] = pd.to_numeric(out["low"], errors="coerce")
    out = out.dropna(subset=["ds", "open", "high", "low", "close"]).copy()
    if len(out) < 100: raise ValueError("Need at least ~100 rows of historical data")
    return out

def infer_median_bar_seconds(df: pd.DataFrame) -> Optional[float]:
    ds = pd.to_datetime(df["ds"], utc=True, errors="coerce").dropna().sort_values()
    if len(ds) < 3: return None
    diffs = ds.diff().dropna().dt.total_seconds()
    if len(diffs) == 0: return None
    return float(diffs.median())

def is_intraday_df(df: pd.DataFrame, max_bar_seconds: int = INTRADAY_MAX_BAR_SECONDS) -> bool:
    median_sec = infer_median_bar_seconds(df)
    if median_sec is None: return False
    return median_sec <= max_bar_seconds

def resample_ohlc(raw_df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = ensure_raw_df(raw_df).set_index("ds").sort_index()
    return out[["open", "high", "low", "close"]].resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()

def build_training_views_for_rule(raw_df: pd.DataFrame, rule: str) -> Dict[str, pd.DataFrame]:
    bars = resample_ohlc(raw_df, rule)
    return {
        "bars": bars,
        "direction_df": bars[["ds", "close"]].rename(columns={"close": "y"}).copy(),
        "low_df": bars[["ds", "low"]].rename(columns={"low": "y"}).copy(),
        "high_df": bars[["ds", "high"]].rename(columns={"high": "y"}).copy(),
    }

def build_future_grid(last_timestamp: pd.Timestamp, rule: str, steps: int) -> pd.DataFrame:
    last_timestamp = pd.Timestamp(last_timestamp)
    if last_timestamp.tzinfo is None: last_timestamp = last_timestamp.tz_localize("UTC")
    else: last_timestamp = last_timestamp.tz_convert("UTC")
    return pd.DataFrame({"ds": pd.date_range(start=last_timestamp + pd.Timedelta(rule), periods=steps, freq=rule, tz="UTC")})

def weighted_timestamp(ts_weight_pairs: List[Tuple[pd.Timestamp, float]]) -> pd.Timestamp:
    total_w = sum(w for _, w in ts_weight_pairs) or 1.0
    avg_ns = int(sum(pd.Timestamp(ts).value * w for ts, w in ts_weight_pairs) / total_w)
    return pd.Timestamp(avg_ns, tz="UTC")

def wait_until_target(target_ts: pd.Timestamp, lead_seconds: int = TARGET_LEAD_SECONDS):
    target_ts = pd.Timestamp(target_ts)
    if target_ts.tzinfo is None: target_ts = target_ts.tz_localize("UTC")
    else: target_ts = target_ts.tz_convert("UTC")
    delta = (target_ts - pd.Timestamp.now(tz="UTC")).total_seconds() - lead_seconds
    if delta > 0:
        print(f"Waiting {delta:.1f}s until target window...")
        time.sleep(delta)

def choose_best_candidate(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [{"requestId": c["requestId"], "outAmount": c.get("outAmount", 0) or 0, "priceImpact": c.get("priceImpact", 999999) or 999999, "totalTime": c.get("totalTime", 999999) or 999999} for c in candidates]
    sdf = pd.DataFrame(rows)
    sdf["final_score"] = sdf["outAmount"].rank(ascending=False) + sdf["priceImpact"].rank(ascending=True) + sdf["totalTime"].rank(ascending=True)
    best_id = sdf.sort_values("final_score", ascending=True).iloc[0]["requestId"]
    return next(c for c in candidates if c["requestId"] == best_id)


# =========================================================
# AGENTS (PROPHET & LINEAR REGRESSION)
# =========================================================

@dataclass
class BaseProphetConfig:
    name: str
    rule: str
    horizon_steps: int
    seasonality_mode: str = "multiplicative"
    changepoint_prior_scale: float = 0.05
    yearly_seasonality: bool = False
    weekly_seasonality: bool = True
    daily_seasonality: bool = True
    growth: str = "linear"
    weight: float = 1.0

# --- 1. Prophet Engine ---
class ProphetEngineAgent:
    def __init__(self, config: BaseProphetConfig):
        self.config = config
        self.model = None
        self.train_df = None
        self.fitted = False

    def load_model(self, cache_dir: str):
        filepath = os.path.join(cache_dir, f"{self.config.name}.json")
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r') as f: return model_from_json(json.load(f))
            except Exception: pass
        return None

    def save_model(self, cache_dir: str):
        if not self.fitted or self.model is None: return
        os.makedirs(cache_dir, exist_ok=True)
        try:
            with open(os.path.join(cache_dir, f"{self.config.name}.json"), 'w') as f:
                json.dump(model_to_json(self.model), f)
        except Exception: pass

    def fit(self, df: pd.DataFrame, prev_model=None, use_warm_start: bool = False):
        self.train_df = df.copy()
        self.model = Prophet(
            seasonality_mode=self.config.seasonality_mode, changepoint_prior_scale=self.config.changepoint_prior_scale,
            yearly_seasonality=self.config.yearly_seasonality, weekly_seasonality=self.config.weekly_seasonality, daily_seasonality=self.config.daily_seasonality
        )
        init = None
        if use_warm_start and prev_model is not None:
            try:
                if getattr(prev_model, "n_changepoints", None) == getattr(self.model, "n_changepoints", None):
                    init = {k: float(np.asarray(v).reshape(-1)[0]) if k in ['k','m','sigma_obs'] else np.asarray(v)[0] for k, v in prev_model.params.items() if k in ['k','m','sigma_obs','delta','beta']}
            except Exception: pass
        try:
            if init: self.model.fit(df, init=init)
            else: self.model.fit(df)
        except Exception:
            self.model.fit(df)
        self.fitted = True
        return self

    def next_horizon_forecast(self) -> pd.DataFrame:
        last_ts = pd.to_datetime(self.train_df["ds"], utc=True).max()
        future = build_future_grid(last_ts, self.config.rule, self.config.horizon_steps)
        return self.model.predict(future)[["ds", "yhat", "yhat_lower", "yhat_upper"]].copy()

# --- 2. Linear Regression Engine (Fallback) ---
class LinearRegressionEngineAgent:
    def __init__(self, config: BaseProphetConfig):
        self.config = config
        self.model = LinearRegression()
        self.train_df = None
        self.fitted = False

    def load_model(self, cache_dir: str): return None
    def save_model(self, cache_dir: str): pass

    def fit(self, df: pd.DataFrame, prev_model=None, use_warm_start: bool = False):
        self.train_df = df.copy()
        X = np.arange(len(self.train_df)).reshape(-1, 1)
        self.model.fit(X, self.train_df['y'].values)
        self.fitted = True
        return self

    def next_horizon_forecast(self) -> pd.DataFrame:
        last_idx = len(self.train_df)
        preds = self.model.predict(np.arange(last_idx, last_idx + self.config.horizon_steps).reshape(-1, 1))
        last_ts = pd.to_datetime(self.train_df["ds"], utc=True).max()
        future_ds = pd.date_range(start=last_ts + pd.Timedelta(self.config.rule), periods=self.config.horizon_steps, freq=self.config.rule, tz="UTC")
        return pd.DataFrame({"ds": future_ds, "yhat": preds, "yhat_lower": preds * 0.99, "yhat_upper": preds * 1.01})

# --- Engine Selector ---
def get_base_agent(config: BaseProphetConfig):
    if MODEL_ENGINE == "LR": return LinearRegressionEngineAgent(config)
    return ProphetEngineAgent(config)


# --- Wrappers ---
class DirectionAgentWrapper:
    def __init__(self, config: BaseProphetConfig):
        self.config = config
        self.engine = get_base_agent(config)

    def load_model(self, d): return self.engine.load_model(d)
    def save_model(self, d): self.engine.save_model(d)
    def fit(self, df, prev_model=None, use_warm_start=False): return self.engine.fit(df, prev_model, use_warm_start)

    def decision(self) -> Dict[str, Any]:
        fcst = self.engine.next_horizon_forecast()
        last_price = float(self.engine.train_df["y"].iloc[-1])
        open_ret = (float(fcst.iloc[0]["yhat"]) / last_price) - 1.0 if last_price else 0.0
        close_ret = (float(fcst.iloc[-1]["yhat"]) / last_price) - 1.0 if last_price else 0.0
        mean_ret = (float(fcst["yhat"].mean()) / last_price) - 1.0 if last_price else 0.0
        score = 0.25 * open_ret + 0.50 * close_ret + 0.25 * mean_ret
        avg_band = float((fcst["yhat_upper"] - fcst["yhat_lower"]).mean())
        uncertainty = avg_band / max(abs(float(fcst["yhat"].mean())), 1e-8)

        if score >= BUY_THRESHOLD and uncertainty < MAX_UNCERTAINTY_RATIO: action = "BUY"
        elif score <= SELL_THRESHOLD and uncertainty < MAX_UNCERTAINTY_RATIO: action = "SELL"
        else: action = "HOLD"
        return {"agent": self.config.name, "action": action, "score": score, "weight": self.config.weight}

class TimingAgentWrapper:
    def __init__(self, config: BaseProphetConfig, mode="low"):
        self.config = config
        self.engine = get_base_agent(config)
        self.mode = mode

    def load_model(self, d): return self.engine.load_model(d)
    def save_model(self, d): self.engine.save_model(d)
    def fit(self, df, prev_model=None, use_warm_start=False): return self.engine.fit(df, prev_model, use_warm_start)

    def aggregate_point(self) -> Dict:
        fcst = self.engine.next_horizon_forecast()
        row = fcst.loc[fcst["yhat"].idxmin()] if self.mode == "low" else fcst.loc[fcst["yhat"].idxmax()]
        return {"agent": self.config.name, "predicted_timestamp": pd.Timestamp(row["ds"]), "predicted_price": float(row["yhat"]), "weight": self.config.weight}
    
    def full_curve(self) -> pd.DataFrame:
        fcst = self.engine.next_horizon_forecast()
        fcst["agent"], fcst["weight"] = self.config.name, self.config.weight
        return fcst


# =========================================================
# COORDINATORS (Cache Aware)
# =========================================================

@dataclass
class DirectionCoordinator:
    agents: List[DirectionAgentWrapper] = field(default_factory=list)
    def fit_all(self, df: pd.DataFrame, prev_agents=None, cache_dir: str = None, force_retrain: bool = False):
        prev_agents = prev_agents or []
        for i, a in enumerate(self.agents):
            use_warm_start, prev_model = False, None
            if i < len(prev_agents) and prev_agents[i] is not None:
                prev_model = getattr(prev_agents[i].engine, "model", None)
                use_warm_start = True
            elif cache_dir and not force_retrain:
                cached = a.load_model(cache_dir)
                if cached: prev_model, use_warm_start = cached, True
            a.fit(df, prev_model=prev_model, use_warm_start=use_warm_start)
            if cache_dir and (force_retrain or prev_model is None): a.save_model(cache_dir)
        return self

    def aggregate(self) -> Dict[str, Any]:
        details = pd.DataFrame([a.decision() for a in self.agents])
        w_score = float((details["score"] * details["weight"]).sum() / (details["weight"].sum() or 1.0))
        buy_w = float(details.loc[details["action"] == "BUY", "weight"].sum())
        sell_w = float(details.loc[details["action"] == "SELL", "weight"].sum())
        hold_w = float(details.loc[details["action"] == "HOLD", "weight"].sum())
        action = "BUY" if buy_w > max(sell_w, hold_w) and w_score > 0 else "SELL" if sell_w > max(buy_w, hold_w) and w_score < 0 else "HOLD"
        return {"final_action": action, "weighted_score": w_score, "details": details}

@dataclass
class TimingCoordinator:
    agents: List[TimingAgentWrapper] = field(default_factory=list)
    mode: str = "low"
    def fit_all(self, df: pd.DataFrame, prev_agents=None, cache_dir: str = None, force_retrain: bool = False):
        prev_agents = prev_agents or []
        for i, a in enumerate(self.agents):
            use_warm_start, prev_model = False, None
            if i < len(prev_agents) and prev_agents[i] is not None:
                prev_model = getattr(prev_agents[i].engine, "model", None)
                use_warm_start = True
            elif cache_dir and not force_retrain:
                cached = a.load_model(cache_dir)
                if cached: prev_model, use_warm_start = cached, True
            a.fit(df, prev_model=prev_model, use_warm_start=use_warm_start)
            if cache_dir and (force_retrain or prev_model is None): a.save_model(cache_dir)
        return self

    def aggregate(self) -> Dict[str, Any]:
        curves = [a.full_curve()[["ds", "yhat"]].rename(columns={"yhat": f"yhat__{a.config.name}"}) for a in self.agents]
        merged = curves[0]
        for c in curves[1:]: merged = merged.merge(c, on="ds", how="inner")
        merged["weighted_yhat"] = sum(merged[f"yhat__{a.config.name}"] * a.config.weight for a in self.agents) / (sum(a.config.weight for a in self.agents) or 1.0)
        row = merged.loc[merged["weighted_yhat"].idxmin()] if self.mode == "low" else merged.loc[merged["weighted_yhat"].idxmax()]
        return {"predicted_timestamp": pd.Timestamp(row["ds"]), "predicted_price": float(row["weighted_yhat"])}


def make_cfgs(prefix: str, rule: str, hs: int):
    return [BaseProphetConfig(name=f"{prefix}_base_{rule}", rule=rule, horizon_steps=hs, weight=1.0),
            BaseProphetConfig(name=f"{prefix}_flat_{rule}", rule=rule, horizon_steps=hs, growth="flat", weight=0.8)]

def build_dir_agents(r: str) -> List[DirectionAgentWrapper]: return [DirectionAgentWrapper(c) for c in make_cfgs("dir", r, HORIZON_STEPS[r])]
def build_low_agents(r: str) -> List[TimingAgentWrapper]: return [TimingAgentWrapper(c, "low") for c in make_cfgs("low", r, HORIZON_STEPS[r])]
def build_high_agents(r: str) -> List[TimingAgentWrapper]: return [TimingAgentWrapper(c, "high") for c in make_cfgs("high", r, HORIZON_STEPS[r])]


# =========================================================
# RUNTIME & CLIENTS
# =========================================================

class JupiterSwapClient:
    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.headers.update({"x-api-key": api_key})

    def get_order(self, input_mint: str, output_mint: str, amount: int, taker: str) -> Dict:
        r = self.session.get(ORDER_URL, params={"inputMint": input_mint, "outputMint": output_mint, "amount": str(amount), "taker": taker})
        r.raise_for_status()
        j = r.json(); j["_fetched_at"] = pd.Timestamp.now(tz="UTC").isoformat()
        return j

    def collect_candidate_orders(self, input_mint: str, output_mint: str, amount: int, taker: str, rounds: int) -> List[Dict]:
        cands = []
        for _ in range(rounds):
            try:
                raw = self.get_order(input_mint, output_mint, amount, taker)
                cands.append({"requestId": raw.get("requestId"), "outAmount": float(raw.get("outAmount", 0)), "priceImpact": float(raw.get("priceImpact", 0)), "totalTime": float(raw.get("totalTime", 0)), "_raw_order": raw})
            except Exception: pass
            time.sleep(QUOTE_BURST_SLEEP_SECONDS)
        return cands

    def execute_order(self, signed_tx_b64: str, req_id: str) -> Dict:
        r = self.session.post(EXECUTE_URL, json={"signedTransaction": signed_tx_b64, "requestId": req_id})
        r.raise_for_status(); return r.json()

@dataclass
class JupiterQuotePoller:
    api_key: str; in_mint: str; out_mint: str; in_dec: int; out_dec: int; amt: int
    def get_snapshot(self) -> Optional[Dict]:
        try:
            r = requests.get(ORDER_URL, headers={"x-api-key": self.api_key}, params={"inputMint": self.in_mint, "outputMint": self.out_mint, "amount": str(self.amt)})
            if r.status_code != 200: return None
            j = r.json()
            if REQUIRE_ULTRA_FOR_TRAINING and j.get("mode") != "ultra": return None
            in_amt, out_amt = float(j.get("inAmount", 0)), float(j.get("outAmount", 0))
            if in_amt <= 0 or out_amt <= 0: return None
            price = (out_amt / (10**self.out_dec)) / (in_amt / (10**self.in_dec))
            return {"ds": pd.Timestamp.now(tz="UTC"), "open": price, "high": price, "low": price, "close": price, "y": price}
        except: return None

@dataclass
class MultiResolutionRuntime:
    raw_df: pd.DataFrame
    dir_states: Dict[str, DirectionCoordinator] = field(default_factory=dict)
    low_states: Dict[str, TimingCoordinator] = field(default_factory=dict)
    high_states: Dict[str, TimingCoordinator] = field(default_factory=dict)
    timing_enabled: bool = False

    def bootstrap(self):
        self.raw_df = ensure_raw_df(self.raw_df)
        self.timing_enabled = is_intraday_df(self.raw_df)
        print(f"\n=== BOOTSTRAP (ENGINE: {MODEL_ENGINE}, TIMING ENABLED: {self.timing_enabled}) ===")
        for r in CADENCE_RULES:
            views = build_training_views_for_rule(self.raw_df, r)
            self.dir_states[r] = DirectionCoordinator(build_dir_agents(r)).fit_all(views["direction_df"], cache_dir=MODEL_CACHE_DIR, force_retrain=FORCE_RETRAIN)
            if self.timing_enabled:
                self.low_states[r] = TimingCoordinator(build_low_agents(r), "low").fit_all(views["low_df"], cache_dir=MODEL_CACHE_DIR, force_retrain=FORCE_RETRAIN)
                self.high_states[r] = TimingCoordinator(build_high_agents(r), "high").fit_all(views["high_df"], cache_dir=MODEL_CACHE_DIR, force_retrain=FORCE_RETRAIN)
        return self

    def refit(self):
        self.raw_df = ensure_raw_df(self.raw_df)
        self.timing_enabled = is_intraday_df(self.raw_df)
        for r in CADENCE_RULES:
            views = build_training_views_for_rule(self.raw_df, r)
            self.dir_states[r].fit_all(views["direction_df"], prev_agents=self.dir_states[r].agents)
            if self.timing_enabled:
                if r not in self.low_states: self.low_states[r] = TimingCoordinator(build_low_agents(r), "low")
                if r not in self.high_states: self.high_states[r] = TimingCoordinator(build_high_agents(r), "high")
                self.low_states[r].fit_all(views["low_df"], prev_agents=self.low_states[r].agents)
                self.high_states[r].fit_all(views["high_df"], prev_agents=self.high_states[r].agents)
        return self

    def infer(self) -> Dict:
        d_vote, d_str = 0.0, 0.0
        target_ts, target_price = None, None
        per_rule = {}
        for r in CADENCE_RULES:
            res_d = self.dir_states[r].aggregate()
            d_vote += CADENCE_WEIGHTS.get(r, 0) * (1.0 if res_d["final_action"]=="BUY" else -1.0 if res_d["final_action"]=="SELL" else 0.0)
            d_str += CADENCE_WEIGHTS.get(r, 0) * res_d["weighted_score"]
            per_rule[r] = {"dir": res_d, "low": self.low_states[r].aggregate() if self.timing_enabled else None, "high": self.high_states[r].aggregate() if self.timing_enabled else None}
        
        final_action = "BUY" if d_vote > 0.2 and d_str > 0 else "SELL" if d_vote < -0.2 and d_str < 0 else "HOLD"
        
        if self.timing_enabled and final_action != "HOLD":
            k = "low" if final_action == "BUY" else "high"
            pairs = [(per_rule[r][k]["predicted_timestamp"], CADENCE_WEIGHTS.get(r,0)) for r in CADENCE_RULES if per_rule[r][k]]
            prices = [(per_rule[r][k]["predicted_price"], CADENCE_WEIGHTS.get(r,0)) for r in CADENCE_RULES if per_rule[r][k]]
            if pairs:
                target_ts = weighted_timestamp(pairs)
                target_price = sum(p*w for p,w in prices) / (sum(w for _,w in prices) or 1.0)

        return {"action": final_action, "ts": target_ts, "price": target_price, "timing": self.timing_enabled}

# =========================================================
# MAIN
# =========================================================

def main():
    require_api_key()
    if os.path.exists(PRICE_HISTORY_CSV):
        df = pd.read_csv(PRICE_HISTORY_CSV)
    else:
        df = fetch_fallback_data(TARGET_COIN_SYMBOL)
        df.to_csv(PRICE_HISTORY_CSV, index=False)
    
    runtime = MultiResolutionRuntime(raw_df=df).bootstrap()
    
    poller = JupiterQuotePoller(JUPITER_API_KEY, INPUT_MINT_FOR_PRICE, OUTPUT_MINT_FOR_PRICE, INPUT_DECIMALS, OUTPUT_DECIMALS, QUOTE_AMOUNT_IN_SMALLEST_UNIT)
    buffer_df = df.copy()

    print(f"\n[START] {TARGET_COIN_SYMBOL} Trading Loop Started. (Engine: {MODEL_ENGINE})")

    for i in range(MAX_ITERATIONS):
        new_rows = [poller.get_snapshot() for _ in range(QUOTE_BURST_COUNT)]
        new_rows = [r for r in new_rows if r]
        
        if new_rows:
            buffer_df = pd.concat([buffer_df, pd.DataFrame(new_rows)], ignore_index=True)
            if len(buffer_df) > 5000: buffer_df = buffer_df.iloc[-5000:]
            
            # 재학습
            runtime.raw_df = buffer_df
            runtime.refit()
            decision = runtime.infer()
            
            print(f"[Iter {i}] Action: {decision['action']} | Target TS: {decision['ts']} | Timing Enabled: {decision['timing']}")
        
        time.sleep(POLL_EVERY_SECONDS)

if __name__ == "__main__":
    main()