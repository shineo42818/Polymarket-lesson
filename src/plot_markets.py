# plot_markets.py — Visualize top Polymarket markets by volume

import pandas as pd
import matplotlib.pyplot as plt

# Read the data we saved earlier
df = pd.read_csv("data/markets.csv")

# Take the top 15 markets
top = df.head(15).copy()

# Shorten long question names so they fit on the chart
top["short_name"] = top["question"].apply(lambda x: x[:50] + "..." if len(x) > 50 else x)

# Create the chart
fig, ax = plt.subplots(figsize=(12, 8))

# Horizontal bar chart (easier to read market names)
bars = ax.barh(range(len(top)), top["volume"], color="#6366f1")

# Add labels
ax.set_yticks(range(len(top)))
ax.set_yticklabels(top["short_name"], fontsize=9)
ax.set_xlabel("Trading Volume ($)")
ax.set_title("Top 15 Polymarket Markets by Volume", fontsize=14, fontweight="bold")

# Flip so #1 is at the top
ax.invert_yaxis()

# Make it look clean
plt.tight_layout()

# Save the chart
plt.savefig("charts/top_markets.png", dpi=150)
print("Chart saved to charts/top_markets.png")

# Also show it on screen
plt.show()