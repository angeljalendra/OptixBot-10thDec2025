
import sys
import os
from unittest.mock import MagicMock

# Mock necessary modules
sys.modules['kiteconnect'] = MagicMock()
sys.modules['kiteconnect.exceptions'] = MagicMock()
sys.modules['scipy'] = MagicMock()
sys.modules['scipy.stats'] = MagicMock()
sys.modules['sklearn'] = MagicMock()
sys.modules['sklearn.ensemble'] = MagicMock()
sys.modules['xgboost'] = MagicMock()
sys.modules['tensorflow'] = MagicMock()
sys.modules['ta'] = MagicMock()
sys.modules['ta.trend'] = MagicMock()
sys.modules['ta.momentum'] = MagicMock()
sys.modules['ta.volatility'] = MagicMock()
sys.modules['ta.volume'] = MagicMock()

# Set up environment
os.environ['TELEGRAM_TOKEN'] = 'test_token'
os.environ['TELEGRAM_CHAT_ID'] = 'test_chat_id'

from trading_bot_live import UltimateFNOTrader, WinRateOptimizer

def test_sotd_confidence():
    print("\n--- Testing SOTD_INTRADAY Configuration ---")
    
    # Initialize bot with SOTD_INTRADAY
    bot = UltimateFNOTrader(dashboard_app=MagicMock(), initial_strategy_key="SOTD_INTRADAY")
    
    print(f"Active Strategy: {bot.active_strategy_key}")
    print(f"Min Confidence (bot.min_confidence): {bot.min_confidence}")
    print(f"Min RR (bot.min_rr_ratio): {bot.min_rr_ratio}")
    
    # Check what WinRateOptimizer uses
    signal_data = {
        'confidence': 7.5,
        'reward_risk_ratio': 4.01,
        'bid_ask_spread': 1.0
    }
    
    print(f"\nSignal Data: {signal_data}")
    
    # Test validate_signal with bot's parameters
    ok, checks = WinRateOptimizer.validate_signal(
        signal_data, 
        min_conf=bot.min_confidence, 
        min_rr=bot.min_rr_ratio
    )
    
    print(f"Validation Result (using bot params): {ok}")
    print(f"Checks: {checks}")
    
    # Test with default params (simulation of what might happen if params are missing)
    ok_default, checks_default = WinRateOptimizer.validate_signal(signal_data)
    print(f"Validation Result (using defaults): {ok_default}")
    print(f"Checks (defaults): {checks_default}")

if __name__ == "__main__":
    test_sotd_confidence()
