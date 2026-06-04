#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sector Orbit Learner.
Tracks and models GICS sector centroid coordinates in information space over time.
Fits an SVD + residual MLP model in numpy to predict coordinate transitions.
Generates dark-themed trajectory plots.
"""

from __future__ import annotations
import os
import sys
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Matplotlib configuration (headless friendly)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.trader.map_store import MODEL_CACHE_DIR, today_market_date

DB_PATH = MODEL_CACHE_DIR / "sp500_information_map_history.sqlite3"
MODEL_SAVE_PATH = MODEL_CACHE_DIR / "sector_orbit_models.json"
ORBIT_PLOT_PATH = ROOT_DIR / "data" / "sector_orbits.png"

# Sector name mappings
SECTOR_SHORT_LABELS = {
    "Information Technology": "XLK",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Utilities": "XLU",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}

SECTOR_COLORS = {
    "Information Technology": "#00E5FF",      # Bright Cyan
    "Financials": "#FFD600",                  # Vibrant Yellow
    "Health Care": "#00E676",                  # Neon Green
    "Energy": "#FF3D00",                      # Red Orange
    "Industrials": "#2979FF",                 # Royal Blue
    "Consumer Discretionary": "#FF1744",       # Hot Pink/Red
    "Consumer Staples": "#D500F9",             # Magenta/Purple
    "Utilities": "#FF9100",                   # Deep Orange
    "Materials": "#AEEA00",                   # Lime Green
    "Real Estate": "#E040FB",                 # Orchid/Pink
    "Communication Services": "#651FFF",       # Indigo/Violet
}

GICS_KOREAN_LABELS = {
    "Information Technology": "정보기술 (IT)",
    "Financials": "금융",
    "Health Care": "헬스케어",
    "Energy": "에너지",
    "Industrials": "산업재",
    "Consumer Discretionary": "자유소비재",
    "Consumer Staples": "필수소비재",
    "Utilities": "유틸리티",
    "Materials": "소재",
    "Real Estate": "부동산",
    "Communication Services": "커뮤니케이션",
}

BASE_KEYS = [
    "momentum_x", "momentum_y", "conviction_x", "conviction_y",
    "momentum_x_spread", "momentum_y_spread", "conviction_x_spread", "conviction_y_spread"
]


def _safe_float(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(numeric):
        return None
    return numeric


def _vector_from_snapshot(row: Dict[str, Any]) -> Optional[np.ndarray]:
    mx = _safe_float(row.get("momentum_space_x"))
    my = _safe_float(row.get("momentum_space_y"))
    cx = _safe_float(row.get("conviction_space_x"))
    cy = _safe_float(row.get("conviction_space_y"))
    
    # Dynamic fallback calculation if database coordinate columns are missing/null
    if mx is None or my is None or cx is None or cy is None:
        f_moment = _safe_float(row.get("first_moment_pct_per_day"))
        s_moment = _safe_float(row.get("second_moment_bp_per_day2"))
        u_ratio = _safe_float(row.get("uncertainty_ratio")) or 0.05
        
        if f_moment is None or s_moment is None:
            return None
            
        # 1st coordinate space (momentum space) transform
        transformed_first = np.sign(f_moment) * np.log1p(abs(f_moment) * 10.0)
        transformed_second = np.sign(s_moment) * np.log1p(abs(s_moment) / 5.0)
        mx = float(transformed_first)
        my = float(transformed_second)
        
        # 2nd coordinate space (conviction space) transform
        u_scale = max(0.01, u_ratio)
        cx = float(f_moment / u_scale)
        cy = float(s_moment / u_scale)
        
    return np.asarray([mx, my, cx, cy], dtype=float)


def load_sector_orbits_from_db() -> Dict[str, pd.DataFrame]:
    """
    Loads sector snapshots from DB, dynamically handles coordinate recalculation,
    and returns a dict of DataFrames (one per GICS sector) containing daily aggregated centroids and spreads.
    """
    if not DB_PATH.exists():
        print(f"⚠️ Database not found at {DB_PATH}. Cannot load orbits.")
        return {}
        
    conn = sqlite3.connect(str(DB_PATH))
    df_raw = pd.read_sql_query(
        """
        SELECT map_date, symbol, sector, 
               momentum_space_x, momentum_space_y, conviction_space_x, conviction_space_y,
               first_moment_pct_per_day, second_moment_bp_per_day2, uncertainty_ratio
        FROM map_symbol_snapshots
        ORDER BY map_date ASC, sector ASC
        """,
        conn
    )
    conn.close()
    
    if df_raw.empty:
        return {}
        
    # Convert raw rows to coordinates
    coordinates = []
    for idx, row in df_raw.iterrows():
        coord_vec = _vector_from_snapshot(row.to_dict())
        if coord_vec is not None:
            coordinates.append({
                "map_date": row["map_date"],
                "sector": row["sector"] or "Unknown",
                "symbol": row["symbol"],
                "momentum_x": coord_vec[0],
                "momentum_y": coord_vec[1],
                "conviction_x": coord_vec[2],
                "conviction_y": coord_vec[3],
            })
            
    df_coords = pd.DataFrame(coordinates)
    if df_coords.empty:
        return {}
        
    # Group by date and sector to calculate centroids and spreads
    grouped = df_coords.groupby(["sector", "map_date"]).agg(
        momentum_x_centroid=("momentum_x", "mean"),
        momentum_y_centroid=("momentum_y", "mean"),
        conviction_x_centroid=("conviction_x", "mean"),
        conviction_y_centroid=("conviction_y", "mean"),
        momentum_x_spread=("momentum_x", "std"),
        momentum_y_spread=("momentum_y", "std"),
        conviction_x_spread=("conviction_x", "std"),
        conviction_y_spread=("conviction_y", "std"),
        member_count=("symbol", "count")
    ).reset_index()
    
    # Fill spreads with 0 if std is NaN (e.g. single stock in sector)
    grouped = grouped.fillna(0.0)
    
    # Split into per-sector DataFrames
    sector_dfs = {}
    for sector in grouped["sector"].unique():
        sector_dfs[sector] = grouped[grouped["sector"] == sector].sort_values("map_date").reset_index(drop=True)
        
    return sector_dfs


def _submanifold_vector(
    current: np.ndarray,
    previous: Optional[np.ndarray],
    earlier: Optional[np.ndarray],
) -> np.ndarray:
    first_delta = current - previous if previous is not None else np.zeros_like(current)
    second_delta = first_delta - (previous - earlier) if previous is not None and earlier is not None else np.zeros_like(current)
    return np.concatenate([current, first_delta, second_delta], axis=0)


def _prepare_state_matrix(df: pd.DataFrame) -> Tuple[List[str], np.ndarray]:
    """
    Constructs the state sequence matrix for a sector's orbit history.
    Each row combines [State, Delta_1, Delta_2].
    """
    feature_cols = [
        "momentum_x_centroid", "momentum_y_centroid", "conviction_x_centroid", "conviction_y_centroid",
        "momentum_x_spread", "momentum_y_spread", "conviction_x_spread", "conviction_y_spread"
    ]
    raw_states = df[feature_cols].to_numpy(dtype=float)
    dates = df["map_date"].tolist()
    
    vectors = []
    for i in range(len(raw_states)):
        curr = raw_states[i]
        prev = raw_states[i - 1] if i - 1 >= 0 else None
        earl = raw_states[i - 2] if i - 2 >= 0 else None
        vectors.append(_submanifold_vector(curr, prev, earl))
        
    return dates, np.vstack(vectors)


def _fit_svd_embedding(matrix: np.ndarray, rank: int = 3) -> Dict[str, Any]:
    mean = matrix.mean(axis=0)
    std = matrix.std(axis=0)
    std = np.where(std < 1e-6, 1.0, std)
    normalized = (matrix - mean) / std
    
    _, s, vt = np.linalg.svd(normalized, full_matrices=False)
    actual_rank = max(1, min(rank, vt.shape[0], normalized.shape[0]))
    basis = vt[:actual_rank]
    latent = normalized @ basis.T
    
    return {
        "mean": mean,
        "std": std,
        "basis": basis,
        "latent": latent,
        "rank": actual_rank,
    }


def _train_residual_mlp(latent: np.ndarray) -> Dict[str, Any]:
    """
    Trains a numpy-based MLP transition model: Predicts delta_latent from current_latent.
    """
    if len(latent) < 3:
        return {"mode": "identity"}
        
    x_train = latent[:-1]
    y_train = latent[1:] - latent[:-1]
    
    in_dim = x_train.shape[1]
    hidden_dim = max(4, in_dim * 2)
    
    rng = np.random.default_rng(42)
    w1 = rng.normal(0.0, 0.15, size=(in_dim, hidden_dim))
    b1 = np.zeros((hidden_dim,), dtype=float)
    w2 = rng.normal(0.0, 0.15, size=(hidden_dim, in_dim))
    b2 = np.zeros((in_dim,), dtype=float)
    
    lr = 0.05
    epochs = 300
    l2 = 1e-3
    
    for _ in range(epochs):
        hidden = np.tanh(x_train @ w1 + b1)
        pred = hidden @ w2 + b2
        err = pred - y_train
        
        grad_pred = (2.0 / len(x_train)) * err
        grad_w2 = hidden.T @ grad_pred + l2 * w2
        grad_b2 = grad_pred.sum(axis=0)
        grad_hidden = grad_pred @ w2.T
        grad_hidden_pre = grad_hidden * (1.0 - np.square(hidden))
        grad_w1 = x_train.T @ grad_hidden_pre + l2 * w1
        grad_b1 = grad_hidden_pre.sum(axis=0)
        
        w2 -= lr * grad_w2
        b2 -= lr * grad_b2
        w1 -= lr * grad_w1
        b1 -= lr * grad_b1
        
    return {
        "mode": "residual_mlp",
        "w1": w1,
        "b1": b1,
        "w2": w2,
        "b2": b2
    }


def _predict_next_latent(last_latent: np.ndarray, model: Dict[str, Any]) -> np.ndarray:
    if model.get("mode") != "residual_mlp":
        return last_latent
    hidden = np.tanh(last_latent @ model["w1"] + model["b1"])
    delta = hidden @ model["w2"] + model["b2"]
    return last_latent + delta


def _decode_latent(latent_vector: np.ndarray, svd_fit: Dict[str, Any]) -> np.ndarray:
    normalized = latent_vector @ svd_fit["basis"]
    return normalized * svd_fit["std"] + svd_fit["mean"]


def train_sector_orbits() -> Dict[str, Any]:
    """
    Fits SVD and Residual Neural Bridge transition models on sector coordinate histories,
    and returns metrics, predictions, and model states.
    """
    sector_dfs = load_sector_orbits_from_db()
    if not sector_dfs:
        return {}
        
    learned_state = {}
    
    for sector, df in sector_dfs.items():
        if len(df) < 3:
            # Skip or use static transition if history is too short
            learned_state[sector] = {
                "status": "warmup",
                "dates": df["map_date"].tolist(),
                "history_count": len(df),
            }
            continue
            
        dates, state_matrix = _prepare_state_matrix(df)
        svd_fit = _fit_svd_embedding(state_matrix, rank=3)
        latent = svd_fit["latent"]
        
        # Train MLP
        mlp_model = _train_residual_mlp(latent)
        
        # Forecast
        last_latent = latent[-1]
        next_latent = _predict_next_latent(last_latent, mlp_model)
        
        # Decode forecast (24-dim space -> extract first 8 components)
        curr_decoded = _decode_latent(last_latent, svd_fit)[:8]
        next_decoded = _decode_latent(next_latent, svd_fit)[:8]
        
        # Ensure values are safe floats
        curr_decoded = [float(v) for v in curr_decoded]
        next_decoded = [float(v) for v in next_decoded]
        
        # Calculate velocity (last step difference of centroids)
        raw_states = df[[
            "momentum_x_centroid", "momentum_y_centroid", "conviction_x_centroid", "conviction_y_centroid"
        ]].to_numpy(dtype=float)
        
        v_current = raw_states[-1] - raw_states[-2] if len(raw_states) >= 2 else np.zeros(4)
        v_prev = raw_states[-2] - raw_states[-3] if len(raw_states) >= 3 else v_current
        accel = v_current - v_prev
        
        # Curvature: turn angle of velocity vector
        v_norm = np.linalg.norm(v_current[:2])
        prev_norm = np.linalg.norm(v_prev[:2])
        if v_norm > 1e-6 and prev_norm > 1e-6:
            cos_theta = np.dot(v_current[:2], v_prev[:2]) / (v_norm * prev_norm)
            cos_theta = np.clip(cos_theta, -1.0, 1.0)
            curvature = float(np.arccos(cos_theta)) # in radians
        else:
            curvature = 0.0
            
        # Parse into serialized dict
        learned_state[sector] = {
            "status": "active",
            "history_count": len(df),
            "current_centroid": {
                "momentum_x": curr_decoded[0],
                "momentum_y": curr_decoded[1],
                "conviction_x": curr_decoded[2],
                "conviction_y": curr_decoded[3],
            },
            "predicted_centroid": {
                "momentum_x": next_decoded[0],
                "momentum_y": next_decoded[1],
                "conviction_x": next_decoded[2],
                "conviction_y": next_decoded[3],
            },
            "current_spread": {
                "momentum_x": max(0.01, curr_decoded[4]),
                "momentum_y": max(0.01, curr_decoded[5]),
                "conviction_x": max(0.01, curr_decoded[6]),
                "conviction_y": max(0.01, curr_decoded[7]),
            },
            "velocity": {
                "momentum_x": float(v_current[0]),
                "momentum_y": float(v_current[1]),
                "conviction_x": float(v_current[2]),
                "conviction_y": float(v_current[3]),
                "speed": float(np.linalg.norm(v_current)),
            },
            "acceleration": float(np.linalg.norm(accel)),
            "curvature": curvature,
            "member_count": int(df["member_count"].iloc[-1]),
            "trail": df[[
                "map_date", "momentum_x_centroid", "momentum_y_centroid", "conviction_x_centroid", "conviction_y_centroid"
            ]].to_dict("records"),
        }
        
    # Persist model parameters (optionally serialize mlp weights too)
    try:
        # Save a clean, JSON-serializable representation
        serialized = {}
        for sector, info in learned_state.items():
            if info["status"] == "active":
                serialized[sector] = {
                    "current_centroid": info["current_centroid"],
                    "predicted_centroid": info["predicted_centroid"],
                    "current_spread": info["current_spread"],
                    "velocity": info["velocity"],
                    "acceleration": info["acceleration"],
                    "curvature": info["curvature"],
                    "member_count": info["member_count"],
                }
        MODEL_SAVE_PATH.write_text(json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"⚠️ Error saving model state: {e}")
        
    return learned_state


def determine_quadrant(x: float, y: float) -> str:
    if x > 0 and y > 0:
        return "Breakout Accel"
    elif x > 0 and y <= 0:
        return "Uptrend Cooling"
    elif x <= 0 and y > 0:
        return "Recovery Setup"
    else:
        return "Selloff Accel"


def generate_and_save_sector_orbit_plot(
    learned_state: Dict[str, Any], 
    photo_path: Path = ORBIT_PLOT_PATH
) -> None:
    """
    Renders a premium dark-themed 2D Matplotlib plot representing GICS sector centroid trails
    in momentum space (X: momentum_x, Y: momentum_y) along with predicted drift directions.
    """
    photo_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Set premium dark-mode styling
    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(10, 8), dpi=300)
    fig.patch.set_facecolor("#121212")
    ax.set_facecolor("#1a1a1a")
    
    # Draw quadrant grids and borders
    ax.axhline(0, color="#333333", linestyle="-", linewidth=1.2)
    ax.axvline(0, color="#333333", linestyle="-", linewidth=1.2)
    ax.grid(color="#2a2a2a", linestyle="--", linewidth=0.6)
    
    # Add subtle glows/shading for quadrants
    # Quadrant Labels (placed in corners)
    ax.text(3.5, 3.5, "Breakout Acceleration\n(High Mom + High Vol)", color="#00E676", alpha=0.5, fontsize=10, ha="right", va="top")
    ax.text(-3.5, 3.5, "Recovery Setup\n(Low Mom + High Vol)", color="#2979FF", alpha=0.5, fontsize=10, ha="left", va="top")
    ax.text(3.5, -3.5, "Uptrend Cooling\n(High Mom + Low Vol)", color="#FFD600", alpha=0.5, fontsize=10, ha="right", va="bottom")
    ax.text(-3.5, -3.5, "Selloff Acceleration\n(Low Mom + Low Vol)", color="#FF1744", alpha=0.5, fontsize=10, ha="left", va="bottom")
    
    # Plot sector orbits
    for sector, info in learned_state.items():
        if info["status"] != "active":
            continue
            
        color = SECTOR_COLORS.get(sector, "#FFFFFF")
        label = SECTOR_SHORT_LABELS.get(sector, sector[:4].upper())
        
        trail = info["trail"]
        dates = [t["map_date"] for t in trail]
        tx = [t["momentum_x_centroid"] for t in trail]
        ty = [t["momentum_y_centroid"] for t in trail]
        
        # Plot historical trail with fade effect (older -> semi-transparent, newer -> solid)
        n_points = len(tx)
        for i in range(n_points - 1):
            alpha = 0.15 + 0.65 * (i / max(1, n_points - 1))
            ax.plot(tx[i:i+2], ty[i:i+2], color=color, alpha=alpha, linewidth=2.0)
            
        # Draw small arrowheads along the trail
        if n_points >= 2:
            mid = n_points // 2
            ax.annotate(
                "",
                xy=(tx[mid+1], ty[mid+1]),
                xytext=(tx[mid], ty[mid]),
                arrowprops=dict(arrowstyle="->", color=color, alpha=0.6, lw=1.5),
            )
            
        # Plot current centroid
        curr_x = info["current_centroid"]["momentum_x"]
        curr_y = info["current_centroid"]["momentum_y"]
        ax.scatter(curr_x, curr_y, color=color, s=120, edgecolors="#FFFFFF", linewidth=1.5, zorder=5, label=f"{label} ({info['member_count']} symbols)")
        
        # Draw text label next to current centroid
        ax.text(
            curr_x + 0.08, curr_y + 0.08, 
            label, 
            color=color, 
            fontsize=10, 
            weight="bold",
            bbox=dict(facecolor="#121212", alpha=0.7, edgecolor="none", pad=1.5)
        )
        
        # Plot predicted centroid drift (dashed line and hollow marker)
        pred_x = info["predicted_centroid"]["momentum_x"]
        pred_y = info["predicted_centroid"]["momentum_y"]
        ax.plot([curr_x, pred_x], [curr_y, pred_y], color=color, linestyle="--", alpha=0.7, linewidth=1.2)
        ax.scatter(pred_x, pred_y, facecolors="none", edgecolors=color, s=80, marker="o", linestyle="--", linewidth=1.5, zorder=4)
        
    ax.set_title("GICS Sector Orbits & Predicted Trajectories\n(Momentum Coordinate Space)", fontsize=14, weight="bold", color="#FFFFFF", pad=15)
    ax.set_xlabel("Momentum Score (X: Direction)", fontsize=11, color="#AAAAAA", labelpad=8)
    ax.set_ylabel("Volatility/Momentum Spread (Y: Uncertainty)", fontsize=11, color="#AAAAAA", labelpad=8)
    
    # Adjust axes limits dynamically
    all_x = []
    all_y = []
    for sector, info in learned_state.items():
        if info["status"] == "active":
            all_x.extend([info["current_centroid"]["momentum_x"], info["predicted_centroid"]["momentum_x"]])
            all_y.extend([info["current_centroid"]["momentum_y"], info["predicted_centroid"]["momentum_y"]])
            for t in info["trail"]:
                all_x.append(t["momentum_x_centroid"])
                all_y.append(t["momentum_y_centroid"])
                
    if all_x and all_y:
        x_min, x_max = min(all_x), max(all_x)
        y_min, y_max = min(all_y), max(all_y)
        padding_x = max(0.5, (x_max - x_min) * 0.15)
        padding_y = max(0.5, (y_max - y_min) * 0.15)
        ax.set_xlim(x_min - padding_x, x_max + padding_x)
        ax.set_ylim(y_min - padding_y, y_max + padding_y)
    else:
        ax.set_xlim(-4, 4)
        ax.set_ylim(-4, 4)
        
    # Style legend
    legend = ax.legend(loc="upper right", bbox_to_anchor=(1.0, 1.0), framealpha=0.2, facecolor="#121212", edgecolor="#444444", fontsize=9)
    for text in legend.get_texts():
        text.set_color("#FFFFFF")
        
    plt.tight_layout()
    plt.savefig(str(photo_path), facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close()
    print(f"📊 Saved sector orbit trajectory plot to {photo_path}")


def analyze_and_rank_sectors(learned_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Ranks sectors by their trajectory/orbit velocity, drift into positive quadrants,
    and returns a sorted summary.
    """
    ranked = []
    for sector, info in learned_state.items():
        if info["status"] != "active":
            continue
            
        curr_x = info["current_centroid"]["momentum_x"]
        curr_y = info["current_centroid"]["momentum_y"]
        pred_x = info["predicted_centroid"]["momentum_x"]
        pred_y = info["predicted_centroid"]["momentum_y"]
        
        curr_quad = determine_quadrant(curr_x, curr_y)
        pred_quad = determine_quadrant(pred_x, pred_y)
        
        # Momentum score of the sector
        score = curr_x * 0.6 + curr_y * 0.2 + info["velocity"]["speed"] * 0.2
        
        ranked.append({
            "sector": sector,
            "korean_label": GICS_KOREAN_LABELS.get(sector, sector),
            "short_label": SECTOR_SHORT_LABELS.get(sector, sector[:4].upper()),
            "curr_x": curr_x,
            "curr_y": curr_y,
            "pred_x": pred_x,
            "pred_y": pred_y,
            "curr_quadrant": curr_quad,
            "pred_quadrant": pred_quad,
            "speed": info["velocity"]["speed"],
            "acceleration": info["acceleration"],
            "curvature": info["curvature"],
            "score": score,
            "dispersion": info["current_spread"]["momentum_x"],
            "member_count": info["member_count"]
        })
        
    # Rank by score descending
    ranked = sorted(ranked, key=lambda x: x["score"], reverse=True)
    return ranked


def build_orbit_text_report(ranked_sectors: List[Dict[str, Any]]) -> str:
    """
    Generates an HTML-formatted report summarizing sector orbit learning results.
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    lines = [
        f"🌀 <b>[No Slip] GICS 섹터 오빗(Orbit) 학습 보고서 ({date_str})</b>",
        "<i>정보 기하학 2D/4D 공간상의 섹터별 무게중심 궤적과 전이 확률망 학습 완료</i>",
        "=" * 40,
        "🔥 <b>가속 및 우상향 국면 (Breakout/Recovery Top 3)</b>",
    ]
    
    top_sectors = [s for s in ranked_sectors if s["curr_quadrant"] in {"Breakout Accel", "Recovery Setup"}][:3]
    # Fallback to absolute highest scores if top quadrant list is empty
    if not top_sectors:
        top_sectors = ranked_sectors[:3]
        
    for rank, s in enumerate(top_sectors, start=1):
        quad_emoji = "🟢" if s["curr_quadrant"] == "Breakout Accel" else "🟣"
        shift_text = ""
        if s["curr_quadrant"] != s["pred_quadrant"]:
            shift_text = f" ➡️ {s['pred_quadrant']}"
            
        lines.append(
            f"  {rank}. <b>{s['korean_label']} ({s['short_label']})</b>\n"
            f"    · 국면: {quad_emoji} {s['curr_quadrant']}{shift_text}\n"
            f"    · 모멘텀: X={s['curr_x']:+.2f} | 궤적 속도: {s['speed']:.3f} | 분산: {s['dispersion']:.2f}"
        )
        
    lines.append("")
    lines.append("🛡️ <b>오빗 특징 및 수렴/발산 (Convergence/Divergence)</b>")
    
    # Find most accelerating sector
    most_accel = max(ranked_sectors, key=lambda x: x["acceleration"])
    lines.append(f"  • <b>가장 강한 가속도</b>: {most_accel['korean_label']} (a={most_accel['acceleration']:.3f})")
    
    # Find highest dispersion (divergence of views)
    highest_disp = max(ranked_sectors, key=lambda x: x["dispersion"])
    lines.append(f"  • <b>의견 발산 (최고 분산)</b>: {highest_disp['korean_label']} (σ={highest_disp['dispersion']:.2f}) → 변동성 대비 필요")
    
    # Find lowest dispersion (consensus)
    lowest_disp = min(ranked_sectors, key=lambda x: x["dispersion"])
    lines.append(f"  • <b>의견 수렴 (최저 분산)</b>: {lowest_disp['korean_label']} (σ={lowest_disp['dispersion']:.2f}) → 강한 방향성 준비")
    
    lines.append("")
    lines.append("🔴 <b>하락 가속 / 관망 국면 (Selloff Accel)</b>")
    selloff_sectors = [s for s in ranked_sectors if s["curr_quadrant"] == "Selloff Accel"]
    if selloff_sectors:
        for s in selloff_sectors[:3]:
            lines.append(f"  • {s['korean_label']} ({s['short_label']}) — X={s['curr_x']:+.2f} | Y={s['curr_y']:+.2f} | 속도: {s['speed']:.3f}")
    else:
        lines.append("  • 현재 하락 가속 국면에 속한 주요 섹터가 없습니다.")
        
    lines.append("=" * 40)
    lines.append("※ 개별 종목의 다차원 좌표를 가우시안 무게중심 및 SVD+MLP 잔차 학습망으로 모델링한 결과입니다. 투자 참고용입니다.")
    
    return "\n".join(lines)


def run_pipeline() -> Tuple[List[Dict[str, Any]], str]:
    print("🚀 Running GICS sector orbit learning pipeline...")
    learned_state = train_sector_orbits()
    if not learned_state:
        print("⚠️ No sector orbit data available.")
        return [], "⚠️ 데이터 베이스에서 섹터 궤적 정보를 불러올 수 없습니다."
        
    ranked = analyze_and_rank_sectors(learned_state)
    generate_and_save_sector_orbit_plot(learned_state, ORBIT_PLOT_PATH)
    report = build_orbit_text_report(ranked)
    
    return ranked, report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Train and plot GICS sector orbits.")
    parser.add_argument("--train", action="store_true", help="Run the full training pipeline.")
    args = parser.parse_args()
    
    if args.train:
        run_pipeline()
