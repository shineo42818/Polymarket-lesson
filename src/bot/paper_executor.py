"""
Paper trading executor -- simulates order fills without real money.

Hybrid strategy simulation:
  - LIMIT (maker) orders: fill after a random delay, with configurable probability.
    Simulates sitting in the order book queue.
  - MARKET (taker) orders: fill instantly at the given price.
    Simulates hitting the ask (offer).
"""

import logging
import time
import uuid
from typing import Optional

from .order_manager import OrderExecutor
from .models import OrderStatus, PaperOrder
from . import config

log = logging.getLogger("bot.paper")


class PaperExecutor(OrderExecutor):
    """Simulates order execution for paper trading."""

    def __init__(self):
        self._orders: dict[str, PaperOrder] = {}  # order_id -> PaperOrder
        self._balance: float = config.STARTING_CAPITAL
        self._engine = None

    async def initialize(self, engine):
        self._engine = engine

    async def place_limit_buy(self, token_id: str, price: float, size_usdc: float) -> str:
        """Create a simulated MAKER limit buy order.

        Fills when the live market ask drops to or below the bid price (as-if-crossed).
        No random coin flip — only fills when the market would actually cross the order.
        """
        order_id = f"paper_{uuid.uuid4().hex[:12]}"

        order = PaperOrder(
            order_id=order_id,
            token_id=token_id,
            side="BUY",
            order_type="LIMIT",
            price=price,
            size=size_usdc,
            status=OrderStatus.OPEN,
        )
        self._orders[order_id] = order
        log.info("Maker order placed: %s token=%s..%s price=%.3f usdc=$%.2f",
                 order_id, token_id[:8], token_id[-4:], price, size_usdc)
        return order_id

    async def place_market_buy(self, token_id: str, price: float, size_usdc: float) -> str:
        """Create a simulated TAKER market buy order. Fills instantly."""
        order_id = f"paper_{uuid.uuid4().hex[:12]}"
        order = PaperOrder(
            order_id=order_id,
            token_id=token_id,
            side="BUY",
            order_type="MARKET",
            price=price,
            size=size_usdc,
            status=OrderStatus.FILLED,
            will_fill=True,
            fill_after=0.0,
            filled_at=time.time(),
        )
        self._orders[order_id] = order
        log.info("Taker order FILLED: %s token=%s..%s ask=%.3f usdc=$%.2f",
                 order_id, token_id[:8], token_id[-4:], price, size_usdc)
        return order_id

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a paper order."""
        order = self._orders.get(order_id)
        if order and order.status == OrderStatus.OPEN:
            order.status = OrderStatus.CANCELLED
            log.info("Paper order cancelled: %s", order_id)
            return True
        return False

    async def get_order_status(self, order_id: str, current_price: Optional[float] = None) -> OrderStatus:
        """Return the current status of a paper order.

        For maker (LIMIT) orders: fills when current_price (live ask) crosses
        at or below the order's bid price (as-if-crossed). If no price is
        available the order stays OPEN until cancelled.
        """
        order = self._orders.get(order_id)
        if not order:
            return OrderStatus.CANCELLED

        if order.status == OrderStatus.OPEN and order.order_type == "LIMIT":
            if current_price is not None and current_price <= order.price:
                order.status = OrderStatus.FILLED
                order.filled_at = time.time()
                log.info("Maker order FILLED (ask=%.3f <= bid=%.3f): %s",
                         current_price, order.price, order.order_id)

        return order.status

    async def get_usdc_balance(self) -> float:
        return self._balance

    async def cancel_all(self):
        """Cancel all open paper orders."""
        for order in self._orders.values():
            if order.status == OrderStatus.OPEN:
                order.status = OrderStatus.CANCELLED
        log.info("All paper orders cancelled")
