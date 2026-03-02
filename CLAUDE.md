# CLAUDE.md — Polymarket Arbitrage Research

## Project Overview
Research project studying arbitrage opportunities on Polymarket's 5m/15m BTC/ETH/SOL binary prediction markets. Core hypothesis: YES + NO token prices sometimes sum to less than $1.00, creating a risk-free profit opportunity.

## Key Concepts
- **gap_ask** = `1.0 - yes_ask - no_ask` — market-order gap (AA scenario, threshold ≥ 0.05)
- **gap_bid** = `1.0 - yes_bid - no_bid` — limit-order gap (BB scenario, whale strategy)
- **Sanity filter**: skip if `yes_ask > 0.90 AND no_ask > 0.90` (stale data near close)
- **Fee formula**: `fee_per_share(p) = 0.25 × (p × (1-p))²` — max 1.5625% at p=0.50
- **Slug format**: `{coin}-updown-{type}-{unix_ts}` where `ts = floor(now/interval)*interval`

## Project Structure
```
Polymarket lesson/
├── src/arbitrage/
│   ├── gap_monitor.py          # WebSocket gap monitor (BTC/ETH/SOL, 5m/15m) — MAIN SCRIPT
│   ├── whale_monitor.py        # Tracks known whale wallets via Polymarket API
│   ├── analyze_gap_log.py      # Offline analysis of data/gap_log.csv
│   ├── analyze_whale_patterns.py  # VWAP + fee analysis on whale trades
│   ├── analyze_signal.py       # Binance signal analysis
│   ├── collect_binance.py      # Binance price data collector
│   ├── collect_polymarket.py   # Polymarket price collector (legacy, REST-based)
│   ├── profit_calculator.py    # Standalone profit calculator
│   └── fullrun.py              # Runs gap_monitor + whale_monitor together
├── data/
│   ├── gap_log.csv             # Live gap observations (written by gap_monitor.py)
│   ├── whale_log.csv           # Whale trade log (written by whale_monitor.py)
│   └── *.csv                   # Historical signal + binance data
├── mock_trader.html            # Standalone HTML mock trader — open in browser, no server needed
├── strategy.md                 # Authoritative arbitrage strategy documentation
├── ARBITRAGE_RESEARCH.md       # Original research hypothesis
├── IMPLEMENTATION_PLAN.md      # System build plan (Sessions 1–5)
└── venv/                       # Python virtual environment
```

## Running the Python Scripts

### Prerequisites
```bash
# Activate venv (from project root)
source venv/Scripts/activate   # Windows Git Bash
# or
venv\Scripts\activate          # Windows CMD/PowerShell
```

### Main Scripts
```bash
# Real-time gap monitor (WebSocket, runs until Ctrl+C or pilot duration)
python src/arbitrage/gap_monitor.py

# Whale wallet tracker
python src/arbitrage/whale_monitor.py

# Run both simultaneously (separate terminals recommended)
python src/arbitrage/fullrun.py

# Offline analysis (run after collecting data)
python src/arbitrage/analyze_gap_log.py
python src/arbitrage/analyze_whale_patterns.py
```

### gap_monitor.py Configuration
- `PILOT_MODE = True` — runs for `PILOT_DURATION_HOURS` then stops
- `PILOT_MODE = False` — runs indefinitely (production mode)
- `MIN_PROFITABLE_GAP = 0.05` — gap threshold to flag an opportunity
- `LOG_INTERVAL_SECONDS = 10` — dashboard refresh rate (CSV writes are event-driven)

## Running the HTML Mock Trader
Open `mock_trader.html` directly in any modern browser — no server required.
See the "How to Run" section in strategy.md for details.

## Critical API Endpoints
- **WebSocket**: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- **Token IDs**: `GET https://gamma-api.polymarket.com/markets?slug={slug}`
- `clobTokenIds[0]` = YES token, `[1]` = NO token

## WebSocket Event Formats
```
book event:
  bids: ascending [{price,size}] → best_bid = bids[-1].price
  asks: ascending [{price,size}] → best_ask = asks[0].price

price_change event:
  price_changes: [{asset_id, best_bid, best_ask, best_ask_size, ...}]
  (must iterate the array — not a flat event)
```

## Important Constraints
- Do **not** modify `gap_monitor.py` without re-checking the WebSocket event format comments (verified 2026-03-01)
- `mock_trader.html` fires trades only on `gap_ask >= 0.05` (AA scenario); `gap_bid` is informational only
- The mock trader covers **BTC and ETH only** (4 markets) — no SOL
- One trade per slug per market (no position stacking)
- Market rotation must reconnect the WebSocket with fresh token IDs

## Open Research Items
- **Maker bid strategy not yet executed in live/paper trading** — the mock trader UI and logic are built (Section 12 in strategy.md), but no real maker bids have been placed and no paper trading results exist yet.
- **Binance signal research pending review** — the signal thresholds (0.3% momentum in 30s, CL lag > 30s) are based on the implementation plan assumptions, not on empirical analysis of `data/` files. Before going live, run `analyze_signal.py` to validate whether these thresholds actually predict `gap_bid` openings in historical data.

## Data Schema — gap_log.csv
```
recorded_at, coin, market_type, slug, market_closes, seconds_left,
yes_price (= yes_ask), no_price (= no_ask), gap (= gap_ask), gap_bid,
gap_duration_ms, arb_size_usd, opportunity
```
Note: `yes_price`/`no_price` column names kept for CSV compatibility — they hold ask prices.
