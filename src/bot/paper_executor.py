"""
Paper trading executor -- simulates order fills without real money.

Hybrid strategy simulation:
  - LIMIT (maker) orders: fill after a random delay, with configurable probability.
    Simulates sitting in the order book queue.
  - MARKET (taker) orders: fill instantly at the given price.
    Simulates hitting the ask (offer).
"""

import logging
import random
import time
import uuid

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

        The order does NOT fill instantly. It fills after a random delay
        (MAKER_FILL_DELAY_MIN to MAKER_FILL_DELAY_MAX), and only if the
        random roll passes MAKER_FILL_PROB. This simulates queue position
        in the order book.
        """
        order_id = f"paper_{uuid.uuid4().hex[:12]}"
        now = time.time()

        # Decide at creation whether this order will fill
        will_fill = random.random() < config.MAKER_FILL_PROB
        fill_delay = random.uniform(config.MAKER_FILL_DELAY_MIN, config.MAKER_FILL_DELAY_MAX)

        order = PaperOrder(
            order_id=order_id,
            token_id=token_id,
            side="BUY",
            order_type="LIMIT",
            price=price,
            size=size_usdc,
            status=OrderStatus.OPEN,
            will_fill=will_fill,
            fill_after=now + fill_delay,
        )
        self._orders[order_id] = order
        log.info("Maker order placed: %s token=%s..%s price=%.3f usdc=$%.2f "
                 "fill_in=%.1fs will_fill=%s",
                 order_id, token_id[:8], token_id[-4:], price, size_usdc,
                 fill_delay, will_fill)
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

    async def get_order_status(self, order_id: str) -> OrderStatus:
        """Return the current status of a paper order.

        For maker orders, transitions OPEN -> FILLED when:
          1. Current time > fill_after (delay elapsed)
          2. will_fill == True (probability roll passed at creation)
        """
        order = self._orders.get(order_id)
        if not order:
            return OrderStatus.CANCELLED

        # Check if a pending maker order should now fill
        if order.status == OrderStatus.OPEN and order.order_type == "LIMIT":
            now = time.time()
            if now >= order.fill_after:
                if order.will_fill:
                    order.status = OrderStatus.FILLED
                    order.filled_at = now
                    log.info("Maker order FILLED (after %.1fs): %s",
                             now - order.created_at, order.order_id)
                # If will_fill=False, order stays OPEN until cancelled

        return order.status

    async def get_usdc_balance(self) -> float:
        return self._balance

    async def cancel_all(self):
        """Cancel all open paper orders."""
        for order in self._orders.values():
            if order.status == OrderStatus.OPEN:
                order.status = OrderStatus.CANCELLED
        log.info("All paper orders cancelled")
