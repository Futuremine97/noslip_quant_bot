#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import sys
import smtplib
import sqlite3
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# .env 로드
load_dotenv(dotenv_path=ROOT_DIR / ".env")

DB_PATH = ROOT_DIR / "services" / "trader" / "model_cache" / "whale_rewards.sqlite3"

# S&P500 Quant virtual account settings
START_CAPITAL = 100000.0   # $100,000 USD starting balance
ALLOCATION_PER_TRADE = 10000.0  # $10,000 USD per stock trade

def init_sp500_db():
    """Initialize SQLite database for S&P500 paper trading."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sp500_trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                entry_time INTEGER NOT NULL, -- Unix timestamp in seconds
                entry_price REAL NOT NULL,
                target_sell_price REAL NOT NULL,
                target_sell_date TEXT, -- YYYY-MM-DD
                status TEXT NOT NULL DEFAULT 'PENDING',
                exit_price REAL,
                exit_time INTEGER,
                realized_return REAL,
                buy_reason TEXT,
                exit_reason TEXT,
                created_at TEXT NOT NULL
            )
        """)
        # Add new Prophet columns if they do not exist
        for col, col_type in [
            ("prophet_trend", "REAL"),
            ("prophet_trend_slope", "REAL"),
            ("prophet_weekly", "REAL"),
            ("prophet_monthly", "REAL")
        ]:
            try:
                conn.execute(f"ALTER TABLE sp500_trade_log ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass
        conn.commit()

def fetch_live_price(symbol: str) -> float:
    """Fetch live price for S&P500 stock using yfinance."""
    normalized_sym = symbol.strip().upper().replace(".", "-")
    try:
        import yfinance as yf
        ticker = yf.Ticker(normalized_sym)
        price = getattr(ticker, "fast_info", {}).get("lastPrice")
        if price is None:
            hist = ticker.history(period="1d")
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])
        if price is not None:
            return float(price)
    except Exception as e:
        print(f"⚠️ Failed to fetch price for {symbol} via yfinance: {e}")
    return 0.0

def process_sp500_quant_trades(map_data: dict):
    """Resolve active trades and enter new top picks based on S&P500 information map."""
    print("⚙️ Processing S&P500 Quant Trader Agent trades...")
    init_sp500_db()
    
    today_str = datetime.today().strftime("%Y-%m-%d")
    now_ts = int(time.time())
    
    # 1. Resolve pending trades
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        pending = conn.execute("""
            SELECT id, symbol, entry_price, target_sell_price, target_sell_date 
            FROM sp500_trade_log 
            WHERE status = 'PENDING'
        """).fetchall()
        
        for trade in pending:
            trade_id = trade["id"]
            symbol = trade["symbol"]
            entry_price = float(trade["entry_price"])
            target_sell_price = float(trade["target_sell_price"])
            target_sell_date = trade["target_sell_date"] # YYYY-MM-DD
            
            cur_price = fetch_live_price(symbol)
            if cur_price <= 0:
                continue
                
            resolved = False
            exit_price = None
            exit_reason = ""
            
            if cur_price >= target_sell_price:
                exit_price = target_sell_price
                resolved = True
                exit_reason = "목표가 달성 익절 청산"
            elif target_sell_date and today_str >= target_sell_date:
                exit_price = cur_price
                resolved = True
                exit_reason = "목표 보유기간 만료 만기 청산"
            elif cur_price <= entry_price * 0.93:
                exit_price = entry_price * 0.93
                resolved = True
                exit_reason = "리스크 관리 손절선 터치 (-7% 손절)"
                
            if resolved:
                realized_return = ((exit_price / entry_price) - 1.0) * 100.0
                conn.execute("""
                    UPDATE sp500_trade_log
                    SET status = 'COMPLETED', exit_price = ?, exit_time = ?, realized_return = ?, exit_reason = ?
                    WHERE id = ?
                """, (exit_price, now_ts, realized_return, exit_reason, trade_id))
                conn.commit()
                print(f"✅ Resolved trade for {symbol}: {exit_reason} (ROI: {realized_return:+.2f}%)")

    # 2. Enter new top picks (limit to top 10)
    top_picks = map_data.get("topPicks", [])
    if not top_picks:
        return
        
    with sqlite3.connect(DB_PATH) as conn:
        for item in top_picks[:10]:
            symbol = item.get("symbol")
            cur_price = float(item.get("currentPrice") or 0)
            target_price = float(item.get("optimalSellPrice") or 0)
            target_date = item.get("optimalSellTimestamp")
            if target_date and "T" in target_date:
                target_date = target_date.split("T")[0]
                
            quadrant = item.get("quadrant", "N/A")
            score = float(item.get("optimizationScore") or 0)
            
            p_trend = float(item.get("prophetTrend") or 0)
            p_slope = float(item.get("prophetTrendSlope") or 0)
            p_weekly = float(item.get("prophetWeekly") or 0)
            p_monthly = float(item.get("prophetMonthly") or 0)
            
            if cur_price <= 0 or not symbol:
                continue
                
            exists = conn.execute("""
                SELECT 1 FROM sp500_trade_log 
                WHERE symbol = ? AND status = 'PENDING'
            """, (symbol,)).fetchone()
            
            if not exists:
                if target_price <= 0:
                    target_price = cur_price * 1.05
                if not target_date:
                    target_date = (datetime.today() + pd.Timedelta(days=15)).strftime("%Y-%m-%d")
                    
                buy_reason = (
                    f"AI 탑 10 추천 편입 (사분면: {quadrant}, 최적화점수: {score:.2f}, "
                    f"Prophet 트렌드: {p_trend:.2f} (일변화: {p_slope:+.4f}), "
                    f"계절성 주간: {p_weekly*100.0:+.2f}%, 월간: {p_monthly*100.0:+.2f}%)"
                )
                
                conn.execute("""
                    INSERT INTO sp500_trade_log (
                        symbol, entry_time, entry_price, target_sell_price, target_sell_date, 
                        status, buy_reason, prophet_trend, prophet_trend_slope, prophet_weekly, 
                        prophet_monthly, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?)
                """, (
                    symbol, now_ts, cur_price, target_price, target_date, 
                    buy_reason, p_trend, p_slope, p_weekly, 
                    p_monthly, datetime.now(timezone.utc).isoformat()
                ))
                conn.commit()
                print(f"🛒 Entered new virtual S&P500 trade for {symbol} at ${cur_price:.2f}")

def generate_sp500_portfolio_pie_chart(photo_path: str) -> bool:
    """Generate S&P500 portfolio allocation pie chart and save to path."""
    import matplotlib
    matplotlib.use('Agg') # Non-interactive backend
    import matplotlib.pyplot as plt
    
    init_sp500_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        completed = conn.execute("SELECT realized_return FROM sp500_trade_log WHERE status = 'COMPLETED'").fetchall()
        pending = conn.execute("SELECT symbol, entry_price FROM sp500_trade_log WHERE status = 'PENDING'").fetchall()
        
    realized_usd = sum((float(t["realized_return"] or 0) / 100.0) * ALLOCATION_PER_TRADE for t in completed)
    
    labels = []
    sizes = []
    
    active_val = 0.0
    for pos in pending:
        sym = pos["symbol"]
        entry = float(pos["entry_price"])
        cur = fetch_live_price(sym)
        if cur <= 0:
            cur = entry
        val = (cur / entry) * ALLOCATION_PER_TRADE
        active_val += val
        labels.append(sym)
        sizes.append(val)
        
    current_cash = START_CAPITAL + realized_usd - (ALLOCATION_PER_TRADE * len(pending))
    if current_cash < 0:
        current_cash = 0.0
        
    if current_cash > 0:
        labels.append("Cash")
        sizes.append(current_cash)
        
    if not sizes:
        labels = ["Empty Portfolio"]
        sizes = [100.0]
        
    # Premium color palette
    colors = ['#4f46e5', '#06b6d4', '#10b981', '#f59e0b', '#ec4899', '#8b5cf6', '#ef4444', '#64748b']
    if len(sizes) > len(colors):
        colors = colors * (len(sizes) // len(colors) + 1)
    colors = colors[:len(sizes)]
    
    try:
        fig, ax = plt.subplots(figsize=(6, 6))
        wedges, texts, autotexts = ax.pie(
            sizes, 
            labels=labels, 
            autopct='%1.1f%%',
            startangle=140, 
            colors=colors,
            textprops=dict(color="#1e293b", weight="bold"),
            wedgeprops=dict(width=0.4, edgecolor='w', linewidth=2)
        )
        
        plt.setp(autotexts, size=9, weight="bold")
        plt.setp(texts, size=10)
        
        total_valuation = current_cash + active_val
        ax.text(0, 0, f"${total_valuation:,.0f}\nUSD", ha='center', va='center', fontsize=14, weight='bold', color='#0f172a')
        
        ax.set_title("S&P500 Quant Portfolio Allocation", fontsize=13, weight="bold", pad=20, color='#0f172a')
        plt.tight_layout()
        
        # Ensure target directory exists
        Path(photo_path).parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(photo_path, dpi=150, bbox_inches='tight', transparent=True)
        plt.close()
        return True
    except Exception as e:
        print(f"⚠️ Failed to generate S&P500 pie chart: {e}")
        return False

def generate_sp500_quant_report() -> str:
    """Generate HTML quant account report for S&P500."""
    init_sp500_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        completed = conn.execute("SELECT entry_price, exit_price, realized_return FROM sp500_trade_log WHERE status = 'COMPLETED'").fetchall()
        pending = conn.execute("SELECT symbol, entry_price, target_sell_price, target_sell_date, buy_reason, prophet_trend, prophet_trend_slope, prophet_weekly, prophet_monthly FROM sp500_trade_log WHERE status = 'PENDING'").fetchall()
        recent_closed = conn.execute("SELECT symbol, entry_price, exit_price, realized_return, exit_reason FROM sp500_trade_log WHERE status = 'COMPLETED' ORDER BY id DESC LIMIT 5").fetchall()
        
    total_trades = len(completed)
    wins = sum(1 for t in completed if float(t["realized_return"] or 0) > 0)
    win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0
    
    realized_usd = sum((float(t["realized_return"] or 0) / 100.0) * ALLOCATION_PER_TRADE for t in completed)
    
    active_lines = []
    unrealized_usd = 0.0
    for pos in pending:
        sym = pos["symbol"]
        entry = float(pos["entry_price"])
        target = float(pos["target_sell_price"])
        target_dt = pos["target_sell_date"]
        reason = pos["buy_reason"]
        p_trend = pos["prophet_trend"]
        p_slope = pos["prophet_trend_slope"]
        p_weekly = pos["prophet_weekly"]
        p_monthly = pos["prophet_monthly"]
        
        p_details = ""
        if p_trend is not None:
            p_slope_val = p_slope if p_slope is not None else 0.0
            p_weekly_val = p_weekly if p_weekly is not None else 0.0
            p_monthly_val = p_monthly if p_monthly is not None else 0.0
            p_details = (
                f"\n  - <u>Prophet 예측</u>: 트렌드 ${p_trend:,.2f} (일변화: {p_slope_val:+.4f}), "
                f"계절성 주간 {p_weekly_val*100.0:+.2f}%, 월간 {p_monthly_val*100.0:+.2f}%"
            )
        
        cur = fetch_live_price(sym)
        if cur > 0:
            unrealized_ret = ((cur / entry) - 1.0) * 100.0
            unrealized_p = (unrealized_ret / 100.0) * ALLOCATION_PER_TRADE
            unrealized_usd += unrealized_p
            active_lines.append(f"• <b>{sym}</b>: 진입 ${entry:,.2f} ➡️ 현재 ${cur:,.2f} ({unrealized_ret:+.2f}%){p_details}\n  - <u>이유</u>: {reason}\n  - <u>목표가</u>: ${target:,.2f} (기한: {target_dt})")
        else:
            active_lines.append(f"• <b>{sym}</b>: 진입 ${entry:,.2f} (시세 조회 실패){p_details}\n  - <u>이유</u>: {reason}")
            
    total_pnl = realized_usd + unrealized_usd
    current_capital = START_CAPITAL + total_pnl
    
    lines = []
    lines.append("🤖 <b>[No Slip Quant] S&P500 가상 매매 일간 리포트</b>")
    lines.append("=" * 40)
    lines.append("S&P500 AI Top 10 추천 포트폴리오를 기반으로 실행한 퀀트 에이전트 가상 매매 결과입니다.\n")
    
    lines.append("💳 <b>계좌 현황 (S&P500 Account)</b>")
    lines.append(f"  • <b>가상 자산 평가총액</b>: ${current_capital:,.2f} USD")
    lines.append(f"  • <b>시작 원금</b>: ${START_CAPITAL:,.2f} USD")
    lines.append(f"  • <b>누적 총 손익</b>: ${total_pnl:+,.2f} USD ({total_pnl/START_CAPITAL*100.0:+.2f}%)")
    lines.append(f"  • <b>실현 손익 (Realized)</b>: ${realized_usd:+,.2f} USD")
    lines.append(f"  • <b>평가 손익 (Unrealized)</b>: ${unrealized_usd:+,.2f} USD")
    lines.append(f"  • <b>총 매매 횟수</b>: {total_trades}회")
    lines.append(f"  • <b>승률 (Win Rate)</b>: {win_rate:.1f}% ({wins}승 / {total_trades - wins}패)")
    
    lines.append("\n📈 <b>보유 중인 주식 포지션 ({0}개)</b>".format(len(pending)))
    if active_lines:
        lines.extend(active_lines)
    else:
        lines.append("  • 현재 보유 중인 가상 주식 포지션 없음")
        
    lines.append("\n🕒 <b>최근 청산 내역 (최근 5건)</b>")
    if recent_closed:
        for t in recent_closed:
            ret = float(t["realized_return"] or 0)
            emoji = "🟢" if ret > 0 else "🔴"
            lines.append(f"  • {emoji} <b>{t['symbol']}</b>: 진입 ${float(t['entry_price']):,.2f} ➡️ 청산 ${float(t['exit_price']):,.2f} ({ret:+.2f}% / {t['exit_reason']})")
    else:
        lines.append("  • 최근 청산 완료된 포지션 없음")
        
    lines.append("\n" + "=" * 40)
    lines.append("💡 <b>인간 가이드</b>: S&P500 AI 추천 포트폴리오를 기반으로 가상 매매를 집행하고, 목표가 도달 혹은 타임아웃 기한 만료 시 자가 판단 청산합니다.")
    return "\n".join(lines)

def get_latest_map_data() -> dict:
    cache_dir = ROOT_DIR / "services" / "trader" / "model_cache" / "sp500_information_maps"
    latest_file = cache_dir / "latest.json"
    if not latest_file.exists():
        json_files = list(cache_dir.glob("*.json"))
        if not json_files:
            return {}
        latest_file = max(json_files, key=os.path.getmtime)
    
    try:
        with open(latest_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def fetch_macro_indicators() -> dict:
    """Fetch live macroeconomic indicators from yfinance."""
    tickers = {
        "US10Y": "^TNX",      # US 10-Year Treasury Yield
        "DXY": "DX-Y.NYB",    # US Dollar Index
        "Oil": "CL=F",        # WTI Crude Oil
        "Gold": "GC=F",       # Gold Futures
        "VIX": "^VIX"         # CBOE Volatility Index
    }
    
    results = {}
    import yfinance as yf
    for name, ticker_symbol in tickers.items():
        try:
            ticker = yf.Ticker(ticker_symbol)
            price = None
            prev_close = None
            
            fast_info = getattr(ticker, "fast_info", None)
            if fast_info:
                price = getattr(fast_info, "lastPrice", None)
                prev_close = getattr(fast_info, "previousClose", None)
                
            if price is None or prev_close is None:
                hist = ticker.history(period="5d")
                if not hist.empty:
                    if len(hist) >= 2:
                        price = float(hist["Close"].iloc[-1])
                        prev_close = float(hist["Close"].iloc[-2])
                    else:
                        price = float(hist["Close"].iloc[-1])
                        prev_close = price
                        
            if price is not None:
                change = 0.0
                if prev_close and prev_close > 0:
                    change = ((price / prev_close) - 1.0) * 100.0
                results[name] = {"price": price, "change": change}
            else:
                results[name] = {"price": 0.0, "change": 0.0}
        except Exception as e:
            print(f"⚠️ Failed to fetch macro ticker {ticker_symbol}: {e}")
            results[name] = {"price": 0.0, "change": 0.0}
            
    return results

def generate_message_1_market_summary_and_top_picks() -> str:
    map_data = get_latest_map_data()
    if not map_data:
        return "⚠️ S&P500 정보 맵 캐시 데이터를 찾을 수 없습니다."
    
    portfolio_cache = ROOT_DIR / "services" / "trader" / "model_cache" / "sp500_portfolio_latest.json"
    portfolio_data = {}
    if portfolio_cache.exists():
        try:
            with open(portfolio_cache, "r", encoding="utf-8") as f:
                portfolio_data = json.load(f)
        except Exception:
            pass

    date_str = map_data.get("mapDate", datetime.today().strftime("%Y-%m-%d"))
    
    lines = []
    lines.append(f"📊 <b>[No Slip] S&P500 시황 & AI 탑 10 추천 ({date_str})</b>")
    lines.append("=" * 40)
    
    reinforcement = map_data.get("reinforcement", {})
    investor_lens = reinforcement.get("investorLensSnapshot", {})
    leader = investor_lens.get("leader", "buffett")
    
    allocation = portfolio_data.get("allocation", {})
    macro = allocation.get("macro", {})
    
    lines.append("<b>▶️ 1. 거시 국면 및 AI 상태 요약</b>")
    lines.append(f"  • AI 리더: {leader.upper()}")
    if macro:
        lines.append(f"  • 금리: {macro.get('rateRegime', 'N/A')} ({macro.get('policyRatePct', 'N/A')}% 수준)")
        lines.append(f"  • 유동성: {macro.get('liquidityRegime', 'N/A')}")
        
    risk_inputs = portfolio_data.get("riskInputs", {})
    if risk_inputs:
        lines.append(f"  • 변동성: {float(risk_inputs.get('weightedVolatilityPct') or 0):.2f}% / 불확실성: {float(risk_inputs.get('weightedUncertaintyPct') or 0):.2f}%")
        lines.append(f"  • 국면 천이 리스크: {float(risk_inputs.get('weightedRegimeRiskPct') or 0):.2f}%")
    
    # 글로벌 거시경제 시황 추가 (Global Macro Update)
    lines.append("\n<b>🌍 글로벌 거시경제 & 매크로 시황 (Macro Update)</b>")
    
    try:
        macro_indicators = fetch_macro_indicators()
    except Exception as e:
        print(f"⚠️ Failed to fetch macro indicators: {e}")
        macro_indicators = {}
        
    if macro_indicators:
        us10y = macro_indicators.get("US10Y", {})
        dxy = macro_indicators.get("DXY", {})
        oil = macro_indicators.get("Oil", {})
        gold = macro_indicators.get("Gold", {})
        vix = macro_indicators.get("VIX", {})
        
        lines.append("📊 <b>실시간 글로벌 금융 지표</b>")
        lines.append(f"  • <b>미 10년물 국채금리 (US10Y)</b>: {us10y.get('price', 0):.3f}% ({us10y.get('change', 0):+.2f}%)")
        lines.append(f"  • <b>달러 인덱스 (DXY)</b>: {dxy.get('price', 0):.2f} ({dxy.get('change', 0):+.2f}%)")
        lines.append(f"  • <b>WTI 국제유가 (Crude Oil)</b>: ${oil.get('price', 0):.2f} ({oil.get('change', 0):+.2f}%)")
        lines.append(f"  • <b>금 선물 (Gold)</b>: ${gold.get('price', 0):,.2f} ({gold.get('change', 0):+.2f}%)")
        lines.append(f"  • <b>VIX 공포지수 (Volatility)</b>: {vix.get('price', 0):.2f} ({vix.get('change', 0):+.2f}%)")
        lines.append("")
        
    lines.append("📰 <b>주요 매크로 이슈 및 국면 분석</b>")
    lines.append("  • <b>미국 통화 정책</b>: 미 연준(Fed) 기준금리 <b>3.50%~3.75%</b> 동결 기조 유지. 중동 지정학적 충격으로 인한 에너지 상승 압력에 추가 긴축 경계감 및 금리 동결 지속.")
    lines.append("  • <b>물가 & 인플레이션</b>: CPI 3.8% 수준으로 연준의 목표치(2%)를 웃돌며 'Higher-for-Longer(고금리 장기화)' 전망 지배적.")
    lines.append("  • <b>성장 & 경기 사이클</b>: 고금리 환경 하에서도 고용 지표와 AI 인프라 투자 견인으로 침체 없는 <b>'노랜딩(No Landing)'</b> 흐름 유지. 단, 성장 둔화 및 인플레이션 가속 압박을 받는 유럽 일부 경제의 스태그플레이션 우려 존재.")
    
    top_picks = map_data.get("topPicks", [])
    if top_picks:
        lines.append(f"\n<b>▶️ 2. S&P500 AI 탑 10 추천 상세</b>")
        for i, item in enumerate(top_picks[:10]):
            symbol = item.get("symbol")
            name = item.get("name", "N/A")[:15]
            upside = float(item.get("maxUpsidePct") or 0) * 100
            score = float(item.get("optimizationScore") or 0)
            cur_price = float(item.get("currentPrice") or 0)
            
            buy_price = float(item.get("optimalBuyPrice") or 0)
            buy_date = (item.get("optimalBuyTimestamp") or "N/A")[5:10] # MM-DD
            sell_price = float(item.get("optimalSellPrice") or 0)
            sell_date = (item.get("optimalSellTimestamp") or "N/A")[5:10] # MM-DD
            max_dd = float(item.get("maxDrawdownPct") or 0) * 100
            
            lines.append(f"\n🏷️ <b>{i+1:02d}. {symbol} ({name})</b> | 점수: {score:.2f}")
            lines.append(f"  • 현재: ${cur_price:.2f} (상승률: +{upside:.1f}% / 낙폭: {max_dd:.1f}%)")
            lines.append(f"  • 매수: ${buy_price:.2f} ({buy_date}) | 매도: ${sell_price:.2f} ({sell_date})")

    # --- Drawdown Exclusions ---
    drawdown_exclusions = map_data.get("drawdownExclusions", [])
    if drawdown_exclusions:
        lines.append("\n<b>🚫 장기 하락 우려 제외 종목 (Drawdown Exclusions)</b>")
        for item in drawdown_exclusions[:5]:  # limit to top 5 exclusions for message length
            symbol = item.get("symbol")
            name = item.get("name", "N/A")[:15]
            reason = item.get("reason", "N/A")
            lines.append(f"  • <b>{symbol} ({name})</b>: {reason}")

    # --- 3. Prophet Daily/Monthly Optimal Strategy ---
    points = map_data.get("points", [])
    if not points:
        points = map_data.get("topPicks", [])
        
    valid_trends = [float(p["prophetTrend"]) for p in points if p.get("prophetTrend") is not None]
    valid_slopes = [float(p["prophetTrendSlope"]) for p in points if p.get("prophetTrendSlope") is not None]
    valid_weeklies = [float(p["prophetWeekly"]) for p in points if p.get("prophetWeekly") is not None]
    valid_monthlies = [float(p["prophetMonthly"]) for p in points if p.get("prophetMonthly") is not None]
    
    avg_trend = sum(valid_trends) / len(valid_trends) if valid_trends else 0.0
    avg_slope = sum(valid_slopes) / len(valid_slopes) if valid_slopes else 0.0
    avg_weekly = sum(valid_weeklies) / len(valid_weeklies) if valid_weeklies else 0.0
    avg_monthly = sum(valid_monthlies) / len(valid_monthlies) if valid_monthlies else 0.0
    
    # Establish daily strategy
    if avg_slope > 0.02 and avg_weekly > 0.01:
        daily_title = "강세 추세 추종 (Aggressive Trend Following)"
        daily_desc = "시장 전체의 Prophet 일별 트렌드가 상승 가속하고 있습니다. 신규 진입 시 매수 대기보다는 돌파 시 추격 매수가 유리하며, 손절선(Trailing Stop)을 타이트하게 설정해 이익을 보존합니다."
    elif avg_slope > 0.02:
        daily_title = "지속 매수 및 보유 (Hold & Accumulate)"
        daily_desc = "일별 트렌드는 안정적인 우상향을 보이고 있으나 단기 주간 변동성이 낮습니다. 분할 매수 관점에서 포지션을 유지하며, 급한 매매를 지양하고 비중을 안정적으로 유지합니다."
    elif avg_slope <= 0.02 and avg_weekly > 0.01:
        daily_title = "단기 스윙 매매 (Short-term Mean Reversion)"
        daily_desc = "트렌드는 횡보 국면이나 주간 변동성이 상대적으로 높은 상태입니다. 지지선 하단 매수, 저항선 상단 청산 형태의 단기 스윙 전략이 유리합니다."
    elif avg_slope <= -0.01:
        daily_title = "방어적 리스크 관리 (Risk-Off Defensive)"
        daily_desc = "일별 트렌드가 약세 국면으로 돌아섰습니다. 신규 매수 편입을 중단하고 손절선에 도달한 포지션은 즉시 청산하여 현금 비중을 극대화합니다."
    else:
        daily_title = "중립 관망 및 선택적 가치 매입 (Selective Value Buying)"
        daily_desc = "시장 트렌드가 중립 횡보 상태입니다. 무리한 지수 추종보다는 개별 종목 중 하단 지지력이 확보된 과매도 우량주 중심의 선택적 대응이 유리합니다."

    # Establish monthly strategy
    if avg_monthly > 0.02:
        monthly_title = "적극적 주식 비중 확대 (Risk-On Aggressive)"
        monthly_desc = "Prophet 월별 계절성 모델이 강한 우상향 주기에 있습니다. 자산배분 내 주식 비중을 최대 75% 이상으로 확대하고, 채권 및 현금 자산을 최소화하여 모멘텀 이익을 수확합니다."
    elif avg_monthly < -0.01:
        monthly_title = "안전자산 확대 및 포트폴리오 헤징 (Risk-Off Defense)"
        monthly_desc = "월별 계절성 지표가 중단기 하강 사이클을 경고합니다. 주식 비중을 40% 수준으로 축소하고 채권(Bonds/Treasuries)과 금(Gold) 및 현금 비중을 60% 이상으로 확대 구성합니다."
    else:
        monthly_title = "균형 리밸런싱 포트폴리오 (Dynamic Balanced)"
        monthly_desc = "월별 계절성 지표가 평이한 중립 범위입니다. 주식 60%, 채권 30%, 현금 10%의 올웨더 형태 자산 배분 비중을 유지하며, 개별 종목의 펀더멘털 노이즈에 집중해 리밸런싱합니다."

    lines.append("\n<b>▶️ 3. Prophet 기반 일일/월별 최적 자산운용 전략</b>")
    lines.append(f"  • <b>일별 트렌드 방향성</b>: {avg_slope:+.4f} (주간 계절성: {avg_weekly*100.0:+.2f}%)")
    lines.append(f"  • <b>월별 계절성 모멘텀</b>: {avg_monthly*100.0:+.2f}%")
    lines.append(f"  • 📊 <b>일일 최적 매매 전략 (Daily)</b>: <u>{daily_title}</u>")
    lines.append(f"    - <i>{daily_desc}</i>")
    lines.append(f"  • 📅 <b>월별 최적 자산 배분 (Monthly)</b>: <u>{monthly_title}</u>")
    lines.append(f"    - <i>{monthly_desc}</i>")

    lines.append("\n" + "=" * 40)
    return "\n".join(lines)

def generate_message_2_information_map_quadrants() -> str:
    map_data = get_latest_map_data()
    if not map_data:
        return "⚠️ S&P500 정보 맵 캐시 데이터를 찾을 수 없습니다."
    
    date_str = map_data.get("mapDate", datetime.today().strftime("%Y-%m-%d"))
    points = map_data.get("points", [])
    
    quadrants = {
        "breakout acceleration": [],
        "recovery setup": [],
        "uptrend cooling": [],
        "selloff acceleration": []
    }
    
    for p in points:
        quad = p.get("quadrant")
        if quad in quadrants:
            quadrants[quad].append(p)
            
    for quad in quadrants:
        quadrants[quad].sort(key=lambda x: float(x.get("optimizationScore") or 0), reverse=True)
        
    lines = []
    lines.append(f"🗺️ <b>[No Slip] S&P500 AI 정보 맵 사분면 ({date_str})</b>")
    lines.append("=" * 40)
    
    lines.append("<b>🚀 1. 돌파 가속 (Breakout Acceleration)</b>")
    q_breakout = quadrants["breakout acceleration"]
    lines.append(f"  • 사분면 종목 수: {len(q_breakout)}개")
    if q_breakout:
        for item in q_breakout[:5]:
            sym = item.get("symbol")
            upside = float(item.get("maxUpsidePct") or 0) * 100
            score = float(item.get("optimizationScore") or 0)
            lines.append(f"    - {sym:<5} (Upside: +{upside:.1f}% / Score: {score:.2f})")
    else:
        lines.append("    - 진입 종목 없음")
        
    lines.append("\n<b>🔄 2. 회복 준비 (Recovery Setup)</b>")
    q_recovery = quadrants["recovery setup"]
    lines.append(f"  • 사분면 종목 수: {len(q_recovery)}개")
    if q_recovery:
        for item in q_recovery[:5]:
            sym = item.get("symbol")
            upside = float(item.get("maxUpsidePct") or 0) * 100
            score = float(item.get("optimizationScore") or 0)
            lines.append(f"    - {sym:<5} (Upside: +{upside:.1f}% / Score: {score:.2f})")
    else:
        lines.append("    - 진입 종목 없음")
        
    lines.append("\n<b>📈 3. 상승 안정/조절 (Uptrend Cooling)</b>")
    q_cooling = quadrants["uptrend cooling"]
    lines.append(f"  • 사분면 종목 수: {len(q_cooling)}개")
    if q_cooling:
        for item in q_cooling[:5]:
            sym = item.get("symbol")
            upside = float(item.get("maxUpsidePct") or 0) * 100
            score = float(item.get("optimizationScore") or 0)
            lines.append(f"    - {sym:<5} (Upside: +{upside:.1f}% / Score: {score:.2f})")
            
    lines.append("\n<b>⚠️ 4. 하락 우위/가속 (Selloff Acceleration)</b>")
    q_selloff = quadrants["selloff acceleration"]
    lines.append(f"  • 사분면 종목 수: {len(q_selloff)}개")
    if q_selloff:
        for item in q_selloff[:5]:
            sym = item.get("symbol")
            upside = float(item.get("maxUpsidePct") or 0) * 100
            score = float(item.get("optimizationScore") or 0)
            lines.append(f"    - {sym:<5} (Upside: +{upside:.1f}% / Score: {score:.2f})")
    else:
        lines.append("    - 진입 종목 없음")

    lines.append("\n" + "=" * 40)
    return "\n".join(lines)

def generate_message_3_turnover_and_dark_horses() -> str:
    map_data = get_latest_map_data()
    if not map_data:
        return "⚠️ S&P500 정보 맵 캐시 데이터를 찾을 수 없습니다."
    
    date_str = map_data.get("mapDate", datetime.today().strftime("%Y-%m-%d"))
    dark_horses = map_data.get("darkHorsePicks", [])
    
    lines = []
    lines.append(f"🐎 <b>[No Slip] 대칭성 턴오버 & 다크호스 리포트 ({date_str})</b>")
    lines.append("=" * 40)
    
    if dark_horses:
        for i, item in enumerate(dark_horses[:6]):
            symbol = item.get("symbol")
            name = item.get("name", "N/A")[:15]
            cur_price = float(item.get("currentPrice") or 0)
            upside = float(item.get("maxUpsidePct") or 0) * 100
            score = float(item.get("darkHorseScore") or 0)
            
            symmetry = item.get("symmetry", {})
            counterpart = symmetry.get("counterpartSymbol", "N/A")
            underfollowed = float(symmetry.get("underfollowedScore") or 0) * 100
            rationale = symmetry.get("rationale", "N/A")[:80] + "..."
            
            lines.append(f"🔥 <b>{i+1:02d}. {symbol} ({name})</b> | Score: {score:.1f}")
            lines.append(f"  • 현재: ${cur_price:.2f} (Upside: +{upside:.1f}%)")
            lines.append(f"  • 대조짝: {counterpart} / 소외도: {underfollowed:.1f}%")
            lines.append(f"  • 분석: {rationale}")
            lines.append("")
    else:
        lines.append("• 대칭성 추천 종목 없음")

    lines.append("=" * 40)
    return "\n".join(lines)

def send_whatsapp_message(text: str) -> bool:
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_whatsapp = os.getenv("TWILIO_FROM_WHATSAPP")
    to_whatsapp = os.getenv("USER_WHATSAPP_NUMBER")
    
    if all([account_sid, auth_token, from_whatsapp, to_whatsapp]) and to_whatsapp.startswith("whatsapp:"):
        try:
            from twilio.rest import Client
            client = Client(account_sid, auth_token)
            client.messages.create(
                body=text,
                from_=from_whatsapp,
                to=to_whatsapp
            )
            print("✅ [Twilio WhatsApp] 전송 완료!")
            return True
        except Exception as e:
            print(f"❌ [Twilio WhatsApp] 전송 실패: {e}")
            
    callmebot_key = os.getenv("CALLMEBOT_API_KEY")
    user_phone = os.getenv("USER_WHATSAPP_NUMBER")
    if callmebot_key and user_phone:
        import urllib.parse
        import urllib.request
        
        clean_phone = user_phone.replace("whatsapp:", "").replace(" ", "").replace("-", "").strip()
        plain_text = text.replace("<b>", "").replace("</b>", "")
        encoded_text = urllib.parse.quote(plain_text)
        url = f"https://api.callmebot.com/whatsapp.php?phone={clean_phone}&text={encoded_text}&apikey={callmebot_key}"
        
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                response.read()
                print("✅ [CallMeBot WhatsApp] 전송 완료!")
                return True
        except Exception as e:
            print(f"❌ [CallMeBot WhatsApp] 전송 실패: {e}")
            
    return False

def send_whatsapp_message_safe(text: str):
    import urllib.parse
    lines = text.split("\n")
    current_chunk = []
    
    for line in lines:
        test_chunk = current_chunk + [line]
        test_text = "\n".join(test_chunk)
        encoded_len = len(urllib.parse.quote(test_text))
        
        if encoded_len > 800 and current_chunk:
            send_whatsapp_message("\n".join(current_chunk))
            time.sleep(2.5)
            current_chunk = [line]
        else:
            current_chunk.append(line)
            
    if current_chunk:
        send_whatsapp_message("\n".join(current_chunk))

def send_telegram_message(text: str) -> bool:
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
                print(f"✅ [Telegram] {cid} 전송 완료!")
        except Exception as e:
            print(f"❌ [Telegram] {cid} 전송 실패: {e}")
            success = False
            
    return success

def send_telegram_photo(photo_path: str, caption: str) -> bool:
    """Send local image file directly to Telegram chat IDs."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_ids_str = os.getenv("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_ids_str:
        print("⚠️ Telegram 설정 누락 (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")
        return False
        
    chat_ids = [cid.strip() for cid in chat_ids_str.split(",") if cid.strip()]
    success = True
    
    import requests
    for cid in chat_ids:
        url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
        try:
            with open(photo_path, 'rb') as f:
                files = {'photo': f}
                data = {
                    'chat_id': cid,
                    'caption': caption,
                    'parse_mode': 'HTML'
                }
                res = requests.post(url, data=data, files=files, timeout=20)
                if res.status_code == 200:
                    print(f"✅ [Telegram Photo] {cid} 전송 완료!")
                else:
                    print(f"❌ [Telegram Photo] {cid} 전송 실패 ({res.status_code}): {res.text}")
                    success = False
        except Exception as e:
            print(f"❌ [Telegram Photo] {cid} 전송 실패: {e}")
            success = False
            
    return success

if __name__ == "__main__":
    print("🚀 다중 메시지 알림 시퀀스 시작...")
    
    map_data = get_latest_map_data()
    
    if map_data:
        try:
            process_sp500_quant_trades(map_data)
        except Exception as e:
            print(f"⚠️ S&P500 가상 트레이딩 연산 오류: {e}")
            
    msg1 = generate_message_1_market_summary_and_top_picks()
    msg2 = generate_message_2_information_map_quadrants()
    msg3 = generate_message_3_turnover_and_dark_horses()
    
    msg_quant = ""
    try:
        msg_quant = generate_sp500_quant_report()
    except Exception as e:
        print(f"⚠️ S&P500 매매 리포트 생성 오류: {e}")
        
    # Generate S&P500 Portfolio Pie Chart
    pie_path = str(ROOT_DIR / "data" / "sp500_portfolio_pie.png")
    pie_generated = False
    try:
        pie_generated = generate_sp500_portfolio_pie_chart(pie_path)
    except Exception as e:
        print(f"⚠️ S&P500 파이차트 생성 오류: {e}")
    
    # 1. Telegram 발송
    print("▶️ Telegram으로 리포트 전송 중...")
    send_telegram_message(msg1)
    time.sleep(1.5)
    send_telegram_message(msg2)
    time.sleep(1.5)
    send_telegram_message(msg3)
    time.sleep(1.5)
    
    if msg_quant:
        send_telegram_message(msg_quant)
        time.sleep(1.5)
        
    if pie_generated:
        caption = "🤖 <b>[No Slip Quant] S&P500 가상 포트폴리오 자산 배분 시각화</b>\n※ 실시간 가상 매수매도 포지션 비율 및 현금 잔고 현황입니다."
        send_telegram_photo(pie_path, caption)
        time.sleep(1.5)
        
    # 2. WhatsApp 발송
    print("▶️ WhatsApp으로 리포트 전송 중 (안전 분할 적용)...")
    send_whatsapp_message_safe(msg1)
    time.sleep(2.5)
    send_whatsapp_message_safe(msg2)
    time.sleep(2.5)
    send_whatsapp_message_safe(msg3)
    
    if msg_quant:
        time.sleep(2.5)
        send_whatsapp_message_safe(msg_quant)
    
    print("🎉 모든 알림 메시지 발송 프로세스 완료!")
