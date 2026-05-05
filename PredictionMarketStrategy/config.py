import os
from dotenv import load_dotenv

load_dotenv()

KALSHI_API_KEY: str = os.getenv("KALSHI_API_KEY", "")
KALSHI_PRIVATE_KEY_PATH: str = os.getenv("KALSHI_PRIVATE_KEY_PATH", "kalshi_private_key.pem")
POLYMARKET_API_KEY: str = os.getenv("POLYMARKET_API_KEY", "")
NEWSAPI_KEY: str = os.getenv("NEWSAPI_KEY", "")

POLL_INTERVAL_SECONDS: int = 60
MIN_SPREAD_PCT: float = 3.0
MIN_NET_SPREAD_PCT: float = 1.0   # minimum spread AFTER fees — raise to filter more aggressively
MIN_LIQUIDITY_USD: float = 100.0

HTTP_PORT: int = 8001
DB_PATH: str = "trades.db"

NEWS_LOOKBACK_HOURS: int = 48
SENTIMENT_MATCH_THRESHOLD: float = 0.5

# Platform fees as a fraction of winnings (both sides combined will be subtracted from gross spread)
# These are charged by the platform when a contract resolves in your favor.
# PredictIt: 10% fee on profits + 5% withdrawal fee on net profits ≈ 14.5% effective
PLATFORM_FEES: dict = {
    "kalshi":     0.07,   # 7% of net profits
    "polymarket": 0.02,   # 2% of trade value on resolution
    "manifold":   0.0,    # play money, no fees
}
