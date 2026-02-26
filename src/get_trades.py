# get_trades.py — Interactive whale tracker for Polymarket
# Run this script and it will guide you through choosing what to analyze

import requests
import pandas as pd
from datetime import datetime

def get_leaderboard(category="OVERALL", time_period="WEEK", order_by="VOL", limit=25):
    """Get top traders from the leaderboard, filtered by category."""
    
    url = "https://data-api.polymarket.com/v1/leaderboard"
    
    params = {
        "category": category,
        "timePeriod": time_period,
        "orderBy": order_by,
        "limit": limit
    }
    
    response = requests.get(url, params=params)
    
    if response.status_code != 200:
        print(f"Error: {response.status_code} — {response.text[:300]}")
        return None
    
    data = response.json()
    if not data:
        return None
    
    clean_data = []
    for trader in data:
        clean_data.append({
            "rank": trader.get("rank", "N/A"),
            "name": trader.get("userName", "Anonymous"),
            "wallet": trader.get("proxyWallet", "N/A"),
            "volume": float(trader.get("vol", 0)),
            "pnl": float(trader.get("pnl", 0)),
            "verified": trader.get("verifiedBadge", False)
        })
    
    return pd.DataFrame(clean_data)


def get_markets_by_category(category_tag, limit=20):
    """Get active markets filtered by category tag."""
    
    url = "https://gamma-api.polymarket.com/markets"
    
    params = {
        "active": "true",
        "closed": "false",
        "tag": category_tag,
        "limit": limit,
        "order": "volume",
        "ascending": "false"
    }
    
    response = requests.get(url, params=params)
    
    if response.status_code != 200:
        print(f"Error: {response.status_code}")
        return None
    
    markets = response.json()
    if not markets:
        return None
    
    clean_data = []
    for m in markets:
        clean_data.append({
            "question": m.get("question", "N/A"),
            "volume": float(m.get("volume", 0)),
            "liquidity": float(m.get("liquidity", 0)),
            "condition_id": m.get("conditionId", "N/A"),
            "slug": m.get("slug", "N/A")
        })
    
    df = pd.DataFrame(clean_data)
    df = df.sort_values("volume", ascending=False).reset_index(drop=True)
    return df


def get_market_positions(condition_id, limit=20):
    """Get top position holders for a specific market."""
    
    url = "https://data-api.polymarket.com/v1/market-positions"
    
    params = {
        "conditionId": condition_id,
        "limit": limit
    }
    
    response = requests.get(url, params=params)
    
    if response.status_code != 200:
        print(f"Error: {response.status_code} — {response.text[:300]}")
        return None
    
    data = response.json()
    if not data:
        return None
    
    all_positions = []
    for token_group in data:
        for pos in token_group.get("positions", []):
            all_positions.append({
                "wallet": pos.get("proxyWallet", "N/A"),
                "name": pos.get("name", "Anonymous"),
                "outcome": pos.get("outcome", "N/A"),
                "size": float(pos.get("size", 0)),
                "avg_price": float(pos.get("avgPrice", 0)),
                "current_value": float(pos.get("currentValue", 0)),
                "pnl": float(pos.get("cashPnl", 0)),
                "total_bought": float(pos.get("totalBought", 0)),
            })
    
    df = pd.DataFrame(all_positions)
    if len(df) > 0:
        df = df.sort_values("total_bought", ascending=False).reset_index(drop=True)
    return df


def get_wallet_activity(wallet_address, limit=50):
    """Get recent trades for a specific wallet."""
    
    url = "https://data-api.polymarket.com/activity"
    
    params = {
        "user": wallet_address,
        "limit": limit,
        "type": "TRADE"
    }
    
    response = requests.get(url, params=params)
    
    if response.status_code != 200:
        print(f"Error: {response.status_code} — {response.text[:300]}")
        return None
    
    data = response.json()
    if not data:
        return None
    
    clean_data = []
    for trade in data:
        timestamp = trade.get("timestamp", 0)
        try:
            date_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
        except:
            date_str = "N/A"
        
        clean_data.append({
            "date": date_str,
            "market": trade.get("title", "N/A"),
            "side": trade.get("side", "N/A"),
            "outcome": trade.get("outcome", "N/A"),
            "size": float(trade.get("size", 0)),
            "usdc_spent": float(trade.get("usdcSize", 0)),
            "price": float(trade.get("price", 0)),
        })
    
    return pd.DataFrame(clean_data)


# ============================================================
# INTERACTIVE MENU
# ============================================================

if __name__ == "__main__":
    
    # --- STEP 1: Choose a category ---
    categories = {
        "1": ("OVERALL", "All categories"),
        "2": ("POLITICS", "Politics & elections"),
        "3": ("CRYPTO", "Cryptocurrency"),
        "4": ("FINANCE", "Finance & economics"),
        "5": ("SPORTS", "Sports"),
        "6": ("TECH", "Technology"),
        "7": ("CULTURE", "Culture & entertainment"),
        "8": ("WEATHER", "Weather"),
        "9": ("ECONOMICS", "Economics"),
    }
    
    print("\n" + "=" * 60)
    print("POLYMARKET WHALE TRACKER")
    print("=" * 60)
    print("\nChoose a category to analyze:\n")
    
    for key, (code, desc) in categories.items():
        print(f"  [{key}] {desc}")
    
    choice = input("\nEnter number (1-9): ").strip()
    
    if choice not in categories:
        print("Invalid choice. Using OVERALL.")
        choice = "1"
    
    category_code, category_name = categories[choice]
    
    # --- STEP 2: Choose time period ---
    print(f"\nTime period for {category_name}:\n")
    print("  [1] Today")
    print("  [2] This week")
    print("  [3] This month")
    print("  [4] All time")
    
    time_choice = input("\nEnter number (1-4): ").strip()
    time_map = {"1": "DAY", "2": "WEEK", "3": "MONTH", "4": "ALL"}
    time_period = time_map.get(time_choice, "WEEK")
    
    sort_order = {
        "1":("VOL", "By volume"),
        "2":("PNL", "By profit/loss")
    }
    for key, (code, desc) in sort_order.items():
        print(f"  [{key}] {desc}")
    print("\nChoose sorting order:")

    sort_by=input("\nEnter sorting (1-2):").strip()
    sort_code,sort_logic = sort_order[sort_by]
    # --- STEP 3: Show leaderboard ---
    print(f"\n{'=' * 60}")
    print(f"TOP TRADERS — {category_name.upper()} ({time_period})")
    print("=" * 60 + "\n")
    
    leaderboard = get_leaderboard(
        category=category_code,
        time_period=time_period,
        order_by=sort_code,
        limit=15
    )
    
    if leaderboard is None or len(leaderboard) == 0:
        print("No traders found for this category/period.")
        exit()
    
    print(leaderboard.to_string(index=False))
    leaderboard.to_csv("data/leaderboard.csv", index=False)
    print(f"\nSaved to data/leaderboard.csv")
    
    # --- STEP 4: Pick a whale to track ---
    print(f"\n{'=' * 60}")
    print("PICK A WHALE TO TRACK")
    print("=" * 60)
    print(f"\nEnter the rank number of the trader to investigate (1-{len(leaderboard)}):")
    
    whale_choice = input("\nTrader rank: ").strip()
    
    try:
        whale_idx = int(whale_choice) - 1
        if whale_idx < 0 or whale_idx >= len(leaderboard):
            print("Invalid rank. Using #1.")
            whale_idx = 0
    except:
        print("Invalid input. Using #1.")
        whale_idx = 0
    
    whale_wallet = leaderboard.iloc[whale_idx]["wallet"]
    whale_name = leaderboard.iloc[whale_idx]["name"]
    whale_vol = leaderboard.iloc[whale_idx]["volume"]
    whale_pnl = leaderboard.iloc[whale_idx]["pnl"]
    
    print(f"\n{'=' * 60}")
    print(f"WHALE PROFILE: {whale_name}")
    print(f"  Wallet:  {whale_wallet}")
    print(f"  Volume:  ${whale_vol:,.2f}")
    print(f"  PnL:     ${whale_pnl:,.2f}")
    print("=" * 60)
    
    # --- STEP 5: Show whale's recent activity ---
    print(f"\nFetching {whale_name}'s recent trades...\n")
    
    activity = get_wallet_activity(whale_wallet)
    
    if activity is not None and len(activity) > 0:
        print("--- RECENT TRADES ---\n")
        print(activity.head(20).to_string(index=False))
        activity.to_csv("data/whale_activity.csv", index=False)
        print(f"\nSaved {len(activity)} trades to data/whale_activity.csv")
        
        # Quick stats
        print(f"\n{'=' * 60}")
        print("QUICK STATS")
        print("=" * 60)
        buys = activity[activity["side"] == "BUY"]
        sells = activity[activity["side"] == "SELL"]
        print(f"  Total trades:    {len(activity)}")
        print(f"  Buy trades:      {len(buys)}")
        print(f"  Sell trades:     {len(sells)}")
        print(f"  Total USDC spent: ${activity['usdc_spent'].sum():,.2f}")
        print(f"  Avg trade size:   ${activity['usdc_spent'].mean():,.2f}")
        
        # What markets is this whale most active in?
        print(f"\n{'=' * 60}")
        print(f"MARKETS {whale_name.upper()} IS MOST ACTIVE IN")
        print("=" * 60 + "\n")
        market_counts = activity.groupby("market").agg(
            trades=("market", "count"),
            total_usdc=("usdc_spent", "sum")
        ).sort_values("total_usdc", ascending=False).head(10)
        print(market_counts.to_string())
    else:
        print("No recent activity found for this whale.")
    
    print(f"\n{'=' * 60}")
    print("DONE! Check the data/ folder for saved CSV files.")
    print("=" * 60)