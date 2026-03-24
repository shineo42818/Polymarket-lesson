"""
Async trading engine -- the core of the bot.

Runs as asyncio tasks inside the FastAPI process:
  1. Polymarket WebSocket consumer (prices)
  2. Binance WebSocket consumer (momentum signals)
  3. Market rotation checker (1s loop)
  4. Order lifecycle manager (check fills, cancel stale)
  5. Portfolio snapshot writer
"""

import asyncio
import json
import time
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Optional

import httpx
import websockets

from . import config, db
from .models import MarketState, SignalState, BotState, ArbTrade
from .fee import fee_per_share, net_shares

log = logging.getLogger("bot.engine")


class TradingEngine:
    """Central engine that manages all async tasks."""

    def __init__(self, order_manager):
        # Order manager (paper or live)
        self.order_manager = order_manager

        # State
        self.state = BotState.STOPPED
        self.started_at: Optional[float] = None

        # Markets: cache_key -> MarketState (populated in start() from DB or defaults)
        self.markets: dict[str, MarketState] = {}

        # Token ID -> cache_key mapping (for WS event routing)
        self._token_to_market: dict[str, tuple[str, str]] = {}  # token_id -> (cache_key, "yes"|"no")

        # Signals: coin -> SignalState
        self.signals: dict[str, SignalState] = {
            coin: SignalState(coin=coin) for coin in config.COINS
        }

        # Binance price history for momentum calculation
        # coin -> deque of (timestamp_s, price)
        self._binance_history: dict[str, deque] = {
            coin: deque(maxlen=600) for coin in config.COINS  # ~10 min at 1/s
        }

        # Gap episode tracking
        self._gap_start_times: dict[str, float] = {}  # cache_key -> timestamp

        # SSE subscribers
        self._sse_queues: list[asyncio.Queue] = []

        # Running tasks
        self._tasks: list[asyncio.Task] = []

        # WS connection reference for clean shutdown
        self._poly_ws = None
        self._binance_ws = None

    # ─── SSE broadcast ───

    def add_sse_queue(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=100)
        self._sse_queues.append(q)
        return q

    def remove_sse_queue(self, q: asyncio.Queue):
        self._sse_queues.discard(q) if hasattr(self._sse_queues, 'discard') else None
        try:
            self._sse_queues.remove(q)
        except ValueError:
            pass

    async def _broadcast(self, event: str, data: dict):
        msg = {"event": event, "data": data}
        dead = []
        for q in self._sse_queues:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.remove_sse_queue(q)

    # ─── Lifecycle ───

    async def start(self):
        if self.state == BotState.RUNNING:
            return
        log.info("Engine starting...")
        self.state = BotState.RUNNING
        self.started_at = time.time()

        # Initialize order manager
        await self.order_manager.initialize(self)

        # Load active markets from DB (persisted selection), fallback to all 6
        saved = db.get_config_value("active_markets")
        if saved:
            active_keys = [k.strip() for k in saved.split(",") if k.strip()]
        else:
            active_keys = [f"{c}_{m}" for c in config.COINS for m in config.MARKET_TYPES]
        self.markets = {}
        for key in active_keys:
            parts = key.split("_", 1)
            if len(parts) == 2:
                ms = MarketState(coin=parts[0], market_type=parts[1])
                self.markets[ms.cache_key] = ms
        log.info("Active markets: %s", list(self.markets.keys()))

        # Fetch initial token IDs
        await self._refresh_all_markets()

        # Start async tasks
        self._tasks = [
            asyncio.create_task(self._polymarket_ws_loop(), name="poly_ws"),
            asyncio.create_task(self._binance_ws_loop(), name="binance_ws"),
            asyncio.create_task(self._market_rotation_loop(), name="rotation"),
            asyncio.create_task(self._order_lifecycle_loop(), name="orders"),
            asyncio.create_task(self._broadcast_loop(), name="broadcast"),
        ]
        log.info("Engine started -- %d tasks running", len(self._tasks))

    async def stop(self):
        if self.state == BotState.STOPPED:
            return
        log.info("Engine stopping...")
        self.state = BotState.STOPPED
        await self._cancel_tasks()

    async def kill(self):
        """Emergency stop: cancel all orders then stop."""
        log.warning("KILL SWITCH activated")
        self.state = BotState.KILLED
        await self.order_manager.cancel_all()
        await self._cancel_tasks()
        await self._broadcast("status", {"engine": "KILLED"})

    async def _cancel_tasks(self):
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        # Close websockets
        if self._poly_ws:
            await self._poly_ws.close()
        if self._binance_ws:
            await self._binance_ws.close()

    # ─── Market slug + token ID management ───

    def _get_current_slug(self, coin: str, market_type: str) -> tuple[str, int]:
        """Calculate current slug and close timestamp."""
        interval = config.MARKET_INTERVALS[market_type]
        now = int(time.time())
        ts = now - (now % interval)
        slug = f"{coin}-updown-{market_type}-{ts}"
        closes_at = ts + interval
        return slug, closes_at

    async def _fetch_token_ids(self, slug: str) -> tuple[Optional[str], Optional[str]]:
        """Fetch YES/NO token IDs from Gamma API."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(config.GAMMA_API_URL, params={"slug": slug})
                data = resp.json()
                if not data:
                    return None, None
                token_ids = json.loads(data[0].get("clobTokenIds", "[]"))
                if len(token_ids) < 2:
                    return None, None
                return token_ids[0], token_ids[1]
        except Exception as e:
            log.error("fetch_token_ids(%s): %s", slug, e)
            return None, None

    async def _refresh_all_markets(self) -> bool:
        """Refresh token IDs for all markets. Returns True if any changed."""
        changed = False
        for key, ms in self.markets.items():
            slug, close_ts = self._get_current_slug(ms.coin, ms.market_type)
            if ms.slug != slug:
                log.info("New market: %s", slug)
                yes_token, no_token = await self._fetch_token_ids(slug)
                if yes_token and no_token:
                    ms.slug = slug
                    ms.yes_token = yes_token
                    ms.no_token = no_token
                    ms.closes_at = close_ts
                    ms.clear_prices()
                    # Update token routing map
                    self._token_to_market[yes_token] = (key, "yes")
                    self._token_to_market[no_token] = (key, "no")
                    changed = True
                else:
                    log.warning("Could not find token IDs for %s", slug)
        return changed

    # ─── Polymarket WebSocket ───

    def _build_subscription(self) -> str:
        all_ids = []
        for ms in self.markets.values():
            if ms.yes_token:
                all_ids.append(ms.yes_token)
            if ms.no_token:
                all_ids.append(ms.no_token)
        return json.dumps({"assets_ids": all_ids, "type": "market"})

    async def _polymarket_ws_loop(self):
        """Connect to Polymarket WS, handle events, reconnect on failure."""
        while self.state == BotState.RUNNING:
            try:
                async with websockets.connect(config.POLYMARKET_WS_URL) as ws:
                    self._poly_ws = ws
                    log.info("Polymarket WS connected")
                    await ws.send(self._build_subscription())
                    log.info("Subscribed to %d tokens", sum(1 for ms in self.markets.values() if ms.yes_token) * 2)

                    async for message in ws:
                        if self.state != BotState.RUNNING:
                            break
                        self._handle_poly_message(message)

            except websockets.ConnectionClosed:
                log.warning("Polymarket WS closed, reconnecting in 3s...")
            except Exception as e:
                log.error("Polymarket WS error: %s, reconnecting in 3s...", e)

            if self.state == BotState.RUNNING:
                await asyncio.sleep(3)

    def _handle_poly_message(self, raw: str):
        """Parse and route Polymarket WS events."""
        try:
            data = json.loads(raw)
            events = data if isinstance(data, list) else [data]
            for event in events:
                self._handle_poly_event(event)
        except Exception as e:
            log.error("WS parse error: %s", e)

    def _handle_poly_event(self, event: dict):
        """Process a single WS event (book or price_change)."""
        event_type = event.get("event_type") or event.get("type", "")

        if event_type == "book":
            asset_id = event.get("asset_id")
            if not asset_id or asset_id not in self._token_to_market:
                return
            cache_key, side = self._token_to_market[asset_id]
            ms = self.markets[cache_key]

            bids = event.get("bids", [])
            asks = event.get("asks", [])
            best_bid = float(bids[-1]["price"]) if bids else None
            best_ask = float(asks[0]["price"]) if asks else None
            ask_size = float(asks[0]["size"]) if asks else None

            if side == "yes":
                ms.yes_bid, ms.yes_ask, ms.yes_ask_size = best_bid, best_ask, ask_size
            else:
                ms.no_bid, ms.no_ask, ms.no_ask_size = best_bid, best_ask, ask_size

        elif event_type == "price_change":
            for change in event.get("price_changes", []):
                asset_id = change.get("asset_id")
                if not asset_id or asset_id not in self._token_to_market:
                    continue
                cache_key, side = self._token_to_market[asset_id]
                ms = self.markets[cache_key]

                raw_bid = change.get("best_bid")
                raw_ask = change.get("best_ask")
                raw_ask_size = change.get("best_ask_size")

                if side == "yes":
                    if raw_bid is not None: ms.yes_bid = float(raw_bid)
                    if raw_ask is not None: ms.yes_ask = float(raw_ask)
                    if raw_ask_size is not None: ms.yes_ask_size = float(raw_ask_size)
                else:
                    if raw_bid is not None: ms.no_bid = float(raw_bid)
                    if raw_ask is not None: ms.no_ask = float(raw_ask)
                    if raw_ask_size is not None: ms.no_ask_size = float(raw_ask_size)

        # After every price update, check for arb opportunities
        self._check_opportunities()

    def _check_opportunities(self):
        """Check all markets for BB arb opportunities and trigger trades."""
        if self.state != BotState.RUNNING:
            return

        for key, ms in self.markets.items():
            if not ms.has_prices() or ms.is_stale:
                continue

            gap_bid = ms.gap_bid
            if gap_bid is None:
                continue

            # Track gap episodes
            now = time.time()
            if gap_bid >= config.MIN_GAP_BID:
                if key not in self._gap_start_times:
                    self._gap_start_times[key] = now
            else:
                self._gap_start_times.pop(key, None)
                continue

            # Check if we should trade
            if ms.seconds_left < config.MIN_SECONDS_LEFT:
                continue

            # Attempt BB arb trade
            self.order_manager.try_bb_arb(ms)

    # ─── Binance WebSocket ───

    async def _binance_ws_loop(self):
        """Connect to Binance aggTrade stream for momentum signals."""
        while self.state == BotState.RUNNING:
            try:
                async with websockets.connect(config.BINANCE_WS_URL) as ws:
                    self._binance_ws = ws
                    log.info("Binance WS connected")

                    async for message in ws:
                        if self.state != BotState.RUNNING:
                            break
                        self._handle_binance_message(message)

            except websockets.ConnectionClosed:
                log.warning("Binance WS closed, reconnecting in 3s...")
            except Exception as e:
                log.error("Binance WS error: %s, reconnecting in 3s...", e)

            if self.state == BotState.RUNNING:
                await asyncio.sleep(3)

    def _handle_binance_message(self, raw: str):
        """Parse Binance aggTrade and update momentum signals."""
        try:
            wrapper = json.loads(raw)
            data = wrapper.get("data", wrapper)
            symbol = data.get("s", "").lower()
            coin = config.BINANCE_SYMBOLS.get(symbol)
            if not coin:
                return

            price = float(data["p"])
            now = time.time()

            # Update signal state
            sig = self.signals[coin]
            sig.binance_price = price

            # Store in history
            history = self._binance_history[coin]
            history.append((now, price))

            # Calculate 30s momentum
            target_time = now - config.MOMENTUM_WINDOW_S
            old_price = None
            for ts, p in history:
                if ts >= target_time:
                    old_price = p
                    break

            if old_price and old_price > 0:
                sig.momentum_30s = (price - old_price) / old_price
                sig.momentum_fired = abs(sig.momentum_30s) >= config.MOMENTUM_THRESH
            else:
                sig.momentum_30s = 0.0
                sig.momentum_fired = False

        except Exception as e:
            log.error("Binance parse error: %s", e)

    # ─── Market rotation ───

    async def _market_rotation_loop(self):
        """Check every 1s if market slugs have changed. Reconnect WS if so."""
        while self.state == BotState.RUNNING:
            try:
                changed = await self._refresh_all_markets()
                if changed:
                    log.info("Market rotation detected -- reconnecting Polymarket WS")
                    if self._poly_ws:
                        await self._poly_ws.close()
                    # The WS loop will auto-reconnect with fresh token IDs
            except Exception as e:
                log.error("Rotation check error: %s", e)

            await asyncio.sleep(config.MARKET_ROTATION_CHECK)

    # ─── Order lifecycle ───

    async def _order_lifecycle_loop(self):
        """Periodically check order fills, cancel stale orders, settle trades."""
        while self.state == BotState.RUNNING:
            try:
                await self.order_manager.check_fills(self.markets)
                await self.order_manager.cancel_expired(self.markets)

                # Check daily loss limit
                daily_pnl = self.order_manager.get_daily_pnl()
                if daily_pnl <= -config.MAX_DAILY_LOSS:
                    log.warning("Daily loss limit hit (%.2f), killing engine", daily_pnl)
                    await self.kill()
                    return

            except Exception as e:
                log.error("Order lifecycle error: %s", e)

            await asyncio.sleep(config.ORDER_CHECK_INTERVAL)

    # ─── Broadcast loop (SSE) ───

    async def _broadcast_loop(self):
        """Periodically broadcast state to SSE subscribers."""
        last_prices = 0
        last_portfolio = 0
        last_status = 0
        last_signal = 0

        while self.state == BotState.RUNNING:
            now = time.time()

            # Prices
            if now - last_prices >= config.PRICE_BROADCAST_INTERVAL:
                price_data = {k: ms.to_dict() for k, ms in self.markets.items()}
                await self._broadcast("prices", price_data)
                last_prices = now

            # Portfolio
            if now - last_portfolio >= config.PORTFOLIO_BROADCAST_INTERVAL:
                portfolio = self.order_manager.get_portfolio_state()
                await self._broadcast("portfolio", portfolio)
                last_portfolio = now

            # Status
            if now - last_status >= config.STATUS_BROADCAST_INTERVAL:
                status = {
                    "engine": self.state.value,
                    "mode": config.MODE,
                    "uptime_s": int(now - self.started_at) if self.started_at else 0,
                    "ws_polymarket": "CONNECTED" if self._poly_ws and self._poly_ws.close_code is None else "DISCONNECTED",
                    "ws_binance": "CONNECTED" if self._binance_ws and self._binance_ws.close_code is None else "DISCONNECTED",
                }
                await self._broadcast("status", status)
                last_status = now

            # Signals
            if now - last_signal >= config.SIGNAL_BROADCAST_INTERVAL:
                sig_data = {coin: sig.to_dict() for coin, sig in self.signals.items()}
                await self._broadcast("signal", sig_data)
                last_signal = now

            await asyncio.sleep(0.5)

    # ─── Market selector ───

    async def update_active_markets(self, markets: list[str]):
        """Update active markets at runtime. Cancels trades for removed markets."""
        current = set(self.markets.keys())
        new_set = set(markets)

        # Cancel trades and remove deselected markets
        for key in current - new_set:
            await self.order_manager.cancel_market_trades(key)
            self.markets.pop(key, None)
            log.info("Removed market: %s", key)

        # Add newly selected markets
        for key in new_set - current:
            parts = key.split("_", 1)
            if len(parts) == 2:
                ms = MarketState(coin=parts[0], market_type=parts[1])
                self.markets[key] = ms
                log.info("Added market: %s", key)

        # Persist selection
        db.set_config_value("active_markets", ",".join(sorted(self.markets.keys())))

        # Reconnect WS with new token subscription
        if self._poly_ws:
            await self._poly_ws.close()

        log.info("Active markets updated: %s", sorted(self.markets.keys()))

    # ─── Public state accessors ───

    def get_status(self) -> dict:
        now = time.time()
        return {
            "engine": self.state.value,
            "mode": config.MODE,
            "uptime_s": int(now - self.started_at) if self.started_at else 0,
            "ws_polymarket": "CONNECTED" if self._poly_ws and self._poly_ws.close_code is None else "DISCONNECTED",
            "ws_binance": "CONNECTED" if self._binance_ws and self._binance_ws.close_code is None else "DISCONNECTED",
            "markets_active": sum(1 for ms in self.markets.values() if ms.has_prices()),
            "open_positions": self.order_manager.open_position_count(),
        }
