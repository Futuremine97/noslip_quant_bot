#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Oh-seon YouTube Daily Market Summary Service.
Crawls the YouTube RSS feed of channel '오선의 미국 증시 라이브' (UC_JJ_NhRqPKcIOj5Ko3W_3w),
retrieves live market context matching the video topics from Google News RSS,
uses the Gemini API to synthesize a structured Korean daily closing summary,
and broadcasts the final HTML report to allowed Telegram channels.
"""

from __future__ import annotations
import os
import sys
import json
import re
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv(dotenv_path=ROOT_DIR / ".env")

try:
    import google.generativeai as genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

CHANNEL_ID = "UC_JJ_NhRqPKcIOj5Ko3W_3w"
YOUTUBE_RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"


def escape_html(text: str) -> str:
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def sanitize_telegram_html(text: str) -> str:
    """
    Strips invalid HTML tags from the LLM output (like <br>) and escapes any naked '<', '>', or '&'
    while keeping Telegram-supported tags (<b>, <i>, <code>, <pre>, <a>).
    """
    if not text:
        return ""
        
    # Replace br tags with simple newlines
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    
    # Track tags we want to keep
    placeholders = []
    
    # 1. Match and protect <a href="..."> tags
    a_pattern = r'<a\s+href="[^"]*">'
    a_tags = re.findall(a_pattern, text)
    temp_text = text
    for idx, tag in enumerate(a_tags):
        placeholder = f"__A_TAG_{idx}__"
        placeholders.append((placeholder, tag))
        temp_text = temp_text.replace(tag, placeholder)
        
    # 2. Match and protect other supported tags
    standard_tags = ['</a>', '<b>', '</b>', '<i>', '</i>', '<code>', '</code>', '<pre>', '</pre>']
    for idx, tag in enumerate(standard_tags):
        placeholder = f"__TAG_{idx}__"
        placeholders.append((placeholder, tag))
        temp_text = temp_text.replace(tag, placeholder)
        
    # 3. Escape all remaining raw HTML special chars
    temp_text = temp_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    
    # 4. Restore protected tags
    for placeholder, tag in placeholders:
        temp_text = temp_text.replace(placeholder, tag)
        
    return temp_text


def fetch_youtube_feed() -> list:
    """Fetches and parses the latest videos from Oh-seon's YouTube channel RSS feed."""
    req = urllib.request.Request(
        YOUTUBE_RSS_URL,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            xml_data = response.read()
        root = ET.fromstring(xml_data)
        
        ns = {
            'atom': 'http://www.w3.org/2005/Atom',
            'yt': 'http://www.youtube.com/xml/schemas/2015',
            'media': 'http://search.yahoo.com/mrss/'
        }
        
        entries = root.findall('atom:entry', ns)
        videos = []
        for entry in entries:
            title = entry.find('atom:title', ns).text if entry.find('atom:title', ns) is not None else ""
            link = entry.find('atom:link', ns).attrib['href'] if entry.find('atom:link', ns) is not None else ""
            published = entry.find('atom:published', ns).text if entry.find('atom:published', ns) is not None else ""
            
            group = entry.find('media:group', ns)
            desc = ""
            if group is not None:
                description_el = group.find('media:description', ns)
                if description_el is not None:
                    desc = description_el.text or ""
            
            videos.append({
                "title": title,
                "link": link,
                "published": published,
                "description": desc
            })
        return videos
    except Exception as e:
        print(f"⚠️ Error fetching YouTube RSS: {e}")
        return []


def fetch_google_news_rss(query: str) -> list:
    """Fetches articles from Google News RSS feed for a specific query."""
    encoded_query = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            xml_data = response.read()
        root = ET.fromstring(xml_data)
        
        items = []
        for item in root.findall(".//item"):
            title = item.find("title").text if item.find("title") is not None else ""
            desc = item.find("description").text if item.find("description") is not None else ""
            items.append({"title": title, "desc": desc})
        return items
    except Exception as e:
        print(f"⚠️ Error fetching Google News RSS for query '{query}': {e}")
        return []


def clean_title_for_keywords(title: str) -> list:
    """Cleans standard YouTube prefixes/dates and extracts key keywords for news search."""
    cleaned = title
    # Remove standard brackets and prefixes
    cleaned = re.sub(r"【.*?】", "", cleaned)
    cleaned = re.sub(r"\[.*?\]", "", cleaned)
    cleaned = cleaned.replace("- 오선의 미국 증시 라이브", "")
    cleaned = cleaned.replace("- 오선의", "")
    cleaned = re.sub(r"\d{4}/\d{2}/\d{2}", "", cleaned) # Remove date formatting
    cleaned = cleaned.replace("｜", " ").replace("|", " ").replace("..", " ")
    
    # Extract words longer than 2 characters
    words = re.findall(r"[가-힣a-zA-Z0-9]{2,}", cleaned)
    
    # Filter out common stop words
    stop_words = {"오늘의", "오늘", "요약", "라이브", "미국", "증시", "주가", "흐름", "현황", "마감", "상황"}
    keywords = [w for w in words if w.lower() not in stop_words]
    return list(dict.fromkeys(keywords)) # Remove duplicates preserving order


def gather_market_news_context(keywords: list) -> str:
    """Aggregates market closing recaps and keyword-specific news details."""
    news_lines = []
    seen_headlines = set()
    
    # 1. Fetch general daily market closing stats
    print("📰 Fetching general US market closing recaps...")
    recap_articles = fetch_google_news_rss("미국 증시 시황 마감 요약")
    for art in recap_articles[:6]:
        headline = art["title"]
        if headline not in seen_headlines:
            seen_headlines.add(headline)
            news_lines.append(f"- {headline}")
            
    # 2. Fetch specific news for parsed keywords
    print(f"🔍 Fetching news for key entities: {keywords[:5]}")
    for kw in keywords[:4]:
        kw_articles = fetch_google_news_rss(f"{kw} 미국 증시")
        for art in kw_articles[:3]:
            headline = art["title"]
            if headline not in seen_headlines:
                seen_headlines.add(headline)
                news_lines.append(f"- [{kw}] {headline}")
                
    return "\n".join(news_lines)


def generate_ohseon_summary_with_gemini(
    latest_videos: list, 
    news_context: str
) -> str:
    """Generates the structured market closing briefing using the Gemini API."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "❌ <b>오선 시황 요약 실패</b>: <code>GEMINI_API_KEY</code>가 설정되지 않았습니다."
        
    if not HAS_GEMINI:
        return "❌ <b>오선 시황 요약 실패</b>: <code>google-generativeai</code> 패키지가 설치되지 않았습니다."
        
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-flash-latest")
    
    # Prepare video details for prompt
    video_details = []
    for idx, v in enumerate(latest_videos[:3], start=1):
        video_details.append(
            f"영상 {idx}:\n"
            f"- 제목: {v['title']}\n"
            f"- 링크: {v['link']}\n"
            f"- 작성일: {v['published']}\n"
        )
    videos_text = "\n".join(video_details)
    
    prompt = f"""
당신은 인기 유튜브 경제 채널 '오선의 미국 증시 라이브'의 내용을 AI 신경망(Neural Net)으로 요약·정리하는 전문 금융 작가이자 퀀트 분석가입니다.
제공된 오늘자 오선 유튜브 방송 제목/정보와 당일 미국 증시 관련 최신 뉴스 컨텍스트를 바탕으로, 시청자들이 오늘 하루 시황을 명확하고 깔끔하게 파악할 수 있도록 '하루 시황 정리 리포트'를 작성해 주세요.

[오선 유튜브 최근 영상 정보]
{videos_text}

[당일 미국 증시 최신 뉴스 컨텍스트]
{news_context}

[작성 가이드라인]
1. **객관성과 팩트 중심**: 오선 채널의 방송 스타일대로 개인적인 예측이나 추천은 배제하고, 외신(CNBC, 블룸버그, 로이터 등)과 경제 지표 결과를 바탕으로 팩트 위주로 차분하고 신뢰성 있게 서술하세요.
2. **구조화된 요약**:
   - 📈 **주요 3대 지수 마감 현황** (S&P 500, 나스닥, 다우 - 최신 뉴스 컨텍스트의 마감 변동률 데이터 반영)
   - 🌍 **거시 경제 & 주요 지표 (Macro)** (베이지북, PMI, 국채 금리, 고용 지표, 연준 발언 등 오늘 방송/뉴스에 언급된 매크로 동향 요약)
   - 🚀 **주요 기업 & 섹터 뉴스 (Corporate/Sector)** (엔비디아, 메타, 테슬라 등 오늘 방송 제목 및 뉴스에 등장한 개별 주식들의 핫이슈 정리)
   - 🛡️ **오늘의 한 줄 요약 & 투자 관점** (전체 시황의 핵심 흐름을 짚어주는 객관적인 마감 요약)
3. **어조**: 차분하고 예의 바른 격식 있는 존댓말(~입니다, ~했습니다)을 사용하세요.
4. **링크 통합**: 오선 유튜브의 가장 최신 영상(영상 1)의 제목을 맨 위에 노출하고, 해당 영상의 유튜브 링크를 하이퍼링크로 포함시켜 주세요.
   - 예: 📺 <b><a href="영상1링크">오선의 미국 증시 라이브 바로가기</a></b>
5. **형식**: 텔레그램 메시지용 HTML 마크업 태그(<b>, <i>, <code>, <a> 등)를 사용하여 깔끔하고 프리미엄한 가독성을 제공하세요. (다른 마크다운 기호 `*`, `**` 등은 텔레그램에서 깨지므로 사용하지 말고 HTML 태그만 쓰세요.)
"""
    
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"❌ Gemini Generation failed: {e}")
        return f"❌ <b>오선 시황 요약 생성 실패</b>: Gemini API 호출 중 오류가 발생했습니다 ({e})."


def send_telegram_message(text: str) -> bool:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_ids_str = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_ids_str:
        print("⚠️ Telegram settings missing (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")
        return False
        
    chat_ids = [cid.strip() for cid in chat_ids_str.split(",") if cid.strip()]
    success = True
    for cid in chat_ids:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": cid,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                resp.read()
                print(f"✅ [Telegram] Message successfully sent to chat {cid}")
        except Exception as e:
            print(f"❌ [Telegram] Failed to send to chat {cid}: {e}")
            success = False
    return success


def run_ohseon_summary_pipeline() -> str:
    print("🚀 Fetching Oh-seon YouTube RSS feed...")
    videos = fetch_youtube_feed()
    if not videos:
        return "⚠️ 오선의 유튜브 채널 feed를 가져오지 못했습니다."
        
    # Extract keywords from the 2 latest videos
    keywords = []
    for v in videos[:2]:
        keywords.extend(clean_title_for_keywords(v["title"]))
    keywords = list(dict.fromkeys(keywords)) # Deduplicate
    
    # Gather live context
    news_context = gather_market_news_context(keywords)
    
    # Generate summary with Gemini
    print("🤖 Synthesizing daily summary report with Gemini...")
    report = generate_ohseon_summary_with_gemini(videos, news_context)
    return sanitize_telegram_html(report)


def main():
    report = run_ohseon_summary_pipeline()
    print("\n--- Generated Oh-seon Summary ---")
    print(report)
    print("---------------------------------\n")
    
    if "failed" not in report.lower() and "failed" not in report.lower():
        send_telegram_message(report)


if __name__ == "__main__":
    main()
