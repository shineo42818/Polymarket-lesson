"""
Generate and send portfolio report via Telegram.

Usage:
  python -m src.bot.report          # send report now
  python -m src.bot.report --test   # send test message

Set up as cron job for hourly/daily reports.
"""

import asyncio
import sys
import os

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.bot import db
from src.bot.telegram import send_message, send_portfolio_summary


def get_trade_stats() -> dict:
    """Query trade statistics from DB."""
    conn = db.get_connection()

    total = conn.execute("SELECT COUNT(*) as c FROM trades").fetchone()["c"]
    maker = conn.execute(
        "SELECT COUNT(*) as c FROM trades WHERE execution_mode='MAKER' AND status='SETTLED'"
    ).fetchone()["c"]
    hybrid = conn.execute(
        "SELECT COUNT(*) as c FROM trades WHERE execution_mode='HYBRID' AND status='SETTLED'"
    ).fetchone()["c"]
    expired = conn.execute(
        "SELECT COUNT(*) as c FROM trades WHERE status='EXPIRED'"
    ).fetchone()["c"]
    pending = conn.execute(
        "SELECT COUNT(*) as c FROM trades WHERE status IN ('PENDING','PARTIAL')"
    ).fetchone()["c"]

    total_pnl = conn.execute(
        "SELECT COALESCE(SUM(settled_pnl), 0) as s FROM trades WHERE settled_pnl IS NOT NULL"
    ).fetchone()["s"]
    balance = conn.execute(
        "SELECT value FROM bot_config WHERE key='usdc_balance'"
    ).fetchone()
    daily_pnl_row = conn.execute(
        "SELECT COALESCE(SUM(settled_pnl), 0) as s FROM trades "
        "WHERE settled_pnl IS NOT NULL AND timestamp LIKE ?",
        (f"{__import__('datetime').datetime.utcnow().strftime('%Y-%m-%d')}%",)
    ).fetchone()

    conn.close()

    return {
        "total": total,
        "maker": maker,
        "hybrid": hybrid,
        "expired": expired,
        "pending": pending,
        "total_pnl": total_pnl,
        "daily_pnl": daily_pnl_row["s"],
        "balance": float(balance["value"]) if balance else 0,
    }


async def send_report():
    """Build and send the report."""
    db.init_db()
    stats = get_trade_stats()

    portfolio = {
        "usdc_balance": stats["balance"],
        "total_pnl": stats["total_pnl"],
        "daily_pnl": stats["daily_pnl"],
        "open_positions": stats["pending"],
    }
    trade_stats = {
        "total": stats["total"],
        "maker": stats["maker"],
        "hybrid": stats["hybrid"],
        "expired": stats["expired"],
    }

    await send_portfolio_summary(portfolio, trade_stats)
    print(f"Report sent: balance=${stats['balance']:.2f} pnl=${stats['total_pnl']:+.4f}")


async def send_test():
    """Send a test message to verify Telegram is working."""
    ok = await send_message("🤖 <b>Polymarket Bot</b> — Telegram connected!")
    if ok:
        print("Test message sent successfully!")
    else:
        print("Failed to send. Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")


if __name__ == "__main__":
    if "--test" in sys.argv:
        asyncio.run(send_test())
    else:
        asyncio.run(send_report())
