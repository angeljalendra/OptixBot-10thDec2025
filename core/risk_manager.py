from typing import Dict
from config.settings import Settings
from core.logger import Logger


logger = Logger.get_logger('risk')


class RiskManager:
    def __init__(self, session):
        self.session = session

    def validate_signal(self, signal: Dict) -> bool:
        if signal.get('confidence', 0) < Settings.trading.MIN_CONFIDENCE:
            return False
        rr = signal.get('reward_risk_ratio', 0)
        if rr < Settings.trading.MIN_RR_RATIO:
            return False
        if signal.get('days_to_expiry', 0) < 1:
            return False
        if signal.get('bid_ask_spread', 99) > 10:
            return False
        return True

    def validate_signal_info(self, signal: Dict):
        checks = {
            'confidence': float(signal.get('confidence', 0) or 0) >= float(Settings.trading.MIN_CONFIDENCE),
            'rr_ratio': float(signal.get('reward_risk_ratio', 0) or 0) >= float(Settings.trading.MIN_RR_RATIO),
            'days_to_expiry': int(signal.get('days_to_expiry', 0) or 0) >= 1,
            'bid_ask_spread': float(signal.get('bid_ask_spread', 99) or 99) <= 10.0,
        }
        return all(checks.values()), checks
