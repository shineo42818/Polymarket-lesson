# fullrun.py — Complete analysis pipeline in one command
# Runs signal analysis + profitability calculator for chosen coins and time period

import pandas as pd
import os
import sys

# Add parent directories so we can import our existing code
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from analyze_signal import load_and_align, calculate_binance_momentum, match_polymarket_to_binance, analyze_prediction_accuracy, analyze_by_momentum_strength
from profit_calculator import simulate_strategy, print_results


def run_analysis(coin, market_type="5m", days_filter=None):
    """
    Run full signal analysis for one coin.
    Returns the matched DataFrame with predictions.
    """
    suffix = "_15m" if market_type == "15m" else ""
    minutes = 15 if market_type == "15m" else 5
    
    print(f"\n{'=' * 60}")
    print(f"  {coin.upper()} — {market_type} markets")
    print(f"{'=' * 60}")
    
    # Load data
    try:
        binance, poly = load_and_align(coin, suffix)
    except FileNotFoundError as e:
        print(f"  Data file not found: {e}")
        print(f"  Run collect_binance.py and collect_polymarket.py first.")
        return None
    
    # Filter by days if requested
    if days_filter is not None:
        poly["timestamp"] = pd.to_datetime(poly["timestamp"], utc=True)
        cutoff = poly["timestamp"].max() - pd.Timedelta(days=days_filter)
        poly = poly[poly["timestamp"] >= cutoff].reset_index(drop=True)
        print(f"  Filtered to last {days_filter} day(s): {len(poly)} markets")
    
    # Calculate momentum
    binance = calculate_binance_momentum(binance, minutes_before=minutes)
    
    # Match
    matched = match_polymarket_to_binance(binance, poly)
    
    if len(matched) == 0:
        print("  No overlapping data found.")
        return None
    
    # Analyze
    df = analyze_prediction_accuracy(matched)
    df = analyze_by_momentum_strength(df)
    
    # Save individual results
    df.to_csv(f"data/signal_analysis_{coin}{suffix}.csv", index=False)
    
    return df


def run_profitability(csv_path, label="", bet_size=10.0):
    """Run profitability analysis on a signal CSV."""
    
    print(f"\n{'=' * 60}")
    print(f"PROFITABILITY — {label}")
    print(f"{'=' * 60}")
    
    # Medium momentum (our best signal)
    results_med = simulate_strategy(
        signal_csv=csv_path,
        bet_size=bet_size,
        min_momentum=0.15,
        max_momentum=0.5
    )
    if results_med is not None:
        print_results(results_med, f"MEDIUM 0.15-0.5% ({label})")
    
    # Best signal (>0.15%)
    results_best = simulate_strategy(
        signal_csv=csv_path,
        bet_size=bet_size,
        min_momentum=0.15
    )
    if results_best is not None:
        print_results(results_best, f"BEST >0.15% ({label})")
    
    return results_best


if __name__ == "__main__":
    
    # ============================================================
    # MENU 1: Choose coins
    # ============================================================
    print("\n" + "=" * 60)
    print("POLYMARKET SIGNAL ANALYSIS — FULL RUN")
    print("=" * 60)
    
    print("\nWhich coins to analyze?\n")
    print("  [1] BTC only")
    print("  [2] ETH only")
    print("  [3] SOL only")
    print("  [4] BTC + ETH")
    print("  [5] BTC + SOL")
    print("  [6] ETH + SOL")
    print("  [7] All three (BTC + ETH + SOL)")
    
    coin_choice = input("\nEnter number (1-7): ").strip()
    
    coin_map = {
        "1": ["btc"],
        "2": ["eth"],
        "3": ["sol"],
        "4": ["btc", "eth"],
        "5": ["btc", "sol"],
        "6": ["eth", "sol"],
        "7": ["btc", "eth", "sol"],
    }
    
    coins = coin_map.get(coin_choice, ["btc"])
    
    # ============================================================
    # MENU 2: Choose time period
    # ============================================================
    print(f"\nTime period?\n")
    print("  [1] Last 1 day")
    print("  [2] Last 3 days")
    print("  [3] Last 7 days (all data)")
    
    time_choice = input("\nEnter number (1-3): ").strip()
    
    time_map = {
        "1": 1,
        "2": 3,
        "3": None,  # None means use all data
    }
    
    days_filter = time_map.get(time_choice, None)
    days_label = f"{days_filter}d" if days_filter else "7d"
    
    # ============================================================
    # MENU 3: Choose market type
    # ============================================================
    print(f"\nMarket type?\n")
    print("  [1] 5-minute markets")
    print("  [2] 15-minute markets")
    print("  [3] Both (compare)")
    
    market_choice = input("\nEnter number (1-3): ").strip()
    
    market_map = {
        "1": ["5m"],
        "2": ["15m"],
        "3": ["5m", "15m"],
    }
    
    market_types = market_map.get(market_choice, ["5m"])
    
    # ============================================================
    # RUN ANALYSIS
    # ============================================================
    
    all_results = []  # Collect for combined summary
    
    for mtype in market_types:
        for coin in coins:
            df = run_analysis(coin, market_type=mtype, days_filter=days_filter)
            
            if df is not None:
                suffix = "_15m" if mtype == "15m" else ""
                csv_path = f"data/signal_analysis_{coin}{suffix}.csv"
                label = f"{coin.upper()} {mtype} ({days_label})"
                
                run_profitability(csv_path, label=label)
                
                # Save for combined summary
                df["coin"] = coin.upper()
                df["market_type"] = mtype
                all_results.append(df)
    
    # ============================================================
    # COMBINED SUMMARY
    # ============================================================
    if len(all_results) > 1:
        combined = pd.concat(all_results, ignore_index=True)
        combined = combined.dropna(subset=["binance_direction"])
        combined = combined[combined["binance_direction"] != "FLAT"]
        
        print(f"\n{'=' * 60}")
        print("COMBINED SUMMARY — ALL COINS")
        print("=" * 60)
        
        # Overall accuracy per coin
        for coin in coins:
            for mtype in market_types:
                subset = combined[(combined["coin"] == coin.upper()) & (combined["market_type"] == mtype)]
                if len(subset) == 0:
                    continue
                acc = subset["correct_prediction"].mean() * 100
                
                # Medium momentum only
                subset["abs_momentum"] = subset["momentum_pct"].abs()
                med = subset[(subset["abs_momentum"] >= 0.15) & (subset["abs_momentum"] <= 0.5)]
                med_acc = med["correct_prediction"].mean() * 100 if len(med) > 0 else 0
                
                print(f"\n  {coin.upper()} ({mtype}):")
                print(f"    Overall:  {acc:.1f}% ({len(subset)} trades)")
                print(f"    Medium:   {med_acc:.1f}% ({len(med)} trades)")
        
        # Save combined
        combined.to_csv("data/signal_analysis_combined.csv", index=False)
        print(f"\n  Combined data saved to data/signal_analysis_combined.csv")
    
    print(f"\n{'=' * 60}")
    print("DONE!")
    print("=" * 60)