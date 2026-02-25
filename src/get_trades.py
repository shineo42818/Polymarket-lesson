# get_trades.py — Find top traders and pull their activity
# Uses the Polymarket Data API (public, no auth needed)

import requests
import pandas as pd

def get_market_positions(condition_id, limit=100):
    """
    Get the top position holders for a specific market.
    These are the wallets with the biggest bets — our potential whales.
    """
    
    url = "https://data-api.polymarket.com/positions"
    
    params = {
        "market": condition_id,
        "limit": limit,
        "sortBy": "CASH",       # Sort by dollar value
        "sortDirection": "DESC"  # Biggest first
    }
    
    print(f"Fetching top positions for market...\n")
    
    response = requests.get(url, params=params)
    
    if response.status_code != 200:
        print(f"Error: {response.status_code}")
        print(f"Response: {response.text[:500]}")
        return None
    
    data = response.json()
    
    if not data:
        print("No positions found.")
        return None
    
    print(f"Found {len(data)} positions!\n")
    
    # Extract the key fields
    clean_data = []
    for pos in data:
        clean_data.append({
            "wallet": pos.get("proxyWallet", "N/A"),
            "name": pos.get("name", "Anonymous"),
            "size": float(pos.get("size", 0)),
            "initial_value_usd": float(pos.get("initialValue", 0)),
            "current_value_usd": float(pos.get("currentValue", 0)),
            "pnl_usd": float(pos.get("cashPnl", 0)),
            "pnl_percent": float(pos.get("percentPnl", 0)),
            "outcome": pos.get("outcome", "N/A"),
            "title": pos.get("title", "N/A")
        })
    
    df = pd.DataFrame(clean_data)
    df = df.sort_values("initial_value_usd", ascending=False).reset_index(drop=True)
    
    return df


def get_wallet_activity(wallet_address, limit=50):
    """
    Get recent trading activity for a specific wallet.
    This is how we track what a whale is doing across ALL markets.
    """
    
    url = "https://data-api.polymarket.com/activity"
    
    params = {
        "user": wallet_address,
        "limit": limit,
        "type": "TRADE"
    }
    
    print(f"Fetching activity for wallet: {wallet_address[:10]}...\n")
    
    response = requests.get(url, params=params)
    
    if response.status_code != 200:
        print(f"Error: {response.status_code}")
        print(f"Response: {response.text[:500]}")
        return None
    
    data = response.json()
    
    if not data:
        print("No activity found for this wallet.")
        return None
    
    print(f"Found {len(data)} trades!\n")
    
    clean_data = []
    for trade in data:
        clean_data.append({
            "timestamp": trade.get("timestamp", "N/A"),
            "market": trade.get("title", "N/A"),
            "side": trade.get("side", "N/A"),
            "size": float(trade.get("size", 0)),
            "usdc_size": float(trade.get("usdcSize", 0)),
            "price": float(trade.get("price", 0)),
            "outcome": trade.get("outcome", "N/A"),
            "tx_hash": trade.get("transactionHash", "N/A")
        })
    
    df = pd.DataFrame(clean_data)
    return df


if __name__ == "__main__":
    # Step 1: Find the biggest position holders on the Iran market
    condition_id = "0x15aa3c1259a716915e068a0d63c3885d2301d29e8982cbb1717ecb9b63d02d95"
    
    print("=" * 60)
    print("STEP 1: FINDING WHALE WALLETS")
    print("=" * 60)
    
    positions = get_market_positions(condition_id)
    
    if positions is not None and len(positions) > 0:
        print("\n--- TOP 10 POSITION HOLDERS (POTENTIAL WHALES) ---\n")
        print(positions.head(10).to_string(index=False))
        
        # Save positions
        positions.to_csv("data/positions.csv", index=False)
        print(f"\nSaved {len(positions)} positions to data/positions.csv")
        
        # Step 2: Pick the top whale and track their activity
        top_whale = positions.iloc[0]["wallet"]
        top_whale_name = positions.iloc[0]["name"]
        
        print(f"\n{'=' * 60}")
        print(f"STEP 2: TRACKING TOP WHALE — {top_whale_name}")
        print(f"Wallet: {top_whale}")
        print(f"{'=' * 60}")
        
        activity = get_wallet_activity(top_whale)
        
        if activity is not None:
            print("\n--- RECENT TRADES BY THIS WHALE ---\n")
            print(activity.head(10).to_string(index=False))
            
            activity.to_csv("data/whale_activity.csv", index=False)
            print(f"\nSaved {len(activity)} trades to data/whale_activity.csv")