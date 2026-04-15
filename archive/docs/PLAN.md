# Coding Lesson Plan: Whale Tracking on Polymarket with Cursor

**Goal:** Learn vibe coding in Cursor to build a Polymarket whale-tracking tool that identifies profitable signals from blockchain data on Polygon and Ethereum.

**Your Background:** JavaScript (undergrad, 15 years ago), Python installed (3.14.3), Cursor installed on Windows, a few AI-coding attempts already.

---

## Module 1 — Cursor Orientation (30 min)

You've already opened Cursor and tried a few things. This module fills in the gaps so you use it efficiently.

### 1.1 What is Cursor?

Cursor is a code editor built on top of VS Code, with AI deeply integrated. It's not just autocomplete — it can write entire files, debug errors, and refactor your code based on plain English instructions.

### 1.2 Cursor vs. Other Tools

| Tool | What It Is | Strengths | Weaknesses |
|------|-----------|-----------|------------|
| **Cursor** | VS Code fork + built-in AI | Best vibe coding workflow, multi-file edits, strong context awareness | Paid after trial |
| **VS Code** | Industry-standard code editor | Massive extension ecosystem, free | AI is add-on only (Copilot) |
| **Windsurf** | AI-first code editor | Similar to Cursor, good for beginners | Smaller community |
| **Replit** | Browser-based coding | No setup needed, runs in browser | Less control, slower for big projects |
| **GitHub Copilot** | AI plugin for VS Code | Great autocomplete | No chat/composer, less "vibe coding" |

**Why Cursor for this project?** You want to move fast without being an expert. Cursor's Composer mode lets you describe what you want and get working code across multiple files — perfect for data analysis projects.

### 1.3 The Three AI Features You'll Use

| Shortcut (Windows) | Feature | When to Use It |
|---------------------|---------|---------------|
| `Ctrl+L` | **AI Chat** | Ask questions, get explanations, debug errors |
| `Ctrl+I` | **Composer / Agent** | Generate or edit code across multiple files |
| `Ctrl+K` | **Inline Edit** | Highlight code → tell AI to change just that part |

**Pro tip:** The AI is only as good as the **context** you give it. If you have relevant files open in Cursor tabs, the AI can see them. If your project has a `PLAN.md`, the AI can reference it. Always think: "does the AI have enough context to help me?"

### 1.4 Cursor Terminal

- Open/close terminal: `` Ctrl+` `` (backtick key, top-left of keyboard)
- This is where you run Python scripts, install packages, and use Git
- You'll use this constantly

---

## Module 2 — Project Setup (20 min)

### 2.1 Create Your Project Folder

1. Open Cursor
2. Go to **File → Open Folder**
3. Navigate to where you want your project (e.g., `Documents`)
4. Create a new folder called `polymarket-analysis`
5. Select that folder and click **Open**

### 2.2 Why Python (Not JavaScript)?

Your JavaScript background will make Python feel familiar — variables, functions, loops, if/else all work similarly. But Python has a much stronger ecosystem for:
- **Data analysis** (pandas — think of it as Excel on steroids)
- **Blockchain interaction** (web3.py — talks directly to Ethereum/Polygon)
- **Visualization** (matplotlib — creates charts and graphs)
- **Quick scripting** (less boilerplate than JavaScript)

Syntax comparison you'll find comforting:

```
JavaScript:                     Python:
function add(a, b) {            def add(a, b):
    return a + b;                   return a + b
}
```

No curly braces, no semicolons — Python uses indentation instead.

### 2.3 Set Up Virtual Environment

A virtual environment is a "sandbox" for your project's packages. It keeps this project's libraries separate from other Python projects on your machine.

Open the terminal (`` Ctrl+` ``) and run these commands one at a time:

```bash
python -m venv venv
venv\Scripts\activate
```

You should see `(venv)` appear at the start of your terminal prompt. This means the virtual environment is active.

**Every time you open Cursor for this project**, you need to activate it again with `venv\Scripts\activate`.

### 2.4 Install Libraries

With the virtual environment active, run:

```bash
python -m pip install requests pandas matplotlib web3 python-dotenv
```

What each library does:
- **requests** — makes HTTP calls (to pull data from Polymarket API)
- **pandas** — data tables and analysis (your main analysis tool)
- **matplotlib** — creates charts and graphs
- **web3** — talks to Ethereum and Polygon blockchains
- **python-dotenv** — reads your secret API keys from a `.env` file

### 2.5 Sanity Check

Create a file called `test_setup.py` in your project folder (right-click in Cursor sidebar → New File) and paste:

```python
import requests
import pandas as pd
import matplotlib
from web3 import Web3

print("All libraries loaded successfully!")
print(f"pandas version: {pd.__version__}")
print(f"web3 version: {Web3.__version__}")
```

Run it in the terminal:

```bash
python test_setup.py
```

If you see "All libraries loaded successfully!" — you're ready. Delete this file afterward.

---

## Module 3 — The Vibe Coding Blueprint: PLAN.md (30 min)

This is the **most important module**. A good plan is the difference between productive vibe coding and going in circles.

### 3.1 What is Vibe Coding?

Vibe coding means describing what you want in plain English and letting the AI write the code. Your job shifts from "writing code" to:
1. **Describing clearly** what you want
2. **Reviewing** what the AI generates
3. **Running** the code to see if it works
4. **Iterating** — telling the AI what to fix

### 3.2 Why Write a PLAN.md First?

Without a plan, vibe coding conversations drift. You ask for one thing, then another, and end up with messy, disconnected code. A `PLAN.md` file:
- Gives the AI context about your whole project
- Breaks the work into small, achievable steps
- Lets you feed specific sections to Cursor's AI as prompts
- Acts as your personal roadmap

### 3.3 Your Project PLAN.md

Create a file called `PLAN.md` in your project root. Here is a starting version — we'll refine it together:

```markdown
# Polymarket Whale Tracker — Project Plan

## Goal
Find profitable trading signals by tracking whale wallets on Polymarket.
Whales = wallets that place large bets. If we can see what they're betting on
before markets move, we may find an edge.

## Blockchains
- Polygon (where Polymarket transactions happen)
- Ethereum (for cross-chain whale identification)

## Data Sources
1. **Polymarket API** — market data, odds, event info
   - Docs: https://docs.polymarket.com/
2. **Polygon RPC** — on-chain transaction data
   - Free RPC: https://polygon-rpc.com
3. **Polygonscan API** — wallet and transaction lookups
   - Get free API key at: https://polygonscan.com/apis
4. **Dune Analytics** — pre-built blockchain queries (optional, for later)

## Analysis Steps (in order)

### Phase 1: Get Market Data
- [ ] Pull active Polymarket markets (name, odds, volume)
- [ ] Store in a pandas DataFrame
- [ ] Identify high-volume markets (these attract whales)

### Phase 2: Identify Whale Wallets
- [ ] Find large transactions on Polymarket contracts via Polygonscan
- [ ] Define "whale" threshold (e.g., bets > $10,000)
- [ ] Build a list of whale wallet addresses

### Phase 3: Track Whale Behavior
- [ ] For each whale wallet, pull their betting history
- [ ] Analyze: what markets do they bet on? How early? Win rate?
- [ ] Visualize whale activity over time

### Phase 4: Signal Detection
- [ ] Compare whale entry timing vs. odds movement
- [ ] Identify patterns: do odds shift AFTER whales enter?
- [ ] Build a simple alert: "Whale X just bet $Y on Market Z"

### Phase 5: Dashboard (optional stretch goal)
- [ ] Simple web page showing live whale activity
- [ ] Refreshes automatically

## Output Files
- `data/` — raw and processed data (CSV files)
- `charts/` — saved visualizations
- `notebooks/` — Jupyter notebooks for exploration (optional)
- `src/` — main Python scripts

## API Keys Needed (store in .env file)
- POLYGONSCAN_API_KEY
- (Optional) DUNE_API_KEY
- (Optional) ALCHEMY_API_KEY for premium RPC access
```

### 3.4 How to Use PLAN.md While Vibe Coding

When you're ready to code a specific phase, open `PLAN.md` in a tab, then open Cursor's Composer (`Ctrl+I`) and write a prompt like:

> "I'm working on Phase 1 of my PLAN.md. Please create a Python script called `src/get_markets.py` that pulls all active Polymarket markets from their API and saves the results as a pandas DataFrame. Include market name, current odds, and total volume."

Because `PLAN.md` is open, Cursor's AI can see the full context of your project.

---

## Module 4 — Your First Vibe Coding Session (45 min)

### 4.1 Folder Structure

First, create these folders in your project:

```
polymarket-analysis/
├── PLAN.md
├── .env              ← your API keys (never share this file)
├── .gitignore        ← tells Git to ignore .env and other files
├── src/              ← your Python scripts
├── data/             ← saved data files
└── charts/           ← saved charts
```

### 4.2 Create .env File

Create a file called `.env` in your project root:

```
POLYGONSCAN_API_KEY=your_key_here
```

(You'll get the actual key from https://polygonscan.com/apis — it's free, just requires signup)

### 4.3 Create .gitignore File

Create a file called `.gitignore` in your project root:

```
.env
venv/
__pycache__/
data/*.csv
*.pyc
```

This tells Git to never track your secrets or temporary files.

### 4.4 First Script: Pull Polymarket Markets

This is where you use Cursor's AI for the first time on real project code.

1. Open `PLAN.md` in a tab (for context)
2. Press `Ctrl+I` (Composer)
3. Type this prompt:

> "Create src/get_markets.py — a Python script that:
> 1. Calls the Polymarket API to get all active markets
> 2. Extracts market name, current price/odds, total volume, and market URL
> 3. Puts results in a pandas DataFrame
> 4. Prints the top 10 markets by volume
> 5. Saves the full data to data/markets.csv
> Use the requests library. Add error handling and comments explaining each step."

4. Review what the AI generates
5. Run it: `python src/get_markets.py`
6. If it fails, copy the error message back to the AI chat (`Ctrl+L`) and ask it to fix it

### 4.5 Second Script: Visualize Market Data

Once you have market data, use the same workflow to create a chart:

> "Create src/plot_markets.py — read data/markets.csv and create a bar chart showing the top 20 markets by trading volume. Save the chart to charts/top_markets.png. Use matplotlib, make it look professional with a title and labels."

---

## Module 5 — Saving Your Work with Git & GitHub (20 min)

### 5.1 What is Git?

Git is a version control system — it takes "snapshots" of your project at different points in time. If you break something, you can go back. Think of it like "undo" but for your entire project.

### 5.2 What is GitHub?

GitHub is a website that stores your Git snapshots online. It's your backup — if your laptop dies, your code is safe on GitHub. It's also how developers share and collaborate on code.

### 5.3 One-Time Setup

In Cursor's terminal:

```bash
git init
git add .
git commit -m "Initial project setup with PLAN.md"
```

That's it — your first snapshot is saved locally.

### 5.4 Push to GitHub

1. Go to https://github.com and create a free account (if you don't have one)
2. Click **New Repository** → name it `polymarket-analysis` → click **Create**
3. GitHub will show you commands. Run these in your Cursor terminal:

```bash
git remote add origin https://github.com/YOUR_USERNAME/polymarket-analysis.git
git branch -M main
git push -u origin main
```

### 5.5 The 3-Command Save Habit

After every working session or milestone, run:

```bash
git add .
git commit -m "Short description of what you did"
git push
```

Example commit messages:
- `"Added market data pulling script"`
- `"Fixed API error, whale threshold set to 10k"`
- `"Added volume chart for top markets"`

---

## Module 6 — Going Deeper: On-Chain Analysis (Future Sessions)

Once Modules 1–5 are complete, you'll tackle the blockchain layer:

### 6.1 Connect to Polygon
- Use `web3.py` to connect to Polygon's RPC
- Query Polymarket smart contract transactions
- Understand the basics: blocks, transactions, addresses, gas

### 6.2 Whale Identification
- Use Polygonscan API to find large transactions on Polymarket contracts
- Filter for bets above your whale threshold
- Build and maintain a whale address list

### 6.3 Whale Behavior Analysis
- For each whale, pull their full betting history
- Calculate: win rate, average bet size, timing relative to odds movement
- Create visualizations of whale patterns

### 6.4 Signal Generation
- Build logic to detect: "a known whale just made a large bet"
- Compare against historical data: do whale entries predict odds movement?
- Backtest simple strategies

---

## Quick Reference Card

### Cursor Shortcuts (Windows)
| Shortcut | Action |
|----------|--------|
| `Ctrl+L` | Open AI Chat |
| `Ctrl+I` | Open Composer (multi-file AI edits) |
| `Ctrl+K` | Inline Edit (edit selected code) |
| `` Ctrl+` `` | Open/close terminal |
| `Ctrl+S` | Save current file |
| `Ctrl+Shift+P` | Command palette (search any Cursor action) |

### Terminal Commands You'll Use Often
| Command | What It Does |
|---------|-------------|
| `venv\Scripts\activate` | Activate virtual environment |
| `python script.py` | Run a Python script |
| `python -m pip install X` | Install a Python library |
| `git add .` | Stage all changes |
| `git commit -m "message"` | Save a snapshot |
| `git push` | Upload to GitHub |

### Vibe Coding Workflow
1. Open PLAN.md (for context)
2. Open Composer (`Ctrl+I`)
3. Describe what you want in plain English
4. Review the generated code
5. Run it in terminal
6. If error → paste error into Chat (`Ctrl+L`) → ask AI to fix
7. Repeat until it works
8. Commit with Git

---

*Last updated: Feb 2026*
*Project: Polymarket Whale Tracker*
