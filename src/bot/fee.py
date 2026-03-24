"""
Polymarket fee model for crypto up/down markets.

Polymarket fee structure:
  - TAKER fee: 0.25 * (p * (1-p))^2  (max 1.5625% at p=0.50)
  - MAKER fee: 0% on crypto markets (as of 2026-03-06)

Fee is deducted from shares received, not USDC paid.
"""


def taker_fee_per_share(price: float) -> float:
    """Taker fee rate at a given price. Applied when you HIT the ask (market order)."""
    return 0.25 * (price * (1.0 - price)) ** 2


def maker_fee_per_share(price: float) -> float:
    """Maker fee rate. Zero on Polymarket crypto markets."""
    return 0.0


def net_shares_taker(usdc: float, ask_price: float) -> float:
    """Shares received when TAKING (market order at ask). Fee deducted from tokens."""
    fee_rate = taker_fee_per_share(ask_price)
    return (usdc / ask_price) * (1.0 - fee_rate)


def net_shares_maker(usdc: float, bid_price: float) -> float:
    """Shares received when MAKING (limit order at bid). No fee on crypto markets."""
    return usdc / bid_price


def taker_arb_profit(yes_ask: float, no_ask: float, usdc: float) -> float:
    """
    Taker arb: buy YES at yes_ask + NO at no_ask (market orders).
    Cost = usdc. Payout = hedged_tokens * $1.00.
    Returns profit in USDC (usually NEGATIVE -- taker arb rarely works).
    """
    half = usdc / 2.0
    yes_tokens = net_shares_taker(half, yes_ask)
    no_tokens = net_shares_taker(half, no_ask)
    hedged = min(yes_tokens, no_tokens)
    # Hedged tokens pay $1.00 at settlement
    payout = hedged * 1.0
    return payout - usdc


def maker_arb_profit(yes_bid: float, no_bid: float, usdc: float) -> tuple[float, float, float, float]:
    """
    Maker arb: post limit buy YES at yes_bid + NO at no_bid (limit orders).
    Zero fee for maker. IF both fill, profit is guaranteed.

    KEY: We buy EQUAL token counts (not equal USDC) to maximize hedging.
    Cost per pair = yes_bid + no_bid. Payout per pair = $1.00.
    Profit per pair = 1.0 - yes_bid - no_bid = gap_bid.

    Returns (profit, num_pairs, yes_usdc, no_usdc).
    """
    cost_per_pair = yes_bid + no_bid  # cost to buy 1 YES + 1 NO
    num_pairs = usdc / cost_per_pair  # how many full pairs we can buy
    yes_usdc = num_pairs * yes_bid    # USDC spent on YES side
    no_usdc = num_pairs * no_bid      # USDC spent on NO side
    payout = num_pairs * 1.0          # one token in each pair pays $1
    profit = payout - usdc            # = num_pairs * gap_bid
    return profit, num_pairs, yes_usdc, no_usdc


# Legacy aliases for backward compatibility
fee_per_share = taker_fee_per_share
net_shares = net_shares_taker
