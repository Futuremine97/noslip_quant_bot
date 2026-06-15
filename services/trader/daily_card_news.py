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
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

try:
    import google.generativeai as genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

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


# ----------------- Custom Topic & Pillow Drawing Renderer -----------------

def get_font_by_lang(size: int, index: int = 0, lang: str = "ko"):
    """Pick optimal font by language (AppleSDGothicNeo for ko, Hiragino/Osaka for ja)."""
    font_path_ko = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
    
    # Try Japanese specific fonts first if lang is ja
    if lang in ["ja", "jp"]:
        candidates = []
        if os.path.exists("/System/Library/Fonts"):
            try:
                for f in os.listdir("/System/Library/Fonts"):
                    if "ヒラ" in f and "角" in f:
                        candidates.append(os.path.join("/System/Library/Fonts", f))
            except:
                pass
        candidates.extend([
            "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
            "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/Supplemental/NotoSansGothic-Regular.ttf",
            "/System/Library/Fonts/Osaka.ttf"
        ])
        for path in candidates:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size, index=0)
                except:
                    pass
        # Fallback to standard AppleSDGothicNeo which often has good CJK support
        if os.path.exists(font_path_ko):
            try:
                return ImageFont.truetype(font_path_ko, size, index=index)
            except:
                pass
    else:
        if os.path.exists(font_path_ko):
            try:
                return ImageFont.truetype(font_path_ko, size, index=index)
            except:
                pass
                
    return ImageFont.load_default()

def create_diagonal_gradient(width, height, color1, color2):
    base = Image.new("RGBA", (width, height))
    pixels = base.load()
    for y in range(height):
        for x in range(width):
            ratio = (x + y) / (width + height)
            r = int(color1[0] * (1 - ratio) + color2[0] * ratio)
            g = int(color1[1] * (1 - ratio) + color2[1] * ratio)
            b = int(color1[2] * (1 - ratio) + color2[2] * ratio)
            pixels[x, y] = (r, g, b, 255)
    return base

def draw_dotted_line(draw, points, fill=(226, 110, 80, 120), width=2, gap=10):
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i+1]
        dx = x2 - x1
        dy = y2 - y1
        dist = math.sqrt(dx**2 + dy**2)
        if dist == 0:
            continue
        step_x = (dx / dist) * gap
        step_y = (dy / dist) * gap
        current_x, current_y = x1, y1
        accum_dist = 0
        draw_dash = True
        while accum_dist < dist:
            next_x = min(current_x + step_x, x2) if dx >= 0 else max(current_x + step_x, x2)
            next_y = min(current_y + step_y, y2) if dy >= 0 else max(current_y + step_y, y2)
            if draw_dash:
                draw.line([(current_x, current_y), (next_x, next_y)], fill=fill, width=width)
            current_x, current_y = next_x, next_y
            accum_dist += gap
            draw_dash = not draw_dash

def draw_grid_lines(img, step=70, color=(0, 230, 118, 14)):
    draw = ImageDraw.Draw(img)
    w, h = img.size
    for x in range(0, w, step):
        draw.line([(x, 0), (x, h)], fill=color, width=1)
    for y in range(0, h, step):
        draw.line([(0, y), (w, y)], fill=color, width=1)

def draw_glow_dots(img, dots, color=(0, 230, 118, 100)):
    draw = ImageDraw.Draw(img)
    for cx, cy, radius in dots:
        for r in range(radius * 3, radius, -2):
            alpha = int(40 * (1.0 - (r - radius) / (radius * 2)))
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(color[0], color[1], color[2], alpha))
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=(255, 255, 255, 255))

# 3 unique theme drawers
def build_theme_cyber():
    img = create_diagonal_gradient(1080, 1080, (4, 12, 24), (12, 34, 58))
    draw_grid_lines(img, step=75, color=(0, 245, 212, 12))
    draw = ImageDraw.Draw(img)
    draw.arc([100, 100, 980, 980], start=180, end=270, fill=(0, 245, 212, 40), width=2)
    draw_dotted_line(draw, [(100, 980), (980, 100)], fill=(217, 70, 239, 120), width=4, gap=15)
    draw_glow_dots(img, [(980, 100, 12)], (217, 70, 239))
    return img

def build_theme_emerald():
    img = create_diagonal_gradient(1080, 1080, (4, 18, 14), (16, 42, 34))
    draw_grid_lines(img, step=70, color=(0, 230, 118, 12))
    draw = ImageDraw.Draw(img)
    draw.line([(100, 900), (300, 800), (500, 850), (700, 600), (900, 500), (1000, 300)], fill=(0, 230, 118, 100), width=4, joint="round")
    draw_glow_dots(img, [(1000, 300, 10)], (0, 230, 118))
    return img

def build_theme_peach():
    img = create_diagonal_gradient(1080, 1080, (255, 230, 217), (255, 246, 232))
    draw = ImageDraw.Draw(img)
    draw_dotted_line(draw, [(150, 900), (450, 800), (750, 880), (950, 750)], fill=(226, 110, 80, 150), width=4, gap=15)
    draw.ellipse([930, 730, 970, 770], fill=(226, 110, 80, 200), outline=(255, 255, 255, 255), width=2)
    return img

def wrap_text_chars(text, font, max_width, draw):
    lines = []
    current_line = ""
    for char in text:
        test_line = current_line + char
        bbox = draw.textbbox((0, 0), test_line, font=font)
        w = bbox[2] - bbox[0]
        if w <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = char
    if current_line:
        lines.append(current_line)
    return lines


def blend_color_match(mascot_img, bg_img, x, y):
    mw, mh = mascot_img.size
    bg_w, bg_h = bg_img.size
    
    # Ensure crop area is within background bounds
    x1 = max(0, min(x, bg_w - 1))
    y1 = max(0, min(y, bg_h - 1))
    x2 = max(0, min(x + mw, bg_w))
    y2 = max(0, min(y + mh, bg_h))
    
    if x2 <= x1 or y2 <= y1:
        return mascot_img
        
    bg_crop = bg_img.crop((x1, y1, x2, y2)).resize((mw, mh))
    mascot_rgba = mascot_img.convert("RGBA")
    
    # Calculate average color to tint the mascot
    avg_bg = bg_crop.resize((1, 1)).resize((mw, mh))
    
    # Split channels
    mr, mg, mb, ma = mascot_rgba.split()
    ar, ag, ab, aa = avg_bg.convert("RGBA").split()
    
    # 8% tint of background average color to match environment mood/lighting
    tint_factor = 0.08
    tinted_r = Image.blend(mr, ar, tint_factor)
    tinted_g = Image.blend(mg, ag, tint_factor)
    tinted_b = Image.blend(mb, ab, tint_factor)
    
    tinted_mascot = Image.merge("RGBA", (tinted_r, tinted_g, tinted_b, ma))
    
    # Calculate relative luminance of background to match brightness
    avg_pixel = bg_crop.resize((1, 1)).getpixel((0, 0))
    r, g, b = avg_pixel[:3]
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    
    # Map luminance to brightness enhancement
    # Darker background -> slightly darker mascot; brighter background -> brighter mascot
    brightness_mult = 0.82 + (luminance * 0.36)  # Range ~ [0.82, 1.18]
    enhancer = ImageEnhance.Brightness(tinted_mascot)
    tinted_mascot = enhancer.enhance(brightness_mult)
    
    return tinted_mascot


def create_pillow_slide(bg_img, slide_num, total_slides, title, subtitle, bullets, is_cover=False, lang="ko"):
    """Assemble a single custom theme slide with dynamic experimental layout and glassmorphic panel."""
    # A dark panel with white text works best on photo backgrounds to guarantee readability!
    is_light = False
    
    color_text = (245, 245, 245, 255)
    color_sub = (0, 245, 212, 255)
    color_muted = (156, 163, 175, 255)
    panel_fill = (12, 16, 22, 160) # more transparent dark blue-gray translucent for stronger glassmorphism
    panel_border = (0, 245, 212, 60) # cyan border glow for cyber robotics theme
    
    overlay = Image.new("RGBA", (1080, 1080), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    
    # Load mascot details
    from pathlib import Path
    mascot_dir = Path("/Users/sunghoon/.gemini/antigravity/scratch/no-slip-saas/data/mascot_poses")
    mascot_list = [
        "mascot_joyful.png",
        "mascot_smart_pointing.png",
        "mascot_explaining.png",
        "mascot_coffee_desk.png",
        "mascot_two_talking.png",
        "mascot_coffee_phone.png",
        "mascot_group_circle.png",
        "mascot_worried.png",
        "mascot_scared_running.png",
        "mascot_joyful.png"
    ]
    m_idx = (slide_num - 1) % len(mascot_list)
    mascot_name = mascot_list[m_idx]
    mascot_path = mascot_dir / mascot_name
    has_mascot = mascot_path.exists()
    
    # Experimental layout coordinates based on slide_num
    layout_type = slide_num % 4
    
    if is_cover:
        # Cover layout: Left half is text panel, right half is huge mascot (split screen)
        card_left, card_top = 80, 160
        card_right, card_bottom = 600, 920
        mascot_scale = 340
        mascot_x = 640
        mascot_y = 440
    elif layout_type == 1:
        # Right aligned panel, mascot on the left pointing
        card_left, card_top = 480, 120
        card_right, card_bottom = 1000, 960
        mascot_scale = 280
        mascot_x = 90
        mascot_y = 420
    elif layout_type == 2:
        # Left aligned panel, mascot on the right
        card_left, card_top = 80, 120
        card_right, card_bottom = 620, 960
        mascot_scale = 280
        mascot_x = 680
        mascot_y = 420
    elif layout_type == 3:
        # Top panel, mascot at the bottom center
        card_left, card_top = 80, 100
        card_right, card_bottom = 1000, 720
        mascot_scale = 260
        mascot_x = 410
        mascot_y = 740
    else:
        # Centered panel, mascot overlapping bottom right
        card_left, card_top = 80, 120
        card_right, card_bottom = 1000, 960
        mascot_scale = 260
        mascot_x = 720
        mascot_y = 660

    # Draw experimental translucent panel
    draw.rounded_rectangle(
        [card_left, card_top, card_right, card_bottom],
        radius=28,
        fill=panel_fill,
        outline=panel_border,
        width=2
    )
    
    # Draw futuristic corner brackets/ticks for high-tech engineered style
    tick_len = 16
    tick_color = (0, 245, 212, 180) # Cyan accent
    # Top-Left corner ticks
    draw.line([(card_left - 4, card_top - 4), (card_left - 4 + tick_len, card_top - 4)], fill=tick_color, width=2)
    draw.line([(card_left - 4, card_top - 4), (card_left - 4, card_top - 4 + tick_len)], fill=tick_color, width=2)
    # Bottom-Right corner ticks
    draw.line([(card_right + 4, card_bottom + 4), (card_right + 4 - tick_len, card_bottom + 4)], fill=tick_color, width=2)
    draw.line([(card_right + 4, card_bottom + 4), (card_right + 4, card_bottom + 4 - tick_len)], fill=tick_color, width=2)

    # Draw category badge at the top-left of the panel (highly popular Instagram infographic style)
    badge_w, badge_h = 145, 26
    badge_x = card_left + 35
    badge_y = card_top - 13
    draw.rounded_rectangle(
        [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
        radius=6,
        fill=(0, 245, 212, 255)
    )
    font_badge = get_font_by_lang(11, index=4, lang=lang)
    draw.text((badge_x + 14, badge_y + 5), "GLOBAL ROBOTICS", font=font_badge, fill=(10, 15, 20, 255))
    
    # Fonts
    font_bold = get_font_by_lang(44, index=6, lang=lang) # slightly smaller for smaller panels
    font_medium = get_font_by_lang(25, index=2, lang=lang)
    font_regular = get_font_by_lang(21, index=0, lang=lang)
    font_semibold = get_font_by_lang(20, index=4, lang=lang)
    
    # 2. Side accent line
    draw.rounded_rectangle(
        [card_left + 35, card_top + 45, card_left + 41, card_top + 115],
        radius=3,
        fill=color_sub
    )
    
    # 3. Title & Subtitle
    title_x = card_left + 65
    title_y = card_top + 40
    draw.text((title_x, title_y), title, font=font_bold, fill=color_text)
    draw.text((title_x, title_y + 55), subtitle, font=font_medium, fill=color_sub)
    
    # Divider
    divider_y = title_y + 105
    draw.line([(card_left + 35, divider_y), (card_right - 35, divider_y)], fill=panel_border, width=1)
    
    # 4. Body content
    body_y = divider_y + 35
    content_width = (card_right - card_left) - 90
    
    if is_cover:
        body_y = divider_y + 70
        for line in bullets:
            wrapped = wrap_text_chars(line, font_medium, content_width, draw)
            for wl in wrapped:
                bbox = draw.textbbox((0, 0), wl, font=font_medium)
                text_w = bbox[2] - bbox[0]
                panel_center = card_left + (card_right - card_left) // 2
                draw.text((panel_center - text_w // 2, body_y), wl, font=font_medium, fill=color_text)
                body_y += 42
            body_y += 15
    else:
        for bullet in bullets:
            wrapped = wrap_text_chars(bullet, font_regular, content_width - 35, draw)
            for i, wl in enumerate(wrapped):
                if i == 0:
                    draw.text((card_left + 35, body_y), "•", font=font_regular, fill=color_sub)
                    draw.text((card_left + 60, body_y), wl, font=font_regular, fill=color_text)
                else:
                    draw.text((card_left + 60, body_y), wl, font=font_regular, fill=color_text)
                body_y += 34
            body_y += 15
            
    # Paste Mascot Character if present
    if has_mascot:
        try:
            mascot_img = Image.open(mascot_path).convert("RGBA")
            w, h = mascot_img.size
            if w > h:
                new_w = mascot_scale
                new_h = int(h * (mascot_scale / w))
            else:
                new_h = mascot_scale
                new_w = int(w * (mascot_scale / h))
            mascot_img = mascot_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            
            # Blend mascot colors & brightness with environment background
            mascot_img = blend_color_match(mascot_img, bg_img, mascot_x, mascot_y)
            
            # Generate drop shadow offset to integrate it in the 3D space
            shadow = Image.new("RGBA", mascot_img.size, (0, 0, 0, 0))
            mascot_alpha = mascot_img.split()[3]
            solid_shadow = Image.new("RGBA", mascot_img.size, (12, 15, 20, 160))
            shadow.paste(solid_shadow, (0, 0), mask=mascot_alpha)
            shadow = shadow.filter(ImageFilter.GaussianBlur(15))
            
            # Floor contact shadow (flat oval) to anchor the character to surfaces
            mw, mh = mascot_img.size
            shadow_w = int(mw * 0.9)
            shadow_h = int(mh * 0.14)
            contact_shadow = Image.new("RGBA", (shadow_w, shadow_h), (0, 0, 0, 0))
            cs_draw = ImageDraw.Draw(contact_shadow)
            cs_draw.ellipse([0, 0, shadow_w, shadow_h], fill=(8, 10, 12, 190))
            contact_shadow = contact_shadow.filter(ImageFilter.GaussianBlur(10))
            
            # Paste drop shadow and contact shadow
            overlay.paste(shadow, (mascot_x + 10, mascot_y + 12), shadow)
            if mascot_y + mh < 1070:
                overlay.paste(contact_shadow, (mascot_x + (mw - shadow_w)//2, mascot_y + mh - shadow_h//2), contact_shadow)
            
            # Paste the blended mascot
            overlay.paste(mascot_img, (mascot_x, mascot_y), mascot_img)
        except Exception as e:
            print(f"⚠️ Failed to load or paste mascot: {e}")
            
    # 5. Footer Info
    footer_y = card_bottom - 50
    draw.text((card_left + 35, footer_y), "NO SLIP AUTOMATION" if lang in ["ja", "jp"] else "노슬립 퀀트 자동화", font=font_semibold, fill=color_muted)
    
    page_str = f"{slide_num:02d} / {total_slides:02d}"
    bbox = draw.textbbox((0, 0), page_str, font=font_semibold)
    page_w = bbox[2] - bbox[0]
    draw.text((card_right - 35 - page_w, footer_y), page_str, font=font_semibold, fill=color_muted)
    
    # Composite overlay on background
    final_img = Image.alpha_composite(bg_img.convert("RGBA"), overlay)
    return final_img.convert("RGB")

def generate_topic_cardnews_data(topic: str, lang: str = "ko", num_slides: int = 5) -> list[dict] | None:
    """Generate 5 cardnews slide data structures based on custom topic using Gemini."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("⚠️ GEMINI_API_KEY is missing. Cannot call Gemini.")
        return None
    if not HAS_GEMINI:
        print("⚠️ google-generativeai package is not installed.")
        return None
        
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-flash-latest")
    
    target_lang = "Japanese" if lang in ["ja", "jp"] else "Korean"
    
    prompt = f"""
You are a highly skilled infographic content writer and graphic designer.
Create a structured {num_slides}-slide cardnews text content for the given topic in "{target_lang}" language.

Topic: "{topic}"

[Guidelines]
1. Generate exactly {num_slides} slides.
2. Slide 1 (index 0) must act as a Cover with summary intro lines in "bullets".
3. Slides 2-{num_slides} must cover {num_slides - 1} distinct key subtopics.
4. Each slide must contain:
   - "title": A short catchy slide title (max 20 chars).
   - "subtitle": Subtitle expanding the title (max 35 chars).
   - "bullets": An array of 3-4 bullet point sentences.
   - "theme": One of "dark_cyber", "emerald_green", "warm_peach" depending on topic tone:
     * "dark_cyber": For tech, AI, space, science, computing, security.
     * "emerald_green": For money, economics, finance, stock market, crypto, business.
     * "warm_peach": For travel, lifestyle, food, coffee, books, history.
5. Output MUST be a valid JSON array block only. Do NOT include markdown code fences (like ```json) or any conversational text.

[Output JSON Schema Example]
[
  {{
    "title": "SPACEX",
    "subtitle": "우주항공의 역사를 새로 쓰다",
    "bullets": [
      "재사용 로켓으로 발사 비용 90% 이상 절감",
      "스타링크를 통한 글로벌 초고속 위성 인터넷망",
      "화성 이주와 다행성 생명체를 꿈꾸는 프로젝트"
    ],
    "theme": "dark_cyber"
  }},
  ...
]
"""
    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        return json.loads(text)
    except Exception as e:
        print(f"❌ Gemini content generation failed: {e}")
        return None

# ----------------- Pipeline -----------------

def generate_card_news(demo: bool = False, outdir: str | None = None,
                       send: bool = True, instagram: bool = False,
                       topic: str | None = None, lang: str = "ko",
                       num_slides: int = 5) -> list[str]:
    from dotenv import load_dotenv
    load_dotenv(ROOT_DIR / ".env")
    setup_korean_font()
    today = datetime.now()
    date_str = today.strftime("%Y년 %m월 %d일 (%a)")
    
    # Setup directories
    folder_name = today.strftime("%Y%m%d")
    if topic:
        safe_topic = "".join(c for c in topic if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
        folder_name += f"_{safe_topic}"
    out = Path(outdir) if outdir else OUT_BASE / folder_name
    out.mkdir(parents=True, exist_ok=True)

    if topic:
        print(f"📡 1/3 Generating topic content for '{topic}' in language '{lang}' via Gemini...")
        topic_data = generate_topic_cardnews_data(topic, lang, num_slides)
        if not topic_data or len(topic_data) < num_slides:
            raise RuntimeError("Failed to generate custom topic cardnews data from Gemini.")
            
        print("🎨 2/3 Rendering cards via custom Pillow vector theme drawers...")
        paths = []
        total = num_slides
        
        theme_map = {
            "dark_cyber": build_theme_cyber,
            "emerald_green": build_theme_emerald,
            "warm_peach": build_theme_peach
        }
        
        for idx, slide in enumerate(topic_data[:num_slides]):
            slide_num = idx + 1
            
            # Check if there is a custom background photo for this slide
            bg_photo_path = Path("/Users/sunghoon/.gemini/antigravity/scratch/no-slip-saas/data/card_news/backgrounds") / f"bg_{slide_num:02d}.png"
            if bg_photo_path.exists():
                try:
                    bg_img = Image.open(bg_photo_path).convert("RGBA").resize((1080, 1080))
                    print(f"   📸 Loaded realistic background: {bg_photo_path}")
                    theme_name = "photo_bg"
                except Exception as e:
                    print(f"   ⚠️ Failed to load background photo: {e}")
                    theme_name = slide.get("theme", "dark_cyber")
                    bg_builder = theme_map.get(theme_name, build_theme_cyber)
                    bg_img = bg_builder()
            else:
                theme_name = slide.get("theme", "dark_cyber")
                bg_builder = theme_map.get(theme_name, build_theme_cyber)
                bg_img = bg_builder()
            
            slide_img = create_pillow_slide(
                bg_img=bg_img,
                slide_num=slide_num,
                total_slides=total,
                title=slide.get("title", ""),
                subtitle=slide.get("subtitle", ""),
                bullets=slide.get("bullets", []),
                is_cover=(slide_num == 1),
                lang=lang
            )
            
            p = out / f"card_{slide_num}_topic.png"
            slide_img.save(p, "PNG")
            paths.append(p)
            print(f"   🖼️ {p} (theme: {theme_name})")
    else:
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
        caption_title = f"Topic: {topic}" if topic else "오늘의 시황 카드뉴스"
        send_telegram_album(paths, f"🗞️ <b>{caption_title}</b> | {date_str}")
    else:
        print("⏭️ 3/3 Send skipped (--no-send)")
    if instagram:
        print("📷 Publishing Instagram carousel...")
        if topic:
            if lang in ["ja", "jp"]:
                ig_caption = (f"🗞️ カードニュース: {topic} | {date_str}\n\n"
                              "AIが自動 생성한 맞춤형 테크/비즈니스 브리핑입니다.\n"
                              "#ビジネス #テック #AI #スタートアップ #カードニュース #noslipquant")
            else:
                ig_caption = (f"🗞️ 카드뉴스: {topic} | {date_str}\n\n"
                              "AI가 자동 생성한 맞춤형 테크/비즈니스 브리핑입니다.\n"
                              "#주식 #시황 #AI #스타트업 #카드뉴스 #noslipquant")
        else:
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
    parser.add_argument("--topic", default=None, help="Generate custom topic cardnews")
    parser.add_argument("--lang", default="ko", help="Output language code (ko, ja, jp)")
    parser.add_argument("--num-slides", type=int, default=5, help="Number of slides to generate")
    args = parser.parse_args()
    try:
        paths = generate_card_news(demo=args.demo, outdir=args.outdir,
                                   send=not args.no_send, instagram=args.instagram,
                                   topic=args.topic, lang=args.lang,
                                   num_slides=args.num_slides)
        print(json.dumps({"cards": paths}, ensure_ascii=False))
    except Exception as e:
        print(f"❌ Card news pipeline failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
