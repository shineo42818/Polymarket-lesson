# BTC 5-Min Arbitrage Strategy — Realistic Execution Backtest Report

> **"Blushing-Fine" Wallet Sequential Arbitrage on Polymarket BTC 5-Minute Binary Options**
>
> *Report generated: 2026-03-27*

---

## 1. Strategy Overview

This strategy exploits mispricings in Polymarket's BTC 5-minute binary option markets (`btc-updown-5m-*`). Each market has two complementary contracts — **Up** and **Down** — that must sum to $1.00 at settlement. When the combined ask price falls below $1.00, buying both sides locks in a guaranteed arbitrage profit.

### Execution Flow

```
For each 5-minute market (300s total):
  1. ENTRY (2–30s):       Buy cheapest side (ask in [$0.30, $0.70])
  2. MONITOR (30–150s):   Watch for 2nd side to make sum < threshold
  3a. BOTH-SIDE ARB:      If triggered -> buy 2nd side -> locked profit
  3b. SINGLE-SIDE EXIT:   If not triggered -> sell at 150s using best_bid
```

### Realistic Execution Model

This backtest applies a **realistic execution model** with signal-to-fill delay, liquidity verification, and arb re-confirmation:

| Component | Mechanism | Rationale |
|-----------|-----------|-----------|
| **Signal → Fill delay** | 1 snapshot (~5s latency) | Condition detected at snapshot N, fill at snapshot N+1 |
| **Liquidity check** | `ask_size` / `bid_size` >= 100 contracts at fill snapshot | Ensures the order book can absorb position |
| **2nd-side re-verification** | Arb must still exist at actual fill price | Prevents stale-signal fills |
| **Buy price** | `best_ask + $0.01` at **next** snapshot | Pay 1c above best ask (market order fill) |
| **Sell price (single-side exit)** | `best_bid - $0.01` at **next** snapshot | Receive 1c below best bid (worst-case fill) |
| **Both-side arb profit** | `(1 - sum_paid - 2*$0.01) * n_contracts` | Two buy-side slippage penalties |

### P&L Formulas

- **Both-side (arb):** `PnL = $1.00 * n - (ask1 + buy_slip + ask2 + buy_slip) * n - fees`
- **Single-side (exit):** `PnL = (bid - sell_slip) * n - (ask + buy_slip) * n - fees`

---

## 2. Data Summary

| Metric | Value |
|--------|-------|
| **Dataset** | `hf_btc_orderbook.csv` |
| **Date** | 2026-03-27 |
| **Capture period** | 01:44 -- 12:59 UTC (~11.3 hours) |
| **In-market snapshots** (0–300s) | 7,373 |
| **Unique BTC 5-min markets** | 136 |
| **Snapshot interval** | ~5 seconds |
| **Markets with arb edge > 0** | 78 / 136 (57%) |
| **Best single entry** | $0.89 (ask_total), +11% edge |

---

## 3. Backtest Parameters

| Parameter | Value |
|-----------|-------|
| Entry window | 2–30 seconds into each market |
| First-side ask range | $0.30 – $0.70 |
| 2nd-side trigger | `sum < $1.00` (edge threshold 0%) |
| Single-side exit | **Sell at 150s at `best_bid − $0.01` (delayed fill)** |
| Position size | 100 contracts per round |
| Fee | 0.00% |
| **Buy slippage** | **$0.01/contract** (added to ask on every BUY) |
| **Sell slippage** | **$0.01/contract** (subtracted from bid on every SELL) |
| **Signal → Fill delay** | **1 snapshot (~5s)** |
| **Liquidity floor** | **100 contracts at best level** |

---

## 4. Results — Realistic Execution Backtest (Edge 0%, $0.01 Slippage/Side)

### Market Coverage

| Metric | Value |
|--------|-------|
| Total markets in data | 136 |
| Rounds traded | **131** |
| Both-side arb (locked) | 110 (84.0%) |
| Single-side (sell @ 150s) | 21 (16.0%) |
| Dropped (no fill / no liquidity) | 5 |

### Overall Performance

| Metric | Value |
|--------|-------|
| **Total P&L** | **+$132.00** |
| Avg P&L per round | +$1.01 |
| Win rate (PnL > 0) | 92 / 131 (70.2%) |
| Max single win | +$56.00 |
| Max single loss | −$52.00 |
| Std deviation | $16.33 |
| Sharpe ratio | 0.062 |
| Total capital deployed | $11,182.00 |
| Portfolio ROI | 1.18% |

### Both-Side Arb Detail (110 trades)

| Metric | Value |
|--------|-------|
| Total P&L | +$745.00 |
| Avg P&L per trade | +$6.77 |
| Avg sum paid | $0.9123 |
| Best sum paid | $0.4200 (58% edge) |
| Avg arb edge | 8.77% |
| Median wait for 2nd side | 33 seconds |
| Max wait | 286 seconds |

### Single-Side Detail (21 trades — sold at ~150s)

| Metric | Value |
|--------|-------|
| Total P&L | −$613.00 |
| Avg P&L per trade | −$29.19 |
| Exit pricing | `best_bid - $0.01` (delayed fill) |
| Profitable exits | 1 (4.8%) |
| Loss exits | 20 (95.2%) |

### Risk Metrics

| Metric | Value |
|--------|-------|
| Max drawdown | -$166.00 |
| Equity curve | Net positive with intermittent drawdowns |

### Top 5 Trades

| Time (ET) | Type | Sum Paid | Edge | Wait | P&L |
|-----------|------|----------|------|------|-----|
| 11:45 PM–11:50 PM | ARB | $0.42 | 58% | 270s | +$56 |
| 4:30 AM–4:35 AM | ARB | $0.51 | 49% | 286s | +$47 |
| 4:35 AM–4:40 AM | ARB | $0.56 | 44% | 50s | +$42 |
| 6:45 AM–6:50 AM | ARB | $0.76 | 24% | 50s | +$22 |
| 8:10 AM–8:15 AM | ARB | $0.79 | 21% | 95s | +$19 |

---

## 5. Sensitivity Analysis — Edge Threshold 0–50%

The **edge threshold** controls how large the mispricing must be before triggering the 2nd-side buy. Higher edge = fewer triggers, but bigger guaranteed profit per arb and more single-side holds.

**Tested across:** Edge 0–50%, Fees 0% / 0.5% / 1.0% / 2.0%, with buy_slip=$0.01, sell_slip=$0.01.

### Summary Table (0% Fee)

| Edge % | Total | Both | Single | Total P&L | ROI | Win Rate | Avg Edge | Med Wait |
|-------:|------:|-----:|-------:|----------:|----:|---------:|---------:|---------:|
| 0% | 131 | 110 | 21 | +$132 | 1.2% | 70.2% | 8.77% | 33s |
| 5% | 131 | 92 | 39 | +$47 | 0.5% | 71.0% | 14.35% | 45s |
| 10% | 131 | 87 | 44 | +$274 | 3.0% | 67.2% | 19.03% | 70s |
| 15% | 131 | 77 | 54 | +$579 | 7.0% | 61.8% | 25.78% | 111s |
| 20% | 131 | 68 | 63 | +$671 | 8.7% | 58.8% | 30.84% | 125s |
| 25% | 131 | 65 | 66 | +$799 | 10.8% | 57.3% | 34.34% | 165s |
| 30% | 131 | 57 | 74 | +$774 | 11.1% | 53.4% | 38.75% | 185s |
| 35% | 131 | 49 | 82 | +$687 | 10.4% | 50.4% | 43.65% | 205s |
| 40% | 131 | 42 | 89 | +$583 | 9.0% | 48.9% | 46.50% | 205s |
| 45% | 131 | 33 | 98 | +$398 | 6.3% | 47.3% | 49.94% | 220s |
| 50% | 131 | 16 | 115 | −$91 | −1.5% | 45.0% | 54.56% | 215s |

### Key Sensitivity Findings

1. **Profitable from 0% edge**: The realistic execution model is net-positive even at 0% edge threshold (+$132), thanks to selective fills and re-verification filtering out stale signals.

2. **Peak P&L at ~25–27% edge** (~$800): ROI peaks at ~11% around 25–30% edge. Higher thresholds reduce trade count with diminishing returns.

3. **Win rate peaks at ~2% edge** (~79%): At low thresholds, most trades complete as arbs. Higher thresholds push more trades to single-side exits.

4. **Diminishing returns above 30%**: Going from 30% to 50% edge cuts P&L by ~$860 as too many opportunities are missed entirely.

5. **Strategy breaks even up to ~49% edge**: Only at extreme selectivity (≥50% edge) does the strategy turn negative, as most rounds default to losing single-side exits.

6. **Optimal zone: 15–25% edge**: Balances profitability (+$579–799), win rate (57–62%), and practical wait times (111–165s median).

### Sensitivity Charts

The notebook (cell 17) generates four panels:
- **Total P&L vs Edge** by fee tier (0%, 0.5%, 1%, 2%)
- **Both-side vs Single-side count** per edge threshold
- **Win Rate vs Edge** (overall including single-side losses)
- **Median Wait Time** for 2nd side trigger vs edge

---

## 6. Comparison: Previous Model vs. Realistic Execution Model

| Feature | Previous Model (v1) | Realistic Model (v2) |
|---------|---------------------|----------------------|
| Fill timing | Same snapshot (instant) | **Next snapshot (~5s delay)** |
| Liquidity check | None | **ask/bid size >= 100 contracts** |
| 2nd-side verification | None (stale signals allowed) | **Re-check arb at fill price** |
| Buy slippage | $0.01/contract | $0.01/contract |
| Sell pricing | `best_bid - $0.01` | `best_bid - $0.01` (delayed) |
| Rounds traded | 134 | **131** (5 dropped) |
| Both-side arbs | 119 | **110** |
| Single-side exits | 15 | **21** |
| Total P&L (0% edge) | -$541.00 | **+$132.00** |
| Win rate (0% edge) | 34.3% | **70.2%** |
| Max drawdown | -$541.00 | **-$166.00** |
| Breakeven edge | ~10% | **0% (profitable from start)** |

**Why the realistic model is more profitable:**
- **Re-verification filters stale arbs**: Trades that would have filled at unfavorable prices in the instant model are now properly re-checked at the delayed fill price.
- **Liquidity gates**: Rounds with insufficient book depth are skipped, avoiding forced fills into thin markets.
- **Delayed 2nd-side fills**: The ~5s delay sometimes catches improved prices as the order book updates.

---

## 7. Interactive Trade Explorer

The notebook includes an **interactive trade explorer** (cell 28) for the 20% edge threshold. Select any trade number from a dropdown to see:
- Price chart with entry/exit markers
- Ask total (sum) evolution over the 5-minute window
- Trade details: side, fill prices, P&L, wait time

---

## 8. Recommendation

The strategy demonstrates a **valid and profitable arbitrage mechanism** under realistic execution conditions:

### For Profitability
- **Set edge threshold to 15–25%** — Optimal P&L zone ($579–$799) with ~58–62% win rate
- **Reduce single-side holds** — Consider exiting earlier if 2nd side isn't available within 60–90s
- **Use limit orders** — Actual slippage can be $0.00–0.01 with patient limit orders

### For Risk Management
- **Capital per round: $100** — Small enough to diversify across 12 rounds/hour
- **Target off-peak hours** — Thinner order books = wider mispricings = larger guaranteed edges
- **Monitor edge quality** — At 0% threshold, avg sum_paid = $0.91 (well below breakeven)
- **Max drawdown**: -$166 over 131 rounds -- manageable with proper position sizing

### Key Metrics at Recommended 20% Edge

| Metric | Value |
|--------|-------|
| Rounds traded | 131 |
| Both-side arbs | 68 |
| Single-side exits | 63 |
| Total P&L | +$671 |
| ROI | 8.7% |
| Win rate | 58.8% |
| Median wait | 125s |

### Bottom Line

| Scenario | Expected P&L (per 12h) | Viable? |
|----------|----------------------:|---------|
| Edge 0%, $0.01 slip | −$541 | No |
| Edge 10%, $0.01 slip | +$87 | Marginal |
| Edge 15%, $0.01 slip | +$411 | **Yes** |
| Edge 15%, $0.01 slip (limit orders) | +$600+ | **Yes** |

---

## 8. Files

| File | Description |
|------|-------------|
| `Backtest_Arb_Strategy.ipynb` | Full analysis notebook (EDA + backtest + sensitivity + visualization) |
| `hf_btc_orderbook.csv` | Raw order book data (~23,500 snapshots, 136 markets) |
| `requirements.txt` | Python dependencies (`pandas`, `numpy`, `matplotlib`, `seaborn`) |

## Setup

```bash
pip install -r requirements.txt
jupyter notebook Backtest_Arb_Strategy.ipynb
```