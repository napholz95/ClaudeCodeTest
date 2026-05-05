import hashlib
from datetime import datetime
from typing import Optional
import httpx

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from models import Market

_BASE = "https://api.manifold.markets/v0"


def _normalize(title: str) -> str:
    import re
    t = title.lower()
    t = re.sub(r"\bwill\b", "", t)
    t = re.sub(r"[^a-z0-9\s]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _canonical_id(normalized: str) -> str:
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


class ManifoldConnector:
    platform = "manifold"

    async def get_markets(self) -> list:
        markets = []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                # Use search-markets which supports sort by 24-hour-vol
                r = await client.get(
                    f"{_BASE}/search-markets",
                    params={
                        "term": "",
                        "limit": 500,
                        "sort": "24-hour-vol",
                        "filter": "open",
                        "contractType": "BINARY",
                    },
                )
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            print(f"[Manifold] fetch error: {e}")
            return []

        for m in data:
            if m.get("isResolved"):
                continue
            prob = m.get("probability")
            if prob is None or float(prob) < 0.01 or float(prob) > 0.99:
                continue
            title = m.get("question", "")
            norm = _normalize(title)
            category = None
            groups = m.get("groupSlugs") or []
            if groups:
                category = groups[0]

            closes_at = None
            close_time_ms = m.get("closeTime")
            if close_time_ms:
                try:
                    closes_at = datetime.utcfromtimestamp(int(close_time_ms) / 1000)
                except Exception:
                    pass

            markets.append(Market(
                platform=self.platform,
                platform_id=m.get("id", ""),
                canonical_id=_canonical_id(norm),
                title=title,
                normalized_title=norm,
                yes_price=round(float(prob), 4),
                no_price=round(1.0 - float(prob), 4),
                category=category,
                volume_24h=float(m.get("volume24Hours", 0) or 0),
                closes_at=closes_at,
                fetched_at=datetime.utcnow(),
            ))
        return markets

    async def get_market_price(self, market_id: str) -> Optional[float]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{_BASE}/market/{market_id}")
                r.raise_for_status()
                return float(r.json().get("probability", 0))
        except Exception as e:
            print(f"[Manifold] price fetch error: {e}")
            return None

    async def place_bet(self, market_id: str, outcome: str, amount: float):
        raise NotImplementedError("Manifold uses play money (mana). Place bets at manifold.markets.")
