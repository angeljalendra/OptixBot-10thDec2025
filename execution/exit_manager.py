from datetime import datetime, timedelta
from config.settings import Settings


class ExitManager:
    def __init__(self, kite=None):
        self.kite = kite
        self.quick_profit_target = Settings.trading.QUICK_PROFIT_FULL
        self.tsl_activation_pct = Settings.trading.TRAILING_STOP_ACTIVATION
        self.tsl_pullback_pct = Settings.trading.TRAILING_STOP_TRAIL
        self.time_exit_minutes = Settings.trading.MAX_HOLD_TIME

    def _current_price(self, symbol: str, fallback: float) -> float:
        try:
            if self.kite:
                return float(self.kite.get_current_price(symbol))
        except Exception:
            pass
        return float(fallback)

    def _current_option_price(self, token, fallback: float) -> float:
        try:
            if self.kite and token:
                ltp = float(self.kite.get_ltp_by_token(token))
                if ltp > 0:
                    return ltp
        except Exception:
            pass
        return float(fallback)

    def check(self, position):
        now = datetime.utcnow()
        hold_time = (now - (position.entry_time or now)).total_seconds() / 60.0
        cp = self._current_price(position.symbol, position.entry_price)
        opt_token = getattr(position, 'option_token', None)
        opt_ltp = self._current_option_price(opt_token, position.entry_price)
        opt_tgt = getattr(position, 'option_target_premium', None)
        opt_sl = getattr(position, 'option_stop_premium', None)
        tgt = getattr(position, 'target_price_hint', None)
        sl = getattr(position, 'stop_loss_hint', None)
        dirn = getattr(position, 'direction', 'BULLISH')

        reason = None
        exit_price = None

        try:
            if opt_tgt is not None and opt_ltp > 0 and not getattr(position, 'partial_booked', False):
                if opt_ltp >= float(opt_tgt):
                    setattr(position, 'partial_exit', True)
                    setattr(position, 'partial_exit_price', float(opt_ltp))
        except Exception:
            pass

        if opt_tgt is not None and opt_sl is not None and opt_ltp > 0:
            if opt_ltp >= float(opt_tgt):
                reason = 'TARGET'
                exit_price = opt_ltp
            elif opt_ltp <= float(opt_sl):
                reason = 'STOP_LOSS'
                exit_price = opt_ltp
        else:
            if dirn == 'BULLISH':
                if tgt is not None and cp >= float(tgt):
                    reason = 'TARGET'
                    exit_price = self._current_option_price(opt_token, position.entry_price)
                elif sl is not None and cp <= float(sl):
                    reason = 'STOP_LOSS'
                    exit_price = self._current_option_price(opt_token, position.entry_price)
            else:
                if tgt is not None and cp <= float(tgt):
                    reason = 'TARGET'
                    exit_price = self._current_option_price(opt_token, position.entry_price)
                elif sl is not None and cp >= float(sl):
                    reason = 'STOP_LOSS'
                    exit_price = self._current_option_price(opt_token, position.entry_price)

        if not reason and hold_time >= float(self.time_exit_minutes):
            reason = 'TIME_EXIT'
            exit_price = self._current_option_price(opt_token, position.entry_price)

        if not reason:
            return position

        position.exit_time = now
        position.exit_price = float(exit_price)
        position.exit_reason = reason
        return position
