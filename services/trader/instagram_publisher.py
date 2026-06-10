#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Instagram Daily Post Automation Script for @noslip.ai
Generates the S&P 500 Information Map plot, format top recommendations,
uploads the plot to tmpfiles.org, and posts it to Instagram.
"""

import os
import sys
import json
import time
import argparse
import requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Set up project root path
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Load environment variables
load_dotenv(dotenv_path=ROOT_DIR / ".env")


def generate_infomap_plot(latest_json_path: Path, output_path: Path) -> dict:
    """
    Renders a premium dark-themed S&P 500 4-quadrant scatter plot using Matplotlib.
    Matches the design aesthetics of the telegram interactive bot.
    """
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    if not latest_json_path.exists():
        raise FileNotFoundError(f"latest.json not found at {latest_json_path}")
        
    with open(latest_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    points = data.get("points", [])
    if not points:
        raise ValueError("No points found in latest.json")
        
    symbols = []
    names = []
    xs = []
    ys = []
    quadrants = []
    
    for p in points:
        sym = p.get("symbol")
        name = p.get("name", sym)
        coords = p.get("firstCoordinateSpace")
        if not coords or coords.get("x") is None or coords.get("y") is None:
            continue
        symbols.append(sym)
        names.append(name)
        xs.append(float(coords["x"]))
        ys.append(float(coords["y"]))
        quadrants.append(p.get("quadrant", "unknown"))
        
    if not xs:
        raise ValueError("No valid coordinates found in points")
        
    quadrant_counts = {
        "breakout acceleration": 0,
        "uptrend cooling": 0,
        "recovery setup": 0,
        "selloff acceleration": 0,
        "unknown": 0
    }
    
    for q in quadrants:
        if q in quadrant_counts:
            quadrant_counts[q] += 1
        else:
            quadrant_counts["unknown"] += 1
            
    fig, ax = plt.subplots(figsize=(10, 8), dpi=150)
    fig.patch.set_facecolor("#121212")
    ax.set_facecolor("#1a1a1a")
    
    colors_map = {
        "breakout acceleration": "#00f5d4",
        "uptrend cooling": "#f59e0b",
        "recovery setup": "#d946ef",
        "selloff acceleration": "#ef4444",
        "unknown": "#888888"
    }
    point_colors = [colors_map.get(q, "#888888") for q in quadrants]
    
    ax.scatter(xs, ys, color=point_colors, s=120, alpha=0.15, edgecolors='none', zorder=2)
    ax.scatter(xs, ys, color=point_colors, s=35, alpha=0.9, edgecolors='#ffffff', linewidths=0.5, zorder=3)
    
    ax.set_title("S&P 500 Information Map", fontsize=16, fontweight="bold", color="#ffffff", pad=15)
    ax.set_xlabel("Momentum / Expected Return (1st Coordinate X)", fontsize=11, color="#aaaaaa", labelpad=10)
    ax.set_ylabel("Volatility / Risk (1st Coordinate Y)", fontsize=11, color="#aaaaaa", labelpad=10)
    
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    x_margin = max(0.1, (x_max - x_min) * 0.15)
    y_margin = max(0.5, (y_max - y_min) * 0.15)
    
    xlim_min = min(x_min - x_margin, -0.05)
    xlim_max = max(x_max + x_margin, 0.05)
    ylim_min = min(y_min - y_margin, -0.5)
    ylim_max = max(y_max + y_margin, 0.5)
    
    ax.set_xlim(xlim_min, xlim_max)
    ax.set_ylim(ylim_min, ylim_max)
    
    ax.axhline(0, color="#444444", linewidth=1.2, linestyle="--", alpha=0.7, zorder=1)
    ax.axvline(0, color="#444444", linewidth=1.2, linestyle="--", alpha=0.7, zorder=1)
    
    ax.grid(True, which="both", color="#2a2a2a", linestyle=":", linewidth=0.5, zorder=0)
    
    ax.tick_params(colors="#aaaaaa", labelsize=9)
    for spine in ax.spines.values():
        spine.set_color("#333333")
        
    annotated_count = 0
    annotated_symbols = set()
    major_targets = {"AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "NFLX", "AMD", "AVGO"}
    
    dists = np.sqrt(np.array(xs)**2 + np.array(ys)**2)
    sorted_indices = np.argsort(dists)[::-1]
    
    for idx in range(len(xs)):
        sym = symbols[idx]
        should_annotate = False
        if len(xs) <= 15:
            should_annotate = True
        else:
            if sym in major_targets:
                should_annotate = True
            elif idx in sorted_indices[:8] and annotated_count < 15:
                should_annotate = True
                
        if should_annotate:
            annotated_count += 1
            annotated_symbols.add(sym)
            ax.annotate(
                sym,
                (xs[idx], ys[idx]),
                textcoords="offset points",
                xytext=(0, 6),
                ha="center",
                va="bottom",
                fontsize=8,
                fontweight="bold",
                color="#ffffff",
                bbox=dict(boxstyle="round,pad=0.2", fc="#262626", ec="none", alpha=0.75),
                zorder=4
            )
            
    bbox_props = dict(boxstyle="round,pad=0.3", fc="#1a1a1a", ec="#333333", alpha=0.85)
    ax.text(xlim_max - (xlim_max * 0.05), ylim_max - (ylim_max * 0.08), "Breakout Acceleration", color="#00f5d4", fontsize=9, fontweight="bold", ha="right", va="top", bbox=bbox_props)
    ax.text(xlim_max - (xlim_max * 0.05), ylim_min + (abs(ylim_min) * 0.08), "Uptrend Cooling", color="#f59e0b", fontsize=9, fontweight="bold", ha="right", va="bottom", bbox=bbox_props)
    ax.text(xlim_min + (abs(xlim_min) * 0.05), ylim_max - (ylim_max * 0.08), "Recovery Setup", color="#d946ef", fontsize=9, fontweight="bold", ha="left", va="top", bbox=bbox_props)
    ax.text(xlim_min + (abs(xlim_min) * 0.05), ylim_min + (abs(ylim_min) * 0.08), "Selloff Acceleration", color="#ef4444", fontsize=9, fontweight="bold", ha="left", va="bottom", bbox=bbox_props)
    
    legend_elements = [
        Line2D([0], [0], marker='o', color='w', markerfacecolor='#00f5d4', markersize=8, label='Breakout Accel'),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='#d946ef', markersize=8, label='Recovery Setup'),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='#f59e0b', markersize=8, label='Uptrend Cooling'),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='#ef4444', markersize=8, label='Selloff Accel')
    ]
    ax.legend(handles=legend_elements, loc='upper center', bbox_to_anchor=(0.5, -0.1), ncol=4, frameon=True, facecolor='#1a1a1a', edgecolor='#333333', labelcolor='#ffffff', fontsize=8)
    
    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, facecolor=fig.get_facecolor(), edgecolor='none', bbox_inches='tight')
    plt.close(fig)
    
    return {
        "mapDate": data.get("mapDate", datetime.today().strftime("%Y-%m-%d")),
        "total_symbols": len(symbols),
        "quadrant_counts": quadrant_counts
    }


def format_instagram_caption(map_data: dict) -> str:
    """Formats the top 10 stocks and market information into a clean plain text caption for Instagram."""
    date_str = map_data.get("mapDate", datetime.today().strftime("%Y-%m-%d"))
    reinforcement = map_data.get("reinforcement", {})
    investor_lens = reinforcement.get("investorLensSnapshot", {})
    leader = investor_lens.get("leader", "buffett").upper()
    
    # Generate daily & monthly strategy using Prophet trend slope stats
    points = map_data.get("points", [])
    if not points:
        points = map_data.get("topPicks", [])
        
    valid_trends = [float(p["prophetTrend"]) for p in points if p.get("prophetTrend") is not None]
    valid_slopes = [float(p["prophetTrendSlope"]) for p in points if p.get("prophetTrendSlope") is not None]
    valid_weeklies = [float(p["prophetWeekly"]) for p in points if p.get("prophetWeekly") is not None]
    valid_monthlies = [float(p["prophetMonthly"]) for p in points if p.get("prophetMonthly") is not None]
    
    avg_slope = sum(valid_slopes) / len(valid_slopes) if valid_slopes else 0.0
    avg_weekly = sum(valid_weeklies) / len(valid_weeklies) if valid_weeklies else 0.0
    avg_monthly = sum(valid_monthlies) / len(valid_monthlies) if valid_monthlies else 0.0
    
    if avg_slope > 0.02 and avg_weekly > 0.01:
        daily_title = "강세 추세 추종 (Aggressive Trend Following)"
        daily_desc = "시장 전체의 Prophet 일별 트렌드가 상승 가속하고 있습니다. 돌파 시 추격 매수가 유리하며 이익 보존을 위한 트레일링 스탑을 설정합니다."
    elif avg_slope > 0.02:
        daily_title = "지속 매수 및 보유 (Hold & Accumulate)"
        daily_desc = "안정적인 우상향 트렌드로 변동성이 낮습니다. 우량주 분할 매수 관점으로 매매 비중을 안정적으로 유지합니다."
    elif avg_slope <= 0.02 and avg_weekly > 0.01:
        daily_title = "단기 스윙 매매 (Short-term Mean Reversion)"
        daily_desc = "횡보 국면이나 주간 변동성이 높습니다. 지지선 매입, 저항선 청산 전략의 짧은 단기 스윙 전략이 유리합니다."
    elif avg_slope <= -0.01:
        daily_title = "방어적 리스크 관리 (Risk-Off Defensive)"
        daily_desc = "일별 트렌드가 하락/약세 국면입니다. 신규 매수를 일시 중단하고 이익 실현 및 현금 비중 확대를 권고합니다."
    else:
        daily_title = "중립 관망 및 선택적 가치 매입"
        daily_desc = "시장 트렌드가 중립 횡보 상태입니다. 무리한 지수 추종보다는 개별 종목 중 하단 지지력이 확보된 우량주 위주로 대응합니다."

    if avg_monthly > 0.02:
        monthly_title = "적극적 주식 비중 확대 (Risk-On Aggressive)"
    elif avg_monthly < -0.01:
        monthly_title = "안전자산 확대 및 포트폴리오 헤징 (Risk-Off Defense)"
    else:
        monthly_title = "균형 리밸런싱 포트폴리오 (Dynamic Balanced)"

    lines = []
    lines.append(f"📊 [No Slip] S&P500 시황 & AI 탑 10 추천 ({date_str})")
    lines.append("=" * 30)
    lines.append("")
    lines.append("🤖 오늘의 AI 리더: " + leader)
    lines.append(f"📈 일일 최적 매매 전략 (Daily): {daily_title}")
    lines.append(f"   └ {daily_desc}")
    lines.append(f"📅 월별 최적 자산배분 (Monthly): {monthly_title}")
    lines.append("")
    
    top_picks = map_data.get("topPicks", [])
    if top_picks:
        lines.append("🔍 S&P500 AI 추천 Top 10 종목:")
        emoji_numbers = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        for i, item in enumerate(top_picks[:10]):
            symbol = item.get("symbol")
            name = item.get("name", "N/A")[:15]
            upside = float(item.get("maxUpsidePct") or 0) * 100
            score = float(item.get("optimizationScore") or 0)
            cur_price = float(item.get("currentPrice") or 0)
            buy_price = float(item.get("optimalBuyPrice") or 0)
            sell_price = float(item.get("optimalSellPrice") or 0)
            max_dd = float(item.get("maxDrawdownPct") or 0) * 100
            
            num = emoji_numbers[i] if i < len(emoji_numbers) else f"{i+1}."
            lines.append(f"{num} {symbol} ({name}) | 점수: {score:.2f}")
            lines.append(f"   • 현재: ${cur_price:.2f} (상승 여력: +{upside:.1f}% / 낙폭: {max_dd:.1f}%)")
            lines.append(f"   • 추천 매수: ${buy_price:.2f} | 목표 매도: ${sell_price:.2f}")
            
    lines.append("")
    lines.append("=" * 30)
    lines.append("💡 4분면 정보 맵 시각화 차트를 통해 S&P500 종목들의 모멘텀과 변동성 분포를 한눈에 확인해 보세요! (밀어서 보기 👉)")
    lines.append("")
    lines.append("#미국주식 #주식투자 #SP500 #인공지능투자 #퀀트투자 #주식추천 #주식포트폴리오 #재테크 #금융공학 #noslip #에이아이퀀트")
    
    return "\n".join(lines)


def upload_image_to_tmpfiles(image_path: Path) -> str:
    """Uploads a local image to tmpfiles.org and returns the direct raw link."""
    url = "https://tmpfiles.org/api/v1/upload"
    try:
        with open(image_path, "rb") as f:
            files = {"file": f}
            response = requests.post(url, files=files, timeout=30)
        
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "success":
            upload_url = data["data"]["url"]
            # Convert public URL to raw download link
            direct_url = upload_url.replace("https://tmpfiles.org/", "https://tmpfiles.org/dl/")
            print(f"✅ Image uploaded to: {direct_url}")
            return direct_url
        else:
            raise Exception(f"Upload failed with status: {data.get('status')}")
    except Exception as e:
        print(f"❌ Failed to upload image to tmpfiles.org: {e}")
        raise


def wait_for_container(container_id: str, access_token: str, max_retries: int = 15, delay: int = 5):
    """Polls the status of an Instagram media container until it is FINISHED or errors out."""
    base_url = "https://graph.facebook.com/v19.0"
    url = f"{base_url}/{container_id}"
    params = {
        "fields": "status_code",
        "access_token": access_token
    }
    
    for i in range(max_retries):
        try:
            res = requests.get(url, params=params, timeout=15)
            res.raise_for_status()
            status = res.json().get("status_code")
            print(f"Container status: {status} (attempt {i+1}/{max_retries})")
            if status == "FINISHED":
                return
            elif status == "ERROR":
                raise Exception(f"Container creation failed: {res.json()}")
        except Exception as e:
            print(f"⚠️ Error checking container status: {e}")
            
        time.sleep(delay)
    
    raise TimeoutError("Timed out waiting for Instagram media container to finish processing.")


def publish_to_instagram(business_id: str, access_token: str, image_urls: list, caption: str) -> str:
    """
    Creates and publishes an Instagram post (single image or carousel) using the Graph API.
    """
    base_url = "https://graph.facebook.com/v19.0"
    
    if len(image_urls) == 1:
        # Single Image post
        print("Creating single image container...")
        url = f"{base_url}/{business_id}/media"
        payload = {
            "image_url": image_urls[0],
            "caption": caption,
            "access_token": access_token
        }
        res = requests.post(url, json=payload, timeout=30)
        res.raise_for_status()
        container_id = res.json()["id"]
        
        # Poll container status
        wait_for_container(container_id, access_token)
        
        # Publish
        print("Publishing post...")
        pub_url = f"{base_url}/{business_id}/media_publish"
        pub_payload = {
            "creation_id": container_id,
            "access_token": access_token
        }
        pub_res = requests.post(pub_url, json=pub_payload, timeout=30)
        pub_res.raise_for_status()
        return pub_res.json()["id"]
    else:
        # Carousel post
        print(f"Creating carousel with {len(image_urls)} items...")
        child_ids = []
        for idx, img_url in enumerate(image_urls):
            print(f"Creating item container {idx+1}/{len(image_urls)}...")
            url = f"{base_url}/{business_id}/media"
            payload = {
                "image_url": img_url,
                "is_carousel_item": True,
                "access_token": access_token
            }
            res = requests.post(url, json=payload, timeout=30)
            res.raise_for_status()
            child_ids.append(res.json()["id"])
        
        # Poll all children to be ready
        for idx, child_id in enumerate(child_ids):
            print(f"Checking item container {idx+1} status...")
            wait_for_container(child_id, access_token)
            
        # Create carousel container
        print("Creating main carousel container...")
        url = f"{base_url}/{business_id}/media"
        payload = {
            "media_type": "CAROUSEL",
            "caption": caption,
            "children": child_ids,
            "access_token": access_token
        }
        res = requests.post(url, json=payload, timeout=30)
        res.raise_for_status()
        carousel_id = res.json()["id"]
        
        # Poll carousel container status
        wait_for_container(carousel_id, access_token)
        
        # Publish carousel
        print("Publishing carousel post...")
        pub_url = f"{base_url}/{business_id}/media_publish"
        pub_payload = {
            "creation_id": carousel_id,
            "access_token": access_token
        }
        pub_res = requests.post(pub_url, json=pub_payload, timeout=30)
        pub_res.raise_for_status()
        return pub_res.json()["id"]


def main():
    parser = argparse.ArgumentParser(description="Upload daily visualization and recommendations to Instagram.")
    parser.add_argument("--dry-run", action="store_true", help="Format caption and render image locally without posting.")
    args = parser.parse_args()

    print("=== Instagram Automation Pipeline Started ===")
    
    # 1. Paths configuration
    latest_json_path = ROOT_DIR / "services" / "trader" / "model_cache" / "sp500_information_maps" / "latest.json"
    sp500_plot_path = ROOT_DIR / "data" / "sp500_infomap.png"
    sector_plot_path = ROOT_DIR / "data" / "sector_orbits.png"
    
    # 2. Render plot
    print("Generating S&P 500 Information Map Plot...")
    try:
        stats = generate_infomap_plot(latest_json_path, sp500_plot_path)
        print(f"Plot saved to {sp500_plot_path}. Total symbols: {stats['total_symbols']}.")
    except Exception as e:
        print(f"❌ Error generating S&P500 Infomap plot: {e}")
        sys.exit(1)
        
    # 3. Load latest map data for caption formatting
    with open(latest_json_path, "r", encoding="utf-8") as f:
        map_data = json.load(f)
        
    caption = format_instagram_caption(map_data)
    
    # 4. Dry-run Mode
    if args.dry_run:
        print("\n--- [DRY RUN] Caption Preview ---")
        print(caption)
        print("---------------------------------")
        print(f"Dry run completed. Image rendered to: {sp500_plot_path}")
        if sector_plot_path.exists():
            print(f"Sector orbit image also detected at: {sector_plot_path}")
        sys.exit(0)
        
    # 5. Production execution - Check credentials
    business_id = os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID")
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    
    if not business_id or not access_token:
        print("⚠️ INSTAGRAM_BUSINESS_ACCOUNT_ID or INSTAGRAM_ACCESS_TOKEN is missing in `.env`.")
        print("⚠️ Instagram posting skipped. Set credentials in `.env` to enable posting.")
        sys.exit(0)
        
    # 6. Upload files
    print("Uploading images to tmpfiles.org...")
    image_paths = [sp500_plot_path]
    
    # If sector_orbits.png exists and was updated within the last day, include it in carousel
    if sector_plot_path.exists():
        mtime = os.path.getmtime(sector_plot_path)
        if time.time() - mtime < 86400:  # 24 hours
            image_paths.append(sector_plot_path)
            
    uploaded_urls = []
    for img_path in image_paths:
        try:
            uploaded_urls.append(upload_image_to_tmpfiles(img_path))
        except Exception as e:
            print(f"❌ Stopping pipeline due to image upload failure: {e}")
            sys.exit(1)
            
    # 7. Post to Instagram
    try:
        print("Initiating Instagram posting...")
        post_id = publish_to_instagram(business_id, access_token, uploaded_urls, caption)
        print(f"🎉 Success! Published Instagram post. ID: {post_id}")
    except Exception as e:
        print(f"❌ Failed to publish to Instagram: {e}")
        sys.exit(1)

    print("=== Instagram Automation Pipeline Completed ===")


if __name__ == "__main__":
    main()
