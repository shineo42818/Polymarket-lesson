# Polymarket BTC/ETH/SOL Arbitrage Strategy

## 1. Market Structure

Polymarket operates binary prediction markets on 5-minute and 15-minute BTC, ETH, and SOL price moves. Each market resolves to either $1.00 (winner) or $0.00 (loser) per token.

### Covered Markets (6 total)
| Market Key | Coin | Interval | Slug Pattern |
|------------|------|----------|--------------|
| `btc_5m`   | BTC  | 5 min    | `btc-updown-5m-{ts}` |
| `btc_15m`  | BTC  | 15 min   | `btc-updown-15m-{ts}` |
| `eth_5m`   | ETH  | 5 min    | `eth-updown-5m-{ts}` |
| `eth_15m`  | ETH  | 15 min   | `eth-updown-15m-{ts}` |
| `sol_5m`   | SOL  | 5 min    | `sol-updown-5m-{ts}` |
| `sol_15m`  | SOL  | 15 min   | `sol-updown-15m-{ts}` |

### Slug Calculation
```
interval = 300 (5m) or 900 (15m)
now      = floor(Date.now() / 1000)
ts       = now - (now % interval)          // align to interval boundary
slug     = `{coin}-updown-{type}-{ts}`
closes_at = ts + interval
```
This mirrors `get_current_slug()` in `gap_monitor.py`.

### Token ID Fetching
```
GET https://gamma-api.polymarket.com/markets?slug={slug}
→ response[0].clobTokenIds (JSON-parsed array)
→ clobTokenIds[0] = YES token ID
→ clobTokenIds[1] = NO  token ID
```

---

## 2. Price Streaming — WebSocket

**URL:** `wss://ws-subscriptions-clob.polymarket.com/ws/market`

**Subscription message** (sent on connect):
```json
{ "assets_ids": ["<yes_id>", "<no_id>", ...], "type": "market" }
```

### Event Types

#### `book` (initial snapshot on subscribe)
```
bids: ascending array [{price, size}]  → best_bid = bids[bids.length - 1].price
asks: ascending array [{price, size}]  → best_ask = asks[0].price
                                         ask_size  = asks[0].size
```

#### `price_change` (real-time update)
```
event.price_changes = [{
  asset_id, best_bid, best_ask, best_ask_size, ...
}, ...]
```
Must iterate `price_changes` array (not a flat event).

---

## 3. Gap Formulas

### AA Scenario — Market Orders (both sides pay ask)
```
gap_ask = 1.0 - yes_ask - no_ask
```
- Always approximately −0.01 to −0.05 in normal markets (market makers embed ~1% spread).
- **This is the scenario the mock trader simulates** (gap_ask ≥ 0.05 fires a trade).
- Rare but real: gap_ask reaches +0.05 to +0.08 during liquidity dislocations.

### BB Scenario — Limit Orders (both sides filled at bid)
```
gap_bid = 1.0 - yes_bid - no_bid
```
- Can be positive (+0.05 to +0.20) even when gap_ask is negative.
- What professional/whale traders use by pre-positioning limit orders.
- **Informational only** in the mock trader; cannot reliably simulate limit-order fills.

### Worked Example
```
yes_ask = 0.47,  no_ask = 0.48   →  gap_ask = 1.0 - 0.47 - 0.48 = +0.05  ✓ SIGNAL
yes_bid = 0.45,  no_bid = 0.46   →  gap_bid = 1.0 - 0.45 - 0.46 = +0.09  (bonus info)
```

### Sanity Filter
```javascript
if (yes_ask > 0.90 && no_ask > 0.90) skip;  // stale stale near market close
```
Near settlement, market makers withdraw liquidity. Stale asks for the losing side can sit at 0.99, creating a garbage gap. This filter (from `gap_monitor.py` line 333) prevents false signals.

---

## 4. Signal Rules

| Condition | Action |
|-----------|--------|
| `gap_ask >= 0.05` | **FIRE mock trade** (AA scenario) |
| `gap_bid >= 0.05` | Show `[BID!]` indicator only (informational) |
| Both >= 0.05 | Fire trade + show `[BOTH!]` indicator |
| Sanity filter triggered | Skip entirely |
| Position already open in same slug | Skip (no stacking) |

**Threshold:** `MIN_PROFITABLE_GAP = 0.05` (matches `gap_monitor.py` line 22).

---

## 5. Fee Model

From `analyze_whale_patterns.py` (`fee_per_share` function, line 49–56):

```
fee_per_share(p) = 0.25 × (p × (1 − p))²

Examples:
  p = 0.50  →  fee = 0.25 × 0.0625 = 0.015625  (1.5625% — maximum)
  p = 0.30  →  fee = 0.25 × (0.21)² = 0.011025  (1.1025%)
  p = 0.47  →  fee = 0.25 × (0.2491)² = 0.015519 (1.55%)
  p = 0.10  →  fee = 0.25 × 0.0081 = 0.002025   (0.2025%)
```

Fee is deducted from **shares received** (tokens), not from USDC paid.

Source: Polymarket crypto market taker fee formula effective 2026-03-06.

---

## 6. Trade Execution

When `gap_ask >= 0.05` is detected and the mock trader is running:

### Position Sizing
```
trade_usdc  = portfolio.usdcBalance × (tradeSizePct / 100)
half_usdc   = trade_usdc / 2                   // split 50/50 YES and NO
```

### Token Calculation (after fees)
```
yes_tokens = (half_usdc / yes_ask) × (1 − fee_per_share(yes_ask))
no_tokens  = (half_usdc / no_ask)  × (1 − fee_per_share(no_ask))
```

### USDC Deduction
```
portfolio.usdcBalance −= trade_usdc
```

### Deduplication
One trade per (coin, type, slug) — no stacking while a position is open in the same slug. When a new slug starts (market rotates), old positions remain in history but the slug lock releases.

### Worked Example
```
yes_ask = 0.47,  no_ask = 0.48,  gap_ask = 0.05
trade_usdc = 100 USDC  →  half = 50 USDC each

fee_yes = 0.25 × (0.47 × 0.53)² = 0.25 × (0.2491)² = 0.015519
fee_no  = 0.25 × (0.48 × 0.52)² = 0.25 × (0.2496)² = 0.015600

yes_tokens = (50 / 0.47) × (1 − 0.015519) = 106.38 × 0.9845 = 104.73
no_tokens  = (50 / 0.48) × (1 − 0.015600) = 104.17 × 0.9844 = 102.54
```

---

## 7. Hedging Definition

```
hedged_ratio = min(yes_tokens, no_tokens) / max(yes_tokens, no_tokens)
```

| hedged_ratio | Label |
|--------------|-------|
| >= 0.95      | "Fully Hedged" |
| < 0.95       | "{X}% hedged" |

In practice with a 50/50 USDC split, the ratio is very close to 1.0 unless prices differ significantly.

Continuing example:
```
hedged_ratio = min(104.73, 102.54) / max(104.73, 102.54)
             = 102.54 / 104.73 = 0.9791  →  "97.9% hedged" (≥ 0.95, so "Fully Hedged")
```

---

## 8. Average Cost — VWAP

From `analyze_whale_patterns.py` (`compute_round_stats`, lines 133–135):

```
yes_avg_cost     = total_yes_usdc_spent / total_yes_tokens_received
no_avg_cost      = total_no_usdc_spent  / total_no_tokens_received
combined_avg_cost = yes_avg_cost + no_avg_cost
```

VWAP accumulates across all fills in the same position (same slug). If the mock trader fires multiple times in the same slug (currently blocked by dedup), the VWAP would weight each fill proportionally.

Continuing example (single fill):
```
yes_avg_cost      = 50 / 104.73 = 0.4774
no_avg_cost       = 50 / 102.54 = 0.4876
combined_avg_cost = 0.4774 + 0.4876 = 0.9650
```

---

## 9. P&L Model

```
hedged_tokens  = min(yes_tokens, no_tokens)     // guaranteed to pay out $1 per token
hedged_profit  = hedged_tokens × (1.0 − combined_avg_cost)   // certain P&L

unhedged_tokens = |yes_tokens − no_tokens|       // directional exposure
excess_side     = whichever side has more tokens

// If excess side wins (settles at $1.00):
win_gain        = unhedged_tokens × (1.0 − avg_excess_side_cost)
pnl_best_case   = hedged_profit + win_gain

// If excess side loses (settles at $0.00):
lose_loss       = unhedged_tokens × avg_excess_side_cost
pnl_worst_case  = hedged_profit − lose_loss
```

Continuing example:
```
hedged_tokens  = 102.54
hedged_profit  = 102.54 × (1.0 − 0.9650) = 102.54 × 0.035 = $3.59

unhedged       = 104.73 − 102.54 = 2.19 YES tokens (excess side = YES)
win_gain       = 2.19 × (1.0 − 0.4774) = 2.19 × 0.5226 = $1.14
lose_loss      = 2.19 × 0.4774 = $1.05

pnl_best_case  = $3.59 + $1.14 = $4.73
pnl_worst_case = $3.59 − $1.05 = $2.54    // still positive! gap was large enough
```

---

## 10. Market Rotation

Every 5 minutes (300s) or 15 minutes (900s), the active market slug changes. The mock trader must:

1. Every 1s: recalculate `expectedSlug = getCurrentSlug(coin, type)`.
2. If slug differs from cached: call Gamma API for new token IDs, clear stale prices, reconnect WebSocket with new subscription.
3. Old position for the previous slug remains in portfolio history.

---

## 11. Key Thresholds Summary

| Parameter | Value | Source |
|-----------|-------|--------|
| `MIN_PROFITABLE_GAP` | 0.05 | `gap_monitor.py` line 22 |
| Fee rate | 0.25 | `analyze_whale_patterns.py` line 31 |
| Fee exponent | 2 | `analyze_whale_patterns.py` line 32 |
| Sanity filter | yes_ask > 0.90 AND no_ask > 0.90 | `gap_monitor.py` line 333 |
| WS URL | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | `gap_monitor.py` line 34 |
| Gamma API | `https://gamma-api.polymarket.com/markets?slug={slug}` | `gap_monitor.py` line 93 |
| Hedging threshold | 95% | Strategy section 7 |
| Default trade size | 10% of USDC balance | Mock trader default |

---

## 12. Maker Bid Signal Strategy

> **STATUS — NOT YET EXECUTED**
> The maker bid UI and logic are fully implemented in `mock_trader.html`, but no maker bids have been placed in live or paper trading. The signal thresholds below are based on plan assumptions and have not been validated against historical data. **Review `analyze_signal.py` output and historical `gap_log.csv` before using this in production.**

### Overview

The taker strategy (Section 6) requires `gap_ask >= 0.05` — buy YES + NO at the ask price when the combined ask is already below $1.00. This is rare in normal markets. Whales use **maker (limit bid) orders** instead, placing bids at `yes_bid` + `no_bid` prices where `gap_bid = 1.0 - yes_bid - no_bid` is routinely +0.05 to +0.08.

The maker bid strategy uses **external signals** to predict *when* to place bids before the gap opens.

### Comparison

| | Taker (existing) | Maker (new) |
|---|---|---|
| Trigger | `gap_ask >= 0.05` (reactive) | Signal fires → pre-place bid |
| Fill price | `yes_ask` + `no_ask` | `yes_bid` + `no_bid` |
| Min gap needed | 0.05 | 0.03 |
| When | After gap opens | Before/as gap opens |

### Signal A — Binance Momentum

```
Source: wss://stream.binance.com:9443/ws/btcusdt@aggTrade/ethusdt@aggTrade
Trigger: abs(price_now - price_30s_ago) / price_30s_ago >= 0.003  (0.3% in 30s)
```

When Binance price moves quickly, Polymarket market makers widen spreads or temporarily withdraw — pushing `yes_bid + no_bid` lower and opening a positive `gap_bid`.

### Signal B — Chainlink Oracle Lag

```
Source: Ethereum mainnet RPC (https://cloudflare-eth.com)
BTC/USD:  0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88
ETH/USD:  0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419
SOL/USD:  0x4ffC43a60e009B551865A93d232E33Fce9f01507
Method:   latestRoundData() → selector 0xfeaf968c
Response: [roundId, answer (8 dec), startedAt, updatedAt, answeredInRound]

Trigger:  chainlink_lag > 30s  AND  abs(binance_price - cl_price) / cl_price >= 0.003
```

If the oracle hasn't confirmed the Binance price move, the probability hasn't settled and market makers hedge by quoting wider — amplifying the `gap_bid`.

### Maker Bid Execution

```
trade_usdc    = portfolio.usdcBalance × (tradeSizePct / 100)
half_usdc     = trade_usdc / 2

If gap_bid < 0.03: skip
If position already exists for this slug: skip (one trade per slug)

yes_tokens = (half_usdc / yes_bid) × (1 − fee_per_share(yes_bid))
no_tokens  = (half_usdc / no_bid)  × (1 − fee_per_share(no_bid))

Simulated as immediately filled (mock — real execution requires liquidity to cross the spread)
P&L calculation uses bid-based avg cost (same hedging formula as taker)
```

### Thresholds

| Parameter | Value | Notes |
|-----------|-------|-------|
| `MIN_MAKER_GAP` | 0.03 | vs 0.05 for taker — maker pays no taker fee |
| `MOMENTUM_THRESH` | 0.003 | 0.3% Binance 30s move |
| `CL_LAG_THRESH` | 30s | Chainlink oracle lag for "stale" |
| `CL_DIVERGE_THRESH` | 0.003 | 0.3% Binance vs Chainlink divergence |

### Signal Panel UI (mock_trader.html)

```
[SIGNAL PANEL — full-width bar, amber background when any signal active]
  BTC | Binance: $XXXXX | 30s move: +0.31% [SIGNAL] | CL: $XXXXX | Lag: 42s [STALE]
  ETH | Binance: $XXXXX | 30s move: +0.10%           | CL: $XXXXX | Lag: 8s
  SOL | Binance: $XXXXX | 30s move: +0.10%           | CL: $XXXXX | Lag: 8s

[MARKET PANELS]
  GAP (BID) ★ — primary column, green if >= 0.03
  GAP (ASK)   — secondary / informational, green if >= 0.05
  [BID!] badge fires when gap_bid >= 0.03 + signal active → maker bid trade

[TRADE LOG]
  Includes "Type" column: TAKER (amber) | MAKER (blue)

[PORTFOLIO POSITIONS]
  Shows [TAKER] or [MAKER] tag on each position block
```

### Chainlink RPC Call (no library needed)

```javascript
// latestRoundData() function selector = 0xfeaf968c
const body = {
  jsonrpc: '2.0', id: 1, method: 'eth_call',
  params: [{ to: CONTRACT_ADDRESS, data: '0xfeaf968c' }, 'latest']
};
// Response hex: 5 × 32-byte slots → [roundId, answer, startedAt, updatedAt, answeredInRound]
const hex       = result.slice(2);
const answer    = BigInt('0x' + hex.slice(64, 128));   // slot 1, 8 decimals
const updatedAt = BigInt('0x' + hex.slice(192, 256));  // slot 3, unix timestamp
const price     = Number(answer) / 1e8;
const lag       = Math.floor(Date.now() / 1000) - Number(updatedAt);
```
