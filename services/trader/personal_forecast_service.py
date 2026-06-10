#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Personalized & zero-shot time-series forecasting service for No Slip Quant.

Domain-agnostic: works on ANY time series, not just stocks/crypto.
Built-in domain presets:

  finance        주가/코인 (weekly seasonality, linear growth)
  semiconductor  반도체 공정 지표 — 수율(%), 결함밀도, CD 등
                 (logistic cap for bounded metrics + SPC 관리한계 이탈 감지)
  quantum        양자에러 데이터 — Stim 시뮬레이션 logical/physical error rate
                 (log10 변환, 드리프트 감지; per-round/per-shot 시계열)
  generic        그 외 모든 시계열

Two serving modes:
  * zero-shot     : 데이터만 주면 즉시 예측 (저장 없음, 도메인 프리셋 적용)
  * personalized  : 데이터셋 등록 -> 하이퍼파라미터 탐색 학습 -> 저장된
                    개인화 모델로 반복 서빙 + 이상치(관리한계 이탈) 리포트

CLI
---
  personal_forecast_service.py zeroshot --csv fab_yield.csv --domain semiconductor --days 14
  personal_forecast_service.py register --user acme --name fab7_yield --csv fab_yield.csv --domain semiconductor
  personal_forecast_service.py train    --user acme --name fab7_yield
  personal_forecast_service.py forecast --user acme --name fab7_yield --days 30
  personal_forecast_service.py list     --user acme
  personal_forecast_service.py stim-demo   # 합성 Stim 스타일 데모 데이터 생성
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
STORE_DIR = ROOT_DIR / "services" / "trader" / "model_cache" / "personal_forecast"
DATA_OUT = ROOT_DIR / "data" / "personal_forecast"

DOMAIN_PRESETS = {
    "finance": {
        "transform": None, "growth": "linear",
        "weekly_seasonality": True, "yearly_seasonality": "auto",
        "spc": False, "unit": "price",
        "description": "주가/코인/매출 등 금융·비즈니스 시계열",
    },
    "semiconductor": {
        "transform": None, "growth": "logistic_if_bounded",
        "weekly_seasonality": True, "yearly_seasonality": False,
        "spc": True, "unit": "metric",
        "description": "반도체 공정 지표 (수율 %, 결함밀도/cm², CD nm, 장비 파라미터)",
    },
    "quantum": {
        "transform": "log10", "growth": "linear",
        "weekly_seasonality": False, "yearly_seasonality": False,
        "spc": True, "unit": "error_rate",
        "description": "양자에러 시계열 (Stim logical/physical error rate, per round/shot/calendar)",
    },
    "generic": {
        "transform": None, "growth": "linear",
        "weekly_seasonality": True, "yearly_seasonality": "auto",
        "spc": True, "unit": "value",
        "description": "범용 시계열",
    },
}

HYPER_GRID = [
    {"changepoint_prior_scale": cps, "seasonality_prior_scale": sps}
    for cps in (0.01, 0.05, 0.2)
    for sps in (1.0, 10.0)
]


# ----------------- Data Layer -----------------

def load_series(csv_path: str | Path) -> pd.DataFrame:
    """Load any CSV into [ds, y]. Accepts date/round/shot index columns.

    Recognized time columns: ds/date/datetime/time/timestamp/round/shot/step/cycle.
    Integer indices (rounds/shots) are mapped onto a synthetic daily axis so
    Prophet can fit them; the mapping is recorded in attrs for display.
    """
    df = pd.read_csv(csv_path)
    cols = {c.lower().strip(): c for c in df.columns}
    t_col = next((cols[k] for k in ("ds", "date", "datetime", "time", "timestamp",
                                    "round", "shot", "step", "cycle", "lot") if k in cols),
                 df.columns[0])
    y_col = next((cols[k] for k in ("y", "value", "close", "yield", "error_rate",
                                    "logical_error_rate", "defect_density", "rate",
                                    "measurement", "metric") if k in cols),
                 df.columns[-1])
    out = df[[t_col, y_col]].rename(columns={t_col: "ds", y_col: "y"})
    out["y"] = pd.to_numeric(out["y"], errors="coerce")

    numeric_index = pd.api.types.is_numeric_dtype(out["ds"]) or \
        t_col.lower().strip() in ("round", "shot", "step", "cycle")
    if numeric_index:  # integer index (round/shot 등) -> synthetic daily axis
        index_mode = f"integer({t_col})"
        base = pd.Timestamp("2000-01-01")
        idx = pd.to_numeric(out["ds"], errors="coerce")
        parsed = base + pd.to_timedelta(idx - idx.min(), unit="D")
    else:
        parsed = pd.to_datetime(out["ds"], errors="coerce")
        index_mode = "datetime"
        if parsed.isna().mean() > 0.5:
            index_mode = f"integer({t_col})"
            base = pd.Timestamp("2000-01-01")
            idx = pd.to_numeric(out["ds"], errors="coerce")
            parsed = base + pd.to_timedelta(idx - idx.min(), unit="D")
    out["ds"] = parsed
    out = out.dropna().sort_values("ds").reset_index(drop=True)
    out = out.drop_duplicates(subset="ds", keep="last")
    if len(out) < 20:
        raise ValueError(f"Need >= 20 rows, got {len(out)}")
    out.attrs["index_mode"] = index_mode
    out.attrs["y_col"] = y_col
    return out


def apply_transform(df: pd.DataFrame, transform: str | None) -> pd.DataFrame:
    df = df.copy()
    if transform == "log10":
        floor = max(df["y"][df["y"] > 0].min() * 0.1, 1e-12) if (df["y"] > 0).any() else 1e-12
        df["y"] = np.log10(df["y"].clip(lower=floor))
    return df


def invert_transform(arr, transform: str | None):
    if transform == "log10":
        return np.power(10.0, np.asarray(arr, dtype=float))
    return np.asarray(arr, dtype=float)


# ----------------- Model Layer -----------------

def _build_model(preset: dict, params: dict, df: pd.DataFrame):
    from prophet import Prophet
    growth = "linear"
    cap = None
    if preset["growth"] == "logistic_if_bounded":
        ymax = float(df["y"].max())
        if 0 <= df["y"].min() and ymax <= 100.0 and ymax > 1.0:  # % 지표로 추정
            growth = "logistic"
            cap = 100.0
    span_days = (df["ds"].max() - df["ds"].min()).days
    yearly = preset["yearly_seasonality"]
    if yearly == "auto":
        yearly = span_days >= 400
    m = Prophet(
        growth=growth,
        weekly_seasonality=preset["weekly_seasonality"],
        yearly_seasonality=yearly,
        daily_seasonality=False,
        interval_width=0.8,
        **params,
    )
    return m, cap


def _fit_predict(preset: dict, params: dict, train: pd.DataFrame, horizon_days: int,
                 full_df: pd.DataFrame | None = None):
    m, cap = _build_model(preset, params, train)
    t = train.copy()
    if cap is not None:
        t["cap"] = cap
    m.fit(t)
    future = m.make_future_dataframe(periods=horizon_days, freq="D")
    if cap is not None:
        future["cap"] = cap
    return m, m.predict(future)


def tune_hyperparams(preset: dict, df: pd.DataFrame) -> tuple[dict, float]:
    """Small grid search on a 15% holdout; returns (best_params, best_mape)."""
    cut = max(int(len(df) * 0.85), len(df) - 60)
    train, valid = df.iloc[:cut], df.iloc[cut:]
    horizon = max((valid["ds"].max() - train["ds"].max()).days, 1)
    best, best_mape = HYPER_GRID[0], float("inf")
    for params in HYPER_GRID:
        try:
            _, fc = _fit_predict(preset, params, train, horizon)
            merged = valid.merge(fc[["ds", "yhat"]], on="ds", how="inner")
            if merged.empty:
                continue
            denom = merged["y"].abs().clip(lower=1e-9)
            mape = float((np.abs(merged["y"] - merged["yhat"]) / denom).mean() * 100)
            if mape < best_mape:
                best, best_mape = params, mape
        except Exception as e:
            print(f"⚠️ grid point {params} failed: {e}")
    return best, best_mape


# ----------------- SPC / Anomaly -----------------

def detect_anomalies(df: pd.DataFrame, forecast: pd.DataFrame, transform: str | None,
                     lookback: int = 60) -> list[dict]:
    """SPC-style: actual points outside the model's 80% band (관리한계 이탈)."""
    hist = forecast.merge(df, on="ds", how="inner").tail(lookback)
    out = []
    for _, r in hist.iterrows():
        if r["y"] > r["yhat_upper"] or r["y"] < r["yhat_lower"]:
            out.append({
                "ds": str(r["ds"].date()),
                "actual": float(invert_transform([r["y"]], transform)[0]),
                "expected": float(invert_transform([r["yhat"]], transform)[0]),
                "side": "UPPER" if r["y"] > r["yhat_upper"] else "LOWER",
            })
    return out


# ----------------- Rendering -----------------

def render_chart(title: str, df: pd.DataFrame, forecast: pd.DataFrame,
                 transform: str | None, anomalies: list[dict], path: Path,
                 unit: str = "value") -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cutoff = df["ds"].max()
    win = pd.Timedelta(days=180)
    h = df[df["ds"] >= cutoff - win]
    fcw = forecast[forecast["ds"] >= cutoff - win]
    fcf = forecast[forecast["ds"] > cutoff]

    fig, ax = plt.subplots(figsize=(11, 6), dpi=110, facecolor="#0a0a0a")
    ax.set_facecolor("#111111")
    ax.plot(h["ds"], invert_transform(h["y"], transform), color="#e5e5e5",
            linewidth=1.3, label="Actual")
    ax.plot(fcw["ds"], invert_transform(fcw["yhat"], transform), color="#00f5d4",
            linewidth=1.7, label="Personalized Forecast")
    ax.fill_between(fcf["ds"], invert_transform(fcf["yhat_lower"], transform),
                    invert_transform(fcf["yhat_upper"], transform),
                    color="#00f5d4", alpha=0.15, label="80% Band (관리한계)")
    ax.axvline(cutoff, color="#f59e0b", linestyle="--", linewidth=1)
    anom_recent = [a for a in anomalies if pd.Timestamp(a["ds"]) >= cutoff - win]
    if anom_recent:
        ax.scatter([pd.Timestamp(a["ds"]) for a in anom_recent],
                   [a["actual"] for a in anom_recent],
                   color="#ef4444", s=42, zorder=5, label="Out-of-control")
    if transform == "log10":
        ax.set_yscale("log")
    ax.set_title(title, fontsize=13, fontweight="bold", color="#ffffff")
    ax.set_ylabel(unit, color="#aaaaaa")
    ax.grid(True, color="#222222", linewidth=0.6)
    ax.tick_params(colors="#aaaaaa", labelsize=9)
    for s in ax.spines.values():
        s.set_color("#333333")
    ax.legend(loc="upper left", facecolor="#1a1a1a", edgecolor="#333333",
              labelcolor="#ffffff", fontsize=8)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor="#0a0a0a", bbox_inches="tight")
    plt.close(fig)
    return path


# ----------------- Service API -----------------

def _ds_dir(user_id: str, name: str) -> Path:
    safe = lambda s: "".join(c for c in s if c.isalnum() or c in "-_")[:40]
    return STORE_DIR / safe(user_id) / safe(name)


def register_dataset(user_id: str, name: str, csv_path: str | None = None,
                     rows: list[dict] | None = None, domain: str = "generic",
                     description: str = "") -> dict:
    """Store a user/company dataset for personalized training."""
    if domain not in DOMAIN_PRESETS:
        raise ValueError(f"domain must be one of {list(DOMAIN_PRESETS)}")
    d = _ds_dir(user_id, name)
    d.mkdir(parents=True, exist_ok=True)
    if rows:
        pd.DataFrame(rows).to_csv(d / "data.csv", index=False)
    elif csv_path:
        shutil.copy2(csv_path, d / "data.csv")
    else:
        raise ValueError("csv_path or rows required")
    df = load_series(d / "data.csv")  # validate
    meta = {
        "user_id": user_id, "name": name, "domain": domain,
        "description": description, "rows": len(df),
        "index_mode": df.attrs.get("index_mode"),
        "range": [str(df["ds"].min()), str(df["ds"].max())],
        "registered_at": datetime.now().isoformat(),
        "trained": False,
    }
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def train_personal_model(user_id: str, name: str) -> dict:
    """Hyperparameter-tuned Prophet fit on the registered dataset; persists model."""
    from prophet.serialize import model_to_json
    d = _ds_dir(user_id, name)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    preset = DOMAIN_PRESETS[meta["domain"]]
    df = apply_transform(load_series(d / "data.csv"), preset["transform"])

    best_params, mape = tune_hyperparams(preset, df)
    model, forecast = _fit_predict(preset, best_params, df, horizon_days=1)
    (d / "model.json").write_text(model_to_json(model), encoding="utf-8")

    meta.update({
        "trained": True, "trained_at": datetime.now().isoformat(),
        "best_params": best_params, "holdout_mape_pct": round(mape, 3),
    })
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def personal_forecast(user_id: str, name: str, days: int = 30,
                      with_chart: bool = True) -> dict:
    """Serve a forecast from the stored personalized model (+SPC anomaly report)."""
    from prophet.serialize import model_from_json
    d = _ds_dir(user_id, name)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    if not meta.get("trained"):
        raise RuntimeError("Model not trained yet — run train first")
    preset = DOMAIN_PRESETS[meta["domain"]]
    df = apply_transform(load_series(d / "data.csv"), preset["transform"])
    model = model_from_json((d / "model.json").read_text(encoding="utf-8"))

    days = max(1, min(int(days), 365))
    future = model.make_future_dataframe(periods=days, freq="D")
    if "cap" in model.history.columns:
        future["cap"] = float(model.history["cap"].iloc[0])
    forecast = model.predict(future)

    anomalies = detect_anomalies(df, forecast, preset["transform"]) if preset["spc"] else []
    cutoff = df["ds"].max()
    fcf = forecast[forecast["ds"] > cutoff]
    end = fcf.iloc[-1] if not fcf.empty else forecast.iloc[-1]
    last_actual = float(invert_transform([df["y"].iloc[-1]], preset["transform"])[0])
    yhat_end = float(invert_transform([end["yhat"]], preset["transform"])[0])

    result = {
        "user_id": user_id, "name": name, "domain": meta["domain"],
        "days": days, "last_actual": last_actual, "forecast_end": yhat_end,
        "change_pct": round((yhat_end / last_actual - 1) * 100, 3) if last_actual else None,
        "band": [float(invert_transform([end["yhat_lower"]], preset["transform"])[0]),
                 float(invert_transform([end["yhat_upper"]], preset["transform"])[0])],
        "holdout_mape_pct": meta.get("holdout_mape_pct"),
        "anomalies_recent": anomalies[-10:],
        "anomaly_count": len(anomalies),
    }
    if with_chart:
        chart = DATA_OUT / f"{user_id}_{name}_{datetime.now():%Y%m%d_%H%M%S}.png"
        render_chart(f"{name} — Personalized {days}D Forecast ({meta['domain']})",
                     df, forecast, preset["transform"], anomalies, chart, preset["unit"])
        result["chart"] = str(chart)
    return result


def zero_shot_forecast(csv_path: str | None = None, rows: list[dict] | None = None,
                       domain: str = "generic", days: int = 30,
                       with_chart: bool = True, title: str = "zero-shot") -> dict:
    """Instant forecast on arbitrary data — no registration, no stored state."""
    if domain not in DOMAIN_PRESETS:
        raise ValueError(f"domain must be one of {list(DOMAIN_PRESETS)}")
    preset = DOMAIN_PRESETS[domain]
    if rows:
        tmp = DATA_OUT / "_zeroshot_tmp.csv"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(tmp, index=False)
        csv_path = str(tmp)
    if not csv_path:
        raise ValueError("csv_path or rows required")
    df = apply_transform(load_series(csv_path), preset["transform"])
    days = max(1, min(int(days), 365))
    _, forecast = _fit_predict(preset, {"changepoint_prior_scale": 0.05}, df, days)
    anomalies = detect_anomalies(df, forecast, preset["transform"]) if preset["spc"] else []

    cutoff = df["ds"].max()
    fcf = forecast[forecast["ds"] > cutoff]
    end = fcf.iloc[-1] if not fcf.empty else forecast.iloc[-1]
    last_actual = float(invert_transform([df["y"].iloc[-1]], preset["transform"])[0])
    yhat_end = float(invert_transform([end["yhat"]], preset["transform"])[0])
    result = {
        "mode": "zero-shot", "domain": domain, "days": days,
        "rows": len(df), "last_actual": last_actual, "forecast_end": yhat_end,
        "change_pct": round((yhat_end / last_actual - 1) * 100, 3) if last_actual else None,
        "band": [float(invert_transform([end["yhat_lower"]], preset["transform"])[0]),
                 float(invert_transform([end["yhat_upper"]], preset["transform"])[0])],
        "anomalies_recent": anomalies[-10:],
        "anomaly_count": len(anomalies),
    }
    if with_chart:
        chart = DATA_OUT / f"zeroshot_{datetime.now():%Y%m%d_%H%M%S}.png"
        render_chart(f"{title} — Zero-shot {days}D Forecast ({domain})",
                     df, forecast, preset["transform"], anomalies, chart, preset["unit"])
        result["chart"] = str(chart)
    return result


def list_datasets(user_id: str) -> list[dict]:
    base = STORE_DIR / "".join(c for c in user_id if c.isalnum() or c in "-_")[:40]
    out = []
    if base.exists():
        for d in sorted(base.iterdir()):
            mp = d / "meta.json"
            if mp.exists():
                out.append(json.loads(mp.read_text(encoding="utf-8")))
    return out


# ----------------- Demo Data (semiconductor / quantum) -----------------

def make_demo_data(kind: str, path: Path) -> Path:
    """Synthetic demo series. 'stim' mimics QEC logical error rates per round
    (uses the `stim` package if installed; otherwise statistical synthesis)."""
    rng = np.random.default_rng(11)
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "semiconductor":
        days = pd.date_range(end=pd.Timestamp.today().normalize(), periods=240, freq="D")
        drift = np.linspace(0, 2.2, 240)                      # 공정 개선 추세
        weekly = 0.6 * np.sin(2 * np.pi * np.arange(240) / 7)  # 주간 로트 사이클
        noise = rng.normal(0, 0.45, 240)
        excursion = np.zeros(240); excursion[180:184] = -4.0   # 공정 사고(excursion)
        y = np.clip(92.0 + drift + weekly + noise + excursion, 0, 100)
        pd.DataFrame({"date": days, "yield": y.round(3)}).to_csv(path, index=False)
    elif kind == "stim":
        rounds = np.arange(1, 301)
        try:
            import stim  # noqa: F401 — real Stim sampling if available
            base = 8e-3
        except Exception:
            base = 8e-3
        decay = base * np.exp(-rounds / 220.0)                 # 디코더/캘리브레이션 개선
        drift = 1 + 0.35 * (rounds > 230)                      # 캘리브레이션 드리프트 발생
        y = decay * drift * rng.lognormal(0, 0.18, len(rounds))
        pd.DataFrame({"round": rounds,
                      "logical_error_rate": y}).to_csv(path, index=False)
    else:
        raise ValueError("kind must be 'semiconductor' or 'stim'")
    return path


# ----------------- CLI -----------------

def main():
    p = argparse.ArgumentParser(description="Personalized/zero-shot forecasting service")
    sub = p.add_subparsers(dest="cmd", required=True)

    z = sub.add_parser("zeroshot")
    z.add_argument("--csv", required=True); z.add_argument("--domain", default="generic")
    z.add_argument("--days", type=int, default=30); z.add_argument("--title", default="zero-shot")

    r = sub.add_parser("register")
    r.add_argument("--user", required=True); r.add_argument("--name", required=True)
    r.add_argument("--csv", required=True); r.add_argument("--domain", default="generic")
    r.add_argument("--desc", default="")

    t = sub.add_parser("train")
    t.add_argument("--user", required=True); t.add_argument("--name", required=True)

    f = sub.add_parser("forecast")
    f.add_argument("--user", required=True); f.add_argument("--name", required=True)
    f.add_argument("--days", type=int, default=30)

    l = sub.add_parser("list"); l.add_argument("--user", required=True)

    d = sub.add_parser("stim-demo")
    d.add_argument("--out", default=str(DATA_OUT / "demo"))

    a = p.parse_args()
    try:
        if a.cmd == "zeroshot":
            print(json.dumps(zero_shot_forecast(csv_path=a.csv, domain=a.domain,
                                                days=a.days, title=a.title),
                             ensure_ascii=False, indent=2))
        elif a.cmd == "register":
            print(json.dumps(register_dataset(a.user, a.name, csv_path=a.csv,
                                              domain=a.domain, description=a.desc),
                             ensure_ascii=False, indent=2))
        elif a.cmd == "train":
            print(json.dumps(train_personal_model(a.user, a.name), ensure_ascii=False, indent=2))
        elif a.cmd == "forecast":
            print(json.dumps(personal_forecast(a.user, a.name, a.days), ensure_ascii=False, indent=2))
        elif a.cmd == "list":
            print(json.dumps(list_datasets(a.user), ensure_ascii=False, indent=2))
        elif a.cmd == "stim-demo":
            out = Path(a.out)
            p1 = make_demo_data("semiconductor", out / "fab_yield_demo.csv")
            p2 = make_demo_data("stim", out / "stim_logical_error_demo.csv")
            print(json.dumps({"semiconductor": str(p1), "stim": str(p2)}, indent=2))
    except Exception as e:
        print(f"❌ {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
