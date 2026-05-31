#!/usr/bin/env python3

import argparse
import hashlib
import json
import math
import sqlite3
import sys
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from prophet.serialize import model_to_json

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.trader.main import (
    HORIZON_STEPS,
    BaseProphetConfig,
    ProphetEngineAgent,
    apply_uncertainty_profile,
    build_training_views_for_rule,
    ensure_raw_df,
)
from services.trader.map_store import load_symbol_information_map_summary
from services.trader.reinforcement import (
    compute_champion_reinforcement_prior,
    load_champion_reinforcement_state,
)

MODEL_CACHE_DIR = ROOT_DIR / "services" / "trader" / "model_cache"
EXPORT_DIR = ROOT_DIR / "services" / "trader" / "exports"
REGISTRY_PATH = MODEL_CACHE_DIR / "model_registry.sqlite3"


@dataclass
class CandidateMetrics:
    name: str
    mae: float
    rmse: float
    mape: float
    directional_accuracy: float
    coverage: float
    composite_score: float
    folds: int
    map_prior_adjustment: float = 0.0
    reinforcement_adjustment: float = 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train or export a champion Prophet model from existing records."
    )
    parser.add_argument("--symbol", default="SOL", help="Logical symbol for the model")
    parser.add_argument(
        "--csv",
        default=str(ROOT_DIR / "data" / "historical" / "sol_usd_1m.csv"),
        help="Historical CSV with ds/close columns",
    )
    parser.add_argument(
        "--task",
        choices=("direction", "low", "high"),
        default="direction",
        help="Target series to optimize",
    )
    parser.add_argument(
        "--rule",
        choices=tuple(HORIZON_STEPS.keys()),
        default="1min",
        help="Cadence rule for the champion model",
    )
    parser.add_argument("--folds", type=int, default=3, help="Rolling validation folds")
    parser.add_argument(
        "--force-retrain",
        action="store_true",
        help="Ignore an existing champion and retrain from scratch",
    )
    parser.add_argument(
        "--export-only",
        action="store_true",
        help="Only package an already-saved champion artifact",
    )
    return parser.parse_args()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper() or "UNKNOWN"


def safe_stem(value: str) -> str:
    return value.replace(" ", "_").replace("/", "_").replace("-", "_").lower()


def artifact_stem(symbol: str, task: str, rule: str) -> str:
    return f"champion_{safe_stem(symbol)}_{safe_stem(task)}_{safe_stem(rule)}"


def champion_paths(symbol: str, task: str, rule: str) -> Dict[str, Path]:
    stem = artifact_stem(symbol, task, rule)
    return {
        "model_json": MODEL_CACHE_DIR / f"{stem}.json",
        "metadata_json": MODEL_CACHE_DIR / f"{stem}.metadata.json",
    }


def ensure_export_dir() -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    return EXPORT_DIR


def load_dataset(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    return ensure_raw_df(df)


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    normalized = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def compute_dataset_signature(df: pd.DataFrame, dataset_path: str) -> str:
    digest = hashlib.sha256()
    digest.update(str(Path(dataset_path).resolve()).encode("utf-8"))
    digest.update(str(len(df)).encode("utf-8"))
    digest.update(str(df["ds"].iloc[0]).encode("utf-8"))
    digest.update(str(df["ds"].iloc[-1]).encode("utf-8"))
    digest.update(str(float(df["close"].iloc[-1])).encode("utf-8"))
    return digest.hexdigest()


def build_candidate_configs(rule: str, horizon_steps: int) -> List[BaseProphetConfig]:
    candidates = [
        BaseProphetConfig(
            name=f"candidate_linear_{rule}",
            rule=rule,
            horizon_steps=horizon_steps,
            changepoint_prior_scale=0.01,
        ),
        BaseProphetConfig(
            name=f"candidate_flex_{rule}",
            rule=rule,
            horizon_steps=horizon_steps,
            changepoint_prior_scale=0.05,
        ),
        BaseProphetConfig(
            name=f"candidate_flat_{rule}",
            rule=rule,
            horizon_steps=horizon_steps,
            growth="flat",
            changepoint_prior_scale=0.02,
        ),
        BaseProphetConfig(
            name=f"candidate_additive_{rule}",
            rule=rule,
            horizon_steps=horizon_steps,
            seasonality_mode="additive",
            changepoint_prior_scale=0.03,
        ),
    ]
    return [apply_uncertainty_profile(config, batch_mode=True) for config in candidates]


def select_training_df(raw_df: pd.DataFrame, rule: str, task: str) -> pd.DataFrame:
    views = build_training_views_for_rule(raw_df, rule)
    if task == "low":
        return views["low_df"]
    if task == "high":
        return views["high_df"]
    return views["direction_df"]


def compute_information_map_prior(
    config: BaseProphetConfig,
    map_summary: Optional[Dict[str, Any]],
) -> float:
    if not map_summary:
        return 0.0

    target_cps = map_summary.get("preferredChangepointScale")
    if target_cps is None:
        return 0.0

    penalty = abs(float(config.changepoint_prior_scale) - float(target_cps)) * 0.9
    buy_share = float(map_summary.get("buyShare") or 0.0)
    volatility_index = float(map_summary.get("volatilityIndex") or 0.0)
    trajectory = map_summary.get("trajectory") or {}
    persistence_score = float(trajectory.get("persistenceScore") or 0.5)
    stability_score = float(trajectory.get("stabilityScore") or 0.5)
    regime_shift_risk = float(trajectory.get("regimeShiftRisk") or 0.5)

    if buy_share >= 0.6 and config.growth == "flat":
        penalty += 0.02
    if volatility_index >= 0.8 and config.changepoint_prior_scale < float(target_cps):
        penalty += 0.015
    if volatility_index <= 0.25 and config.changepoint_prior_scale > float(target_cps):
        penalty += 0.012
    if persistence_score >= 0.7 and stability_score >= 0.65 and config.changepoint_prior_scale > float(target_cps):
        penalty += 0.012
    if regime_shift_risk >= 0.65 and config.changepoint_prior_scale < float(target_cps):
        penalty += 0.018

    return float(penalty)


def evaluate_candidate(
    config: BaseProphetConfig,
    training_df: pd.DataFrame,
    folds: int,
) -> CandidateMetrics:
    horizon = max(1, int(config.horizon_steps))
    min_train_rows = max(180, horizon * 8)
    mae_values: List[float] = []
    rmse_values: List[float] = []
    mape_values: List[float] = []
    direction_hits: List[float] = []
    coverages: List[float] = []

    for fold_index in range(folds):
        test_end = len(training_df) - horizon * (folds - fold_index - 1)
        test_start = test_end - horizon
        train_end = test_start

        if train_end < min_train_rows:
            continue

        train_slice = training_df.iloc[:train_end].copy()
        test_slice = training_df.iloc[test_start:test_end].copy()

        agent = ProphetEngineAgent(config)
        agent.fit(train_slice)
        forecast = agent.next_horizon_forecast()
        merged = test_slice.merge(forecast, on="ds", how="inner")

        if merged.empty:
            continue

        actual = merged["y"].to_numpy(dtype=float)
        predicted = merged["yhat"].to_numpy(dtype=float)
        scale = max(float(np.abs(train_slice["y"]).median()), 1e-8)

        mae = float(np.mean(np.abs(actual - predicted)))
        rmse = float(np.sqrt(np.mean((actual - predicted) ** 2)))
        mape = float(np.mean(np.abs((actual - predicted) / np.maximum(np.abs(actual), 1e-8))))
        coverage = float(
            np.mean(
                (actual >= merged["yhat_lower"].to_numpy(dtype=float))
                & (actual <= merged["yhat_upper"].to_numpy(dtype=float))
            )
        )

        baseline = float(train_slice["y"].iloc[-1])
        predicted_move = float(predicted[-1] - baseline)
        actual_move = float(actual[-1] - baseline)
        direction_hit = 1.0 if math.copysign(1.0, predicted_move or 0.0) == math.copysign(1.0, actual_move or 0.0) else 0.0

        mae_values.append(mae / scale)
        rmse_values.append(rmse / scale)
        mape_values.append(mape)
        direction_hits.append(direction_hit)
        coverages.append(coverage)

    if not mae_values:
        return CandidateMetrics(
            name=config.name,
            mae=float("inf"),
            rmse=float("inf"),
            mape=float("inf"),
            directional_accuracy=0.0,
            coverage=0.0,
            composite_score=float("inf"),
            folds=0,
        )

    mae_score = float(np.mean(mae_values))
    rmse_score = float(np.mean(rmse_values))
    mape_score = float(np.mean(mape_values))
    directional_accuracy = float(np.mean(direction_hits))
    coverage = float(np.mean(coverages))
    coverage_penalty = abs(coverage - 0.8)
    composite = (
        mae_score * 0.4
        + rmse_score * 0.25
        + mape_score * 0.15
        + (1.0 - directional_accuracy) * 0.15
        + coverage_penalty * 0.05
    )

    return CandidateMetrics(
        name=config.name,
        mae=mae_score,
        rmse=rmse_score,
        mape=mape_score,
        directional_accuracy=directional_accuracy,
        coverage=coverage,
        composite_score=composite,
        folds=len(mae_values),
    )


def ensure_registry_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS model_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            agent_group TEXT NOT NULL,
            rule TEXT NOT NULL,
            horizon_steps INTEGER NOT NULL,
            engine TEXT NOT NULL,
            growth TEXT,
            seasonality_mode TEXT,
            changepoint_prior_scale REAL,
            dataset_path TEXT,
            dataset_signature TEXT NOT NULL,
            training_rows INTEGER NOT NULL,
            ds_start TEXT,
            ds_end TEXT,
            last_timestamp TEXT,
            last_value REAL,
            bar_seconds REAL,
            artifact_kind TEXT NOT NULL,
            model_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            context_text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(symbol, agent_name, engine, dataset_signature, artifact_kind)
        );
        CREATE INDEX IF NOT EXISTS idx_model_documents_lookup
            ON model_documents(symbol, agent_name, engine, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_model_documents_rule
            ON model_documents(rule, agent_group, updated_at DESC);
        CREATE VIRTUAL TABLE IF NOT EXISTS model_documents_fts
            USING fts5(context_text, content='model_documents', content_rowid='id');
        CREATE TRIGGER IF NOT EXISTS model_documents_ai
            AFTER INSERT ON model_documents
            BEGIN
                INSERT INTO model_documents_fts(rowid, context_text)
                VALUES (new.id, new.context_text);
            END;
        CREATE TRIGGER IF NOT EXISTS model_documents_ad
            AFTER DELETE ON model_documents
            BEGIN
                INSERT INTO model_documents_fts(model_documents_fts, rowid, context_text)
                VALUES('delete', old.id, old.context_text);
            END;
        CREATE TRIGGER IF NOT EXISTS model_documents_au
            AFTER UPDATE ON model_documents
            BEGIN
                INSERT INTO model_documents_fts(model_documents_fts, rowid, context_text)
                VALUES('delete', old.id, old.context_text);
                INSERT INTO model_documents_fts(rowid, context_text)
                VALUES (new.id, new.context_text);
            END;
        """
    )


def send_telegram_notification(text: str) -> bool:
    import os
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=ROOT_DIR / ".env")
    
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_ids_str = os.getenv("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_ids_str:
        print("⚠️ Telegram 설정 누락 (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID를 .env에 입력해 주세요.)")
        return False
        
    chat_ids = [cid.strip() for cid in chat_ids_str.split(",") if cid.strip()]
    success = True
    
    import urllib.parse
    import urllib.request
    import json
    
    for cid in chat_ids:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": cid,
            "text": text,
            "parse_mode": "HTML"
        }
        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                url, 
                data=data, 
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                response.read()
                print(f"✅ [Telegram Champion Notification] {cid} 전송 완료!")
        except Exception as e:
            print(f"❌ [Telegram Champion Notification] {cid} 전송 실패: {e}")
            success = False
            
    return success


def register_champion(
    *,
    symbol: str,
    task: str,
    rule: str,
    dataset_path: str,
    dataset_signature: str,
    training_df: pd.DataFrame,
    winner_config: BaseProphetConfig,
    winner_metrics: CandidateMetrics,
    model_json_text: str,
    map_summary: Optional[Dict[str, Any]] = None,
) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    now = utc_now_iso()
    context_text = (
        f"champion prophet model for {symbol} {task} {rule}. "
        f"winner={winner_config.name}. score={winner_metrics.composite_score:.6f}. "
        f"mae={winner_metrics.mae:.6f}. rmse={winner_metrics.rmse:.6f}. "
        f"directional_accuracy={winner_metrics.directional_accuracy:.4f}. "
        f"map_prior_adjustment={winner_metrics.map_prior_adjustment:.6f}. "
        f"reinforcement_adjustment={winner_metrics.reinforcement_adjustment:.6f}. "
        f"map_days_observed={(map_summary or {}).get('daysObserved', 0)}. "
        f"trajectory_persistence={((map_summary or {}).get('trajectory') or {}).get('persistenceScore', 0):.4f}. "
        f"trajectory_regime_risk={((map_summary or {}).get('trajectory') or {}).get('regimeShiftRisk', 0):.4f}."
    )
    metrics_json = json.dumps(asdict(winner_metrics), ensure_ascii=False)

    with sqlite3.connect(REGISTRY_PATH) as conn:
        ensure_registry_schema(conn)
        conn.execute(
            """
            INSERT INTO model_documents (
                symbol, agent_name, agent_group, rule, horizon_steps, engine, growth,
                seasonality_mode, changepoint_prior_scale, dataset_path,
                dataset_signature, training_rows, ds_start, ds_end, last_timestamp,
                last_value, bar_seconds, artifact_kind, model_json, metrics_json,
                context_text, created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(symbol, agent_name, engine, dataset_signature, artifact_kind)
            DO UPDATE SET
                growth=excluded.growth,
                seasonality_mode=excluded.seasonality_mode,
                changepoint_prior_scale=excluded.changepoint_prior_scale,
                dataset_path=excluded.dataset_path,
                training_rows=excluded.training_rows,
                ds_start=excluded.ds_start,
                ds_end=excluded.ds_end,
                last_timestamp=excluded.last_timestamp,
                last_value=excluded.last_value,
                bar_seconds=excluded.bar_seconds,
                model_json=excluded.model_json,
                metrics_json=excluded.metrics_json,
                context_text=excluded.context_text,
                updated_at=excluded.updated_at
            """,
            (
                symbol,
                artifact_stem(symbol, task, rule),
                task,
                rule,
                int(winner_config.horizon_steps),
                "prophet",
                winner_config.growth,
                winner_config.seasonality_mode,
                float(winner_config.changepoint_prior_scale),
                dataset_path,
                dataset_signature,
                int(len(training_df)),
                str(training_df["ds"].iloc[0]),
                str(training_df["ds"].iloc[-1]),
                str(training_df["ds"].iloc[-1]),
                float(training_df["y"].iloc[-1]),
                float((training_df["ds"].diff().dropna().dt.total_seconds().median()) or 0.0),
                "champion_prophet_json",
                model_json_text,
                metrics_json,
                context_text,
                now,
                now,
            ),
        )
        conn.commit()

    # Format a beautiful message and send to Telegram
    msg = [
        f"👑 <b>[No Slip AI Quant] 새로운 Prophet 챔피언 모델 등극!</b>",
        f"=" * 40,
        f"📊 <b>대상 자산 (Asset)</b>: <code>{symbol}</code>",
        f"🎯 <b>최적화 타겟 (Task)</b>: <code>{task.upper()}</code>",
        f"⏱️ <b>예측 주기 (Cadence)</b>: <code>{rule}</code>",
        f"🏆 <b>선정된 알고리즘</b>: <code>{winner_config.name}</code>",
        f"=" * 40,
        f"📈 <b>챔피언 모델 종합 평가 메트릭스</b>:",
        f"  • <b>종합 스코어 (Composite)</b>: {winner_metrics.composite_score:.6f}",
        f"  • <b>평균 절대 오차 (MAE)</b>: {winner_metrics.mae:.6f}",
        f"  • <b>평균 제곱근 오차 (RMSE)</b>: {winner_metrics.rmse:.6f}",
        f"  • <b>방향성 정확도 (Dir Acc)</b>: {winner_metrics.directional_accuracy * 100.0:.1f}%",
        f"  • <b>신뢰구간 커버리지</b>: {winner_metrics.coverage * 100.0:.1f}% (목표 80%)",
        f"  • <b>검증 폴드 수 (Folds)</b>: {winner_metrics.folds} folds",
        f"-" * 40,
        f"🧠 <b>강화 학습 및 정보 맵 반영</b>:",
        f"  • <b>매크로/정보맵 가중치 조정</b>: {winner_metrics.map_prior_adjustment:+.6f}",
        f"  • <b>강화학습(RL) 피드백 조정</b>: {winner_metrics.reinforcement_adjustment:+.6f}",
        f"  • <b>학습에 사용된 데이터 수</b>: {len(training_df):,} rows",
        f"  • <b>학습 기간</b>: {training_df['ds'].iloc[0]} ~ {training_df['ds'].iloc[-1]}",
        f"=" * 40,
        f"※ 새로 등록된 챔피언 모델 설정은 다음 예측 실행 시 실시간 자동 적용됩니다."
    ]
    send_telegram_notification("\n".join(msg))


def export_champion_bundle(
    symbol: str,
    task: str,
    rule: str,
    metadata_path: Path,
    model_json_path: Path,
) -> Path:
    ensure_export_dir()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_path = EXPORT_DIR / f"{artifact_stem(symbol, task, rule)}_{timestamp}.zip"

    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(model_json_path, arcname=model_json_path.name)
        archive.write(metadata_path, arcname=metadata_path.name)
        archive.writestr(
            "README.txt",
            "\n".join(
                [
                    "No Slip champion Prophet bundle",
                    f"symbol={symbol}",
                    f"task={task}",
                    f"rule={rule}",
                    f"model_json={model_json_path.name}",
                    f"metadata={metadata_path.name}",
                ]
            ),
        )

    return bundle_path


def load_existing_metadata(symbol: str, task: str, rule: str) -> Optional[Dict[str, Any]]:
    metadata_path = champion_paths(symbol, task, rule)["metadata_json"]
    if not metadata_path.exists():
        return None
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def train_or_export_champion_from_raw_df(
    *,
    symbol: str,
    raw_df: pd.DataFrame,
    dataset_label: str,
    task: str,
    rule: str,
    folds: int,
    force_retrain: bool,
    export_only: bool,
) -> Dict[str, Any]:
    symbol = normalize_symbol(symbol)
    normalized_df = ensure_raw_df(raw_df)
    training_df = select_training_df(normalized_df, rule, task)
    dataset_signature = compute_dataset_signature(normalized_df, dataset_label)
    paths = champion_paths(symbol, task, rule)
    map_summary = load_symbol_information_map_summary(symbol, lookback_days=30)
    reinforcement_state = load_champion_reinforcement_state(symbol, task, rule)

    existing_metadata = load_existing_metadata(symbol, task, rule)
    if existing_metadata and not force_retrain:
        existing_signature = existing_metadata.get("dataset_signature")
        if existing_signature == dataset_signature and paths["model_json"].exists():
            bundle_path = export_champion_bundle(
                symbol,
                task,
                rule,
                paths["metadata_json"],
                paths["model_json"],
            )
            existing_metadata["export_bundle_path"] = str(bundle_path)
            existing_metadata["reused_existing"] = True
            return existing_metadata

    if export_only:
        raise FileNotFoundError(
            f"No saved champion model exists yet for {symbol} {task} {rule}."
        )

    horizon_steps = HORIZON_STEPS[rule]
    candidates = build_candidate_configs(rule, horizon_steps)
    candidate_metrics = [evaluate_candidate(config, training_df, folds) for config in candidates]
    for metrics, config in zip(candidate_metrics, candidates):
        metrics.map_prior_adjustment = compute_information_map_prior(config, map_summary)
        metrics.reinforcement_adjustment = compute_champion_reinforcement_prior(
            config,
            reinforcement_state,
        )
        metrics.composite_score += (
            metrics.map_prior_adjustment + metrics.reinforcement_adjustment
        )
    candidate_metrics.sort(key=lambda item: item.composite_score)
    winner_metrics = candidate_metrics[0]
    winner_config = next(config for config in candidates if config.name == winner_metrics.name)

    champion_agent = ProphetEngineAgent(winner_config)
    champion_agent.fit(training_df)
    model_json_text = model_to_json(champion_agent.model)

    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    paths["model_json"].write_text(json.dumps(model_json_text), encoding="utf-8")

    metadata = {
        "symbol": symbol,
        "task": task,
        "rule": rule,
        "dataset_path": dataset_label,
        "dataset_signature": dataset_signature,
        "training_rows": int(len(training_df)),
        "ds_start": str(training_df["ds"].iloc[0]),
        "ds_end": str(training_df["ds"].iloc[-1]),
        "winner": {
            "name": winner_config.name,
            "growth": winner_config.growth,
            "seasonality_mode": winner_config.seasonality_mode,
            "changepoint_prior_scale": winner_config.changepoint_prior_scale,
            "interval_width": winner_config.interval_width,
            "uncertainty_samples": winner_config.uncertainty_samples,
            "mcmc_samples": winner_config.mcmc_samples,
            "yearly_seasonality": winner_config.yearly_seasonality,
            "weekly_seasonality": winner_config.weekly_seasonality,
            "daily_seasonality": winner_config.daily_seasonality,
            "horizon_steps": winner_config.horizon_steps,
        },
        "information_map_context": map_summary,
        "reinforcement_context": reinforcement_state,
        "metrics": asdict(winner_metrics),
        "candidates": [asdict(metric) for metric in candidate_metrics],
        "model_json_path": str(paths["model_json"]),
        "created_at": utc_now_iso(),
    }
    paths["metadata_json"].write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    register_champion(
        symbol=symbol,
        task=task,
        rule=rule,
        dataset_path=dataset_label,
        dataset_signature=dataset_signature,
        training_df=training_df,
        winner_config=winner_config,
        winner_metrics=winner_metrics,
        model_json_text=model_json_text,
        map_summary=map_summary,
    )

    bundle_path = export_champion_bundle(
        symbol,
        task,
        rule,
        paths["metadata_json"],
        paths["model_json"],
    )
    metadata["export_bundle_path"] = str(bundle_path)
    metadata["registry_path"] = str(REGISTRY_PATH)
    metadata["reused_existing"] = False
    paths["metadata_json"].write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return metadata


def metadata_is_stale(metadata: Optional[Dict[str, Any]], max_age_hours: float) -> bool:
    if not metadata:
        return True
    created_at = parse_iso_datetime(metadata.get("created_at"))
    if created_at is None:
        return True
    age_seconds = (datetime.now(timezone.utc) - created_at.astimezone(timezone.utc)).total_seconds()
    return age_seconds >= max(0.0, max_age_hours) * 3600.0


def build_runtime_config_name(symbol: str, task: str, rule: str) -> str:
    return f"champion_{safe_stem(symbol)}_{safe_stem(task)}_{safe_stem(rule)}"


def metadata_to_config(
    symbol: str,
    task: str,
    rule: str,
    metadata: Dict[str, Any],
    *,
    default_horizon_steps: Optional[int] = None,
) -> Optional[BaseProphetConfig]:
    winner = metadata.get("winner") or {}
    if not winner:
        return None

    return BaseProphetConfig(
        name=build_runtime_config_name(symbol, task, rule),
        rule=rule,
        horizon_steps=int(winner.get("horizon_steps") or default_horizon_steps or 1),
        seasonality_mode=str(winner.get("seasonality_mode") or "multiplicative"),
        changepoint_prior_scale=float(winner.get("changepoint_prior_scale") or 0.05),
        interval_width=float(winner.get("interval_width") or 0.8),
        uncertainty_samples=int(winner.get("uncertainty_samples") or 1000),
        mcmc_samples=int(winner.get("mcmc_samples") or 0),
        yearly_seasonality=bool(winner.get("yearly_seasonality", False)),
        weekly_seasonality=bool(winner.get("weekly_seasonality", True)),
        daily_seasonality=bool(winner.get("daily_seasonality", True)),
        growth=str(winner.get("growth") or "linear"),
        weight=1.0,
    )


def load_runtime_champion_config(
    *,
    symbol: str,
    task: str,
    rule: str,
    default_horizon_steps: Optional[int] = None,
) -> Optional[BaseProphetConfig]:
    metadata = load_existing_metadata(normalize_symbol(symbol), task, rule)
    if not metadata:
        return None
    return metadata_to_config(
        normalize_symbol(symbol),
        task,
        rule,
        metadata,
        default_horizon_steps=default_horizon_steps,
    )


def ensure_symbol_champion_configs(
    *,
    symbol: str,
    raw_df: pd.DataFrame,
    cadence_rules: Tuple[str, ...],
    folds: int = 3,
    max_age_hours: float = 24.0,
) -> Dict[str, Any]:
    normalized_symbol = normalize_symbol(symbol)
    dataset_label = f"runtime::{normalized_symbol}"
    report: Dict[str, Any] = {
        "symbol": normalized_symbol,
        "status": "checked",
        "rules": {},
    }

    for rule in cadence_rules:
        for task in ("direction", "low", "high"):
            metadata = load_existing_metadata(normalized_symbol, task, rule)
            stale = metadata_is_stale(metadata, max_age_hours=max_age_hours)
            key = f"{task}:{rule}"

            if stale:
                refreshed = train_or_export_champion_from_raw_df(
                    symbol=normalized_symbol,
                    raw_df=raw_df,
                    dataset_label=dataset_label,
                    task=task,
                    rule=rule,
                    folds=max(1, int(folds)),
                    force_retrain=True,
                    export_only=False,
                )
                report["rules"][key] = {
                    "status": "refreshed",
                    "winner": (refreshed.get("winner") or {}).get("name"),
                    "created_at": refreshed.get("created_at"),
                }
            else:
                report["rules"][key] = {
                    "status": "cached",
                    "winner": ((metadata or {}).get("winner") or {}).get("name"),
                    "created_at": (metadata or {}).get("created_at"),
                }

    return report


def train_or_export_champion(
    *,
    symbol: str,
    csv_path: str,
    task: str,
    rule: str,
    folds: int,
    force_retrain: bool,
    export_only: bool,
) -> Dict[str, Any]:
    dataset_path = str(Path(csv_path).resolve())
    raw_df = load_dataset(dataset_path)
    return train_or_export_champion_from_raw_df(
        symbol=symbol,
        raw_df=raw_df,
        dataset_label=dataset_path,
        task=task,
        rule=rule,
        folds=folds,
        force_retrain=force_retrain,
        export_only=export_only,
    )


def main() -> None:
    args = parse_args()
    result = train_or_export_champion(
        symbol=args.symbol,
        csv_path=args.csv,
        task=args.task,
        rule=args.rule,
        folds=max(1, int(args.folds)),
        force_retrain=bool(args.force_retrain),
        export_only=bool(args.export_only),
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
