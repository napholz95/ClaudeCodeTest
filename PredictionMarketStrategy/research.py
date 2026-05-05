import re
from datetime import datetime
from typing import Optional
import httpx

from config import NEWSAPI_KEY, NEWS_LOOKBACK_HOURS
from models import Market, ResearchSignal

_BULL = {"win", "lead", "ahead", "likely", "favored", "approved", "passes",
         "yes", "rise", "gain", "surge", "support", "confirmed", "elected"}
_BEAR = {"lose", "fail", "behind", "unlikely", "rejected", "blocked", "no",
         "drop", "decline", "fall", "oppose", "denied", "lost", "defeated"}


def _extract_topic(title: str) -> str:
    # Take first 6 meaningful words after stripping "Will"
    words = re.sub(r"\bwill\b", "", title, flags=re.IGNORECASE).split()
    words = [w for w in words if len(w) > 2][:6]
    return " ".join(words)


async def _fetch_newsapi(topic: str) -> list:
    if not NEWSAPI_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": topic,
                    "pageSize": 10,
                    "apiKey": NEWSAPI_KEY,
                    "language": "en",
                },
            )
            r.raise_for_status()
            articles = r.json().get("articles", [])
            return [a["title"] for a in articles if a.get("title")]
    except Exception as e:
        print(f"[Research] NewsAPI error: {e}")
        return []


async def _fetch_duckduckgo(topic: str) -> list:
    try:
        async with httpx.AsyncClient(
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
            follow_redirects=True,
        ) as client:
            r = await client.get(
                "https://duckduckgo.com/html/",
                params={"q": f"{topic} prediction market"},
            )
            # Extract link text from search results
            titles = re.findall(r'class="result__a"[^>]*>([^<]+)<', r.text)
            return titles[:10]
    except Exception as e:
        print(f"[Research] DuckDuckGo fallback error: {e}")
        return []


def score_sentiment(headlines: list) -> tuple:
    if not headlines:
        return ("NEUTRAL", 0.0)
    bull_count = 0
    bear_count = 0
    combined = " ".join(headlines).lower()
    for word in combined.split():
        clean = re.sub(r"[^a-z]", "", word)
        if clean in _BULL:
            bull_count += 1
        elif clean in _BEAR:
            bear_count += 1
    total = bull_count + bear_count
    if total == 0:
        return ("NEUTRAL", 0.0)
    score = bull_count / total
    if score > 0.6:
        direction = "BULL"
    elif score < 0.4:
        direction = "BEAR"
    else:
        direction = "NEUTRAL"
    confidence = round(abs(score - 0.5) * 2, 3)
    return (direction, confidence)


class ResearchEngine:
    async def fetch_headlines(self, topic: str) -> list:
        headlines = await _fetch_newsapi(topic)
        if not headlines:
            headlines = await _fetch_duckduckgo(topic)
        return headlines[:10]

    async def analyze_market(self, market: Market) -> ResearchSignal:
        topic = _extract_topic(market.title)
        headlines = await self.fetch_headlines(topic)
        direction, confidence = score_sentiment(headlines)
        return ResearchSignal(
            market_id=market.platform_id,
            platform=market.platform,
            direction=direction,
            confidence=confidence,
            headline_count=len(headlines),
            top_headlines=headlines[:5],
            generated_at=datetime.utcnow(),
        )
