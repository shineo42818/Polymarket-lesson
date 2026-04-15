import requests
import pandas as pd
import json
import os
from datetime import datetime, timezone, timedelta

COINS = ["btc", "eth", "sol"]
DAYS_BACK = 7
BATCH_SIZE = 20  # API caps at 20 markets per call

# ============================================================
# MARKET TYPE: change this to switch between 5-min and 15-min
# ============================================================
MARKET_TYPE = "15m"  # "5m" or "15m"

# Settings per market type
MARKET_CONFIG = {
    "5m": {
        "interval_seconds": 300,    # 5 minutes
        "slug_pattern": "{coin}-updown-5m-{ts}",
        "file_suffix": "",          # data/polymarket_btc.csv (original)
    },
    "15m": {
        "interval_seconds": 900,    # 15 minutes
        "slug_pattern": "{coin}-updown-15m-{ts}",
        "file_suffix": "_15m",      # data/polymarket_btc_15m.csv
    }
}

# Test mode: set to True to run quickly with 1 coin, 1 day
TEST_MODE = True
TEST_COINS = ["btc"]
TEST_DAYS = 1


def generate_timestamps(days_back=7):
    """Generate all boundary timestamps for the research window."""
    interval = MARKET_CONFIG[MARKET_TYPE]["interval_seconds"]
    
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)
    
    # Round start down to nearest boundary
    ts = int(start.timestamp())
    ts = ts - (ts % interval)
    
    timestamps = []
    end_ts = int(now.timestamp())
    
    while ts <= end_ts:
        timestamps.append(ts)
        ts += interval
    
    return timestamps


def fetch_batch(coin, timestamps):
    """
    Fetch multiple markets in one API call using batch slugs.
    Returns a list of results.
    """
    url = "https://gamma-api.polymarket.com/markets"
    config = MARKET_CONFIG[MARKET_TYPE]
    
    # Build slugs using the pattern for current market type
    slugs = [(config["slug_pattern"].format(coin=coin, ts=ts), ts) for ts in timestamps]
    
    # Pass multiple slug params in one request
    params = [("slug", slug) for slug, ts in slugs]
    
    response = requests.get(url, params=params, timeout=10)
    data = response.json()
    
    results = []
    for market in data:
        slug = market.get("slug", "")
        prices_str = market.get("outcomePrices", "[]")
        price_list = json.loads(prices_str)
        
        if price_list:
            # Extract timestamp from slug (always the last number)
            ts = int(slug.split("-")[-1])
            token_ids = json.loads(market.get("clobTokenIds", "[]"))
            
            results.append({
                "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc),
                "slug": slug,
                "yes_price": float(price_list[0]),
                "no_price": float(price_list[1]) if len(price_list) > 1 else None,
                "clob_token_id_yes": token_ids[0] if len(token_ids) > 0 else None,
                "clob_token_id_no": token_ids[1] if len(token_ids) > 1 else None
            })
    
    return results


def collect_coin(coin, days_back=7):
    """Collect all updown market prices for one coin."""
    interval_min = MARKET_CONFIG[MARKET_TYPE]["interval_seconds"] // 60
    
    print(f"\nCollecting {coin.upper()} {MARKET_TYPE} updown markets ({days_back} days)...")
    
    timestamps = generate_timestamps(days_back)
    total = len(timestamps)
    print(f"  {total} time slots → {total // BATCH_SIZE + 1} API calls")
    
    all_results = []
    
    for i in range(0, total, BATCH_SIZE):
        batch = timestamps[i:i + BATCH_SIZE]
        results = fetch_batch(coin, batch)
        all_results.extend(results)
        print(f"  Batch {i // BATCH_SIZE + 1} done — found {len(results)} markets")
    
    df = pd.DataFrame(all_results)
    if not df.empty:
        df = df.sort_values("timestamp").reset_index(drop=True)
    
    coverage = len(df) / total * 100
    missing = total - len(df)
    
    print(f"  Total slots:    {total}")
    print(f"  Markets found:  {len(df)} ({coverage:.1f}% coverage)")
    print(f"  Missing slots:  {missing}")
    
    return df


def save_all_coins():
    os.makedirs("data", exist_ok=True)
    
    coins = TEST_COINS if TEST_MODE else COINS
    days = TEST_DAYS if TEST_MODE else DAYS_BACK
    suffix = MARKET_CONFIG[MARKET_TYPE]["file_suffix"]
    
    print(f"Market type: {MARKET_TYPE}")
    print(f"Coins: {coins}")
    print(f"Days: {days}")
    
    for coin in coins:
        df = collect_coin(coin, days)
        
        if not df.empty:
            filename = f"data/polymarket_{coin}{suffix}.csv"
            df.to_csv(filename, index=False)
            print(f"  Saved to {filename}")


if __name__ == "__main__":
    save_all_coins()