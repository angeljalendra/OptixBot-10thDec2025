import random
import pandas as pd
import numpy as np
from typing import List


class TechnicalIndicators:
    def __init__(self, symbol: str, data_provider=None):
        self.symbol = symbol
        self.data_provider = data_provider
        self._price = self._resolve_price()

    def get_current_price(self) -> float:
        if self.data_provider:
            try:
                return round(float(self.data_provider.get_current_price(self.symbol)), 2)
            except Exception:
                pass
        return round(self._price, 2)

    def _resolve_price(self) -> float:
        if self.data_provider:
            try:
                p = float(self.data_provider.get_current_price(self.symbol))
                return p
            except Exception:
                pass
        return 100.0 + random.random() * 10

    def calculate_rsi(self, period: int = 14) -> dict:
        series = self._series(period * 3)
        if len(series) >= period + 1:
            delta = np.diff(series)
            up = np.where(delta > 0, delta, 0)
            down = np.where(delta < 0, -delta, 0)
            roll_up = pd.Series(up).rolling(period).mean().iloc[-1]
            roll_down = pd.Series(down).rolling(period).mean().iloc[-1]
            rs = roll_up / roll_down if roll_down > 0 else 0
            rsi = 100 - (100 / (1 + rs))
            return {'value': round(float(rsi), 2)}
        return {'value': round(50 + random.uniform(-15, 15), 2)}

    def calculate_macd(self) -> dict:
        series = pd.Series(self._series(60))
        if len(series) >= 35:
            ema12 = series.ewm(span=12, adjust=False).mean()
            ema26 = series.ewm(span=26, adjust=False).mean()
            macd_line = ema12 - ema26
            signal = macd_line.ewm(span=9, adjust=False).mean()
            bull = macd_line.iloc[-1] > signal.iloc[-1] and macd_line.iloc[-2] <= signal.iloc[-2]
            bear = macd_line.iloc[-1] < signal.iloc[-1] and macd_line.iloc[-2] >= signal.iloc[-2]
            return {'bullish_crossover': bool(bull), 'bearish_crossover': bool(bear)}
        bull = random.random() > 0.6
        bear = (not bull) and (random.random() > 0.5)
        return {'bullish_crossover': bull, 'bearish_crossover': bear}

    def calculate_bollinger_bands(self) -> dict:
        series = pd.Series(self._series(20))
        if len(series) >= 20:
            mid = float(series.rolling(20).mean().iloc[-1])
            std = float(series.rolling(20).std().iloc[-1])
            lower = mid - 2 * std
            upper = mid + 2 * std
            cp = self.get_current_price()
            return {
                'lower': round(lower, 2),
                'middle': round(mid, 2),
                'upper': round(upper, 2),
                'price_below_lower': cp < lower,
                'price_above_upper': cp > upper
            }
        mid = self._price
        lower = round(mid * 0.98, 2)
        upper = round(mid * 1.02, 2)
        return {
            'lower': lower,
            'middle': round(mid, 2),
            'upper': upper,
            'price_below_lower': self._price < lower,
            'price_above_upper': self._price > upper
        }

    def calculate_sma(self) -> dict:
        return {'sma50': round(self._price * 0.995, 2), 'sma200': round(self._price * 0.98, 2)}

    def calculate_atr(self) -> float:
        series = pd.Series(self._series(15))
        if len(series) >= 15:
            # Simplified ATR approximation using close-only volatility
            return round(float(series.diff().abs().rolling(14).mean().iloc[-1]), 2)
        return round(2.0 + random.uniform(-0.5, 0.5), 2)

    def calculate_atr_percent(self) -> float:
        cp = self.get_current_price()
        atr = self.calculate_atr()
        return round((atr / max(cp, 1)) * 100.0, 2)

    def calculate_volume_ratio(self) -> float:
        try:
            if self.data_provider:
                series = list(self.data_provider.get_volume_series(self.symbol, 20))
                if len(series) >= 20:
                    avg = float(sum(series[:-1])) / max(1, len(series) - 1)
                    current = float(series[-1])
                    return round(current / max(avg, 1.0), 2)
        except Exception:
            pass
        return 1.0

    def calculate_turnover_ratio(self) -> float:
        try:
            vol_ratio = float(self.calculate_volume_ratio())
            price_now = float(self.get_current_price())
            base_price = float(self._resolve_price())
            price_ratio = price_now / max(base_price, 1.0)
            return round(vol_ratio * price_ratio, 2)
        except Exception:
            return 1.0

    def _series(self, window: int) -> List[float]:
        if self.data_provider:
            try:
                return list(self.data_provider.get_price_series(self.symbol, window))
            except Exception:
                pass
        cp = self.get_current_price()
        return [cp + random.uniform(-1, 1) for _ in range(window)]
