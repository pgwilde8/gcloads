def calculate_referral_payout(load_value: float) -> float:
    # 1. Calculate our 2.5% dispatch fee
    gcd_fee = load_value * 0.025
    
    # 2. Calculate the 10% referral share
    raw_bounty = gcd_fee * 0.10
    
    # 3. Apply the $5.00 Ceiling
    final_bounty = min(raw_bounty, 5.00)
    
    return round(final_bounty, 2)