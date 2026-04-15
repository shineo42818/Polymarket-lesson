# Arbitrage Research Plan: Polymarket vs Binance Crypto Markets

## Hypothesis

Polymarket's short-term crypto prediction markets (e.g., "Bitcoin Up or Down in the next 5 minutes") react **slower** than real-time crypto exchanges like Binance. When a significant price move happens on Binance, there is a time window (lag) before Polymarket's odds adjust — creating an arbitrage opportunity.

This lag may be **amplified during black swan events** (e.g., Iran military strikes, ETF approvals, exchange hacks) when sudden, large price movements overwhelm the normal market-making process.

## Key Questions

1. **How fast does Polymarket react to real price moves on Binance?**
   - Is there a measurable lag (seconds? minutes?)
   - Is the lag consistent or variable?

2. **How big is the pricing gap on Polymarket?**
   - Yes price + No price should equal $1.00 in a perfect market
   - When it doesn't (e.g., 0.76 + 0.19 = 0.95), the gap represents potential profit
   - How does this gap behave over time?

3. **Is the lag bigger during black swan / news events?**
   - Compare lag during normal hours vs. major news events
   - Do whales pile in faster during these events?

4. **Is this profitable after fees?**
   - What are Polymarket's trading fees?
   - What is the minimum price gap needed to break even?

5. **Can we build a detection system?**
   - If Binance BTC moves > X% in Y seconds, is that a reliable signal to bet on Polymarket?
   - What are the optimal X and Y thresholds?

## Data Sources

| Source | URL | Key Needed? | Data |
|--------|-----|-------------|------|
| Binance API | api.binance.com | No | Real-time & historical crypto prices (1-min candles) |
| Polymarket Gamma API | gamma-api.polymarket.com | No | Market odds, price history |
| Polymarket Data API | data-api.polymarket.com | No | Whale trades, positions, leaderboard |
| Polymarket CLOB API | clob.polymarket.com | No (read-only public) | Order book depth, midpoint prices |
| Etherscan V2 API | api.etherscan.io/v2/api | Yes (have it) | On-chain transaction timestamps |
| News events | Manual + web search | No | Timestamps of major market-moving events |

## Research Phases

### Phase 1: Data Collection (Scripts to Build)

**1A: Binance Historical Data**
- Script: `src/arbitrage/collect_binance.py`
- Pull 1-minute candle data (open, high, low, close, volume) for:
  - BTC/USDT
  - ETH/USDT
  - SOL/USDT
  - XRP/USDT
- Time range: past 7 days to start
- Save to: `data/binance_btc.csv`, `data/binance_eth.csv`, etc.

**1B: Polymarket Historical Odds**
- Script: `src/arbitrage/collect_polymarket.py`
- Pull price history for active "Up or Down" crypto markets
- Match the same time range as Binance data
- Save to: `data/polymarket_crypto_odds.csv`

**1C: Whale Trade Timestamps**
- We already have this from `get_trades.py`
- Extract precise timestamps of whale trades on crypto markets
- Save to: `data/whale_timestamps.csv`

### Phase 2: Lag Analysis

**2A: Normal Conditions**
- Script: `src/arbitrage/analyze_lag.py`
- Align Binance price data and Polymarket odds by timestamp
- For each significant Binance price move (> 0.5% in 5 min):
  - Record when the move started on Binance
  - Record when Polymarket odds shifted in the same direction
  - Calculate the lag (in seconds/minutes)
- Output: distribution of lag times, average lag, median lag
- Visualize: scatter plot of Binance move time vs Polymarket reaction time

**2B: Black Swan / News Events**
- Script: `src/arbitrage/analyze_events.py`
- Identify major events in the past week/month:
  - Iran military strikes (Feb 28, 2026)
  - Any sudden BTC moves > 3% within 1 hour
  - Regulatory announcements, exchange incidents
- For each event:
  - Zoom into minute-by-minute Binance + Polymarket data
  - Measure the lag specifically during the event
  - Compare to normal-condition lag
- Output: event-by-event lag comparison table

### Phase 3: Gap Analysis

- Script: `src/arbitrage/analyze_gap.py`
- For each "Up or Down" market snapshot:
  - Calculate: Yes price + No price
  - The difference from 1.00 is the "gap"
- Track gap over time: when is it widest?
- Correlate gap size with:
  - Time of day
  - Market volatility (Binance)
  - Proximity to market close time
- Output: gap distribution chart, gap vs. volatility chart

### Phase 4: Whale Timing Analysis

- Script: `src/arbitrage/analyze_whales.py`
- Overlay whale trade timestamps onto:
  - Binance price chart
  - Polymarket odds chart
- For each whale trade:
  - Was Binance already moving in that direction? (leading indicator confirmation)
  - How many seconds/minutes before the Polymarket odds caught up?
  - Did the whale trade profitably?
- Output: whale accuracy rate, average time-advantage over market

### Phase 5: Strategy Simulation (Backtest)

- Script: `src/arbitrage/simulate_strategy.py`
- Define a simple strategy:
  - IF Binance BTC moves > X% in Y seconds
  - THEN buy the corresponding direction on Polymarket
  - EXIT when the Polymarket odds adjust (or at market close)
- Test across multiple X and Y thresholds
- Calculate: win rate, average profit, total PnL, max drawdown
- Account for: trading fees, slippage, execution delay
- Output: strategy performance table, equity curve chart

## Expected Deliverables

| Deliverable | Format | Description |
|-------------|--------|-------------|
| Lag distribution | Chart (.png) | How fast does Polymarket react to Binance? |
| Event analysis | Table (.csv) | Lag during black swan events vs normal |
| Gap analysis | Chart (.png) | When is the arbitrage gap widest? |
| Whale timing map | Chart (.png) | Are whales trading during the lag window? |
| Strategy backtest | Table + Chart | Would this strategy actually make money? |

## Risks & Limitations

- **API rate limits**: Binance and Polymarket may limit how much data we can pull per minute
- **Data gaps**: Polymarket may not have granular enough historical odds data (may need to collect in real-time going forward)
- **Execution reality**: Even if a lag exists, executing trades fast enough in practice is harder than in a backtest
- **Market evolution**: If many people exploit this lag, it will shrink over time (arbitrage is self-correcting)
- **Fees**: Trading fees could eat the profit if the gap is too small

## Tools & Libraries

Already installed:
- `requests` — API calls
- `pandas` — data analysis
- `matplotlib` — charts
- `web3` — blockchain interaction
- `python-dotenv` — API key management

May need to install:
- `numpy` — numerical calculations (for statistics)
- `scipy` — statistical tests (optional)

## Next Steps

1. Start with Phase 1A — pull Binance data (this is the foundation)
2. Then Phase 1B — pull matching Polymarket data
3. Quick visual overlay to see if the lag hypothesis holds before deep analysis

---

*Research plan created: February 28, 2026*
*Project: Polymarket Whale Tracker → Arbitrage Research*