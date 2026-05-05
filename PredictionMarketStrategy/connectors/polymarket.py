import asyncio
import hashlib
import json
import time
from datetime import datetime
from typing import Optional
import httpx

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from models import Market

_GAMMA = "https://gamma-api.polymarket.com"
_CACHE_TTL = 300  # 5 minutes
_TARGET_MARKETS = 500

_cache: dict = {"markets": [], "fetched_at": 0.0}


def _normalize(title: str) -> str:
    import re
    t = title.lower()
    t = re.sub(r"\bwill\b", "", t)
    t = re.sub(r"[^a-z0-9\s]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _canonical_id(normalized: str) -> str:
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _parse_market(m: dict) -> Optional[Market]:
    outcome_prices = m.get("outcomePrices")
    if not outcome_prices:
        return None
    try:
        if isinstance(outcome_prices, str):
            outcome_prices = json.loads(outcome_prices)
        yes_price = float(outcome_prices[0])
    except (IndexError, TypeError, ValueError):
        return None
    if yes_price < 0.01 or yes_price > 0.99:
        return None
    title = m.get("question", "")
    if not title:
        return None
    norm = _normalize(title)
    events = m.get("events") or []
    category = events[0].get("slug") if events else m.get("groupItemTitle")
    return Market(
        platform="polymarket",
        platform_id=str(m.get("conditionId") or m.get("id", "")),
        canonical_id=_canonical_id(norm),
        title=title,
        normalized_title=norm,
        yes_price=round(yes_price, 4),
        no_price=round(1.0 - yes_price, 4),
        category=category,
        volume_24h=float(m.get("volume24hr") or m.get("volume") or 0),
        fetched_at=datetime.utcnow(),
    )


async def _fetch_page(client: httpx.AsyncClient, offset: int, limit: int = 100) -> list:
    try:
        r = await client.get(
            f"{_GAMMA}/markets",
            params={
                "active": "true",
                "closed": "false",
                "limit": limit,
                "offset": offset,
                "order": "volume24hr",
                "ascending": "false",
            },
        )
        r.raise_for_status()
        return r.json() or []
    except Exception as e:
        print(f"[Polymarket] offset={offset} fetch error: {e}")
        return []


class PolymarketConnector:
    platform = "polymarket"

    async def get_markets(self) -> list:
        now = time.time()
        if _cache["markets"] and (now - _cache["fetched_at"]) < _CACHE_TTL:
            return _cache["markets"]

        raw = []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                # Fetch up to _TARGET_MARKETS markets using parallel paginated requests
                offsets = list(range(0, _TARGET_MARKETS, 100))
                pages = await asyncio.gather(
                    *[_fetch_page(client, offset, limit=100) for offset in offsets],
                    return_exceptions=True,
                )
                seen_ids: set = set()
                for page in pages:
                    if isinstance(page, Exception):
                        continue
                    for m in page:
                        mid = str(m.get("conditionId") or m.get("id", ""))
                        if mid and mid not in seen_ids:
                            seen_ids.add(mid)
                            raw.append(m)
        except Exception as e:
            print(f"[Polymarket] fetch error: {e}")
            return _cache["markets"]

        markets = []
        for m in raw:
            parsed = _parse_market(m)
            if parsed:
                markets.append(parsed)

        _cache["markets"] = markets
        _cache["fetched_at"] = now

        # Log category breakdown
        from collections import Counter
        cats = Counter(m.category for m in markets if m.category)
        top_cats = ", ".join(f"{c}:{n}" for c, n in cats.most_common(5))
        print(f"[Polymarket] fetched {len(markets)} markets (top categories: {top_cats})")
        return markets

    async def get_market_price(self, market_id: str) -> Optional[float]:
        for m in _cache["markets"]:
            if m.platform_id == market_id:
                return m.yes_price
        return None

    async def place_bet(self, market_id: str, outcome: str, amount: float):
        raise NotImplementedError("Polymarket trading requires a Polygon wallet. Place trades at polymarket.com.")
