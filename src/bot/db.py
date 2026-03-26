"""SQLite database layer for the trading bot."""

import sqlite3
import os
from datetime import datetime, timezone
from typing import Optional
from . import config


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            coin TEXT NOT NULL,
            market_type TEXT NOT NULL,
            slug TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'PAPER',
            yes_order_id TEXT,
            no_order_id TEXT,
            yes_bid REAL NOT NULL,
            no_bid REAL NOT NULL,
            yes_ask REAL,
            no_ask REAL,
            gap_bid REAL NOT NULL,
            trade_usdc REAL NOT NULL,
            yes_usdc REAL DEFAULT 0,
            no_usdc REAL DEFAULT 0,
            yes_tokens REAL DEFAULT 0,
            no_tokens REAL DEFAULT 0,
            fee_yes REAL DEFAULT 0,
            fee_no REAL DEFAULT 0,
            yes_filled INTEGER DEFAULT 0,
            no_filled INTEGER DEFAULT 0,
            status TEXT DEFAULT 'PENDING',
            execution_mode TEXT DEFAULT 'PENDING',
            taker_leg TEXT DEFAULT '',
            taker_ask REAL DEFAULT 0,
            taker_fee REAL DEFAULT 0,
            hedged_profit REAL,
            settled_pnl REAL,
            market_outcome TEXT
        );

        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            usdc_balance REAL NOT NULL,
            total_pnl REAL NOT NULL,
            open_positions INTEGER DEFAULT 0,
            mode TEXT NOT NULL DEFAULT 'PAPER'
        );

        CREATE TABLE IF NOT EXISTS bot_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_trades_slug ON trades(slug);
        CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
        CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON portfolio_snapshots(timestamp);
    """)
    # Migrations: add columns to existing databases (idempotent)
    for col_def in [
        "ALTER TABLE trades ADD COLUMN market_outcome TEXT",
        "ALTER TABLE trades ADD COLUMN execution_mode TEXT DEFAULT 'PENDING'",
        "ALTER TABLE trades ADD COLUMN taker_leg TEXT DEFAULT ''",
        "ALTER TABLE trades ADD COLUMN taker_ask REAL DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN taker_fee REAL DEFAULT 0",
    ]:
        try:
            conn.execute(col_def)
            conn.commit()
        except Exception:
            pass  # column already exists
    conn.commit()
    conn.close()


# ── Trade CRUD ──

def insert_trade(trade_dict: dict) -> int:
    conn = get_connection()
    cur = conn.execute("""
        INSERT INTO trades (
            timestamp, coin, market_type, slug, mode,
            yes_order_id, no_order_id, yes_bid, no_bid, yes_ask, no_ask, gap_bid,
            trade_usdc, yes_usdc, no_usdc, yes_tokens, no_tokens, fee_yes, fee_no,
            yes_filled, no_filled, status, hedged_profit
        ) VALUES (
            :timestamp, :coin, :market_type, :slug, :mode,
            :yes_order_id, :no_order_id, :yes_bid, :no_bid, :yes_ask, :no_ask, :gap_bid,
            :trade_usdc, :yes_usdc, :no_usdc, :yes_tokens, :no_tokens, :fee_yes, :fee_no,
            :yes_filled, :no_filled, :status, :hedged_profit
        )
    """, trade_dict)
    trade_id = cur.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def update_trade(trade_id: int, updates: dict):
    if not updates:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = trade_id
    conn = get_connection()
    conn.execute(f"UPDATE trades SET {set_clause} WHERE id = :id", updates)
    conn.commit()
    conn.close()


def get_trades(limit: int = 50, offset: int = 0, status: Optional[str] = None) -> list[dict]:
    conn = get_connection()
    if status:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (status, limit, offset)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_open_trades() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM trades WHERE status IN ('PENDING', 'PARTIAL') ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_trade_by_slug(slug: str, mode: str = "PAPER") -> Optional[dict]:
    """Check if we already have an active trade for this slug."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM trades WHERE slug = ? AND mode = ? AND status IN ('PENDING', 'PARTIAL', 'FILLED') LIMIT 1",
        (slug, mode)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_daily_pnl() -> float:
    """Sum of settled P&L for today."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = get_connection()
    row = conn.execute(
        "SELECT COALESCE(SUM(settled_pnl), 0) as total FROM trades WHERE timestamp LIKE ? AND settled_pnl IS NOT NULL",
        (f"{today}%",)
    ).fetchone()
    conn.close()
    return row["total"]


# ── Portfolio snapshots ──

def insert_snapshot(usdc_balance: float, total_pnl: float, open_positions: int, mode: str):
    conn = get_connection()
    conn.execute(
        "INSERT INTO portfolio_snapshots (timestamp, usdc_balance, total_pnl, open_positions, mode) VALUES (?, ?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), usdc_balance, total_pnl, open_positions, mode)
    )
    conn.commit()
    conn.close()


def get_pnl_history(limit: int = 500) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM portfolio_snapshots ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


# ── Bot config ──

def get_config_value(key: str, default: str = "") -> str:
    conn = get_connection()
    row = conn.execute("SELECT value FROM bot_config WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_config_value(key: str, value: str):
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)", (key, value)
    )
    conn.commit()
    conn.close()


# ── Stats ──

def get_total_trades() -> int:
    conn = get_connection()
    row = conn.execute("SELECT COUNT(*) as cnt FROM trades").fetchone()
    conn.close()
    return row["cnt"]


def get_total_pnl() -> float:
    conn = get_connection()
    row = conn.execute(
        "SELECT COALESCE(SUM(settled_pnl), 0) as total FROM trades WHERE settled_pnl IS NOT NULL"
    ).fetchone()
    conn.close()
    return row["total"]
