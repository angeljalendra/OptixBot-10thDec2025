from typing import Dict, Tuple
from config.settings import Settings, get_config
from core.logger import Logger

logger = Logger.get_logger('risk')


class EliteRiskManager:
    """
    Strict risk validation - only allows elite signals through
    """

    def __init__(self, session, config=None):
        self.session = session
        self.config = config or get_config()

    def validate_signal_elite(self, signal: Dict) -> Tuple[bool, Dict]:
        """
        Multi-layer validation before trade execution
        Returns: (is_valid, checks_dict)
        """

        checks = {}

        # CHECK 1: CONFIDENCE MINIMUM (8.5+)
        min_conf = float(self.config.get('min_confidence', 8.5))
        confidence = float(signal.get('confidence', 0))
        checks['confidence'] = confidence >= min_conf
        if not checks['confidence']:
            logger.warning(f"Signal rejected: Confidence {confidence:.1f} < {min_conf}")
            return False, checks

        # CHECK 2: RISK/REWARD RATIO (2.0+ minimum)
        min_rr = float(self.config.get('min_rr_ratio', 2.0))
        rr = float(signal.get('reward_risk_ratio', 0))
        checks['rr_ratio'] = rr >= min_rr
        if not checks['rr_ratio']:
            logger.warning(f"Signal rejected: R:R {rr:.2f} < {min_rr}")
            return False, checks

        # CHECK 3: DAYS TO EXPIRY (At least 2 days)
        days_exp = int(signal.get('days_to_expiry', 0))
        checks['days_to_expiry'] = days_exp >= 2
        if not checks['days_to_expiry']:
            logger.warning(f"Signal rejected: Days to expiry {days_exp} < 2")
            return False, checks

        # CHECK 4: BID-ASK SPREAD (< 10)
        spread = float(signal.get('bid_ask_spread', 99))
        checks['bid_ask_spread'] = spread <= 10.0
        if not checks['bid_ask_spread']:
            logger.warning(f"Signal rejected: Bid-Ask spread {spread} too wide")
            return False, checks

        # CHECK 5: PREMIUM MINIMUM (> 10)
        premium = float(signal.get('premium', 0))
        checks['premium_minimum'] = premium > 10.0
        if not checks['premium_minimum']:
            logger.warning(f"Signal rejected: Premium {premium:.1f} too low")
            return False, checks

        # CHECK 6: VOLATILITY RANGE (10-40%)
        vol = float(signal.get('volatility', 20))
        checks['volatility'] = 10 <= vol <= 40
        if not checks['volatility']:
            logger.warning(f"Signal rejected: Volatility {vol:.1f}% out of range")
            return False, checks

        # CHECK 7: ENTRY PRICE RANGE (50+)
        entry = float(signal.get('entry_price', 0))
        checks['entry_price_range'] = entry >= 50
        if not checks['entry_price_range']:
            logger.warning(f"Signal rejected: Entry price {entry:.1f} too low")
            return False, checks

        # ALL CHECKS PASSED
        logger.info(f"✅ Signal ELITE APPROVED: {signal.get('symbol')} "
                   f"(Conf: {confidence:.1f}, R:R: {rr:.2f})")
        return True, checks

    def validate_position_execution(self,
                                   signal: Dict,
                                   existing_positions: list,
                                   portfolio_pnl_pct: float) -> Tuple[bool, str]:
        """
        Check if position can be opened (risk, capital, exposure)
        """

        # CHECK 1: Position Limit
        max_pos = int(self.config.get('max_positions', 2))
        if len(existing_positions) >= max_pos:
            return False, f"Max positions ({max_pos}) reached"

        # CHECK 2: Same Symbol Already Open
        for pos in existing_positions:
            if pos.get('symbol') == signal.get('symbol'):
                return False, f"Position already open for {signal['symbol']}"

        # CHECK 3: Correlation Check (< 0.4)
        for pos in existing_positions:
            corr = self._calculate_correlation(signal['symbol'], pos['symbol'])
            if corr and corr > 0.4:
                return False, f"High correlation ({corr:.2f}) with {pos['symbol']}"

        # CHECK 4: Daily Loss Limit
        max_daily_loss = float(self.config.get('max_daily_loss_pct', 1.0))
        if portfolio_pnl_pct <= -max_daily_loss:
            return False, f"Daily loss limit ({max_daily_loss}%) reached"

        # CHECK 5: Daily Profit Target
        target_profit = float(self.config.get('target_profit_pct', 5.0))
        if portfolio_pnl_pct >= target_profit:
            return False, f"Daily profit target ({target_profit}%) reached"

        return True, "All position checks passed"

    def _calculate_correlation(self, sym1: str, sym2: str) -> float:
        """
        Stub for correlation calculation
        In real implementation, fetch price series and calculate
        """
        # This would be implemented with actual price data
        return 0.0