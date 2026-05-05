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

# Load private key once at import time
_private_key = None

def _load_private_key():
    global _private_key
    if _private_key is not None:
        return _private_key
    key_path = KALSHI_PRIVATE_KEY_PATH
    # Resolve relative path from the project root (parent of connectors/)
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


class KalshiConnector:
    platform = "kalshi"

    async def get_markets(self) -> list:
        markets = []
        cursor = None
        pages = 0
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                while pages < 5:  # cap at 5 pages to avoid timeout
                    params = {"status": "open", "limit": 200}
                    if cursor:
                        params["cursor"] = cursor
                    path = "/trade-api/v2/markets"
                    r = await client.get(
                        f"{_BASE}/markets",
                        params=params,
                        headers=_auth_headers("GET", path),
                    )
                    r.raise_for_status()
                    data = r.json()
                    pages += 1
                    for m in data.get("markets", []):
                        title = m.get("title", "")
                        # Skip multi-event parlay bundles (commas indicate bundled conditions)
                        if "," in title:
                            continue
                        # Field names changed in API — now use _dollars suffix
                        yes_bid = float(m.get("yes_bid_dollars") or 0)
                        yes_ask = float(m.get("yes_ask_dollars") or 0)
                        last_price = float(m.get("last_price_dollars") or 0)
                        if yes_bid > 0 and yes_ask > 0:
                            yes_price = (yes_bid + yes_ask) / 2
                        elif yes_ask > 0:
                            yes_price = yes_ask
                        elif last_price > 0:
                            yes_price = last_price
                        else:
                            # Infer from no_bid (no_bid ≈ 1 - yes_ask in liquid markets)
                            no_bid = float(m.get("no_bid_dollars") or 0)
                            if no_bid > 0:
                                yes_price = round(1.0 - no_bid, 4)
                            else:
                                continue
                        if yes_price < 0.01 or yes_price > 0.99:
                            continue
                        norm = _normalize(title)
                        markets.append(Market(
                            platform=self.platform,
                            platform_id=m.get("ticker", ""),
                            canonical_id=_canonical_id(norm),
                            title=title,
                            normalized_title=norm,
                            yes_price=round(yes_price, 4),
                            no_price=round(1.0 - yes_price, 4),
                            category=m.get("category"),
                            volume_24h=float(m.get("volume_24h_fp") or m.get("volume_fp") or 0),
                            fetched_at=datetime.utcnow(),
                        ))
                    cursor = data.get("cursor")
                    if not cursor or not data.get("markets"):
                        break
        except Exception as e:
            print(f"[Kalshi] fetch error: {e}")
        print(f"[Kalshi] fetched {len(markets)} usable markets")
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
