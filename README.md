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

## Optional Web3 / Base Integration

NoSlip supports an optional Web3 extension for wallet-based access, credit
purchases, and future utility-token features. The core analytics system can
still run locally without wallet login.

Current direction:
- Base wallet login with a signed, non-transaction authentication message
- off-chain NoSlip Credits with an auditable ledger
- optional Base Sepolia USDC payment-intent readiness
- premium feature gates with server-defined costs
- future NSQ utility-token design

The default payment mode is `mock` on `base-sepolia`. Base mainnet payment
intents are blocked unless explicitly enabled after production review. The
current browser connector supports injected EVM wallets; the
`NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID` variable is reserved for a later
WalletConnect adapter.

NSQ is planned only as a utility token for product access, API usage,
signal-marketplace features, and reputation-based creator tools. It is not an
investment product and does not provide guaranteed returns, profit sharing,
trading yield, or automatic trading-profit distribution.

NoSlip provides analytics software only. It is not financial advice, investment
advice, or a licensed broker/dealer service. No trading performance is
guaranteed, and users are responsible for their own decisions.

Privacy and security:
- Local-first workflows remain available without wallet login.
- Hosted prediction endpoints may receive the rows or inputs submitted to them.
- Inspect `PREDICTION_API_URL` and `NOSLIP_WEB_APP_URL` before sending data.
- Never upload a private key, seed phrase, or exchange API key.
- Hosted credit/payment mutations require a wallet session or bearer API token.

See `SECURITY_WEB3.md`, `docs/web3_data_flow.md`, and `contracts/README.md`.

---

## Optional Securities Open API Integration

NoSlip includes optional broker adapters for:

- **Toss Securities**: official OAuth2 REST Open API for prices, accounts,
  holdings, order history, previews, and tightly gated library-level orders.
- **Yuanta Securities**: official Windows COM/DLL API through a loopback,
  bearer-authenticated Windows bridge.

Both providers are `disabled` by default. MCP and Telegram provide broker
status, prices, holdings, and order previews only; they cannot submit, modify,
or cancel an order. Yuanta live order submission is not implemented. Toss live
submission requires three explicit safety gates and should remain disabled
until account-owner testing and operational review are complete.

Yuanta's vendor module is Windows-only. macOS/Linux hosts must use an SSH tunnel
to the loopback Windows bridge and must never expose the bridge port directly.
Broker passwords, certificate passwords, client secrets, and account passwords
must not be uploaded to the web app, Telegram, MCP, or source control.

See `docs/broker_open_api.md` and `SECURITY_BROKER.md`.

NoSlip is not financial advice, investment advice, or a licensed broker/dealer
service. No trading performance is guaranteed.

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

### Method 1: Installing Directly via Terminal Shell (Recommended)
You can install our plugin directly from your terminal using `claude` CLI commands without starting an interactive session first:

```bash
# 1. Add our repository as a plugin marketplace
claude plugin marketplace add Futuremine97/noslip_quant_bot

# 2. Install the plugin from our marketplace
claude plugin install noslip-quant@noslip-marketplace
```

### Method 2: Installing inside an Active Claude Code Session
If you are already inside an active Claude Code CLI session (chat interface), run these slash commands:

```text
# 1. Add our repository as a plugin marketplace
/plugin marketplace add Futuremine97/noslip_quant_bot

# 2. Install the plugin from our marketplace
/plugin install noslip-quant@noslip-marketplace
```

### Method 3: Local Development Loading
If you clone the repository locally, you can load the plugin directly during startup:

```bash
# 1. Clone the repository
git clone https://github.com/Futuremine97/noslip_quant_bot.git
cd noslip_quant_bot

# 2. Start Claude Code with our local plugin directory
claude --plugin-dir .
```

You can run `/reload-plugins` within the Claude Code session to apply changes to the plugin during local development.

---

## Claude Desktop MCP Server Integration

No Slip includes a **Model Context Protocol (MCP)** JSON-RPC server that exposes our quant tools directly to your Claude Desktop AI assistant.

### MCP Tools Provided
1. `analyze_ticker`: Prompts the 6-Agent Consensus engine to run a real-time evaluation.
2. `run_league_tournament`: Executes the 60-day backtest tournament leaderboard.
3. `get_credit_balance`: Reads an off-chain credit account and recent ledger entries.
4. `estimate_feature_cost`: Reads server-defined premium feature pricing.
5. `create_credit_payment_intent`: Prepares a user-signed Base payment flow.
6. `confirm_credit_payment`: Requests backend verification; it never signs a transaction.
7. `check_premium_access`: Checks balance without consuming credits.
8. `get_broker_status`: Reads local Toss/Yuanta integration readiness.
9. `get_broker_prices`: Reads configured broker price data.
10. `get_broker_holdings`: Reads configured account holdings.
11. `prepare_broker_order`: Validates an order preview without submitting it.

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

Run the setup commands from the repository root. If your shell is currently in
`services/`, `services/trader/`, or another repository subdirectory, first run:

```bash
cd "$(git rev-parse --show-toplevel)"
```

### 1. Install Node Dependencies (Next.js)
```bash
npm install
```

### 2. Create Python Environment & Install Core Packages
Python 3.10 or newer is recommended. The macOS system Python 3.9 may still run
the current services, but some upstream Google packages emit end-of-life
warnings.

```bash
python3 -m venv services/trader/.venv
services/trader/.venv/bin/python -m pip install -r services/trader/requirements.txt
```

### 3. Environment Variables Config (`.env`)
Create a `.env` file in the root directory. Required parameters:
```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_allowed_chat_ids (comma-separated)
GEMINI_API_KEY=your_google_ai_studio_key
NEXT_PUBLIC_TOSS_CLIENT_KEY=your_toss_payments_client_key
TOSS_SECRET_KEY=your_toss_payments_secret_key
PREDICTION_API_URL=http://localhost:8000
PREDICTION_API_TOKEN=
NEXT_PUBLIC_BASE_CHAIN_ID=8453
NEXT_PUBLIC_BASE_SEPOLIA_CHAIN_ID=84532
NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID=
NOSLIP_PAYMENT_MODE=mock
NOSLIP_CHAIN_MODE=base-sepolia
NOSLIP_ALLOW_BASE_MAINNET=false
NOSLIP_SESSION_SECRET=replace_with_at_least_32_random_characters
NOSLIP_API_TOKEN=replace_with_a_shared_hosted_api_token
NOSLIP_WEB_APP_URL=http://localhost:3000
NOSLIP_DASHBOARD_URL=http://localhost:3000
TOSS_SECURITIES_MODE=disabled
TOSS_SECURITIES_CLIENT_ID=
TOSS_SECURITIES_CLIENT_SECRET=
TOSS_SECURITIES_ACCOUNT_SEQ=
TOSS_SECURITIES_ALLOW_LIVE_ORDERS=false
YUANTA_SECURITIES_MODE=disabled
YUANTA_BRIDGE_URL=http://127.0.0.1:8765
YUANTA_BRIDGE_TOKEN=replace_with_at_least_32_random_characters
YUANTA_BRIDGE_DRIVER=mock
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
- **Yuanta local mock/Windows bridge**:
  ```bash
  services/trader/.venv/bin/python -m services.trader.brokers.yuanta_bridge
  ```

### 5. Tests

```bash
# Securities adapter tests; no live broker calls
services/trader/.venv/bin/python -m unittest discover -s services/trader/tests -v

# Credit ledger, payment intent, and feature-gate tests
npm test

# Hardhat 3 / OpenZeppelin contract dependencies
cd contracts
npm install
npm run compile
npm test
```

Local contract deployment:

```bash
# Terminal 1
cd contracts
npm run node

# Terminal 2
cd contracts
npm run deploy:local
```

Base Sepolia only:

```bash
cd contracts
npx hardhat keystore set BASE_SEPOLIA_RPC_URL
npx hardhat keystore set BASE_SEPOLIA_PRIVATE_KEY
npm run deploy:base-sepolia
```

Do not deploy these draft contracts to Base mainnet without independent legal,
security, and operational review.

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


======================

If this project helpful to you, please leave me a star 


======================
