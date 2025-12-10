import os
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from kiteconnect import KiteConnect

load_dotenv()

API_KEY = os.getenv('KITE_API_KEY')
API_SECRET = os.getenv('KITE_API_SECRET')

print("=" * 70)
print("MCX COMMODITY DATA DEBUG TEST")
print("=" * 70)

kite = KiteConnect(api_key=API_KEY)

# Load access token
with open('kite_access_token.json', 'r') as f:
    data = json.load(f)
    kite.set_access_token(data['access_token'])

# Verify connection
try:
    profile = kite.profile()
    print(f"\n✅ Connected as: {profile.get('user_name', 'Unknown')}")
    print(f"🔐 User ID: {profile.get('user_id', 'Unknown')}")
    print(f"📊 Exchanges: {profile.get('exchanges', [])}")
except Exception as e:
    print(f"❌ Profile fetch error: {e}")
    profile = {}

# Step 1: Fetch MCX instruments
print("\n" + "=" * 70)
print("STEP 1: Fetching MCX Instruments")
print("=" * 70)

try:
    instruments = kite.instruments("MCX")
    print(f"✅ Total MCX instruments: {len(instruments)}")
    
    # Find commodity symbols
    commodities = {}
    for inst in instruments:
        name = inst.get('name')
        token = inst.get('instrument_token')
        expiry = inst.get('expiry')
        segment = inst.get('segment')
        
        if name and token and 'FUT' in segment:
            if name not in commodities:
                commodities[name] = []
            commodities[name].append({
                'token': token,
                'expiry': expiry,
                'segment': segment,
                'tradingsymbol': inst.get('tradingsymbol')
            })
    
    print(f"✅ Found {len(commodities)} commodity symbols:")
    print("-" * 70)
    
    # Show each commodity with available contracts
    for symbol in sorted(commodities.keys())[:15]:  # First 15
        contracts = commodities[symbol]
        print(f"\n📦 {symbol}:")
        for i, contract in enumerate(contracts[:3]):  # Show first 3 expiry
            print(f"   [{i+1}] Token: {contract['token']:10} | Expiry: {contract['expiry']} | {contract['tradingsymbol']}")
    
except Exception as e:
    print(f"❌ ERROR fetching instruments: {e}")
    import traceback
    traceback.print_exc()
    commodities = {}

# Step 2: Test data fetch for each commodity
print("\n" + "=" * 70)
print("STEP 2: Testing Historical Data Fetch")
print("=" * 70)

now = datetime.now()
from_date = (now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
to_date = now.strftime("%Y-%m-%d %H:%M:%S")

print(f"Time Range: {from_date} to {to_date}")
print(f"Continuous: False (required for MCX)")
print("-" * 70)

# Test top 5 commodities
test_symbols = ['CRUDEOIL', 'GOLD', 'SILVER', 'COPPER', 'ZINC']

results = {}

for symbol in test_symbols:
    if symbol not in commodities:
        print(f"⏭️  {symbol}: Not found in MCX instruments")
        continue
    
    # Use the first (nearest) expiry
    contract = commodities[symbol][0]
    token = contract['token']
    expiry = contract['expiry']
    
    print(f"\n🔍 {symbol}:")
    print(f"   Token: {token}")
    print(f"   Expiry: {expiry}")
    print(f"   Trading Symbol: {contract['tradingsymbol']}")
    
    try:
        history = kite.historical_data(
            token,
            from_date,
            to_date,
            '5minute',
            continuous=False
        )
        
        num_bars = len(history)
        results[symbol] = num_bars
        
        print(f"   Result: {num_bars} bars", end="")
        
        if num_bars > 0:
            last_price = history[-1].get('close', 0)
            print(f" ✅ (Last Price: {last_price:.2f})")
        else:
            print(f" ❌ (0 bars)")
            
    except Exception as e:
        print(f"   ❌ ERROR: {e}")
        results[symbol] = -1

# Step 3: Show current time info
print("\n" + "=" * 70)
print("STEP 3: Time Information")
print("=" * 70)

now = datetime.now()
current_minutes = now.hour * 60 + now.minute

print(f"Current Time: {now.strftime('%Y-%m-%d %H:%M:%S')} IST")
print(f"Current Minutes: {current_minutes}")

MCX_OPEN = 9 * 60
MCX_CLOSE = 23 * 60 + 55

if MCX_OPEN <= current_minutes <= MCX_CLOSE:
    print(f"🟢 MCX is OPEN (9:00 AM - 11:55 PM)")
else:
    print(f"🔴 MCX is CLOSED")
    minutes_until_open = MCX_OPEN - current_minutes
    if minutes_until_open < 0:
        minutes_until_open += 24*60
    hours = minutes_until_open // 60
    mins = minutes_until_open % 60
    print(f"   Opens in: {hours}h {mins}m")

# Step 4: Summary
print("\n" + "=" * 70)
print("STEP 4: Summary")
print("=" * 70)

print("\nData Fetch Results:")
for symbol, bars in results.items():
    if bars > 0:
        print(f"  ✅ {symbol}: {bars} bars")
    elif bars == 0:
        print(f"  ❌ {symbol}: 0 bars")
    else:
        print(f"  ❌ {symbol}: Error")

if any(bars > 0 for bars in results.values()):
    print(f"\n✅ SUCCESS! Your bot should work fine.")
    print(f"   Tokens are valid and data is available.")
else:
    print(f"\n⚠️  NO DATA RETRIEVED")
    if MCX_OPEN <= current_minutes <= MCX_CLOSE:
        print(f"   Market is OPEN but no data!")
        print(f"   Possible issues:")
        print(f"   1. Tokens might be stale")
        print(f"   2. API rate limited")
        print(f"   3. No trades in time window")
    else:
        print(f"   Market is CLOSED!")
        print(f"   Come back during 9 AM - 11:55 PM IST")

print("\n" + "=" * 70)