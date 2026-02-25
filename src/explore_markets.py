# explore_market.py — Explore a single Polymarket market in detail

import requests
import json

def explore_market(slug):
    """
    Pull detailed info about one specific market using its slug.
    The slug is the URL-friendly name (e.g., 'us-strikes-iran-by-march-1-2026-492')
    """
    
    url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
    
    print(f"Fetching market: {slug}\n")
    
    response = requests.get(url)
    
    if response.status_code != 200:
        print(f"Error: {response.status_code}")
        return
    
    markets = response.json()
    
    if not markets:
        print("No market found with that slug.")
        return
    
    market = markets[0]
    
    # Print ALL available fields so we can see what data exists
    print("=== ALL AVAILABLE FIELDS ===\n")
    for key, value in market.items():
        # Truncate long values so the output is readable
        display = str(value)
        if len(display) > 200:
            display = display[:200] + "..."
        print(f"  {key}: {display}")
    
    return market


if __name__ == "__main__":
    # Use the highest-volume market from our data
    slug = "us-strikes-iran-by-march-1-2026-492"
    market = explore_market(slug)