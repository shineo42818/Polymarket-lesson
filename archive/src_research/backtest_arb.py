"""
backtest_arb.py -- Backtest the BB (limit-order) arbitrage strategy
==================================================================

Strategy: Place limit buy orders on BOTH YES and NO tokens at bid prices.
When both fill, total cost = yes_bid + no_bid + fees.
One token always settles at $1.00, so profit = gap_bid - fees.

Key question: With $100, how much can this strategy make?
"""

import pandas as pd
import numpy as np
import os

# ============================================================
# FEE MODEL (from Polymarket docs)
# ============================================================

def fee_per_share(price):
    """Polymarket fee: 0.25 * (p * (1-p))^2  -- max 1.5625% at p=0.50"""
    return 0.25 * (price * (1 - price)) ** 2


# ============================================================
# LOAD & DEDUPLICATE TO EPISODES
# ============================================================

def load_episodes():
    """Load gap_log.csv, deduplicate to one row per episode (best gap_bid)."""
    files = ["data/gap_log.csv", "data/gap_log_old.csv"]
    frames = []

    for f in files:
        if os.path.exists(f) and os.path.getsize(f) > 0:
            # Detect if file has header
            with open(f, "r") as fh:
                first = fh.readline()
            if "recorded_at" in first:
                df = pd.read_csv(f, on_bad_lines="skip")
            else:
                cols = [
                    "recorded_at", "coin", "market_type", "slug",
                    "market_closes", "seconds_left", "yes_price", "no_price",
                    "gap", "gap_bid", "gap_duration_ms", "arb_size_usd",
                    "opportunity",
                ]
                df = pd.read_csv(f, on_bad_lines="skip", header=None, names=cols)
            frames.append(df)

    if not frames:
        print("ERROR: No gap log data found.")
        return None

    df = pd.concat(frames, ignore_index=True)
    for col in ["gap_bid", "arb_size_usd", "gap_duration_ms", "yes_price",
                 "no_price", "seconds_left"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["gap_bid", "yes_price", "no_price"])
    df["recorded_at"] = pd.to_datetime(df["recorded_at"], errors="coerce")
    df = df.sort_values("recorded_at").reset_index(drop=True)

    # Build episodes: group consecutive rows with same slug & rising gap_duration
    df["_prev_dur"] = df.groupby(["coin", "market_type"])["gap_duration_ms"].shift(1)
    df["_ep_start"] = (
        (df["gap_duration_ms"] == 0) |
        (df["gap_duration_ms"] < df["_prev_dur"])
    ).fillna(True)
    df["episode_id"] = df.groupby(["coin", "market_type"])["_ep_start"].cumsum()

    # Take the snapshot with the BEST gap_bid per episode (best entry point)
    episodes = (
        df.loc[df.groupby(["coin", "market_type", "episode_id"])["gap_bid"].idxmax()]
        .reset_index(drop=True)
    )

    print(f"Loaded {len(df):,} snapshots -> {len(episodes):,} unique episodes")
    return episodes


# ============================================================
# BACKTEST ENGINE
# ============================================================

def backtest(episodes, starting_capital=100.0, trade_pct=0.10,
             min_gap_bid=0.05, min_seconds_left=30,
             max_fill_prob=0.50, fill_model="optimistic"):
    """
    Simulate the BB strategy across historical episodes.

    Parameters:
    -----------
    starting_capital : float
        Starting USDC balance (default $100)
    trade_pct : float
        Fraction of balance to risk per trade (default 10%)
    min_gap_bid : float
        Minimum gap_bid to enter (default 0.05)
    min_seconds_left : int
        Don't trade if market closes in < N seconds (need time for fills)
    max_fill_prob : float
        Probability both limit orders fill (conservative estimate)
    fill_model : str
        "optimistic" = assume all qualifying trades fill
        "realistic" = random fill based on max_fill_prob
        "duration_based" = longer episodes more likely to fill
    """
    eps = episodes[
        (episodes["gap_bid"] >= min_gap_bid) &
        (episodes["seconds_left"] >= min_seconds_left)
    ].copy()

    if eps.empty:
        print("No qualifying episodes.")
        return None

    np.random.seed(42)
    balance = starting_capital
    trades = []

    for _, row in eps.iterrows():
        yes_bid = row["yes_price"]  # Approximate: yes_price ≈ yes_ask ≈ yes_bid + spread
        no_bid = row["no_price"]
        gap_bid = row["gap_bid"]

        # The actual bid prices are: yes_bid ≈ yes_ask - spread, no_bid ≈ no_ask - spread
        # gap_bid = 1.0 - yes_bid - no_bid (already computed by gap_monitor)
        # So total cost per pair = yes_bid + no_bid = 1.0 - gap_bid
        cost_per_pair = 1.0 - gap_bid

        # Split cost: we buy yes_bid shares worth and no_bid shares worth
        yes_price_est = cost_per_pair / 2  # Rough split
        no_price_est = cost_per_pair / 2

        # More accurate: use the actual prices to estimate bid
        # yes_ask = yes_price, spread ~ 0.01-0.02
        # yes_bid ≈ yes_ask - typical_spread
        # But gap_bid is already computed correctly, so just use it directly
        yes_bid_est = row["yes_price"] - 0.01  # conservative spread assumption
        no_bid_est = row["no_price"] - 0.01
        # Ensure consistency
        actual_cost = yes_bid_est + no_bid_est

        # Fees on each leg
        fee_yes = fee_per_share(yes_bid_est)
        fee_no = fee_per_share(no_bid_est)
        total_fee_per_pair = fee_yes + fee_no

        # Profit per pair of shares
        gross_profit = gap_bid
        net_profit_per_pair = gross_profit - total_fee_per_pair

        if net_profit_per_pair <= 0:
            continue  # Not profitable after fees

        # Position sizing
        trade_amount = balance * trade_pct
        if trade_amount < 1.0:
            continue  # Too small to trade

        # How many pairs can we buy?
        cost_with_fees = actual_cost + total_fee_per_pair
        num_pairs = trade_amount / cost_with_fees

        # Fill probability
        if fill_model == "optimistic":
            filled = True
        elif fill_model == "realistic":
            filled = np.random.random() < max_fill_prob
        elif fill_model == "duration_based":
            # Longer episodes = higher fill probability
            dur_ms = row.get("gap_duration_ms", 100)
            fill_p = min(0.8, 0.1 + dur_ms / 2000)  # 10% base + ramp
            filled = np.random.random() < fill_p
        else:
            filled = True

        if not filled:
            trades.append({
                "time": row["recorded_at"],
                "coin": row["coin"],
                "market": row["market_type"],
                "gap_bid": gap_bid,
                "net_profit_per_pair": net_profit_per_pair,
                "trade_amount": trade_amount,
                "pnl": 0.0,
                "filled": False,
                "balance": balance,
                "seconds_left": row["seconds_left"],
            })
            continue

        # Trade executes
        pnl = num_pairs * net_profit_per_pair
        balance += pnl

        trades.append({
            "time": row["recorded_at"],
            "coin": row["coin"],
            "market": row["market_type"],
            "gap_bid": gap_bid,
            "net_profit_per_pair": net_profit_per_pair,
            "trade_amount": trade_amount,
            "pnl": pnl,
            "filled": True,
            "balance": balance,
            "seconds_left": row["seconds_left"],
        })

    return pd.DataFrame(trades)


# ============================================================
# REPORT
# ============================================================

def print_report(trades, starting_capital, label=""):
    if trades is None or trades.empty:
        print(f"\n  No trades to report. {label}")
        return

    filled = trades[trades["filled"]]
    n_total = len(trades)
    n_filled = len(filled)

    total_pnl = filled["pnl"].sum()
    final_balance = starting_capital + total_pnl
    roi = total_pnl / starting_capital * 100

    avg_pnl = filled["pnl"].mean() if n_filled else 0
    max_pnl = filled["pnl"].max() if n_filled else 0
    min_pnl = filled["pnl"].min() if n_filled else 0

    # Cumulative P&L for drawdown
    if n_filled > 0:
        cum = filled["pnl"].cumsum()
        peak = cum.cummax()
        drawdown = (cum - peak).min()
    else:
        drawdown = 0

    # Time span
    t_min = trades["time"].min()
    t_max = trades["time"].max()

    print(f"\n{'=' * 60}")
    print(f"  BACKTEST RESULTS {label}")
    print(f"{'=' * 60}")
    print(f"""
  Period:              {str(t_min)[:19]} to {str(t_max)[:19]}
  Starting capital:    ${starting_capital:,.2f}

  Total episodes:      {n_total}
  Filled trades:       {n_filled}  ({n_filled/n_total*100:.0f}% fill rate)

  -----------------------------
  Total P&L:           ${total_pnl:,.4f}
  Final balance:       ${final_balance:,.4f}
  ROI:                 {roi:,.2f}%
  -----------------------------

  Avg profit/trade:    ${avg_pnl:,.4f}
  Best trade:          ${max_pnl:,.4f}
  Worst trade:         ${min_pnl:,.4f}
  Max drawdown:        ${drawdown:,.4f}

  Avg gap_bid:         {filled['gap_bid'].mean():.4f}
  Avg net profit/pair: ${filled['net_profit_per_pair'].mean():.4f}
""")

    # Per-coin breakdown
    if n_filled > 0:
        print(f"  {'Market':<12} {'Trades':>7} {'Total P&L':>11} {'Avg P&L':>10}")
        print(f"  {'-' * 44}")
        for (coin, mkt), grp in filled.groupby(["coin", "market"]):
            label_m = f"{coin.upper()} {mkt}"
            print(f"  {label_m:<12} {len(grp):>7} ${grp['pnl'].sum():>10.4f} ${grp['pnl'].mean():>9.4f}")

    # Fee impact analysis
    print(f"\n  Fee Analysis:")
    print(f"  Avg gap_bid:                {filled['gap_bid'].mean():.4f}")
    print(f"  Avg fees (both sides):      {filled['gap_bid'].mean() - filled['net_profit_per_pair'].mean():.4f}")
    print(f"  Avg net after fees:         {filled['net_profit_per_pair'].mean():.4f}")
    pct_eaten = (1 - filled['net_profit_per_pair'].mean() / filled['gap_bid'].mean()) * 100
    print(f"  % of gap eaten by fees:     {pct_eaten:.1f}%")


# ============================================================
# ANNUALIZED PROJECTIONS
# ============================================================

def project_annual(trades, starting_capital):
    """Extrapolate from observed data to daily/monthly/annual estimates."""
    if trades is None or trades.empty:
        return

    filled = trades[trades["filled"]]
    if filled.empty:
        return

    t_span = (trades["time"].max() - trades["time"].min()).total_seconds()
    if t_span <= 0:
        return

    hours_observed = t_span / 3600
    trades_per_hour = len(filled) / hours_observed
    pnl_per_hour = filled["pnl"].sum() / hours_observed

    # Markets run 24/7
    daily_trades = trades_per_hour * 24
    daily_pnl = pnl_per_hour * 24
    monthly_pnl = daily_pnl * 30
    annual_pnl = daily_pnl * 365

    print(f"\n{'=' * 60}")
    print(f"  PROJECTIONS (extrapolated from {hours_observed:.1f}h of data)")
    print(f"{'=' * 60}")
    print(f"""
  Trades/hour:     {trades_per_hour:.1f}
  Trades/day:      {daily_trades:.0f}
  P&L/hour:        ${pnl_per_hour:,.4f}
  P&L/day:         ${daily_pnl:,.2f}
  P&L/month:       ${monthly_pnl:,.2f}
  P&L/year:        ${annual_pnl:,.2f}

  WARNING: These projections assume:
    1. Gaps continue at the same frequency & size (may not hold)
    2. Your limit orders actually get filled (big assumption)
    3. No competition from other bots (unlikely -- you'll be competing)
    4. No downtime, network issues, or API failures
    5. Compounding not modeled (balance grows -> bigger trades)

  Real-world expectation: divide by 3-5x for conservative estimate.
""")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    STARTING_CAPITAL = 100.0

    episodes = load_episodes()
    if episodes is None:
        exit(1)

    print(f"\nQualifying episodes (gap_bid >= 0.05): {len(episodes[episodes['gap_bid'] >= 0.05])}")

    # ── Scenario 1: Optimistic (all trades fill) ──
    trades_opt = backtest(
        episodes,
        starting_capital=STARTING_CAPITAL,
        trade_pct=0.10,
        min_gap_bid=0.05,
        min_seconds_left=30,
        fill_model="optimistic",
    )
    print_report(trades_opt, STARTING_CAPITAL, "-- OPTIMISTIC (100% fill)")

    # ── Scenario 2: Realistic (50% random fill) ──
    trades_real = backtest(
        episodes,
        starting_capital=STARTING_CAPITAL,
        trade_pct=0.10,
        min_gap_bid=0.05,
        min_seconds_left=30,
        fill_model="realistic",
        max_fill_prob=0.50,
    )
    print_report(trades_real, STARTING_CAPITAL, "-- REALISTIC (50% fill)")

    # ── Scenario 3: Only big gaps (gap_bid >= 0.10) ──
    trades_big = backtest(
        episodes,
        starting_capital=STARTING_CAPITAL,
        trade_pct=0.20,  # Bigger size for bigger gaps
        min_gap_bid=0.10,
        min_seconds_left=30,
        fill_model="optimistic",
    )
    print_report(trades_big, STARTING_CAPITAL, "-- BIG GAPS ONLY (>=0.10)")

    # ── Scenario 4: Duration-based fill model ──
    trades_dur = backtest(
        episodes,
        starting_capital=STARTING_CAPITAL,
        trade_pct=0.10,
        min_gap_bid=0.05,
        min_seconds_left=30,
        fill_model="duration_based",
    )
    print_report(trades_dur, STARTING_CAPITAL, "-- DURATION-BASED FILL")

    # Projections based on optimistic scenario
    project_annual(trades_opt, STARTING_CAPITAL)
