from core.win_rate_strategy import WinRateOptimizer


class SignalValidator:
    def validate(self, signal):
        return WinRateOptimizer.validate_signal(signal)[0]