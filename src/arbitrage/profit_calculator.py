# profit_calculator.py — Calculate if the Binance→Polymarket signal is profitable
# Uses real Polymarket fee structure for 5-min crypto markets

import pandas as pd

def polymarket_taker_fee(price):
    """
    Calculate Polymarket taker fee for crypto up/down markets.
    Fee is highest at 50% probability, lowest near 0% or 100%.
    Formula from Polymarket docs: fee = price × (1 - price) × FEE_RATE
    """
    FEE_RATE = 0.0625  # 6.25% base rate
    fee = price * (1 - price) * FEE_RATE
    return fee


def simulate_strategy(signal_csv="data/signal_analysis_btc.csv", 
                      bet_size=10.0,
                      min_momentum=0.0,
                      max_momentum=999.0):
    """
    Simulate trading based on the Binance momentum signal.
    
    Strategy: 
    - When Binance momentum is UP → buy YES on Polymarket
    - When Binance momentum is DOWN → buy NO on Polymarket
    - Only trade when momentum is within our target range
    
    Parameters:
    - bet_size: how much USDC to bet per trade
    - min_momentum: minimum absolute momentum % to trigger a trade
    - max_momentum: maximum absolute momentum % to trigger a trade
    """
    
    df = pd.read_csv(signal_csv)
    df = df.dropna(subset=["binance_direction", "momentum_pct"])
    df = df[df["binance_direction"] != "FLAT"]
    
    # Filter by momentum range
    df["abs_momentum"] = df["momentum_pct"].abs()
    df = df[(df["abs_momentum"] >= min_momentum) & (df["abs_momentum"] <= max_momentum)]
    
    if len(df) == 0:
        print("No trades match this momentum range.")
        return None
    
    # Simulate each trade
    results = []
    
    for _, trade in df.iterrows():
        # We buy at roughly 50% odds (these are up/down markets)
        # In reality the price varies, but 0.50 is a good approximation
        buy_price = 0.50
        
        # Calculate fee
        fee_per_share = polymarket_taker_fee(buy_price)
        
        # How many shares can we buy with our bet size?
        cost_per_share = buy_price + fee_per_share
        shares = bet_size / cost_per_share
        
        # Did we win?
        won = trade["correct_prediction"]
        
        if won:
            # Win: each share pays $1.00
            payout = shares * 1.00
            profit = payout - bet_size
        else:
            # Lose: shares worth $0.00
            payout = 0
            profit = -bet_size
        
        results.append({
            "market_time": trade.get("market_time", "N/A"),
            "direction": trade["binance_direction"],
            "momentum": trade["momentum_pct"],
            "won": won,
            "bet": bet_size,
            "fee": fee_per_share * shares,
            "payout": payout,
            "profit": profit
        })
    
    return pd.DataFrame(results)


def print_results(results, label=""):
    """Print a clean summary of simulation results."""
    
    total_trades = len(results)
    wins = results["won"].sum()
    losses = total_trades - wins
    win_rate = wins / total_trades * 100
    
    total_bet = results["bet"].sum()
    total_fees = results["fee"].sum()
    total_payout = results["payout"].sum()
    total_profit = results["profit"].sum()
    
    # Calculate per-trade stats
    avg_profit = total_profit / total_trades
    
    # Max drawdown (worst losing streak)
    results["cumulative"] = results["profit"].cumsum()
    peak = results["cumulative"].cummax()
    drawdown = results["cumulative"] - peak
    max_drawdown = drawdown.min()
    
    print(f"\n{'=' * 60}")
    print(f"PROFITABILITY REPORT {label}")
    print(f"{'=' * 60}")
    print(f"""
  Trades:          {total_trades}
  Wins:            {wins} ({win_rate:.1f}%)
  Losses:          {losses} ({100 - win_rate:.1f}%)
  
  Total wagered:   ${total_bet:,.2f}
  Total fees paid: ${total_fees:,.2f}
  Total payouts:   ${total_payout:,.2f}
  
  ─────────────────────────────
  NET PROFIT:      ${total_profit:,.2f}
  ROI:             {total_profit / total_bet * 100:.2f}%
  ─────────────────────────────
  
  Avg profit/trade: ${avg_profit:,.2f}
  Max drawdown:     ${max_drawdown:,.2f}
  
  Fees as % of bets: {total_fees / total_bet * 100:.2f}%
""")


if __name__ == "__main__":
    
    BET_SIZE = 10.0  # $10 per trade
    
    print("\n" + "=" * 60)
    print("POLYMARKET 5-MIN CRYPTO SIGNAL — PROFITABILITY ANALYSIS")
    print("=" * 60)
    
    # ---- TEST 1: Trade ALL signals (any momentum) ----
    results_all = simulate_strategy(
        bet_size=BET_SIZE,
        min_momentum=0.0
    )
    if results_all is not None:
        print_results(results_all, "— ALL MOMENTUM LEVELS")
    
    # ---- TEST 2: Trade only TINY momentum (<0.05%) ----
    results_tiny = simulate_strategy(
        bet_size=BET_SIZE,
        min_momentum=0.0,
        max_momentum=0.05
    )
    if results_tiny is not None:
        print_results(results_tiny, "— TINY (<0.05%)")
    
    # ---- TEST 3: Trade only MEDIUM momentum (0.15-0.5%) ----
    results_med = simulate_strategy(
        bet_size=BET_SIZE,
        min_momentum=0.15,
        max_momentum=0.5
    )
    if results_med is not None:
        print_results(results_med, "— MEDIUM (0.15-0.5%)")
    
    # ---- TEST 4: Trade only LARGE momentum (>0.5%) ----
    results_large = simulate_strategy(
        bet_size=BET_SIZE,
        min_momentum=0.5
    )
    if results_large is not None:
        print_results(results_large, "— LARGE (>0.5%)")
    
    # ---- TEST 5: Trade MEDIUM + LARGE combined (>0.15%) ----
    results_best = simulate_strategy(
        bet_size=BET_SIZE,
        min_momentum=0.15
    )
    if results_best is not None:
        print_results(results_best, "— BEST SIGNAL (>0.15%)")
    
    print("\n" + "=" * 60)
    print("INTERPRETATION GUIDE")
    print("=" * 60)
    print("""
  Positive NET PROFIT = strategy makes money after fees
  Negative NET PROFIT = fees eat the edge, not profitable
  
  ROI > 0%  = worth pursuing
  ROI > 5%  = strong edge
  ROI > 10% = very strong edge
  
  Max drawdown = worst peak-to-trough loss
  (How much you'd need to survive the worst streak)
""")