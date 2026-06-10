#!/bin/bash

# Get the script's directory (workspace root)
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=== [$(date)] S&P500 & Crypto Daily Job Started ==="

# 1. Update the S&P500 Information Map
echo "1. Building S&P500 Information Map..."
./services/trader/.venv/bin/python services/trader/sp500_information_map.py --force-refresh

# 2. Send the S&P500 report to WhatsApp and Telegram
echo "2. Sending S&P500 report to WhatsApp/Telegram..."
./services/trader/.venv/bin/python services/trader/notifier.py

# 3. Run S&P500 daily reinforcement learning weight updates
echo "3. Updating S&P500 AI model weights via Reinforcement Learning..."
./services/trader/.venv/bin/python services/trader/daily_reinforcement.py

# 4. Re-optimize Whale/Pump parameters based on latest data
echo "4. Re-optimizing Whale/Pump parameters..."
./services/trader/.venv/bin/python services/trader/optimize_whale_parameters.py

# 5. Analyze Crypto (BTC, ETH, SOL) and send report to Telegram
echo "5. Analyzing Crypto (BTC, ETH, SOL) and sending report to Telegram..."
./services/trader/.venv/bin/python services/trader/crypto_notifier.py

# 6. Run Multi-Agent Consensus Forum and send report to Telegram
echo "6. Running Multi-Agent Consensus Forum and sending report to Telegram..."
./services/trader/.venv/bin/python services/trader/multi_agent_consensus.py

# 7. Dynamically crawl YouTube/Google Trends and broadcast to Telegram
echo "7. Running Dynamic YouTube/Google Trends Crawler and sending report..."
./services/trader/.venv/bin/python services/trader/dynamic_youtube_trends.py

# 8. Learn sector correlations and broadcast the daily recommended-sector message
echo "8. Learning sector correlations and sending recommended-sector report..."
./services/trader/.venv/bin/python services/trader/sector_correlation.py

# 9. Train GICS sector orbits and update trajectory models
echo "9. Training sector orbits and updating trajectory models..."
./services/trader/.venv/bin/python services/trader/sector_orbit_learner.py --train

# 10. Generate S&P500 Infomap plot and publish report to Instagram
echo "10. Generating S&P500 Infomap and publishing to Instagram..."
./services/trader/.venv/bin/python services/trader/instagram_publisher.py

# 11. Generate daily market card news (5 cards) -> Telegram album + Instagram carousel
echo "11. Generating daily market card news and broadcasting..."
./services/trader/.venv/bin/python services/trader/daily_card_news.py --instagram

echo "=== [$(date)] S&P500 & Crypto Daily Job Completed ==="
