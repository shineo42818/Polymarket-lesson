"""
Live order executor using Polymarket CLOB API via py-clob-client.

Phase 4 — real money trading.

Prerequisites:
  pip install py-clob-client

Required env vars (set in .env):
  POLYMARKET_PRIVATE_KEY      = "0x..."   # wallet private key (Polygon)
  POLYMARKET_FUNDER_ADDRESS   = "0x..."   # funder/proxy wallet address
  POLYMARKET_API_KEY          = "..."
  POLYMARKET_API_SECRET       = "..."
  POLYMARKET_API_PASSPHRASE   = "..."

Minimum order size:
  Polymarket enforces a minimum per order (exact value returned in API error).
  Safe minimum to avoid rejection: 5 USDC per leg (= $10 total per trade).
  Set MAX_TRADE_USDC = 5.0 in config.py when testing live execution.

Switch from paper to live:
  Set BOT_MODE=LIVE in your .env file.
  main.py will automatically use LiveExecutor instead of PaperExecutor.
"""

import logging
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, MarketOrderArgs, OrderType
from py_clob_client.constants import POLYGON

from . import config
from .order_manager import OrderExecutor
from .models import OrderStatus

log = logging.getLogger("bot.live")

# Polygon mainnet chain ID
CHAIN_ID = POLYGON  # 137


class LiveExecutor(OrderExecutor):
    """Real order execution via Polymarket CLOB API."""

    def __init__(self):
        if not config.POLYMARKET_PRIVATE_KEY:
            raise ValueError("POLYMARKET_PRIVATE_KEY not set in .env")
        if not config.POLYMARKET_API_KEY:
            raise ValueError("POLYMARKET_API_KEY not set in .env")

        self._client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=CHAIN_ID,
            key=config.POLYMARKET_PRIVATE_KEY,
            creds={
                "apiKey": config.POLYMARKET_API_KEY,
                "secret": config.POLYMARKET_API_SECRET,
                "passphrase": config.POLYMARKET_API_PASSPHRASE,
            },
            funder=config.POLYMARKET_FUNDER,
        )
        log.info("LiveExecutor initialized (POLYGON mainnet)")

    async def place_limit_buy(self, token_id: str, price: float, size_usdc: float) -> str:
        """Place a GTC limit (maker) buy order.

        size_usdc is converted to shares: shares = size_usdc / price
        Minimum safe size: 5 USDC per leg.
        """
        shares = round(size_usdc / price, 4)
        order_args = OrderArgs(
            token_id=token_id,
            price=round(price, 4),
            size=shares,
            side="BUY",
        )
        resp = self._client.create_and_post_order(order_args)
        order_id = resp.get("orderID") or resp.get("id") or str(resp)
        log.info("LIMIT BUY placed: token=...%s price=%.4f shares=%.4f id=%s",
                 token_id[-6:], price, shares, order_id)
        return order_id

    async def place_market_buy(self, token_id: str, price: float, size_usdc: float) -> str:
        """Place a FOK market (taker) buy order.

        amount is in USDC for BUY orders.
        """
        order_args = MarketOrderArgs(
            token_id=token_id,
            amount=round(size_usdc, 4),
            side="BUY",
        )
        resp = self._client.create_and_post_order(order_args)
        order_id = resp.get("orderID") or resp.get("id") or str(resp)
        log.info("MARKET BUY placed: token=...%s usdc=%.4f id=%s",
                 token_id[-6:], size_usdc, order_id)
        return order_id

    async def cancel_order(self, order_id: str) -> bool:
        try:
            resp = self._client.cancel(order_id)
            log.info("Cancelled order %s: %s", order_id, resp)
            return True
        except Exception as e:
            log.warning("Cancel failed for %s: %s", order_id, e)
            return False

    async def get_order_status(self, order_id: str, current_price=None) -> OrderStatus:
        try:
            resp = self._client.get_order(order_id)
            status = resp.get("status", "").upper()
            if status in ("MATCHED", "FILLED"):
                return OrderStatus.FILLED
            if status in ("CANCELLED", "CANCELED"):
                return OrderStatus.CANCELLED
            return OrderStatus.OPEN
        except Exception as e:
            log.warning("get_order_status failed for %s: %s", order_id, e)
            return OrderStatus.OPEN

    async def get_usdc_balance(self) -> float:
        try:
            resp = self._client.get_balance_allowance_params(asset_type="COLLATERAL")
            return float(resp.get("balance", 0)) / 1e6  # USDC.e has 6 decimals
        except Exception as e:
            log.warning("get_usdc_balance failed: %s", e)
            return 0.0

    async def cancel_all(self):
        try:
            resp = self._client.cancel_all()
            log.info("Cancel all orders: %s", resp)
        except Exception as e:
            log.error("cancel_all failed: %s", e)
