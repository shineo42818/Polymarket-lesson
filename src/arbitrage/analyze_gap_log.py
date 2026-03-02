"""
analyze_gap_log.py
==================
Offline analysis of data/gap_log.csv (falls back to data/gap_log_old.csv).

Answers the key question:
  "What does the gap_bid opportunity landscape look like across coins,
   market types, timing, and episode durations?"

Key concepts:
  gap_ask      = 1.0 - yes_ask - no_ask  -> always ~-0.01 to -0.05 (AA, never profitable)
  gap_bid      = 1.0 - yes_bid - no_bid  -> can be +0.05+ (BB, limit-order whale edge)
  gap_duration_ms = ms since this episode opened (0 = first snapshot of episode)
  arb_size_usd = min(yes_ask_size, no_ask_size) -- max USDC executable at best ask

Run:
  python src/arbitrage/analyze_gap_log.py
"""

import pandas as pd
import os

GAP_LOG     = "data/gap_log.csv"
GAP_LOG_OLD = "data/gap_log_old.csv"

COL_NAMES = [
    "recorded_at", "coin", "market_type", "slug", "market_closes",
    "seconds_left", "yes_price", "no_price", "gap", "gap_bid",
    "gap_duration_ms", "arb_size_usd", "opportunity"
]

NUMERIC_COLS = [
    "seconds_left", "yes_price", "no_price", "gap",
    "gap_bid", "gap_duration_ms", "arb_size_usd"
]


# ============================================================
# LOAD DATA
# ============================================================

def load():
    if os.path.exists(GAP_LOG) and os.path.getsize(GAP_LOG) > 0:
        path, has_hdr = GAP_LOG, True
    elif os.path.exists(GAP_LOG_OLD) and os.path.getsize(GAP_LOG_OLD) > 0:
        path, has_hdr = GAP_LOG_OLD, False
    else:
        print(f"ERROR: Neither {GAP_LOG} nor {GAP_LOG_OLD} found.")
        return None

    if has_hdr:
        df = pd.read_csv(path, on_bad_lines="skip")
    else:
        # gap_log_old.csv has NO header row
        df = pd.read_csv(path, on_bad_lines="skip", header=None, names=COL_NAMES)

    if df.empty:
        print(f"ERROR: {path} is empty.")
        return None

    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["gap_bid", "arb_size_usd", "gap_duration_ms"])
    print(f"Loaded {len(df):,} rows from {path}")
    return df


# ============================================================
# EPISODE DETECTION
# ============================================================

def build_episodes(df):
    """
    Assign a sequential episode_id within each (coin, market_type) group.

    A new episode starts when:
      - gap_duration_ms == 0  (explicit reset: new gap opened after a closed one)
      - gap_duration_ms < previous value in the same group (implicit rollover)
      - First row of any group

    Returns (df_with_episode_ids, episodes_summary_df).
    episodes_summary_df has one row per unique episode with aggregate stats.
    """
    d = df.sort_values(["coin", "market_type", "recorded_at"]).reset_index(drop=True)

    d["_prev_dur"] = d.groupby(["coin", "market_type"])["gap_duration_ms"].shift(1)
    d["_ep_start"] = (
        (d["gap_duration_ms"] == 0) |
        (d["gap_duration_ms"] < d["_prev_dur"])
    ).fillna(True)   # NaN (first row of each group) counts as a new episode

    d["episode_id"] = d.groupby(["coin", "market_type"])["_ep_start"].cumsum()

    episodes = d.groupby(["coin", "market_type", "slug", "episode_id"]).agg(
        episode_length_ms = ("gap_duration_ms", "max"),
        snapshot_count    = ("gap_duration_ms", "count"),
        best_gap_bid      = ("gap_bid",         "max"),
        avg_gap_bid       = ("gap_bid",         "mean"),
        best_arb_size     = ("arb_size_usd",    "max"),
        avg_arb_size      = ("arb_size_usd",    "mean"),
    ).reset_index()

    return d, episodes


# ============================================================
# A. OVERALL SUMMARY
# ============================================================

def analyze_summary(df, episodes):
    print("\n" + "=" * 65)
    print("  A. OVERALL SUMMARY")
    print("=" * 65)

    n_rows = len(df)
    n_ep   = len(episodes)
    ts_min = str(df["recorded_at"].min())[:19]
    ts_max = str(df["recorded_at"].max())[:19]

    print(f"\n  Total snapshots logged:    {n_rows:,}")
    print(f"  Date range:                {ts_min}  to  {ts_max}")
    print(f"  Total gap episodes:        {n_ep:,}")

    print(f"\n  {'Market':<14} {'Snapshots':>10} {'Episodes':>10}")
    print(f"  {'-' * 38}")
    for (coin, mtype), grp in df.groupby(["coin", "market_type"]):
        label   = f"{coin.upper()} {mtype}"
        n_snaps = len(grp)
        n_ep_m  = len(episodes[
            (episodes["coin"] == coin) & (episodes["market_type"] == mtype)
        ])
        print(f"  {label:<14} {n_snaps:>10,} {n_ep_m:>10,}")


# ============================================================
# B. GAP SIZE DISTRIBUTION
# ============================================================

def analyze_gap_distribution(df):
    print("\n" + "=" * 65)
    print("  B. GAP SIZE DISTRIBUTION (gap_bid -- BB scenario, limit orders)")
    print("=" * 65)

    gb = df["gap_bid"].dropna()
    n  = len(gb)

    print(f"\n  Count:   {n:,}")
    print(f"  Min:     {gb.min():.4f}")
    print(f"  Max:     {gb.max():.4f}")
    print(f"  Mean:    {gb.mean():.4f}")
    print(f"  Median:  {gb.median():.4f}")

    # All logged rows already satisfy gap_bid >= 0.05 (MIN_PROFITABLE_GAP filter)
    bins   = [0.05, 0.07, 0.10, 0.20, float("inf")]
    labels = ["0.05-0.07 ", "0.07-0.10 ", "0.10-0.20 ", "0.20+     "]

    print(f"\n  {'Bucket':<12} {'Count':>8} {'%':>7}  {'Avg gap_bid':>12}")
    print(f"  {'-' * 45}")

    bucketed = pd.cut(gb, bins=bins, labels=labels, include_lowest=True)
    for label in labels:
        mask    = bucketed == label
        cnt     = int(mask.sum())
        pct     = cnt / n * 100
        avg_gb  = gb[mask].mean() if cnt else float("nan")
        avg_str = f"{avg_gb:.4f}" if cnt else "  N/A  "
        print(f"  {label:<12} {cnt:>8,} {pct:>6.1f}%  {avg_str:>12}")

    print(f"\n  Note: gap_ask (AA/market orders) always ~-0.01 -- never profitable.")
    print(f"        gap_bid >= 0.05 = BB (limit-order) opportunity logged here.")


# ============================================================
# C. GAP DURATION ANALYSIS
# ============================================================

def analyze_duration(df, episodes):
    print("\n" + "=" * 65)
    print("  C. GAP DURATION ANALYSIS (episode lengths in milliseconds)")
    print("=" * 65)

    ep  = episodes.copy()
    n   = len(ep)
    avg = ep["episode_length_ms"].mean()
    med = ep["episode_length_ms"].median()
    mx  = ep["episode_length_ms"].max()

    print(f"\n  Total episodes:        {n:,}")
    print(f"  Avg episode length:    {avg:.0f} ms  ({avg/1000:.3f} s)")
    print(f"  Median episode length: {med:.0f} ms  ({med/1000:.3f} s)")
    print(f"  Max episode length:    {mx:.0f} ms  ({mx/1000:.3f} s)")

    bins_ms   = [0, 100, 500, 2000, 5000, float("inf")]
    labels_ms = ["<100ms   ", "100-500ms", "500ms-2s ", "2s-5s    ", ">5s      "]

    print(f"\n  {'Duration Bucket':<13} {'Episodes':>9} {'%':>7}  {'Avg gap_bid':>12}")
    print(f"  {'-' * 47}")

    ep["dur_bucket"] = pd.cut(
        ep["episode_length_ms"], bins=bins_ms, labels=labels_ms, include_lowest=True
    )
    for label in labels_ms:
        grp    = ep[ep["dur_bucket"] == label]
        cnt    = len(grp)
        pct    = cnt / n * 100
        avg_gb = grp["avg_gap_bid"].mean() if cnt else float("nan")
        gb_str = f"{avg_gb:.4f}" if cnt else "  N/A  "
        print(f"  {label:<13} {cnt:>9,} {pct:>6.1f}%  {gb_str:>12}")

    short = ep[ep["episode_length_ms"] < 500]
    long_ = ep[ep["episode_length_ms"] >= 500]
    s_avg = short["avg_gap_bid"].mean() if len(short) else float("nan")
    l_avg = long_["avg_gap_bid"].mean()  if len(long_) else float("nan")

    print(f"\n  Avg gap_bid -- short episodes (<500ms):   {s_avg:.4f}  (n={len(short):,})")
    print(f"  Avg gap_bid -- long episodes  (>=500ms):  {l_avg:.4f}  (n={len(long_):,})")
    print(f"\n  Interpretation: WebSocket fires many events per second.")
    print(f"  Most episodes are sub-second flashes. Whales need limit orders")
    print(f"  pre-positioned BEFORE the gap opens, not placed in reaction.")


# ============================================================
# D. ARB SIZE ANALYSIS
# ============================================================

def analyze_arb_size(df):
    print("\n" + "=" * 65)
    print("  D. ARB SIZE ANALYSIS (arb_size_usd at best ask, in USDC)")
    print("=" * 65)

    arb = df["arb_size_usd"].dropna()
    n   = len(arb)

    print(f"\n  Count:    {n:,}")
    print(f"  Min:      ${arb.min():>10,.2f}")
    print(f"  Max:      ${arb.max():>10,.2f}")
    print(f"  Mean:     ${arb.mean():>10,.2f}")
    print(f"  Median:   ${arb.median():>10,.2f}")

    bins   = [0, 5_000, 10_000, 15_000, 20_000, float("inf")]
    labels = ["<$5k     ", "$5k-$10k ", "$10k-$15k", "$15k-$20k", ">$20k    "]

    print(f"\n  {'Size Bucket':<13} {'Snapshots':>10} {'%':>7}")
    print(f"  {'-' * 35}")

    bucketed = pd.cut(arb, bins=bins, labels=labels, include_lowest=True)
    for label in labels:
        cnt = int((bucketed == label).sum())
        pct = cnt / n * 100
        print(f"  {label:<13} {cnt:>10,} {pct:>6.1f}%")

    print(f"\n  Note: arb_size_usd = min(yes_ask_depth, no_ask_depth).")
    print(f"  This is the max AA (market order) arb size at this instant.")
    print(f"  Limit-order (BB) arb size may differ (fills at bid depth).")


# ============================================================
# E. TIMING ANALYSIS
# ============================================================

def analyze_timing(df):
    print("\n" + "=" * 65)
    print("  E. TIMING ANALYSIS (seconds_left until market closes)")
    print("=" * 65)

    sl = df["seconds_left"].dropna()
    print(f"\n  Min seconds_left:  {sl.min():.0f}s")
    print(f"  Max seconds_left:  {sl.max():.0f}s")
    print(f"  Mean:              {sl.mean():.0f}s")

    bins   = [0, 50, 100, 200, 400, float("inf")]
    labels = ["<50s    ", "50-100s ", "100-200s", "200-400s", ">400s   "]

    d2 = df.copy()
    d2["sl_bucket"] = pd.cut(
        d2["seconds_left"], bins=bins, labels=labels, include_lowest=True
    )

    n = len(d2)
    print(f"\n  {'Time Window':<13} {'Snapshots':>10} {'%':>7}  {'Avg gap_bid':>12}")
    print(f"  {'-' * 48}")

    for label in labels:
        grp    = d2[d2["sl_bucket"] == label]
        cnt    = len(grp)
        pct    = cnt / n * 100
        avg_gb = grp["gap_bid"].mean() if cnt else float("nan")
        gb_str = f"{avg_gb:.4f}" if cnt else "  N/A  "
        print(f"  {label:<13} {cnt:>10,} {pct:>6.1f}%  {gb_str:>12}")

    bucket_avgs = d2.groupby("sl_bucket", observed=True)["gap_bid"].mean()
    best_bucket = bucket_avgs.idxmax()
    n_early = int((d2["sl_bucket"] == ">400s   ").sum())
    n_late  = int((d2["sl_bucket"] == "<50s    ").sum())
    pattern = "early in market (>400s)" if n_early > n_late else "late in market (<50s)"

    print(f"\n  Best avg gap_bid window: {best_bucket.strip()}  "
          f"(avg={bucket_avgs[best_bucket]:.4f})")
    print(f"  Gaps occur more:         {pattern}  "
          f"(>400s: {n_early:,}  vs  <50s: {n_late:,})")


# ============================================================
# F. COIN & MARKET FOCUS
# ============================================================

def analyze_coin_market(df, episodes):
    print("\n" + "=" * 65)
    print("  F. COIN & MARKET FOCUS")
    print("=" * 65)

    summary = episodes.groupby(["coin", "market_type"]).agg(
        episode_count   = ("episode_id",        "count"),
        avg_gap_bid     = ("avg_gap_bid",        "mean"),
        best_gap_bid    = ("best_gap_bid",       "max"),
        avg_arb_usd     = ("avg_arb_size",       "mean"),
        avg_ep_dur_ms   = ("episode_length_ms",  "mean"),
    ).reset_index().sort_values("episode_count", ascending=False)

    print(f"\n  {'Market':<12} {'Episodes':>9} {'AvgGapBid':>10} "
          f"{'BestGapBid':>11} {'AvgArbUSD':>11} {'AvgDurMs':>10}")
    print(f"  {'-' * 67}")

    for _, row in summary.iterrows():
        label = f"{row['coin'].upper()} {row['market_type']}"
        print(
            f"  {label:<12} {int(row['episode_count']):>9,} "
            f"{row['avg_gap_bid']:>10.4f} "
            f"{row['best_gap_bid']:>11.4f} "
            f"${row['avg_arb_usd']:>10,.0f} "
            f"{row['avg_ep_dur_ms']:>9.0f}ms"
        )

    best_vol = summary.loc[summary["avg_arb_usd"].idxmax()]
    best_gap = summary.loc[summary["avg_gap_bid"].idxmax()]
    print(f"\n  Best avg gap_bid:  "
          f"{best_gap['coin'].upper()} {best_gap['market_type']}  "
          f"(avg={best_gap['avg_gap_bid']:.4f})")
    print(f"  Best avg arb size: "
          f"{best_vol['coin'].upper()} {best_vol['market_type']}  "
          f"(avg=${best_vol['avg_arb_usd']:,.0f})")


# ============================================================
# G. PER-MARKET DETAILED BREAKDOWN
# ============================================================

def analyze_per_market(df, episodes):
    print("\n" + "=" * 65)
    print("  G. PER-MARKET DETAILED BREAKDOWN")
    print("=" * 65)

    gap_bins    = [0.05, 0.07, 0.10, 0.20, float("inf")]
    gap_labels  = ["0.05-0.07", "0.07-0.10", "0.10-0.20", "0.20+    "]
    dur_bins    = [0, 100, 500, 2000, float("inf")]
    dur_labels  = ["<100ms", "100-500ms", "500ms-2s", ">2s    "]
    arb_bins    = [0, 5_000, 10_000, 20_000, float("inf")]
    arb_labels  = ["<$5k   ", "$5-$10k", "$10-$20k", ">$20k  "]
    sl_bins     = [0, 50, 100, 200, float("inf")]
    sl_labels   = ["<50s   ", "50-100s", "100-200s", ">200s  "]

    # Sort markets: 5m before 15m, alphabetical by coin
    markets = (
        df[["coin", "market_type"]]
        .drop_duplicates()
        .sort_values(["market_type", "coin"])   # 15m then 5m; alphabetical coin
        .values.tolist()
    )
    # reorder: 5m first
    markets = sorted(markets, key=lambda x: (x[1], x[0]))

    for coin, mtype in markets:
        dg = df[(df["coin"] == coin) & (df["market_type"] == mtype)].copy()
        eg = episodes[(episodes["coin"] == coin) & (episodes["market_type"] == mtype)].copy()

        if dg.empty:
            continue

        label    = f"{coin.upper()} {mtype}"
        n_snap   = len(dg)
        n_ep     = len(eg)

        print(f"\n  {label}  --  {n_ep} episodes | {n_snap:,} snapshots")
        print(f"  {'-' * 55}")

        # --- Gap bid ---
        gb = dg["gap_bid"].dropna()
        print(f"  Gap bid:   min={gb.min():.4f}  avg={gb.mean():.4f}  "
              f"max={gb.max():.4f}  median={gb.median():.4f}")
        bucketed = pd.cut(gb, bins=gap_bins, labels=gap_labels, include_lowest=True)
        parts = []
        for lbl in gap_labels:
            pct = int((bucketed == lbl).sum()) / len(gb) * 100
            parts.append(f"{lbl.strip()}: {pct:>4.1f}%")
        print(f"             {' | '.join(parts)}")

        # --- Episode duration ---
        avg_dur = eg["episode_length_ms"].mean()
        max_dur = eg["episode_length_ms"].max()
        print(f"  Duration:  avg={avg_dur:.0f}ms  max={max_dur:.0f}ms")
        dur_bucketed = pd.cut(eg["episode_length_ms"], bins=dur_bins,
                              labels=dur_labels, include_lowest=True)
        parts = []
        for lbl in dur_labels:
            pct = int((dur_bucketed == lbl).sum()) / len(eg) * 100
            parts.append(f"{lbl.strip()}: {pct:>4.1f}%")
        print(f"             {' | '.join(parts)}")

        # --- Arb size ---
        arb = dg["arb_size_usd"].dropna()
        print(f"  Arb size:  avg=${arb.mean():,.0f}  median=${arb.median():,.0f}  "
              f"max=${arb.max():,.0f}")
        arb_bucketed = pd.cut(arb, bins=arb_bins, labels=arb_labels, include_lowest=True)
        parts = []
        for lbl in arb_labels:
            pct = int((arb_bucketed == lbl).sum()) / len(arb) * 100
            parts.append(f"{lbl.strip()}: {pct:>4.1f}%")
        print(f"             {' | '.join(parts)}")

        # --- Timing (seconds_left) ---
        sl = dg["seconds_left"].dropna()
        pct_last50  = (sl < 50).sum()  / len(sl) * 100
        pct_first   = (sl > 200).sum() / len(sl) * 100
        print(f"  Timing:    avg={sl.mean():.0f}s left  "
              f"last-50s: {pct_last50:.1f}%  after-200s: {pct_first:.1f}%")
        sl_bucketed = pd.cut(sl, bins=sl_bins, labels=sl_labels, include_lowest=True)
        parts = []
        for lbl in sl_labels:
            pct = int((sl_bucketed == lbl).sum()) / len(sl) * 100
            parts.append(f"{lbl.strip()}: {pct:>4.1f}%")
        print(f"             {' | '.join(parts)}")


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 65)
    print("  GAP LOG ANALYSIS")
    print("=" * 65)

    df = load()
    if df is None:
        return

    df, episodes = build_episodes(df)

    analyze_summary(df, episodes)        # A
    analyze_gap_distribution(df)         # B
    analyze_duration(df, episodes)       # C
    analyze_arb_size(df)                 # D
    analyze_timing(df)                   # E
    analyze_coin_market(df, episodes)    # F
    analyze_per_market(df, episodes)     # G

    print("\n" + "=" * 65)
    print("  END OF ANALYSIS")
    print("=" * 65)


if __name__ == "__main__":
    main()
