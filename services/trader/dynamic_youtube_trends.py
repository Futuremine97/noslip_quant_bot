#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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

# Set root directory and load environment variables
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv(dotenv_path=ROOT_DIR / ".env")

# Import the existing telegram sending function
from services.trader.whale_pump_monitor import send_telegram_message

# Stock mappings containing aliases and tickers
STOCK_MAPPING = {
    "SK하이닉스": {"ticker": "000660.KS", "aliases": ["sk하이닉스", "하이닉스", "hynix"]},
    "삼성전자": {"ticker": "005930.KS", "aliases": ["삼성전자", "삼성", "samsung"]},
    "엔비디아": {"ticker": "NVDA", "aliases": ["엔비디아", "nvidia", "nvda"]},
    "테슬라": {"ticker": "TSLA", "aliases": ["테슬라", "tesla", "tsla"]},
    "현대차": {"ticker": "005380.KS", "aliases": ["현대차", "현대자동차", "hyundai"]},
    "한미반도체": {"ticker": "042700.KS", "aliases": ["한미반도체", "한미반도"]},
    "알테오젠": {"ticker": "196170.KQ", "aliases": ["알테오젠", "alteogen"]},
    "LS일렉트릭": {"ticker": "010120.KS", "aliases": ["ls일렉트릭", "ls electric", "ls일렉"]},
    "에코프로": {"ticker": "086520.KQ", "aliases": ["에코프로", "ecopro"]},
    "HLB": {"ticker": "028300.KQ", "aliases": ["hlb", "에이치엘비"]},
    "애플": {"ticker": "AAPL", "aliases": ["애플", "apple", "aapl"]},
    "마이크로소프트": {"ticker": "MSFT", "aliases": ["마이크로소프트", "microsoft", "msft"]},
    "비트코인": {"ticker": "BTC-USD", "aliases": ["비트코인", "bitcoin", "btc"]},
    "도지코인": {"ticker": "DOGE-USD", "aliases": ["도지코인", "dogecoin", "doge"]},
    "이더리움": {"ticker": "ETH-USD", "aliases": ["이더리움", "ethereum", "eth"]},
    "네이버": {"ticker": "035420.KS", "aliases": ["네이버", "naver"]},
    "카카오": {"ticker": "035720.KS", "aliases": ["카카오", "kakao"]},
    "삼양식품": {"ticker": "003230.KS", "aliases": ["삼양식품", "삼양라면"]},
    "산일전기": {"ticker": "006840.KS", "aliases": ["산일전기", "산일"]},
    "두산에너빌리티": {"ticker": "034020.KS", "aliases": ["두산에너빌리티", "두산에너"]},
    "인텔": {"ticker": "INTC", "aliases": ["인텔", "intel", "intc"]},
    "디웨이브": {"ticker": "QBTS", "aliases": ["디웨이브", "dwave", "qbts", "d-wave"]},
    "아이온큐": {"ticker": "IONQ", "aliases": ["아이온큐", "ionq"]},
    "델": {"ticker": "DELL", "aliases": ["델", "dell"]}
}

BULLISH_KEYWORDS = [
    "상승", "급등", "돌파", "호재", "강세", "성장", "최고치", "매수", "수혜", "대박", "수주", "실적", "호조", "전망", "목표가 상향", "상향", "폭등", "훈풍", "우위", "최고", "인기", "장밋빛", "독점", "유망", "상승세", "신고가",
    "bullish", "surge", "gain", "rise", "rally", "growth", "high", "upgrade", "optimistic", "outperform", "success", "record", "jump", "positive", "bounce", "beat", "breakout", "strong", "bull"
]
BEARISH_KEYWORDS = [
    "하락", "급락", "폭락", "약세", "우려", "매도", "악재", "부진", "경고", "쇼크", "위기", "소송", "규제", "침체", "감소", "적자", "둔화", "타격", "악화", "손실", "위험", "과열", "거품", "조정", "실패",
    "bearish", "slide", "drop", "fall", "dump", "down", "downgrade", "pessimistic", "underperform", "fail", "regulatory", "crash", "negative", "plunge", "sec", "lawsuit", "decline", "bear"
]

def escape_html(text: str) -> str:
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def fetch_rss_feed(query: str) -> list:
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
            link = item.find("link").text if item.find("link") is not None else ""
            pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
            desc = item.find("description").text if item.find("description") is not None else ""
            items.append({"title": title, "link": link, "pub_date": pub_date, "desc": desc})
        return items
    except Exception as e:
        print(f"⚠️ Error fetching RSS feed for query '{query}': {e}")
        return []

def calculate_sentiment(text: str) -> float:
    text_lower = text.lower()
    pos_count = sum(1 for word in BULLISH_KEYWORDS if word in text_lower)
    neg_count = sum(1 for word in BEARISH_KEYWORDS if word in text_lower)
    
    total = pos_count + neg_count
    if total == 0:
        return 0.0
    return (pos_count - neg_count) / float(total)

def get_yfinance_news_fallback(symbol: str) -> list:
    import yfinance as yf
    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news or []
        fallback_items = []
        for article in news[:5]:
            title = article.get("title", "")
            link = article.get("link", "")
            fallback_items.append({
                "title": title,
                "link": link,
                "pub_date": "",
                "desc": ""
            })
        return fallback_items
    except Exception as e:
        print(f"⚠️ yfinance news fallback failed for {symbol}: {e}")
        return []

def generate_youtube_trends_report(target_keyword: str = None) -> str:
    if target_keyword:
        print(f"🚀 Running Google News/YouTube stock crawler logic for target: {target_keyword}...")
        queries = [
            f"{target_keyword} 주식 유튜브 전망",
            f"{target_keyword} 유튜브 추천"
        ]
        
        all_articles = []
        seen_links = set()
        
        for q in queries:
            items = fetch_rss_feed(q)
            for item in items:
                link = item["link"]
                if link not in seen_links:
                    seen_links.add(link)
                    all_articles.append(item)
                    
        # Fallback to general keyword news if no YouTube-specific results found
        if not all_articles:
            print(f"⚠️ No YouTube specific articles found, querying general news for: '{target_keyword}'")
            items = fetch_rss_feed(f"{target_keyword} 주식")
            for item in items:
                link = item["link"]
                if link not in seen_links:
                    seen_links.add(link)
                    all_articles.append(item)
                    
        if not all_articles:
            return f"⚠️ <b>{target_keyword}</b> 관련 최근 유튜브/구글 뉴스 분석 데이터가 존재하지 않습니다. (검색어를 한글/영어로 변경하여 다시 시도해 보세요.)"
            
        sentiment_scores = []
        evidence = []
        for article in all_articles:
            title = article["title"]
            desc = article["desc"]
            link = article["link"]
            sentiment = calculate_sentiment(title + " " + desc)
            sentiment_scores.append(sentiment)
            evidence.append({"title": title, "link": link})
            
        avg_sentiment = sum(sentiment_scores) / len(sentiment_scores) if sentiment_scores else 0.0
        count = len(all_articles)
        
        if avg_sentiment > 0.1:
            sentiment_disp = f"🟢 긍정 ({avg_sentiment:+.2f})"
        elif avg_sentiment < -0.1:
            sentiment_disp = f"🔴 부정 ({avg_sentiment:+.2f})"
        else:
            sentiment_disp = f"🟡 중립 ({avg_sentiment:+.2f})"
            
        date_str = time.strftime("%Y-%m-%d")
        lines = []
        lines.append(f"📺 <b>[No Slip YouTube] '{target_keyword}' 실시간 분석 ({date_str})</b>")
        lines.append("=" * 40)
        lines.append(f"📊 <b>분석 대상</b>: <code>{target_keyword}</code>")
        lines.append(f"🔥 <b>실시간 관심지수</b>: 관련 소스 {count}건 감지 | <b>소셜 센티먼트</b>: {sentiment_disp}")
        lines.append("=" * 40)
        lines.append("📰 <b>최근 주요 언급 토픽 (헤드라인 링크)</b>:")
        for ev in evidence[:8]:  # Show up to 8 matching headlines
            escaped_title = escape_html(ev["title"])
            lines.append(f"  - <a href=\"{ev['link']}\">\"{escaped_title}\"</a>")
            
        lines.append("\n" + "=" * 40)
        lines.append("※ 본 분석은 실시간 Google News 및 YouTube RSS 검색 데이터를 기반으로 한 소셜 여론 모니터링입니다.")
        return "\n".join(lines)
        
    else:
        # Default General TOP 5 report
        print("🚀 Running Google News/YouTube stock crawler logic for general trends...")
        queries = [
            "주식 전망 유튜브",
            "인기 주식 유튜브",
            "유튜브 주식 추천",
            "주식 유튜버"
        ]
        
        all_articles = []
        seen_links = set()
        
        for q in queries:
            items = fetch_rss_feed(q)
            for item in items:
                link = item["link"]
                if link not in seen_links:
                    seen_links.add(link)
                    all_articles.append(item)
                    
        # Track metrics per stock
        stock_metrics = {}
        for name, info in STOCK_MAPPING.items():
            stock_metrics[name] = {
                "ticker": info["ticker"],
                "aliases": info["aliases"],
                "count": 0,
                "sentiment_scores": [],
                "evidence": []
            }
            
        for article in all_articles:
            title = article["title"]
            desc = article["desc"]
            link = article["link"]
            combined_text = f"{title} {desc}".lower()
            
            for name, metrics in stock_metrics.items():
                matched = False
                for alias in metrics["aliases"]:
                    if alias in combined_text:
                        matched = True
                        break
                
                if matched:
                    metrics["count"] += 1
                    sentiment = calculate_sentiment(title + " " + desc)
                    metrics["sentiment_scores"].append(sentiment)
                    if len(metrics["evidence"]) < 3:
                        metrics["evidence"].append({"title": title, "link": link})
                        
        # Sort stocks by mention frequency count descending
        ranked_stocks = []
        for name, metrics in stock_metrics.items():
            avg_sentiment = sum(metrics["sentiment_scores"]) / len(metrics["sentiment_scores"]) if metrics["sentiment_scores"] else 0.0
            ranked_stocks.append({
                "name": name,
                "ticker": metrics["ticker"],
                "count": metrics["count"],
                "sentiment": avg_sentiment,
                "evidence": metrics["evidence"]
            })
            
        ranked_stocks.sort(key=lambda x: (-x["count"], -abs(x["sentiment"])))
        trending_list = [s for s in ranked_stocks if s["count"] > 0]
        
        fallback_candidates = [
            "엔비디아", "SK하이닉스", "삼성전자", "테슬라", "애플", "마이크로소프트", "비트코인"
        ]
        
        if len(trending_list) < 5:
            already_added = {s["name"] for s in trending_list}
            for cand in fallback_candidates:
                if len(trending_list) >= 5:
                    break
                if cand in already_added:
                    continue
                info = STOCK_MAPPING.get(cand)
                if not info:
                    continue
                fb_articles = get_yfinance_news_fallback(info["ticker"])
                fb_count = len(fb_articles)
                fb_sentiments = []
                fb_evidence = []
                for art in fb_articles:
                    fb_sentiments.append(calculate_sentiment(art["title"]))
                    if len(fb_evidence) < 3:
                        fb_evidence.append({"title": art["title"], "link": art["link"]})
                avg_sentiment = sum(fb_sentiments) / len(fb_sentiments) if fb_sentiments else 0.0
                trending_list.append({
                    "name": cand,
                    "ticker": info["ticker"],
                    "count": fb_count,
                    "sentiment": avg_sentiment,
                    "evidence": fb_evidence
                })
                already_added.add(cand)
                
        top_5 = trending_list[:5]
        
        date_str = time.strftime("%Y-%m-%d")
        lines = []
        lines.append(f"📺 <b>[No Slip YouTube] 주식 유튜버 인기/관심 종목 TOP 5 ({date_str})</b>")
        lines.append("=" * 40)
        lines.append("삼프로TV, 소수몽키, 슈카월드 등 금융 유튜브 및 온라인 채널 실시간 검색 키워드 분석 리포트입니다.\n")
        
        for i, stock in enumerate(top_5, 1):
            name = stock["name"]
            ticker = stock["ticker"]
            sentiment = stock["sentiment"]
            count = stock["count"]
            evidence = stock["evidence"]
            
            if sentiment > 0.1:
                sentiment_disp = f"🟢 긍정 ({sentiment:+.2f})"
            elif sentiment < -0.1:
                sentiment_disp = f"🔴 부정 ({sentiment:+.2f})"
            else:
                sentiment_disp = f"🟡 중립 ({sentiment:+.2f})"
                
            lines.append(f"🔥 <b>0{i}. {name} ({ticker})</b>")
            lines.append(f"  • <b>실시간 관심지수</b>: 언급 {count}회 | <b>소셜 센티먼트</b>: {sentiment_disp}")
            if evidence:
                lines.append("  • <b>최근 주요 언급 토픽 (링크)</b>:")
                for ev in evidence:
                    escaped_title = escape_html(ev["title"])
                    lines.append(f"    - <a href=\"{ev['link']}\">\"{escaped_title}\"</a>")
            else:
                lines.append("  • <b>최근 주요 언급 토픽 (링크)</b>: N/A")
            lines.append("")
            
        lines.append("=" * 40)
        lines.append("※ 본 리포트는 실시간 Google News 및 YouTube RSS 분석 데이터를 융합하여 구성되었습니다. 투자 결정의 보조 지표로 활용하세요.")
        return "\n".join(lines)

def crawl_and_broadcast():
    print("🚀 Starting Dynamic YouTube/Google Trends Crawler...")
    report_msg = generate_youtube_trends_report()
    success = send_telegram_message(report_msg)
    if success:
        print("✅ Telegram broadcast completed successfully!")
    else:
        print("❌ Telegram broadcast failed!")

if __name__ == "__main__":
    crawl_and_broadcast()
