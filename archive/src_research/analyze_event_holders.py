# analyze_event_holders.py — Combine top holders across all outcomes of a Polymarket event
#
# Usage:
#   python src/arbitrage/analyze_event_holders.py <event_slug>
#
# Example:
#   python src/arbitrage/analyze_event_holders.py highest-temperature-in-nyc-on-march-15

import requests
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import sys
import os

# Fix encoding for Windows terminals
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ============================================================
# API HELPERS
# ============================================================

def fetch_event(slug):
    """Fetch event data from Gamma API. Returns the event dict or None."""
    url = f"https://gamma-api.polymarket.com/events?slug={slug}"
    resp = requests.get(url, timeout=15)
    if resp.status_code != 200:
        print(f"Error fetching event: {resp.status_code}")
        return None
    data = resp.json()
    if not data:
        print(f"No event found for slug: {slug}")
        return None
    return data[0]


def parse_markets(event):
    """
    Extract sub-markets from the event.
    Returns list of dicts: [{question, condition_id, yes_token, no_token}, ...]
    """
    markets_raw = event.get("markets", [])
    # markets may be a JSON string in some responses
    if isinstance(markets_raw, str):
        import json
        markets_raw = json.loads(markets_raw)

    markets = []
    for m in markets_raw:
        tokens = m.get("clobTokenIds", "")
        if isinstance(tokens, str):
            import json
            tokens = json.loads(tokens)

        if len(tokens) < 2:
            continue

        markets.append({
            "question": m.get("question", m.get("groupItemTitle", "Unknown")),
            "condition_id": m.get("conditionId", ""),
            "yes_token": tokens[0],
            "no_token": tokens[1],
        })
    return markets


def fetch_holders(condition_ids, limit=20):
    """
    Fetch top holders for multiple condition IDs in one call.
    Returns the raw JSON array of {token, holders} groups.
    """
    url = "https://data-api.polymarket.com/holders"
    params = {
        "market": ",".join(condition_ids),
        "limit": limit,
    }
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code != 200:
        print(f"Error fetching holders: {resp.status_code} — {resp.text[:200]}")
        return []
    return resp.json()


# ============================================================
# DISPLAY HELPERS
# ============================================================

def _shorten_columns(columns, total_col="TOTAL_YES"):
    """
    Attempt to shorten long market question strings for table display.
    Extracts the distinguishing part (e.g. temperature range, outcome name).
    """
    import re
    mapping = {}
    for col in columns:
        if col in (total_col, "TOTAL"):
            mapping[col] = "TOTAL"
            continue

        short = col
        # Try: "Will the highest temperature in NYC be between 46-47°F on March 15?" → "46-47F"
        m = re.search(r'(\d+[-–]\d+)\s*°?\s*F', col)
        if m:
            short = m.group(1) + "F"
        else:
            # Try: "45°F or below" → "<=45F", "56°F or higher" → ">=56F"
            m = re.search(r'(\d+)\s*°?\s*F\s+or\s+(below|lower)', col)
            if m:
                short = "<=" + m.group(1) + "F"
            else:
                m = re.search(r'(\d+)\s*°?\s*F\s+or\s+(above|higher)', col)
                if m:
                    short = ">=" + m.group(1) + "F"
                else:
                    # Generic: try to find the unique part after common prefix
                    # Keep it short — take last meaningful segment
                    parts = col.replace("?", "").strip().split()
                    if len(parts) > 3:
                        # Use last 3 words as short name
                        short = " ".join(parts[-3:])

        mapping[col] = short
    return mapping


# ============================================================
# VISUALIZATION
# ============================================================

def plot_holders(df, slug):
    """
    Create stacked horizontal bar charts for YES and NO holders.
    Each chart shows top 20 holders with color segments per outcome (temperature range).
    Saves PNGs to data/ and displays interactively.
    """
    col_shortener = _shorten_columns(df["outcome"].unique().tolist(), total_col="")

    for side in ("YES", "NO"):
        side_df = df[df["side"] == side].copy()
        if side_df.empty:
            print(f"  No {side} holders to plot.")
            continue

        # Map outcome names to short labels for legend
        side_df["outcome_short"] = side_df["outcome"].map(col_shortener)

        # Pivot: rows=holder, columns=outcome_short, values=amount
        pivot = side_df.pivot_table(
            index="display_name",
            columns="outcome_short",
            values="amount",
            aggfunc="sum",
            fill_value=0,
        )

        # Sort by total shares descending, take top 20
        pivot["_total"] = pivot.sum(axis=1)
        pivot = pivot.sort_values("_total", ascending=False).head(20)
        pivot = pivot.drop(columns=["_total"])

        # Reverse so largest is at top of horizontal bar chart
        pivot = pivot.iloc[::-1]

        # Sort columns by total across all holders (most popular outcome first)
        col_order = pivot.sum(axis=0).sort_values(ascending=False).index.tolist()
        pivot = pivot[col_order]

        # Plot
        fig, ax = plt.subplots(figsize=(12, max(4, len(pivot) * 0.45)))
        cmap = plt.colormaps.get_cmap("tab20").resampled(len(pivot.columns))
        left = np.zeros(len(pivot))

        for i, col in enumerate(pivot.columns):
            vals = pivot[col].values
            ax.barh(pivot.index, vals, left=left, label=col, color=cmap(i))
            left += vals

        ax.set_xlabel("Shares")
        ax.set_title(f"Top {side} Holders — {slug}")
        ax.legend(title="Outcome", bbox_to_anchor=(1.02, 1), loc="upper left", fontsize=8)
        fig.tight_layout()

        out_path = f"data/event_holders_{slug}_{side.lower()}.png"
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        print(f"  Chart saved: {out_path}")

    plt.show()


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze_event_holders(slug, limit=20):
    print(f"\n{'=' * 70}")
    print(f"  EVENT HOLDER ANALYSIS")
    print(f"  Slug: {slug}")
    print(f"{'=' * 70}\n")

    # 1. Fetch event and parse sub-markets
    event = fetch_event(slug)
    if not event:
        return

    title = event.get("title", slug)
    print(f"  Event: {title}")

    markets = parse_markets(event)
    if not markets:
        print("  No sub-markets found.")
        return

    print(f"  Sub-markets: {len(markets)}")
    for i, m in enumerate(markets):
        print(f"    [{i+1}] {m['question']}")

    # 2. Build token→outcome mapping
    token_to_outcome = {}  # token_id → (question, "YES"/"NO")
    for m in markets:
        token_to_outcome[m["yes_token"]] = (m["question"], "YES")
        token_to_outcome[m["no_token"]] = (m["question"], "NO")

    # 3. Fetch holders
    condition_ids = [m["condition_id"] for m in markets]
    print(f"\n  Fetching holders (limit={limit} per token)...")
    raw_holders = fetch_holders(condition_ids, limit=limit)

    if not raw_holders:
        print("  No holder data returned.")
        return

    # 4. Flatten into rows
    rows = []
    for group in raw_holders:
        token = group.get("token", "")
        outcome_info = token_to_outcome.get(token)
        if not outcome_info:
            continue  # skip unknown tokens

        question, side = outcome_info
        for h in group.get("holders", []):
            wallet = h.get("proxyWallet", "")
            display = h.get("pseudonym") or h.get("name") or wallet[:10]
            rows.append({
                "wallet": wallet,
                "display_name": display,
                "outcome": question,
                "side": side,
                "amount": float(h.get("amount", 0)),
            })

    if not rows:
        print("  No positions found.")
        return

    df = pd.DataFrame(rows)

    # 5. Print per-outcome breakdown (the "split" view, for reference)
    print(f"\n{'=' * 70}")
    print(f"  POSITIONS BY OUTCOME")
    print(f"{'=' * 70}")
    for outcome in df["outcome"].unique():
        sub = df[df["outcome"] == outcome].sort_values("amount", ascending=False)
        print(f"\n  --- {outcome} ---")
        for _, r in sub.iterrows():
            print(f"    {r['display_name']:<25} {r['side']:>3}  {r['amount']:>10.2f} shares")

    # 6. Build the combined table: one row per wallet, columns = outcomes
    print(f"\n{'=' * 70}")
    print(f"  COMBINED HOLDER TABLE (all outcomes)")
    print(f"{'=' * 70}\n")

    # Pivot: for each wallet, show YES positions per outcome
    # We focus on YES positions since that's the directional bet
    yes_df = df[df["side"] == "YES"].copy()

    if yes_df.empty:
        print("  No YES positions found.")
        return

    pivot = yes_df.pivot_table(
        index=["wallet", "display_name"],
        columns="outcome",
        values="amount",
        aggfunc="sum",
        fill_value=0,
    )

    # Add total column
    pivot["TOTAL_YES"] = pivot.sum(axis=1)
    pivot = pivot.sort_values("TOTAL_YES", ascending=False)

    # Shorten column names for readable display
    pivot = pivot.rename(columns=_shorten_columns(pivot.columns))

    # Format for display
    print(pivot.to_string(float_format=lambda x: f"{x:,.2f}" if x > 0 else "-"))

    # 7. Also show NO holders summary
    no_df = df[df["side"] == "NO"].copy()
    if not no_df.empty:
        print(f"\n{'=' * 70}")
        print(f"  NO-TOKEN HOLDERS (hedging / market-making)")
        print(f"{'=' * 70}\n")
        no_pivot = no_df.pivot_table(
            index=["wallet", "display_name"],
            columns="outcome",
            values="amount",
            aggfunc="sum",
            fill_value=0,
        )
        no_pivot["TOTAL_NO"] = no_pivot.sum(axis=1)
        no_pivot = no_pivot.sort_values("TOTAL_NO", ascending=False)
        no_pivot = no_pivot.rename(columns=_shorten_columns(no_pivot.columns, total_col="TOTAL_NO"))
        print(no_pivot.to_string(float_format=lambda x: f"{x:,.2f}" if x > 0 else "-"))

    # 8. Save to CSV
    out_file = f"data/event_holders_{slug}.csv"
    df.to_csv(out_file, index=False)
    print(f"\n  Raw data saved to: {out_file}")

    # 9. Visualize
    print(f"\n  Generating charts...")
    plot_holders(df, slug)

    print(f"\n{'=' * 70}")
    print(f"  DONE")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        slug = "highest-temperature-in-nyc-on-march-15"
        print(f"  No slug provided. Using default: {slug}")
    else:
        slug = sys.argv[1]

    analyze_event_holders(slug)
