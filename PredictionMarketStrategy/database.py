import sqlite3
from datetime import datetime
from typing import Optional
from models import Trade


CREATE_TRADES_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    market_id TEXT NOT NULL,
    market_title TEXT NOT NULL,
    outcome TEXT NOT NULL,
    amount REAL NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    pnl REAL,
    notes TEXT DEFAULT '',
    opened_at TEXT NOT NULL,
    closed_at TEXT
);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    with get_connection(db_path) as conn:
        conn.execute(CREATE_TRADES_SQL)
        conn.commit()


def _row_to_trade(row: sqlite3.Row) -> Trade:
    return Trade(
        id=row["id"],
        platform=row["platform"],
        market_id=row["market_id"],
        market_title=row["market_title"],
        outcome=row["outcome"],
        amount=row["amount"],
        entry_price=row["entry_price"],
        exit_price=row["exit_price"],
        status=row["status"],
        pnl=row["pnl"],
        notes=row["notes"] or "",
        opened_at=datetime.fromisoformat(row["opened_at"]),
        closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
    )


def insert_trade(conn: sqlite3.Connection, trade: Trade) -> int:
    cur = conn.execute(
        """INSERT INTO trades
           (platform, market_id, market_title, outcome, amount, entry_price,
            status, notes, opened_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (trade.platform, trade.market_id, trade.market_title, trade.outcome,
         trade.amount, trade.entry_price, trade.status, trade.notes,
         trade.opened_at.isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def get_open_trades(conn: sqlite3.Connection) -> list:
    rows = conn.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY opened_at DESC").fetchall()
    return [_row_to_trade(r) for r in rows]


def get_all_trades(conn: sqlite3.Connection) -> list:
    rows = conn.execute("SELECT * FROM trades ORDER BY opened_at DESC").fetchall()
    return [_row_to_trade(r) for r in rows]


def close_trade(conn: sqlite3.Connection, trade_id: int, exit_price: float, pnl: float) -> None:
    conn.execute(
        "UPDATE trades SET exit_price=?, pnl=?, status='CLOSED', closed_at=? WHERE id=?",
        (exit_price, pnl, datetime.utcnow().isoformat(), trade_id),
    )
    conn.commit()


def compute_portfolio_summary(conn: sqlite3.Connection) -> dict:
    row = conn.execute("""
        SELECT
            COUNT(*) AS total_trades,
            SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open_count,
            SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) AS closed_count,
            SUM(CASE WHEN status='CLOSED' AND pnl > 0 THEN 1 ELSE 0 END) AS wins,
            SUM(amount) AS total_invested,
            COALESCE(SUM(CASE WHEN status='CLOSED' THEN pnl ELSE 0 END), 0) AS total_pnl
        FROM trades
    """).fetchone()
    wins = row["wins"] or 0
    closed = row["closed_count"] or 0
    return {
        "total_trades": row["total_trades"],
        "open_count": row["open_count"] or 0,
        "closed_count": closed,
        "win_rate": round(wins / closed, 3) if closed else 0.0,
        "total_invested": round(row["total_invested"] or 0, 2),
        "total_pnl": round(row["total_pnl"], 2),
    }
