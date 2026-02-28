import requests
import pandas as pd
import os

# The coins we want to track
COINS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
}

def get_candles(symbol, interval="1m", limit=1000):
    """
    Pull candle (kline) data from Binance public API.
    No API key needed — this is public data.
    """
    url = "https://api.binance.com/api/v3/klines"
    
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    }
    
    print(f"Fetching {limit} candles for {symbol}...")
    response = requests.get(url, params=params)
    data = response.json()
    
    # Binance returns a list of lists — each inner list is one candle
    # Column order is fixed by Binance (we only need the first 6)
    df = pd.DataFrame(data, columns=[
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
    
    return df


def save_all_coins():
    """
    Loop through all coins, fetch candles, and save to CSV.
    """
    # Make sure data folder exists
    os.makedirs("data", exist_ok=True)
    
    for name, symbol in COINS.items():
        df = get_candles(symbol)
        
        filename = f"data/binance_{name.lower()}.csv"
        df.to_csv(filename, index=False)
        
        print(f"Saved {len(df)} rows to {filename}")
        print(f"  Time range: {df['open_time'].iloc[0]} → {df['open_time'].iloc[-1]}")
        print()


# Run it
save_all_coins()
