import requests
import pandas as pd
import time
import os
import json
import threading
import websocket
from datetime import datetime, timezone

# ============================================================
# CONFIGURATION
# ============================================================

COINS = ["btc", "eth", "sol"]
MARKET_TYPES = ["5m", "15m"]
MARKET_INTERVALS = {"5m": 300, "15m": 900}

# WebSocket pushes prices continuously.
# LOG_INTERVAL_SECONDS controls how often we write to CSV and refresh the dashboard.
LOG_INTERVAL_SECONDS = 10

MIN_PROFITABLE_GAP = 0.03     # minimum gap to flag as opportunity (lowered from 0.06 for pilot)

# ============================================================
# PILOT MODE
# ============================================================
# PILOT_MODE = True  -> run for PILOT_DURATION_HOURS then print summary and stop
# PILOT_MODE = False -> run indefinitely (production / 7-day collection mode)

PILOT_MODE           = True   # <-- flip this switch
PILOT_DURATION_HOURS = 3      # hours to run when PILOT_MODE is True

GAP_LOG_FILE = "data/gap_log.csv"
WS_URL       = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

CSV_COLUMNS = [
    "recorded_at", "coin", "market_type", "slug",
    "market_closes", "seconds_left",
    "yes_price", "no_price", "gap", "gap_bid",
    "gap_duration_ms", "arb_size_usd",
    "opportunity"
]

# Cache: stores token IDs for currently active markets
# Format: { "btc_5m": {"slug": "...", "yes_token": "...", "no_token": "...", "closes": int} }
market_cache = {}

# Shared price state — written by WebSocket thread, read by main thread
# Format: { token_id: {"bid": float_or_None, "ask": float_or_None} }
prices = {}

# Global WebSocket reference (set in start_websocket)
ws_app = None

# Tracks when each gap episode started (for duration measurement).
# Format: { (coin, mtype): datetime } — set when gap opens, cleared when it closes.
gap_start_times = {}

# Lock to protect gap_log.csv from concurrent writes.
# _handle_ws_event() runs in the WebSocket thread; log_lock ensures
# that event-driven writes don't collide with each other.
log_lock = threading.Lock()


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
    Returns True if any slug changed (signals main loop to reconnect WebSocket).
    """
    changed = False
    for coin in COINS:
        for mtype in MARKET_TYPES:
            cache_key = f"{coin}_{mtype}"
            slug, close_ts = get_current_slug(coin, mtype)

            if cache_key not in market_cache or market_cache[cache_key]["slug"] != slug:
                print(f"  [Cache] New market: {slug}")
                yes_token, no_token = fetch_token_ids(slug)

                if yes_token and no_token:
                    market_cache[cache_key] = {
                        "slug":      slug,
                        "yes_token": yes_token,
                        "no_token":  no_token,
                        "closes":    close_ts
                    }
                    changed = True
                else:
                    print(f"  [Warning] Could not find token IDs for {slug}")

    return changed


# ============================================================
# STEP 3: WEBSOCKET — PRICE STREAMING
# ============================================================

def build_subscription():
    """
    Build the subscription JSON message for all token IDs in market_cache.
    Subscribes to 12 tokens: 3 coins x 2 market types x 2 sides (Yes + No).
    """
    all_ids = []
    for cached in market_cache.values():
        all_ids.append(cached["yes_token"])
        all_ids.append(cached["no_token"])
    return json.dumps({"assets_ids": all_ids, "type": "market"})


def on_ws_open(ws):
    """Called once when the WebSocket connection is established."""
    print(f"  [WS] Connected to {WS_URL}")
    sub = build_subscription()
    ws.send(sub)
    n = len(market_cache) * 2
    print(f"  [WS] Subscribed to {n} tokens")


def on_ws_message(ws, message):
    """
    Called on every incoming WebSocket message.

    Handles two event types:
      'book'         — initial order book snapshot sent on subscribe
                       Seeds prices{} with best bid/ask from the snapshot.
      'price_change' — real-time update whenever bid or ask moves
                       Updates prices{} in place.

    Gap calculation uses best_ask (the price you pay to buy a position).
    gap = 1.0 - yes_ask - no_ask
    """
    try:
        data = json.loads(message)

        # The WebSocket may send a list of events or a single event
        if isinstance(data, list):
            for event in data:
                _handle_ws_event(event)
        else:
            _handle_ws_event(data)

    except Exception as e:
        print(f"  [WS] Message parse error: {e}")


def _handle_ws_event(event):
    """
    Process a single WebSocket event dict.

    Confirmed message formats (verified 2026-03-01):

    book event — full order book snapshot on subscribe:
      bids: ascending list [{price, size}, ...] → best bid = bids[-1]
      asks: ascending list [{price, size}, ...] → best ask = asks[0]

    price_change event — real-time update after any trade:
      price_changes: list of per-token updates, each with:
        asset_id, best_bid, best_ask, price, side, ...
      (NOT a flat event — must iterate price_changes array)
    """
    event_type = event.get("event_type") or event.get("type", "")

    if event_type == "book":
        asset_id = event.get("asset_id")
        if not asset_id:
            return
        asks = event.get("asks", [])
        bids = event.get("bids", [])
        # Bids sorted ascending → last entry = highest (best) bid
        # Asks sorted ascending → first entry = lowest (best) ask
        best_bid  = float(bids[-1]["price"]) if bids else None
        best_ask  = float(asks[0]["price"])  if asks else None
        ask_size  = float(asks[0]["size"])   if asks else None
        prices[asset_id] = {"bid": best_bid, "ask": best_ask, "ask_size": ask_size}

    elif event_type == "price_change":
        # price_changes is a list — one entry per affected token
        for change in event.get("price_changes", []):
            asset_id = change.get("asset_id")
            if not asset_id:
                continue
            entry = prices.get(asset_id, {"bid": None, "ask": None, "ask_size": None})
            raw_bid      = change.get("best_bid")
            raw_ask      = change.get("best_ask")
            raw_ask_size = change.get("best_ask_size")
            if raw_bid      is not None: entry["bid"]      = float(raw_bid)
            if raw_ask      is not None: entry["ask"]      = float(raw_ask)
            if raw_ask_size is not None: entry["ask_size"] = float(raw_ask_size)
            prices[asset_id] = entry

    else:
        return  # unknown event type — skip logging

    # Event-driven logging: compute gaps immediately after every price update.
    # Enrich opportunity rows with gap_duration_ms, then save only those rows.
    all_obs = calculate_current_gaps()
    now_dt  = datetime.now(timezone.utc)

    for obs in all_obs:
        key = (obs["coin"], obs["market_type"])
        if obs["opportunity"]:
            if key not in gap_start_times:
                gap_start_times[key] = now_dt          # gap just opened — start timer
            obs["gap_duration_ms"] = int(
                (now_dt - gap_start_times[key]).total_seconds() * 1000
            )
        else:
            gap_start_times.pop(key, None)              # gap closed — reset timer

    to_save = [o for o in all_obs if o["opportunity"]]
    if to_save:
        with log_lock:
            save_observations(to_save)


def on_ws_error(ws, error):
    """Called on WebSocket error. run_forever(reconnect=5) handles retry."""
    print(f"  [WS] Error: {error}")


def on_ws_close(ws, close_status_code, close_msg):
    """Called when WebSocket connection closes."""
    print(f"  [WS] Connection closed (code={close_status_code})")


def start_websocket():
    """
    Create a WebSocketApp and start it in a background daemon thread.
    run_forever(reconnect=5) auto-reconnects after errors with a 5-second delay.
    Stores the WebSocketApp in ws_app so run() can close/reopen on market rotation.
    """
    global ws_app
    ws_app = websocket.WebSocketApp(
        WS_URL,
        on_open=on_ws_open,
        on_message=on_ws_message,
        on_error=on_ws_error,
        on_close=on_ws_close,
    )
    t = threading.Thread(
        target=ws_app.run_forever,
        kwargs={"reconnect": 5},
        daemon=True
    )
    t.start()
    return t


# ============================================================
# STEP 4: GAP CALCULATION FROM LIVE PRICES
# ============================================================

def calculate_current_gaps():
    """
    Calculate gaps for all markets using the current prices{} dict.
    Replaces fetch_all_prices() — no API calls, reads from WebSocket-fed state.
    Returns list of observation dicts in the same format as before.
    """
    observations = []
    now = datetime.now(timezone.utc)

    for coin in COINS:
        for mtype in MARKET_TYPES:
            cache_key = f"{coin}_{mtype}"

            if cache_key not in market_cache:
                continue

            cached   = market_cache[cache_key]
            yes_data = prices.get(cached["yes_token"])
            no_data  = prices.get(cached["no_token"])

            if not yes_data or not no_data:
                continue

            yes_ask      = yes_data.get("ask")
            no_ask       = no_data.get("ask")

            if yes_ask is None or no_ask is None:
                continue

            # Sanity filter: near market close, market makers pull liquidity.
            # Stale ask orders for the losing side can sit at 0.990, causing
            # both asks to show ~0.990 → gap = -0.98 (garbage data).
            if yes_ask > 0.90 and no_ask > 0.90:
                continue

            gap          = round(1.0 - yes_ask - no_ask, 4)

            # BID-based gap: whales use limit orders filled at bid prices.
            # gap_bid can be positive even when gap_ask is negative.
            yes_bid      = yes_data.get("bid")
            no_bid       = no_data.get("bid")
            gap_bid      = (round(1.0 - yes_bid - no_bid, 4)
                            if yes_bid is not None and no_bid is not None
                            else None)
            closes_dt    = datetime.fromtimestamp(cached["closes"], tz=timezone.utc)
            seconds_left = int((closes_dt - now).total_seconds())

            # Arb size: the bottleneck side limits how much USDC can be executed.
            # ask_size is the volume sitting at the best ask in USDC.
            yes_ask_size = yes_data.get("ask_size")
            no_ask_size  = no_data.get("ask_size")
            arb_size_usd = (
                round(min(yes_ask_size, no_ask_size), 2)
                if yes_ask_size is not None and no_ask_size is not None
                else None
            )

            opportunity = (
                gap >= MIN_PROFITABLE_GAP or
                (gap_bid is not None and gap_bid >= MIN_PROFITABLE_GAP)
            )

            observations.append({
                "recorded_at":    now.isoformat(),
                "coin":           coin,
                "market_type":    mtype,
                "slug":           cached["slug"],
                "market_closes":  closes_dt.isoformat(),
                "seconds_left":   max(0, seconds_left),
                "yes_price":      yes_ask,        # column name kept for CSV compatibility
                "no_price":       no_ask,         # column name kept for CSV compatibility
                "gap":            gap,
                "gap_bid":        gap_bid,        # bid-based gap (what limit-order traders see)
                "gap_duration_ms": 0,             # filled in by _handle_ws_event()
                "arb_size_usd":   arb_size_usd,
                "opportunity":    opportunity
            })

    return observations


# ============================================================
# STEP 5: LOGGING
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
    # Write headers if file was deleted mid-run (e.g. user manually cleaned it).
    need_header = not os.path.exists(GAP_LOG_FILE) or os.path.getsize(GAP_LOG_FILE) == 0
    df.to_csv(GAP_LOG_FILE, mode="a", header=need_header, index=False)


# ============================================================
# STEP 6: LIVE DASHBOARD
# ============================================================

def print_dashboard(observations, cycle, pilot_seconds_left=None):
    """
    Print a live status table to the terminal.

    Columns:
      MARKET   — coin + timeframe
      UP_ASK   — best ask for the Up token (cost to buy with a market order)
      DN_ASK   — best ask for the Down token
      gap_ask  — 1 - UP_ASK - DN_ASK  (AA scenario: both market orders, always ~-0.01)
      gap_bid  — 1 - UP_BID - DN_BID  (BB scenario: both limit orders — what whales use)
      ARB      — min(up_ask_size, dn_ask_size) in USDC — how much is executable
      LEFT     — seconds until market closes
      OPP      — [ASK!!] / [BID! ] / [BOTH!] / --
    """
    W = 80
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{'=' * W}")
    print(f"  GAP MONITOR | Cycle {cycle} | {now_str}")
    if pilot_seconds_left is not None:
        h, rem = divmod(int(pilot_seconds_left), 3600)
        m, s   = divmod(rem, 60)
        print(f"  PILOT MODE  | Time remaining: {h:02d}:{m:02d}:{s:02d}")
    print(f"{'=' * W}")
    print(f"  {'MARKET':<10} {'UP_ASK':>7} {'DN_ASK':>7} {'gap_ask':>8} {'gap_bid':>8} {'ARB':>7} {'LEFT':>6}  {'OPP':<8}")
    print(f"  {'-' * (W - 2)}")

    if not observations:
        print(f"  Waiting for WebSocket price data...")
    else:
        # Sort by gap_bid descending (BB scenario = what whales use).
        # Treat None gap_bid as -999 so those rows sink to the bottom.
        def sort_key(o):
            return o["gap_bid"] if o["gap_bid"] is not None else -999.0

        for obs in sorted(observations, key=sort_key, reverse=True):
            label = f"{obs['coin'].upper()} {obs['market_type']}"

            # gap_bid display
            if obs["gap_bid"] is not None:
                gap_bid_str = f"{obs['gap_bid']:>+8.4f}"
            else:
                gap_bid_str = f"{'N/A':>8}"

            # ARB size display
            if obs["arb_size_usd"] is not None:
                arb_str = f"${obs['arb_size_usd']:>6.1f}"
            else:
                arb_str = f"{'N/A':>7}"

            # OPP indicator
            ask_opp = obs["gap"] >= MIN_PROFITABLE_GAP
            bid_opp = obs["gap_bid"] is not None and obs["gap_bid"] >= MIN_PROFITABLE_GAP
            if ask_opp and bid_opp:
                opp_str = "[BOTH!]"
            elif ask_opp:
                opp_str = "[ASK!!]"
            elif bid_opp:
                opp_str = "[BID! ]"
            else:
                opp_str = "  --  "

            print(f"  {label:<10} {obs['yes_price']:>7.3f} {obs['no_price']:>7.3f} "
                  f"{obs['gap']:>+8.4f} {gap_bid_str} {arb_str} {obs['seconds_left']:>5}s  {opp_str}")

    opportunities = [o for o in observations if o["opportunity"]]
    print(f"  {'-' * (W - 2)}")
    print(f"  Opportunities: {len(opportunities)}/{len(observations)}"
          f"  |  ask=market-order gap  bid=limit-order gap  ARB=max USDC")


def print_pilot_summary():
    """Print an end-of-run summary after the pilot period completes."""
    print(f"\n{'=' * 65}")
    print(f"  PILOT MODE COMPLETE - {PILOT_DURATION_HOURS}-HOUR SUMMARY")
    print(f"{'=' * 65}")

    if not os.path.exists(GAP_LOG_FILE):
        print("  No data collected.")
        return

    df = pd.read_csv(GAP_LOG_FILE)
    if df.empty:
        print("  No data collected.")
        return

    total_opps  = len(df)   # every row in CSV is already an opportunity
    max_gap     = df["gap"].max()
    max_gap_row = df.loc[df["gap"].idxmax()]

    print(f"\n  OVERALL")
    print(f"  {'Opportunity snapshots logged:':<30} {total_opps:,}")
    print(f"  {'Largest gap seen:':<30} {max_gap:.4f}  "
          f"({max_gap_row['coin'].upper()} {max_gap_row['market_type']} "
          f"at {max_gap_row['recorded_at'][:19]})")
    print(f"  {'Average gap (opps only):':<30} {df['gap'].mean():.4f}")

    print(f"\n  BY COIN")
    for coin in ["btc", "eth", "sol"]:
        sub = df[df["coin"] == coin]
        if sub.empty:
            continue
        opps = int(sub["opportunity"].sum())
        print(f"  {coin.upper():<6}  avg gap={sub['gap'].mean():.4f}  "
              f"max gap={sub['gap'].max():.4f}  opportunities={opps}")

    print(f"\n  BY MARKET TYPE")
    for mtype in ["5m", "15m"]:
        sub = df[df["market_type"] == mtype]
        if sub.empty:
            continue
        opps = int(sub["opportunity"].sum())
        print(f"  {mtype:<6}  avg gap={sub['gap'].mean():.4f}  "
              f"max gap={sub['gap'].max():.4f}  opportunities={opps}")

    if total_opps > 0:
        print(f"\n  *** GAPS DETECTED - arbitrage may be real. Run 7-day collection. ***")
    else:
        print(f"\n  No profitable gaps found in {PILOT_DURATION_HOURS}h. Consider wider time window.")

    print(f"\n  Data saved to: {GAP_LOG_FILE}")
    print(f"{'=' * 65}")


# ============================================================
# STEP 7: MAIN LOOP
# ============================================================

def run():
    mode_label = f"PILOT ({PILOT_DURATION_HOURS}h)" if PILOT_MODE else "PRODUCTION (continuous)"
    print("=" * 65)
    print("  POLYMARKET GAP MONITOR  [WebSocket Edition]")
    print(f"  Mode: {mode_label}")
    print("  Monitoring BTC/ETH/SOL updown markets in real-time")
    print("  Press Ctrl+C to stop early")
    print("=" * 65)

    init_log_file()

    # Fetch initial token IDs
    refresh_cache_if_needed()

    # Start WebSocket in background thread
    start_websocket()

    # Wait briefly for first 'book' snapshots to seed prices{}
    print("  Waiting for WebSocket price data...")
    time.sleep(3)

    cycle         = 0
    last_log_time = time.time()
    start_time    = time.time()
    pilot_end     = start_time + PILOT_DURATION_HOURS * 3600 if PILOT_MODE else None

    while True:
        try:
            time.sleep(1)
            now = time.time()

            # Check if a market window has rotated (every 5m or 15m boundary).
            # IMPORTANT: ws_app.close() is a clean close — run_forever(reconnect=5)
            # does NOT auto-reconnect after a clean close, only after errors.
            # So we must explicitly call start_websocket() to get a fresh connection.
            if refresh_cache_if_needed():
                print("  [WS] Market window rotated — reconnecting with new tokens")
                if ws_app:
                    ws_app.close()
                prices.clear()    # drop stale prices for old token IDs
                time.sleep(2)     # allow close to complete
                start_websocket() # fresh WebSocketApp + thread with new token IDs

            # Refresh dashboard every LOG_INTERVAL_SECONDS.
            # CSV is already written event-driven in _handle_ws_event()
            # so we only need to print here — no save_observations() call.
            if now - last_log_time >= LOG_INTERVAL_SECONDS:
                cycle += 1
                observations = calculate_current_gaps()

                seconds_left = max(0, pilot_end - now) if PILOT_MODE else None
                print_dashboard(observations, cycle, pilot_seconds_left=seconds_left)
                last_log_time = now

            # Check pilot end
            if PILOT_MODE and now >= pilot_end:
                print_pilot_summary()
                ws_app.close()
                break

        except KeyboardInterrupt:
            print(f"\n\nStopped by user after {cycle} cycles.")
            if PILOT_MODE:
                print_pilot_summary()
            else:
                print(f"Data saved to: {GAP_LOG_FILE}")
            if ws_app:
                ws_app.close()
            break
        except Exception as e:
            print(f"[Error]: {e}")
            time.sleep(1)


if __name__ == "__main__":
    run()
