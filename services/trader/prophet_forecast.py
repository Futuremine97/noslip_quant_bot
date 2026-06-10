"""Prophet time-series forecast & visualization engine for No Slip Quant.

Entry points
------------
1. Telegram bot  : /prophet <symbol> [days]   (telegram_interactive_bot.py dispatch)
2. Claude Code   : /prophet slash command      (commands/prophet.md -> CLI below)
3. Python import : generate_prophet_forecast(symbol, days) -> (report_html, photo_path)

CLI usage
---------
    .venv/bin/python services/trader/prophet_forecast.py TSLA --days 30
    .venv/bin/python services/trader/prophet_forecast.py BTC  --days 14 --output data/btc_prophet.png
    .venv/bin/python services/trader/prophet_forecast.py NVDA --csv my_data.csv   # offline test
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR.parent.parent / "data"

CRYPTO_SHORT = {"BTC", "ETH", "SOL"}
KOREAN_ALIASES = {
    "비트코인": "BTC-USD", "이더리움": "ETH-USD", "솔라나": "SOL-USD",
    "엔비디아": "NVDA", "테슬라": "TSLA", "애플": "AAPL",
    "마이크로소프트": "MSFT", "구글": "GOOGL", "아마존": "AMZN", "메타": "META",
}
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]
WEEKDAY_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]  # chart labels (font-safe)


def resolve_symbol(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if s in KOREAN_ALIASES:
        return KOREAN_ALIASES[s]
    s = s.upper()
    if s in CRYPTO_SHORT:
        return f"{s}-USD"
    return s


def fetch_history(symbol: str, lookback_days: int = 730) -> pd.DataFrame:
    """Fetch daily OHLC history via yfinance -> DataFrame[ds, y]."""
    import yfinance as yf

    df = yf.download(
        symbol,
        period=f"{lookback_days}d",
        interval="1d",
        auto_adjust=True,
        progress=False,
    )
    if df is None or df.empty:
        raise ValueError(f"No price data returned for '{symbol}'")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    out = df.reset_index()[["Date", "Close"]].rename(columns={"Date": "ds", "Close": "y"})
    out["ds"] = pd.to_datetime(out["ds"]).dt.tz_localize(None)
    out = out.dropna()
    if len(out) < 90:
        raise ValueError(f"Not enough history for '{symbol}' ({len(out)} rows; need >= 90)")
    return out


def load_csv_history(csv_path: str) -> pd.DataFrame:
    """Offline fallback: load a CSV with date + close columns into [ds, y]."""
    df = pd.read_csv(csv_path)
    cols = {c.lower().strip(): c for c in df.columns}
    date_col = next((cols[k] for k in ("ds", "date", "datetime", "time", "timestamp") if k in cols), df.columns[0])
    y_col = next((cols[k] for k in ("y", "close", "price", "adj close", "adj_close") if k in cols), df.columns[-1])
    out = df[[date_col, y_col]].rename(columns={date_col: "ds", y_col: "y"})
    out["ds"] = pd.to_datetime(out["ds"], errors="coerce").dt.tz_localize(None)
    out["y"] = pd.to_numeric(out["y"], errors="coerce")
    return out.dropna().sort_values("ds").reset_index(drop=True)


def run_forecast(history: pd.DataFrame, days: int = 30):
    """Fit Prophet and forecast `days` ahead. Returns (model, forecast_df)."""
    from prophet import Prophet

    span_days = (history["ds"].max() - history["ds"].min()).days
    model = Prophet(
        daily_seasonality=False,
        weekly_seasonality=True,
        yearly_seasonality=span_days >= 400,  # Nyquist guard: need >1yr of data
        changepoint_prior_scale=0.05,
        interval_width=0.8,
    )
    model.fit(history)
    future = model.make_future_dataframe(periods=days, freq="D")
    forecast = model.predict(future)
    return model, forecast


def _style_dark(ax):
    ax.set_facecolor("#111111")
    for spine in ax.spines.values():
        spine.set_color("#333333")
    ax.tick_params(colors="#aaaaaa", labelsize=8)
    ax.xaxis.label.set_color("#cccccc")
    ax.yaxis.label.set_color("#cccccc")
    ax.title.set_color("#ffffff")
    ax.grid(True, color="#222222", linewidth=0.6)


def render_chart(symbol: str, history: pd.DataFrame, forecast: pd.DataFrame,
                 days: int, photo_path: Path) -> Path:
    """Render dark-themed forecast dashboard PNG (main forecast + trend + weekly seasonality)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cutoff = history["ds"].max()
    hist_window = history[history["ds"] >= cutoff - pd.Timedelta(days=180)]
    fc_future = forecast[forecast["ds"] > cutoff]
    fc_window = forecast[forecast["ds"] >= cutoff - pd.Timedelta(days=180)]

    fig = plt.figure(figsize=(11, 8), facecolor="#0a0a0a")
    gs = fig.add_gridspec(2, 2, height_ratios=[2.2, 1], hspace=0.35, wspace=0.25)

    # --- Panel 1: history + forecast with confidence band ---
    ax1 = fig.add_subplot(gs[0, :])
    _style_dark(ax1)
    ax1.plot(hist_window["ds"], hist_window["y"], color="#e5e5e5", linewidth=1.2, label="Actual Close")
    ax1.plot(fc_window["ds"], fc_window["yhat"], color="#00f5d4", linewidth=1.6, label="Prophet Forecast (yhat)")
    ax1.fill_between(fc_future["ds"], fc_future["yhat_lower"], fc_future["yhat_upper"],
                     color="#00f5d4", alpha=0.15, label="80% Confidence Band")
    ax1.axvline(cutoff, color="#f59e0b", linestyle="--", linewidth=1, alpha=0.8)
    ax1.text(cutoff, ax1.get_ylim()[1], " today", color="#f59e0b", fontsize=8, va="top")
    ax1.set_title(f"{symbol} — Prophet {days}-Day Forecast", fontsize=13, fontweight="bold")
    ax1.legend(loc="upper left", frameon=True, facecolor="#1a1a1a",
               edgecolor="#333333", labelcolor="#ffffff", fontsize=8)

    # --- Panel 2: trend component ---
    ax2 = fig.add_subplot(gs[1, 0])
    _style_dark(ax2)
    ax2.plot(fc_window["ds"], fc_window["trend"], color="#d946ef", linewidth=1.5)
    ax2.set_title("Macro Trend Component", fontsize=10, fontweight="bold")
    for label in ax2.get_xticklabels():
        label.set_rotation(25)

    # --- Panel 3: weekly seasonality (% of yhat) ---
    ax3 = fig.add_subplot(gs[1, 1])
    _style_dark(ax3)
    if "weekly" in forecast.columns:
        fc = forecast.copy()
        fc["weekday"] = fc["ds"].dt.dayofweek
        mean_yhat = max(abs(fc["yhat"].mean()), 1e-9)
        weekly_pct = fc.groupby("weekday")["weekly"].mean() / mean_yhat * 100.0
        colors = ["#00f5d4" if v >= 0 else "#ef4444" for v in weekly_pct.values]
        ax3.bar([WEEKDAY_EN[i] for i in weekly_pct.index], weekly_pct.values, color=colors)
        ax3.axhline(0, color="#555555", linewidth=0.8)
        ax3.set_title("Weekly Seasonality (% of yhat)", fontsize=10, fontweight="bold")
    else:
        ax3.text(0.5, 0.5, "No weekly seasonality", color="#888888", ha="center", va="center")

    photo_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(photo_path, facecolor=fig.get_facecolor(), edgecolor="none",
                bbox_inches="tight", dpi=110)
    plt.close(fig)
    return photo_path


def build_report(symbol: str, history: pd.DataFrame, forecast: pd.DataFrame,
                 days: int, html: bool = True) -> str:
    """Build a summary report string (Telegram HTML or plain text)."""
    cutoff = history["ds"].max()
    last_price = float(history["y"].iloc[-1])
    fc_future = forecast[forecast["ds"] > cutoff]
    fc_end = fc_future.iloc[-1] if not fc_future.empty else forecast.iloc[-1]

    yhat_end = float(fc_end["yhat"])
    lo, hi = float(fc_end["yhat_lower"]), float(fc_end["yhat_upper"])
    chg_pct = (yhat_end / last_price - 1.0) * 100.0

    # trend slope: avg daily change of trend component over the forecast horizon
    trend_future = fc_future["trend"].values
    slope_pct = 0.0
    if len(trend_future) >= 2:
        slope_pct = (trend_future[-1] - trend_future[0]) / max(len(trend_future) - 1, 1) / last_price * 100.0

    if chg_pct >= 3:
        signal, emoji = "강세 (BULLISH)", "🚀"
    elif chg_pct <= -3:
        signal, emoji = "약세 (BEARISH)", "📉"
    else:
        signal, emoji = "중립 (NEUTRAL)", "⚖️"

    best_day = ""
    if "weekly" in forecast.columns:
        fc = forecast.copy()
        fc["weekday"] = fc["ds"].dt.dayofweek
        weekly_mean = fc.groupby("weekday")["weekly"].mean()
        best_day = WEEKDAY_KO[int(weekly_mean.idxmax())] + "요일"

    b, _b = ("<b>", "</b>") if html else ("", "")
    code, _code = ("<code>", "</code>") if html else ("", "")
    lines = [
        f"{emoji} {b}{symbol} Prophet {days}일 예측 리포트{_b}",
        "=" * 32,
        f"• 현재가: {code}{last_price:,.2f}{_code}",
        f"• {days}일 후 예측가(yhat): {code}{yhat_end:,.2f}{_code} ({chg_pct:+.2f}%)",
        f"• 80% 신뢰구간: {code}{lo:,.2f} ~ {hi:,.2f}{_code}",
        f"• 일평균 추세 기울기: {code}{slope_pct:+.3f}%/일{_code}",
    ]
    if best_day:
        lines.append(f"• 주간 계절성 최고점: {code}{best_day}{_code}")
    lines += [
        f"• 종합 시그널: {b}{signal}{_b}",
        "=" * 32,
        f"기준일: {cutoff.date()} | 학습 데이터: {len(history)}일",
    ]
    return "\n".join(lines)


def generate_prophet_forecast(symbol_raw: str, days: int = 30,
                              output: str | None = None,
                              csv: str | None = None) -> tuple[str, str]:
    """High-level API used by the Telegram bot and CLI.

    Returns (report_html, photo_path).
    """
    symbol = resolve_symbol(symbol_raw)
    if not symbol:
        raise ValueError("Symbol is required (e.g. /prophet TSLA 30)")
    days = max(5, min(int(days), 365))

    history = load_csv_history(csv) if csv else fetch_history(symbol)
    _, forecast = run_forecast(history, days)

    photo_path = Path(output) if output else (
        DATA_DIR / f"prophet_{symbol.replace('-', '_')}_{datetime.now():%Y%m%d_%H%M%S}.png"
    )
    render_chart(symbol, history, forecast, days, photo_path)
    report = build_report(symbol, history, forecast, days, html=True)
    return report, str(photo_path)


# ----------------- CLI -----------------

def main():
    parser = argparse.ArgumentParser(description="Prophet forecast & visualization (No Slip Quant)")
    parser.add_argument("symbol", help="Ticker: TSLA, NVDA, BTC, ETH, SOL, BTC-USD ...")
    parser.add_argument("--days", type=int, default=30, help="Forecast horizon in days (default 30)")
    parser.add_argument("--output", default=None, help="Output PNG path")
    parser.add_argument("--csv", default=None, help="Offline CSV (date+close columns) instead of yfinance")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON result")
    args = parser.parse_args()

    try:
        report_html, photo_path = generate_prophet_forecast(
            args.symbol, days=args.days, output=args.output, csv=args.csv
        )
    except Exception as e:
        print(f"❌ Prophet forecast failed: {e}")
        sys.exit(1)

    plain = (report_html.replace("<b>", "").replace("</b>", "")
                        .replace("<code>", "").replace("</code>", ""))
    if args.json:
        print(json.dumps({"report": plain, "photo": photo_path}, ensure_ascii=False, indent=2))
    else:
        print(plain)
        print(f"\n🖼️ Chart saved: {photo_path}")


if __name__ == "__main__":
    main()
