"""Bot configuration -- constants, risk limits, env var loading."""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Trading mode ──
MODE = os.getenv("BOT_MODE", "PAPER")  # "PAPER" or "LIVE"

# ── Markets ──
COINS = ["btc", "eth", "sol"]
MARKET_TYPES = ["5m", "15m"]
MARKET_INTERVALS = {"5m": 300, "15m": 900}

# ── Polymarket endpoints ──
POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"

# ── Binance endpoint ──
BINANCE_WS_URL = (
    "wss://data-stream.binance.vision/stream"
    "?streams=btcusdt@aggTrade/ethusdt@aggTrade/solusdt@aggTrade"
)
BINANCE_SYMBOLS = {"btcusdt": "btc", "ethusdt": "eth", "solusdt": "sol"}

# ── Gap thresholds ──
MIN_GAP_BID = 0.05          # minimum gap_bid to trigger BB arb
MAX_GAP_BID = 0.15          # maximum gap_bid -- reject near-expiry lopsided markets
MIN_MAKER_GAP = 0.03        # minimum for signal-driven maker bids
SANITY_ASK_MAX = 0.90       # skip if both asks > this (stale data)

# ── Risk management ($100 account) ──
STARTING_CAPITAL = 100.0
MAX_TRADE_USDC = 10.0        # hard cap per trade in USDC (prevents compounding runaway)
MAX_TRADE_PCT = 0.10         # 10% of balance per trade (applied after hard cap)
MAX_OPEN_POSITIONS = 3
MIN_USDC_RESERVE = 20.0      # always keep $20 liquid
MAX_DAILY_LOSS = 15.0        # kill switch trigger
MIN_SECONDS_LEFT = 30        # don't enter if market closes in < 30s
CANCEL_BEFORE_CLOSE = 10     # cancel unfilled orders 10s before close

# ── Signal thresholds (from strategy.md Section 12) ──
MOMENTUM_THRESH = 0.003      # 0.3% Binance 30s move
MOMENTUM_WINDOW_S = 30       # look-back window in seconds
CL_LAG_THRESH = 30           # Chainlink "stale" lag in seconds
CL_DIVERGE_THRESH = 0.003    # 0.3% Binance vs Chainlink divergence

# ── Hybrid strategy (maker-then-taker) ──
MAKER_FILL_DELAY_MIN = 2.0      # min seconds before maker fill (paper sim)
MAKER_FILL_DELAY_MAX = 8.0      # max seconds before maker fill (paper sim)
MAKER_FILL_PROB = 0.50           # probability a maker order fills (paper sim)
MIN_HYBRID_PROFIT = 0.005        # min $USD profit to trigger taker leg

# ── Timing ──
PRICE_BROADCAST_INTERVAL = 1.0   # SSE price push every 1s
PORTFOLIO_BROADCAST_INTERVAL = 5.0
STATUS_BROADCAST_INTERVAL = 10.0
SIGNAL_BROADCAST_INTERVAL = 2.0
ORDER_CHECK_INTERVAL = 2.0       # check fill status every 2s
MARKET_ROTATION_CHECK = 1.0      # check slug change every 1s

# ── Database ──
DB_PATH = os.getenv("BOT_DB_PATH", "data/bot.db")

# ── Live trading (Phase 4) ──
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_FUNDER = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_API_SECRET = os.getenv("POLYMARKET_API_SECRET", "")
POLYMARKET_API_PASSPHRASE = os.getenv("POLYMARKET_API_PASSPHRASE", "")

# ── Server ──
HOST = os.getenv("BOT_HOST", "0.0.0.0")
PORT = int(os.getenv("BOT_PORT", "8000"))
