# analyze_signal.py — Does Binance price direction predict Polymarket resolution?
# This is our core research question.

import pandas as pd
import matplotlib.pyplot as plt

def load_and_align(coin="btc", suffix=""):
    """
    Load Binance and Polymarket data, align timezones, and merge.
    """
    
    # Load Binance data
    binance = pd.read_csv(f"data/binance_{coin}.csv")
    binance["open_time"] = pd.to_datetime(binance["open_time"])
    
    # Load Polymarket data
    poly = pd.read_csv(f"data/polymarket_{coin}{suffix}.csv")
    poly["timestamp"] = pd.to_datetime(poly["timestamp"], utc=True)
    
    # Make Binance timezone-aware (UTC)
    binance["open_time"] = binance["open_time"].dt.tz_localize("UTC")
    
    print(f"Loaded {coin.upper()} data:")
    print(f"  Binance:     {len(binance)} rows ({binance.open_time.iloc[0]} to {binance.open_time.iloc[-1]})")
    print(f"  Polymarket:  {len(poly)} rows ({poly.timestamp.iloc[0]} to {poly.timestamp.iloc[-1]})")
    
    return binance, poly


def calculate_binance_momentum(binance, minutes_before=5):
    """
    For each minute in Binance data, calculate the price change
    over the previous N minutes.
    
    Example: if minutes_before=5, for each row we calculate
    (current_close - close_5_minutes_ago) / close_5_minutes_ago
    """
    
    binance = binance.copy()
    binance = binance.sort_values("open_time").reset_index(drop=True)
    
    # Shift gives us the price N rows ago (each row = 1 minute)
    binance["prev_close"] = binance["close"].shift(minutes_before)
    
    # Percentage change
    binance["momentum"] = (binance["close"] - binance["prev_close"]) / binance["prev_close"] * 100
    
    # Direction: positive momentum = trending up
    binance["binance_direction"] = binance["momentum"].apply(
        lambda x: "UP" if x > 0 else "DOWN" if x < 0 else "FLAT"
    )
    
    return binance


def match_polymarket_to_binance(binance, poly):
    """
    For each Polymarket 5-min market, find the Binance momentum
    at the START of that market's window.
    
    Example: Polymarket market starts at 20:00
    → We look at Binance momentum at 20:00 (which reflects the prior 5 min)
    """
    
    results = []
    
    for _, market in poly.iterrows():
        market_start = market["timestamp"]
        yes_price = market["yes_price"]
        
        # Skip markets that haven't resolved yet (price between 0 and 1)
        if yes_price not in [0.0, 1.0]:
            continue
        
        # Polymarket resolution: 1.0 = UP won, 0.0 = DOWN won
        poly_outcome = "UP" if yes_price == 1.0 else "DOWN"
        
        # Find the closest Binance row to this market's start time
        time_diffs = abs(binance["open_time"] - market_start)
        closest_idx = time_diffs.idxmin()
        closest_row = binance.iloc[closest_idx]
        
        # Only use if the time match is within 2 minutes
        time_gap = time_diffs.iloc[closest_idx]
        if time_gap > pd.Timedelta(minutes=2):
            continue
        
        results.append({
            "market_time": market_start,
            "slug": market["slug"],
            "poly_outcome": poly_outcome,
            "binance_momentum": closest_row.get("momentum", None),
            "binance_direction": closest_row.get("binance_direction", None),
            "binance_close": closest_row["close"],
            "momentum_pct": closest_row.get("momentum", None)
        })
    
    df = pd.DataFrame(results)
    return df


def analyze_prediction_accuracy(matched):
    """
    Core analysis: when Binance is trending UP before a market,
    does Polymarket also resolve UP? And vice versa.
    """
    
    # Remove rows where we couldn't calculate momentum
    df = matched.dropna(subset=["binance_direction"]).copy()
    df = df[df["binance_direction"] != "FLAT"]
    
    # Did Binance direction match Polymarket outcome?
    df["correct_prediction"] = df["binance_direction"] == df["poly_outcome"]
    
    total = len(df)
    correct = df["correct_prediction"].sum()
    accuracy = correct / total * 100 if total > 0 else 0
    
    print(f"\n{'=' * 60}")
    print(f"SIGNAL ANALYSIS RESULTS")
    print(f"{'=' * 60}")
    print(f"\n  Total matched markets:  {total}")
    print(f"  Correct predictions:    {correct}")
    print(f"  Accuracy:               {accuracy:.1f}%")
    print(f"  (50% = random, >55% = potentially useful signal)")
    
    # Break down by direction
    up_markets = df[df["binance_direction"] == "UP"]
    down_markets = df[df["binance_direction"] == "DOWN"]
    
    up_correct = up_markets["correct_prediction"].sum()
    down_correct = down_markets["correct_prediction"].sum()
    
    print(f"\n  When Binance trending UP:")
    print(f"    Markets: {len(up_markets)}")
    print(f"    Polymarket also UP: {up_correct} ({up_correct/len(up_markets)*100:.1f}%)" if len(up_markets) > 0 else "    No data")
    
    print(f"\n  When Binance trending DOWN:")
    print(f"    Markets: {len(down_markets)}")
    print(f"    Polymarket also DOWN: {down_correct} ({down_correct/len(down_markets)*100:.1f}%)" if len(down_markets) > 0 else "    No data")
    
    return df


def analyze_by_momentum_strength(df):
    """
    Does a STRONGER Binance move predict Polymarket more accurately?
    Group by momentum size and check accuracy for each group.
    """
    
    df = df.copy()
    df["abs_momentum"] = df["momentum_pct"].abs()
    
    # Create bins: small (0-0.05%), medium (0.05-0.15%), large (>0.15%)
    bins = [0, 0.05, 0.15, 0.50, float("inf")]
    labels = ["Tiny (<0.05%)", "Small (0.05-0.15%)", "Medium (0.15-0.5%)", "Large (>0.5%)"]
    df["momentum_size"] = pd.cut(df["abs_momentum"], bins=bins, labels=labels)
    
    print(f"\n{'=' * 60}")
    print(f"ACCURACY BY MOMENTUM STRENGTH")
    print(f"{'=' * 60}")
    print(f"\n  (Does a bigger Binance move = better prediction?)\n")
    
    for label in labels:
        group = df[df["momentum_size"] == label]
        if len(group) == 0:
            continue
        acc = group["correct_prediction"].sum() / len(group) * 100
        print(f"  {label}:")
        print(f"    Markets: {len(group)},  Accuracy: {acc:.1f}%")
    
    return df


def plot_results(df, coin="btc"):
    """
    Create a visual showing accuracy vs momentum strength.
    """
    
    df = df.copy()
    df["abs_momentum"] = df["momentum_pct"].abs()
    
    bins = [0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, float("inf")]
    labels = ["0-0.05", "0.05-0.10", "0.10-0.15", "0.15-0.20", "0.20-0.30", "0.30-0.50", "0.50+"]
    df["bin"] = pd.cut(df["abs_momentum"], bins=bins, labels=labels)
    
    summary = df.groupby("bin").agg(
        count=("correct_prediction", "count"),
        accuracy=("correct_prediction", "mean")
    ).reset_index()
    
    summary["accuracy_pct"] = summary["accuracy"] * 100
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))
    
    # Top chart: accuracy by momentum bin
    bars = ax1.bar(summary["bin"].astype(str), summary["accuracy_pct"], color="#6366f1")
    ax1.axhline(y=50, color="red", linestyle="--", label="Random (50%)")
    ax1.set_ylabel("Prediction Accuracy (%)")
    ax1.set_title(f"{coin.upper()}: Does Binance Momentum Predict Polymarket Outcome?", fontsize=14, fontweight="bold")
    ax1.legend()
    ax1.set_ylim(0, 100)
    
    # Add count labels on bars
    for bar, count in zip(bars, summary["count"]):
        ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                f"n={count}", ha="center", fontsize=9)
    
    # Bottom chart: number of markets per bin
    ax2.bar(summary["bin"].astype(str), summary["count"], color="#a5b4fc")
    ax2.set_ylabel("Number of Markets")
    ax2.set_xlabel("Binance Momentum (absolute % change in prior 5 min)")
    ax2.set_title("Sample Size per Momentum Bin")
    
    plt.tight_layout()
    plt.savefig(f"charts/signal_analysis_{coin}{suffix}.png", dpi=150)
    print(f"\nChart saved to charts/signal_analysis_{coin}.png")
    plt.show()


if __name__ == "__main__":
    
    coin = "btc"
    market_type = "15m"  # "5m" or "15m"
    
    print(f"\n{'=' * 60}")
    print(f"POLYMARKET vs BINANCE SIGNAL ANALYSIS — {coin.upper()}")
    print(f"{'=' * 60}\n")
    
    # Step 1: Load data
    suffix = "_15m" if market_type == "15m" else ""
    binance, poly = load_and_align(coin, suffix)

    
    # Step 2: Calculate Binance momentum
    minutes = 15 if market_type == "15m" else 5
    print(f"\nCalculating Binance momentum ({minutes}-min lookback)...")
    binance = calculate_binance_momentum(binance, minutes_before=minutes)

    
    # Step 3: Match Polymarket markets to Binance data
    print("Matching Polymarket markets to Binance timestamps...")
    matched = match_polymarket_to_binance(binance, poly)
    
    print(f"\nSuccessfully matched {len(matched)} markets")
    
    if len(matched) == 0:
        print("No overlapping data found. Check timezone alignment.")
        exit()
    
    # Step 4: Analyze prediction accuracy
    df = analyze_prediction_accuracy(matched)
    
    # Step 5: Break down by momentum strength
    df = analyze_by_momentum_strength(df)
    
    # Step 6: Visualize
    plot_results(df, coin)
    
    # Save results
    df.to_csv(f"data/signal_analysis_{coin}{suffix}.csv", index=False)
    print(f"\nFull results saved to data/signal_analysis_{coin}.csv")
    
    # Summary
    print(f"\n{'=' * 60}")
    print("WHAT TO LOOK FOR IN THE RESULTS:")
    print("=" * 60)
    print("""
  If accuracy > 55% for larger momentum bins:
    → Binance IS a leading indicator for Polymarket
    → Bigger moves = stronger signal
    → This could be a tradeable edge

  If accuracy ≈ 50% across all bins:
    → No predictive signal from Binance momentum
    → The market is efficient (prices already reflect the info)
    → Need a different approach

  If accuracy < 50%:
    → Contrarian signal (Binance direction is wrong!)
    → Unusual, but worth investigating
""")