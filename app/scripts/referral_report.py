import argparse
import psycopg2
from decimal import Decimal

def get_referral_stats(driver_id):
    # Connection logic for gcloads_db
    conn = psycopg2.connect("host=localhost dbname=gcloads_db user=gcd_admin password=YOUR_PASSWORD port=5432")
    cur = conn.cursor()

    # 1. Get Referral Count
    cur.execute("SELECT COUNT(*) FROM drivers WHERE referred_by_id = %s", (driver_id,))
    total_referrals = cur.fetchone()[0]

    # 2. Get Earnings Stats
    cur.execute("""
        SELECT 
            SUM(amount) FILTER (WHERE status = 'AVAILABLE') as available,
            SUM(amount) FILTER (WHERE status = 'PENDING') as pending,
            COUNT(*) as total_loads
        FROM referral_earnings 
        WHERE referrer_id = %s
    """, (driver_id,))
    
    available, pending, total_loads = cur.fetchone()

    # Handle None values from SUM
    available = available or Decimal('0.00')
    pending = pending or Decimal('0.00')

    print(f"\n🚀 GREEN CANDLE REFERRAL REPORT | Driver ID: {driver_id}")
    print("-" * 45)
    print(f"👥 Total Drivers Referred:   {total_referrals}")
    print(f"📦 Total Loads Moved:        {total_loads or 0}")
    print("-" * 45)
    print(f"💰 PENDING EARNINGS:         ${pending:,.2f}")
    print(f"💳 AVAILABLE TO CLAIM:       ${available:,.2f}")
    print("-" * 45)
    
    if total_referrals > 0:
        avg_per_ref = (available + pending) / total_referrals
        print(f"📈 Avg. Monthly Value/Ref:   ${avg_per_ref:,.2f}")
        print(f"\nPROJECTION: If you refer 10 drivers doing 4k/week,")
        print(f"you could be looking at ~$200.00/month in PASSIVE INCOME.")
    
    cur.close()
    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--driver_id", type=int, required=True)
    args = parser.parse_args()
    get_referral_stats(args.driver_id)