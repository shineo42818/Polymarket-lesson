"""
analyze_whale_patterns.py
=========================
Offline analysis of data/whale_log.csv (falls back to data/whale_log_Old.csv).

Answers the key question:
  "Why do whales profit when gap_monitor shows no gap?"

Answer: Whales use LIMIT ORDERS (filled at bid prices) and fragment their
  positions across multiple fills in the same market round.

  gap_ask = 1.0 - ask_Up - ask_Down  ->  always ~-0.01 (AA scenario, always a loss)
  gap_bid = 1.0 - bid_Up - bid_Down  ->  can be positive (BB scenario, whale edge)

  Correct evaluation uses VWAP across ALL fills per wallet+slug round,
  not just one UP/DOWN pair. The hedged portion earns a certain arb profit;
  unhedged tokens create directional exposure in the best/worst case.

Run:
  python src/arbitrage/analyze_whale_patterns.py
"""

import pandas as pd
import os

WHALE_LOG     = "data/whale_log.csv"
WHALE_LOG_OLD = "data/whale_log_Old.csv"

# Polymarket crypto fee formula (applies to new markets from 2026-03-06)
# fee_per_share = FEE_RATE * (p * (1 - p)) ** FEE_EXP
FEE_RATE         = 0.25
FEE_EXP          = 2
MAKER_REBATE_PCT = 0.20   # makers receive 20% of taker fees back in shares


# ============================================================
# HELPERS
# ============================================================

def classify_combined(combined):
    if combined < 0.97:
        return "strong arb (<0.97)"
    elif combined < 1.00:
        return "marginal arb (0.97-1.00)"
    else:
        return "no arb (>= 1.00)"


def fee_per_share(p):
    """
    Taker fee charged in shares for Polymarket crypto markets.
    fee = feeRate x (p x (1-p))^exponent  where feeRate=0.25, exponent=2.
    Fee is deducted from shares received on buy orders.
    Max fee: 1.5625% at p=0.50; decreases symmetrically towards 0 and 1.
    """
    return FEE_RATE * (p * (1.0 - p)) ** FEE_EXP


def eff_price_taker(p):
    """
    Effective cost per net token received for a taker buy at listed price p.
    Since fee is deducted in shares: eff_price = p / (1 - fee_per_share(p)).
    """
    f = fee_per_share(p)
    if f >= 1.0:
        return float("inf")
    return p / (1.0 - f)


# ============================================================
# LOAD DATA
# ============================================================

def load():
    path = WHALE_LOG if os.path.exists(WHALE_LOG) else WHALE_LOG_OLD
    if not os.path.exists(path):
        print(f"ERROR: Neither {WHALE_LOG} nor {WHALE_LOG_OLD} found. "
              f"Run whale_monitor.py first.")
        return None
    df = pd.read_csv(path, on_bad_lines="skip")
    if df.empty:
        print(f"ERROR: {path} is empty.")
        return None
    print(f"Loaded {len(df):,} trades from {path}")
    return df


# ============================================================
# CORE: BUILD ROUND-LEVEL STATS (VWAP per wallet+slug round)
# ============================================================

def compute_round_stats(df):
    """
    Group all trades by (wallet, slug). For each round where BOTH UP and DOWN
    trades exist, compute VWAP cost basis and full P&L breakdown.

    P&L model:
      tokens per fill     = size_usd / price
      avg_up_vwap         = sum(up_size_usd) / sum(up_tokens)
      avg_dn_vwap         = sum(dn_size_usd) / sum(dn_tokens)
      combined_cost       = avg_up + avg_dn
      hedged_tokens       = min(up_tokens, dn_tokens)  -- earns certain arb
      unhedged_tokens     = |up_tokens - dn_tokens|    -- directional exposure
      hedged_profit       = hedged_tokens x (1 - combined_cost)  [certain]
      win_gain            = unhedged_tokens x (1 - avg_excess)   [if excess side wins]
      lose_loss           = unhedged_tokens x avg_excess          [if excess side loses]
      pnl_win             = hedged_profit + win_gain               [best case]
      pnl_lose            = hedged_profit - lose_loss              [worst case]

    Returns a DataFrame with one row per (wallet, slug) round.
    """
    d = df.copy()
    d["tokens"] = d["size_usd"] / d["price"]   # conditional tokens received per fill

    up = d[d["side"] == "UP"].groupby(["wallet", "slug"]).agg(
        up_spend    =("size_usd",        "sum"),
        up_tokens   =("tokens",          "sum"),
        up_first_ts =("trade_timestamp", "min"),
        up_last_ts  =("trade_timestamp", "max"),
        up_fills    =("size_usd",        "count"),
    )
    dn = d[d["side"] == "DOWN"].groupby(["wallet", "slug"]).agg(
        dn_spend    =("size_usd",        "sum"),
        dn_tokens   =("tokens",          "sum"),
        dn_first_ts =("trade_timestamp", "min"),
        dn_last_ts  =("trade_timestamp", "max"),
        dn_fills    =("size_usd",        "count"),
    )

    rounds = up.join(dn, how="inner").reset_index()

    # VWAP per side
    rounds["avg_up"]   = rounds["up_spend"] / rounds["up_tokens"]
    rounds["avg_dn"]   = rounds["dn_spend"] / rounds["dn_tokens"]
    rounds["combined"] = rounds["avg_up"]   + rounds["avg_dn"]
    rounds["edge_pct"] = (1.0 - rounds["combined"]) * 100

    # Hedged vs unhedged tokens
    rounds["hedged_tokens"]   = rounds[["up_tokens", "dn_tokens"]].min(axis=1)
    rounds["unhedged_tokens"] = (rounds["up_tokens"] - rounds["dn_tokens"]).abs()
    rounds["excess_side"]     = rounds.apply(
        lambda r: "UP" if r["up_tokens"] >= r["dn_tokens"] else "DN", axis=1)

    # 1. Certain arb profit (from hedged sets, regardless of outcome)
    rounds["hedged_profit"] = rounds["hedged_tokens"] * (1.0 - rounds["combined"])

    # 2. Win gain: if settlement favours the excess side
    rounds["win_gain"] = rounds.apply(lambda r:
        r["unhedged_tokens"] * (1.0 - r["avg_up"]) if r["excess_side"] == "UP"
        else r["unhedged_tokens"] * (1.0 - r["avg_dn"]), axis=1)

    # 3. Lose loss: if settlement goes against the excess side (tokens worth $0)
    rounds["lose_loss"] = rounds.apply(lambda r:
        r["unhedged_tokens"] * r["avg_up"] if r["excess_side"] == "UP"
        else r["unhedged_tokens"] * r["avg_dn"], axis=1)

    rounds["pnl_win"]     = rounds["hedged_profit"] + rounds["win_gain"]
    rounds["pnl_lose"]    = rounds["hedged_profit"] - rounds["lose_loss"]
    rounds["total_spend"] = rounds["up_spend"] + rounds["dn_spend"]

    # Execution window: from first fill (either side) to last fill (either side)
    rounds["exec_window_s"] = (
        rounds[["up_last_ts", "dn_last_ts"]].max(axis=1) -
        rounds[["up_first_ts", "dn_first_ts"]].min(axis=1)
    )

    return rounds


# ============================================================
# A. ROUND-LEVEL VWAP ANALYSIS
# ============================================================

def analyze_round_vwap(rounds):
    print("\n" + "=" * 65)
    print("  A. ROUND-LEVEL VWAP ANALYSIS (all fills per wallet+slug round)")
    print("=" * 65)

    if rounds.empty:
        print("  No rounds with both UP and DOWN found.")
        return

    n = len(rounds)
    print(f"\n  Total rounds (both UP and DOWN present): {n:,}")
    print(f"  Avg combined VWAP:                {rounds['combined'].mean():.4f}")
    print(f"  Median combined VWAP:             {rounds['combined'].median():.4f}")
    print(f"  Min combined VWAP (best arb):     {rounds['combined'].min():.4f}")
    print(f"  Avg UP fills per round:           {rounds['up_fills'].mean():.1f}")
    print(f"  Avg DN fills per round:           {rounds['dn_fills'].mean():.1f}")
    print(f"  Avg total spend per round:        ${rounds['total_spend'].mean():.2f}")
    print(f"  Avg hedged tokens per round:      {rounds['hedged_tokens'].mean():.1f}")
    print(f"  Avg unhedged tokens per round:    {rounds['unhedged_tokens'].mean():.1f}")

    print(f"\n  P&L Breakdown (avg per round, in USDC):")
    print(f"    1. Hedged profit (certain):          ${rounds['hedged_profit'].mean():>+8.2f}")
    print(f"    2. Win gain if excess side wins:     ${rounds['win_gain'].mean():>+8.2f}")
    print(f"    3. Lose loss if excess side loses:  -${rounds['lose_loss'].mean():>8.2f}")
    print(f"    {'-' * 48}")
    print(f"    Best-case  (win scenario):           ${rounds['pnl_win'].mean():>+8.2f}")
    print(f"    Worst-case (lose scenario):          ${rounds['pnl_lose'].mean():>+8.2f}")

    n_hedged_pos = (rounds["hedged_profit"] > 0).sum()
    n_lose_pos   = (rounds["pnl_lose"] > 0).sum()
    n_win_pos    = (rounds["pnl_win"] > 0).sum()
    print(f"\n  Rounds where hedged_profit > 0:  {n_hedged_pos:,}  ({n_hedged_pos/n*100:.1f}%)"
          f"  <- true arb rounds")
    print(f"  Rounds where pnl_lose > 0:       {n_lose_pos:,}  ({n_lose_pos/n*100:.1f}%)"
          f"  <- profitable EVEN if direction wrong")
    print(f"  Rounds where pnl_win > 0:        {n_win_pos:,}  ({n_win_pos/n*100:.1f}%)")

    print(f"\n  Classification by combined VWAP:")
    rounds_copy = rounds.copy()
    rounds_copy["category"] = rounds_copy["combined"].apply(classify_combined)
    for cat in ["strong arb (<0.97)", "marginal arb (0.97-1.00)", "no arb (>= 1.00)"]:
        grp = rounds_copy[rounds_copy["category"] == cat]
        if len(grp) == 0:
            continue
        pct      = len(grp) / n * 100
        avg_hp   = grp["hedged_profit"].mean()
        avg_edge = grp["edge_pct"].mean()
        print(f"    {cat:<30}  {len(grp):>6,}  ({pct:>5.1f}%)  "
              f"avg edge={avg_edge:>+6.2f}%  avg hedged P&L=${avg_hp:>+7.2f}")


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
    if not both.empty:
        print(f"\n  Most active bucket for arb trades: "
              f"{both['time_bucket'].value_counts().idxmax()}")
        print(f"  Average seconds_into for both_sides trades: "
              f"{both['seconds_into'].mean():.0f}s")


# ============================================================
# C. WALLET PROFITABILITY RANKING
# ============================================================

def analyze_wallets(df, rounds):
    print("\n" + "=" * 65)
    print("  C. WALLET PROFITABILITY RANKING")
    print("=" * 65)

    total_vol = df.groupby("wallet")["size_usd"].sum().rename("total_volume_usd")

    if rounds is not None and not rounds.empty:
        wallet_stats = rounds.groupby("wallet").agg(
            arb_rounds    =("combined",      "count"),
            avg_combined  =("combined",      "mean"),
            avg_edge_pct  =("edge_pct",      "mean"),
            hedged_profit =("hedged_profit", "sum"),
            pnl_win       =("pnl_win",       "sum"),
            pnl_lose      =("pnl_lose",      "sum"),
        )
        summary = pd.concat([total_vol, wallet_stats], axis=1).fillna(0)
        summary = summary.sort_values("arb_rounds", ascending=False).head(10)
    else:
        summary = pd.DataFrame({"total_volume_usd": total_vol}).head(10)

    print(f"\n  {'Wallet':<22} {'Rnds':>5} {'AvgCombined':>12} {'AvgEdge':>9} "
          f"{'HedgedP&L':>11} {'WinCase':>10} {'LoseCase':>10}")
    print(f"  {'-'*83}")

    for wallet, row in summary.iterrows():
        label = df[df["wallet"] == wallet]["wallet_label"].iloc[0] \
            if "wallet_label" in df.columns else wallet[:14]
        rnds  = int(row.get("arb_rounds", 0))
        acomb = f"{row['avg_combined']:.4f}" if pd.notna(row.get("avg_combined")) and row.get("avg_combined") else "  N/A  "
        aedge = f"{row['avg_edge_pct']:>+.2f}%" if pd.notna(row.get("avg_edge_pct")) and row.get("avg_edge_pct") else "  N/A "
        hp    = f"${row['hedged_profit']:>+8.1f}" if pd.notna(row.get("hedged_profit")) else "   N/A  "
        pw    = f"${row['pnl_win']:>+7.1f}"       if pd.notna(row.get("pnl_win"))       else "   N/A "
        pl    = f"${row['pnl_lose']:>+7.1f}"      if pd.notna(row.get("pnl_lose"))      else "   N/A "
        print(f"  {label:<22} {rnds:>5,} {acomb:>12} {aedge:>9} {hp:>11} {pw:>10} {pl:>10}")


# ============================================================
# D. MARKET FOCUS
# ============================================================

def analyze_markets(df):
    print("\n" + "=" * 65)
    print("  D. MARKET FOCUS (which coin/type has most both-sides activity?)")
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

def print_takeaways(df, rounds):
    print("\n" + "=" * 65)
    print("  E. KEY TAKEAWAYS")
    print("=" * 65)

    total      = len(df)
    both_count = int(df["both_sides_flag"].sum())
    both_pct   = both_count / total * 100

    print(f"\n  Total trades logged:          {total:,}")
    print(f"  Both-sides (arb) trades:      {both_count:,}  ({both_pct:.1f}% of all trades)")

    if rounds is not None and not rounds.empty:
        profitable = rounds[rounds["combined"] < 1.0]
        pct_profit = len(profitable) / len(rounds) * 100
        avg_edge   = profitable["edge_pct"].mean() if not profitable.empty else 0
        total_hp   = rounds["hedged_profit"].sum()
        print(f"\n  Rounds with both UP and DOWN:  {len(rounds):,}")
        print(f"  Rounds with combined < 1.0:    {len(profitable):,}  ({pct_profit:.1f}%)")
        print(f"  Avg edge on profitable rounds: {avg_edge:.2f}%")
        print(f"  Total estimated hedged P&L:    ${total_hp:,.2f}")

    print(f"""
  WHY WHALES PROFIT WHEN gap_monitor SHOWS NO GAP:
  -------------------------------------------------
  gap_monitor tracks ASK prices -> gap_ask = 1.0 - ask_Up - ask_Down ~ -0.01
  (market makers embed a ~1% spread; AA scenario structurally impossible)

  Whales use LIMIT ORDERS -> filled at BID prices.
  gap_bid = 1.0 - bid_Up - bid_Down -> can be positive!

  A market with bid_Up=0.29, bid_Down=0.69:
    gap_ask = 1.0 - 0.31 - 0.71 = -0.02  (monitor: no opportunity)
    gap_bid = 1.0 - 0.29 - 0.69 = +0.02  (whale: +2% edge)

  Whales also fragment orders across multiple fills per round.
  VWAP across all fills = true cost basis.
  Hedged portion earns a certain arb profit regardless of outcome.
  Unhedged portion = directional exposure (win or lose at settlement).
""")


# ============================================================
# F. EXECUTION WINDOW TIMING
# ============================================================

def analyze_fill_timing(rounds):
    print("\n" + "=" * 65)
    print("  F. EXECUTION WINDOW (first fill to last fill, both sides)")
    print("=" * 65)

    print(f"\n  Median execution window:   {rounds['exec_window_s'].median():.0f}s")
    print(f"  90th percentile:           {rounds['exec_window_s'].quantile(0.90):.0f}s")
    print(f"  Max execution window:      {rounds['exec_window_s'].max():.0f}s")

    bins   = [0, 10, 30, 60, 120, float("inf")]
    labels = ["0-10s", "10-30s", "30-60s", "60-120s", ">120s"]
    rounds_copy = rounds.copy()
    rounds_copy["win_bucket"] = pd.cut(
        rounds_copy["exec_window_s"], bins=bins, labels=labels)

    total  = len(rounds_copy)
    interp = {
        "0-10s":   "near-simultaneous (both limits placed at same time)",
        "10-30s":  "very fast sequential or fill staggering",
        "30-60s":  "moderate delay — typical limit order fragmentation",
        "60-120s": "significant delay — sequential placement with price risk",
        ">120s":   "long execution — multi-stage position building",
    }
    print(f"\n  {'Bucket':<10} {'Rounds':>8}  {'%':>5}  Interpretation")
    print(f"  {'-'*70}")
    for label in labels:
        grp = rounds_copy[rounds_copy["win_bucket"] == label]
        pct = len(grp) / total * 100
        print(f"  {label:<10} {len(grp):>8,}  {pct:>5.1f}%  {interp[label]}")

    fast = (rounds["exec_window_s"] <= 30).sum()
    tag  = "mostly simultaneous" if fast / total > 0.5 else "mostly sequential"
    print(f"\n  {fast/total*100:.1f}% of rounds complete within 30s -> {tag}")


# ============================================================
# G. FEE-ADJUSTED PROFITABILITY
# ============================================================

def analyze_fees(rounds):
    print("\n" + "=" * 65)
    print("  G. FEE-ADJUSTED PROFITABILITY (Polymarket crypto fee formula)")
    print("=" * 65)
    print(f"\n  Fee formula: fee_per_share = 0.25 x (p x (1-p))^2  (taker only)")
    print(f"  Fee deducted in shares on buy; maker gets 20% of taker fee as rebate.")
    print(f"  NOTE: Historical data pre-2026-03-06 collected with 0% fees.")
    print(f"        Rows below show what fees WOULD do after March 6, 2026.\n")

    r = rounds.copy()

    # Effective taker prices
    r["eff_up_taker"] = r["avg_up"].apply(eff_price_taker)
    r["eff_dn_taker"] = r["avg_dn"].apply(eff_price_taker)

    # Per-hedged-token profit under each fee scenario
    # Maker+Maker: both use limit orders, 0% fee (whale BB strategy)
    r["profit_maker"] = 1.0 - r["combined"]

    # Mixed: UP limit (maker 0%), DN market (taker +fee)
    r["profit_mixed_up_maker"] = 1.0 - r["avg_up"] - r["eff_dn_taker"]

    # Mixed: UP market (taker +fee), DN limit (maker 0%)
    r["profit_mixed_dn_maker"] = 1.0 - r["eff_up_taker"] - r["avg_dn"]

    # Both market orders: both pay taker fee
    r["profit_taker"] = 1.0 - r["eff_up_taker"] - r["eff_dn_taker"]

    scenarios = [
        ("Maker+Maker (whale: both limit orders)",     "profit_maker"),
        ("Mixed: UP-maker + DN-taker",                 "profit_mixed_up_maker"),
        ("Mixed: UP-taker + DN-maker",                 "profit_mixed_dn_maker"),
        ("Taker+Taker (retail: both market orders)",   "profit_taker"),
    ]

    print(f"  {'Scenario':<44} {'Profitable':>11} {'Avg Edge':>9}")
    print(f"  {'-'*68}")
    for label, col in scenarios:
        profitable = (r[col] > 0).mean() * 100
        avg_edge   = r[col].mean() * 100
        marker     = "  <- WHALE" if col == "profit_maker" else ""
        print(f"  {label:<44} {profitable:>10.1f}%  {avg_edge:>+8.2f}%{marker}")

    # Spot-checks
    print(f"\n  Spot-check fee_per_share(0.50) = {fee_per_share(0.50):.6f}  (expected 0.015625)")
    print(f"  Spot-check fee_per_share(0.30) = {fee_per_share(0.30):.6f}  (expected 0.011025)")


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
    df["both_sides_flag"] = df["both_sides_flag"].astype(str).str.lower() == "true"
    df["trade_timestamp"] = pd.to_numeric(df["trade_timestamp"], errors="coerce")
    df["price"]           = pd.to_numeric(df["price"], errors="coerce")
    df["size_usd"]        = pd.to_numeric(df["size_usd"], errors="coerce")

    # Drop rows with missing critical fields (avoid division by zero in token calc)
    df = df.dropna(subset=["price", "size_usd", "trade_timestamp"])
    df = df[df["price"] > 0]

    # Compute round-level VWAP stats
    rounds = compute_round_stats(df)

    analyze_round_vwap(rounds)
    analyze_timing(df)
    analyze_wallets(df, rounds)
    analyze_markets(df)
    print_takeaways(df, rounds)
    analyze_fill_timing(rounds)
    analyze_fees(rounds)


if __name__ == "__main__":
    main()
