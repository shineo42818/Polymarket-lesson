


RESEARCH INSTRUCTION: Build a Polymarket Market-Open Sniper Bot
Context & Source
This document extends the Polymarket Arbitrage Research project (see blueprint.md for the original LLM-based bot architecture). The Sniper Bot is a second, independent bot targeting a different edge: thin order books during the first 10-20 seconds of each 5m/15m BTC/ETH/SOL market cycle. Unlike the original bot (which scans many markets for mispriced probabilities), the sniper exploits a structural timing inefficiency — the gap between when a market opens and when liquidity providers populate the book.

Hypothesis: At second 0 of a new market cycle, the order book is empty or near-empty. During seconds 1-20, bids and asks populate discretely. If `gap_bid = 1.0 - yes_bid - no_bid > 0`, placing maker limit orders on BOTH sides locks in risk-free profit at 0% maker fee. The edge decays to zero as the book fills, so speed is everything.

Prior art: Our existing `src/bot/` (arb bot) already implements ClobClient live execution, WebSocket book parsing, slug calculation, Gamma API token ID fetching, and outcome resolution. The sniper reuses these proven patterns.

SYSTEM ARCHITECTURE OVERVIEW
The sniper follows a time-triggered pipeline, not a continuous scan:
```
CLOCK → PREFETCH → CONNECT → SNIPE → FILL MONITOR → SETTLE
  │        │          │         │          │            │
  │   T-30s: slug +   │    T+0 to T+20s:  │     T+close+10m:
  │   token IDs via    │    watch book,     │     poll Gamma for
  │   Gamma API        │    place makers    │     outcome, calc P&L
  │                    │    if gap > thresh  │
  │               T-5s: WS connect      poll fills every 2s,
  │               with token IDs        cancel unfilled at T+20s
  │
  repeats every 5m/15m cycle
```

Each cycle is self-contained: prefetch → snipe → settle → wait for next open.

LAYER 1: MARKET CLOCK & PREFETCH
What: Know exactly when each market opens, pre-compute the slug, and fetch token IDs before the market goes live.
Why: The sniper window is 20 seconds. If you spend 5 of those seconds fetching token IDs, you've already lost 25% of the opportunity. Pre-fetching at T-30s ensures everything is ready at T+0.

Components to implement:

1. **`clock.py` — Market Cycle Timing**
   - `next_open(coin, market_type) → (open_ts, slug, close_ts)` — returns the next market open time for a given coin/type pair
   - `seconds_until_open(coin, market_type) → float` — how long to sleep
   - Slug formula (proven, from `src/bot/engine.py:163-170`):
     ```python
     interval = 300 if market_type == "5m" else 900
     now = int(time.time())
     ts = now - (now % interval)        # current cycle start
     next_ts = ts + interval            # NEXT cycle start
     slug = f"{coin}-updown-{market_type}-{next_ts}"
     ```
   - Must handle all 6 markets: BTC/ETH/SOL × 5m/15m
   - Returns absolute timestamps (not relative) so the engine can schedule precisely

2. **`prefetch.py` — Token ID Fetching**
   - `fetch_token_ids(slug) → (yes_token_id, no_token_id)` — calls Gamma API
   - Reuses proven pattern from `src/bot/engine.py:172-186`:
     ```python
     GET https://gamma-api.polymarket.com/markets?slug={slug}
     → response[0].clobTokenIds (JSON array)
     → [0] = YES token, [1] = NO token
     ```
   - Retry logic: Gamma API may not have the market at T-30s (market not yet created). Retry every 2s up to T+5s.
   - Cache: store token IDs per slug — no refetch within same cycle

Research tasks:
- Verify Gamma API market creation timing: how far in advance of T+0 does the market appear? (This determines our prefetch lead time.)
- Confirm that token IDs remain stable within a cycle (they should — they're on-chain).
- Test edge case: what happens if you request a slug that doesn't exist yet? (Expected: empty array `[]`.)


LAYER 2: WEBSOCKET BOOK MONITOR
What: Connect to Polymarket's WebSocket and track the live order book for YES and NO tokens in real-time during the sniper window.
Why: The sniper needs sub-second gap_bid updates to fire before the opportunity disappears. REST polling is too slow (~200ms per request × 2 tokens = 400ms minimum latency). WebSocket gives us push-based book updates.

Components to implement:

3. **`book_monitor.py` — Real-Time Book Tracking**
   - WebSocket URL: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
   - Subscription message:
     ```json
     { "assets_ids": ["<yes_token>", "<no_token>"], "type": "market" }
     ```
   - Event parsing (proven format from `src/bot/engine.py`):
     - `book` event (initial snapshot): bids ascending → `best_bid = bids[-1].price`, asks ascending → `best_ask = asks[0].price`
     - `price_change` event (incremental): iterate `price_changes[]` array, match by `asset_id`
   - Maintains state:
     ```python
     yes_bid: float   # best bid for YES token
     yes_ask: float   # best ask for YES token
     no_bid: float    # best bid for NO token
     no_ask: float    # best ask for NO token
     gap_bid: float   # = 1.0 - yes_bid - no_bid (maker arb)
     gap_ask: float   # = 1.0 - yes_ask - no_ask (taker arb, informational)
     ```
   - Emits a callback/event on every state change with the current gap_bid
   - Connect at T-5s so the WS handshake and initial `book` snapshot arrive before T+0
   - Critical: handle the case where book is EMPTY at T+0 (no bids/asks yet). Set prices to None and wait for first update.

Research tasks:
- Measure WS connection + initial book snapshot latency (expect 200-500ms based on arb bot experience)
- Confirm book event is sent immediately on subscribe or only after first trade
- Study how quickly bids appear after market open — does the book go from empty to populated gradually, or does it jump?
- Test: can you subscribe to a market's tokens BEFORE the market officially opens? (If yes, we can connect even earlier.)


LAYER 3: SNIPER ENGINE (Core Loop)
What: The main orchestrator that ties clock, prefetch, book monitor, and order placement into a single cycle.
Why: Timing precision is the entire edge. The engine must coordinate prefetch, WS connection, book monitoring, and order placement within a tight 20-second window across up to 6 markets.

Components to implement:

4. **`sniper_engine.py` — Main Loop**
   ```
   while running:
       # 1. Find the soonest market open across all 6 markets
       next_market = clock.soonest_open(COINS, MARKET_TYPES)

       # 2. Sleep until T-30s
       await sleep_until(next_market.open_ts - 30)

       # 3. Prefetch token IDs
       yes_id, no_id = await prefetch.fetch_token_ids(next_market.slug)
       if not yes_id:
           log warning, skip cycle
           continue

       # 4. Connect WS at T-5s
       await sleep_until(next_market.open_ts - 5)
       monitor = BookMonitor(yes_id, no_id)
       await monitor.connect()

       # 5. Sniper window: T+0 to T+20s
       await sleep_until(next_market.open_ts)
       snipe_result = await run_sniper_window(monitor, duration=SNIPER_WINDOW_SECONDS)

       # 6. Place orders if gap found
       if snipe_result.gap_bid > MIN_SNIPER_GAP:
           trade = await order_placer.place_pair(
               yes_id, no_id,
               snipe_result.yes_bid, snipe_result.no_bid,
               TRADE_USDC
           )
           db.insert_trade(trade)

       # 7. Monitor fills
       await fill_monitor.track(trade, timeout=SNIPER_WINDOW_SECONDS)

       # 8. Cancel unfilled, schedule settlement
       await order_placer.cancel_unfilled(trade)
       schedule_settlement(trade, next_market.close_ts + 600)  # +10min

       # 9. Disconnect WS
       await monitor.disconnect()
   ```

   Key design decisions:
   - **Single market per cycle**: Focus on ONE market open at a time. If two markets open simultaneously (e.g., BTC 5m and ETH 5m at the same :00 boundary), pick the one with historically higher gap_bid.
   - **MAX_CONCURRENT_SNIPES = 2**: Allow at most 2 simultaneous snipe attempts (when 5m and 15m cycles align differently). Prevents capital spread.
   - **No retry on missed window**: If prefetch or WS connect fails, skip the cycle. The next market opens in at most 5 minutes.

Research tasks:
- Map out the timing collision matrix: which (coin, type) pairs open at the same time? BTC/ETH/SOL 5m all open at the same boundaries. 15m opens every 15m. So every 15 minutes, potentially 6 markets open simultaneously.
- Given collision frequency, decide: sequential (snipe one, skip others) or parallel (asyncio.gather for 2-3 simultaneous snipes)?
- Measure end-to-end latency from gap detection to order placement. Target: under 500ms.


LAYER 4: ORDER PLACEMENT
What: Place maker (limit) buy orders on both YES and NO tokens via py-clob-client.
Why: The maker fee is 0% on Polymarket crypto markets. This is the critical edge. If both legs fill at bid prices where `gap_bid > 0`, profit is guaranteed: `profit = gap_bid × num_pairs`. Taker orders (hitting the ask) would incur fees that eat the gap.

Components to implement:

5. **`order_placer.py` — py-clob-client Wrapper**
   - Reuses ClobClient setup pattern from `src/bot/live_executor.py:44-60`:
     ```python
     ClobClient(
         host="https://clob.polymarket.com",
         chain_id=137,  # Polygon
         key=POLYMARKET_PRIVATE_KEY,
         creds={"apiKey": ..., "secret": ..., "passphrase": ...},
         funder=POLYMARKET_FUNDER_ADDRESS,
     )
     ```
   - `place_pair(yes_id, no_id, yes_bid, no_bid, total_usdc) → SniperTrade`
     - Equal token count strategy (from `src/bot/fee.py:48-65`):
       ```
       cost_per_pair = yes_bid + no_bid
       num_pairs = total_usdc / cost_per_pair
       yes_usdc = num_pairs * yes_bid
       no_usdc = num_pairs * no_bid
       ```
     - Place two GTC limit orders:
       - YES: BUY at `yes_bid`, size = `num_pairs` tokens
       - NO: BUY at `no_bid`, size = `num_pairs` tokens
     - Both are MAKER orders (posted to book, not crossing the spread)
   - `cancel_order(order_id)` — cancel a single unfilled/partial order
   - `cancel_unfilled(trade)` — cancel whichever leg didn't fill
   - Important: py-clob-client calls are synchronous. Wrap in `asyncio.to_thread()` to avoid blocking the event loop (same pattern used in existing arb bot).

   Required env vars (shared with arb bot via `.env`):
   ```
   POLYMARKET_PRIVATE_KEY=0x...
   POLYMARKET_FUNDER_ADDRESS=0x...
   POLYMARKET_API_KEY=...
   POLYMARKET_API_SECRET=...
   POLYMARKET_API_PASSPHRASE=...
   ```

   Minimum order: Polymarket enforces ~$5 USDC minimum per leg. Start with $5-10 total per trade ($2.50-5 per leg).

Research tasks:
- Measure order placement latency via py-clob-client: `create_and_post_order()` round-trip time
- Verify GTC order behavior at market boundaries: does an unfilled GTC order persist past market close, or does Polymarket auto-cancel it?
- Understand order IDs: can we poll fill status by order ID? (Yes — `src/bot/live_executor.py:107-118` already does `client.get_order(order_id)`)
- Test: what happens if you place a limit order and the market doesn't exist yet? (Should fail gracefully.)
- Check if there are rate limits on order placement: how many orders per second?


LAYER 5: FILL MONITORING & SETTLEMENT
What: Track whether limit orders get filled, handle partial fills and one-sided fills, and settle P&L after market resolution.
Why: Unlike taker orders (instant fill or fail), maker orders sit in the book and may fill partially, fully, or not at all. The sniper must handle all outcomes correctly — especially the dangerous case of one-sided fills (directional exposure).

Components to implement:

6. **`fill_monitor.py` — Order Fill Tracking**
   - Poll order status every 2s during sniper window using `client.get_order(order_id)`
   - Status values: OPEN → MATCHED/FILLED or CANCELLED
   - Fill scenarios and handling:

   | YES Fill | NO Fill | Result | Action |
   |----------|---------|--------|--------|
   | Full | Full | Both legs filled | Guaranteed profit = `gap_bid × pairs`. Log and celebrate. |
   | Full | None | One-sided (long YES) | Cancel NO order. Settle based on market outcome. |
   | None | Full | One-sided (long NO) | Cancel YES order. Settle based on market outcome. |
   | Partial | Partial | Imbalanced hedge | Cancel remainders. Hedge on min(yes_shares, no_shares), residual settles on outcome. |
   | None | None | No fills | Cancel both. No P&L. Log as "no opportunity." |

   - One-sided fill mitigation:
     - If gap collapses (gap_bid ≤ 0) while one leg is filled and other is open → cancel unfilled leg IMMEDIATELY
     - Don't wait for sniper window to expire — react to book changes
     - If stuck with one-sided position, settle at market close using outcome

7. **`fill_monitor.py` — Outcome Resolution** (reuse from arb bot)
   - After market closes, poll Gamma API for outcome:
     ```python
     GET /markets?slug={slug} → outcomePrices
     yes_price = float(outcomePrices[0])
     # Resolved when yes_price is exactly 0.0 or 1.0
     ```
   - Resolution takes 7-9 minutes after market close (measured empirically in arb bot testing)
   - Poll every 15s for up to 10 minutes (40 attempts), same as arb bot
   - P&L calculation:
     - Both legs filled: `profit = (1.0 - yes_bid - no_bid) × num_pairs`
     - One-sided YES: `profit = (outcome × 1.0 - yes_bid) × yes_shares` (outcome = 1.0 if YES wins, 0.0 if NO wins)
     - One-sided NO: `profit = ((1 - outcome) × 1.0 - no_bid) × no_shares`

Research tasks:
- Measure fill rate during first 20s: what % of maker orders actually get matched?
- Study one-sided fill frequency: how often does only one leg get hit?
- Validate that `client.get_order()` returns accurate partial fill info (shares matched so far)
- Determine if Polymarket auto-cancels open orders when a market resolves


LAYER 6: DATA PERSISTENCE
What: SQLite database tracking every snipe attempt, fill, and settlement.
Why: Need historical data to: (a) calculate running P&L, (b) measure fill rates, (c) optimize thresholds, (d) debug issues.

Components to implement:

8. **`db.py` — Sniper Trade Database**
   - Separate DB file: `data/sniper.db` (independent of arb bot's `data/bot.db`)
   - Schema:
   ```sql
   CREATE TABLE snipes (
       snipe_id        INTEGER PRIMARY KEY AUTOINCREMENT,
       timestamp       TEXT NOT NULL,       -- ISO 8601
       coin            TEXT NOT NULL,       -- BTC, ETH, SOL
       market_type     TEXT NOT NULL,       -- 5m, 15m
       slug            TEXT NOT NULL,
       market_open_ts  INTEGER NOT NULL,    -- Unix timestamp of market open
       market_close_ts INTEGER NOT NULL,

       -- Book state at snipe time
       yes_bid         REAL,
       no_bid          REAL,
       yes_ask         REAL,
       no_ask          REAL,
       gap_bid         REAL,               -- 1.0 - yes_bid - no_bid
       gap_detected_at TEXT,               -- ISO 8601: when gap first exceeded threshold

       -- Orders placed
       yes_order_id    TEXT,
       no_order_id     TEXT,
       num_pairs       REAL,               -- target pair count
       yes_usdc        REAL,               -- USDC allocated to YES leg
       no_usdc         REAL,               -- USDC allocated to NO leg

       -- Fill results
       yes_filled      INTEGER DEFAULT 0,  -- 0/1
       no_filled       INTEGER DEFAULT 0,  -- 0/1
       yes_shares      REAL DEFAULT 0,     -- actual shares received
       no_shares       REAL DEFAULT 0,
       fill_time_ms    INTEGER,            -- time from order to fill

       -- Settlement
       market_outcome  TEXT,               -- YES, NO, VOID
       pnl             REAL,               -- final P&L in USDC
       status          TEXT DEFAULT 'PENDING'  -- PENDING, FILLED, PARTIAL, SETTLED, CANCELLED
   );

   CREATE TABLE sniper_log (
       id              INTEGER PRIMARY KEY AUTOINCREMENT,
       timestamp       TEXT NOT NULL,
       level           TEXT NOT NULL,       -- INFO, WARN, ERROR
       message         TEXT NOT NULL
   );
   ```

Research tasks:
- None — straightforward SQLite, reuse patterns from `src/bot/db.py`


LAYER 7: CONFIGURATION
What: Centralized configuration for all sniper parameters.
Why: Tuning thresholds, timing windows, and trade sizes is expected. Keep them in one place, not scattered.

Components to implement:

9. **`config.py` — Sniper Configuration**
   ```python
   # ── Mode ──
   MODE = "LIVE"                        # Always live (no paper mode — use tiny sizes instead)

   # ── Markets ──
   COINS = ["btc", "eth", "sol"]
   MARKET_TYPES = ["5m", "15m"]         # 6 total markets
   MARKET_INTERVALS = {"5m": 300, "15m": 900}

   # ── Timing ──
   PREFETCH_LEAD_SECONDS = 30           # Fetch token IDs this many seconds before open
   WS_CONNECT_LEAD_SECONDS = 5          # Connect WebSocket this early
   SNIPER_WINDOW_SECONDS = 20           # How long after open to look for opportunities
   OUTCOME_POLL_SECONDS = 600           # Max time to wait for outcome (10 min)

   # ── Thresholds ──
   MIN_SNIPER_GAP = 0.03               # Minimum gap_bid to trigger a snipe
   # Lower than arb bot's 0.05 because:
   # - Maker fee is 0% (no fee drag)
   # - We expect smaller but more frequent gaps at market open

   # ── Sizing ──
   TRADE_USDC = 5.0                     # USDC per trade (both legs combined)
   MAX_CONCURRENT_SNIPES = 2            # Max simultaneous open snipe positions
   MAX_DAILY_LOSS = 20.0                # Kill switch: stop if daily loss exceeds this

   # ── API ──
   GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"
   WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

   # ── Credentials (from .env) ──
   # Shared with arb bot — same wallet
   POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
   POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
   POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")
   POLYMARKET_API_SECRET = os.getenv("POLYMARKET_API_SECRET", "")
   POLYMARKET_API_PASSPHRASE = os.getenv("POLYMARKET_API_PASSPHRASE", "")

   # ── Dashboard ──
   DASHBOARD_PORT = 8001                # Separate from arb bot's 8000
   ```


LAYER 8: MONITORING DASHBOARD
What: FastAPI web app with SSE for real-time monitoring of snipe attempts, fills, and P&L.
Why: The sniper runs on a remote GCP VM. Need a browser-accessible dashboard to monitor without SSH.

Components to implement:

10. **`main.py` — FastAPI Server**
    - Separate app on port 8001 (arb bot uses 8000)
    - Endpoints:
      - `GET /` → dashboard HTML
      - `GET /api/status` → current engine state (next market open, book state, position info)
      - `GET /api/trades` → recent snipe history from DB
      - `GET /api/stats` → aggregate stats (fill rate, P&L, win/loss count)
      - `GET /sse` → Server-Sent Events stream (real-time book + gap updates during sniper window)
      - `POST /api/start` / `POST /api/stop` — engine control
    - Auto-starts engine on FastAPI startup (lesson learned from arb bot)

11. **`static/dashboard.html` — Single-Page Dashboard**
    - Stats bar: Free USDC | Locked USDC | Daily P&L | Fill Rate | Snipes Today
    - Countdown timer to next market open
    - Live book display during sniper window (YES bid/ask, NO bid/ask, gap_bid)
    - Trade log table: time, coin, type, gap_bid, fills, P&L, status
    - Visual indicator: green flash when snipe fires, red when one-sided fill detected

Research tasks:
- None — reuse dashboard patterns from `src/bot/static/dashboard.html`


LAYER 9: DEPLOYMENT (GCP VM)
What: Deploy alongside existing arb bot on the same GCP VM.
Why: Reuse existing infrastructure ($5/month e2-small). Two bots share the same wallet but run independently.

Components to implement:

12. **systemd service: `sniper.service`**
    ```ini
    [Unit]
    Description=Polymarket Sniper Bot
    After=network.target

    [Service]
    Type=simple
    User=tangmo82
    WorkingDirectory=/home/tangmo82/polymarket_new
    ExecStart=/home/tangmo82/polymarket_new/venv/bin/python -m src.sniper.main
    Restart=always
    RestartSec=5
    EnvironmentFile=/home/tangmo82/polymarket_new/.env

    [Install]
    WantedBy=multi-user.target
    ```

    GCP VM details:
    - Instance: polymarket-bot, e2-small, us-east4-a
    - IP: 34.86.131.153
    - Existing service: `polymarket-bot.service` (arb bot, port 8000)
    - New service: `sniper.service` (port 8001)
    - Firewall: open port 8001 for dashboard access

    Deployment workflow:
    ```bash
    # Local: tar + upload
    tar -czf sniper-deploy.tar.gz src/sniper/ requirements.txt
    gcloud compute scp sniper-deploy.tar.gz tangmo82@polymarket-bot:~/polymarket_new/

    # VM: extract + install + start
    cd ~/polymarket_new && tar -xzf sniper-deploy.tar.gz
    source venv/bin/activate && pip install -r requirements.txt
    sudo cp sniper.service /etc/systemd/system/
    sudo systemctl daemon-reload && sudo systemctl enable sniper && sudo systemctl start sniper
    ```

Research tasks:
- Confirm e2-small has enough RAM for two Python processes (~100MB each, 1GB total available)
- Check if both bots using the same Polymarket wallet creates conflicts (simultaneous order placement)
- Test: can two ClobClient instances with the same credentials coexist?


CRITICAL RISKS & OPEN QUESTIONS

| # | Risk | Severity | Mitigation | Status |
|---|------|----------|------------|--------|
| 1 | Gamma API doesn't have slug at T-30s | Medium | Retry every 2s until T+5s; abort if unavailable | **Must test** |
| 2 | Book empty at T+0 (no bids/asks) | Medium | Wait for first book update; don't snipe if no prices by T+10s | **Must test** |
| 3 | py-clob-client blocks event loop | High | `asyncio.to_thread()` wrapper (proven in arb bot) | Known fix |
| 4 | One-sided fill (only YES or NO fills) | High | Cancel unfilled leg immediately when gap closes; settle on outcome | Handled in design |
| 5 | Both bots place orders simultaneously | Medium | Different strategies target different signals; unlikely conflict | **Must test** |
| 6 | Gap is too small after fees | Low | Maker fee is 0%; gap_bid > 0.03 is pure profit if both fill | Non-issue for makers |
| 7 | Market resolution takes 7-9 minutes | Low | Already solved: 10-min polling with 15s intervals | Proven |
| 8 | Polymarket rate limits order spam | Medium | Max 1 pair per cycle; space orders 100ms apart | **Must test** |
| 9 | Edge disappears as more snipers compete | Long-term | Monitor fill rates weekly; reduce trade size or abandon if edge decays | Ongoing |
| 10 | Real money loss during testing | High | Start at $5/trade; MAX_DAILY_LOSS kill switch at $20 | Config-based |


REUSABLE CODE FROM src/bot/

| Component | Source File | Reuse In | Method |
|-----------|------------|----------|--------|
| Slug calculation | `engine.py:163-170` | `clock.py` | Copy + adapt for "next open" |
| Token ID fetch | `engine.py:172-186` | `prefetch.py` | Copy (identical pattern) |
| WS book parsing | `engine.py` | `book_monitor.py` | Adapt (same event format) |
| ClobClient init | `live_executor.py:44-60` | `order_placer.py` | Copy (identical setup) |
| Limit order placement | `live_executor.py:63-80` | `order_placer.py` | Copy + pair logic |
| Order status check | `live_executor.py:107-118` | `fill_monitor.py` | Copy (identical) |
| Cancel order | `live_executor.py:98-105` | `order_placer.py` | Copy (identical) |
| Outcome detection | `order_manager.py:_fetch_outcome()` | `fill_monitor.py` | Copy (proven logic) |
| Fee model | `fee.py:48-65` (maker_arb_profit) | `order_placer.py` | Import or copy |
| DB patterns | `db.py` | `db.py` | Adapt schema |

Start with direct copies. Extract to `src/shared/` only if maintaining two copies becomes burdensome.


SUGGESTED BUILD ORDER

Phase 1 (Day 1): Foundation
- Execute project cleanup (archive old files)
- Create `src/sniper/` package with `__init__.py` and `config.py`
- Implement `clock.py` — test by printing next 10 market opens for all 6 markets
- Implement `prefetch.py` — test by fetching token IDs for an upcoming market

Phase 2 (Day 1-2): Data Collection
- Implement `book_monitor.py` — connect to WS, log gap_bid for first 30s of each market
- Run for 2-4 hours across multiple market opens
- Analyze: how often is gap_bid > 0.03? How long do gaps last? What's the average gap size?
- **DECISION GATE**: If gaps are rarely > 0.03 or last < 2 seconds, the strategy may not work. Reconsider before building execution.

Phase 3 (Day 2-3): Execution
- Implement `order_placer.py` — ClobClient wrapper with place_pair() and cancel
- Implement `fill_monitor.py` — fill tracking + outcome resolution
- Implement `db.py` — sniper trade database
- Implement `sniper_engine.py` — main loop tying everything together

Phase 4 (Day 3): Dashboard & Testing
- Implement `main.py` + `dashboard.html`
- Dry run: full loop with order placement disabled, logging what it WOULD do
- Live micro-test: $5 trade on a single BTC 5m market, verify fill + settlement

Phase 5 (Day 4+): Deploy & Validate
- Deploy to GCP VM as `sniper.service`
- Run 24h soak test on all 6 markets
- Analyze: fill rate, one-sided fill %, net P&L
- If profitable: gradually increase TRADE_USDC ($5 → $10 → $25)


KEY METRICS TO TARGET

- Sniper window utilization: % of cycles where a gap > threshold is detected
- Fill rate: % of placed orders that get filled (both legs)
- One-sided fill rate: % of trades where only one leg fills (want this LOW)
- Average gap_bid at snipe time: target > 0.03
- Average P&L per snipe: positive after accounting for one-sided losses
- Daily trade count: depends on gap frequency (estimate 5-20 per day across 6 markets)
- Monthly cost: $0 incremental (same VM, maker fee = 0%)


CRITICAL LESSONS (from arb bot experience)

1. **Outcome detection is tricky** — Gamma API `resolved` field is always None. Must infer from `outcomePrices` being exactly 0.0 or 1.0. Resolution takes 7-9 minutes. Don't guess — poll until final.
2. **DB migrations matter** — Always add ALTER TABLE for new columns with try/except. Old DBs crash otherwise.
3. **py-clob-client is synchronous** — Every call blocks. Always wrap in `asyncio.to_thread()`.
4. **Auto-start the engine** — Don't require manual "START" click. Bot should run immediately on service start.
5. **Kill switch saves money** — MAX_DAILY_LOSS prevents runaway losses during bugs.
6. **Test with real market cycles** — Paper trading with random fills is misleading. Use as-if-crossed logic or go live with tiny sizes.
7. **Partial fills are the hard part** — Both-legs-filled is easy math. One-sided fills require outcome-based settlement. Get this right from day one.
