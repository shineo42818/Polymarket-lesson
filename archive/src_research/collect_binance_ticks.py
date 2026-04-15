"""
collect_binance_ticks.py — Binance aggTrade tick collector for lead-lag analysis.

Connects to Binance WebSocket aggTrade stream (microsecond timestamps) and records
tick-level price data. Used for Phase 1A of the Binance→Chainlink lead-lag
research (LEAD_LAG_RESEARCH_PLAN.md).

Output: data/binance_ticks_btc_{YYYYMMDD_HH}.csv  (rotated hourly)

Run:
    python src/arbitrage/collect_binance_ticks.py

Stop: Ctrl+C  (flushes buffer before exit)
"""

import json
import os
import time
import threading
from datetime import datetime, timezone

import websocket  # websocket-client (same library as gap_monitor.py)

# ============================================================
# CONFIGURATION
# ============================================================

SYMBOL = "BTCUSDT"           # Change to "ETHUSDT" to collect ETH ticks
STREAM = "btcusdt@aggTrade"  # Change to "ethusdt@aggTrade" for ETH

# timeUnit=MICROSECOND makes E (event time) and T (trade time) return microseconds
WS_URL = (
    "wss://data-stream.binance.vision/stream"
    f"?streams={STREAM}&timeUnit=MICROSECOND"
)

OUTPUT_DIR    = "data"
OUTPUT_PREFIX = "binance_ticks_btc"   # Change to "binance_ticks_eth" for ETH

# Write batch to CSV after this many rows…
BATCH_SIZE = 1000
# …or after this many seconds, whichever comes first
BATCH_TIMEOUT_S = 10.0

# Pilot mode: stop automatically after PILOT_DURATION_HOURS (set False for production)
PILOT_MODE           = True
PILOT_DURATION_HOURS = 6

# ============================================================
# STATE (module-level, shared between threads)
# ============================================================

CSV_HEADER = "recv_ns,trade_us,event_us,symbol,price,qty,is_sell\n"

_batch: list[str] = []
_batch_lock = threading.Lock()
_last_write_time = time.time()
_current_hour: str | None = None
_current_file = None
_stop_event = threading.Event()
_start_time = time.time()

# ============================================================
# FILE MANAGEMENT
# ============================================================

def _get_output_path(dt: datetime) -> str:
    hour_str = dt.strftime("%Y%m%d_%H")
    return os.path.join(OUTPUT_DIR, f"{OUTPUT_PREFIX}_{hour_str}.csv")


def _ensure_file(dt: datetime):
    """Open (or continue) the CSV for the current UTC hour."""
    global _current_hour, _current_file
    hour = dt.strftime("%Y%m%d_%H")
    if hour == _current_hour and _current_file is not None:
        return
    if _current_file is not None:
        _current_file.close()
    path = _get_output_path(dt)
    is_new = not os.path.exists(path)
    _current_file = open(path, "a", buffering=1)  # line-buffered
    if is_new:
        _current_file.write(CSV_HEADER)
    _current_hour = hour
    print(f"[{dt.isoformat()}] Writing to: {path}")


def _flush_batch():
    """Write accumulated rows to the current CSV file."""
    global _batch, _last_write_time
    with _batch_lock:
        if not _batch:
            return
        rows = _batch
        _batch = []
    now_utc = datetime.now(timezone.utc)
    _ensure_file(now_utc)
    _current_file.writelines(rows)
    _current_file.flush()
    _last_write_time = time.time()


def _batch_writer_loop():
    """Background thread: flush on timeout even when BATCH_SIZE not reached."""
    while not _stop_event.is_set():
        time.sleep(1.0)
        if time.time() - _last_write_time >= BATCH_TIMEOUT_S:
            _flush_batch()


# ============================================================
# WEBSOCKET HANDLERS
# ============================================================

def _on_open(ws):
    print(f"[{datetime.now(timezone.utc).isoformat()}] Connected: {STREAM}")


def _on_message(ws, message):
    recv_ns = time.time_ns()  # local receive time — most precise timestamp we have

    try:
        outer = json.loads(message)
        # Combined-stream wrapper: {"stream": "...", "data": {...}}
        data = outer.get("data", outer)

        if data.get("e") != "aggTrade":
            return

        # T = trade/matching-engine time (μs); E = event publish time (μs)
        trade_us = data["T"]
        event_us = data["E"]
        symbol   = data["s"]
        price    = data["p"]   # keep as string (no float rounding)
        qty      = data["q"]
        # m=True → buyer is market-maker → trade was seller-aggressed
        is_sell  = "1" if data["m"] else "0"

        row = f"{recv_ns},{trade_us},{event_us},{symbol},{price},{qty},{is_sell}\n"

        with _batch_lock:
            _batch.append(row)
            do_flush = len(_batch) >= BATCH_SIZE

        if do_flush:
            _flush_batch()

    except Exception as exc:
        print(f"[ERROR] on_message: {exc} | raw={message[:200]}")


def _on_error(ws, error):
    print(f"[ERROR] WebSocket: {error}")


def _on_close(ws, code, msg):
    print(f"[{datetime.now(timezone.utc).isoformat()}] Closed: code={code} msg={msg}")


# ============================================================
# NTP SANITY CHECK
# ============================================================

def _check_ntp():
    """Compare local clock to worldtimeapi.org. Warns if offset > 500 ms."""
    try:
        import urllib.request
        resp = urllib.request.urlopen(
            "https://worldtimeapi.org/api/timezone/UTC", timeout=5
        )
        server_ts = float(json.loads(resp.read().decode())["unixtime"])
        offset_ms = (time.time() - server_ts) * 1000
        print(f"[NTP] Local clock offset: {offset_ms:+.1f} ms")
        if abs(offset_ms) > 500:
            print("[NTP WARNING] Offset > 500 ms — lag measurements will be unreliable!")
    except Exception as exc:
        print(f"[NTP] Clock check failed: {exc}")


# ============================================================
# MAIN
# ============================================================

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    _check_ntp()

    pilot_end = _start_time + PILOT_DURATION_HOURS * 3600 if PILOT_MODE else None
    if PILOT_MODE:
        print(f"[PILOT] Collecting for {PILOT_DURATION_HOURS}h then stopping.")

    writer = threading.Thread(target=_batch_writer_loop, daemon=True)
    writer.start()

    reconnect_delay = 5
    while not _stop_event.is_set():
        if pilot_end and time.time() >= pilot_end:
            print(f"\n[PILOT] {PILOT_DURATION_HOURS}h complete. Stopping.")
            break

        ws = websocket.WebSocketApp(
            WS_URL,
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

    _flush_batch()
    if _current_file:
        _current_file.close()
    _stop_event.set()
    print("Collector stopped.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Ctrl+C] Stopping...")
        _stop_event.set()
        _flush_batch()
        if _current_file:
            _current_file.close()
