"""
volume_concentration.py

Validates hypothesis: majority of Polymarket daily volume is concentrated
in a small fraction of markets (Pareto / Lorenz analysis).

Outputs:
  - Console summary: overall concentration stats + per-category breakdown
  - data/volume_concentration.csv: raw market data for further analysis
"""

import asyncio
import csv
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

GAMMA_API = "https://gamma-api.polymarket.com/markets"
PAGE_SIZE = 500
OUTPUT_CSV = Path(__file__).parents[2] / "data" / "volume_concentration.csv"


async def fetch_all_markets(client: httpx.AsyncClient) -> list[dict]:
    """Paginate through all Gamma API markets."""
    markets = []
    offset = 0
    print("Fetching markets from Gamma API...")
    while True:
        resp = await client.get(
            GAMMA_API,
            params={"limit": PAGE_SIZE, "offset": offset, "active": "true"},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        markets.extend(batch)
        print(f"  fetched {len(markets)} markets so far...", end="\r")
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    print(f"\nTotal markets fetched: {len(markets)}")
    return markets


def parse_market(m: dict) -> dict | None:
    """Extract fields we care about; return None to skip."""
    # Skip closed, archived, or inactive markets
    if m.get("closed") or m.get("archived") or m.get("active") is False:
        return None

    now = datetime.now(timezone.utc)

    # Skip markets whose end date has already passed
    end_date_str = m.get("endDate") or m.get("end_date_iso")
    if end_date_str:
        try:
            end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            if (end_dt - now).total_seconds() <= 0:
                return None
        except ValueError:
            pass

    # Determine status: "pending" if the event start time is in the future
    # or if the market is not yet accepting orders.
    # eventStartTime = when the trading window actually opens (key for 5m/15m crypto markets).
    status = "active"
    event_start_str = m.get("eventStartTime") or m.get("game_start_time")
    if event_start_str:
        try:
            event_start = datetime.fromisoformat(event_start_str.replace("Z", "+00:00"))
            if event_start > now:
                status = "pending"
        except ValueError:
            pass
    if m.get("acceptingOrders") is False:
        status = "pending"

    vol24h = float(m.get("volume24hr") or 0)
    vol_total = float(m.get("volume") or 0)

    # Derive category from tags or question text
    tags = [t.get("label", "").lower() for t in (m.get("tags") or [])]
    category = categorize(tags, m.get("question") or m.get("slug") or "")

    return {
        "id": m.get("id", ""),
        "slug": m.get("slug", ""),
        "question": (m.get("question") or "")[:80],
        "category": category,
        "status": status,
        "volume_24h": vol24h,
        "volume_total": vol_total,
        "end_date": end_date_str or "",
        "event_start_time": event_start_str or "",
    }


CATEGORY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("Crypto",      ["crypto", "bitcoin", "btc", "eth", "ethereum", "sol", "solana",
                     "xrp", "doge", "defi", "nft", "coin", "token", "blockchain"]),
    ("Politics",    ["election", "president", "senate", "congress", "democrat",
                     "republican", "vote", "political", "govern", "trump", "biden",
                     "harris", "policy", "minister", "parliament"]),
    ("Sports",      ["nfl", "nba", "mlb", "nhl", "soccer", "football", "basketball",
                     "baseball", "tennis", "golf", "ufc", "mma", "f1", "formula",
                     "olympic", "world cup", "champion", "league", "match"]),
    ("Finance",     ["stock", "s&p", "nasdaq", "fed", "interest rate", "gdp",
                     "inflation", "market cap", "ipo", "earnings"]),
    ("AI/Tech",     ["ai", "gpt", "openai", "anthropic", "llm", "tech", "apple",
                     "google", "microsoft", "meta", "nvidia", "spacex"]),
    ("Pop Culture", ["oscars", "grammy", "emmy", "award", "celebrity", "movie",
                     "music", "album", "box office", "tv show", "streaming"]),
    ("Science",     ["nasa", "climate", "earthquake", "hurricane", "space",
                     "scientific", "covid", "vaccine", "health"]),
    ("World",       ["war", "conflict", "ceasefire", "ukraine", "russia", "china",
                     "israel", "nato", "un ", "united nations", "treaty"]),
]


def categorize(tags: list[str], text: str) -> str:
    combined = " ".join(tags) + " " + text.lower()
    for category, keywords in CATEGORY_KEYWORDS:
        if any(kw in combined for kw in keywords):
            return category
    return "Other"


def lorenz_stats(volumes: list[float]) -> list[tuple[int, float, float]]:
    """
    Returns list of (percentile_cutoff, pct_of_markets, pct_of_volume)
    for cutoffs [1, 5, 10, 20, 50].
    """
    total = sum(volumes)
    if total == 0:
        return []
    n = len(volumes)
    sorted_vols = sorted(volumes, reverse=True)
    results = []
    for pct in [1, 5, 10, 20, 50]:
        top_n = max(1, round(n * pct / 100))
        top_vol = sum(sorted_vols[:top_n])
        results.append((pct, top_n, top_vol / total * 100))
    return results


def print_concentration(label: str, volumes: list[float], indent: str = "") -> None:
    n = len(volumes)
    total = sum(volumes)
    zero_count = sum(1 for v in volumes if v == 0)
    low_count = sum(1 for v in volumes if 0 < v < 100)
    print(f"{indent}{label}: {n} markets, ${total:,.0f} total 24h volume")
    print(f"{indent}  Zero volume:     {zero_count:4d} ({zero_count/n*100:.1f}%)")
    print(f"{indent}  < $100 volume:   {low_count:4d} ({low_count/n*100:.1f}%)")
    stats = lorenz_stats(volumes)
    for pct_markets, top_n, pct_vol in stats:
        print(f"{indent}  Top {pct_markets:2d}% ({top_n:4d} markets) -> {pct_vol:.1f}% of volume")


def save_csv(markets: list[dict]) -> None:
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fields = ["id", "slug", "question", "category", "status", "volume_24h", "volume_total", "end_date", "event_start_time"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(sorted(markets, key=lambda m: m["volume_24h"], reverse=True))
    print(f"\nCSV saved -> {OUTPUT_CSV}")


def rank_at_pct_volume(volumes_sorted_desc: list[float], target_pct: float) -> int:
    """Return how many top markets are needed to reach target_pct% of total volume."""
    total = sum(volumes_sorted_desc)
    if total == 0:
        return 0
    cumsum = 0.0
    for i, v in enumerate(volumes_sorted_desc, 1):
        cumsum += v
        if cumsum / total * 100 >= target_pct:
            return i
    return len(volumes_sorted_desc)


def print_summary_block(label: str, markets: list[dict]) -> None:
    n = len(markets)
    vols = sorted([m["volume_24h"] for m in markets], reverse=True)
    total = sum(vols)
    zero  = sum(1 for v in vols if v == 0)
    low   = sum(1 for v in vols if 0 < v < 100)

    print(f"\n{'=' * 65}")
    print(f"  {label}")
    print(f"{'=' * 65}")
    print(f"  Total markets:      {n:>7,}")
    print(f"  Total 24h volume:   ${total:>14,.0f}")
    print(f"  Zero-volume mkts:   {zero:>7,}  ({zero/n*100:.1f}%)")
    print(f"  < $100 volume:      {low:>7,}  ({low/n*100:.1f}%)")
    print()

    stats = lorenz_stats(vols)
    print(f"  {'Top % of markets':<25} {'# markets':>10}  {'% of volume':>12}")
    print(f"  {'-'*25} {'-'*10}  {'-'*12}")
    for pct_markets, top_n, pct_vol in stats:
        print(f"  Top {pct_markets:2d}%{'':<20} {top_n:>10,}  {pct_vol:>11.1f}%")

    print()
    for target in [80, 90, 95, 99]:
        r = rank_at_pct_volume(vols, target)
        print(f"  Markets to reach {target}% of volume: {r:>6,}  ({r/n*100:.2f}% of all markets)")


async def main() -> None:
    async with httpx.AsyncClient() as client:
        raw = await fetch_all_markets(client)

    markets = [p for m in raw if (p := parse_market(m)) is not None]

    active_markets  = [m for m in markets if m["status"] == "active"]
    pending_markets = [m for m in markets if m["status"] == "pending"]

    print(f"\nMarkets parsed:     {len(markets):,}")
    print(f"  Active (started): {len(active_markets):,}")
    print(f"  Pending:          {len(pending_markets):,}  (eventStartTime in future / not accepting orders)")

    # ── Scenario A: Active + Pending ──────────────────────────────────────────
    print_summary_block("SCENARIO A — All markets (Active + Pending)", markets)

    # ── Scenario B: Active only ───────────────────────────────────────────────
    print_summary_block("SCENARIO B — Active only (Pending excluded)", active_markets)

    # ── Scenario C: Active only, no Crypto ────────────────────────────────────
    active_no_crypto = [m for m in active_markets if m["category"] != "Crypto"]
    print_summary_block("SCENARIO C — Active only, Crypto excluded", active_no_crypto)

    # ── Side-by-side comparison table ─────────────────────────────────────────
    print(f"\n{'=' * 65}")
    print("  SIDE-BY-SIDE COMPARISON")
    print(f"{'=' * 65}")
    scenarios = [
        ("A: All (incl. pending)", markets),
        ("B: Active only",         active_markets),
        ("C: Active, no Crypto",   active_no_crypto),
    ]
    headers = ["Scenario", "# Markets", "Total Vol", "Top1%->Vol", "Mkts@90%", "Mkts@95%"]
    print(f"  {headers[0]:<26} {headers[1]:>10} {headers[2]:>12} {headers[3]:>11} {headers[4]:>9} {headers[5]:>9}")
    print(f"  {'-'*26} {'-'*10} {'-'*12} {'-'*11} {'-'*9} {'-'*9}")
    for name, mkt_list in scenarios:
        vols_s = sorted([m["volume_24h"] for m in mkt_list], reverse=True)
        total  = sum(vols_s)
        n      = len(vols_s)
        top1n  = max(1, round(n * 0.01))
        top1v  = sum(vols_s[:top1n]) / total * 100 if total else 0
        m90    = rank_at_pct_volume(vols_s, 90)
        m95    = rank_at_pct_volume(vols_s, 95)
        print(f"  {name:<26} {n:>10,} ${total:>11,.0f} {top1v:>10.1f}% {m90:>9,} {m95:>9,}")

    # ── Per-category breakdown (active only) ──────────────────────────────────
    print(f"\n{'=' * 65}")
    print("  CATEGORY BREAKDOWN  (active markets only)")
    print(f"{'=' * 65}")
    by_cat: dict[str, list[float]] = defaultdict(list)
    for m in active_markets:
        by_cat[m["category"]].append(m["volume_24h"])
    grand_total = sum(m["volume_24h"] for m in active_markets)
    for cat, _ in sorted(by_cat.items(), key=lambda x: sum(x[1]), reverse=True):
        vols = by_cat[cat]
        share = sum(vols) / grand_total * 100 if grand_total else 0
        n_cat = len(vols)
        zero  = sum(1 for v in vols if v == 0)
        top1n = max(1, round(n_cat * 0.01))
        top1v = sum(sorted(vols, reverse=True)[:top1n]) / sum(vols) * 100 if sum(vols) else 0
        m90   = rank_at_pct_volume(sorted(vols, reverse=True), 90)
        print(f"  {cat:<12}  {n_cat:>6,} mkts  ${sum(vols):>12,.0f}  {share:>5.1f}% of total  "
              f"zero={zero/n_cat*100:.0f}%  top1%->{top1v:.0f}%vol  @90%:{m90}mkts")

    save_csv(markets)


if __name__ == "__main__":
    asyncio.run(main())
