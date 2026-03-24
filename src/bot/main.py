"""
FastAPI server -- serves dashboard + SSE stream + REST API.

Run with:
  uvicorn src.bot.main:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import json
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .engine import TradingEngine
from .order_manager import OrderManager
from .paper_executor import PaperExecutor

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bot.main")

# ── App ──
app = FastAPI(title="Polymarket Arb Bot", version="1.0.0")

# ── Initialize components ──
executor = PaperExecutor()
order_manager = OrderManager(executor)
engine = TradingEngine(order_manager)


# ── Startup / Shutdown ──

@app.on_event("startup")
async def startup():
    db.init_db()
    log.info("Database initialized at %s", config.DB_PATH)
    log.info("Bot mode: %s", config.MODE)
    log.info("Starting capital: $%.2f", config.STARTING_CAPITAL)


@app.on_event("shutdown")
async def shutdown():
    if engine.state.value == "RUNNING":
        await engine.stop()
    log.info("Shutdown complete")


# ── Dashboard ──

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the dashboard HTML."""
    html_path = os.path.join(os.path.dirname(__file__), "static", "dashboard.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()


# ── SSE Stream ──

@app.get("/stream")
async def sse_stream(request: Request):
    """Server-Sent Events stream for real-time dashboard updates."""
    queue = engine.add_sse_queue()

    async def event_generator():
        try:
            # Send initial state
            yield _sse_format("status", engine.get_status())
            yield _sse_format("portfolio", order_manager.get_portfolio_state())

            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield _sse_format(msg["event"], msg["data"])
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield ": keepalive\n\n"
        finally:
            engine.remove_sse_queue(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_format(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# ── REST API ──

@app.post("/api/start")
async def api_start():
    """Start the trading engine."""
    await engine.start()
    return {"status": "started", "mode": config.MODE}


@app.post("/api/stop")
async def api_stop():
    """Stop the trading engine (orders remain open on exchange)."""
    await engine.stop()
    return {"status": "stopped"}


@app.post("/api/kill")
async def api_kill():
    """Emergency stop: cancel all orders and stop."""
    await engine.kill()
    return {"status": "killed"}


@app.get("/api/status")
async def api_status():
    return engine.get_status()


@app.get("/api/portfolio")
async def api_portfolio():
    return order_manager.get_portfolio_state()


@app.get("/api/trades")
async def api_trades(limit: int = 50, offset: int = 0):
    return db.get_trades(limit=limit, offset=offset)


@app.get("/api/pnl-history")
async def api_pnl_history(limit: int = 500):
    return db.get_pnl_history(limit=limit)


@app.get("/api/config")
async def api_get_config():
    return {
        "mode": config.MODE,
        "min_gap_bid": config.MIN_GAP_BID,
        "max_trade_pct": config.MAX_TRADE_PCT,
        "max_open_positions": config.MAX_OPEN_POSITIONS,
        "min_usdc_reserve": config.MIN_USDC_RESERVE,
        "max_daily_loss": config.MAX_DAILY_LOSS,
        "min_seconds_left": config.MIN_SECONDS_LEFT,
        "starting_capital": config.STARTING_CAPITAL,
        "coins": config.COINS,
        "market_types": config.MARKET_TYPES,
    }


@app.post("/api/config")
async def api_update_config(request: Request):
    """Update bot config values at runtime."""
    body = await request.json()
    updatable = {
        "min_gap_bid": "MIN_GAP_BID",
        "max_trade_pct": "MAX_TRADE_PCT",
        "max_open_positions": "MAX_OPEN_POSITIONS",
    }
    updated = {}
    for key, attr in updatable.items():
        if key in body:
            setattr(config, attr, float(body[key]))
            db.set_config_value(key, str(body[key]))
            updated[key] = body[key]
    return {"updated": updated}


@app.get("/api/health")
async def api_health():
    return {"status": "ok", "engine": engine.state.value}
