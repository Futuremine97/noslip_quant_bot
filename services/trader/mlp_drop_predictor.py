#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import time
import pickle
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT_DIR / "services" / "trader" / "model_cache"

def compute_mlp_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute technical indicators as features for the MLP drop predictor."""
    df = df.copy()
    close_series = df["close"]
    volume_series = df["volume"]
    
    # 1. RSI 14
    delta = close_series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    df["rsi"] = 100 - (100 / (1 + rs))
    
    # 2. MACD
    df["ema12"] = close_series.ewm(span=12, adjust=False).mean()
    df["ema26"] = close_series.ewm(span=26, adjust=False).mean()
    df["macd"] = df["ema12"] - df["ema26"]
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    
    # 3. Bollinger Bands Bandwidth
    df["bb_mid"] = close_series.rolling(window=20).mean()
    df["bb_std"] = close_series.rolling(window=20).std()
    df["bb_upper"] = df["bb_mid"] + 2.0 * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - 2.0 * df["bb_std"]
    df["bb_bandwidth"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"].replace(0, 1e-9)
    
    # 4. Short-term Price Momentum (5m, 15m)
    df["price_change_5m"] = close_series.pct_change(5) * 100.0
    df["price_change_15m"] = close_series.pct_change(15) * 100.0
    
    # 5. Volume ratio
    vol_ma = volume_series.rolling(30).mean().replace(0, 1e-9)
    df["volume_ratio"] = volume_series / vol_ma
    
    # 6. MA Ratio (sma 5 / sma 20)
    sma_5 = close_series.rolling(5).mean()
    sma_20 = close_series.rolling(20).mean().replace(0, 1e-9)
    df["ma_ratio_5_20"] = sma_5 / sma_20
    
    return df

def train_mlp_drop_predictor(symbol: str):
    """
    Fetch the last 1000 minutes of price history from Binance Spot
    and train an MLP model to predict price drop in the next 15 minutes.
    """
    print(f"🧠 [MLP Training] Training drop predictor for {symbol}...")
    
    # Lazy import to avoid circular dependency
    from whale_pump_monitor import fetch_recent_klines
    
    df = fetch_recent_klines(symbol, limit=1000)
    if df.empty or len(df) < 150:
        print(f"⚠️ [MLP Training] Not enough data to train for {symbol} (rows: {len(df)})")
        return False
        
    df = compute_mlp_features(df)
    
    # Target: 1 if close price 15m later is lower than current close price, 0 otherwise
    df["target"] = (df["close"].shift(-15) < df["close"]).astype(int)
    
    feature_cols = [
        "rsi", "macd_hist", "bb_bandwidth", 
        "price_change_5m", "price_change_15m", 
        "volume_ratio", "ma_ratio_5_20"
    ]
    
    # Clean up NaNs and Infinities
    df = df.replace([np.inf, -np.inf], np.nan)
    valid_data = df.dropna(subset=feature_cols + ["target"])
    if len(valid_data) < 100:
        print(f"⚠️ [MLP Training] Too few valid rows ({len(valid_data)}) for {symbol}")
        return False
        
    X = valid_data[feature_cols].values
    y = valid_data["target"].values
    
    from sklearn.preprocessing import StandardScaler
    from sklearn.neural_network import MLPClassifier
    
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    # Train the MLP
    model = MLPClassifier(
        hidden_layer_sizes=(16, 8),
        activation="tanh",
        max_iter=300,
        random_state=42,
        early_stopping=True,
        validation_fraction=0.1
    )
    
    import warnings
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        model.fit(X_scaled, y)
    
    # Save the model & scaler
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    model_path = CACHE_DIR / f"mlp_drop_model_{symbol}.pkl"
    scaler_path = CACHE_DIR / f"mlp_drop_scaler_{symbol}.pkl"
    
    with open(model_path, "wb") as f:
        pickle.dump(model, f)
    with open(scaler_path, "wb") as f:
        pickle.dump(scaler, f)
        
    val_score = getattr(model, "best_validation_score_", 0.0)
    print(f"✅ [MLP Training] Trained model for {symbol}. Validation Accuracy: {val_score:.2%}")
    return True

def should_halt_due_to_mlp_drop(symbol: str, df: pd.DataFrame) -> tuple[bool, float]:
    """
    Checks if MLP model predicts a price drop for the symbol.
    Returns (should_halt, drop_probability).
    """
    model_path = CACHE_DIR / f"mlp_drop_model_{symbol}.pkl"
    scaler_path = CACHE_DIR / f"mlp_drop_scaler_{symbol}.pkl"
    
    if not model_path.exists() or not scaler_path.exists():
        return False, 0.0
        
    try:
        with open(model_path, "rb") as f:
            model = pickle.load(f)
        with open(scaler_path, "rb") as f:
            scaler = pickle.load(f)
            
        # Compute features on copy
        df_feat = compute_mlp_features(df.copy())
        
        feature_cols = [
            "rsi", "macd_hist", "bb_bandwidth", 
            "price_change_5m", "price_change_15m", 
            "volume_ratio", "ma_ratio_5_20"
        ]
        
        latest_row = df_feat[feature_cols].iloc[-1]
        if latest_row.isna().any():
            return False, 0.0
            
        X = latest_row.values.reshape(1, -1)
        X_scaled = scaler.transform(X)
        
        # Predict probability of a drop (class 1)
        prob = float(model.predict_proba(X_scaled)[0][1])
        
        # Load configuration settings
        from whale_pump_monitor import load_whale_config
        config = load_whale_config()
        
        mlp_config = config.get(symbol, {}).get("mlp_filter", {})
        enabled = mlp_config.get("enabled", True)
        halt_threshold = mlp_config.get("halt_threshold", 0.50)
        
        if enabled and prob >= halt_threshold:
            return True, prob
            
        return False, prob
    except Exception as e:
        print(f"⚠️ [MLP Inference Error] Failed to predict for {symbol}: {e}")
        return False, 0.0

def send_mlp_halt_alert(symbol: str, strategy: str, prob: float, price: float):
    """Broadcast a Telegram alert explaining the MLP trade block."""
    display_names = {
        "whale_pump": "고래 수급 (Whale Pump)",
        "rsi_reversion": "RSI 과매도 반등 (RSI Reversion)",
        "macd_crossover": "MACD 골든크로스 (MACD Crossover)",
        "bb_breakout": "볼린저 밴드 돌파 (BB Breakout)",
        "spot_arbitrage": "양방향 거래소 차익거래 (Spot Arbitrage)",
        "kimchi_arbitrage": "김치 프리미엄 차익거래 (Kimchi Arbitrage)",
        "three_way_arbitrage": "3자간 무위험 차익거래 (3-Way Arbitrage)"
    }
    strat_name = display_names.get(strategy, strategy)
    display_sym = symbol.replace("USDT", "")
    
    # Load halt threshold
    from whale_pump_monitor import load_whale_config, send_telegram_message
    config = load_whale_config()
    mlp_config = config.get(symbol, {}).get("mlp_filter", {})
    halt_threshold = mlp_config.get("halt_threshold", 0.50)
    
    lines = [
        f"🚫 <b>[No Slip MLP Filter] 매매 진입 차단 (하락 예측)</b>",
        "=" * 40,
        f"⚠️ <b>{display_sym} 전략 진입 신호 발생했으나 차단됨</b>",
        f"  • 대상 전략: {strat_name}",
        f"  • 현재 가격: ${price:,.2f}" if price > 0 else "  • 현재 가격: N/A",
        f"  • <b>MLP 하락 예측 확률</b>: <b>{prob*100:.1f}%</b> (차단 임계치: {halt_threshold*100:.1f}%)",
        "  • <b>진입 제한 사유</b>: 다층 인공신경망(MLP) 분석 결과, 향후 15분 이내 단기 하락 확률이 우세하여 손실 방지를 위해 강제 차단(Halt) 처리를 적용했습니다.",
        "=" * 40,
        "※ 본 차단 필터는 머신러닝 실시간 예측 엔진에 의해 상시 작동 중입니다."
    ]
    send_telegram_message("\n".join(lines), strategy=strategy)
