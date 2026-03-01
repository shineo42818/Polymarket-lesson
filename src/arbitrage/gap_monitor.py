import requests
import pandas as pd
import time
import os
import json
from datetime import datetime, timezone

# ============================================================
# CONFIGURATION
# ============================================================

COINS = ["btc", "eth", "sol"]
MARKET_TYPES = ["5m", "15m"]
MARKET_INTERVALS = {"5m": 300, "15m": 900}

POLL_INTERVAL_SECONDS = 10    # how often to fetch prices
MIN_PROFITABLE_GAP = 0.06     # minimum gap after fees to flag as opportunity

GAP_LOG_FILE = "data/gap_log.csv"
CSV_COLUMNS = [
    "recorded_at", "coin", "market_type", "slug",
    "market_closes", "seconds_left",
    "yes_price", "no_price", "gap", "opportunity"
]

# Cache: stores token IDs for currently active markets
# Avoids re-calling Gamma API every 10 seconds
# Format: { "btc_5m": {"slug": "...", "yes_token": "...", "no_token": "...", "closes": int} }
market_cache = {}


# ============================================================
# STEP 1: MARKET SLUG CALCULATION
# ============================================================

def get_current_slug(coin, market_type):
    """
    Calculate the slug and close timestamp for the currently active market.
    Uses timestamp math to find the current 5m or 15m window.
    Returns: (slug_string, unix_close_timestamp)
    """
    interval = MARKET_INTERVALS[market_type]
    now = int(time.time())
    ts = now - (now % interval)
    slug = f"{coin}-updown-{market_type}-{ts}"
    closes_at = ts + interval
    return slug, closes_at


# ============================================================
# STEP 2: TOKEN ID FETCHING AND CACHING
# ============================================================

def fetch_token_ids(slug):
    """
    Fetch Yes and No token IDs from Gamma API for a given slug.
    Returns: (yes_token_id, no_token_id) or (None, None) if not found.
    """
    url = "https://gamma-api.polymarket.com/markets"
    try:
        response = requests.get(url, params={"slug": slug}, timeout=5)
        data = response.json()

        if not data:
            return None, None

        market = data[0]
        token_ids = json.loads(market.get("clobTokenIds", "[]"))

        if len(token_ids) < 2:
            return None, None

        return token_ids[0], token_ids[1]
    except Exception as e:
        print(f"  [Error] fetch_token_ids({slug}): {e}")
        return None, None


def refresh_cache_if_needed():
    """
    Check if any cached market has a new slug (new time window started).
    If yes, fetch new token IDs from Gamma API and update the cache.
    """
    for coin in COINS:
        for mtype in MARKET_TYPES:
            cache_key = f"{coin}_{mtype}"
            slug, close_ts = get_current_slug(coin, mtype)

            # Only refresh if slug has changed or not cached yet
            if cache_key not in market_cache or market_cache[cache_key]["slug"] != slug:
                print(f"  [Cache] New market: {slug}")
                yes_token, no_token = fetch_token_ids(slug)

                if yes_token and no_token:
                    market_cache[cache_key] = {
                        "slug": slug,
                        "yes_token": yes_token,
                        "no_token": no_token,
                        "closes": close_ts
                    }
                else:
                    print(f"  [Warning] Could not find token IDs for {slug}")


# ============================================================
# STEP 3: PRICE FETCHING
# ============================================================

def fetch_midpoint(token_id):
    """
    Fetch the midpoint price for a single token from CLOB API.
    Midpoint = average of best bid and best ask.
    Returns: float price, or None if error.

    IMPORTANT: Uses /midpoint not /book (book returns stale data).
    """
    url = "https://clob.polymarket.com/midpoint"
    try:
        response = requests.get(url, params={"token_id": token_id}, timeout=5)
        data = response.json()
        mid = data.get("mid")
        return float(mid) if mid is not None else None
    except:
        return None


def fetch_all_prices():
    """
    Fetch Yes and No midpoint prices for all cached markets.
    Returns: list of observation dicts ready to log.
    """
    observations = []
    now = datetime.now(timezone.utc)

    for coin in COINS:
        for mtype in MARKET_TYPES:
            cache_key = f"{coin}_{mtype}"

            if cache_key not in market_cache:
                continue

            cached = market_cache[cache_key]
            yes_price = fetch_midpoint(cached["yes_token"])
            no_price  = fetch_midpoint(cached["no_token"])

            if yes_price is None or no_price is None:
                continue

            gap = round(1.0 - yes_price - no_price, 4)
            closes_dt = datetime.fromtimestamp(cached["closes"], tz=timezone.utc)
            seconds_left = int((closes_dt - now).total_seconds())

            observations.append({
                "recorded_at":   now.isoformat(),
                "coin":          coin,
                "market_type":   mtype,
                "slug":          cached["slug"],
                "market_closes": closes_dt.isoformat(),
                "seconds_left":  max(0, seconds_left),
                "yes_price":     yes_price,
                "no_price":      no_price,
                "gap":           gap,
                "opportunity":   gap >= MIN_PROFITABLE_GAP
            })

    return observations


# ============================================================
# STEP 4: LOGGING
# ============================================================

def init_log_file():
    """Create the CSV file with headers if it does not exist yet."""
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(GAP_LOG_FILE):
        pd.DataFrame(columns=CSV_COLUMNS).to_csv(GAP_LOG_FILE, index=False)
        print(f"  Created {GAP_LOG_FILE}")


def save_observations(observations):
    """Append new observations to the gap log CSV."""
    if not observations:
        return
    df = pd.DataFrame(observations)[CSV_COLUMNS]
    df.to_csv(GAP_LOG_FILE, mode="a", header=False, index=False)


# ============================================================
# STEP 5: LIVE DASHBOARD
# ============================================================

def print_dashboard(observations, cycle):
    """Print a clean status table to the terminal."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{'=' * 65}")
    print(f"  GAP MONITOR | Cycle {cycle} | {now_str}")
    print(f"{'=' * 65}")
    print(f"  {'MARKET':<22} {'YES':>6} {'NO':>6} {'GAP':>7} {'LEFT':>6} {'OPP':>6}")
    print(f"  {'-' * 58}")

    for obs in sorted(observations, key=lambda x: x["gap"], reverse=True):
        label = f"{obs['coin'].upper()} {obs['market_type']}"
        opp_str = "YES ***" if obs["opportunity"] else "no"
        print(f"  {label:<22} {obs['yes_price']:>6.3f} {obs['no_price']:>6.3f} "
              f"{obs['gap']:>7.4f} {obs['seconds_left']:>5}s {opp_str:>6}")

    opportunities = [o for o in observations if o["opportunity"]]
    print(f"\n  Opportunities (gap > {MIN_PROFITABLE_GAP}): {len(opportunities)}/{len(observations)}")
    print(f"  Logging to: {GAP_LOG_FILE}")


# ============================================================
# STEP 6: MAIN LOOP
# ============================================================

def run():
    print("=" * 65)
    print("  POLYMARKET GAP MONITOR")
    print("  Monitoring BTC/ETH/SOL updown markets every 10 seconds")
    print("  Press Ctrl+C to stop")
    print("=" * 65)

    init_log_file()
    cycle = 0

    while True:
        try:
            cycle += 1

            # Refresh token ID cache if market window changed
            refresh_cache_if_needed()

            # Fetch all live prices
            observations = fetch_all_prices()

            # Save to CSV
            save_observations(observations)

            # Print dashboard
            print_dashboard(observations, cycle)

            # Wait before next cycle
            time.sleep(POLL_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            print(f"\n\nStopped by user after {cycle} cycles.")
            print(f"Data saved to: {GAP_LOG_FILE}")
            break
        except Exception as e:
            print(f"[Error in cycle {cycle}]: {e}")
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run()
