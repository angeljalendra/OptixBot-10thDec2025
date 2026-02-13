from typing import List, Dict, Tuple
import numpy as np
from signals.indicators import TechnicalIndicators
from core.logger import Logger

logger = Logger.get_logger('signals')


class EliteSignalDetector:
    """
    High-conviction signal detector for 90%+ win rate
    Only generates signals on confluence of 5+ bullish/bearish factors
    """

    def __init__(self, session=None, data_provider=None):
        self.session = session
        self.data_provider = data_provider
        self.min_bars_required = 50

    def _normalize_confidence(self, x: float) -> float:
        return max(0.0, min(10.0, round(x, 2)))

    def calculate_elite_bullish_score(self, indicators: Dict) -> float:
        """
        STRICT bullish scoring - requires multi-factor confirmation
        Returns 0 if any critical check fails
        """
        score = 0.0

        if not (indicators['current_price'] > indicators['sma50'] > indicators['sma200']):
            return 0.0  # NO SIGNAL - trend not aligned
        score += 3.0

        if not indicators['macd_bull']:
            return 0.0  # NO SIGNAL - momentum missing
        score += 2.0

        # CRITICAL CHECK 3: RSI SWEET SPOT (40-65 for continuations)
        if not (35 <= indicators['rsi'] <= 70):
            return 0.0  # NO SIGNAL - RSI overbought/oversold

        if 40 <= indicators['rsi'] <= 65:
            score += 2.5  # Perfect zone
        elif 35 <= indicators['rsi'] < 40:
            score += 1.5  # Oversold bounce
        else:
            score += 0.5  # Near overbought

        if indicators['volume_ratio'] < 1.1:
            return 0.0  # NO SIGNAL - insufficient volume
        score += 1.5

        # CRITICAL CHECK 5: VOLATILITY IN RANGE
        vol = float(indicators.get('volatility', 20))
        if vol < 8 or vol > 50:
            return 0.0  # NO SIGNAL - too much/little volatility
        score += 0.5

        # SUPPORTING CHECK 1: PRICE NEAR SUPPORT
        support_distance = ((indicators['current_price'] - indicators['support']) /
                           indicators['current_price']) * 100
        if 1.5 <= support_distance <= 8:
            score += 1.0

        # SUPPORTING CHECK 2: BOLLINGER BAND POSITION
        if indicators.get('bb_lower') and indicators['current_price'] > indicators['bb_lower']:
            score += 0.5

        # SUPPORTING CHECK 3: ATR VOLATILITY RATIO
        atr_pct = float(indicators.get('atr_percent', 2))
        if 1.0 <= atr_pct <= 3.5:
            score += 0.5

        logger.debug(f"Bullish score: {score:.1f} (Price: {indicators['current_price']:.1f}, "
                    f"RSI: {indicators['rsi']:.0f}, Vol: {vol:.1f}%)")

        return self._normalize_confidence(score)

    def calculate_elite_bearish_score(self, indicators: Dict) -> float:
        """
        STRICT bearish scoring - mirror of bullish logic
        """
        score = 0.0

        if not (indicators['current_price'] < indicators['sma50'] < indicators['sma200']):
            return 0.0
        score += 3.0

        if not indicators['macd_bear']:
            return 0.0
        score += 2.0

        # CRITICAL CHECK 3: RSI SWEET SPOT (30-60 for continuations)
        if not (30 <= indicators['rsi'] <= 70):
            return 0.0

        if 35 <= indicators['rsi'] <= 60:
            score += 2.5
        elif 60 < indicators['rsi'] <= 70:
            score += 1.5  # Overbought bounce
        else:
            score += 0.5

        # CRITICAL CHECK 4: VOLUME CONFIRMATION
        if indicators['volume_ratio'] < 1.2:
            return 0.0
        score += 1.5

        # CRITICAL CHECK 5: VOLATILITY
        vol = float(indicators.get('volatility', 20))
        if vol < 10 or vol > 45:
            return 0.0
        score += 0.5

        # SUPPORTING CHECK 1: PRICE NEAR RESISTANCE
        resistance_distance = ((indicators['resistance'] - indicators['current_price']) /
                              indicators['current_price']) * 100
        if 1.5 <= resistance_distance <= 8:
            score += 1.0

        # SUPPORTING CHECK 2: BOLLINGER BAND POSITION
        if indicators.get('bb_upper') and indicators['current_price'] < indicators['bb_upper']:
            score += 0.5

        # SUPPORTING CHECK 3: ATR
        atr_pct = float(indicators.get('atr_percent', 2))
        if 1.0 <= atr_pct <= 3.5:
            score += 0.5

        logger.debug(f"Bearish score: {score:.1f} (Price: {indicators['current_price']:.1f}, "
                    f"RSI: {indicators['rsi']:.0f}, Vol: {vol:.1f}%)")

        return self._normalize_confidence(score)

    def _compute_confidence(self, rsi_val: float, macd: Dict, bb: Dict, atr_pct: float,
                            volume_ratio: float, direction: str) -> float:
        score = 0.0
        if direction.upper() == 'BULLISH':
            if macd.get('bullish_crossover'):
                score += 3.5
            if 40 <= rsi_val <= 65:
                score += 3.0
            elif 35 <= rsi_val <= 70:
                score += 1.0
            if volume_ratio >= 1.1:
                score += 2.5
            if 1.0 <= atr_pct <= 3.5:
                score += 0.5
            if bb.get('lower') and bb.get('middle') and bb.get('upper'):
                score += 0.5
        else:
            if macd.get('bearish_crossover'):
                score += 3.5
            if 35 <= rsi_val <= 60:
                score += 3.0
            elif 30 <= rsi_val <= 70:
                score += 1.0
            if volume_ratio >= 1.2:
                score += 2.5
            if 1.0 <= atr_pct <= 3.5:
                score += 0.5
            if bb.get('lower') and bb.get('middle') and bb.get('upper'):
                score += 0.5
        return self._normalize_confidence(score)

    def scan_symbols(self, symbols: List[str]) -> List[Dict]:
        """
        Scan symbols and return ONLY elite-grade signals
        Target: 5-8 signals per 100 symbols (5-8% hit rate)
        """
        signals: List[Dict] = []

        for sym in symbols:
            try:
                ind = TechnicalIndicators(sym, data_provider=self.data_provider)
                price = ind.get_current_price()

                # PRICE FILTER: Only liquid instruments
                try:
                    from config.settings import get_config
                    cfg = get_config()
                    min_price = float(cfg.get('min_entry_price', 50))
                except Exception:
                    min_price = 50.0
                if price < min_price:
                    continue

                # GET INDICATORS
                rsi = ind.calculate_rsi()
                macd = ind.calculate_macd()
                bb = ind.calculate_bollinger_bands()
                atr = ind.calculate_atr()
                atr_pct = ind.calculate_atr_percent()
                volume_ratio = ind.calculate_volume_ratio()
                sma = ind.calculate_sma()
                adx = ind.calculate_adx()
                slope = ind.calculate_sma_slope()
                persistence = ind.calculate_trend_persistence()

                indicators = {
                    'current_price': price,
                    'rsi': float(rsi.get('value', 50)),
                    'macd_bull': bool(macd.get('bullish_crossover', False)),
                    'macd_bear': bool(macd.get('bearish_crossover', False)),
                    'bb_upper': float(bb.get('upper', price)),
                    'bb_lower': float(bb.get('lower', price)),
                    'bb_middle': float(bb.get('middle', price)),
                    'atr_percent': atr_pct,
                    'volume_ratio': volume_ratio,
                    'sma50': float(sma.get('sma50', price) or price),
                    'sma200': float(sma.get('sma200', price) or price),
                    'support': float(bb.get('lower', price)),
                    'resistance': float(bb.get('upper', price)),
                    'volatility': (atr_pct * 4),
                    'bars': 50,
                    'adx': float(adx),
                    'sma_slope': float(slope),
                    'trend_persistence': float(persistence)
                }

                # CALCULATE ELITE SCORES
                bullish_score = self.calculate_elite_bullish_score(indicators)
                bearish_score = self.calculate_elite_bearish_score(indicators)

                # ONLY ADD HIGH CONFIDENCE SIGNALS
                if bullish_score >= 7.5:
                    if indicators.get('adx', 0) >= 18:
                        bullish_score = self._normalize_confidence(bullish_score + 0.3)
                    if indicators.get('sma_slope', 0) > 0:
                        bullish_score = self._normalize_confidence(bullish_score + 0.2)
                    if indicators.get('trend_persistence', 0) >= 0.6:
                        bullish_score = self._normalize_confidence(bullish_score + 0.2)
                    target_price = round(price + atr * 2.0, 2)
                    stop_loss = round(price - atr * 1.2, 2)
                    rr = (target_price - price) / max(price - stop_loss, 0.01)

                    # FINAL FILTER: R:R >= 2.0
                    if rr >= 2.0:
                        signals.append({
                            'symbol': sym,
                            'direction': 'BULLISH',
                            'confidence': bullish_score,
                            'entry_price': price,
                            'target_price': target_price,
                            'stop_loss': stop_loss,
                            'premium': max(10.0, round(price * 0.5 / 100.0, 2)),
                            'lot_size': 50,
                            'quantity': 1,
                            'days_to_expiry': 3,
                            'reward_risk_ratio': round(rr, 2),
                            'bid_ask_spread': 5.0,
                            'strategy': 'ELITE_HIGH_CONVICTION',
                            'option_type': 'CALL',
                            'strike': round(price, 2),
                            'volatility': indicators['volatility']
                        })

                elif bearish_score >= 7.5:
                    if indicators.get('adx', 0) >= 18:
                        bearish_score = self._normalize_confidence(bearish_score + 0.3)
                    if indicators.get('sma_slope', 0) < 0:
                        bearish_score = self._normalize_confidence(bearish_score + 0.2)
                    if indicators.get('trend_persistence', 0) <= 0.4:
                        bearish_score = self._normalize_confidence(bearish_score + 0.2)
                    target_price = round(price - atr * 2.0, 2)
                    stop_loss = round(price + atr * 1.2, 2)
                    rr = (price - target_price) / max(stop_loss - price, 0.01)

                    if rr >= 2.0:
                        signals.append({
                            'symbol': sym,
                            'direction': 'BEARISH',
                            'confidence': bearish_score,
                            'entry_price': price,
                            'target_price': target_price,
                            'stop_loss': stop_loss,
                            'premium': max(10.0, round(price * 0.5 / 100.0, 2)),
                            'lot_size': 50,
                            'quantity': 1,
                            'days_to_expiry': 3,
                            'reward_risk_ratio': round(rr, 2),
                            'bid_ask_spread': 5.0,
                            'strategy': 'ELITE_HIGH_CONVICTION',
                            'option_type': 'PUT',
                            'strike': round(price, 2),
                            'volatility': indicators['volatility']
                        })

            except Exception as e:
                logger.debug(f"Error scanning {sym}: {e}")
                continue

        logger.info(f"ELITE SCAN: {len(signals)} high-conviction signals from {len(symbols)} symbols")
        return signals

# Backward compatibility alias expected by tests/imports
class SignalDetector(EliteSignalDetector):
    pass
