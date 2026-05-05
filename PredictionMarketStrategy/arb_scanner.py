import asyncio
import hashlib
import re
from datetime import datetime
from math import log1p
from typing import Callable, Optional

from rapidfuzz import process, fuzz

from config import MIN_SPREAD_PCT, MIN_NET_SPREAD_PCT, POLL_INTERVAL_SECONDS, PLATFORM_FEES
from models import ArbOpportunity, Market

# Date tokens that must match exactly to avoid false positives
_DATE_PATTERN = re.compile(
    r"\b(\d{4}|q[1-4]|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b"
)


def normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"\bwill\b", "", t)
    t = re.sub(r"[^a-z0-9\s]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def compute_canonical_id(normalized: str) -> str:
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _date_tokens(normalized: str) -> frozenset:
    return frozenset(_DATE_PATTERN.findall(normalized))


def match_markets(all_markets: list) -> dict:
    """Groups markets by canonical_id, then fuzzy-merges close matches.

    Returns dict of {canonical_id: [Market, ...]} where each group has
    at least two different platforms.
    """
    # Pass 1: exact canonical match
    groups: dict[str, list] = {}
    for m in all_markets:
        groups.setdefault(m.canonical_id, []).append(m)

    # Pass 2: fuzzy merge groups with similar normalized titles
    canonical_ids = list(groups.keys())
    title_map = {cid: groups[cid][0].normalized_title for cid in canonical_ids}
    merged: set[str] = set()
    merged_groups: dict[str, list] = {}

    for i, cid_a in enumerate(canonical_ids):
        if cid_a in merged:
            continue
        merged_groups[cid_a] = list(groups[cid_a])
        for cid_b in canonical_ids[i+1:]:
            if cid_b in merged:
                continue
            title_a = title_map[cid_a]
            title_b = title_map[cid_b]
            # Date tokens must match exactly
            if _date_tokens(title_a) != _date_tokens(title_b):
                continue
            score = fuzz.token_sort_ratio(title_a, title_b)
            if score >= 80:
                merged_groups[cid_a].extend(groups[cid_b])
                merged.add(cid_b)

    # Keep only groups that span at least two platforms
    result = {}
    for cid, markets in merged_groups.items():
        platforms = {m.platform for m in markets}
        if len(platforms) >= 2:
            result[cid] = markets
    return result


def _is_aggregate_market(normalized_title: str) -> bool:
    """Detect 'who will win X' aggregate markets — these can't be arb'd against individual-candidate markets."""
    agg_phrases = ["who will win", "who wins", "which party", "which candidate"]
    return any(p in normalized_title for p in agg_phrases)


def compute_spread(market_a: Market, market_b: Market) -> Optional[ArbOpportunity]:
    gross_spread = abs(market_a.yes_price - market_b.yes_price) * 100
    if gross_spread < MIN_SPREAD_PCT:
        return None

    # Filter out false arb: one aggregate "who wins" market matched against an individual "will X win" market
    a_agg = _is_aggregate_market(market_a.normalized_title)
    b_agg = _is_aggregate_market(market_b.normalized_title)
    if a_agg != b_agg:
        return None

    # Calculate fees and net spread
    fee_a = PLATFORM_FEES.get(market_a.platform, 0.0)
    fee_b = PLATFORM_FEES.get(market_b.platform, 0.0)
    # Fee cost: each platform charges fee_x on winning $1, so total fee drag = fee_a + fee_b as % of $1 payout
    net_spread = gross_spread - (fee_a + fee_b) * 100
    if net_spread < MIN_NET_SPREAD_PCT:
        return None

    # Determine which side to buy YES on (the cheaper one) and buy NO on (the more expensive one)
    # Buying YES on A + Buying NO on B = guaranteed $1 payout regardless of outcome
    if market_a.yes_price < market_b.yes_price:
        buy_yes_platform = market_a.platform
        buy_no_platform = market_b.platform
    else:
        buy_yes_platform = market_b.platform
        buy_no_platform = market_a.platform

    action = f"Buy YES on {buy_yes_platform.title()} · Buy NO on {buy_no_platform.title()}"

    liq_a = market_a.volume_24h or 0
    liq_b = market_b.volume_24h or 0
    return ArbOpportunity(
        canonical_id=market_a.canonical_id,
        title=market_a.title,
        platform_a=market_a.platform,
        market_id_a=market_a.platform_id,
        price_a=market_a.yes_price,
        platform_b=market_b.platform,
        market_id_b=market_b.platform_id,
        price_b=market_b.yes_price,
        gross_spread_pct=round(gross_spread, 2),
        net_spread_pct=round(net_spread, 2),
        fee_a_pct=round(fee_a * 100, 1),
        fee_b_pct=round(fee_b * 100, 1),
        recommended_action=action,
        liquidity_score=min(liq_a, liq_b),
        detected_at=datetime.utcnow(),
    )


def score_opportunity(opp: ArbOpportunity) -> float:
    return opp.net_spread_pct * log1p(opp.liquidity_score + 1)


class ArbScanner:
    def __init__(self, connectors: list):
        self.connectors = connectors
        self._last_opportunities: list = []

    async def poll_once(self) -> list:
        # 30s per-connector timeout so one slow API can't hang the whole scan
        async def _fetch(c):
            return await asyncio.wait_for(c.get_markets(), timeout=30)

        results = await asyncio.gather(
            *[_fetch(c) for c in self.connectors],
            return_exceptions=True,
        )
        all_markets = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                print(f"[ArbScanner] connector {i} error: {r}")
            else:
                # Cap each platform at top 1000 by volume to keep matching fast
                sorted_markets = sorted(r, key=lambda m: m.volume_24h or 0, reverse=True)
                all_markets.extend(sorted_markets[:1000])
                print(f"[ArbScanner] {r[0].platform if r else '?'}: {len(r)} markets (using top {min(len(r),1000)})")

        groups = match_markets(all_markets)
        opportunities = []
        for markets in groups.values():
            # Compare every platform pair within the group
            for i in range(len(markets)):
                for j in range(i + 1, len(markets)):
                    if markets[i].platform == markets[j].platform:
                        continue
                    opp = compute_spread(markets[i], markets[j])
                    if opp:
                        opportunities.append(opp)

        opportunities.sort(key=score_opportunity, reverse=True)
        self._last_opportunities = opportunities
        return opportunities

    @property
    def last_opportunities(self) -> list:
        return self._last_opportunities

    async def run_loop(self, interval: int, callback: Callable) -> None:
        while True:
            try:
                opps = await self.poll_once()
                callback(opps)
            except Exception as e:
                print(f"[ArbScanner] loop error: {e}")
            await asyncio.sleep(interval)
