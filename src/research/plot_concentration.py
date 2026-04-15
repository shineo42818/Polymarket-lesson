"""
plot_concentration.py

Two-panel figure for YC proposal:
  Left  — Top 15 markets by 24h volume (horizontal bar, colored by category)
  Right — Cumulative Pareto curve with key markers
"""

import sys
import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.patches as mpatches
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CSV_PATH  = Path(__file__).parents[2] / "data" / "volume_concentration.csv"
OUT_PATH  = Path(__file__).parents[2] / "data" / "volume_concentration_chart.png"
TOP_N     = 15

CATEGORY_COLORS = {
    "Crypto":      "#F7931A",
    "Politics":    "#3B6FE0",
    "Sports":      "#2ECC71",
    "Finance":     "#9B59B6",
    "AI/Tech":     "#E74C3C",
    "Pop Culture": "#F39C12",
    "World":       "#1ABC9C",
    "Science":     "#95A5A6",
    "Other":       "#BDC3C7",
}


EXCLUDE_SLUGS      = {"military-action-against-iran-ends-by-april-17-2026"}
EXCLUDE_CATEGORIES = {"Crypto"}


def load_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [
        r for r in rows
        if r["slug"] not in EXCLUDE_SLUGS
        and r.get("status", "active") == "active"
        and r.get("category", "") not in EXCLUDE_CATEGORIES
    ]


def shorten(text: str, max_len: int = 48) -> str:
    return text if len(text) <= max_len else text[:max_len - 1] + "…"


def main() -> None:
    rows = load_csv(CSV_PATH)
    # CSV is already sorted by volume_24h desc
    all_vols = [float(r["volume_24h"]) for r in rows]
    total_vol = sum(all_vols)

    top15 = rows[:TOP_N]

    # ── Figure layout ──────────────────────────────────────────────────────────
    fig, (ax_bar, ax_pareto) = plt.subplots(
        1, 2,
        figsize=(16, 7),
        gridspec_kw={"width_ratios": [1.1, 1]},
    )
    fig.patch.set_facecolor("#0F1117")
    for ax in (ax_bar, ax_pareto):
        ax.set_facecolor("#0F1117")
        ax.tick_params(colors="#CCCCCC")
        ax.spines[:].set_color("#333333")
        for spine in ax.spines.values():
            spine.set_color("#333333")

    label_color = "#CCCCCC"
    title_color = "#FFFFFF"
    accent_color = "#F7931A"

    # ── LEFT: Horizontal bar chart ─────────────────────────────────────────────
    labels   = [shorten(r["question"] or r["slug"]) for r in top15]
    values   = [float(r["volume_24h"]) for r in top15]
    colors   = [CATEGORY_COLORS.get(r["category"], "#BDC3C7") for r in top15]
    top15_pct = sum(values) / total_vol * 100

    y_pos = np.arange(TOP_N)
    bars = ax_bar.barh(y_pos, values, color=colors, height=0.65, edgecolor="none")

    # Value labels on bars
    for bar, val in zip(bars, values):
        ax_bar.text(
            bar.get_width() + total_vol * 0.002,
            bar.get_y() + bar.get_height() / 2,
            f"${val/1e6:.1f}M",
            va="center", ha="left",
            fontsize=8, color=label_color,
        )

    ax_bar.set_yticks(y_pos)
    ax_bar.set_yticklabels(labels, fontsize=8.5, color=label_color)
    ax_bar.invert_yaxis()
    ax_bar.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x/1e6:.0f}M"))
    ax_bar.tick_params(axis="x", colors=label_color, labelsize=8)
    ax_bar.set_xlabel("24h Volume (USD)", color=label_color, fontsize=9)
    ax_bar.set_title(
        f"Top {TOP_N} Markets by 24h Volume\n"
        f"({top15_pct:.1f}% of total ${total_vol/1e6:.0f}M daily volume)",
        color=title_color, fontsize=11, pad=10,
    )
    ax_bar.set_xlim(0, max(values) * 1.18)

    # Category legend
    seen_cats = []
    legend_patches = []
    for r in top15:
        cat = r["category"]
        if cat not in seen_cats:
            seen_cats.append(cat)
            legend_patches.append(
                mpatches.Patch(color=CATEGORY_COLORS.get(cat, "#BDC3C7"), label=cat)
            )
    ax_bar.legend(
        handles=legend_patches, loc="lower right",
        fontsize=8, framealpha=0.15,
        labelcolor=label_color, facecolor="#1A1D27", edgecolor="#444444",
    )

    # ── RIGHT: Pareto cumulative curve ─────────────────────────────────────────
    cumulative = np.cumsum(all_vols) / total_vol * 100
    ranks = np.arange(1, len(all_vols) + 1)

    # Only plot up to where cumulative hits 99.9% to keep curve meaningful
    cutoff_idx = int(np.searchsorted(cumulative, 99.9))
    plot_ranks = ranks[:cutoff_idx]
    plot_cum   = cumulative[:cutoff_idx]

    ax_pareto.plot(plot_ranks, plot_cum, color=accent_color, linewidth=2)
    ax_pareto.fill_between(plot_ranks, plot_cum, alpha=0.12, color=accent_color)

    # Marker: top 15
    top15_cum = cumulative[TOP_N - 1]
    ax_pareto.axvline(TOP_N, color="#FFFFFF", linestyle="--", linewidth=1, alpha=0.5)
    ax_pareto.axhline(top15_cum, color="#FFFFFF", linestyle="--", linewidth=1, alpha=0.5)
    ax_pareto.scatter([TOP_N], [top15_cum], color="#FFFFFF", zorder=5, s=60)
    ax_pareto.annotate(
        f"Top {TOP_N} markets\n{top15_cum:.1f}% of volume",
        xy=(TOP_N, top15_cum),
        xytext=(TOP_N + cutoff_idx * 0.05, top15_cum - 12),
        color="#FFFFFF", fontsize=9,
        arrowprops=dict(arrowstyle="->", color="#FFFFFF", lw=1),
    )

    # Marker: top 1% (~476 markets)
    top1pct_rank = max(1, round(len(all_vols) * 0.01))
    top1pct_cum  = cumulative[top1pct_rank - 1]
    ax_pareto.scatter([top1pct_rank], [top1pct_cum], color=accent_color, zorder=5, s=60)
    ax_pareto.annotate(
        f"Top 1% ({top1pct_rank} markets)\n{top1pct_cum:.1f}% of volume",
        xy=(top1pct_rank, top1pct_cum),
        xytext=(top1pct_rank + cutoff_idx * 0.08, top1pct_cum - 16),
        color=accent_color, fontsize=9,
        arrowprops=dict(arrowstyle="->", color=accent_color, lw=1),
    )

    ax_pareto.set_xlim(0, cutoff_idx)
    ax_pareto.set_ylim(0, 102)
    ax_pareto.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))
    ax_pareto.yaxis.set_major_formatter(mticker.FuncFormatter(lambda y, _: f"{y:.0f}%"))
    ax_pareto.tick_params(axis="both", colors=label_color, labelsize=8)
    ax_pareto.set_xlabel("Market rank (by 24h volume)", color=label_color, fontsize=9)
    ax_pareto.set_ylabel("Cumulative % of total volume", color=label_color, fontsize=9)
    ax_pareto.set_title(
        f"Volume Concentration — Pareto Curve\n"
        f"({len(all_vols):,} active markets, ${total_vol/1e6:.0f}M total 24h)",
        color=title_color, fontsize=11, pad=10,
    )

    # 90% marker — how many markets reach 90% of volume
    rank_90 = int(np.searchsorted(cumulative, 90)) + 1
    pct_markets_90 = rank_90 / len(all_vols) * 100
    ax_pareto.axvline(rank_90, color="#2ECC71", linestyle="--", linewidth=1, alpha=0.6)
    ax_pareto.axhline(90, color="#2ECC71", linestyle="--", linewidth=1, alpha=0.6)
    ax_pareto.scatter([rank_90], [90], color="#2ECC71", zorder=5, s=60)
    ax_pareto.annotate(
        f"{rank_90} markets ({pct_markets_90:.1f}%)\nreach 90% of volume",
        xy=(rank_90, 90),
        xytext=(rank_90 + cutoff_idx * 0.07, 90 - 14),
        color="#2ECC71", fontsize=9,
        arrowprops=dict(arrowstyle="->", color="#2ECC71", lw=1),
    )
    print(f"Markets needed for 90% of volume: {rank_90} ({pct_markets_90:.2f}% of all markets)")

    # 95% reference line
    ax_pareto.axhline(95, color="#555555", linestyle=":", linewidth=1)
    ax_pareto.text(cutoff_idx * 0.97, 95.8, "95%", color="#888888", fontsize=8, ha="right")

    # ── Footer ─────────────────────────────────────────────────────────────────
    fig.text(
        0.5, 0.01,
        "Source: Polymarket Gamma API  |  Active & started markets only  |  Data as of today  |  "
        "* Crypto category excluded (high-frequency 5m/15m markets)  |  "
        "* Outlier excluded: 'Military action against Iran ends by Apr 17' ($6.8M)",
        ha="center", fontsize=7.5, color="#666666",
    )

    plt.tight_layout(rect=[0, 0.03, 1, 1])
    plt.savefig(OUT_PATH, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"Chart saved -> {OUT_PATH}")


if __name__ == "__main__":
    main()
