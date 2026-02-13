from config.settings import Settings, get_config


class WinRateOptimizer:
    MIN_CONFIDENCE = Settings.trading.MIN_CONFIDENCE
    MIN_RR_RATIO = Settings.trading.MIN_RR_RATIO
    QUALITY_GATES = {
        'signal_confidence': 10,
        'liquidity_minimum': 10,
    }
    @staticmethod
    def validate_signal(signal_data, min_conf=None, min_rr=None):
        try:
            if min_conf is None or min_rr is None:
                cfg = get_config()
                if min_conf is None:
                    min_conf = float(cfg.get('min_confidence', WinRateOptimizer.MIN_CONFIDENCE))
                if min_rr is None:
                    min_rr = float(cfg.get('min_rr_ratio', WinRateOptimizer.MIN_RR_RATIO))
        except Exception:
            if min_conf is None:
                min_conf = WinRateOptimizer.MIN_CONFIDENCE
            if min_rr is None:
                min_rr = WinRateOptimizer.MIN_RR_RATIO
        
        # Ensure we don't accidentally override passed values with defaults if something failed above
        # (Though logic above handles None checks, this is extra safety)
        
        checks = {
            'confidence': signal_data.get('confidence', 0) >= min_conf,
            'rr_ratio': signal_data.get('reward_risk_ratio', 0) >= min_rr,
            'liquidity': signal_data.get('bid_ask_spread', 99) <= WinRateOptimizer.QUALITY_GATES['liquidity_minimum'],
        }
        return all(checks.values()), checks
