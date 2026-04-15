# check_api.py — Verify that our API keys are working
# Add more checks here as we get more API keys

import requests
import os
from dotenv import load_dotenv

# Load the .env file
load_dotenv()

def check_etherscan():
    """Test if the Etherscan API key works."""
    
    key = os.getenv("ETHERSCAN_API_KEY")
    
    if not key:
        print("[FAIL] ETHERSCAN_API_KEY not found in .env file")
        return False
    
    print(f"[OK] Found Etherscan key: {key[:6]}...{key[-4:]}")
    
    # Make a simple test call — get the latest Ethereum block number
    url = "https://api.etherscan.io/v2/api"
    params = {
        "module": "proxy",
        "action": "eth_blockNumber",
        "apikey": key,
        "chainid": 1
    }
    
    response = requests.get(url, params=params)
    data = response.json()
    
    if data.get("result") and not data.get("result", "").startswith("Error"):
        # Convert hex block number to regular number
        block = int(data["result"], 16)
        print(f"[OK] Etherscan API working! Latest block: {block}")
        return True
    else:
        print(f"[FAIL] Etherscan returned: {data}")
        return False


if __name__ == "__main__":
    print("=" * 50)
    print("API KEY CHECK")
    print("=" * 50)
    print()
    
    check_etherscan()
    
    print()
    print("--- Future checks (not yet configured) ---")
    print("[ ] Polygonscan API key")
    print("[ ] Dune Analytics API key (optional)")