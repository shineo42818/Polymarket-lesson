"""Telegram bot notifications for trade alerts and reports."""

import logging
import os
from typing import Optional

import httpx

from . import config

log = logging.getLogger("bot.telegram")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


async def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a Telegram message. Returns True on success."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": parse_mode,
            })
            if resp.status_code == 200:
                return True
            log.warning("Telegram send failed: %s", resp.text)
            return False
    except Exception as e:
        log.error("Telegram error: %s", e)
        return False


async def send_trade_alert(trade_dict: dict):
    """Send instant alert when a trade fills."""
    mode = trade_dict.get("execution_mode", "?")
    coin = trade_dict.get("coin", "?").upper()
    mtype = trade_dict.get("market_type", "?")
    pnl = trade_dict.get("hedged_profit") or 0
    gap = trade_dict.get("gap_bid", 0)
    usdc = trade_dict.get("trade_usdc", 0)
    status = trade_dict.get("status", "?")

    icon = "✅" if pnl > 0 else "🔴"

    text = (
        f"{icon} <b>Trade {status}</b>\n"
        f"<b>{coin} {mtype}</b> [{mode}]\n"
        f"Gap: {gap:.4f} | Size: ${usdc:.2f}\n"
        f"P&L: <b>${pnl:+.4f}</b>"
    )

    if mode == "HYBRID":
        taker_leg = trade_dict.get("taker_leg", "?").upper()
        taker_ask = trade_dict.get("taker_ask", 0)
        taker_fee = trade_dict.get("taker_fee", 0)
        text += f"\nTaker: {taker_leg} ask={taker_ask:.3f} fee={taker_fee:.4f}"

    await send_message(text)


async def send_portfolio_summary(portfolio: dict, trade_stats: dict):
    """Send periodic portfolio summary."""
    balance = portfolio.get("usdc_balance", 0)
    total_pnl = portfolio.get("total_pnl", 0)
    daily_pnl = portfolio.get("daily_pnl", 0)
    open_pos = portfolio.get("open_positions", 0)

    total_trades = trade_stats.get("total", 0)
    maker_fills = trade_stats.get("maker", 0)
    hybrid_fills = trade_stats.get("hybrid", 0)
    expired = trade_stats.get("expired", 0)

    icon = "📈" if total_pnl >= 0 else "📉"

    text = (
        f"{icon} <b>Portfolio Update</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Balance: <b>${balance:.2f}</b>\n"
        f"Total P&L: <b>${total_pnl:+.4f}</b>\n"
        f"Daily P&L: ${daily_pnl:+.4f}\n"
        f"Open: {open_pos} positions\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Trades: {total_trades} total\n"
        f"  Maker: {maker_fills} | Hybrid: {hybrid_fills}\n"
        f"  Expired: {expired}"
    )

    await send_message(text)


async def send_kill_alert(reason: str, balance: float):
    """Send urgent alert when kill switch triggers."""
    text = (
        f"🚨 <b>KILL SWITCH ACTIVATED</b> 🚨\n"
        f"Reason: {reason}\n"
        f"Balance: ${balance:.2f}\n"
        f"All orders cancelled."
    )
    await send_message(text)
