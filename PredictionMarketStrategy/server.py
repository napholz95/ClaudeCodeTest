import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from typing import Optional


import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import DB_PATH, HTTP_PORT, POLL_INTERVAL_SECONDS
from connectors import KalshiConnector, PolymarketConnector, ManifoldConnector
from arb_scanner import ArbScanner
from database import init_db, get_connection, insert_trade, get_all_trades, get_open_trades, close_trade, compute_portfolio_summary
from models import Trade, TradeCreate, TradeResponse, ArbOpportunityResponse, MarketResponse, ResearchSignalResponse
from research import ResearchEngine

# ── Globals ───────────────────────────────────────────────────────────────────

_connections: set[WebSocket] = set()
_connectors = [
    KalshiConnector(),
    PolymarketConnector(),
    ManifoldConnector(),
]
_scanner = ArbScanner(_connectors)
_research = ResearchEngine()
_last_poll: Optional[datetime] = None


# ── WebSocket broadcast ───────────────────────────────────────────────────────

async def broadcast(message: dict) -> None:
    dead = set()
    payload = json.dumps(message, default=str)
    for ws in list(_connections):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _connections.difference_update(dead)


# ── Scanner loop ──────────────────────────────────────────────────────────────

async def scanner_loop() -> None:
    global _last_poll
    while True:
        try:
            opps = await _scanner.poll_once()
            _last_poll = datetime.utcnow()
            await broadcast({
                "type": "ARB_UPDATE",
                "payload": [asdict(o) for o in opps],
            })
        except Exception as e:
            print(f"[server] scanner error: {e}")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


# ── App lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(DB_PATH)
    asyncio.create_task(scanner_loop())
    yield


app = FastAPI(title="PredictionMarketStrategy", lifespan=lifespan)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(os.path.join(os.path.dirname(__file__), "dashboard.html"))


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "last_poll": _last_poll.isoformat() if _last_poll else None,
        "open_connections": len(_connections),
        "connectors": [c.platform for c in _connectors],
    }


@app.get("/markets")
async def get_markets(platform: Optional[str] = None):
    results = await asyncio.gather(
        *[c.get_markets() for c in _connectors],
        return_exceptions=True,
    )
    all_markets = []
    for r in results:
        if not isinstance(r, Exception):
            all_markets.extend(r)
    if platform:
        all_markets = [m for m in all_markets if m.platform == platform]
    return [
        MarketResponse(
            platform=m.platform,
            platform_id=m.platform_id,
            title=m.title,
            yes_price=m.yes_price,
            no_price=m.no_price,
            category=m.category,
            volume_24h=m.volume_24h,
            closes_at=m.closes_at.isoformat() if m.closes_at else None,
        )
        for m in all_markets
    ]


@app.get("/arb")
async def get_arb(min_spread: float = 0.0):
    opps = _scanner.last_opportunities
    if min_spread > 0:
        opps = [o for o in opps if o.net_spread_pct >= min_spread]
    return [
        ArbOpportunityResponse(
            canonical_id=o.canonical_id,
            title=o.title,
            platform_a=o.platform_a,
            market_id_a=o.market_id_a,
            price_a=o.price_a,
            platform_b=o.platform_b,
            market_id_b=o.market_id_b,
            price_b=o.price_b,
            gross_spread_pct=o.gross_spread_pct,
            net_spread_pct=o.net_spread_pct,
            fee_a_pct=o.fee_a_pct,
            fee_b_pct=o.fee_b_pct,
            recommended_action=o.recommended_action,
            liquidity_score=o.liquidity_score,
            closes_at=o.closes_at.isoformat() if o.closes_at else None,
            detected_at=o.detected_at.isoformat(),
        )
        for o in opps
    ]


@app.get("/portfolio")
async def get_portfolio():
    with get_connection(DB_PATH) as conn:
        all_trades = get_all_trades(conn)
        summary = compute_portfolio_summary(conn)

    open_trades = [t for t in all_trades if t.status == "OPEN"]
    closed_trades = [t for t in all_trades if t.status == "CLOSED"]

    # Enrich open trades with current market price
    connector_map = {c.platform: c for c in _connectors}
    enriched_open = []
    for t in open_trades:
        current_price = None
        connector = connector_map.get(t.platform)
        if connector:
            try:
                current_price = await connector.get_market_price(t.market_id)
            except Exception:
                pass
        unrealised = None
        if current_price is not None:
            if t.outcome == "YES":
                unrealised = round((current_price - t.entry_price) * t.amount, 2)
            else:
                unrealised = round((t.entry_price - current_price) * t.amount, 2)
        enriched_open.append({
            **_trade_dict(t),
            "current_price": current_price,
            "unrealised_pnl": unrealised,
        })

    return {
        "open": enriched_open,
        "closed": [_trade_dict(t) for t in closed_trades],
        "summary": summary,
    }


@app.post("/trades", response_model=TradeResponse, status_code=201)
async def log_trade(body: TradeCreate):
    trade = Trade(
        platform=body.platform,
        market_id=body.market_id,
        market_title=body.market_title,
        outcome=body.outcome,
        amount=body.amount,
        entry_price=body.entry_price,
        notes=body.notes,
    )
    with get_connection(DB_PATH) as conn:
        trade.id = insert_trade(conn, trade)
    return _trade_response(trade)


@app.get("/research")
async def get_research(market_id: str, platform: str):
    connector = next((c for c in _connectors if c.platform == platform), None)
    if not connector:
        raise HTTPException(status_code=404, detail=f"Platform '{platform}' not found")
    markets = await connector.get_markets()
    market = next((m for m in markets if m.platform_id == market_id), None)
    if not market:
        raise HTTPException(status_code=404, detail="Market not found")
    signal = await _research.analyze_market(market)
    return ResearchSignalResponse(
        market_id=signal.market_id,
        platform=signal.platform,
        direction=signal.direction,
        confidence=signal.confidence,
        headline_count=signal.headline_count,
        top_headlines=signal.top_headlines,
        generated_at=signal.generated_at.isoformat(),
    )


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _connections.add(ws)
    try:
        # Send full state on connect
        opps = _scanner.last_opportunities
        with get_connection(DB_PATH) as conn:
            summary = compute_portfolio_summary(conn)
        await ws.send_text(json.dumps({
            "type": "FULL_STATE",
            "payload": {
                "arb": [asdict(o) for o in opps],
                "portfolio_summary": summary,
            },
        }, default=str))
        while True:
            await ws.receive_text()  # keep alive
    except WebSocketDisconnect:
        pass
    finally:
        _connections.discard(ws)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _trade_dict(t: Trade) -> dict:
    return {
        "id": t.id,
        "platform": t.platform,
        "market_id": t.market_id,
        "market_title": t.market_title,
        "outcome": t.outcome,
        "amount": t.amount,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "status": t.status,
        "pnl": t.pnl,
        "notes": t.notes,
        "opened_at": t.opened_at.isoformat(),
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
    }


def _trade_response(t: Trade) -> TradeResponse:
    return TradeResponse(
        id=t.id,
        platform=t.platform,
        market_id=t.market_id,
        market_title=t.market_title,
        outcome=t.outcome,
        amount=t.amount,
        entry_price=t.entry_price,
        exit_price=t.exit_price,
        status=t.status,
        pnl=t.pnl,
        notes=t.notes,
        opened_at=t.opened_at.isoformat(),
        closed_at=t.closed_at.isoformat() if t.closed_at else None,
    )


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=HTTP_PORT, reload=False)
