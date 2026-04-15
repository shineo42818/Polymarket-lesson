# get_markets.py — Pull active markets from Polymarket API
# This is your first real data script!

import requests
import pandas as pd
import os

def get_polymarket_markets():
    """
    Calls the Polymarket API to get active markets.
    Returns a pandas DataFrame with market details.
    """
    
    # Polymarket's public API endpoint
    url = "https://gamma-api.polymarket.com/markets"
    
    # Parameters: we want active markets, sorted by volume
    params = {
        "active": "true",
        "closed": "false",
        "limit": 100,        # get up to 100 markets
        "order": "volume",   # sort by trading volume
        "ascending": "false"  # highest volume first
    }
    
    print("Fetching markets from Polymarket API...")
    
    # Make the API call
    response = requests.get(url, params=params)
    
    # Check if the request was successful
    if response.status_code != 200:
        print(f"Error: API returned status code {response.status_code}")
        return None
    
    # Parse the JSON response into a Python list
    markets = response.json()
    
    print(f"Received {len(markets)} markets!")
    
    # Extract the fields we care about into a clean list
    clean_data = []
    for market in markets:
        clean_data.append({
            "question": market.get("question", "N/A"),
            "volume": float(market.get("volume", 0)),
            "liquidity": float(market.get("liquidity", 0)),
            "slug": market.get("slug", "N/A"),
            "end_date": market.get("endDate", "N/A"),
            "active": market.get("active", False)
        })
    
    # Convert to a pandas DataFrame (think of it as a spreadsheet in Python)
    df = pd.DataFrame(clean_data)
    
    # Sort by volume, highest first
    df = df.sort_values("volume", ascending=False).reset_index(drop=True)
    
    return df


# This runs when you execute the script
if __name__ == "__main__":
    
    # Get the data
    df = get_polymarket_markets()
    
    if df is not None:
        # Show the top 15 markets
        print("\n--- TOP 15 POLYMARKET MARKETS BY VOLUME ---\n")
        print(df.head(15).to_string(index=False))
        
        # Save to CSV
        df.to_csv("data/markets.csv", index=False)
        print(f"\nSaved {len(df)} markets to data/markets.csv")