# CLAUDE.md — Polymarket Trading Bots

## Project Overview
Two automated trading bots for Polymarket's 5m/15m BTC/ETH/SOL binary prediction markets:
1. **Arb Bot** (`src/bot/`) — Paper/live arbitrage bot exploiting gap_ask/gap_bid mispricing throughout each market cycle. Deployed on GCP.
2. **Sniper Bot** (`src/sniper/`) — Live bot targeting thin order books in the first 10-20 seconds of each market cycle. (In development — see `Sniper_Architecture.md`)

## Key Concepts
- **gap_ask** = `1.0 - yes_ask - no_ask` — market-order gap (AA scenario, threshold ≥ 0.05)
- **gap_bid** = `1.0 - yes_bid - no_bid` — limit-order gap (BB scenario, maker strategy)
- **Fee formula**: taker = `0.25 × (p × (1-p))²` (max 1.5625% at p=0.50), maker = 0%
- **Slug format**: `{coin}-updown-{type}-{unix_ts}` where `ts = floor(now/interval)*interval`

## Project Structure
```
Polymarket lesson/
├── src/
│   ├── bot/                        # Arb bot (paper + live)
│   │   ├── main.py                 # FastAPI app + SSE dashboard (port 8000)
│   │   ├── engine.py               # Market rotation, WS connection, slug management
│   │   ├── order_manager.py        # Trade lifecycle: place → fill → settle
│   │   ├── paper_executor.py       # Paper trading (as-if-crossed matching)
│   │   ├── live_executor.py        # Live trading via py-clob-client
│   │   ├── fee.py                  # Fee model (maker 0%, taker formula)
│   │   ├── db.py                   # SQLite trades DB
│   │   ├── config.py               # Bot configuration
│   │   ├── models.py               # Data models
│   │   └── static/dashboard.html   # Web dashboard
│   └── sniper/                     # Sniper bot (IN DEVELOPMENT)
│       └── (see Sniper_Architecture.md for planned modules)
├── data/
│   └── bot.db                      # Active arb bot database
├── strategy.md                     # Arb strategy documentation
├── Blueprint.md                    # Original LLM-bot architecture reference
├── Sniper_Architecture.md          # Sniper bot architecture & build plan
├── requirements.txt
├── venv/
└── archive/                        # Archived research phase files
    ├── data/                       # Old CSVs, JSONs, logs (44 files)
    ├── src_research/               # gap_monitor, whale_monitor, analyzers, collectors
    ├── src_scripts/                # One-off API exploration scripts
    ├── docs/                       # Old docs (ARBITRAGE_RESEARCH, IMPLEMENTATION_PLAN, etc.)
    ├── charts/                     # Signal analysis PNGs
    ├── deploy/                     # Old GCP deploy scripts
    ├── bot_db_archives/            # Archived bot.db snapshots
    └── mock_trader.html            # HTML mock trader
```

## Running the Bots

### Prerequisites
```bash
source venv/Scripts/activate   # Windows Git Bash
```

### Arb Bot
```bash
# Local
python -m src.bot.main
# Dashboard: http://localhost:8000

# GCP (systemd)
sudo systemctl start polymarket-bot
# Dashboard: http://34.86.131.153:8000
```

### Sniper Bot (planned)
```bash
python -m src.sniper.main
# Dashboard: http://localhost:8001
```

## Critical API Endpoints
- **WebSocket**: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- **Gamma API**: `GET https://gamma-api.polymarket.com/markets?slug={slug}`
  - `clobTokenIds[0]` = YES token, `[1]` = NO token
- **CLOB API**: `https://clob.polymarket.com` (via py-clob-client)

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
- **Outcome detection is unreliable**: Gamma API `resolved` field is always None. Current workaround infers from `outcomePrices` (0.0 or 1.0 = resolved), but this takes 7-9 minutes and is fragile. **Must find a better method** — e.g., on-chain resolution event, CLOB API settlement status, or Polygon contract call.
- Current outcome polling: every 15s for up to 10 minutes (40 attempts). Works but is slow and wastes API calls.
- py-clob-client is synchronous — always wrap in `asyncio.to_thread()`
- Minimum order size: $5 USDC per leg
- One trade per slug per market (no position stacking)

## GCP Deployment
- **Current VM**: `polymarket-bot`, e2-small, us-east4-a (Virginia), IP 34.86.131.153
- Service path: `/home/tangmo82/polymarket_new/`
- Arb bot: `polymarket-bot.service` (port 8000)
- Sniper bot: `sniper.service` (port 8001, planned)
- Env vars: `/home/tangmo82/polymarket_new/.env`
- **Planned migration**: Move to Dublin (europe-west1) region for lower latency to Polymarket/Polygon infrastructure. Per Reddit reports, Dublin has the lowest latency to Polymarket's CLOB matching engine. Critical for sniper bot where sub-second order placement matters.
