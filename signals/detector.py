from typing import List, Dict
from signals.indicators import TechnicalIndicators


class SignalDetector:
    def __init__(self, session=None, data_provider=None):
        self.session = session
        self.data_provider = data_provider

    def _normalize_confidence(self, x: float) -> float:
        return max(0.0, min(10.0, round(x, 2)))

    def _compute_confidence(self, rsi_val: float, macd: Dict, bb: Dict, atr_pct: float, volume_ratio: float, direction: str) -> float:
        score = 0.0
        if direction == 'BULLISH':
            score += 4.0 if macd.get('bullish_crossover') else 0.0
            target = 55.0
            proximity = max(0.0, 1.0 - abs(rsi_val - target) / 25.0)
            score += 4.0 * proximity
            bw = 0.0
            try:
                mid = float(bb.get('middle') or 0)
                low = float(bb.get('lower') or 0)
                up = float(bb.get('upper') or 0)
                w = (up - low) / max(mid, 1.0)
                bw = max(0.0, min(0.1, w))
            except Exception:
                bw = 0.0
            score += 2.5 * (1.0 - (bw / 0.1))
        else:
            score += 4.0 if macd.get('bearish_crossover') else 0.0
            target = 45.0
            proximity = max(0.0, 1.0 - abs(rsi_val - target) / 25.0)
            score += 4.0 * proximity
            align = 1.0 if bb.get('price_above_upper') else 0.0
            score += 2.5 * align
        if atr_pct > 3.5:
            score -= 0.5
        elif 1.0 <= atr_pct <= 2.5:
            score += 1.0
        if volume_ratio >= 1.5:
            score += 1.0
        elif volume_ratio >= 1.2:
            score += 0.5
        return self._normalize_confidence(score)

    def scan_symbols(self, symbols: List[str]) -> List[Dict]:
        signals: List[Dict] = []
        for sym in symbols:
            ind = TechnicalIndicators(sym, data_provider=self.data_provider)
            price = ind.get_current_price()
            rsi = ind.calculate_rsi()
            macd = ind.calculate_macd()
            bb = ind.calculate_bollinger_bands()
            atr = ind.calculate_atr()
            atr_pct = ind.calculate_atr_percent()
            volume_ratio = ind.calculate_volume_ratio()
            sma = ind.calculate_sma()
            if atr_pct > 4.2:
                continue
            if macd.get('bullish_crossover') and 35 < float(rsi.get('value', 50)) < 70:
                if price > sma.get('sma50', price) and sma.get('sma50', price) > sma.get('sma200', price):
                    conf = self._compute_confidence(float(rsi.get('value', 50)), macd, bb, atr_pct, volume_ratio, 'BULLISH')
                    target_price = round(price + atr * 2.0, 2)
                    stop_loss = round(price - atr * 1.2, 2)
                    rr = (target_price - price) / max(price - stop_loss, 0.01)
                    signals.append({
                        'symbol': sym,
                        'direction': 'BULLISH',
                        'confidence': conf,
                        'entry_price': price,
                        'target_price': target_price,
                        'stop_loss': stop_loss,
                        'premium': max(10.0, round(price * 0.5 / 100.0, 2)),
                        'lot_size': 50,
                        'quantity': 1,
                        'days_to_expiry': 3,
                        'reward_risk_ratio': round(rr, 2),
                        'bid_ask_spread': 5.0,
                        'strategy': 'HIGH_CONVICTION',
                        'option_type': 'CALL',
                        'strike': round(price, 2),
                    })
            elif bb.get('price_above_upper') and float(rsi.get('value', 50)) > 60:
                if price < sma.get('sma50', price) and sma.get('sma50', price) < sma.get('sma200', price):
                    conf = self._compute_confidence(float(rsi.get('value', 50)), macd, bb, atr_pct, volume_ratio, 'BEARISH')
                    target_price = round(bb.get('middle', price), 2)
                    stop_loss = round(float(bb.get('upper', price)) * 1.01, 2)
                    rr = (price - target_price) / max(stop_loss - price, 0.01)
                    signals.append({
                        'symbol': sym,
                        'direction': 'BEARISH',
                        'confidence': conf,
                        'entry_price': price,
                        'target_price': target_price,
                        'stop_loss': stop_loss,
                        'premium': max(10.0, round(price * 0.5 / 100.0, 2)),
                        'lot_size': 50,
                        'quantity': 1,
                        'days_to_expiry': 3,
                        'reward_risk_ratio': round(rr, 2),
                        'bid_ask_spread': 5.0,
                        'strategy': 'AGGRESSIVE_REVERSAL',
                        'option_type': 'PUT',
                        'strike': round(price, 2),
                    })
            elif macd.get('bullish_crossover') and 45 <= float(rsi.get('value', 50)) <= 60 and atr_pct <= 2.5:
                if price >= sma.get('sma50', price):
                    conf = self._compute_confidence(float(rsi.get('value', 50)), macd, bb, atr_pct, volume_ratio, 'BULLISH')
                    target_price = round(price + atr * 1.0, 2)
                    stop_loss = round(price - atr * 0.4, 2)
                    rr = (target_price - price) / max(price - stop_loss, 0.01)
                    signals.append({
                        'symbol': sym,
                        'direction': 'BULLISH',
                        'confidence': conf,
                        'entry_price': price,
                        'target_price': target_price,
                        'stop_loss': stop_loss,
                        'premium': max(10.0, round(price * 0.5 / 100.0, 2)),
                        'lot_size': 50,
                        'quantity': 1,
                        'days_to_expiry': 3,
                        'reward_risk_ratio': round(rr, 2),
                        'bid_ask_spread': 5.0,
                        'strategy': 'AGGRESSIVE_SCALP',
                        'option_type': 'CALL',
                        'strike': round(price, 2),
                    })
            elif macd.get('bearish_crossover') and 40 <= float(rsi.get('value', 50)) <= 55 and atr_pct <= 2.5:
                if price <= sma.get('sma50', price):
                    conf = self._compute_confidence(float(rsi.get('value', 50)), macd, bb, atr_pct, volume_ratio, 'BEARISH')
                    target_price = round(price - atr * 1.0, 2)
                    stop_loss = round(price + atr * 0.4, 2)
                    rr = (price - target_price) / max(stop_loss - price, 0.01)
                    signals.append({
                        'symbol': sym,
                        'direction': 'BEARISH',
                        'confidence': conf,
                        'entry_price': price,
                        'target_price': target_price,
                        'stop_loss': stop_loss,
                        'premium': max(10.0, round(price * 0.5 / 100.0, 2)),
                        'lot_size': 50,
                        'quantity': 1,
                        'days_to_expiry': 3,
                        'reward_risk_ratio': round(rr, 2),
                        'bid_ask_spread': 5.0,
                        'strategy': 'AGGRESSIVE_SCALP',
                        'option_type': 'PUT',
                        'strike': round(price, 2),
                    })
        return signals
