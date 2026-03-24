# Polymarket Arb Bot — Operations Guide

## Dashboard

http://34.86.131.153:8000

## VM Details

| Item | Value |
|---|---|
| Project | polymarket1234 |
| VM Name | polymarket-bot |
| Zone | us-east4-a |
| Machine | e2-small (2 vCPU, 2 GB RAM) |
| External IP | 34.86.131.153 |
| Cost | ~$14/month ($300 credit = ~21 months) |

## SSH into VM

```bash
gcloud compute ssh polymarket-bot --zone=us-east4-a
```

Or use browser SSH: https://console.cloud.google.com/compute/instances?project=polymarket1234

## Bot Service Commands

```bash
# Check if bot is running
sudo systemctl status bot

# View live logs (Ctrl+C to stop watching)
sudo journalctl -u bot -f

# View last 50 log lines
sudo journalctl -u bot --no-pager -n 50

# Stop bot
sudo systemctl stop bot

# Start bot
sudo systemctl start bot

# Restart bot
sudo systemctl restart bot
```

## Updating Code

From your local machine (PowerShell or Git Bash):

```bash
cd "C:\Users\Supakorn.Co\Documents\Polymarket lesson"

# Create tar archive
tar -czf /tmp/polymarket-code.tar.gz src/ deploy/ requirements.txt

# Upload (from Git Bash)
gcloud compute scp --zone=us-east4-a /tmp/polymarket-code.tar.gz polymarket-bot:~/

# Or upload via browser SSH: gear icon -> Upload file
# File location: C:\Users\Supakorn.Co\AppData\Local\Temp\polymarket-code.tar.gz
```

Then on the VM:

```bash
cd ~/polymarket
tar -xzf ~/polymarket-code.tar.gz
sudo systemctl restart bot
```

## File Locations on VM

```
/home/tangmo82/polymarket/
  src/bot/           # Bot source code
  deploy/            # Service files
  data/bot.db        # SQLite database (trades, portfolio)
  requirements.txt   # Python dependencies
  .env               # Environment config (BOT_MODE=PAPER)
  venv/              # Python virtual environment
```

## Strategy: Hybrid Maker-Then-Taker

1. Detect gap_bid >= 0.05 (YES bid + NO bid < $0.95)
2. Post BOTH legs as MAKER limit orders at bid price (0% fee)
3. When first leg fills:
   - Check current ask on other side
   - If hybrid profit > $0.005: TAKER the other side immediately
   - If not profitable: wait for second maker fill or expire
4. If BOTH fill as maker: best case, full maker profit

## Paper Trading Config

| Parameter | Value |
|---|---|
| Starting Capital | $100 |
| Trade Size | 10% of balance per trade |
| Max Open Positions | 3 |
| Min Gap Bid | 0.05 |
| Min USDC Reserve | $20 |
| Max Daily Loss (kill switch) | $15 |
| Maker Fill Probability (sim) | 50% |
| Maker Fill Delay (sim) | 2-8 seconds |

## Next Steps

1. Paper trade for 7+ days
2. Monitor P&L trend on dashboard
3. If net positive: Phase 4 = live trading with py-clob-client
