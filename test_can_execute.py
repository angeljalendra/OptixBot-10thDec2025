
import unittest
from unittest.mock import MagicMock
from trading_bot_live import UltimateFNOTrader as TradingBot

class TestCanExecuteLive(unittest.TestCase):
    def setUp(self):
        self.bot = TradingBot(dashboard_app=MagicMock())
        # Mock dependencies
        self.bot.config = {
            'min_win_rate_threshold': 55.0,
            'min_profit_factor_threshold': 1.2,
            'min_closed_trades_for_validation': 5
        }
        self.bot.trade_journal = {
            'performance_metrics': {'win_rate': 0, 'profit_factor': 0},
            'closed_trades': []
        }
        self.bot.last_last_trade_times = {}
        self.bot.paper_portfolio = {'positions': {}}
        
    def test_can_execute_fresh_bot(self):
        # Should return True because not enough trades
        self.bot.check_circuit_breakers = MagicMock(return_value=False)
        self.bot._correlation_with_open_positions = MagicMock(return_value=0.0)
        
        # Currently failing logic simulation
        # In current code: 0 < 55 -> returns False
        
        # We want to verify it returns True after fix
        pass

if __name__ == '__main__':
    bot = TradingBot(dashboard_app=MagicMock())
    bot.config = {
        'min_win_rate_threshold': 55.0,
        'min_profit_factor_threshold': 1.2,
        'min_closed_trades_for_validation': 5
    }
    bot.trade_journal = {
        'performance_metrics': {'win_rate': 0, 'profit_factor': 0},
        'closed_trades': []
    }
    
    print("Testing can_execute_live with fresh bot...")
    result = bot.can_execute_live({'symbol': 'TEST', 'confidence': 8.0})
    print(f"Result: {result}")
    
    if not result:
        print("FAIL: can_execute_live blocked trade for fresh bot due to 0% win rate.")
    else:
        print("PASS: can_execute_live allowed trade.")
