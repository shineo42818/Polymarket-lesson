"""Dataclasses for the trading bot's shared state."""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class TradeStatus(str, Enum):
    PENDING = "PENDING"       # orders placed, waiting for fills
    PARTIAL = "PARTIAL"       # one leg filled
    FILLED = "FILLED"         # both legs filled -- arb locked in
    EXPIRED = "EXPIRED"       # market rotated before fill
    SETTLED = "SETTLED"       # market resolved, P&L realized


class BotState(str, Enum):
    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    KILLED = "KILLED"         # emergency stop


@dataclass
class MarketState:
    """Live price state for one market (e.g., btc_5m)."""
    coin: str
    market_type: str
    slug: str = ""
    yes_token: str = ""
    no_token: str = ""
    closes_at: int = 0
    # Prices (updated by WS)
    yes_bid: Optional[float] = None
    yes_ask: Optional[float] = None
    yes_ask_size: Optional[float] = None
    no_bid: Optional[float] = None
    no_ask: Optional[float] = None
    no_ask_size: Optional[float] = None

    @property
    def cache_key(self) -> str:
        return f"{self.coin}_{self.market_type}"

    @property
    def gap_ask(self) -> Optional[float]:
        if self.yes_ask is None or self.no_ask is None:
            return None
        return round(1.0 - self.yes_ask - self.no_ask, 4)

    @property
    def gap_bid(self) -> Optional[float]:
        if self.yes_bid is None or self.no_bid is None:
            return None
        return round(1.0 - self.yes_bid - self.no_bid, 4)

    @property
    def arb_size_usd(self) -> Optional[float]:
        if self.yes_ask_size is None or self.no_ask_size is None:
            return None
        return round(min(self.yes_ask_size, self.no_ask_size), 2)

    @property
    def seconds_left(self) -> int:
        now = datetime.now(timezone.utc).timestamp()
        return max(0, int(self.closes_at - now))

    @property
    def is_stale(self) -> bool:
        """Sanity filter: both asks > 0.90 means stale data near close."""
        if self.yes_ask is None or self.no_ask is None:
            return True
        return self.yes_ask > 0.90 and self.no_ask > 0.90

    def has_prices(self) -> bool:
        return (self.yes_bid is not None and self.yes_ask is not None
                and self.no_bid is not None and self.no_ask is not None)

    def clear_prices(self):
        self.yes_bid = self.yes_ask = self.yes_ask_size = None
        self.no_bid = self.no_ask = self.no_ask_size = None

    def to_dict(self) -> dict:
        return {
            "coin": self.coin,
            "market_type": self.market_type,
            "slug": self.slug,
            "seconds_left": self.seconds_left,
            "yes_bid": self.yes_bid,
            "yes_ask": self.yes_ask,
            "no_bid": self.no_bid,
            "no_ask": self.no_ask,
            "gap_ask": self.gap_ask,
            "gap_bid": self.gap_bid,
            "arb_size": self.arb_size_usd,
        }


@dataclass
class SignalState:
    """External signal state for one coin."""
    coin: str
    binance_price: Optional[float] = None
    momentum_30s: float = 0.0
    momentum_fired: bool = False
    chainlink_price: Optional[float] = None
    chainlink_updated_at: float = 0.0
    chainlink_lag: float = 0.0
    chainlink_fired: bool = False

    @property
    def any_fired(self) -> bool:
        return self.momentum_fired or self.chainlink_fired

    def to_dict(self) -> dict:
        return {
            "coin": self.coin,
            "binance_price": self.binance_price,
            "momentum_30s": round(self.momentum_30s, 6),
            "momentum_fired": self.momentum_fired,
            "cl_price": self.chainlink_price,
            "cl_lag": round(self.chainlink_lag, 1),
            "cl_fired": self.chainlink_fired,
            "any_fired": self.any_fired,
        }


@dataclass
class PaperOrder:
    """A simulated order in paper trading mode."""
    order_id: str
    token_id: str
    side: str = "BUY"
    order_type: str = "LIMIT"     # "LIMIT" (maker) or "MARKET" (taker)
    price: float = 0.0
    size: float = 0.0
    status: OrderStatus = OrderStatus.OPEN
    created_at: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())
    filled_at: Optional[float] = None
    # Paper simulation: maker orders fill after delay (if will_fill=True)
    will_fill: bool = True        # decided at creation time via MAKER_FILL_PROB
    fill_after: float = 0.0       # timestamp when fill triggers


@dataclass
class ArbTrade:
    """A BB arb trade pair (YES + NO limit orders)."""
    trade_id: int = 0
    timestamp: str = ""
    coin: str = ""
    market_type: str = ""
    slug: str = ""
    mode: str = "PAPER"
    # Order details
    yes_order_id: str = ""
    no_order_id: str = ""
    yes_bid: float = 0.0
    no_bid: float = 0.0
    yes_ask: float = 0.0
    no_ask: float = 0.0
    gap_bid: float = 0.0
    trade_usdc: float = 0.0
    yes_usdc: float = 0.0
    no_usdc: float = 0.0
    yes_tokens: float = 0.0
    no_tokens: float = 0.0
    fee_yes: float = 0.0
    fee_no: float = 0.0
    # Status
    yes_filled: bool = False
    no_filled: bool = False
    status: str = "PENDING"
    # Execution mode: PENDING -> MAKER (both legs) or HYBRID (one maker + one taker)
    execution_mode: str = "PENDING"  # PENDING, MAKER, HYBRID
    taker_leg: str = ""              # "yes" or "no" -- which side was takered
    taker_order_id: str = ""         # order ID for the taker market order
    taker_ask: float = 0.0           # actual ask price paid on taker leg
    taker_fee: float = 0.0           # taker fee on that leg
    # P&L
    hedged_profit: Optional[float] = None
    settled_pnl: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "timestamp": self.timestamp,
            "coin": self.coin,
            "market_type": self.market_type,
            "slug": self.slug,
            "mode": self.mode,
            "yes_bid": self.yes_bid,
            "no_bid": self.no_bid,
            "yes_ask": self.yes_ask,
            "no_ask": self.no_ask,
            "gap_bid": self.gap_bid,
            "trade_usdc": self.trade_usdc,
            "yes_usdc": round(self.yes_usdc, 4),
            "no_usdc": round(self.no_usdc, 4),
            "yes_tokens": self.yes_tokens,
            "no_tokens": self.no_tokens,
            "fee_yes": round(self.fee_yes, 6),
            "fee_no": round(self.fee_no, 6),
            "yes_filled": self.yes_filled,
            "no_filled": self.no_filled,
            "status": self.status,
            "execution_mode": self.execution_mode,
            "taker_leg": self.taker_leg,
            "taker_ask": self.taker_ask,
            "taker_fee": round(self.taker_fee, 6),
            "hedged_profit": round(self.hedged_profit, 4) if self.hedged_profit else None,
            "settled_pnl": round(self.settled_pnl, 4) if self.settled_pnl else None,
        }
