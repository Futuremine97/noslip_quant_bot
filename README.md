# No Slip

No Slip is an AI-assisted swap analysis and execution-planning system for crypto routes.

It combines:

- Jupiter route discovery for live swap paths
- Birdeye OHLCV data for hop-level market context
- Prophet-based multi-resolution forecasting
- a wrapper-agent council that can learn and reweight itself over time
- an execution-risk engine that turns prediction and route quality into concrete execution policy
- optional LLM agents that summarize what the system is seeing in plain language

The current `main` branch is a Next.js application with a Python prediction layer and deployment support for a separate production prediction API.

## What the app does

Given a swap such as `SOL -> WBTC`, the system:

1. Searches tokens and fetches a Jupiter quote / route plan
2. Breaks the route into step-level hops
3. Runs Prophet predictions on each hop
4. Aggregates 10m / 5m / 1m directional signals
5. Applies wrapper agents for conservative filtering and execution gating
6. Calculates an execution risk score
7. Explains the result through UI panels and optional LLM summaries

The goal is not only to show the cheapest route, but also to answer:

- Is this route step bullish, bearish, or better left alone?
- Should the swap execute now, be delayed, be split, or be halted?
- Is the current route likely suffering from poor effective liquidity?
- Are the wrapper agents learning from previous market outcomes?

## Core features

### 1. Route-step prediction

Each hop in a Jupiter route can be analyzed independently.

Examples:

- `SOL -> USDC`
- `USDC -> WBTC`
- `SOL -> BNB`

If Birdeye cannot provide enough OHLCV rows for a route step, the system falls back to synthetic pair construction in Python. This allows pairs such as `SOL -> BNB` to remain predictable even when direct Birdeye history is missing.

Relevant files:

- [app/actions/prediction.ts](/path/to/your/cloned/repository/app/actions/prediction.ts:1)
- [services/trader/pair_fallback.py](/path/to/your/cloned/repository/services/trader/pair_fallback.py:1)
- [services/trader/predict_signal.py](/path/to/your/cloned/repository/services/trader/predict_signal.py:1)
- [services/trader/prediction_api.py](/path/to/your/cloned/repository/services/trader/prediction_api.py:1)

### 2. Multi-resolution Prophet runtime

The prediction stack uses multiple cadence views and aggregates them into a final decision:

- `10min` direction
- `5min` direction
- `1min` direction
- timing projections for local lows and highs

The UI exposes:

- direction vote
- direction strength
- target price
- target timestamp
- time-to-below-current

Relevant files:

- [services/trader/main.py](/path/to/your/cloned/repository/services/trader/main.py:1)
- [services/trader/runtime.py](/path/to/your/cloned/repository/services/trader/runtime.py:1)
- [services/trader/prophet_agents.py](/path/to/your/cloned/repository/services/trader/prophet_agents.py:1)

### 3. Wrapper council with learned weights

Above the raw Prophet output, No Slip runs a wrapper-agent layer:

- `final_action_agent`
- `time_to_below_agent`
- `conservative_gold_agent`
- `execution_cost_agent`

These agents are aggregated into a wrapper council that decides whether the base signal should be trusted strongly enough for execution planning.

The wrapper system now supports:

- base weights
- learned weights
- persisted SQLite storage
- automatic reweighting from later realized market movement

This means the wrapper layer no longer starts from scratch on every request.

Relevant files:

- [services/llm/config.py](/path/to/your/cloned/repository/services/llm/config.py:1)
- [services/llm/debate.py](/path/to/your/cloned/repository/services/llm/debate.py:1)
- [services/llm/pipeline.py](/path/to/your/cloned/repository/services/llm/pipeline.py:1)
- [services/llm/weight_store.py](/path/to/your/cloned/repository/services/llm/weight_store.py:1)

### 4. Execution risk engine

The UI includes an execution-risk gauge inspired by:

- entropy / uncertainty
- concentration or whale-style penalties
- apparent vs effective depth
- wrapper veto behavior

The execution engine turns those inputs into a recommended mode such as:

- market execution
- split / TWAP-style execution
- private routing preference
- halt

Relevant files:

- [services/risk/execution-risk.ts](/path/to/your/cloned/repository/services/risk/execution-risk.ts:1)
- [app/page.tsx](/path/to/your/cloned/repository/app/page.tsx:1)

### 5. LLM synthesis

The app also includes optional LLM summaries for:

- buy rationale
- wait rationale
- route guidance in one sentence
- next-action date / time window

If Gemini is not configured, the app falls back to deterministic text so the UI still remains useful.

Relevant files:

- [services/llm/agents.ts](/path/to/your/cloned/repository/services/llm/agents.ts:1)
- [services/llm/pipeline.ts](/path/to/your/cloned/repository/services/llm/pipeline.ts:1)
- [app/api/analyze/route.ts](/path/to/your/cloned/repository/app/api/analyze/route.ts:1)

### 6. Champion Prophet trainer

The repo also includes a trainer that scores candidate Prophet configurations and persists a champion model for later reuse and export.

Relevant file:

- [services/trader/champion_prophet.py](/path/to/your/cloned/repository/services/trader/champion_prophet.py:1)
- [services/trader/sp500_champion_batch.py](/path/to/your/cloned/repository/services/trader/sp500_champion_batch.py:1)

Example batch run for the full S&P500 close matrix:

```bash
cd /path/to/your/cloned/repository
services/trader/.venv/bin/python services/trader/sp500_champion_batch.py \
  --close-matrix data/sp500/sp500_close_daily.csv \
  --folds 3
```

This trains `direction`, `low`, and `high` champions for the daily S&P500 rules (`20D`, `5D`, `1D`) and writes a batch report JSON under `services/trader/exports/`.

## Architecture

### Frontend

- Next.js App Router
- React 19
- route-step visualization and agent graph
- wrapper and risk introspection panels

Key files:

- [app/page.tsx](/path/to/your/cloned/repository/app/page.tsx:1)
- [app/globals.css](/path/to/your/cloned/repository/app/globals.css:1)

### Node / server actions

These are used for:

- token search
- Jupiter route calls
- Birdeye fetches
- local Python invocation when running on a machine with Python available
- forwarding to a remote prediction API in production

Key files:

- [app/actions/tokens.ts](/path/to/your/cloned/repository/app/actions/tokens.ts:1)
- [app/actions/jupiter.ts](/path/to/your/cloned/repository/app/actions/jupiter.ts:1)
- [app/actions/birdeye.ts](/path/to/your/cloned/repository/app/actions/birdeye.ts:1)
- [app/actions/prediction.ts](/path/to/your/cloned/repository/app/actions/prediction.ts:1)

### Python prediction service

The Python side is responsible for:

- preparing raw data
- running Prophet inference
- pair-fallback construction
- wrapper-weight persistence and reweighting
- serving `/predict-step` in production

Key files:

- [services/trader/prediction_api.py](/path/to/your/cloned/repository/services/trader/prediction_api.py:1)
- [services/trader/predict_signal.py](/path/to/your/cloned/repository/services/trader/predict_signal.py:1)
- [services/trader/main.py](/path/to/your/cloned/repository/services/trader/main.py:1)

## Local development

### 1. Install Node dependencies

```bash
npm install
```

### 2. Create Python environment

```bash
python3 -m venv services/trader/.venv
services/trader/.venv/bin/pip install -r services/trader/requirements.txt
```

### 3. Configure environment variables

Create `.env` from `.env.example`.

Important values:

```env
JUPITER_API_KEY=your_jupiter_api_key
BIRDEYE_API_KEY=your_birdeye_api_key
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.5-flash
PREDICTION_API_URL=
PREDICTION_API_TOKEN=
PREDICTION_PYTHON_BIN=
PORT=8787
CORS_ORIGIN=http://localhost:3000
```

Reference file:

- [.env.example](/path/to/your/cloned/repository/.env.example:1)

### 4. Run the app

Frontend:

```bash
npm run dev
```

Optional local proxy server:

```bash
npm run server
```

### 5. Run checks

Build:

```bash
npm run build
```

Tests:

```bash
npm test
```

## Production deployment

### Recommended shape

Production works best with:

- Vercel for the Next.js app
- a separate Python prediction API for Prophet inference

This avoids trying to spawn local Python directly inside a Vercel runtime.

### Prediction API

Run the Python prediction API with:

```bash
services/trader/.venv/bin/uvicorn services.trader.prediction_api:app --host 0.0.0.0 --port 8000
```

Required environment on the prediction server:

```env
PREDICTION_API_TOKEN=your_shared_secret
MPLCONFIGDIR=/tmp/no-slip-matplotlib
```

### Vercel environment

Set:

```env
PREDICTION_API_URL=https://your-prediction-service.example.com
PREDICTION_API_TOKEN=your_shared_secret
```

### Ubuntu / systemd example

Files included in the repo:

- [deploy/prediction-api.env.example](/path/to/your/cloned/repository/deploy/prediction-api.env.example:1)
- [deploy/prediction-api.service.example](/path/to/your/cloned/repository/deploy/prediction-api.service.example:1)

Example flow:

```bash
git clone <repo> /root/no-slip
cd /root/no-slip
python3 -m venv services/trader/.venv
services/trader/.venv/bin/pip install -r services/trader/requirements.txt
sudo mkdir -p /etc/no-slip
sudo cp deploy/prediction-api.env.example /etc/no-slip/prediction-api.env
sudo cp deploy/prediction-api.service.example /etc/systemd/system/no-slip-prediction.service
sudo systemctl daemon-reload
sudo systemctl enable --now no-slip-prediction
```

## Data sources and fallbacks

Primary live sources:

- Jupiter for route discovery and swap path structure
- Birdeye for OHLCV

Fallbacks:

- local CSV files in `data/historical`
- `yfinance` for supported assets
- synthetic pair construction for missing route-step OHLCV

This fallback stack is especially important for pairs where direct route-step history is sparse.

## What the UI shows

The UI is designed to make the internal AI stack inspectable rather than opaque.

It includes:

- route-step summaries
- direction 10m / 5m / 1m agents
- low / high timing agents
- wrapper council
- base weight vs learned weight
- wrapper feedback count
- route-leg detail
- execution risk gauge
- LLM synthesis panel

The goal is to let users see not just the final answer, but how the system arrived there.

## Repository guide

### `app/`

Next.js frontend, server actions, and API routes.

### `services/trader/`

Prophet runtime, pair fallback logic, prediction API, champion trainer, and related utilities.

### `services/llm/`

Both the TypeScript LLM summarization agents and the Python wrapper-agent council logic.

### `services/risk/`

Execution-risk modeling.

### `deploy/`

Systemd and environment templates for the production prediction service.

## Notes

- `Weight source: default` means the wrapper council has not yet accumulated enough realized feedback for that symbol.
- `Weight source: learned` means previous predictions for that symbol have already been scored and reused.
- `Feedback count` is symbol-specific, not global.
- Route-step prediction can still work when Birdeye returns insufficient rows, as long as a fallback path can be constructed.

## Summary

No Slip is not only a quote viewer.

It is a layered execution intelligence system:

- route discovery
- hop-level forecasting
- wrapper-based decision control
- adaptive wrapper reweighting
- execution-risk scoring
- optional LLM explanation

That makes it suitable for both operator-facing analysis and future automation / execution tooling.
