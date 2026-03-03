# Research Plan: Binance → Chainlink Lead-Lag Hypothesis

## Background & Hypothesis

**Claim (from strategy.md Section 12):** Binance spot price moving ≥0.3% in 30 seconds is a leading indicator that Chainlink's on-chain oracle will update within 30s, and this divergence causes Polymarket market-makers to widen spreads → `gap_bid` opens.

**Problem:** Both the 0.3% threshold and the 30s lag are assumptions — never empirically tested. The current `collect_binance.py` uses 1-minute candles, which cannot detect sub-minute lag at all.

**Research goal:** Determine empirically:
1. Does Binance price move **before** Chainlink oracle updates? By how many seconds?
2. Is any lag window ≥ 0.5 seconds (usable for trading execution)?
3. What are the correct thresholds (momentum %, lookback window)?

---

## Architecture Map: What We're Actually Measuring

```
Binance Spot (CEX)
  │
  │  [Hypothesis: leads by 0.5–120s]
  ▼
Chainlink DON nodes (observe CEX, reach OCR consensus off-chain)
  │
  │  [Ethereum block time: min 12s per update]
  ▼
Chainlink on-chain AnswerUpdated event
  │
  │  [Polymarket MMs observe this → widen quotes]
  ▼
gap_bid opens on Polymarket (our opportunity)
```

**Key finding from research:** Chainlink's on-chain Data Feed lags Binance by **12 seconds minimum** (one Ethereum block), typically **1–5 minutes** in active markets. This lag is actually structural and exploitable — the question is whether it's *predictable* from Binance momentum.

---

## Phase 0: Instrument Clarification (Before Writing Code)

**Problem to resolve first:** Which Chainlink oracle does Polymarket actually use?

- Ethereum mainnet BTC/USD: `0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c` (0.5% deviation, 1h heartbeat)
- Ethereum mainnet ETH/USD: `0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419` (0.5% deviation, 1h heartbeat)
- Polymarket may use **Polygon** or another chain (cheaper gas), which would have different update frequency

**Action:** Check Polymarket's resolution documentation or contract code to confirm the exact oracle contract address and chain used for BTC/ETH updown markets.

---

## Phase 1: Data Collection Infrastructure (New Scripts Required)

### 1A — `collect_binance_ticks.py` (Replace 1-minute candles)

**Current problem:** `collect_binance.py` polls REST API for 1-minute candles. Cannot detect <60s lags.

**New approach:** Connect to Binance aggTrade WebSocket stream. Each event gives:
- `T` = matching engine trade timestamp (microsecond precision with `timeUnit=MICROSECOND`)
- `E` = event publish timestamp
- `p` = price, `q` = quantity, `m` = buyer/seller direction

```
Stream URL: wss://data-stream.binance.vision/stream?streams=btcusdt@aggTrade/ethusdt@aggTrade&timeUnit=MICROSECOND
```

**What to record per row:**

| Field | Source | Notes |
|---|---|---|
| `recv_ns` | `time.time_ns()` local | Local receive timestamp |
| `trade_us` | `T` field | Binance matching engine time (microseconds) |
| `event_us` | `E` field | Binance event publish time |
| `symbol` | `s` | BTCUSDT / ETHUSDT |
| `price` | `p` | Trade price |
| `qty` | `q` | Volume |
| `is_sell` | `m` | Direction indicator |

**Output:** `data/binance_ticks_btc.csv`, `data/binance_ticks_eth.csv`

**Key design decisions:**
- Write to CSV in batches (every 1,000 rows or 10 seconds) — avoid per-tick disk I/O
- Rotate files hourly to keep files manageable
- Use `asyncio` + `websockets` library (same pattern as `gap_monitor.py`)
- Run NTP sync check on startup

---

### 1B — `collect_chainlink.py` (New Script)

**Source:** Ethereum (or Polygon) WebSocket node → subscribe to `AnswerUpdated` events from the Chainlink aggregator contract.

**Requires:** Alchemy or Infura free-tier API key (provides WebSocket access)

**WebSocket endpoint:**
```
wss://eth-mainnet.g.alchemy.com/v2/YOUR_KEY
```

**Subscription payload (`eth_subscribe logs`):**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "eth_subscribe",
  "params": ["logs", {
    "address": "<aggregator_address>",
    "topics": ["0x0559884fd3a460db3073b7fc896cc77986f16e378210ded43186175bf646fc5f"]
  }]
}
```

**`AnswerUpdated` event fields:**
- `topics[1]` = `current` (int256, indexed) → price in 8 decimals → divide by 1e8
- `topics[2]` = `roundId` (uint256, indexed)
- `data` = `updatedAt` (uint256, non-indexed) → unix seconds (NOTE: second-precision, not ms)
- `blockNumber` from log → need a second call to get block timestamp for ms-precision

**What to record per row:**

| Field | Source | Notes |
|---|---|---|
| `recv_ns` | `time.time_ns()` local | When our script received the log |
| `block_number` | log.blockNumber | Ethereum block |
| `block_timestamp_s` | `eth_getBlockByNumber` | Unix seconds (block time, ~12s resolution) |
| `updated_at_s` | decoded `data` field | Oracle's own timestamp (seconds) |
| `price_usd` | topics[1] / 1e8 | Oracle price |
| `round_id` | topics[2] | Chainlink round ID |
| `symbol` | config | BTC or ETH |

**Important nuance:** `updatedAt` in the event is seconds-precision. The `recv_ns` local timestamp gives us sub-millisecond precision of when the event arrived at our machine — this is what we use for lag calculation.

**Output:** `data/chainlink_updates_btc.csv`, `data/chainlink_updates_eth.csv`

---

### 1C — Simultaneous Dual Collection

Run both scripts in parallel for a minimum of **24 hours** of concurrent data:
```bash
python src/arbitrage/collect_binance_ticks.py &
python src/arbitrage/collect_chainlink.py &
```

**Clock synchronization requirement:** Both scripts must run on the same machine (same `time.time_ns()` clock). If split across machines, NTP offset must be measured and corrected. Target accuracy: ≤10ms.

**Minimum data needed:**
- At least **100 Chainlink update events** per symbol for event study statistical significance
- At Ethereum mainnet (1–5 min updates, active market): 100 events ≈ 1.5–8 hours
- Target: 24 hours minimum, 72 hours ideal

---

## Phase 2: Exploratory Data Analysis (EDA)

Before running the full lead-lag analysis, characterize each series independently.

### 2A — Chainlink Update Pattern Analysis

Questions to answer:
1. What is the **empirical inter-update interval** distribution? (Mean, median, 95th percentile)
2. Are updates **deviation-triggered** (price moved ≥0.5%) or **heartbeat-triggered** (60-minute timeout)?
3. What is the **size of each oracle update** in % terms?
4. Is there a **time-of-day pattern**? (US market hours vs Asia hours)

**Metric to compute:** For each `AnswerUpdated` event, compute:
- `delta_t_s = current_updated_at - prev_updated_at` (inter-update interval)
- `delta_price_pct = abs(current_price - prev_price) / prev_price * 100`
- Classify: `heartbeat` (delta_t ≈ 3600s) vs `deviation` (delta_price ≥ 0.5%)

### 2B — Binance Tick Characterization

Questions to answer:
1. What is the **1-second, 5-second, 30-second return distribution**?
2. How often does a **±0.3% in 30s** threshold trigger? (Event frequency)
3. What is the **autocorrelation structure** of 1-second returns?

---

## Phase 3: Lead-Lag Analysis (Four Methods, in Order)

### Method 1 — Event Study (Primary — Run First)

**Most interpretable. Fewest assumptions. Best for threshold discovery.**

**Algorithm:**
1. **Detect Binance momentum events:** Find all times where `|price_now - price_30s_ago| / price_30s_ago ≥ threshold` (initially test threshold = 0.3%)
2. **Debounce:** Suppress events within 30s of a prior event (avoid clusters counting as multiple events)
3. **Measure Chainlink response:** For each Binance event, find the next Chainlink update within a 120-second window
4. **Compute:** latency (seconds from Binance event to CL update), direction alignment (did CL move same direction?)

**Output metrics:**
- Response rate: % of Binance events followed by a CL update within 60s
- Latency distribution: median, 25th/75th/95th percentile
- Direction alignment rate: % where CL moved same direction as Binance
- Comparison to **baseline rate** (unconditional P(CL updates in any 60s window))

**Decision criteria:**
- Response rate significantly above baseline → Binance predicts CL updates
- Median latency → defines the exploitable trading window
- If median latency > 0.5s → hypothesis supported (we have a trading window)

### Method 2 — Conditional Probability Matrix

**Run immediately after event study, using same event set.**

Build a 2D matrix of:
```
P(CL updates within N seconds | Binance moved ≥ X%)
```

For N ∈ {5, 10, 15, 30, 60} seconds and X ∈ {0.1%, 0.2%, 0.3%, 0.5%, 1.0%}

**Output:** Which (X, N) cell gives the best lift (observed / baseline)? This empirically calibrates the Section 12 thresholds (currently guessed at 0.3% / 30s).

### Method 3 — Cross-Correlation Function (CCF)

**Run on 1-second resampled data to get a clean lag estimate.**

1. Resample both series to 1-second bars: `last()` for Binance, `last().ffill()` for Chainlink (forward-fill between updates)
2. Compute log returns on both 1-second series
3. Compute CCF at lags −120s to +120s
4. Look for: positive peak CCF at lag +k (k > 0) = Binance leads Chainlink by k seconds

**Warning:** CCF will underestimate the true relationship because Chainlink's threshold-based updates create non-linear sparsity. CCF is a sanity check, not the primary test.

**Output:** Peak lag in seconds, CCF value at peak, Bartlett 95% confidence interval

### Method 4 — Granger Causality (Run Last, Only If Enough Data)

**Formal statistical test. Requires ≥6,000 observations at 1-second resolution (≥1.5 hours).**

1. Check stationarity of both 1-second return series (ADF test)
2. Select optimal VAR lag order (AIC/BIC, test up to 60 lags)
3. Run `grangercausalitytests([chainlink_returns, binance_returns], maxlag=60)`
4. Report p-values at each lag; significant lags (p < 0.05) confirm Granger causality

**Multiple comparison correction:** Apply Benjamini-Hochberg FDR across all tested lags.

---

## Phase 4: Threshold Calibration & Trading Window Validation

After Phase 3, answer these specific questions:

| Question | Method | Output |
|---|---|---|
| What is the true median Binance → CL lag? | Event study latency distribution | e.g., "median 8s, 95th pct 45s" |
| What momentum threshold maximizes lift? | Conditional probability matrix | e.g., "0.5% not 0.3%" |
| Is the lag stable across time? | Rolling 6-hour windows of event study | Stability check |
| Is the lag different for BTC vs ETH? | Separate analysis per symbol | May differ |
| Is the lag direction-symmetric? | Separate UP vs DOWN event studies | Asymmetry check |
| Is our 0.5s execution window realistic? | p5 of latency distribution | Must be >0.5s |

**Go/No-Go decision:** If the p5 of latency (fastest 5th percentile events) is consistently >0.5 seconds, the hypothesis is supported and the strategy window is validated. If latency is <0.5s at p50, the signal is too fast for our execution.

---

## Phase 5: Connecting Back to Polymarket (Secondary Validation)

The full causal chain is: Binance move → CL lag grows → MM spreads widen → `gap_bid` opens.

Once the Binance→CL lag is validated, run a second analysis using existing `gap_log.csv`:

1. For each `gap_bid ≥ 0.05` event in `gap_log.csv`, look back at:
   - Binance momentum in the prior 30s (needs tick data)
   - Chainlink staleness (`now - last_CL_update_s`) at that moment
2. Compute: P(gap_bid ≥ 0.05 | CL lag > 30s AND Binance momentum > X%)
3. Compare to unconditional P(gap_bid ≥ 0.05)

This closes the loop from Binance signal → Polymarket opportunity.

---

## Phase 6: Implementation Updates

After Phases 1–5, update the codebase:

| File | Change |
|---|---|
| `collect_binance.py` | Keep for historical candles; do NOT remove |
| `collect_binance_ticks.py` | New script — live tick collection |
| `collect_chainlink.py` | New script — on-chain oracle events |
| `analyze_signal.py` | Replace hardcoded 5m/30s windows with empirically validated values |
| `gap_monitor.py` | Add CL staleness signal (optional, once lag is confirmed) |
| `strategy.md` | Update Section 12 with empirical thresholds |

---

## Deliverables & Decision Tree

```
Phase 1 → 24h+ of dual tick data collected
    ↓
Phase 2 → EDA: CL updates ~every N minutes on average
    ↓
Phase 3 → Event Study result
    ├── Response rate > baseline? YES → lag exists
    │       ↓
    │   Median latency > 0.5s? YES → HYPOTHESIS CONFIRMED
    │       ↓
    │   Run Phase 4 calibration → update thresholds in strategy.md
    │
    └── Response rate ≤ baseline? → HYPOTHESIS REJECTED
            → Binance is NOT a leading indicator of CL
            → Remove Signal B from Section 12
            → Rely only on Signal A (gap_ask ≥ 0.05)
```

---

## Open Questions to Resolve Before Starting

1. **Which chain does Polymarket use for oracle resolution?** (Ethereum mainnet vs Polygon — different update frequencies)
2. **Do we have access to an Alchemy or Infura API key?** (Required for Chainlink WebSocket subscription)
3. **How long can we run the collectors?** (Minimum 24h, ideally 72h for statistical power)
4. **Should we start with BTC only, then add ETH?** (Simpler to debug one symbol first)

---

## Technical References

### Binance WebSocket
- Stream: `wss://data-stream.binance.vision/stream?streams=btcusdt@aggTrade/ethusdt@aggTrade&timeUnit=MICROSECOND`
- Use `T` (trade time) field as authoritative Binance price timestamp
- `@bookTicker` has no exchange timestamp — use `@aggTrade` only
- Max 1,024 streams per connection; 300 new connections per 5-minute window

### Chainlink Contract Addresses (Ethereum Mainnet)
- BTC/USD Proxy: `0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c`
- ETH/USD Proxy: `0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419`
- Deviation threshold: 0.5% | Heartbeat: 3,600 seconds
- `AnswerUpdated` topic0: `0x0559884fd3a460db3073b7fc896cc77986f16e378210ded43186175bf646fc5f`
- NOTE: Subscribe to underlying aggregator address, not proxy (get via Etherscan or `aggregatorAddress()` call)

### Chainlink Data Streams (Enterprise — Future Option)
- WS: `wss://ws.dataengine.chain.link/api/v1/ws?feedIDs=...`
- Sub-second latency but requires API key from Chainlink (not public)
- ETH/USD Feed ID: `0x000359843a543ee2fe414dc14c7e7920ef10f4372990b79d6361cdc0dd1ba782`

### Key Python Libraries
| Task | Library | Function |
|---|---|---|
| Resample tick data to 1s grid | `pandas` | `Series.resample('1s').last().ffill()` |
| Irregular time join | `pandas` | `pd.merge_asof(..., direction='forward', tolerance=...)` |
| Cross-correlation (FFT) | `scipy.signal` | `correlate(y, x, mode='full')` |
| ADF stationarity test | `statsmodels.tsa.stattools` | `adfuller(series)` |
| Granger causality | `statsmodels.tsa.stattools` | `grangercausalitytests(data, maxlag=k)` |
| Binomial significance test | `scipy.stats` | `binom_test(k, n, p, alternative='greater')` |
| FDR multiple test correction | `statsmodels.stats.multitest` | `multipletests(pvals, method='fdr_bh')` |
