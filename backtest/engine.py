from typing import Dict
from core.strategy_manager import StrategyManager
from core.risk_manager import RiskManager
from execution.executor import ExecutionEngine
from api.zerodha import KiteAPI
from database.models import get_db_engine, get_session


class BacktestRunner:
    def __init__(self):
        self.engine = get_db_engine('sqlite:///trading_bot.db')
        self.session = get_session(self.engine)
        self.kite = KiteAPI()
        self.strategies = StrategyManager(self.session)
        self.risk = RiskManager(self.session)
        self.exec = ExecutionEngine(self.kite, self.session, mode='paper')

    def run(self, days: int = 1) -> Dict:
        trades = 0
        for _ in range(days):
            signals = self.strategies.scan_all_strategies()
            for s in signals:
                if self.risk.validate_signal(s):
                    t = self.exec.execute_trade(s)
                    if t:
                        trades += 1
                        self.exec.check_position_exits(t)
        return {'trades': trades}