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
The background monitor daemon watches Binance, Bybit, Upbit, Bithumb, and Coinone in real-time:
- **RSI Reversion**: Rebound at oversold thresholds (<25).
- **MACD Crossover**: Volatility breakout at MACD Golden Cross.
- **Bollinger Band Squeeze**: Price breakout following tight volatility bands.
- **Spot Arbitrage**: Price inefficiencies between Binance Spot and Bybit Spot.
- **Kimchi Premium Arbitrage**: Real-time USD/KRW exchange-rate-adjusted premium tracking between Upbit, Bithumb, Coinone, and Binance Spot.
- **Multi-Exchange Arbitrage**: A 5-way comparative scanner finding the optimal buy-sell spread direction across 5 major exchanges.
- Relevant files: `services/trader/whale_pump_monitor.py`

### 5. Monthly Parameter Optimization Pipeline
Automatically updates the config file on the 1st of every month using numpy-accelerated strategy simulators:
- Downloads and caches 1-minute historical klines.
- Runs a calendar grid search to find optimal trigger thresholds, Hold times (H), Stop-Loss (SL), and Take-Profit (TP) targets for each strategy.
- Automatically updates `services/trader/model_cache/whale_config.json` dynamically loaded by the daemon.
- Relevant files: `services/trader/optimize_monthly_strategies.py`

### 6. GICS Sector Orbit Learning & Trajectory Modeling
Models macro rotation vectors across 11 standard Global Industry Classification Standard (GICS) sectors:
- **SVD + MLP Trajectory Learner**: Compresses high-dimensional centroid/dispersion coordinate histories using SVD and trains a numpy-based transition MLP model using SGD to learn trajectory momentum drifts.
- **Telegram `/orbit` Command**: Automatically generates and broadcasts a beautiful custom sector orbit momentum plot (`sector_orbits.png`) with predicted trajectories and ranked sector statistics.
- Relevant files: `services/trader/sector_orbit_learner.py`

### 7. Federated RL Agent & Consent-based Sharing (FedAvg)
A decentralized strategy consolidation framework:
- **Federated RL Agent**: A Q-learning reinforcement agent mapped to discretized states (composed of MLP drop probabilities and GICS sector momentum values) that updates the active risk configuration file (`whale_config.json`) dynamically.
- **FedAvg Consent Sharing Client**: Obfuscates and shares local Q-table parameters with a central aggregator endpoint (`/aggregate`) to merge weights via Privacy-Guarded Federated Averaging (FedAvg), protecting individual balances and API credentials.
- **CLI Dialogue Eavesdropping (`show_federated_log.py`)**: A witty CLI parser that queries active Q-tables and translates mathematical weight transfers into a conversational chat log between your local bot and the central aggregator using Gemini.
- Relevant files: `services/trader/federated_sharing.py`, `services/trader/show_federated_log.py`, `services/trader/federated_rl_agent.py`

### 8. MLP Arbitrage Drop Filter & High-Profit Alert Shield
Protects the user against volatile market downturns and notification fatigue:
- **MLP Drop Filter**: A scikit-learn based Multi-Layer Perceptron drop predictor halts Spot/Kimchi/Three-way arbitrage executions if price drop probability in the next 15 minutes is $\ge 50\%$.
- **High-Profit Alert Filter**: Restricts Telegram notifications to high-margin opportunities only (Spot Spread $\ge 0.25\%$, 3-way Spread $\ge 0.25\%$, Kimchi Premium deviation $\ge 0.30\%$) while leaving automatic trades running behind the scenes.
- **Evaluation Scoring**: The RL agent uses `gemini-flash-latest` to evaluate and score (0 to 100) the daily market summary reports prior to Telegram broadcast.
- Relevant files: `services/trader/mlp_drop_predictor.py`, `services/trader/ohseon_summary.py`

### 9. Quantopia: 2D RPG Agent Township Dashboard (`/village`)
An interactive, game-like Next.js React dashboard representing our cooperative quant bot ecosystem:
- **Cozy RPG Theme**: Styled with lush green grass, peach paths, and a rustic wooden frame, inspired by retro RPG board games.
- **Seven AI Agent Characters**: Renders Traders (BTC, ETH, SOL), MLP Advisors (BTC MLP, ETH MLP, SOL MLP), and the Federated RL Coordinator as cute Slime/Robot avatars.
- **24/7 Overclocked Night-Shift Schedule**: Features time-of-day clock cycles where bots work at desks, meet at the Town Council Hall table to sync parameters, and run server maintenance in the Server Cabin.
- **Interactive Computer Terminals**: Clicking on any building pops open a green-on-black CRT monitor modal containing the local logs of the agents residing in that building.
- Relevant files: `app/village/page.tsx`

---

## Claude Code CLI Plugin Integration

Our quant tools can be integrated directly into your Claude Code CLI agent as an agent plugin.

### Method 1: Direct installation from GitHub
You can add the plugin directly to your Claude Code instance by pointing to the repository URL:
```bash
claude plugin add https://github.com/Futuremine97/noslip_quant_bot
```

### Method 2: Local development installation
Alternatively, you can clone the repository and add the plugin locally:
```bash
# 1. Clone the repository
git clone https://github.com/Futuremine97/noslip_quant_bot.git
cd noslip_quant_bot

# 2. Register the plugin to Claude Code
claude plugin add .
```

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
