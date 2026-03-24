"""
Order manager -- hybrid maker-then-taker BB arb strategy.

Strategy flow:
  1. Detect gap_bid >= threshold (both bid prices sum to < $0.95)
  2. Post BOTH legs as MAKER limit orders at bid price (0% fee)
  3. Wait for fills (simulated delay in paper, real queue in live)
  4. When first leg fills as maker:
     - Check current ask price on the OTHER side
     - Calculate hybrid profit: maker_cost + taker_cost vs $1 payout
     - If profitable -> TAKER the other side immediately
     - If not -> keep waiting for second maker fill or expire
  5. If BOTH legs fill as maker -> best case, full maker profit
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

from . import config, db
from .models import MarketState, ArbTrade, OrderStatus
from . import telegram
from .fee import (
    taker_fee_per_share, maker_fee_per_share,
    net_shares_taker, net_shares_maker,
    taker_arb_profit, maker_arb_profit,
)

log = logging.getLogger("bot.orders")


class OrderExecutor(ABC):
    """Abstract interface for order execution (paper or live)."""

    @abstractmethod
    async def place_limit_buy(self, token_id: str, price: float, size_usdc: float) -> str:
        """Place a GTC limit buy (MAKER). Returns order_id."""

    @abstractmethod
    async def place_market_buy(self, token_id: str, price: float, size_usdc: float) -> str:
        """Place a market buy (TAKER) at the given ask price. Returns order_id."""

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""

    @abstractmethod
    async def get_order_status(self, order_id: str, current_price: Optional[float] = None) -> OrderStatus:
        """Check if order is OPEN, FILLED, CANCELLED, etc.
        current_price: live market ask for the token (used by paper sim for as-if-crossed logic).
        """

    @abstractmethod
    async def get_usdc_balance(self) -> float:
        """Return current USDC balance."""

    @abstractmethod
    async def cancel_all(self):
        """Cancel all open orders (kill switch)."""


class OrderManager:
    """Manages hybrid maker-then-taker BB arb trade lifecycle."""

    def __init__(self, executor: OrderExecutor):
        self.executor = executor
        self._engine = None  # set in initialize()
        self.usdc_balance: float = config.STARTING_CAPITAL
        self.total_pnl: float = 0.0
        self._active_trades: dict[str, ArbTrade] = {}  # slug -> ArbTrade
        self._settled_trades: list[ArbTrade] = []

    async def initialize(self, engine):
        self._engine = engine
        saved = db.get_config_value("usdc_balance")
        if saved:
            self.usdc_balance = float(saved)
        saved_pnl = db.get_config_value("total_pnl")
        if saved_pnl:
            self.total_pnl = float(saved_pnl)
        log.info("OrderManager initialized: balance=$%.2f, pnl=$%.4f",
                 self.usdc_balance, self.total_pnl)

    # ── Entry: detect opportunity and place maker orders ──

    def try_bb_arb(self, ms: MarketState):
        """Place BOTH legs as MAKER limit orders at bid price.

        Both orders sit on the book (0% fee). The hybrid logic in
        check_fills() handles what happens when one fills first.
        """
        if ms.slug in self._active_trades:
            return

        if not ms.has_prices():
            return

        gap_bid = ms.gap_bid
        if gap_bid is None or gap_bid < config.MIN_GAP_BID:
            return
        if gap_bid > config.MAX_GAP_BID:
            return  # near-expiry lopsided market, not a real arb opportunity

        if ms.seconds_left < config.MIN_SECONDS_LEFT:
            return

        if len(self._active_trades) >= config.MAX_OPEN_POSITIONS:
            return

        available = self.usdc_balance - config.MIN_USDC_RESERVE
        if available <= 0:
            return

        trade_usdc = min(config.MAX_TRADE_USDC, self.usdc_balance * config.MAX_TRADE_PCT, available)
        if trade_usdc < 1.0:
            return

        # MAKER: buy equal token counts at BID prices, 0% fee.
        # cost_per_pair = yes_bid + no_bid, profit_per_pair = gap_bid
        maker_profit, num_pairs, yes_usdc, no_usdc = maker_arb_profit(
            ms.yes_bid, ms.no_bid, trade_usdc
        )

        trade = ArbTrade(
            timestamp=datetime.now(timezone.utc).isoformat(),
            coin=ms.coin,
            market_type=ms.market_type,
            slug=ms.slug,
            mode=config.MODE,
            yes_bid=ms.yes_bid,
            no_bid=ms.no_bid,
            yes_ask=ms.yes_ask,
            no_ask=ms.no_ask,
            gap_bid=gap_bid,
            trade_usdc=trade_usdc,
            yes_usdc=yes_usdc,
            no_usdc=no_usdc,
            yes_tokens=num_pairs,
            no_tokens=num_pairs,
            fee_yes=0.0,  # maker fee = 0%
            fee_no=0.0,
            hedged_profit=maker_profit,  # best-case estimate (both maker)
        )

        # Reserve slug immediately to prevent duplicates
        self._active_trades[ms.slug] = trade

        asyncio.ensure_future(self._place_maker_orders(trade, ms))

    async def _place_maker_orders(self, trade: ArbTrade, ms: MarketState):
        """Place both MAKER limit orders at bid price."""
        try:
            yes_oid, no_oid = await asyncio.gather(
                self.executor.place_limit_buy(ms.yes_token, ms.yes_bid, trade.yes_usdc),
                self.executor.place_limit_buy(ms.no_token, ms.no_bid, trade.no_usdc),
            )

            trade.yes_order_id = yes_oid
            trade.no_order_id = no_oid

            # Reserve USDC
            self.usdc_balance -= trade.trade_usdc

            # Save to DB
            trade_dict = {
                "timestamp": trade.timestamp,
                "coin": trade.coin,
                "market_type": trade.market_type,
                "slug": trade.slug,
                "mode": trade.mode,
                "yes_order_id": yes_oid,
                "no_order_id": no_oid,
                "yes_bid": trade.yes_bid,
                "no_bid": trade.no_bid,
                "yes_ask": trade.yes_ask,
                "no_ask": trade.no_ask,
                "gap_bid": trade.gap_bid,
                "trade_usdc": trade.trade_usdc,
                "yes_usdc": trade.yes_usdc,
                "no_usdc": trade.no_usdc,
                "yes_tokens": trade.yes_tokens,
                "no_tokens": trade.no_tokens,
                "fee_yes": trade.fee_yes,
                "fee_no": trade.fee_no,
                "yes_filled": 0,
                "no_filled": 0,
                "status": "PENDING",
                "hedged_profit": trade.hedged_profit,
            }
            trade.trade_id = db.insert_trade(trade_dict)

            log.info(
                "MAKER ORDERS placed: %s %s gap_bid=%.4f "
                "yes_bid=%.3f no_bid=%.3f usdc=$%.2f "
                "maker_profit=$%.4f (if both fill)",
                ms.coin.upper(), ms.market_type, trade.gap_bid,
                ms.yes_bid, ms.no_bid, trade.trade_usdc, trade.hedged_profit,
            )

            if self._engine:
                await self._engine._broadcast("trade", trade.to_dict())

            db.set_config_value("usdc_balance", str(self.usdc_balance))

        except Exception as e:
            log.error("Failed to place maker orders: %s", e)
            self._active_trades.pop(trade.slug, None)

    # ── Fill checking + hybrid taker logic ──

    async def check_fills(self, markets: dict[str, MarketState]):
        """Check maker fill status. When one fills, evaluate taker on other side."""
        for slug, trade in list(self._active_trades.items()):
            if trade.status not in ("PENDING", "PARTIAL"):
                continue

            # Poll maker order statuses — pass live ask so paper sim uses as-if-crossed
            cache_key = f"{trade.coin}_{trade.market_type}"
            ms_live = markets.get(cache_key)
            secs = ms_live.seconds_left if ms_live else 9999

            if not trade.yes_filled:
                yes_ask = ms_live.yes_ask if ms_live else None
                status = await self.executor.get_order_status(trade.yes_order_id, current_price=yes_ask)
                if status == OrderStatus.FILLED:
                    trade.yes_filled = True
                    log.info("YES maker FILLED for %s", slug)

            if not trade.no_filled:
                no_ask = ms_live.no_ask if ms_live else None
                status = await self.executor.get_order_status(trade.no_order_id, current_price=no_ask)
                if status == OrderStatus.FILLED:
                    trade.no_filled = True
                    log.info("NO maker FILLED for %s", slug)

            # Both maker legs filled -- best case!
            if trade.yes_filled and trade.no_filled:
                trade.status = "FILLED"
                trade.execution_mode = "MAKER"
                # hedged_profit already set to maker profit
                log.info("BOTH MAKER FILLED: %s profit=$%.4f (0%% fee, pure arb)",
                         slug, trade.hedged_profit)
                db.update_trade(trade.trade_id, {
                    "yes_filled": 1, "no_filled": 1,
                    "status": "FILLED",
                })
                asyncio.ensure_future(telegram.send_trade_alert(trade.to_dict()))
                continue

            # One leg filled -- try hybrid taker on the other side
            if trade.yes_filled or trade.no_filled:
                trade.status = "PARTIAL"
                if trade.execution_mode == "PENDING":
                    await self._try_hybrid_taker(trade, markets, seconds_left=secs)

            db.update_trade(trade.trade_id, {
                "yes_filled": int(trade.yes_filled),
                "no_filled": int(trade.no_filled),
                "status": trade.status,
            })

    def _min_profit_threshold(self, seconds_left: int) -> float:
        """Return the minimum hybrid profit required to execute taker, scaled by urgency."""
        if seconds_left > config.HYBRID_URGENCY_S:
            return config.MIN_HYBRID_PROFIT        # patient: $0.005
        elif seconds_left > config.HYBRID_EMERGENCY_S:
            return 0.0                             # urgent: accept break-even
        else:
            return -config.HYBRID_EMERGENCY_MAX_LOSS  # emergency: accept small loss

    async def _try_hybrid_taker(self, trade: ArbTrade, markets: dict[str, MarketState], seconds_left: int = 9999):
        """One maker leg filled. Evaluate and potentially taker the other side.

        Profit calculation:
          maker_cost = num_pairs * maker_bid_price  (already paid, 0% fee)
          taker_cost = num_pairs * ask / (1 - taker_fee)  (to get same # tokens)
          total_cost = maker_cost + taker_cost
          payout     = num_pairs * $1.00
          profit     = payout - total_cost
        """
        cache_key = f"{trade.coin}_{trade.market_type}"
        ms = markets.get(cache_key)
        if not ms or not ms.has_prices():
            log.warning("No live prices for %s, skipping hybrid taker", cache_key)
            return

        num_pairs = trade.yes_tokens  # == trade.no_tokens (equal pairs)

        if trade.yes_filled:
            # YES filled as maker, need to TAKER NO at current ask
            taker_side = "no"
            maker_cost = num_pairs * trade.yes_bid  # already paid
            current_ask = ms.no_ask
            taker_token = ms.no_token
        else:
            # NO filled as maker, need to TAKER YES at current ask
            taker_side = "yes"
            maker_cost = num_pairs * trade.no_bid
            current_ask = ms.yes_ask
            taker_token = ms.yes_token

        if current_ask is None or current_ask <= 0:
            log.warning("No ask price for %s side, skipping hybrid", taker_side)
            return

        # Calculate taker cost for N tokens at current ask (with fee)
        fee_rate = taker_fee_per_share(current_ask)
        # To receive num_pairs tokens: cost = num_pairs * ask / (1 - fee)
        taker_cost = num_pairs * current_ask / (1.0 - fee_rate)
        total_cost = maker_cost + taker_cost
        payout = num_pairs * 1.0
        hybrid_profit = payout - total_cost

        log.info(
            "HYBRID eval %s: %s maker filled, %s taker at ask=%.3f "
            "fee=%.4f hybrid_pnl=$%.4f (maker_only=$%.4f)",
            trade.slug, "YES" if trade.yes_filled else "NO",
            taker_side.upper(), current_ask, fee_rate,
            hybrid_profit, trade.hedged_profit,
        )

        threshold = self._min_profit_threshold(seconds_left)
        if hybrid_profit < threshold:
            log.info("Hybrid not profitable ($%.4f < $%.4f threshold at %ds left), waiting",
                     hybrid_profit, threshold, seconds_left)
            return

        # Execute taker order
        try:
            taker_usdc = taker_cost  # USDC needed for taker leg
            taker_oid = await self.executor.place_market_buy(
                taker_token, current_ask, taker_usdc
            )

            # Update trade with hybrid details
            trade.execution_mode = "HYBRID"
            trade.taker_leg = taker_side
            trade.taker_order_id = taker_oid
            trade.taker_ask = current_ask
            trade.taker_fee = fee_rate
            trade.hedged_profit = hybrid_profit  # update to actual hybrid profit

            # Mark both sides as filled
            trade.yes_filled = True
            trade.no_filled = True
            trade.status = "FILLED"

            # Update fee on the taker side
            if taker_side == "yes":
                trade.fee_yes = fee_rate
            else:
                trade.fee_no = fee_rate

            log.info(
                "HYBRID FILLED: %s %s maker+%s taker "
                "profit=$%.4f (ask=%.3f fee=%.4f)",
                trade.coin.upper(), trade.market_type,
                taker_side.upper(), hybrid_profit,
                current_ask, fee_rate,
            )

            asyncio.ensure_future(telegram.send_trade_alert(trade.to_dict()))

            db.update_trade(trade.trade_id, {
                "yes_filled": 1,
                "no_filled": 1,
                "status": "FILLED",
                "hedged_profit": hybrid_profit,
                "fee_yes": trade.fee_yes,
                "fee_no": trade.fee_no,
            })

            if self._engine:
                await self._engine._broadcast("trade", trade.to_dict())

        except Exception as e:
            log.error("Hybrid taker failed: %s", e)

    # ── Expiry + settlement ──

    async def cancel_expired(self, markets: dict[str, MarketState]):
        """Cancel orders for markets about to close, settle filled trades."""
        for slug, trade in list(self._active_trades.items()):
            cache_key = f"{trade.coin}_{trade.market_type}"
            ms = markets.get(cache_key)

            should_settle = False

            if ms and ms.slug != trade.slug:
                should_settle = True
            elif ms and ms.seconds_left < config.CANCEL_BEFORE_CLOSE:
                should_settle = True
            else:
                # Fallback: expire by age in case slug rotation detection fails
                interval = 300 if trade.market_type == "5m" else 900
                trade_age = (datetime.now(timezone.utc) -
                             datetime.fromisoformat(trade.timestamp)).total_seconds()
                if trade_age > interval + 30:
                    log.warning("Force-expiring stale trade %s (age=%.0fs)", slug, trade_age)
                    should_settle = True

            if should_settle:
                # Last chance: if one leg filled, try emergency taker before writing off as directional loss
                if trade.status == "PARTIAL" and ms and ms.seconds_left > 0:
                    log.warning("PARTIAL trade %s at expiry — attempting emergency taker", slug)
                    await self._try_hybrid_taker(trade, markets, seconds_left=0)
                await self._settle_trade(trade)

    async def _settle_trade(self, trade: ArbTrade):
        """Settle a trade: cancel unfilled orders, calculate final P&L."""
        # Cancel any unfilled maker orders
        if not trade.yes_filled:
            await self.executor.cancel_order(trade.yes_order_id)
        if not trade.no_filled:
            await self.executor.cancel_order(trade.no_order_id)

        if trade.yes_filled and trade.no_filled:
            # Both filled (maker or hybrid): profit is locked
            trade.settled_pnl = trade.hedged_profit
            trade.status = "SETTLED"
            log.info("SETTLED %s [%s]: profit=$%.4f",
                     trade.slug, trade.execution_mode, trade.settled_pnl)
        elif trade.yes_filled or trade.no_filled:
            # One maker filled, taker was not profitable -- directional exposure.
            # Worst case: the filled token goes to $0, lose the USDC spent on it.
            filled_side = "YES" if trade.yes_filled else "NO"
            filled_usdc = trade.yes_usdc if trade.yes_filled else trade.no_usdc
            trade.settled_pnl = -filled_usdc
            trade.status = "EXPIRED"
            log.warning("EXPIRED %s: only %s filled, directional loss=$%.4f",
                        trade.slug, filled_side, trade.settled_pnl)
        else:
            # Neither filled: full refund
            trade.settled_pnl = 0.0
            trade.status = "EXPIRED"
            log.info("EXPIRED %s: no fills, USDC refunded", trade.slug)

        # Update balance
        self.usdc_balance += trade.trade_usdc + (trade.settled_pnl or 0.0)
        self.total_pnl += trade.settled_pnl or 0.0

        db.update_trade(trade.trade_id, {
            "status": trade.status,
            "settled_pnl": trade.settled_pnl,
        })
        db.set_config_value("usdc_balance", str(self.usdc_balance))
        db.set_config_value("total_pnl", str(self.total_pnl))

        db.insert_snapshot(self.usdc_balance, self.total_pnl,
                           len(self._active_trades) - 1, config.MODE)

        if self._engine:
            await self._engine._broadcast("trade", trade.to_dict())

        self._active_trades.pop(trade.slug, None)
        self._settled_trades.append(trade)

    # ── Kill switch ──

    async def cancel_all(self):
        """Cancel all open orders (kill switch)."""
        for trade in list(self._active_trades.values()):
            await self._settle_trade(trade)

    # ── Market selector ──

    async def cancel_market_trades(self, cache_key: str):
        """Cancel all active trades for a market (e.g. 'btc_5m'). Full USDC refund, pnl=0."""
        cancelled = 0
        for slug, trade in list(self._active_trades.items()):
            if f"{trade.coin}_{trade.market_type}" == cache_key:
                await self.executor.cancel_order(trade.yes_order_id)
                await self.executor.cancel_order(trade.no_order_id)
                trade.settled_pnl = 0.0
                trade.status = "EXPIRED"
                self.usdc_balance += trade.trade_usdc
                db.update_trade(trade.trade_id, {"status": "EXPIRED", "settled_pnl": 0.0})
                self._active_trades.pop(slug, None)
                cancelled += 1
        if cancelled:
            db.set_config_value("usdc_balance", str(self.usdc_balance))
            log.info("Cancelled %d trades for %s, USDC refunded", cancelled, cache_key)

    # ── Paper reset ──

    def reset_paper(self):
        """Reset paper trading: restore starting balance, clear all trade state."""
        self.usdc_balance = config.STARTING_CAPITAL
        self.total_pnl = 0.0
        self._active_trades.clear()
        self._settled_trades.clear()
        db.set_config_value("usdc_balance", str(self.usdc_balance))
        db.set_config_value("total_pnl", "0.0")
        log.info("Paper trading reset: balance=$%.2f", self.usdc_balance)

    # ── Accessors ──

    def get_daily_pnl(self) -> float:
        return db.get_daily_pnl()

    def open_position_count(self) -> int:
        return len(self._active_trades)

    def get_portfolio_state(self) -> dict:
        return {
            "usdc_balance": round(self.usdc_balance, 4),
            "total_pnl": round(self.total_pnl, 4),
            "open_positions": len(self._active_trades),
            "daily_pnl": round(self.get_daily_pnl(), 4),
            "mode": config.MODE,
            "active_trades": [t.to_dict() for t in self._active_trades.values()],
        }
