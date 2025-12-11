from datetime import datetime, timedelta
from config.settings import Settings
from core.logger import Logger

logger = Logger.get_logger('exit')


class EliteExitManager:
    """
    Smart exit manager with strict exit rules
    Priority order: Hard SL > Target > Profit Lock > Time Exit
    """
    
    def __init__(self, kite=None):
        self.kite = kite
        
    def _current_option_price(self, token, fallback: float) -> float:
        """Get live LTP for option"""
        try:
            if self.kite and token:
                ltp = float(self.kite.get_ltp_by_token(token))
                if ltp > 0:
                    return ltp
        except Exception:
            pass
        return float(fallback)
    
    def check(self, position) -> dict:
        """
        Check position for exit signals
        Returns: {should_exit: bool, reason: str, exit_price: float}
        """
        now = datetime.utcnow()
        entry_time = datetime.fromisoformat(position['entry_time'].replace('Z', '+00:00')) \
                     if isinstance(position['entry_time'], str) else position['entry_time']
        hold_time_minutes = (now - entry_time).total_seconds() / 60.0
        
        # GET CURRENT PRICES
        opt_token = getattr(position, 'option_token', None)
        opt_ltp = self._current_option_price(opt_token, position.get('current_premium', 
                                                                     position['premium_paid']))
        
        target = float(position.get('target', float('inf')))
        stop_loss = float(position.get('stop_loss', 0))
        entry_premium = float(position.get('premium_paid', position.get('entry_price', 0)))
        pnl_pct = ((opt_ltp - entry_premium) / entry_premium * 100) if entry_premium > 0 else 0
        
        # EXIT RULE 1: HARD STOP LOSS (HIGHEST PRIORITY)
        # If premium falls below SL premium, exit immediately
        sl_premium = entry_premium * 0.8  # 20% loss = exit
        if opt_ltp <= sl_premium:
            logger.info(f"EXIT: {position['symbol']} - HARD STOP LOSS (P&L: {pnl_pct:.1f}%)")
            return {
                'should_exit': True,
                'reason': 'HARD_STOP_LOSS',
                'exit_price': opt_ltp
            }
        
        # EXIT RULE 2: TARGET HIT (HIGH PRIORITY)
        if opt_ltp >= entry_premium * 1.15:  # 15% premium gain
            logger.info(f"EXIT: {position['symbol']} - TARGET HIT (P&L: +{pnl_pct:.1f}%)")
            return {
                'should_exit': True,
                'reason': 'TARGET_HIT',
                'exit_price': opt_ltp
            }
        
        # EXIT RULE 3: PARTIAL PROFIT LOCK AT 5%
        # Lock in 50% at 5% profit
        if 4 <= pnl_pct < 5 and not position.get('partial_booked', False):
            logger.info(f"PARTIAL EXIT: {position['symbol']} - LOCK 50% (P&L: {pnl_pct:.1f}%)")
            position['partial_exit'] = True
            position['partial_exit_price'] = opt_ltp
            position['partial_booked'] = True
            return {
                'should_exit': False,
                'reason': 'PARTIAL_EXIT_50',
                'exit_price': opt_ltp
            }
        
        # EXIT RULE 4: TIME-BASED EXIT
        # Hold for max 45 minutes in profitable, 20 minutes if losing
        if pnl_pct < -5:
            time_limit = 20
        elif pnl_pct < 0:
            time_limit = 30
        else:
            time_limit = 45
        
        if hold_time_minutes >= time_limit:
            logger.info(f"EXIT: {position['symbol']} - TIME LIMIT ({int(hold_time_minutes)}m > {time_limit}m)")
            return {
                'should_exit': True,
                'reason': f'TIME_EXIT_{time_limit}m',
                'exit_price': opt_ltp
            }
        
        # EXIT RULE 5: PREMIUM DECAY BEYOND TOLERANCE
        # If we're losing more than 15% on premium, exit
        if pnl_pct <= -15:
            logger.info(f"EXIT: {position['symbol']} - PREMIUM DECAY (P&L: {pnl_pct:.1f}%)")
            return {
                'should_exit': True,
                'reason': 'PREMIUM_DECAY_LIMIT',
                'exit_price': opt_ltp
            }
        
        # NO EXIT SIGNAL
        return {
            'should_exit': False,
            'reason': 'HOLD',
            'exit_price': opt_ltp
        }
    
    def check_commodity(self, position) -> dict:
        """
        Exit logic for commodity futures
        Spot-based SL/Target checks
        """
        now = datetime.utcnow()
        entry_time = datetime.fromisoformat(position['entry_time'].replace('Z', '+00:00')) \
                     if isinstance(position['entry_time'], str) else position['entry_time']
        hold_time_minutes = (now - entry_time).total_seconds() / 60.0
        
        current_price = position.get('current_price', position.get('entry_price', 0))
        entry_price = float(position['entry_price'])
        target = float(position.get('target', float('inf')))
        stop_loss = float(position.get('stop_loss', 0))
        direction = position.get('direction', 'BULLISH')
        
        pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
        
        # EXIT RULE 1: HARD STOP LOSS
        if direction == 'BULLISH' and current_price <= stop_loss:
            return {
                'should_exit': True,
                'reason': 'HARD_STOP_LOSS',
                'exit_price': current_price
            }
        elif direction == 'BEARISH' and current_price >= stop_loss:
            return {
                'should_exit': True,
                'reason': 'HARD_STOP_LOSS',
                'exit_price': current_price
            }
        
        # EXIT RULE 2: TARGET HIT
        if direction == 'BULLISH' and current_price >= target:
            return {
                'should_exit': True,
                'reason': 'TARGET_HIT',
                'exit_price': current_price
            }
        elif direction == 'BEARISH' and current_price <= target:
            return {
                'should_exit': True,
                'reason': 'TARGET_HIT',
                'exit_price': current_price
            }
        
        # EXIT RULE 3: PARTIAL LOCK AT 3% (Commodities move faster)
        if 2.5 <= pnl_pct < 3.5 and not position.get('partial_booked', False):
            position['partial_booked'] = True
            return {
                'should_exit': False,
                'reason': 'PARTIAL_EXIT_50',
                'exit_price': current_price
            }
        
        # EXIT RULE 4: TIME-BASED (Shorter for commodities)
        if pnl_pct < -3:
            time_limit = 15
        elif pnl_pct < 0:
            time_limit = 20
        else:
            time_limit = 40
        
        if hold_time_minutes >= time_limit:
            return {
                'should_exit': True,
                'reason': f'TIME_EXIT_{time_limit}m',
                'exit_price': current_price
            }
        
        # EXIT RULE 5: DRAWDOWN LIMIT
        if pnl_pct <= -10:
            return {
                'should_exit': True,
                'reason': 'DRAWDOWN_LIMIT',
                'exit_price': current_price
            }
        
        return {
            'should_exit': False,
            'reason': 'HOLD',
            'exit_price': current_price
        }