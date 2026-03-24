"""
collect_chainlink.py — Polygon Chainlink Price Feed oracle event collector.

Subscribes to AnswerUpdated events from the Chainlink BTC/USD aggregator on
Polygon mainnet via an Alchemy (or Infura) WebSocket endpoint. Records oracle
price updates with local receive timestamps for Phase 1B of the
Binance→Chainlink lead-lag research (LEAD_LAG_RESEARCH_PLAN.md).

Architecture note (Phase 0 finding):
  Polymarket uses Chainlink Data Streams (pull-based, sub-second, gated API)
  NOT the legacy push-based Price Feeds. This script uses the Polygon legacy
  Price Feed as the closest publicly-accessible proxy:
    BTC/USD Polygon proxy: 0xc907E116054Ad103354f2D350FD2514433D57F6f
    Deviation threshold: 0.1% | Heartbeat: 60s | Block time: ~2s

Setup:
  1. Get a free Alchemy account at https://alchemy.com
  2. Create a Polygon Mainnet app → copy the WebSocket URL
  3. Set environment variable:
       export ALCHEMY_WSS_URL="wss://polygon-mainnet.g.alchemy.com/v2/YOUR_KEY"
     OR edit ALCHEMY_WSS_URL below directly (do not commit your key).

Output: data/chainlink_updates_btc.csv

Run:
    python src/arbitrage/collect_chainlink.py

Stop: Ctrl+C
"""

import json
import os
import time
import threading
import requests
from datetime import datetime, timezone

import websocket  # websocket-client

# ============================================================
# CONFIGURATION
# ============================================================

# Paste your Alchemy Polygon WebSocket URL here, or set env var ALCHEMY_WSS_URL
# Example: "wss://polygon-mainnet.g.alchemy.com/v2/abc123..."
ALCHEMY_WSS_URL = os.environ.get("ALCHEMY_WSS_URL", "")

# Chainlink BTC/USD proxy contract on Polygon mainnet
PROXY_ADDRESS = "0xc907E116054Ad103354f2D350FD2514433D57F6f"

# AnswerUpdated event topic0 (same on all chains)
ANSWER_UPDATED_TOPIC = (
    "0x0559884fd3a460db3073b7fc896cc77986f16e378210ded43186175bf646fc5f"
)

SYMBOL     = "BTC"
OUTPUT_DIR = "data"
OUTPUT_CSV = os.path.join(OUTPUT_DIR, "chainlink_updates_btc.csv")

# Pilot mode: stop automatically after this many hours (False = run forever)
PILOT_MODE           = True
PILOT_DURATION_HOURS = 6

# ============================================================
# STATE
# ============================================================

CSV_HEADER = (
    "recv_ns,block_number,block_timestamp_s,updated_at_s,"
    "price_usd,round_id,symbol\n"
)

_stop_event     = threading.Event()
_start_time     = time.time()
_ws_app         = None          # set in main()
_aggregator_addr: str | None = None  # resolved at startup

# HTTP endpoint derived from WSS URL (for eth_call + eth_getBlockByNumber)
_http_url: str = ""

# ============================================================
# AGGREGATOR RESOLUTION
# ============================================================

def _derive_http_url(wss_url: str) -> str:
    """Convert a wss:// Alchemy URL to its https:// counterpart."""
    return wss_url.replace("wss://", "https://").replace("ws://", "http://")


def _eth_call(to: str, data: str) -> str:
    """Call a read-only contract function via eth_call."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": to, "data": data}, "latest"],
    }
    resp = requests.post(_http_url, json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()["result"]


def _resolve_aggregator(proxy: str) -> str:
    """
    Call aggregator() on the Chainlink proxy contract to get the underlying
    aggregator address. We must subscribe to the aggregator, not the proxy,
    to receive AnswerUpdated events.

    aggregator() selector: keccak256("aggregator()")[0:4] = 0x245a7bfc
    """
    raw = _eth_call(proxy, "0x245a7bfc")
    # Returns ABI-encoded address (32 bytes, address in last 20 bytes)
    addr = "0x" + raw[-40:]
    return addr


# ============================================================
# BLOCK TIMESTAMP FETCH
# ============================================================

def _get_block_timestamp(block_hex: str) -> int | None:
    """Fetch the timestamp of a Polygon block (seconds since epoch)."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_getBlockByNumber",
        "params": [block_hex, False],
    }
    try:
        resp = requests.post(_http_url, json=payload, timeout=10)
        result = resp.json().get("result", {})
        ts_hex = result.get("timestamp", "0x0")
        return int(ts_hex, 16)
    except Exception as exc:
        print(f"[WARN] Could not fetch block timestamp for {block_hex}: {exc}")
        return None


# ============================================================
# EVENT DECODING
# ============================================================

def _decode_answer_updated(log: dict) -> dict | None:
    """
    Decode an AnswerUpdated log event.

    Event: AnswerUpdated(int256 indexed current, uint256 indexed roundId, uint256 updatedAt)
    topics[0] = event topic hash
    topics[1] = current  (int256, 32-byte ABI encoding)
    topics[2] = roundId  (uint256, 32-byte ABI encoding)
    data      = updatedAt (uint256, 32-byte ABI encoding, seconds)
    """
    try:
        topics = log["topics"]
        if len(topics) < 3:
            return None
        if topics[0].lower() != ANSWER_UPDATED_TOPIC:
            return None

        # current (int256): interpret as signed 256-bit integer
        raw_price = int(topics[1], 16)
        if raw_price >= (1 << 255):   # two's complement negative
            raw_price -= (1 << 256)
        price_usd = raw_price / 1e8

        round_id   = int(topics[2], 16)
        updated_at = int(log["data"], 16)  # unix seconds (oracle timestamp)
        block_hex  = log["blockNumber"]
        block_num  = int(block_hex, 16)

        return {
            "block_hex":  block_hex,
            "block_num":  block_num,
            "updated_at": updated_at,
            "price_usd":  price_usd,
            "round_id":   round_id,
        }
    except Exception as exc:
        print(f"[ERROR] Decode failed: {exc} | log={json.dumps(log)[:300]}")
        return None


# ============================================================
# FILE OUTPUT
# ============================================================

_csv_file = None

def _open_csv():
    global _csv_file
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    is_new = not os.path.exists(OUTPUT_CSV)
    _csv_file = open(OUTPUT_CSV, "a", buffering=1)
    if is_new:
        _csv_file.write(CSV_HEADER)
    print(f"[{datetime.now(timezone.utc).isoformat()}] Writing to: {OUTPUT_CSV}")


def _write_row(recv_ns, block_num, block_ts, updated_at, price_usd, round_id):
    if _csv_file is None:
        return
    row = f"{recv_ns},{block_num},{block_ts},{updated_at},{price_usd:.2f},{round_id},{SYMBOL}\n"
    _csv_file.write(row)
    _csv_file.flush()


# ============================================================
# WEBSOCKET HANDLERS
# ============================================================

_sub_id: str | None = None  # eth_subscription ID returned after subscribing


def _on_open(ws):
    now = datetime.now(timezone.utc).isoformat()
    print(f"[{now}] Connected to Alchemy (Polygon). Subscribing to logs...")

    sub_msg = json.dumps({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "eth_subscribe",
        "params": [
            "logs",
            {
                "address": _aggregator_addr,
                "topics": [ANSWER_UPDATED_TOPIC],
            },
        ],
    })
    ws.send(sub_msg)


def _on_message(ws, message):
    global _sub_id
    recv_ns = time.time_ns()

    try:
        msg = json.loads(message)

        # Subscription confirmation
        if msg.get("id") == 2 and "result" in msg:
            _sub_id = msg["result"]
            print(f"[SUB] Subscription confirmed: {_sub_id}")
            return

        # Live log event
        if msg.get("method") == "eth_subscription":
            log = msg["params"]["result"]
            decoded = _decode_answer_updated(log)
            if decoded is None:
                return

            # Fetch block timestamp (synchronous — ~100ms on Polygon RPC)
            block_ts = _get_block_timestamp(decoded["block_hex"])
            if block_ts is None:
                block_ts = 0  # log with 0 rather than drop

            _write_row(
                recv_ns,
                decoded["block_num"],
                block_ts,
                decoded["updated_at"],
                decoded["price_usd"],
                decoded["round_id"],
            )

            ts_str = datetime.fromtimestamp(decoded["updated_at"], timezone.utc).isoformat()
            print(
                f"  [ORACLE] block={decoded['block_num']} "
                f"ts={ts_str} "
                f"price=${decoded['price_usd']:,.2f} "
                f"round={decoded['round_id']}"
            )

    except Exception as exc:
        print(f"[ERROR] on_message: {exc} | raw={message[:200]}")


def _on_error(ws, error):
    print(f"[ERROR] WebSocket: {error}")


def _on_close(ws, code, msg):
    print(f"[{datetime.now(timezone.utc).isoformat()}] Closed: code={code} msg={msg}")


# ============================================================
# MAIN
# ============================================================

def main():
    global _http_url, _aggregator_addr, _ws_app

    if not ALCHEMY_WSS_URL:
        print(
            "ERROR: No Alchemy WSS URL configured.\n"
            "  Set env var: export ALCHEMY_WSS_URL='wss://polygon-mainnet.g.alchemy.com/v2/YOUR_KEY'\n"
            "  OR edit ALCHEMY_WSS_URL at the top of this file.\n"
            "  Get a free API key at https://alchemy.com (Polygon Mainnet app)."
        )
        return

    _http_url = _derive_http_url(ALCHEMY_WSS_URL)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    _open_csv()

    print(f"[SETUP] Resolving aggregator address from proxy {PROXY_ADDRESS}...")
    _aggregator_addr = _resolve_aggregator(PROXY_ADDRESS)
    print(f"[SETUP] Aggregator: {_aggregator_addr}")

    pilot_end = _start_time + PILOT_DURATION_HOURS * 3600 if PILOT_MODE else None
    if PILOT_MODE:
        print(f"[PILOT] Collecting for {PILOT_DURATION_HOURS}h then stopping.")

    reconnect_delay = 5
    while not _stop_event.is_set():
        if pilot_end and time.time() >= pilot_end:
            print(f"\n[PILOT] {PILOT_DURATION_HOURS}h complete. Stopping.")
            break

        ws = websocket.WebSocketApp(
            ALCHEMY_WSS_URL,
            on_open=_on_open,
            on_message=_on_message,
            on_error=_on_error,
            on_close=_on_close,
        )
        ws.run_forever(ping_interval=30, ping_timeout=10)

        if _stop_event.is_set():
            break
        if pilot_end and time.time() >= pilot_end:
            break

        print(f"Reconnecting in {reconnect_delay}s...")
        time.sleep(reconnect_delay)

    if _csv_file:
        _csv_file.close()
    _stop_event.set()
    print("Collector stopped.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Ctrl+C] Stopping...")
        _stop_event.set()
        if _csv_file:
            _csv_file.close()
