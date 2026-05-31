# No Slip Quant AI Trading Bot & SaaS Platform

No Slip is a state-of-the-art AI-assisted quantitative trading bot, swap analysis tool, and SaaS platform for S&P 500 stocks and major cryptocurrencies (BTC, ETH, SOL).

It integrates:
- **6-Agent Consensus Suite**: Dynamic roundtable debate and voting between Macro, Trend, Value, Whale, Mean-Reversion, and Freqtrade ClucMay agents.
- **Prophet Forecasting Engine**: Multi-resolution timeframes analyzing daily and monthly trends, seasonality, and optimal trade timing.
- **Multi-Strategy Real-time Monitor**: Runs RSI Reversion, MACD Crossover, BB Squeeze Breakout, Spot Arbitrage, and Kimchi Premium Arbitrage in parallel.
- **Telegram Interactive Bot**: Persistent daemon allowing real-time portfolio tracking, debate simulations, backtest competition leaderboards, and on-demand stock/crypto analysis.
- **Model Context Protocol (MCP)**: Custom MCP server exposing trading tools directly into Claude Desktop.
- **SaaS Web App**: Next.js dashboard with integrated Toss Payments SDK and credit system.

---

## Core System Features

### 1. Multi-Agent Consensus Roundtable
The core valuation and signaling stack uses a weighted consensus from six specialized agents:
- **Macro Agent**: Tracks bond yields, inflation, USD index, crude oil, and VIX.
- **Trend Agent**: Evaluates EMA crossovers and momentum.
- **Value Agent**: Inspects valuation metrics and relative strength.
- **Whale Agent**: Tracks large-volume breakouts and trade signals.
- **Mean-Reversion Agent**: Searches for overextended local tops/bottoms.
- **ClucMay Agent (Freqtrade)**: Implements the famous ClucMay strategy.

The agent weights are continuously tuned using a **Policy Gradient Reinforcement Learning (RL)** feedback loop based on realized market outcomes.
- Relevant files: `services/trader/multi_agent_consensus.py`, `services/trader/daily_reinforcement.py`

### 2. Prophet Trend & Seasonality Forecasting
Features high-performance Prophet prediction cadences (intraday 4h for cryptos, daily for stocks). It captures:
- Macro trend slope (daily rate of change).
- Weekly & monthly seasonality normalized as a percentage of total forecasted price ($yhat$).
- Nyquist checking to prevent short-term seasonality aliasing on coarse data rules.
- Relevant files: `services/trader/main.py`, `services/trader/predict_signal.py`, `services/trader/champion_prophet.py`

### 3. Telegram Interactive Bot Daemon (`/기능` & `/portfolio`)
Runs a background long-polling listener allowing users to query and control the bot:
- `/portfolio` (`/포트폴리오`): Displays S&P 500 asset allocations, virtual active holdings, and latest champion models registry.
- `/analyze <symbol>`: Resolves Korean or English stock/crypto names and replies with the 6-Agent voting consensus.
- `/debate <symbol>`: Initiates a live debate between agents. Users reply with their thesis, and the AI agent panel responds using Gemini or rule-based counterarguments.
- `/competition`: Compares the No Slip Quant strategy against Jesse, Hummingbot, and Freqtrade in a 60-day historical backtest tournament.
- `/website` (`/웹사이트`): Spawns an NPX localtunnel background process mapping `localhost:3000` to a public URL with a bypass code, letting you view your Next.js app on a mobile device.
- Relevant files: `services/trader/telegram_interactive_bot.py`

### 4. Parallel Multi-Strategy & Arbitrage Scanner
The background monitor daemon watches Binance, Bybit, and Upbit to scan:
- **RSI Reversion**: Rebound at oversold thresholds (<25).
- **MACD Crossover**: Volatility breakout at MACD Golden Cross.
- **Bollinger Band Squeeze**: Price breakout following tight volatility bands.
- **Spot Arbitrage**: Price inefficiencies between Binance Spot and Bybit Spot.
- **Kimchi Premium Arbitrage**: Real-time USD/KRW exchange-rate-adjusted premium tracking between Upbit and Binance Spot.
- Relevant files: `services/trader/whale_pump_monitor.py`

### 5. Monthly Parameter Optimization Pipeline
Automatically updates the config file on the 1st of every month using numpy-accelerated strategy simulators:
- Downloads and caches 1-minute historical klines.
- Runs a calendar grid search to find optimal trigger thresholds, Hold times (H), Stop-Loss (SL), and Take-Profit (TP) targets for each strategy.
- Automatically updates `services/trader/model_cache/whale_config.json` dynamically loaded by the daemon.
- Relevant files: `services/trader/optimize_monthly_strategies.py`

---

## Claude Desktop MCP Server Integration

No Slip includes a **Model Context Protocol (MCP)** JSON-RPC server that exposes our quant tools directly to your Claude Desktop AI assistant.

### MCP Tools Provided
1. `analyze_ticker`: Prompts the 6-Agent Consensus engine to run a real-time evaluation.
2. `run_league_tournament`: Executes the 60-day backtest tournament leaderboard.

### Installation / Registration
Add the following to your Claude Desktop configuration file (typically `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "NoSlipQuant": {
      "command": "/path/to/your/cloned/repository/services/trader/.venv/bin/python",
      "args": [
        "-u",
        "/path/to/your/cloned/repository/services/trader/mcp_server.py"
      ]
    }
  }
}
```

---

## Local Development & Setup

### 1. Install Node Dependencies (Next.js)
```bash
npm install
```

### 2. Create Python Environment & Install Core Packages
```bash
python3 -m venv services/trader/.venv
services/trader/.venv/bin/pip install -r services/trader/requirements.txt
```

### 3. Environment Variables Config (`.env`)
Create a `.env` file in the root directory. Required parameters:
```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_allowed_chat_ids (comma-separated)
GEMINI_API_KEY=your_google_ai_studio_key
NEXT_PUBLIC_TOSS_CLIENT_KEY=your_toss_payments_client_key
TOSS_SECRET_KEY=your_toss_payments_secret_key
PREDICTION_API_TOKEN=your_shared_api_secret_token
```

### 4. Running the Applications
- **Web App Dashboard (Next.js Dev Server)**:
  ```bash
  npm run dev
  ```
- **Telegram Long-Polling Daemon & Web Tunnel**:
  Managed via macOS launchd plist or run directly:
  ```bash
  services/trader/.venv/bin/python services/trader/telegram_interactive_bot.py
  ```
- **Real-time Price Strategy & Arbitrage Monitor**:
  ```bash
  services/trader/.venv/bin/python services/trader/whale_pump_monitor.py
  ```

---

## production Deployment (macOS Daemon Setup)

The background jobs are orchestrated via macOS `launchd` plist templates:
- `com.noslip.telegram.plist`: Runs the Telegram interactive bot daemon.
- `com.noslip.whale.plist`: Runs the price/arbitrage monitor.
- `com.noslip.daily.plist`: Spawns the S&P 500 & Crypto daily reports (`run_daily.sh`) at 08:30 KST.
- `com.noslip.monthly.plist`: Runs the monthly numpy backtest optimizer on the 1st of every month at 00:05 KST.

To load a daemon:
```bash
cp com.noslip.telegram.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.noslip.telegram.plist
```
