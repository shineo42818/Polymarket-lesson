# Archive

The `archive/` directory contains all research-phase files from the initial Polymarket arbitrage investigation (pre-bot). These are kept for reference but are not part of the active codebase.

## Contents

```
archive/
├── data/                    # Historical CSVs, JSONs, logs
│   ├── gap_log.csv / gap_log_old.csv
│   ├── whale_log.csv / whale_log_Old.csv
│   ├── binance_ticks_btc_*.csv, binance_btc/eth/sol.csv
│   ├── chainlink_updates_btc.csv
│   ├── polymarket_btc/eth/sol.csv, polymarket_*_15m.csv
│   ├── signal_analysis_*.csv
│   ├── markets.csv, leaderboard.csv, known_whales.csv
│   └── event_holders_*.csv / *.png
├── src_research/            # src/arbitrage/ — gap/whale monitors, analyzers, collectors
│   ├── gap_monitor.py, whale_monitor.py, fullrun.py
│   ├── analyze_gap_log.py, analyze_whale_patterns.py, analyze_signal.py
│   ├── analyze_event_holders.py, backtest_arb.py, profit_calculator.py
│   ├── collect_binance.py, collect_binance_ticks.py
│   ├── collect_polymarket.py, collect_chainlink.py
├── src_scripts/             # One-off API exploration scripts
│   ├── check_api.py, explore_markets.py, get_markets.py
│   ├── get_trades.py, plot_markets.py, test.py
├── docs/                    # Old documentation
│   ├── ARBITRAGE_RESEARCH.md
│   ├── IMPLEMENTATION_PLAN.md
│   ├── Bot_Trading_Readme.md
│   ├── LEAD_LAG_RESEARCH_PLAN.md
│   └── PLAN.md
├── charts/                  # Signal analysis PNGs
│   ├── signal_analysis_btc.png / signal_analysis_btc_15m.png
│   └── top_markets.png
├── deploy/                  # Old GCP deploy scripts
│   ├── Caddyfile, bot.service, setup_gcp.sh
├── bot_db_archives/         # bot.db snapshots
└── mock_trader.html         # HTML mock trading UI
```

## Why Archived

These files were used during the research phase to:
- Collect Binance, Polymarket, and Chainlink price data
- Monitor gap and whale activity
- Backtest arbitrage signals
- Explore the Polymarket CLOB and Gamma APIs

The active bots (`src/bot/` and `src/sniper/`) supersede all of this work.
