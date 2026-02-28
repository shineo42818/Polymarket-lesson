import requests
import pandas as pd

token_id = "66661729852136393273263759071389675840539333884525023142990717159920280337640"

r = requests.get("https://clob.polymarket.com/prices-history", params={
    "market": token_id,
    "interval": "1m",
    "fidelity": 10
})

history = r.json().get("history", [])
df = pd.DataFrame(history)
df["time"] = pd.to_datetime(df["t"], unit="s", utc=True)
df = df[["time", "p"]].rename(columns={"p": "yes_price"})

print(f"Data points: {len(df)}")
print(df.tail(20).to_string())  # show last 20 — most recent activity