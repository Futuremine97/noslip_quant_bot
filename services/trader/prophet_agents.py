from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from prophet import Prophet

from .config import SETTINGS
from .utils import build_future_grid, sigmoid


def warm_start_params(model) -> dict:
    def scalar_first(x):
        arr = np.asarray(x)
        return float(arr.reshape(-1)[0])

    return {
        "k": scalar_first(model.params["k"]),
        "m": scalar_first(model.params["m"]),
        "sigma_obs": scalar_first(model.params["sigma_obs"]),
        "delta": np.asarray(model.params["delta"])[0],
        "beta": np.asarray(model.params["beta"])[0],
    }


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
    add_monthly: bool = False
    add_quarterly: bool = False
    add_custom_yearly: bool = False
    us_holidays: bool = False
    weight: float = 1.0


@dataclass
class BaseProphetAgent:
    config: BaseProphetConfig
    model: Optional[Prophet] = None
    train_df: Optional[pd.DataFrame] = None
    fitted: bool = False

    def build_model(self) -> Prophet:
        m = Prophet(
            seasonality_mode=self.config.seasonality_mode,
            changepoint_prior_scale=self.config.changepoint_prior_scale,
            yearly_seasonality=self.config.yearly_seasonality,
            weekly_seasonality=self.config.weekly_seasonality,
            daily_seasonality=self.config.daily_seasonality,
            growth=self.config.growth,
        )
        if self.config.add_monthly:
            m.add_seasonality(name="monthly", period=30.5, fourier_order=10)
        if self.config.add_quarterly:
            m.add_seasonality(name="quarterly", period=91.25, fourier_order=10)
        if self.config.add_custom_yearly:
            m.add_seasonality(name="yearly_custom", period=365.25, fourier_order=15)
        if self.config.us_holidays:
            m.add_country_holidays(country_name="US")
        return m

    def fit(self, df: pd.DataFrame, prev_model=None, use_warm_start: bool = False):
        self.train_df = df.copy()
        self.model = self.build_model()

        init = None
        if use_warm_start and prev_model is not None:
            try:
                if getattr(prev_model, "n_changepoints", None) == getattr(self.model, "n_changepoints", None):
                    init = warm_start_params(prev_model)
            except Exception:
                init = None

        try:
            if init is not None:
                self.model.fit(df, init=init)
            else:
                self.model.fit(df)
        except Exception:
            self.model = self.build_model()
            self.model.fit(df)

        self.fitted = True
        return self

    def next_horizon_forecast(self) -> pd.DataFrame:
        if not self.fitted or self.model is None or self.train_df is None:
            raise RuntimeError(f"{self.config.name} is not fitted")
        last_ts = pd.to_datetime(self.train_df["ds"]).max()
        future = build_future_grid(last_ts, self.config.rule, self.config.horizon_steps)
        fcst = self.model.predict(future)
        return fcst[["ds", "yhat", "yhat_lower", "yhat_upper"]].copy()


@dataclass
class DirectionProphetAgent(BaseProphetAgent):
    buy_threshold: float = SETTINGS.buy_threshold
    sell_threshold: float = SETTINGS.sell_threshold
    max_uncertainty_ratio: float = SETTINGS.max_uncertainty_ratio

    def decision(self) -> Dict[str, float | str]:
        fcst = self.next_horizon_forecast()
        last_price = float(self.train_df["y"].iloc[-1])

        first_price = float(fcst.iloc[0]["yhat"])
        last_future_price = float(fcst.iloc[-1]["yhat"])
        session_mean = float(fcst["yhat"].mean())

        open_ret = (first_price / last_price) - 1.0 if last_price else 0.0
        close_ret = (last_future_price / last_price) - 1.0 if last_price else 0.0
        mean_ret = (session_mean / last_price) - 1.0 if last_price else 0.0

        score = 0.25 * open_ret + 0.50 * close_ret + 0.25 * mean_ret
        avg_band = float((fcst["yhat_upper"] - fcst["yhat_lower"]).mean())
        uncertainty_ratio = avg_band / max(abs(session_mean), 1e-8)

        if score >= self.buy_threshold and uncertainty_ratio < self.max_uncertainty_ratio:
            action = "BUY"
        elif score <= self.sell_threshold and uncertainty_ratio < self.max_uncertainty_ratio:
            action = "SELL"
        else:
            action = "HOLD"

        return {
            "agent": self.config.name,
            "action": action,
            "score": score,
            "uncertainty_ratio": uncertainty_ratio,
            "weight": self.config.weight,
        }


@dataclass
class TimingProphetAgent(BaseProphetAgent):
    mode: str = "low"

    def aggregate_point(self) -> Dict[str, object]:
        fcst = self.next_horizon_forecast()
        row = fcst.loc[fcst["yhat"].idxmin()] if self.mode == "low" else fcst.loc[fcst["yhat"].idxmax()]
        return {
            "agent": self.config.name,
            "predicted_timestamp": pd.Timestamp(row["ds"]),
            "predicted_price": float(row["yhat"]),
            "weight": self.config.weight,
        }

    def full_curve(self) -> pd.DataFrame:
        fcst = self.next_horizon_forecast()
        fcst["agent"] = self.config.name
        fcst["weight"] = self.config.weight
        return fcst


@dataclass
class DirectionCoordinator:
    agents: List[DirectionProphetAgent] = field(default_factory=list)

    def fit_all(self, df: pd.DataFrame, prev_agents=None, use_warm_start: bool = False):
        prev_agents = prev_agents or []
        for i, a in enumerate(self.agents):
            prev_model = getattr(prev_agents[i], "model", None) if i < len(prev_agents) else None
            a.fit(df, prev_model=prev_model, use_warm_start=use_warm_start)
        return self

    def aggregate(self) -> Dict[str, object]:
        details = pd.DataFrame([a.decision() for a in self.agents])
        total_weight = details["weight"].sum() or 1.0

        weighted_score = float((details["score"] * details["weight"]).sum() / total_weight)
        buy_weight = float(details.loc[details["action"] == "BUY", "weight"].sum())
        sell_weight = float(details.loc[details["action"] == "SELL", "weight"].sum())
        hold_weight = float(details.loc[details["action"] == "HOLD", "weight"].sum())

        if buy_weight > max(sell_weight, hold_weight) and weighted_score > 0:
            final_action = "BUY"
        elif sell_weight > max(buy_weight, hold_weight) and weighted_score < 0:
            final_action = "SELL"
        else:
            final_action = "HOLD"

        return {
            "final_action": final_action,
            "weighted_score": weighted_score,
            "details": details,
        }


@dataclass
class TimingCoordinator:
    agents: List[TimingProphetAgent] = field(default_factory=list)
    mode: str = "low"

    def fit_all(self, df: pd.DataFrame, prev_agents=None, use_warm_start: bool = False):
        prev_agents = prev_agents or []
        for i, a in enumerate(self.agents):
            prev_model = getattr(prev_agents[i], "model", None) if i < len(prev_agents) else None
            a.fit(df, prev_model=prev_model, use_warm_start=use_warm_start)
        return self

    def aggregate_curve(self) -> pd.DataFrame:
        curves = []
        for a in self.agents:
            fcst = a.full_curve()[["ds", "yhat"]].rename(columns={"yhat": f"yhat__{a.config.name}"})
            curves.append(fcst)

        merged = curves[0]
        for c in curves[1:]:
            merged = merged.merge(c, on="ds", how="inner")

        total_weight = 0.0
        weighted_sum = 0.0
        for a in self.agents:
            col = f"yhat__{a.config.name}"
            w = a.config.weight
            weighted_sum += merged[col] * w
            total_weight += w

        merged["weighted_yhat"] = weighted_sum / (total_weight or 1.0)
        return merged

    def aggregate(self) -> Dict[str, object]:
        curve = self.aggregate_curve()
        row = curve.loc[curve["weighted_yhat"].idxmin()] if self.mode == "low" else curve.loc[curve["weighted_yhat"].idxmax()]
        return {
            "predicted_timestamp": pd.Timestamp(row["ds"]),
            "predicted_price": float(row["weighted_yhat"]),
            "curve": curve,
        }


def make_cfgs(prefix: str, rule: str, horizon_steps: int) -> List[BaseProphetConfig]:
    return [
        BaseProphetConfig(name=f"{prefix}_base_{rule}", rule=rule, horizon_steps=horizon_steps, changepoint_prior_scale=0.01, weight=1.0),
        BaseProphetConfig(name=f"{prefix}_flex_{rule}", rule=rule, horizon_steps=horizon_steps, changepoint_prior_scale=0.05, weight=1.0),
        BaseProphetConfig(name=f"{prefix}_flat_{rule}", rule=rule, horizon_steps=horizon_steps, growth="flat", changepoint_prior_scale=0.01, weight=0.8),
        BaseProphetConfig(name=f"{prefix}_hol_{rule}", rule=rule, horizon_steps=horizon_steps, changepoint_prior_scale=0.03, us_holidays=True, weight=1.2),
    ]


def build_direction_agents(rule: str) -> List[DirectionProphetAgent]:
    return [DirectionProphetAgent(config=c) for c in make_cfgs("dir", rule, SETTINGS.horizon_steps[rule])]


def build_low_agents(rule: str) -> List[TimingProphetAgent]:
    return [TimingProphetAgent(config=c, mode="low") for c in make_cfgs("low", rule, SETTINGS.horizon_steps[rule])]


def build_high_agents(rule: str) -> List[TimingProphetAgent]:
    return [TimingProphetAgent(config=c, mode="high") for c in make_cfgs("high", rule, SETTINGS.horizon_steps[rule])]
