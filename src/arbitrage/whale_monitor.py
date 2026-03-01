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

# ============================================================
# PILOT MODE
# ============================================================
# PILOT_MODE = True  → run for PILOT_DURATION_HOURS then print summary and stop
# PILOT_MODE = False → run indefinitely (production / 7-day collection mode)

PILOT_MODE           = True   # <-- flip this switch
PILOT_DURATION_HOURS = 3      # hours to run when PILOT_MODE is True

# Keywords that identify crypto updown markets
UPDOWN_KEYWORDS = ["btc-updown", "eth-updown", "sol-updown"]

# ============================================================
# TRADE FIELD MAPPING
# ============================================================
# Verified against live data-api.polymarket.com/activity response
# on 2026-03-01 Session 6.

FIELD_SLUG      = "slug"        # confirmed: slug = "eth-updown-15m-1772365500"
FIELD_TIMESTAMP = "timestamp"   # confirmed: Unix integer, e.g. 1772365913
FIELD_OUTCOME   = "outcome"     # confirmed: "Up" or "Down" (use instead of side)
FIELD_SIZE_USD  = "usdcSize"    # confirmed: USDC spent (not "size" which is token count)
FIELD_PRICE     = "price"       # confirmed: float, e.g. 0.43

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
        response = requests.get(
            url,
            params={"user": wallet, "limit": limit, "type": "TRADE"},
            timeout=10
        )
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

        # Convert Unix timestamp to datetime
        trade_dt = pd.to_datetime(trade_timestamp, unit="s", utc=True)
        time_diffs = abs(subset["recorded_at"] - trade_dt)
        closest_idx = time_diffs.idxmin()
        closest_row = subset.loc[closest_idx]
        seconds_diff = int(time_diffs[closest_idx].total_seconds())

        # Only use if within 5 minutes (300 seconds) - otherwise too far apart
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


def print_pilot_summary():
    """Print an end-of-run summary after the pilot period completes."""
    print(f"\n{'=' * 65}")
    print(f"  PILOT MODE COMPLETE - {PILOT_DURATION_HOURS}-HOUR SUMMARY")
    print(f"{'=' * 65}")

    if not os.path.exists(WHALE_LOG_FILE):
        print("  No data collected.")
        return

    df = pd.read_csv(WHALE_LOG_FILE)
    if df.empty:
        print("  No data collected.")
        return

    total_trades    = len(df)
    both_sides      = int(df["both_sides_flag"].sum())
    both_rate       = both_sides / total_trades * 100 if total_trades else 0
    unique_whales   = df["wallet_label"].nunique()
    total_usd       = df["size_usd"].sum()

    print(f"\n  OVERALL")
    print(f"  {'Total trades logged:':<35} {total_trades:,}")
    print(f"  {'Both-sides (arbitrage signal):':<35} {both_sides:,}  ({both_rate:.1f}% of trades)")
    print(f"  {'Unique active whales:':<35} {unique_whales}")
    print(f"  {'Total USDC volume observed:':<35} ${total_usd:,.2f}")

    print(f"\n  BY COIN")
    for coin in ["btc", "eth", "sol"]:
        sub = df[df["coin"] == coin]
        if sub.empty:
            continue
        bs  = int(sub["both_sides_flag"].sum())
        print(f"  {coin.upper():<6}  trades={len(sub):>4}  both-sides={bs:>3}  "
              f"avg_size=${sub['size_usd'].mean():>8.2f}")

    print(f"\n  MOST ACTIVE WHALES")
    whale_summary = (
        df.groupby("wallet_label")
          .agg(trades=("slug", "count"),
               both_sides=("both_sides_flag", "sum"),
               total_usd=("size_usd", "sum"))
          .sort_values("trades", ascending=False)
          .head(5)
    )
    for label, row in whale_summary.iterrows():
        short = label[:24]
        print(f"  {short:<26}  trades={int(row['trades']):>4}  "
              f"both-sides={int(row['both_sides']):>3}  ${row['total_usd']:>10,.2f}")

    if both_sides > 0:
        print(f"\n  *** BOTH-SIDES TRADES FOUND - whales ARE doing arbitrage. ***")
    else:
        print(f"\n  No both-sides trades detected. Whales may be directional only.")

    print(f"\n  Data saved to: {WHALE_LOG_FILE}")
    print(f"{'=' * 65}")


# ============================================================
# MAIN LOOP
# ============================================================

def run():
    mode_label = f"PILOT ({PILOT_DURATION_HOURS}h)" if PILOT_MODE else "PRODUCTION (continuous)"
    print("=" * 65)
    print("  POLYMARKET WHALE MONITOR")
    print(f"  Mode: {mode_label}")
    print("  Monitoring known whale wallets every 30 seconds")
    print("  Press Ctrl+C to stop early")
    print("=" * 65)

    init_log_file()
    whales = load_whales()

    if not whales:
        print("No whales loaded. Please create data/known_whales.csv first.")
        return

    print(f"  Loaded {len(whales)} whale wallets")
    cycle      = 0
    start_time = time.time()
    pilot_end  = start_time + PILOT_DURATION_HOURS * 3600 if PILOT_MODE else None

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

                    # Get trade timestamp (Unix integer)
                    trade_ts = trade.get(FIELD_TIMESTAMP)
                    if not trade_ts:
                        continue

                    # Skip trades we have already logged
                    if wallet in last_seen_timestamp:
                        if int(trade_ts) <= int(last_seen_timestamp[wallet]):
                            continue

                    # Extract coin and market type
                    coin, mtype = extract_coin_and_type(slug)
                    if not coin:
                        continue

                    # Map outcome to UP/DOWN
                    # "outcome" field = "Up" or "Down" (from live API verification)
                    raw_outcome = str(trade.get(FIELD_OUTCOME, "")).strip()
                    side = "UP" if raw_outcome.lower() == "up" else "DOWN"

                    # Get USDC spent and price
                    size_usd = float(trade.get(FIELD_SIZE_USD, 0) or 0)
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

            # Build status line with optional time remaining
            if PILOT_MODE:
                secs_left = max(0, pilot_end - time.time())
                h, rem = divmod(int(secs_left), 3600)
                m, s   = divmod(rem, 60)
                time_str = f"  |  Pilot ends in {h:02d}:{m:02d}:{s:02d}"
            else:
                time_str = ""

            print(f"[Cycle {cycle} | {now.strftime('%H:%M:%S UTC')}] "
                  f"New trades: {new_trades_total} | "
                  f"Next check in {POLL_INTERVAL_SECONDS}s{time_str}")

            # Check if pilot duration has elapsed
            if PILOT_MODE and time.time() >= pilot_end:
                print_pilot_summary()
                break

            time.sleep(POLL_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            print(f"\n\nStopped by user after {cycle} cycles.")
            if PILOT_MODE:
                print_pilot_summary()
            else:
                print(f"Data saved to: {WHALE_LOG_FILE}")
            break
        except Exception as e:
            print(f"[Error in cycle {cycle}]: {e}")
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run()
