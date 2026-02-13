import sys
import datetime
import math
from datetime import date
# Mock required modules/globals if needed before importing trading_bot_live
# (Assuming trading_bot_live can be imported without immediate side effects)

from trading_bot_live import UltimateFNOTrader

# Mock Kite
class MockKite:
    def instruments(self, exchange):
        # Return mock MCX instruments
        return [
            # CALLS
            {'instrument_token': 101, 'exchange': 'MCX', 'tradingsymbol': 'SILVERM24FEB89500CE', 'name': 'SILVERM', 'expiry': date(2024, 2, 28), 'strike': 89500, 'tick_size': 1, 'lot_size': 5, 'instrument_type': 'CE', 'segment': 'MCX-OPT'},
            {'instrument_token': 102, 'exchange': 'MCX', 'tradingsymbol': 'SILVERM24FEB90000CE', 'name': 'SILVERM', 'expiry': date(2024, 2, 28), 'strike': 90000, 'tick_size': 1, 'lot_size': 5, 'instrument_type': 'CE', 'segment': 'MCX-OPT'},
            {'instrument_token': 103, 'exchange': 'MCX', 'tradingsymbol': 'SILVERM24FEB90500CE', 'name': 'SILVERM', 'expiry': date(2024, 2, 28), 'strike': 90500, 'tick_size': 1, 'lot_size': 5, 'instrument_type': 'CE', 'segment': 'MCX-OPT'},
            
            # PUTS
            {'instrument_token': 201, 'exchange': 'MCX', 'tradingsymbol': 'SILVERM24FEB90000PE', 'name': 'SILVERM', 'expiry': date(2024, 2, 28), 'strike': 90000, 'tick_size': 1, 'lot_size': 5, 'instrument_type': 'PE', 'segment': 'MCX-OPT'},
            {'instrument_token': 202, 'exchange': 'MCX', 'tradingsymbol': 'SILVERM24FEB90500PE', 'name': 'SILVERM', 'expiry': date(2024, 2, 28), 'strike': 90500, 'tick_size': 1, 'lot_size': 5, 'instrument_type': 'PE', 'segment': 'MCX-OPT'},
            {'instrument_token': 203, 'exchange': 'MCX', 'tradingsymbol': 'SILVERM24FEB91000PE', 'name': 'SILVERM', 'expiry': date(2024, 2, 28), 'strike': 91000, 'tick_size': 1, 'lot_size': 5, 'instrument_type': 'PE', 'segment': 'MCX-OPT'},
        ]

    def quote(self, keys):
        # Mock quotes
        res = {}
        for k in keys:
            # Assume key is MCX:SYMBOL
            price = 100.0
            if '89500CE' in k: price = 1500.0 # Deep ITM
            if '90000CE' in k: price = 1000.0 # ITM
            if '90500CE' in k: price = 500.0  # OTM
            
            if '91000PE' in k: price = 1500.0 # Deep ITM
            if '90500PE' in k: price = 1000.0 # ITM
            if '90000PE' in k: price = 500.0  # OTM
            
            res[k] = {
                'last_price': price,
                'depth': {
                    'buy': [{'price': price-1, 'quantity': 5, 'orders': 1}],
                    'sell': [{'price': price+1, 'quantity': 5, 'orders': 1}]
                }
            }
        return res

# Initialize Bot
print("Initializing Bot...")
bot = UltimateFNOTrader(None)
# FORCE MOCKING
bot.kite_api = None
bot.kite = MockKite()
bot.mcx_instruments = bot.kite.instruments('MCX')

# Verify Mock Instruments
print(f"Mock Instruments Count: {len(bot.mcx_instruments)}")
found_silverm = [i for i in bot.mcx_instruments if i['name'] == 'SILVERM']
print(f"Found SILVERM Mock Instruments: {len(found_silverm)}")

# Test Parameters
symbol = 'SILVERM'
spot_price = 90123.0
expiry_str = '2024-02-28'

print(f"Testing {symbol} Spot: {spot_price}")

# Test CALL (Bullish)
print("\n--- Testing CALL (Bullish) ---")
trade_plan_call = {'symbol': symbol, 'direction': 'BULLISH', 'entry': spot_price}
# Manually invoke logic from execute_commodity_option_trade
step = bot.get_strike_step(symbol)
desired_strike_call = float(math.floor(spot_price / step) * step) # floor
print(f"Step: {step}, Desired Strike (Calculated): {desired_strike_call}")

token, ts, strike_sel, ltp = bot.ensure_liquid_commodity_option(symbol, desired_strike_call, 'CE', expiry_str)
print(f"Selected CALL: Token={token}, TS={ts}, Strike={strike_sel}, LTP={ltp}")

# Test PUT (Bearish)
print("\n--- Testing PUT (Bearish) ---")
trade_plan_put = {'symbol': symbol, 'direction': 'BEARISH', 'entry': spot_price}
# Manually invoke logic from execute_commodity_option_trade
desired_strike_put = float(math.ceil(spot_price / step) * step) # ceil
print(f"Step: {step}, Desired Strike (Calculated): {desired_strike_put}")

token, ts, strike_sel, ltp = bot.ensure_liquid_commodity_option(symbol, desired_strike_put, 'PE', expiry_str)
print(f"Selected PUT: Token={token}, TS={ts}, Strike={strike_sel}, LTP={ltp}")
