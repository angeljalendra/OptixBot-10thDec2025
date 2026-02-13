
import sys
import os
import logging
sys.path.append(os.getcwd())
# Mock logger before import
logging.basicConfig(level=logging.INFO)

# Mock Settings if needed, but let's try importing
try:
    from trading_bot_live import UltimateFNOTrader
except ImportError:
    print("Failed to import UltimateFNOTrader")
    sys.exit(1)

from core.win_rate_strategy import WinRateOptimizer

class MockBot(UltimateFNOTrader):
    def __init__(self):
        # Minimal init to avoid full startup
        self.min_confidence = 7.0
        self.min_rr_ratio = 1.5
        self.config = {'high_precision_mode': False}
        self.logger = logging.getLogger('MockBot')

bot = MockBot()
price = 3600.0
indicators = {'true_range': 50.0, 'volatility': 14.0, 'atr_percent': 1.5}
# Test IRON_CONDOR plan
print("\n--- Testing Trade Plan for IRON_CONDOR ---")
plan = bot.trade_plan('LT', 'IRON_CONDOR', potential=2.0, confidence=7.5, price=price, indicators=indicators)

if plan:
    print(f"Symbol: {plan['symbol']}")
    print(f"Direction: {plan['direction']}")
    print(f"Entry: {plan['entry']}")
    print(f"Target: {plan['target']}")
    print(f"Stop Loss: {plan['stop_loss']}")
    print(f"R:R: {plan['risk_reward']}")
    
    # Check if entry/target/stop are non-zero and reasonable
    if plan['entry'] == price and plan['target'] == price:
        print("SUCCESS: Entry and Target correctly set to Price (Neutral).")
    else:
        print(f"FAILURE: Entry/Target mismatch. Expected {price}.")
else:
    print("FAILURE: Plan is None")

# Test Validation Logic
print("\n--- Testing WinRateOptimizer Validation ---")
signal_data = {
    'confidence': 7.5,
    'reward_risk_ratio': 4.0,
    'bid_ask_spread': 5
}
# Simulate Strategy Specific Override
min_conf_strategy = 7.2
ok, checks = WinRateOptimizer.validate_signal(signal_data, min_conf=min_conf_strategy)
print(f"Strategy Threshold (7.2) -> Signal (7.5): Passed={ok}")
if ok:
    print("SUCCESS: Signal passed strategy threshold.")
else:
    print("FAILURE: Signal failed strategy threshold.")

# Simulate Global Threshold (8.0)
ok_global, checks_global = WinRateOptimizer.validate_signal(signal_data, min_conf=8.0)
print(f"Global Threshold (8.0) -> Signal (7.5): Passed={ok_global}")
if not ok_global:
    print("SUCCESS: Signal correctly failed global threshold.")
else:
    print("FAILURE: Signal passed global threshold (should fail).")
