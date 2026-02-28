import requests
import pandas as pd
import os
import time

# The coins we want to track
COINS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
}

# How many days of data to pull
DAYS_BACK = 7

def get_candles(symbol, interval="1m", days_back=7):
    """
    Pull candle (kline) data from Binance public API.
    Pulls in chunks of 1000 (API limit) to cover the full time range.
    No API key needed — this is public data.
    """
    
    # Calculate start time in milliseconds
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (days_back * 24 * 60 * 60 * 1000)
    
    all_candles = []
    current_start = start_ms
    chunk = 1
    total_chunks = (days_back * 24 * 60 // 1000) + 1
    
    while current_start < now_ms:
        url = "https://api.binance.com/api/v3/klines"
        
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "limit": 1000,
        }
        
        print(f"  Fetching chunk {chunk}/{total_chunks} for {symbol}...")
        response = requests.get(url, params=params)
        data = response.json()
        
        if not data:
            break
        
        all_candles.extend(data)
        
        # Move start time to after the last candle we received
        # Each candle's first element is the open_time in milliseconds
        current_start = data[-1][0] + 60000  # +60000ms = +1 minute
        
        chunk += 1
        
        # Small delay to be nice to the API
        time.sleep(0.5)
    
    print(f"  Total candles fetched: {len(all_candles)}")
    
    # Build DataFrame (same as before)
    df = pd.DataFrame(all_candles, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore"
    ])
    
    # Keep only the columns we care about
    df = df[["open_time", "open", "high", "low", "close", "volume"]]
    
    # Convert timestamp from milliseconds to readable datetime
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    
    # Convert price columns from string to float
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    
    # Remove any duplicates just in case
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    
    return df


def save_all_coins():
    """
    Loop through all coins, fetch candles, and save to CSV.
    """
    os.makedirs("data", exist_ok=True)
    
    for name, symbol in COINS.items():
        print(f"\n{'=' * 50}")
        print(f"Fetching {DAYS_BACK} days of {name} data...")
        print(f"{'=' * 50}")
        
        df = get_candles(symbol, days_back=DAYS_BACK)
        
        filename = f"data/binance_{name.lower()}.csv"
        df.to_csv(filename, index=False)
        
        print(f"\nSaved {len(df)} rows to {filename}")
        print(f"  Time range: {df['open_time'].iloc[0]} to {df['open_time'].iloc[-1]}")
        print(f"  Expected:   ~{DAYS_BACK * 24 * 60} rows for {DAYS_BACK} days")


# Run it
save_all_coins()