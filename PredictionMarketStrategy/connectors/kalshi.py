import base64
import hashlib
import os
import time
from datetime import datetime
from typing import Optional
import httpx

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from models import Market
from config import KALSHI_API_KEY, KALSHI_PRIVATE_KEY_PATH

_BASE = "https://api.elections.kalshi.com/trade-api/v2"
_CACHE_TTL = 300  # 5 minutes

_cache: dict = {"markets": [], "fetched_at": 0.0}
_private_key = None


def _load_private_key():
    global _private_key
    if _private_key is not None:
        return _private_key
    key_path = KALSHI_PRIVATE_KEY_PATH
    if not os.path.isabs(key_path):
        key_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), key_path)
    if not os.path.exists(key_path):
        print(f"[Kalshi] Private key not found at {key_path}")
        return None
    with open(key_path, "rb") as f:
        _private_key = serialization.load_pem_private_key(f.read(), password=None)
    return _private_key


def _normalize(title: str) -> str:
    import re
    t = title.lower()
    t = re.sub(r"\bwill\b", "", t)
    t = re.sub(r"[^a-z0-9\s]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _canonical_id(normalized: str) -> str:
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _auth_headers(method: str, path: str) -> dict:
    if not KALSHI_API_KEY:
        return {}
    key = _load_private_key()
    if key is None:
        return {}
    timestamp = str(int(time.time() * 1000))
    message = f"{timestamp}{method.upper()}{path}".encode()
    signature = key.sign(
        message,
        asym_padding.PSS(
            mgf=asym_padding.MGF1(hashes.SHA256()),
            salt_length=asym_padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    sig_b64 = base64.b64encode(signature).decode()
    return {
        "KALSHI-ACCESS-KEY": KALSHI_API_KEY,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }


def _parse_market(m: dict) -> Optional[Market]:
    title = m.get("title", "")
    if not title or "," in title:
        return None

    yes_bid = float(m.get("yes_bid_dollars") or 0)
    yes_ask = float(m.get("yes_ask_dollars") or 0)
    last_price = float(m.get("last_price_dollars") or 0)

    if yes_bid > 0 and yes_ask > 0:
        yes_price = (yes_bid + yes_ask) / 2
    elif yes_ask > 0 and yes_ask < 1:
        yes_price = yes_ask
    elif last_price > 0:
        yes_price = last_price
    else:
        no_bid = float(m.get("no_bid_dollars") or 0)
        if 0 < no_bid < 1:
            yes_price = round(1.0 - no_bid, 4)
        else:
            return None

    if yes_price < 0.01 or yes_price > 0.99:
        return None

    norm = _normalize(title)

    closes_at = None
    close_time = m.get("close_time") or m.get("expiration_time")
    if close_time:
        try:
            closes_at = datetime.fromisoformat(close_time.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass

    return Market(
        platform="kalshi",
        platform_id=m.get("ticker", ""),
        canonical_id=_canonical_id(norm),
        title=title,
        normalized_title=norm,
        yes_price=round(yes_price, 4),
        no_price=round(1.0 - yes_price, 4),
        category=m.get("category"),
        volume_24h=float(m.get("volume_24h_fp") or m.get("volume_fp") or 0),
        closes_at=closes_at,
        fetched_at=datetime.utcnow(),
    )


class KalshiConnector:
    platform = "kalshi"

    async def get_markets(self) -> list:
        import time
        now = time.time()
        if _cache["markets"] and (now - _cache["fetched_at"]) < _CACHE_TTL:
            return _cache["markets"]

        markets = []
        cursor = None
        pages = 0
        event_count = 0
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                while pages < 10:
                    params = {"limit": 100, "with_nested_markets": "true"}
                    if cursor:
                        params["cursor"] = cursor
                    path = "/trade-api/v2/events"
                    r = await client.get(
                        f"{_BASE}/events",
                        params=params,
                        headers=_auth_headers("GET", path),
                    )
                    r.raise_for_status()
                    data = r.json()
                    pages += 1
                    events = data.get("events", [])
                    event_count += len(events)
                    for event in events:
                        for m in event.get("markets", []):
                            parsed = _parse_market(m)
                            if parsed:
                                markets.append(parsed)
                    cursor = data.get("cursor")
                    if not cursor or not events:
                        break
        except Exception as e:
            print(f"[Kalshi] fetch error: {e}")
        _cache["markets"] = markets
        _cache["fetched_at"] = time.time()
        print(f"[Kalshi] fetched {len(markets)} markets from {event_count} events ({pages} pages)")
        return markets

    async def get_market_price(self, market_id: str) -> Optional[float]:
        path = f"/trade-api/v2/markets/{market_id}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{_BASE}/markets/{market_id}",
                    headers=_auth_headers("GET", path),
                )
                r.raise_for_status()
                m = r.json().get("market", {})
                yes_bid = float(m.get("yes_bid_dollars") or 0)
                yes_ask = float(m.get("yes_ask_dollars") or 0)
                if yes_bid > 0 and yes_ask > 0:
                    return (yes_bid + yes_ask) / 2
                return float(m.get("last_price_dollars") or 0) or None
        except Exception as e:
            print(f"[Kalshi] price fetch error: {e}")
            return None

    async def place_bet(self, market_id: str, outcome: str, amount: float):
        raise NotImplementedError("Kalshi trading not yet wired — place trades at kalshi.com.")
