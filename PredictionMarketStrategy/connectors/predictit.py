import hashlib
import time
from datetime import datetime
from typing import Optional
import httpx

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from models import Market

_BASE = "https://www.predictit.org/api/marketdata/all/"
_CACHE_TTL = 120  # seconds — two poll intervals to avoid rate limiting

_cache: dict = {"data": None, "fetched_at": 0.0}


def _normalize(title: str) -> str:
    import re
    t = title.lower()
    t = re.sub(r"\bwill\b", "", t)
    t = re.sub(r"[^a-z0-9\s]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _canonical_id(normalized: str) -> str:
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


class PredictItConnector:
    platform = "predictit"

    async def _fetch_raw(self) -> Optional[dict]:
        now = time.time()
        if _cache["data"] and (now - _cache["fetched_at"]) < _CACHE_TTL:
            return _cache["data"]
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(_BASE)
                r.raise_for_status()
                data = r.json()
                _cache["data"] = data
                _cache["fetched_at"] = now
                return data
        except Exception as e:
            print(f"[PredictIt] fetch error: {e}")
            return _cache["data"]

    async def get_markets(self) -> list:
        raw = await self._fetch_raw()
        if not raw:
            return []
        markets = []
        for m in raw.get("markets", []):
            # Each market has multiple contracts (one per candidate/option)
            # For binary YES/NO markets, pick the YES contract
            contracts = m.get("contracts", [])
            yes_contract = next(
                (c for c in contracts if c.get("name", "").upper() in ("YES", "Y")),
                contracts[0] if contracts else None,
            )
            if not yes_contract:
                continue
            yes_price = yes_contract.get("bestBuyYesCost") or yes_contract.get("lastTradePrice") or 0.0
            if yes_price < 0.01 or yes_price > 0.99:
                continue
            title = m.get("name", "")
            norm = _normalize(title)
            markets.append(Market(
                platform=self.platform,
                platform_id=str(m.get("id", "")),
                canonical_id=_canonical_id(norm),
                title=title,
                normalized_title=norm,
                yes_price=float(yes_price),
                no_price=round(1.0 - float(yes_price), 4),
                category=m.get("shortName"),
                volume_24h=None,
                closes_at=None,
                fetched_at=datetime.utcnow(),
            ))
        return markets

    async def get_market_price(self, market_id: str) -> Optional[float]:
        raw = await self._fetch_raw()
        if not raw:
            return None
        for m in raw.get("markets", []):
            if str(m.get("id")) == market_id:
                contracts = m.get("contracts", [])
                yes_contract = next(
                    (c for c in contracts if c.get("name", "").upper() in ("YES", "Y")),
                    contracts[0] if contracts else None,
                )
                if yes_contract:
                    return yes_contract.get("bestBuyYesCost") or yes_contract.get("lastTradePrice")
        return None

    async def place_bet(self, market_id: str, outcome: str, amount: float):
        raise NotImplementedError("PredictIt's API is read-only. Place trades at predictit.org.")
