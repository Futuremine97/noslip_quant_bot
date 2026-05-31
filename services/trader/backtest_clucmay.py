#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

def run_clucmay_backtest(symbol: str) -> dict:
    """Run a 30-day historical backtest of the ClucMay strategy for a symbol."""
    print(f"Backtesting Freqtrade ClucMay for {symbol}...")
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="30d", interval="15m")
        if df.empty or len(df) < 55:
            print(f"⚠️ Insufficient data for {symbol}")
            return {}
    except Exception as e:
        print(f"⚠️ Error fetching data for {symbol}: {e}")
        return {}
        
    # Calculate indicators
    df["ema50"] = df["Close"].ewm(span=50, adjust=False).mean()
    
    # 20-period Bollinger Bands
    df["std"] = df["Close"].rolling(window=20).std()
    df["middle_bb"] = df["Close"].rolling(window=20).mean()
    df["lower_bb"] = df["middle_bb"] - 2 * df["std"]
    
    # 30-period Volume MA (excluding current candle)
    df["vol_ma"] = df["Volume"].rolling(window=30).mean()
    
    trades = []
    in_position = False
    buy_price = 0.0
    buy_date = None
    
    # Run simulation
    for i in range(50, len(df)):
        row = df.iloc[i]
        close_p = float(row["Close"])
        volume = float(row["Volume"])
        ema = float(row["ema50"])
        lower_bb = float(row["lower_bb"])
        middle_bb = float(row["middle_bb"])
        vol_ma = float(df["vol_ma"].iloc[i-1]) # Volume MA of previous candles
        
        date = df.index[i]
        
        if not in_position:
            # Check Buy Condition:
            # 1. Price is below 50 EMA
            # 2. Price is below 99.5% of lower Bollinger Band
            # 3. Volume is less than 20x average volume (to filter out extreme volatility spikes)
            if close_p < ema and close_p < lower_bb * 0.995 and vol_ma > 0 and volume < 20 * vol_ma:
                in_position = True
                buy_price = close_p
                buy_date = date
                # print(f"  [BUY] at ${buy_price:,.2f} on {date.strftime('%Y-%m-%d')}")
        else:
            # Check Sell Condition:
            # Price crosses above the middle Bollinger Band
            if close_p > middle_bb:
                in_position = False
                sell_price = close_p
                ret = ((sell_price / buy_price) - 1.0) * 100.0
                holding_days = (date - buy_date).days
                trades.append({
                    "buy_date": buy_date,
                    "sell_date": date,
                    "buy_price": buy_price,
                    "sell_price": sell_price,
                    "return": ret,
                    "holding_days": holding_days
                })
                # print(f"  [SELL] at ${sell_price:,.2f} on {date.strftime('%Y-%m-%d')} (ROI: {ret:+.2f}%, Hold: {holding_days}d)")
                
    # If still in position, close at final price for metric accuracy
    if in_position:
        close_p = float(df["Close"].iloc[-1])
        ret = ((close_p / buy_price) - 1.0) * 100.0
        holding_days = (df.index[-1] - buy_date).days
        trades.append({
            "buy_date": buy_date,
            "sell_date": df.index[-1],
            "buy_price": buy_price,
            "sell_price": close_p,
            "return": ret,
            "holding_days": holding_days,
            "unresolved": True
        })
        
    # Calculate statistics
    total_trades = len(trades)
    wins = sum(1 for t in trades if t["return"] > 0)
    win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0
    net_return = sum(t["return"] for t in trades)
    avg_hold = sum(t["holding_days"] for t in trades) / total_trades if total_trades > 0 else 0.0
    
    # Benchmark buy and hold
    first_p = float(df["Close"].iloc[0])
    last_p = float(df["Close"].iloc[-1])
    buy_hold_ret = ((last_p / first_p) - 1.0) * 100.0
    
    return {
        "symbol": symbol,
        "total_trades": total_trades,
        "win_rate": win_rate,
        "net_return": net_return,
        "avg_hold_days": avg_hold,
        "buy_hold_return": buy_hold_ret,
        "trades": trades
    }

def main():
    print("==================================================")
    print("📈 Freqtrade ClucMay72018 30-Day Strategy Backtest (15m)")
    print("==================================================")
    
    targets = ["BTC-USD", "ETH-USD", "SOL-USD", "NVDA", "AAPL", "MSFT", "INTC", "QBTS", "IONQ", "DELL"]
    results = []
    
    for symbol in targets:
        res = run_clucmay_backtest(symbol)
        if res:
            results.append(res)
            
    print("\n📊 BACKTEST SUMMARY REPORT")
    print("-" * 80)
    print(f"{'Asset':<10} | {'Trades':<6} | {'Win Rate':<10} | {'ClucMay ROI':<13} | {'B&H ROI':<10} | {'Avg Hold':<8}")
    print("-" * 80)
    
    for r in results:
        avg_hold_str = f"{r['avg_hold_days']:.1f}d"
        print(f"{r['symbol']:<10} | {r['total_trades']:<6d} | {r['win_rate']:>8.1f}% | {r['net_return']:>+11.2f}% | {r['buy_hold_return']:>+8.2f}% | {r['avg_hold_days']:>7.1f}d")
        
    print("-" * 80)
    print("Conclusion: Freqtrade's ClucMay strategy focuses on low-risk buying points under extreme market dips.")
    
if __name__ == "__main__":
    main()
