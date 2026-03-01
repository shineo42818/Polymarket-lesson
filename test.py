import pandas as pd

df = pd.read_csv("data/polymarket_btc.csv")
print(df.head(10).to_string())
print("\nGap analysis:")
df["gap"] = 1.0 - df["yes_price"] - df["no_price"]
print(df["gap"].describe())