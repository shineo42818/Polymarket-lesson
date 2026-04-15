


RESEARCH INSTRUCTION: Build a Polymarket Automated Trading Bot Engine
Context & Source
This document is based on a detailed breakdown by @LunarResearcher (posted March 23, 2026) who built a fully automated Polymarket trading bot in ~2 weeks using ~32 tools, running at ~$5-8/month. The bot scans 50+ markets per cycle, estimates probabilities using an LLM, calculates position sizes with mathematical models, executes trades on-chain, and monitors everything via Telegram.

SYSTEM ARCHITECTURE OVERVIEW
The bot follows a linear pipeline:
DATA → AI BRAIN → MATH ENGINE → EXECUTION → MONITORING
Each layer is independent but feeds into the next. The entire system is async Python running on a cheap Linux VPS.

LAYER 1: DATA & MARKET ACCESS
What: Pull real-time market data from Polymarket's on-chain prediction market.
Why: The bot needs live prices, orderbook depth, and market metadata to identify opportunities. Without fast, reliable data ingestion, nothing downstream works.
Components to implement:

Polymarket CLOB API — Central Limit Order Book API providing real-time prices, orderbook depth, market metadata. Supports both REST and WebSocket. Free tier. Docs: https://docs.polymarket.com/trading/overview
py-clob-client — Official Python SDK for the CLOB API. Handles authentication, cryptographic signing, and order placement. This saves weeks versus writing raw API calls. Repo: https://github.com/Polymarket/py-clob-client
Polygon RPC via Alchemy — Polymarket settles on the Polygon blockchain. You need an RPC endpoint for checking wallet balances, approving tokens, and interacting with smart contracts. Alchemy's free tier is sufficient. URL: https://www.alchemy.com/polygon
USDC.e on Polygon — The settlement currency. All trades are denominated in USDC.e. Contract: 0x2791bca1f2de4661ed88a30c99a7a9449aa84174. Bridge from Ethereum or buy directly on Polygon.
python-dotenv — Store API keys, private keys, and config outside of code. Critical security measure — one leaked key means lost funds. Package: https://pypi.org/project/python-dotenv/

Research tasks:

Study the Polymarket CLOB API docs thoroughly — understand how markets, conditions, and tokens are structured
Understand the difference between REST polling vs WebSocket streaming for price updates
Learn how py-clob-client handles order signing (it uses EIP-712 typed signatures)
Set up an Alchemy account and understand Polygon RPC basics
Understand how USDC.e token approvals work on Polygon


LAYER 2: AI BRAIN (Probability Estimation)
What: Use Claude (or another LLM) to sharpen the trading strategy and translate into an action item.
Why: Most bots use hardcoded rules or simple heuristics. Using an LLM allows the bot to reason about novel questions, consider base rates, recent news, and historical patterns.
Components to implement:
Claude API (Anthropic) — The core reasoning engine. Use claude-sonnet for the best balance of speed and cost. The bot sends each market question to Claude and receives a probability estimate. Docs: https://docs.anthropic.com/en/api/getting-started
Structured JSON Prompts — Force Claude to return structured output: {"probability": 0.XX, "confidence": "high/medium/low", "reasoning": "..."}. This eliminates parsing errors and makes downstream processing deterministic.
httpx — Async HTTP client for Python. Superior to requests because it supports async operations and connection pooling. When calling Claude API 50+ times per scan cycle, this matters. Docs: https://www.python-httpx.org
Prompt Versioning System — Keep every prompt iteration in a /prompts folder with dates. The author went through 7 versions. Track what changed and measure accuracy improvements.

Research tasks:
Study Claude API structured output / tool-use features for reliable JSON responses
Design a prompt that forces the model to: (a) consider the base rate for the type of event, (b) penalize extreme confidence, (c) consider recent news/context, (d) return structured JSON
Build a prompt evaluation framework — test prompts against markets with known outcomes
Understand token costs: calculate cost per market analysis at ~50 markets/cycle
Research how to pass relevant context (news, historical data) into each prompt without exceeding token limits
Critical lesson from the author: Don't trust the first Claude response. Early prompts returned overconfident probabilities. The fix was forcing explicit base-rate consideration and penalizing extreme confidence.

LAYER 3: MATH ENGINE (Decision Logic)
What: Four mathematical formulas that take the AI's probability estimate and decide: (a) whether to trade, (b) how much to bet, and (c) how to update beliefs when new information arrives.
Why: AI gives you probabilities, but math tells you what to do with them. Without rigorous position sizing and edge filtering, even accurate predictions will lose money through over-betting or taking low-edge trades.
Formulas to implement:

Expected Value (EV) Filter:

    EV = P(win) × Profit - P(lose) × Loss
If EV < 5% edge → skip. No exceptions. This single filter eliminates ~90% of bad trades. The "edge" is the difference between your estimated probability and the market-implied probability.
11. Kelly Criterion (Position Sizing):
    f* = (p × b - q) / b
Where p = probability of winning, q = 1-p, b = odds (payout ratio). This tells you the optimal fraction of bankroll to bet. **USE QUARTER KELLY (0.25×)**, not half or full Kelly. The author learned that half Kelly creates too much variance and causes panic-closing of winning positions.
12. Bayesian Updating:
    P(H|E) = P(E|H) × P(H) / P(E)
When news drops mid-trade, update the probability estimate rather than holding a stale opinion. The bot should re-evaluate open positions when significant new information appears.
13. Log Returns (P&L Calculation):
    log_return = ln(P1 / P0)
Arithmetic returns don't sum correctly across multiple periods. All P&L calculations should use log returns.
14. NumPy — For all numerical computations: log calculations, array operations, statistical functions. URL: https://numpy.org
Research tasks:

Implement EV calculation: understand how to derive market-implied probability from Polymarket prices
Study Kelly Criterion deeply — understand why quarter Kelly is preferred for volatile prediction markets
Implement Bayesian updating: define what constitutes "new evidence" and how to trigger re-evaluation
Build a backtesting framework to validate the math engine against historical Polymarket data
Understand how log returns differ from arithmetic returns and why they matter for compounding


LAYER 4: EXECUTION (On-Chain Trading)
What: Actually place orders on Polymarket's CLOB and manage positions.
Why: Knowing what to trade is half the job. Executing without losing money to slippage, failed orders, or bugs is the other half.
Components to implement:

GTC Orders (Good Till Cancelled) — Use GTC, NOT FOK (Fill or Kill). FOK orders fail constantly on low-liquidity markets. GTC orders sit in the orderbook and wait. The author's fill rate went from 60%→95% after switching.
Balance Pre-Check — Before every trade, check on-chain balance via RPC. Prevents "not enough balance" errors. The author lost 4 trades before implementing this.
Position Tracker (SQLite) — Local database tracking every open position: entry price, size, market ID, timestamp. Avoids querying the chain every time. URL: https://sqlite.org
Slippage Protection — Max 2% slippage on any order. If the orderbook is too thin, skip the trade. Better to miss a trade than get filled at a terrible price.
web3.py — For direct blockchain interactions: token approvals, balance checks, contract calls. py-clob-client handles most trading, but some operations need raw web3. Docs: https://web3py.readthedocs.io

Research tasks:

Study py-clob-client order placement: understand GTC vs FOK vs IOC order types
Design the SQLite schema: positions table, trades table, market metadata table
Implement a pre-trade checklist: balance check → slippage check → EV check → size calculation → order placement
Build retry logic for failed orders (network issues, RPC errors)
Understand token approval flows on Polygon (approve USDC.e spending for the CLOB contract)
Implement a "paper trading" / simulation mode that logs what the bot WOULD do without placing real orders

Critical lesson: Start with paper trading. The author lost $12 on day one due to a double-ordering bug.

LAYER 5: MONITORING & ALERTS
What: Real-time notifications, position dashboard, and comprehensive logging.
Why: A bot that runs without monitoring loses money silently. The author's first version had no alerts and woke up to find the bot stuck on one market for 6 hours due to an unexpected API response.
Components to implement:

Telegram Bot (aiogram) — Real-time notifications for every event: market scanned, edge found, order filled, error encountered. The author manages the entire bot from their phone. Repo: https://github.com/aiogram/aiogram
Position Dashboard via Telegram — Telegram command that shows all open positions with P&L, current prices, and a "close" button for each. No laptop needed.
Python logging module — Every decision logged with timestamps. Use rotating file handlers to prevent disk space issues. Docs: https://docs.python.org/3/library/logging.html
Error Recovery — The bot never crashes on errors. It catches exceptions, logs them, sends a Telegram alert, and continues scanning. Uptime > perfection.

Research tasks:

Set up a Telegram bot via @BotFather and understand the aiogram async framework
Design notification categories: INFO (scans), TRADE (executions), ALERT (errors), CRITICAL (system failures)
Build inline keyboard buttons in Telegram for position management (view, close)
Implement structured logging with log levels and rotating handlers
Design the error recovery strategy: which errors are retryable vs fatal?


LAYER 6: INFRASTRUCTURE
What: The runtime environment and deployment.

Python 3.11+ — The entire bot is Python. Fast enough for this use case, massive ecosystem.
asyncio — Fully async architecture. Scan markets, call Claude, check balances — all concurrently. A synchronous version would be ~10× slower per cycle. Docs: https://docs.python.org/3/library/asyncio.html
VPS ($5/month) — 1 vCPU, 1GB RAM. The bot uses ~100MB of memory. Any cheap Linux box works (DigitalOcean, Hetzner, Vultr).
systemd — Auto-restart on crash, auto-start on boot. Keeps the bot running for weeks without manual intervention.
Git — Version control for every change. Easy rollback when a new feature breaks something.

Research tasks:

Design the async architecture: main event loop, scan cycle, concurrent API calls
Write a systemd service file for the bot
Plan the deployment workflow: local dev → git push → VPS pull → systemd restart
Understand Python asyncio patterns: gather, TaskGroup, semaphores for rate limiting


LAYER 7: RESEARCH & DEVELOPMENT
What: Tools used to understand the market and reverse-engineer successful strategies.

Polymarket Leaderboard — Study top wallets: what markets they trade, frequency, sizes. URL: https://polymarket.com/leaderboard
Polygonscan — Trace top trader transactions on-chain to understand their patterns. URL: https://polygonscan.com
Claude (claude.ai) — Use Claude not just in the bot, but to help build the bot. Debugging, optimizing prompts, researching mechanics.
Perplexity — Real-time research during development for quick, sourced answers. URL: https://www.perplexity.ai


SUGGESTED BUILD ORDER

Week 1, Days 1-2: Set up project structure, Python environment, dotenv config. Get Polymarket CLOB API returning live market data using py-clob-client. Set up Alchemy RPC.
Week 1, Days 3-4: Build the AI brain. Get Claude API returning structured probability estimates. Iterate on prompts. Build prompt versioning system.
Week 1, Days 5-7: Build the math engine (EV filter, Kelly sizing). Integrate with AI output. Build paper trading / simulation mode that logs hypothetical trades.
Week 2, Days 1-2: Build execution layer. SQLite position tracker. Balance pre-checks. GTC order placement. Slippage protection.
Week 2, Days 3-4: Build monitoring. Telegram bot with notifications and position dashboard. Logging. Error recovery.
Week 2, Days 5-7: Deploy to VPS. Set up systemd. Run in paper trading mode. Fix bugs. Gradually transition to live trading with tiny sizes.


KEY METRICS TO TARGET

Markets scanned per cycle: 50+
Average trades per day: 2-4
EV threshold: >5% edge required
Kelly fraction: Quarter (0.25×)
Max slippage: 2%
Monthly cost: ~$5 VPS + ~$3 Claude API = ~$8 total


CRITICAL LESSONS (from the author's experience)

Paper trade first — Simulate before risking real money. A double-ordering bug cost $12 on day one.
Quarter Kelly, not Half Kelly — Variance in prediction markets is brutal. Quarter Kelly lets you sleep.
Don't trust the first LLM response — Force base-rate consideration and penalize extreme confidence in prompts.
GTC orders over FOK — Fill rate goes from 60%→95%.
Monitor everything — The bot got stuck for 6 hours on one market because of an unexpected API format. Telegram alerts catch this.
Prompt iteration is everything — The difference between 40% and 75% accuracy was 6 prompt rewrites.