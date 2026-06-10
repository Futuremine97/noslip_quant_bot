"""Daily market-brief card-news pipeline for No Slip Quant.

Generates Instagram-style 1080x1080 info cards from the bot's own data
(yfinance market snapshot + Prophet forecast engine) and broadcasts them
to Telegram as an album.

Cards
-----
1. Cover       : date + headline metric
2. Market      : indices & crypto table with daily change
3. Top Movers  : gainers/losers bar chart
4. Prophet     : 7-day BTC forecast mini chart + signal
5. Outro       : signal summary + disclaimer

Usage
-----
    .venv/bin/python services/trader/daily_card_news.py              # generate + send to Telegram
    .venv/bin/python services/trader/daily_card_news.py --no-send    # generate only
    .venv/bin/python services/trader/daily_card_news.py --demo       # offline synthetic data test

Automation: appended as step 10 in run_daily.sh (08:30 KST daily via launchd).
Telegram on-demand: /cardnews (installed by apply_cardnews_patch.py).
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
ROOT_DIR = BASE_DIR.parent.parent
OUT_BASE = ROOT_DIR / "data" / "card_news"

# ----------------- Theme -----------------
BG = "#0a0a0a"
CARD_BG = "#111111"
ACCENT = "#00f5d4"
ACCENT2 = "#d946ef"
UP = "#00f5d4"
DOWN = "#ef4444"
TEXT = "#f5f5f5"
SUBTEXT = "#9ca3af"
BRAND = "NO SLIP QUANT"

INDICES = [("S&P 500", "^GSPC"), ("NASDAQ", "^IXIC"), ("DOW", "^DJI"), ("KOSPI", "^KS11")]
CRYPTOS = [("BTC", "BTC-USD"), ("ETH", "ETH-USD"), ("SOL", "SOL-USD")]
MOVER_UNIVERSE = ["NVDA", "MSFT", "GOOGL", "AMZN", "META", "AAPL", "TSLA",
                  "AMD", "AVGO", "TSM", "MU", "INTC", "PLTR", "ORCL", "SMCI", "VRT"]


def setup_korean_font():
    """Pick a Korean-capable font (AppleGothic on macOS); fall back silently."""
    import matplotlib
    from matplotlib import font_manager
    candidates = ["AppleGothic", "Apple SD Gothic Neo", "NanumGothic",
                  "Noto Sans CJK KR", "Malgun Gothic"]
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in available:
            matplotlib.rcParams["font.family"] = name
            break
    matplotlib.rcParams["axes.unicode_minus"] = False


# ----------------- Data Layer -----------------

def fetch_market_snapshot() -> dict:
    """Pull 2-day closes for indices/cryptos/movers via yfinance -> snapshot dict."""
    import yfinance as yf

    tickers = [t for _, t in INDICES] + [t for _, t in CRYPTOS] + MOVER_UNIVERSE
    df = yf.download(tickers, period="5d", interval="1d", auto_adjust=True,
                     progress=False)["Close"]

    def last_change(tk):
        s = df[tk].dropna()
        if len(s) < 2:
            return None, None
        return float(s.iloc[-1]), (float(s.iloc[-1]) / float(s.iloc[-2]) - 1) * 100

    snap = {"indices": [], "cryptos": [], "movers": []}
    for name, tk in INDICES:
        px, chg = last_change(tk)
        if px is not None:
            snap["indices"].append({"name": name, "price": px, "chg": chg})
    for name, tk in CRYPTOS:
        px, chg = last_change(tk)
        if px is not None:
            snap["cryptos"].append({"name": name, "price": px, "chg": chg})
    for tk in MOVER_UNIVERSE:
        px, chg = last_change(tk)
        if px is not None:
            snap["movers"].append({"name": tk, "price": px, "chg": chg})
    snap["movers"].sort(key=lambda m: m["chg"], reverse=True)
    return snap


def demo_snapshot() -> dict:
    rng = np.random.default_rng(42)
    snap = {"indices": [], "cryptos": [], "movers": []}
    for name, _ in INDICES:
        snap["indices"].append({"name": name, "price": float(rng.uniform(4000, 45000)),
                                "chg": float(rng.normal(0.2, 1.2))})
    for name, base in [("BTC", 95000), ("ETH", 4800), ("SOL", 210)]:
        snap["cryptos"].append({"name": name, "price": base * float(rng.uniform(0.97, 1.03)),
                                "chg": float(rng.normal(0.5, 3.0))})
    for tk in MOVER_UNIVERSE:
        snap["movers"].append({"name": tk, "price": float(rng.uniform(50, 900)),
                               "chg": float(rng.normal(0, 2.5))})
    snap["movers"].sort(key=lambda m: m["chg"], reverse=True)
    return snap


def get_prophet_card_data(demo: bool = False) -> dict | None:
    """7-day BTC forecast using the project's prophet_forecast engine."""
    try:
        if demo:
            raise RuntimeError("demo mode")
        from prophet_forecast import fetch_history, run_forecast
        history = fetch_history("BTC-USD", lookback_days=365)
        _, forecast = run_forecast(history, days=7)
    except Exception as e:
        if not demo:
            print(f"⚠️ Prophet card unavailable ({e}); using naive trend fallback")
        rng = np.random.default_rng(3)
        ds = pd.date_range(end=pd.Timestamp.today(), periods=120, freq="D")
        y = 90000 * np.exp(np.cumsum(rng.normal(0.001, 0.02, 120)))
        history = pd.DataFrame({"ds": ds, "y": y})
        x = np.arange(120 + 7)
        coef = np.polyfit(np.arange(120), y, 1)
        yhat = np.polyval(coef, x)
        sigma = np.std(y) * 0.08
        forecast = pd.DataFrame({
            "ds": pd.date_range(ds[0], periods=127, freq="D"),
            "yhat": yhat, "trend": yhat,
            "yhat_lower": yhat - sigma * (1 + x / 127),
            "yhat_upper": yhat + sigma * (1 + x / 127),
        })
    cutoff = history["ds"].max()
    last = float(history["y"].iloc[-1])
    fc_future = forecast[forecast["ds"] > cutoff]
    end = fc_future.iloc[-1] if not fc_future.empty else forecast.iloc[-1]
    chg = (float(end["yhat"]) / last - 1) * 100
    return {"history": history, "forecast": forecast, "cutoff": cutoff,
            "last": last, "yhat_end": float(end["yhat"]), "chg": chg,
            "lo": float(end["yhat_lower"]), "hi": float(end["yhat_upper"])}


# ----------------- Card Renderer -----------------

def _new_card():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(10.8, 10.8), dpi=100, facecolor=BG)
    return fig, plt


def _frame(fig, plt, page: int, total: int, title: str, subtitle: str = ""):
    """Common card chrome: border, brand footer, page dots, title block."""
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.add_patch(plt.Rectangle((0.035, 0.035), 0.93, 0.93, fill=True,
                               facecolor=CARD_BG, edgecolor="#262626", linewidth=2))
    ax.add_patch(plt.Rectangle((0.035, 0.93), 0.93, 0.035, fill=True,
                               facecolor=ACCENT, edgecolor="none"))
    ax.text(0.08, 0.875, title, fontsize=34, fontweight="bold", color=TEXT,
            ha="left", va="top", transform=fig.transFigure)
    if subtitle:
        ax.text(0.08, 0.825, subtitle, fontsize=16, color=SUBTEXT,
                ha="left", va="top", transform=fig.transFigure)
    ax.text(0.08, 0.06, BRAND, fontsize=13, fontweight="bold", color=ACCENT,
            ha="left", va="center", transform=fig.transFigure)
    for i in range(total):
        ax.scatter(0.86 + i * 0.022, 0.06, s=40,
                   color=ACCENT if i == page - 1 else "#333333",
                   transform=fig.transFigure, clip_on=False)
    return ax


def card_cover(date_str: str, snap: dict, path: Path, total: int):
    fig, plt = _new_card()
    ax = _frame(fig, plt, 1, total, "오늘의 시황 카드뉴스", date_str)
    spx = next((i for i in snap["indices"] if i["name"] == "S&P 500"), None)
    btc = next((c for c in snap["cryptos"] if c["name"] == "BTC"), None)
    headline = spx or btc
    if headline:
        color = UP if headline["chg"] >= 0 else DOWN
        arrow = "▲" if headline["chg"] >= 0 else "▼"
        ax.text(0.5, 0.55, f"{headline['name']}", fontsize=30, color=SUBTEXT,
                ha="center", transform=fig.transFigure)
        ax.text(0.5, 0.44, f"{arrow} {headline['chg']:+.2f}%", fontsize=72,
                fontweight="bold", color=color, ha="center", transform=fig.transFigure)
        ax.text(0.5, 0.35, f"{headline['price']:,.2f}", fontsize=24, color=TEXT,
                ha="center", transform=fig.transFigure)
    ax.text(0.5, 0.18, "AI 퀀트봇이 매일 아침 자동 생성하는 시황 브리핑",
            fontsize=15, color=SUBTEXT, ha="center", transform=fig.transFigure)
    fig.savefig(path, facecolor=BG)
    plt.close(fig)


def card_market(snap: dict, path: Path, total: int):
    fig, plt = _new_card()
    ax = _frame(fig, plt, 2, total, "글로벌 마켓 요약", "주요 지수 & 암호화폐 (전일 대비)")
    rows = snap["indices"] + snap["cryptos"]
    y = 0.72
    for r in rows:
        color = UP if r["chg"] >= 0 else DOWN
        arrow = "▲" if r["chg"] >= 0 else "▼"
        ax.text(0.10, y, r["name"], fontsize=24, fontweight="bold", color=TEXT,
                transform=fig.transFigure)
        ax.text(0.62, y, f"{r['price']:,.2f}", fontsize=22, color=TEXT,
                ha="right", transform=fig.transFigure)
        ax.text(0.90, y, f"{arrow} {r['chg']:+.2f}%", fontsize=22, fontweight="bold",
                color=color, ha="right", transform=fig.transFigure)
        ax.plot([0.08, 0.92], [y - 0.035, y - 0.035], color="#222222", linewidth=1,
                transform=fig.transFigure)
        y -= 0.082
    fig.savefig(path, facecolor=BG)
    plt.close(fig)


def card_movers(snap: dict, path: Path, total: int):
    fig, plt = _new_card()
    _frame(fig, plt, 3, total, "오늘의 TOP MOVERS", "관심 유니버스 상승/하락 상위 종목")
    movers = snap["movers"]
    top = movers[:5]
    bottom = movers[-5:][::-1]
    sel = top + bottom
    names = [m["name"] for m in sel]
    vals = [m["chg"] for m in sel]
    colors = [UP if v >= 0 else DOWN for v in vals]
    axb = fig.add_axes([0.15, 0.14, 0.74, 0.62])
    axb.set_facecolor(CARD_BG)
    ypos = np.arange(len(sel))[::-1]
    axb.barh(ypos, vals, color=colors, height=0.62)
    axb.set_yticks(ypos)
    axb.set_yticklabels(names, fontsize=15, color=TEXT, fontweight="bold")
    axb.axvline(0, color="#444444", linewidth=1)
    for spine in axb.spines.values():
        spine.set_visible(False)
    axb.tick_params(colors=SUBTEXT, labelsize=12)
    axb.grid(True, axis="x", color="#1f1f1f", linewidth=0.7)
    vmax = max(abs(v) for v in vals) if vals else 1.0
    pad = vmax * 0.06
    axb.set_xlim(-vmax * 1.45, vmax * 1.45)
    for yp, v in zip(ypos, vals):
        axb.text(v + (pad if v >= 0 else -pad), yp, f"{v:+.2f}%", fontsize=12,
                 fontweight="bold", color=UP if v >= 0 else DOWN,
                 va="center", ha="left" if v >= 0 else "right")
    fig.savefig(path, facecolor=BG)
    plt.close(fig)


def card_prophet(pdata: dict, path: Path, total: int):
    fig, plt = _new_card()
    ax = _frame(fig, plt, 4, total, "BTC 7일 Prophet 예측", "시계열 머신러닝 예측 (80% 신뢰구간)")
    hist = pdata["history"]
    fc = pdata["forecast"]
    cutoff = pdata["cutoff"]
    hist_win = hist[hist["ds"] >= cutoff - pd.Timedelta(days=60)]
    fc_win = fc[fc["ds"] >= cutoff - pd.Timedelta(days=60)]
    fc_fut = fc[fc["ds"] > cutoff]

    axc = fig.add_axes([0.12, 0.30, 0.8, 0.45])
    axc.set_facecolor(CARD_BG)
    axc.plot(hist_win["ds"], hist_win["y"], color=TEXT, linewidth=1.4)
    axc.plot(fc_win["ds"], fc_win["yhat"], color=ACCENT, linewidth=1.8)
    axc.fill_between(fc_fut["ds"], fc_fut["yhat_lower"], fc_fut["yhat_upper"],
                     color=ACCENT, alpha=0.18)
    axc.axvline(cutoff, color="#f59e0b", linestyle="--", linewidth=1)
    for spine in axc.spines.values():
        spine.set_color("#333333")
    axc.tick_params(colors=SUBTEXT, labelsize=11)
    axc.grid(True, color="#1c1c1c", linewidth=0.6)

    color = UP if pdata["chg"] >= 0 else DOWN
    arrow = "▲" if pdata["chg"] >= 0 else "▼"
    ax.text(0.12, 0.215, f"7일 후 예측가  {pdata['yhat_end']:,.0f}",
            fontsize=22, fontweight="bold", color=TEXT, transform=fig.transFigure)
    ax.text(0.90, 0.215, f"{arrow} {pdata['chg']:+.2f}%", fontsize=24,
            fontweight="bold", color=color, ha="right", transform=fig.transFigure)
    ax.text(0.12, 0.165, f"신뢰구간  {pdata['lo']:,.0f} ~ {pdata['hi']:,.0f}",
            fontsize=15, color=SUBTEXT, transform=fig.transFigure)
    fig.savefig(path, facecolor=BG)
    plt.close(fig)


def card_outro(snap: dict, pdata: dict | None, path: Path, total: int):
    fig, plt = _new_card()
    ax = _frame(fig, plt, 5, total, "오늘의 시그널 요약")
    ups = sum(1 for m in snap["movers"] if m["chg"] >= 0)
    downs = len(snap["movers"]) - ups
    breadth = "위험선호 (Risk-On)" if ups > downs else "위험회피 (Risk-Off)"
    lines = [
        ("시장 폭(Breadth)", f"상승 {ups} : 하락 {downs} → {breadth}"),
    ]
    if snap["cryptos"]:
        btc = snap["cryptos"][0]
        lines.append(("크립토", f"BTC {btc['chg']:+.2f}% " + ("강세 유지" if btc["chg"] >= 0 else "조정 국면")))
    if pdata:
        sig = "BULLISH 🚀" if pdata["chg"] >= 3 else ("BEARISH 📉" if pdata["chg"] <= -3 else "NEUTRAL ⚖️")
        lines.append(("Prophet 7일 시그널", f"BTC {pdata['chg']:+.2f}% → {sig}"))
    y = 0.66
    for k, v in lines:
        ax.text(0.10, y, f"• {k}", fontsize=22, fontweight="bold", color=ACCENT,
                transform=fig.transFigure)
        ax.text(0.13, y - 0.05, v, fontsize=19, color=TEXT, transform=fig.transFigure)
        y -= 0.13
    ax.text(0.5, 0.17, "본 콘텐츠는 자동 생성된 정보 제공용이며 투자 자문이 아닙니다.",
            fontsize=13, color=SUBTEXT, ha="center", transform=fig.transFigure)
    ax.text(0.5, 0.135, "Generated by No Slip Quant Bot · Prophet · 6-Agent Consensus",
            fontsize=12, color="#555555", ha="center", transform=fig.transFigure)
    fig.savefig(path, facecolor=BG)
    plt.close(fig)


# ----------------- Telegram -----------------

def send_telegram_album(paths: list[Path], caption: str):
    import requests
    from dotenv import load_dotenv
    load_dotenv(ROOT_DIR / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_ids = [c.strip() for c in os.getenv("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
    if not token or not chat_ids:
        print("⚠️ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not configured; skipping send")
        return
    url = f"https://api.telegram.org/bot{token}/sendMediaGroup"
    for chat_id in chat_ids:
        media, files = [], {}
        for i, p in enumerate(paths):
            key = f"card{i}"
            files[key] = open(p, "rb")
            item = {"type": "photo", "media": f"attach://{key}"}
            if i == 0:
                item["caption"] = caption
                item["parse_mode"] = "HTML"
            media.append(item)
        try:
            res = requests.post(url, data={"chat_id": chat_id, "media": json.dumps(media)},
                                files=files, timeout=60)
            res.raise_for_status()
            print(f"✅ Card news album sent to chat {chat_id}")
        except Exception as e:
            print(f"❌ Failed to send card news to {chat_id}: {e}")
        finally:
            for f in files.values():
                f.close()


def publish_instagram_carousel(paths: list[Path], caption: str):
    """Publish the card set as an Instagram carousel via instagram_publisher infra."""
    from dotenv import load_dotenv
    load_dotenv(ROOT_DIR / ".env")
    business_id = os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID")
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    if not business_id or not access_token:
        print("⚠️ Instagram credentials not configured; skipping Instagram publish")
        return
    try:
        from instagram_publisher import upload_image_to_tmpfiles, publish_to_instagram
        urls = [upload_image_to_tmpfiles(p) for p in paths]
        urls = [u for u in urls if u]
        if not urls:
            raise RuntimeError("no image URLs uploaded")
        media_id = publish_to_instagram(business_id, access_token, urls, caption)
        print(f"✅ Instagram carousel published: {media_id}")
    except Exception as e:
        print(f"❌ Instagram publish failed: {e}")


# ----------------- Pipeline -----------------

def generate_card_news(demo: bool = False, outdir: str | None = None,
                       send: bool = True, instagram: bool = False) -> list[str]:
    setup_korean_font()
    today = datetime.now()
    date_str = today.strftime("%Y년 %m월 %d일 (%a)")
    out = Path(outdir) if outdir else OUT_BASE / today.strftime("%Y%m%d")
    out.mkdir(parents=True, exist_ok=True)

    print("📡 1/3 Collecting market data...")
    snap = demo_snapshot() if demo else fetch_market_snapshot()
    pdata = get_prophet_card_data(demo=demo)

    print("🎨 2/3 Rendering cards...")
    total = 5
    paths = [
        out / "card_1_cover.png", out / "card_2_market.png", out / "card_3_movers.png",
        out / "card_4_prophet.png", out / "card_5_outro.png",
    ]
    card_cover(date_str, snap, paths[0], total)
    card_market(snap, paths[1], total)
    card_movers(snap, paths[2], total)
    card_prophet(pdata, paths[3], total)
    card_outro(snap, pdata, paths[4], total)
    for p in paths:
        print(f"   🖼️ {p}")

    if send:
        print("📨 3/3 Sending Telegram album...")
        send_telegram_album(paths, f"🗞️ <b>오늘의 시황 카드뉴스</b> | {date_str}")
    else:
        print("⏭️ 3/3 Send skipped (--no-send)")
    if instagram:
        print("📷 Publishing Instagram carousel...")
        ig_caption = (f"🗞️ 오늘의 시황 카드뉴스 | {date_str}\n\n"
                      "AI 퀀트봇이 자동 생성한 데일리 마켓 브리핑입니다.\n"
                      "#주식 #시황 #퀀트 #AI #비트코인 #noslipquant")
        publish_instagram_carousel(paths, ig_caption)
    return [str(p) for p in paths]


def main():
    parser = argparse.ArgumentParser(description="Daily market card-news pipeline (No Slip Quant)")
    parser.add_argument("--demo", action="store_true", help="Offline synthetic data (testing)")
    parser.add_argument("--no-send", action="store_true", help="Generate only; skip Telegram")
    parser.add_argument("--instagram", action="store_true", help="Also publish as Instagram carousel")
    parser.add_argument("--outdir", default=None, help="Output directory override")
    args = parser.parse_args()
    try:
        paths = generate_card_news(demo=args.demo, outdir=args.outdir,
                                   send=not args.no_send, instagram=args.instagram)
        print(json.dumps({"cards": paths}, ensure_ascii=False))
    except Exception as e:
        print(f"❌ Card news pipeline failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
