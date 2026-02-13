
import sys
import os
from unittest.mock import MagicMock, patch

# Mock all dependencies BEFORE importing trading_bot_live
sys.modules['kiteconnect'] = MagicMock()
sys.modules['kiteconnect.KiteConnect'] = MagicMock()
sys.modules['kiteconnect.KiteTicker'] = MagicMock()
sys.modules['kiteconnect.exceptions'] = MagicMock()
sys.modules['flask'] = MagicMock()
sys.modules['flask_socketio'] = MagicMock()
sys.modules['schedule'] = MagicMock()
sys.modules['scipy'] = MagicMock()
sys.modules['scipy.stats'] = MagicMock()
sys.modules['pandas'] = MagicMock()
sys.modules['numpy'] = MagicMock()
sys.modules['yfinance'] = MagicMock()
sys.modules['psutil'] = MagicMock()

# Now we can import the class safely
# We need to manually load the file content and exec it because of top-level imports
with open('trading_bot_live.py', 'r') as f:
    code = f.read()

# Mock os.getenv to return something safe
with patch.dict(os.environ, {'KITE_API_KEY': 'fake', 'KITE_API_SECRET': 'fake'}):
    # Executing the code in a namespace
    namespace = {}
    try:
        exec(code, namespace)
    except Exception as e:
        print(f"Error executing module: {e}")
        sys.exit(1)

    TradingBot = namespace.get('UltimateFNOTrader') # The class is actually UltimateFNOTrader based on grep
    if not TradingBot:
        print("UltimateFNOTrader class not found")
        sys.exit(1)
        
    # --- Test Logic ---
    print("\n--- Testing trade_plan for IRON_CONDOR ---")
    bot = TradingBot(dashboard_app=MagicMock())
    bot.min_rr_ratio = 1.5
    
    # Mock indicators
    price = 3600.0
    indicators = {'true_range': 50.0, 'volatility': 14.0, 'atr_percent': 1.5}
    
    # Call trade_plan
    plan = bot.trade_plan('LT', 'IRON_CONDOR', potential=2.0, confidence=7.5, price=price, indicators=indicators)
    
    if plan:
        print("✅ Plan created successfully")
        print(f"Direction: {plan['direction']}")
        print(f"Entry: {plan['entry']}")
        print(f"Target: {plan['target']}")
        print(f"Stop Loss: {plan['stop_loss']}")
        print(f"R:R: {plan['risk_reward']}")
        
        if plan['entry'] > 0 and plan['target'] > 0:
            print("✅ Entry and Target are non-zero")
        else:
            print("❌ Entry or Target is zero")
    else:
        print("❌ Plan is None")

    # --- Test Validation Logic ---
    print("\n--- Testing WinRateOptimizer Validation ---")
    # We need to import WinRateOptimizer from where it was defined or mocked
    # Since we exec'd the file, we can look for it in sys.modules or use the one in namespace
    # But WinRateOptimizer is imported inside the file.
    
    # Let's test the logic we patched in core/win_rate_strategy.py
    # We can just import that file directly since it has few dependencies
    import core.win_rate_strategy
    from core.win_rate_strategy import WinRateOptimizer
    
    signal_data = {
        'confidence': 7.5,
        'reward_risk_ratio': 4.0,
        'bid_ask_spread': 5
    }
    
    # Test Strategy Specific Override
    min_conf_strategy = 7.2
    ok, checks = WinRateOptimizer.validate_signal(signal_data, min_conf=min_conf_strategy)
    print(f"Validation with min_conf={min_conf_strategy}: Passed={ok}")
    
    if ok:
        print("✅ Signal passed with strategy threshold")
    else:
        print("❌ Signal failed with strategy threshold")
        
    # Test Global Default (Simulate high global requirement)
    ok_global, checks_global = WinRateOptimizer.validate_signal(signal_data, min_conf=8.0)
    print(f"Validation with min_conf=8.0: Passed={ok_global}")
    
    if not ok_global:
        print("✅ Signal correctly failed global threshold (verification of logic)")
    else:
        print("❌ Signal passed global threshold unexpectedly")

