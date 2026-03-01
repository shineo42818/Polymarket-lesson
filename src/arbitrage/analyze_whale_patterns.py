"""
analyze_whale_patterns.py
=========================
Offline analysis of data/whale_log.csv.

Answers the key question:
  "Why do whales profit when gap_monitor shows no gap?"

Answer: Whales use LIMIT ORDERS (filled at bid prices).
  gap_ask = 1.0 - ask_Up - ask_Down  ->  always ~-0.01 (market maker spread)
  gap_bid = 1.0 - bid_Up - bid_Down  ->  can be positive (whale edge)

Run:
  python src/arbitrage/analyze_whale_patterns.py
"""

import pandas as pd
import os

WHALE_LOG = "data/whale_log.csv"


# ============================================================
# HELPERS
# ============================================================

def parse_slug(slug):
    """Extract coin, market_type, and market_open_ts from a slug string."""
    parts = slug.split("-")
    # Format: {coin}-updown-{type}-{ts}  e.g. btc-updown-5m-1772376900
    try:
        market_open_ts = int(parts[-1])
        market_type    = parts[-2]         # "5m" or "15m"
        coin           = parts[0]          # "btc", "eth", "sol"
        interval       = 300 if market_type == "5m" else 900
        return coin, market_type, market_open_ts, interval
    except Exception:
        return None, None, None, None


def classify_combined(combined):
    if combined < 0.97:
        return "strong arb (<0.97)"
    elif combined < 1.00:
        return "marginal arb (0.97-1.00)"
    else:
        return "no arb (>= 1.00)"


# ============================================================
# LOAD DATA
# ============================================================

def load():
    if not os.path.exists(WHALE_LOG):
        print(f"ERROR: {WHALE_LOG} not found. Run whale_monitor.py first.")
        return None
    df = pd.read_csv(WHALE_LOG)
    if df.empty:
        print("ERROR: whale_log.csv is empty.")
        return None
    print(f"Loaded {len(df):,} trades from {WHALE_LOG}")
    return df


# ============================================================
# A. COMBINED PRICE ANALYSIS
# ============================================================

def analyze_combined_prices(df):
    print("\n" + "=" * 65)
    print("  A. COMBINED PRICE ANALYSIS (matched UP+DOWN pairs)")
    print("=" * 65)

    both = df[df["both_sides_flag"] == True].copy()
    if both.empty:
        print("  No both_sides trades found.")
        return

    # Group by wallet + slug, pivot UP and DOWN prices
    up_trades   = both[both["side"] == "UP" ][["wallet", "slug", "price", "size_usd"]]
    down_trades = both[both["side"] == "DOWN"][["wallet", "slug", "price", "size_usd"]]

    merged = pd.merge(
        up_trades.rename(columns={"price": "up_price", "size_usd": "up_size"}),
        down_trades.rename(columns={"price": "down_price", "size_usd": "down_size"}),
        on=["wallet", "slug"],
        how="inner"
    )

    if merged.empty:
        print("  Could not match UP/DOWN pairs within same wallet+slug.")
        return

    merged["combined"] = merged["up_price"] + merged["down_price"]
    merged["edge_pct"]  = (1.0 - merged["combined"]) * 100
    merged["category"]  = merged["combined"].apply(classify_combined)

    total = len(merged)
    print(f"\n  Total matched pairs: {total:,}")
    print(f"  Average combined price: {merged['combined'].mean():.4f}")
    print(f"  Average edge per pair:  {merged['edge_pct'].mean():.2f}%")
    print(f"  Median combined price:  {merged['combined'].median():.4f}")
    print(f"  Min combined price:     {merged['combined'].min():.4f}  (best arb)")
    print(f"  Max combined price:     {merged['combined'].max():.4f}")

    print(f"\n  Classification:")
    for cat, grp in merged.groupby("category"):
        pct = len(grp) / total * 100
        avg_edge = grp["edge_pct"].mean()
        print(f"    {cat:<30} {len(grp):>5} pairs  ({pct:.1f}%)  avg edge={avg_edge:.2f}%")

    return merged


# ============================================================
# B. TIMING PATTERN
# ============================================================

def analyze_timing(df):
    print("\n" + "=" * 65)
    print("  B. TIMING PATTERN (when into the market window do they trade?)")
    print("=" * 65)

    df = df.copy()
    df["market_open_ts"] = df["slug"].apply(
        lambda s: int(s.split("-")[-1]) if s and "-" in s else None
    )
    df["market_interval"] = df["slug"].apply(
        lambda s: 300 if "5m" in s else (900 if "15m" in s else None)
    )
    df["seconds_into"] = df["trade_timestamp"] - df["market_open_ts"]
    df["pct_into"]     = (df["seconds_into"] / df["market_interval"] * 100).clip(0, 100)

    bins   = [0, 10, 30, 60, 90, 100]
    labels = ["0-10%", "10-30%", "30-60%", "60-90%", "90-100%"]
    df["time_bucket"] = pd.cut(df["pct_into"], bins=bins, labels=labels, include_lowest=True)

    print(f"\n  {'Bucket':<12} {'All trades':>11} {'Both_sides=True':>16} {'%':>5}")
    print(f"  {'-'*50}")
    for label in labels:
        bucket_all  = df[df["time_bucket"] == label]
        bucket_both = bucket_all[bucket_all["both_sides_flag"] == True]
        pct = len(bucket_both) / len(bucket_all) * 100 if len(bucket_all) else 0
        print(f"  {label:<12} {len(bucket_all):>11,} {len(bucket_both):>16,} {pct:>4.0f}%")

    both = df[df["both_sides_flag"] == True]
    print(f"\n  Most active bucket for arb trades: "
          f"{both['time_bucket'].value_counts().idxmax()}")
    print(f"  Average seconds_into for both_sides trades: "
          f"{both['seconds_into'].mean():.0f}s")


# ============================================================
# C. WALLET PROFITABILITY RANKING
# ============================================================

def analyze_wallets(df, merged_pairs):
    print("\n" + "=" * 65)
    print("  C. WALLET PROFITABILITY RANKING")
    print("=" * 65)

    both_counts = df[df["both_sides_flag"] == True].groupby("wallet").size().rename("both_sides_trades")
    total_vol   = df.groupby("wallet")["size_usd"].sum().rename("total_volume_usd")

    summary = pd.concat([both_counts, total_vol], axis=1).fillna(0)
    summary["both_sides_trades"] = summary["both_sides_trades"].astype(int)

    if merged_pairs is not None and not merged_pairs.empty:
        wallet_pairs = merged_pairs.groupby("wallet").agg(
            matched_pairs=("combined", "count"),
            avg_combined=("combined", "mean"),
            avg_edge_pct=("edge_pct", "mean"),
            estimated_edge_usd=("edge_pct", lambda x: (x / 100 * merged_pairs.loc[x.index, "up_size"]).sum())
        )
        summary = summary.join(wallet_pairs, how="left")

    summary = summary.sort_values("both_sides_trades", ascending=False).head(10)

    for wallet, row in summary.iterrows():
        label = df[df["wallet"] == wallet]["wallet_label"].iloc[0] \
            if "wallet_label" in df.columns else wallet[:12]
        avg_comb = f"{row['avg_combined']:.4f}" if pd.notna(row.get("avg_combined")) else "N/A"
        edge     = f"{row['avg_edge_pct']:.2f}%" if pd.notna(row.get("avg_edge_pct")) else "N/A"
        print(f"  {label:<20} both_sides={int(row['both_sides_trades']):>5}  "
              f"avg_combined={avg_comb}  avg_edge={edge}")


# ============================================================
# D. MARKET FOCUS
# ============================================================

def analyze_markets(df):
    print("\n" + "=" * 65)
    print("  D. MARKET FOCUS (which coin/type has most profitable arb?)")
    print("=" * 65)

    both = df[df["both_sides_flag"] == True]
    market_counts = both.groupby(["coin", "market_type"]).size().reset_index(name="both_sides_trades")
    market_counts["market"] = market_counts["coin"].str.upper() + " " + market_counts["market_type"]
    market_counts = market_counts.sort_values("both_sides_trades", ascending=False)

    print(f"\n  {'Market':<12} {'Both-sides trades':>18}")
    print(f"  {'-'*35}")
    for _, row in market_counts.iterrows():
        print(f"  {row['market']:<12} {int(row['both_sides_trades']):>18,}")


# ============================================================
# E. KEY TAKEAWAYS
# ============================================================

def print_takeaways(df, merged_pairs):
    print("\n" + "=" * 65)
    print("  E. KEY TAKEAWAYS")
    print("=" * 65)

    total        = len(df)
    both_count   = int(df["both_sides_flag"].sum())
    both_pct     = both_count / total * 100

    print(f"\n  Total trades logged:          {total:,}")
    print(f"  Both-sides (arb) trades:      {both_count:,}  ({both_pct:.1f}% of all trades)")

    if merged_pairs is not None and not merged_pairs.empty:
        profitable = merged_pairs[merged_pairs["combined"] < 1.0]
        pct_profit = len(profitable) / len(merged_pairs) * 100
        avg_edge   = profitable["edge_pct"].mean()
        print(f"\n  Matched UP+DOWN pairs:        {len(merged_pairs):,}")
        print(f"  Pairs with combined < 1.0:    {len(profitable):,}  ({pct_profit:.1f}%)")
        print(f"  Avg edge on profitable pairs: {avg_edge:.2f}%")

    print(f"""
  WHY WHALES PROFIT WHEN gap_monitor SHOWS NO GAP:
  -------------------------------------------------
  Our gap_monitor tracks ASK prices (best ask).
  ASK-based gap = 1.0 - ask_Up - ask_Down ≈ -0.01 always
  (market makers embed a ~1% spread into ask prices)

  Whales use LIMIT ORDERS filled at BID prices.
  BID-based gap = 1.0 - bid_Up - bid_Down → can be positive!

  A market with bid_Up=0.29, bid_Down=0.69:
    gap_ask = 1.0 - 0.31 - 0.71 = -0.02  (our monitor: no opportunity)
    gap_bid = 1.0 - 0.29 - 0.69 = +0.02  (whale sees: +2% edge)

  NEXT STEP: gap_monitor.py now also tracks gap_bid so we can
  detect the same opportunities whales see via limit orders.
""")


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 65)
    print("  WHALE PATTERN ANALYSIS")
    print("=" * 65)

    df = load()
    if df is None:
        return

    # Normalize column types
    df["both_sides_flag"]  = df["both_sides_flag"].astype(str).str.lower() == "true"
    df["trade_timestamp"]  = pd.to_numeric(df["trade_timestamp"], errors="coerce")
    df["price"]            = pd.to_numeric(df["price"], errors="coerce")
    df["size_usd"]         = pd.to_numeric(df["size_usd"], errors="coerce")

    merged_pairs = analyze_combined_prices(df)
    analyze_timing(df)
    analyze_wallets(df, merged_pairs)
    analyze_markets(df)
    print_takeaways(df, merged_pairs)


if __name__ == "__main__":
    main()
