import os
from dotenv import load_dotenv

load_dotenv()

PS3838_USERNAME = os.getenv("PS3838_USERNAME")
PS3838_PASSWORD = os.getenv("PS3838_PASSWORD")
PS3838_BASE_URL = os.getenv("PS3838_BASE_URL", "https://api.ps3838.com")

KALSHI_KEY_ID = os.getenv("KALSHI_KEY_ID")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "./kalshi_private.pem")
KALSHI_BASE_URL = os.getenv("KALSHI_BASE_URL", "https://api.elections.kalshi.com")
KALSHI_WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"

# Sports to monitor (PS3838 sport IDs)
# 29: Soccer, 4: Basketball, 33: Tennis, 3: Baseball
SPORTS_TO_MONITOR = [29, 4, 33, 3]

# Minimum arbitrage profit % to consider
MIN_ARB_PROFIT_PCT = 0.5

# Poll interval for PS3838 in seconds
PS3838_POLL_INTERVAL = 5

# DRY_RUN=true  — только логируем, ничего не ставим
DRY_RUN = os.getenv("DRY_RUN", "true").lower() != "false"

BANKROLL = float(os.getenv("BANKROLL", "1000"))
MAX_LEG_STAKE = float(os.getenv("MAX_LEG_STAKE", "2"))
