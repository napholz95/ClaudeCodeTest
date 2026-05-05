from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


# ── Internal dataclasses (pipeline use) ──────────────────────────────────────

@dataclass
class Market:
    platform: str
    platform_id: str
    canonical_id: str
    title: str
    normalized_title: str
    yes_price: float          # 0.0–1.0
    no_price: float
    category: Optional[str] = None
    volume_24h: Optional[float] = None
    closes_at: Optional[datetime] = None
    fetched_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ArbOpportunity:
    canonical_id: str
    title: str
    platform_a: str
    market_id_a: str          # platform_id of the market on platform_a (for research lookup)
    price_a: float
    platform_b: str
    market_id_b: str          # platform_id of the market on platform_b
    price_b: float
    gross_spread_pct: float   # raw price difference × 100
    net_spread_pct: float     # gross spread minus both platforms' fees
    fee_a_pct: float          # platform_a fee %
    fee_b_pct: float          # platform_b fee %
    recommended_action: str   # human-readable: "Buy YES on A · Buy NO on B"
    liquidity_score: float
    closes_at: Optional[datetime] = None
    detected_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Trade:
    platform: str
    market_id: str
    market_title: str
    outcome: str              # "YES" | "NO"
    amount: float
    entry_price: float
    status: str = "OPEN"
    id: Optional[int] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    notes: str = ""
    opened_at: datetime = field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None


@dataclass
class ResearchSignal:
    market_id: str
    platform: str
    direction: str            # "BULL" | "BEAR" | "NEUTRAL"
    confidence: float
    headline_count: int
    top_headlines: list
    generated_at: datetime = field(default_factory=datetime.utcnow)


# ── Pydantic models (FastAPI I/O) ─────────────────────────────────────────────

class TradeCreate(BaseModel):
    platform: str
    market_id: str
    market_title: str
    outcome: str
    amount: float
    entry_price: float
    notes: str = ""


class TradeResponse(BaseModel):
    id: Optional[int]
    platform: str
    market_id: str
    market_title: str
    outcome: str
    amount: float
    entry_price: float
    exit_price: Optional[float]
    status: str
    pnl: Optional[float]
    notes: str
    opened_at: str
    closed_at: Optional[str]


class ArbOpportunityResponse(BaseModel):
    canonical_id: str
    title: str
    platform_a: str
    market_id_a: str
    price_a: float
    platform_b: str
    market_id_b: str
    price_b: float
    gross_spread_pct: float
    net_spread_pct: float
    fee_a_pct: float
    fee_b_pct: float
    recommended_action: str
    liquidity_score: float
    closes_at: Optional[str] = None
    detected_at: str


class MarketResponse(BaseModel):
    platform: str
    platform_id: str
    title: str
    yes_price: float
    no_price: float
    category: Optional[str]
    volume_24h: Optional[float]
    closes_at: Optional[str]


class ResearchSignalResponse(BaseModel):
    market_id: str
    platform: str
    direction: str
    confidence: float
    headline_count: int
    top_headlines: list
    generated_at: str
