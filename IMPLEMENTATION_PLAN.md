# IMPLEMENTATION PLAN — Real-Time Gap Monitor & Whale Tracker
# Project: Polymarket Arbitrage Research
# Prepared for: Supakorn's quant research team
# Date: March 1, 2026 | Session 5

============================================================
OVERVIEW
============================================================

This document is a complete step-by-step instruction guide for
building two real-time data collection systems:

  SYSTEM 1: gap_monitor.py
    Watches live Polymarket prices every 10 seconds.
    Detects when Yes_price + No_price < $1.00 (the gap).
    Logs every observation to data/gap_log.csv.

  SYSTEM 2: whale_monitor.py
    Watches known whale wallets every 30 seconds.
    Detects new trades on crypto updown markets.
    Logs every trade to data/whale_log.csv.

Both systems run simultaneously (in separate terminal windows)
and their data is later combined to answer:
  "Do whales trade when the gap is open, and does it close after?"

============================================================
WHY THIS APPROACH
============================================================

KEY DISCOVERY FROM SESSIONS 1-5:

  Historical data is useless for gap analysis because:
  - Resolved markets always show yes_price = 1.0 or 0.0
  - The gap only exists DURING a live market (minutes or seconds)
  - CLOB price history API samples every ~10 minutes (too coarse)

  The only solution is FORWARD-LOOKING real-time collection.
  We must collect data AS IT HAPPENS, not retrieve it later.

THE GAP EXPLAINED:

  In a perfect market:   Yes_ask + No_ask = $1.00
  When gap exists:       Yes_ask + No_ask < $1.00

  Example:
    Yes_ask = $0.52 (cost to bet BTC goes UP)
    No_ask  = $0.44 (cost to bet BTC goes DOWN)
    Sum     = $0.96
    Gap     = $0.04 (4% guaranteed profit if you buy both)

  The whale buys BOTH sides simultaneously.
  One side always pays $1.00.
  Guaranteed $0.04 profit on $0.96 investment = 4.17% ROI.
  Zero directional risk.

  Fee formula (important for minimum gap threshold):
    fee = price x (1 - price) x 0.0625
    At 50/50: ~3.03% per side = ~6% total for both sides
    Minimum profitable gap after fees: ~0.06 ($0.06 per $1)

============================================================
PRE-REQUISITES
============================================================

ENVIRONMENT:
  Python 3.14.3 in venv
  Windows (Cursor IDE)
  Project at: C:\Users\Supakorn.Co\Documents\Polymarket lesson\

LIBRARIES NEEDED:
  Already installed: requests, pandas
  NEW to install (for future WebSocket upgrade only):
    python -m pip install websocket-client

FILES ALREADY EXIST (do not modify):
  src/arbitrage/collect_polymarket.py  (reference for API patterns)
  src/get_trades.py                    (reference for whale API patterns)
  data/polymarket_btc.csv              (has token IDs for reference)

FILES TO CREATE:
  data/known_whales.csv                (create manually — see Part B Step 1)
  src/arbitrage/gap_monitor.py         (System 1 — build first)
  src/arbitrage/whale_monitor.py       (System 2 — build second)
  src/arbitrage/analyze_realtime.py    (System 3 — build after 7 days)
  data/gap_log.csv                     (auto-created by gap_monitor)
  data/whale_log.csv                   (auto-created by whale_monitor)

============================================================
PART A: SYSTEM 1 — gap_monitor.py
============================================================

------------------------------------------------------------
STEP A1: UNDERSTAND THE DATA FLOW
------------------------------------------------------------

Every 10 seconds, gap_monitor.py does this:

  STEP 1 — FIND ACTIVE MARKETS
  Calculate which markets are open right now using timestamp math:

    import time
    now = int(time.time())
    ts_5m  = now - (now % 300)   # round down to 5-min boundary
    ts_15m = now - (now % 900)   # round down to 15-min boundary

    Active slugs:
      btc-updown-5m-{ts_5m}
      btc-updown-15m-{ts_15m}
      eth-updown-5m-{ts_5m}
      eth-updown-15m-{ts_15m}
      sol-updown-5m-{ts_5m}
      sol-updown-15m-{ts_15m}

  STEP 2 — FETCH TOKEN IDs (CACHED)
  Call Gamma API to get both Yes and No token IDs per market:

    GET https://gamma-api.polymarket.com/markets?slug={slug}
    Extract: clobTokenIds[0] = Yes token ID
             clobTokenIds[1] = No token ID

    IMPORTANT: Cache these! Same market runs for 5 or 15 minutes.
    Only re-fetch when the slug changes (new window starts).
    This saves ~90% of Gamma API calls.

  STEP 3 — FETCH LIVE PRICES
  For each token ID, call CLOB midpoint endpoint:

    GET https://clob.polymarket.com/midpoint?token_id={TOKEN_ID}
    Returns: {"mid": "0.52"}

    Do this for BOTH Yes and No tokens of each market.
    Total calls per 10-second cycle:
      3 coins x 2 market types x 2 tokens = 12 API calls

    NOTE: Use /midpoint NOT /book endpoint.
    The /book endpoint returns stale data (known bug, GitHub #180).
    /midpoint is accurate and fast.

  STEP 4 — CALCULATE THE GAP
    yes_price = float(midpoint response for Yes token)
    no_price  = float(midpoint response for No token)
    gap       = 1.00 - yes_price - no_price
    opportunity = True if gap >= 0.06

  STEP 5 — LOG TO CSV
  Append one row per market per cycle to data/gap_log.csv:

    recorded_at    | "2026-03-01 14:23:10+00:00"
    coin           | "btc"
    market_type    | "5m"
    slug           | "btc-updown-5m-1772287200"
    market_closes  | "2026-03-01 14:25:00+00:00"
    seconds_left   | 110
    yes_price      | 0.52
    no_price       | 0.44
    gap            | 0.04
    opportunity    | False  (0.04 < 0.06 threshold)

  STEP 6 — PRINT LIVE DASHBOARD
  Show current status in terminal for human monitoring.

------------------------------------------------------------
STEP A2: FULL CODE — gap_monitor.py
------------------------------------------------------------

Copy this entire block into src/arbitrage/gap_monitor.py:

---------- BEGIN CODE ----------

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

---------- END CODE ----------


------------------------------------------------------------
STEP A3: TESTING gap_monitor.py — DO THESE IN ORDER
------------------------------------------------------------

TEST 1: Verify midpoint endpoint works
  In terminal, replace TOKEN_ID with any ID from data/polymarket_btc.csv:
    python -c "import requests; r = requests.get('https://clob.polymarket.com/midpoint', params={'token_id': 'TOKEN_ID_HERE'}); print(r.json())"
  Expected: {"mid": "0.52"} or similar number

TEST 2: Verify slug calculation
    python -c "import time; now=int(time.time()); ts=now-(now%300); print(f'btc-updown-5m-{ts}')"
  Expected: A slug like btc-updown-5m-1772287200
  Verify it exists: go to polymarket.com/event/[that-slug]

TEST 3: Run gap_monitor for 3 cycles only
  Edit gap_monitor.py temporarily:
    Change: while True:
    To:     for cycle in range(1, 4):
  Run: python src/arbitrage/gap_monitor.py
  Expected output: Dashboard table with 6 rows (3 coins x 2 types)
  Check: data/gap_log.csv should have ~18 rows
  REVERT the change (back to while True) after testing

TEST 4: Verify CSV output
  In terminal:
    python -c "import pandas as pd; df=pd.read_csv('data/gap_log.csv'); print(df.to_string())"
  Check:
    - All 10 columns present
    - yes_price + no_price adds up close to 1.00
    - gap is close to 0.00 (normal market — no opportunity expected yet)
    - seconds_left is counting down from ~300 or ~900


============================================================
PART B: SYSTEM 2 — whale_monitor.py
============================================================

------------------------------------------------------------
STEP B1: CREATE data/known_whales.csv MANUALLY FIRST
------------------------------------------------------------

Before writing any code, create the whale list.

HOW TO FIND WHALE WALLET ADDRESSES:
  1. Run: python src/get_trades.py
  2. Choose: Crypto category
  3. Choose: Weekly time period
  4. Choose: Sort by PnL
  5. Note the top 10 wallet addresses shown

CREATE THE FILE:
  Open Notepad (or Cursor), create new file at:
    data/known_whales.csv

  File contents (replace with real wallet addresses):
    wallet,label,discovered_session
    0x1979ae...,ArbitrageBot1,Session2
    0xABCD...,BoneReader,Session2
    0x1234...,TopPnL_3,Session5
    [add more rows for each whale]

  Save the file before running whale_monitor.py.


------------------------------------------------------------
STEP B2: UNDERSTAND THE TRADE API
------------------------------------------------------------

Before finalizing the code, verify the exact field names
returned by the activity API for your whale wallets.

Run this (replace WALLET with a real whale address):
  python -c "
  import requests, json
  r = requests.get('https://data-api.polymarket.com/activity',
    params={'user': 'WALLET_ADDRESS_HERE', 'limit': 2})
  print(json.dumps(r.json(), indent=2))
  "

Look for these fields in the output and note their exact names:
  - The field containing the market slug (may be 'market' or 'slug')
  - The field containing the trade timestamp
  - The field containing the trade side (BUY/SELL or UP/DOWN)
  - The field containing the trade size in USD
  - The field containing the trade price

UPDATE the field names in the TRADE FIELD MAPPING section
of whale_monitor.py before running it.


------------------------------------------------------------
STEP B3: FULL CODE — whale_monitor.py
------------------------------------------------------------

Copy this entire block into src/arbitrage/whale_monitor.py:

---------- BEGIN CODE ----------

import requests
import pandas as pd
import time
import os
from datetime import datetime, timezone

# ============================================================
# CONFIGURATION
# ============================================================

WHALE_LOG_FILE    = "data/whale_log.csv"
GAP_LOG_FILE      = "data/gap_log.csv"
KNOWN_WHALES_FILE = "data/known_whales.csv"

POLL_INTERVAL_SECONDS = 30  # how often to check whale activity

# Keywords that identify crypto updown markets
UPDOWN_KEYWORDS = ["btc-updown", "eth-updown", "sol-updown"]

# ============================================================
# TRADE FIELD MAPPING
# ============================================================
# IMPORTANT: Verify these field names by running the test in Step B2!
# Adjust if the API returns different field names.

FIELD_SLUG      = "market"       # field containing the market slug
FIELD_TIMESTAMP = "timestamp"    # field containing the trade time
FIELD_SIDE      = "side"         # field containing BUY or SELL
FIELD_SIZE      = "size"         # field containing USD amount
FIELD_PRICE     = "price"        # field containing trade price

CSV_COLUMNS = [
    "recorded_at", "wallet", "wallet_label", "coin", "market_type",
    "slug", "side", "size_usd", "price", "trade_timestamp",
    "gap_at_time", "gap_seconds_diff", "both_sides_flag"
]

# In-memory tracker to detect both-sides trading
# Format: { "wallet_slug": [{"side": "UP", "time": unix_timestamp}, ...] }
recent_trades_tracker = {}

# Track last seen timestamp per wallet to avoid duplicate logging
last_seen_timestamp = {}


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def load_whales():
    """Load the known whale wallet list from CSV."""
    if not os.path.exists(KNOWN_WHALES_FILE):
        print(f"ERROR: {KNOWN_WHALES_FILE} not found!")
        print(f"Create it with columns: wallet,label,discovered_session")
        return []
    df = pd.read_csv(KNOWN_WHALES_FILE)
    return df.to_dict("records")


def is_updown_market(slug):
    """Return True if the slug is a crypto updown market."""
    if not slug:
        return False
    return any(kw in str(slug) for kw in UPDOWN_KEYWORDS)


def extract_coin_and_type(slug):
    """
    Extract coin and market type from slug string.
    Example: "btc-updown-5m-1772287200" returns ("btc", "5m")
    Returns: (coin, market_type) or (None, None) if not recognized.
    """
    slug = str(slug)
    for coin in ["btc", "eth", "sol"]:
        if slug.startswith(coin):
            if "updown-5m" in slug:
                return coin, "5m"
            elif "updown-15m" in slug:
                return coin, "15m"
    return None, None


def fetch_recent_trades(wallet, limit=50):
    """
    Fetch recent trades for a whale wallet from Polymarket Data API.
    Returns list of trade dicts, or empty list on error.
    """
    url = "https://data-api.polymarket.com/activity"
    try:
        response = requests.get(url, params={"user": wallet, "limit": limit}, timeout=10)
        return response.json() if response.status_code == 200 else []
    except Exception as e:
        print(f"  [Error] fetch_recent_trades({wallet[:10]}...): {e}")
        return []


def find_gap_at_time(trade_timestamp, coin, market_type):
    """
    Find the gap that existed at the time of the whale's trade.
    Searches gap_log.csv for the closest entry by timestamp.
    Returns: (gap_value, seconds_difference) or (None, None).
    """
    if not os.path.exists(GAP_LOG_FILE):
        return None, None

    try:
        gap_df = pd.read_csv(GAP_LOG_FILE)
        if gap_df.empty:
            return None, None

        gap_df["recorded_at"] = pd.to_datetime(gap_df["recorded_at"], utc=True)

        # Filter for same coin and market type
        subset = gap_df[
            (gap_df["coin"] == coin) &
            (gap_df["market_type"] == market_type)
        ]

        if subset.empty:
            return None, None

        # Find the closest time entry
        trade_dt = pd.to_datetime(trade_timestamp, utc=True)
        time_diffs = abs(subset["recorded_at"] - trade_dt)
        closest_idx = time_diffs.idxmin()
        closest_row = subset.loc[closest_idx]
        seconds_diff = int(time_diffs[closest_idx].total_seconds())

        # Only use if within 5 minutes (300 seconds) — otherwise too far apart
        if seconds_diff > 300:
            return None, seconds_diff

        return float(closest_row["gap"]), seconds_diff

    except Exception as e:
        print(f"  [Error] find_gap_at_time: {e}")
        return None, None


def detect_both_sides(wallet, slug, current_side, window_seconds=120):
    """
    Check if this whale recently traded the OPPOSITE side of the same market.
    If yes, this is the signature of true arbitrage (buy both Up AND Down).
    window_seconds: how many seconds back to look for the opposite trade.
    Returns: True if opposite side was also traded recently.
    """
    key = f"{wallet}_{slug}"
    now = time.time()

    if key not in recent_trades_tracker:
        recent_trades_tracker[key] = []

    # Record the current trade
    recent_trades_tracker[key].append({
        "side": current_side,
        "time": now
    })

    # Remove old trades outside the window
    recent_trades_tracker[key] = [
        t for t in recent_trades_tracker[key]
        if now - t["time"] <= window_seconds
    ]

    # Check if the opposite side was traded within the window
    opposite = "DOWN" if current_side == "UP" else "UP"
    return any(t["side"] == opposite for t in recent_trades_tracker[key])


def init_log_file():
    """Create whale_log.csv with headers if it does not exist."""
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(WHALE_LOG_FILE):
        pd.DataFrame(columns=CSV_COLUMNS).to_csv(WHALE_LOG_FILE, index=False)
        print(f"  Created {WHALE_LOG_FILE}")


# ============================================================
# MAIN LOOP
# ============================================================

def run():
    print("=" * 65)
    print("  POLYMARKET WHALE MONITOR")
    print("  Monitoring known whale wallets every 30 seconds")
    print("  Press Ctrl+C to stop")
    print("=" * 65)

    init_log_file()
    whales = load_whales()

    if not whales:
        print("No whales loaded. Please create data/known_whales.csv first.")
        return

    print(f"  Loaded {len(whales)} whale wallets")
    cycle = 0

    while True:
        try:
            cycle += 1
            now = datetime.now(timezone.utc)
            new_trades_total = 0

            for whale in whales:
                wallet = whale["wallet"]
                label  = whale.get("label", wallet[:8])

                # Fetch recent trades for this whale
                trades = fetch_recent_trades(wallet)
                new_rows = []

                for trade in trades:
                    # Get market slug from trade
                    slug = trade.get(FIELD_SLUG, "")

                    # Skip non-updown markets
                    if not is_updown_market(slug):
                        continue

                    # Get trade timestamp
                    trade_ts = trade.get(FIELD_TIMESTAMP)
                    if not trade_ts:
                        continue

                    # Skip trades we have already logged
                    if wallet in last_seen_timestamp:
                        if str(trade_ts) <= str(last_seen_timestamp[wallet]):
                            continue

                    # Extract coin and market type
                    coin, mtype = extract_coin_and_type(slug)
                    if not coin:
                        continue

                    # Map side to UP/DOWN
                    raw_side = str(trade.get(FIELD_SIDE, "")).upper()
                    side = "UP" if raw_side == "BUY" else "DOWN"

                    # Get size and price
                    size_usd = float(trade.get(FIELD_SIZE, 0) or 0)
                    price    = float(trade.get(FIELD_PRICE, 0) or 0)

                    # Look up gap at the time of trade
                    gap_val, gap_diff = find_gap_at_time(trade_ts, coin, mtype)

                    # Detect both-sides trading
                    both_sides = detect_both_sides(wallet, slug, side)

                    new_rows.append({
                        "recorded_at":      now.isoformat(),
                        "wallet":           wallet,
                        "wallet_label":     label,
                        "coin":             coin,
                        "market_type":      mtype,
                        "slug":             slug,
                        "side":             side,
                        "size_usd":         size_usd,
                        "price":            price,
                        "trade_timestamp":  trade_ts,
                        "gap_at_time":      gap_val,
                        "gap_seconds_diff": gap_diff,
                        "both_sides_flag":  both_sides
                    })

                # Save new trades to CSV
                if new_rows:
                    df = pd.DataFrame(new_rows)[CSV_COLUMNS]
                    df.to_csv(WHALE_LOG_FILE, mode="a", header=False, index=False)
                    new_trades_total += len(new_rows)
                    print(f"  [{label}] {len(new_rows)} new trades logged")

                    # Check for both-sides trades
                    both_count = sum(1 for r in new_rows if r["both_sides_flag"])
                    if both_count > 0:
                        print(f"  *** BOTH-SIDES DETECTED: {label} traded both Up AND Down! ***")

                # Update last seen timestamp
                if trades:
                    ts_values = [t.get(FIELD_TIMESTAMP) for t in trades if t.get(FIELD_TIMESTAMP)]
                    if ts_values:
                        last_seen_timestamp[wallet] = max(ts_values)

            print(f"[Cycle {cycle} | {now.strftime('%H:%M:%S UTC')}] "
                  f"New trades: {new_trades_total} | "
                  f"Next check in {POLL_INTERVAL_SECONDS}s")

            time.sleep(POLL_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            print(f"\n\nStopped by user after {cycle} cycles.")
            print(f"Data saved to: {WHALE_LOG_FILE}")
            break
        except Exception as e:
            print(f"[Error in cycle {cycle}]: {e}")
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run()

---------- END CODE ----------


------------------------------------------------------------
STEP B4: TESTING whale_monitor.py — DO THESE IN ORDER
------------------------------------------------------------

TEST 1: Verify known_whales.csv loads correctly
    python -c "import pandas as pd; print(pd.read_csv('data/known_whales.csv'))"
  Expected: A table showing your whale wallets and labels

TEST 2: Verify trade API field names (CRITICAL)
  Run get_trades.py to see actual trade data first.
  Then run:
    python -c "
    import requests, json
    r = requests.get('https://data-api.polymarket.com/activity',
      params={'user': 'PASTE_WHALE_WALLET_HERE', 'limit': 2})
    print(json.dumps(r.json()[:1], indent=2))
    "
  Check the actual field names and update FIELD_SLUG, FIELD_TIMESTAMP,
  FIELD_SIDE, FIELD_SIZE, FIELD_PRICE at the top of whale_monitor.py.

TEST 3: Run for 2 cycles only
  Edit whale_monitor.py temporarily:
    Change: while True:
    To:     for cycle in range(1, 3):
  Run: python src/arbitrage/whale_monitor.py
  Expected: Output showing cycles, possibly 0 new trades (OK if no activity)
  Check: data/whale_log.csv exists (even if empty, headers should be there)
  REVERT the change (back to while True) after testing

TEST 4: Verify CSV output after any trades are logged
    python -c "import pandas as pd; df=pd.read_csv('data/whale_log.csv'); print(df.to_string())"
  Check: All 13 columns present, values look reasonable


============================================================
PART C: RUNNING BOTH SYSTEMS TOGETHER
============================================================

------------------------------------------------------------
STEP C1: OPEN TWO TERMINAL WINDOWS IN CURSOR
------------------------------------------------------------

To open a second terminal in Cursor:
  Click the + button in the terminal panel
  OR press Ctrl+Shift+` (backtick)

Terminal 1 — Gap Monitor:
  cd "C:\Users\Supakorn.Co\Documents\Polymarket lesson"
  venv\Scripts\activate
  python src/arbitrage/gap_monitor.py

Terminal 2 — Whale Monitor:
  cd "C:\Users\Supakorn.Co\Documents\Polymarket lesson"
  venv\Scripts\activate
  python src/arbitrage/whale_monitor.py

Both run independently and continuously.
gap_log.csv and whale_log.csv grow in parallel.
whale_monitor reads gap_log.csv to cross-reference prices.

------------------------------------------------------------
STEP C2: DAILY HEALTH CHECK COMMAND
------------------------------------------------------------

Each morning, run this to verify collection is working:

  python -c "
  import pandas as pd, os
  gap = pd.read_csv('data/gap_log.csv') if os.path.exists('data/gap_log.csv') else None
  whale = pd.read_csv('data/whale_log.csv') if os.path.exists('data/whale_log.csv') else None
  print('=== DAILY HEALTH CHECK ===')
  if gap is not None:
      print(f'Gap log rows:         {len(gap)}')
      print(f'Opportunities found:  {gap[\"opportunity\"].sum()}')
      print(f'Latest entry:         {gap[\"recorded_at\"].max()}')
  if whale is not None:
      print(f'Whale log rows:       {len(whale)}')
      print(f'Both-sides trades:    {whale[\"both_sides_flag\"].sum()}')
      print(f'Latest whale trade:   {whale[\"recorded_at\"].max()}')
  "

------------------------------------------------------------
STEP C3: IF A SCRIPT CRASHES
------------------------------------------------------------

Both scripts have try/except — they auto-recover from single errors.
If a script crashes completely, just restart it:
  python src/arbitrage/gap_monitor.py

CSV files are append-only. No data is lost on restart.

------------------------------------------------------------
STEP C4: KEEPING LAPTOP AWAKE
------------------------------------------------------------

If the laptop sleeps, both scripts pause and miss data.
To prevent this:
  1. Open Settings → System → Power & Sleep
  2. Set "When plugged in, PC goes to sleep after: Never"
  3. Keep the laptop plugged in while collecting

OR ask the quant team to run the scripts on a cloud server
for uninterrupted 7-day collection.


============================================================
PART D: SYSTEM 3 — analyze_realtime.py (after 7 days)
============================================================

Build this ONLY after 7 days of gap_log and whale_log data.
This is the analysis that answers the research questions.

------------------------------------------------------------
KEY QUESTIONS TO ANSWER AND HOW
------------------------------------------------------------

QUESTION 1: Gap frequency (how often per hour does gap appear?)
  gap_df["hour"] = pd.to_datetime(gap_df["recorded_at"]).dt.hour
  freq = gap_df.groupby("hour")["opportunity"].mean() * 100
  freq.plot(kind="bar", title="Gap Opportunity % by Hour of Day")
  plt.savefig("charts/gap_frequency_by_hour.png")

QUESTION 2: Gap duration (how long does gap stay open?)
  Find runs of consecutive True values in the "opportunity" column.
  Measure time from first True to first False after it.
  Plot: histogram of durations in seconds

QUESTION 3: Gap size distribution (histogram)
  gap_df["gap"].hist(bins=50)
  plt.title("Distribution of Gap Sizes")
  plt.savefig("charts/gap_distribution.png")

QUESTION 4: Gap timing within market life
  gap_df.groupby("seconds_left")["gap"].mean().plot()
  (Does gap appear more at start, middle, or end of market?)

QUESTION 5: Whale correlation
  Merge whale_log and gap_log on nearest timestamp.
  Compare: average gap_at_time for whale trades vs overall average.
  If whale gap_at_time > overall average gap → whales trade when gaps are large.

QUESTION 6: Binance connection
  Load binance_btc.csv.
  Calculate momentum as in analyze_signal.py.
  Merge with gap_log on nearest timestamp.
  Check: does high Binance momentum correlate with large gaps?


============================================================
PART E: FUTURE UPGRADE — WebSocket
============================================================

After REST polling is confirmed working AND 7 days of data prove
that gaps exist, upgrade gap_monitor to WebSocket for true
real-time detection (sub-second vs current 10-second polling).

INSTALL:
  python -m pip install websocket-client

WEBSOCKET ENDPOINT:
  wss://ws-subscriptions-clob.polymarket.com/ws/market

SUBSCRIPTION MESSAGE (send once after connecting):
  {
    "assets_ids": ["YES_TOKEN_ID", "NO_TOKEN_ID", ...],
    "type": "market"
  }

MESSAGE TYPES YOU WILL RECEIVE:
  "book"             — full order book snapshot on connect
  "price_change"     — real-time bid/ask update (use this for gap calc)
  "last_trade_price" — price of most recent actual trade

KEY FIELDS IN price_change:
  best_bid  — highest buy offer
  best_ask  — lowest sell offer
  Use best_ask for gap calculation (cost to buy)

BUILD THIS ONLY AFTER:
  1. REST polling version is confirmed working
  2. 7 days of data confirm gaps actually exist
  3. Quant team needs faster than 10-second detection


============================================================
KNOWN ISSUES AND WATCHOUTS
============================================================

ISSUE 1: Order book endpoint returns stale data
  DO NOT USE: GET https://clob.polymarket.com/book
  USE INSTEAD: GET https://clob.polymarket.com/midpoint
  Reason: /book returns 0.01/0.99 ghost data (GitHub issue #180)

ISSUE 2: Whale API field names
  The data-api activity endpoint field names MUST be verified
  against live data before finalizing whale_monitor.py.
  Different API versions may use different field names.

ISSUE 3: Gap threshold calibration
  MIN_PROFITABLE_GAP = 0.06 is estimated based on 3% fee per side.
  After collecting data, recalibrate based on actual observed fees.

ISSUE 4: Rate limits
  Polymarket CLOB: not officially documented.
  Observed safe rate: 1-2 requests per second.
  gap_monitor: 12 calls per 10-second cycle = 1.2 calls/second (fine).

ISSUE 5: Market cache at boundary
  Brief window (~10 seconds) at each 5-min/15-min boundary where
  cache may point to the old market. Acceptable for research.

ISSUE 6: Windows laptop sleep
  Laptop sleep pauses collection. Set power settings to Never Sleep.


============================================================
SUCCESS CRITERIA
============================================================

After 7 days of running both systems, we can answer:

  Does the gap actually appear? (gap > 0.06 at all?)
  How often per day? (frequency — ideally charted by hour)
  How long does it last? (seconds)
  Do whales trade when it appears? (correlation)
  Does the gap close AFTER the whale trades? (causation?)
  Does Binance momentum predict gap appearances? (trigger signal)

If YES to most: the arbitrage is real. Quant team can build trading system.
If NO: pivot to momentum strategy (already proven profitable in Session 4).


============================================================
FILE CHECKLIST
============================================================

STEP  FILE                                    STATUS
----  ----                                    ------
1     data/known_whales.csv                   Create manually before coding
2     src/arbitrage/gap_monitor.py            Copy code from Part A Step 2
3     src/arbitrage/whale_monitor.py          Copy code from Part B Step 3
4     data/gap_log.csv                        Auto-created on first run
5     data/whale_log.csv                      Auto-created on first run
6     src/arbitrage/analyze_realtime.py       Build after 7 days of data

============================================================
END OF IMPLEMENTATION PLAN
============================================================
