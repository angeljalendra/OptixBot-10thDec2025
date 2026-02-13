from typing import List, Dict
import numpy as np
from signals.indicators import TechnicalIndicators
from core.logger import Logger
from config.settings import Settings
from config.settings import get_config
from signals.detector import EliteSignalDetector
from signals.validator import SignalValidator


logger = Logger.get_logger('strategies')


class StrategyManager:
    def __init__(self, session, data_provider=None):
        self.session = session
        self.active_strategies = Settings.strategies.ACTIVE_STRATEGIES
        self.detector = EliteSignalDetector(session, data_provider=data_provider)
        self.validator = SignalValidator()

    def scan_all_strategies(self) -> List[Dict]:
        try:
            return self.scan_all_strategies_elite()
        except Exception:
            return []

    def scan_all_strategies_elite(self) -> List[Dict]:
        """
        Elite scanning - focus on quality over quantity
        """

        cfg = get_config()

        # Get symbols to scan (NFO + optional MCX commodities)
        symbols: List[str] = []
        try:
            if hasattr(self.detector, 'data_provider') and hasattr(self.detector.data_provider, 'get_nfo_underlyings'):
                symbols = self.detector.data_provider.get_nfo_underlyings(
                    limit=int(cfg.get('expanded_universe_size', 20))
                ) or []
        except Exception:
            symbols = []
        # Optionally add commodities if enabled and supported by data provider
        try:
            if bool(cfg.get('commodity_trading_enabled', False)) and hasattr(self.detector, 'data_provider') and hasattr(self.detector.data_provider, 'get_commodity_underlyings'):
                commodities = self.detector.data_provider.get_commodity_underlyings(
                    limit=int(cfg.get('expanded_universe_size', 50))
                ) or []
                symbols = (symbols or []) + commodities
        except Exception:
            pass
        if not symbols:
            symbols = Settings.strategies.WATCHLIST[:20]

        logger.info(f"⚡ ELITE SCAN: {len(symbols)} liquid symbols")

        market_regime = self.assess_market_regime()
        logger.info(f"📊 Market Regime: {market_regime}")


        # Scan only most liquid symbols
        raw_signals = self.detector.scan_symbols(symbols)

        try:
            conf_thr = float(cfg.get('precision_min_confidence', cfg.get('min_confidence', 8.0)))
        except Exception:
            conf_thr = 8.0
        try:
            rr_thr = float(cfg.get('precision_min_rr_ratio', cfg.get('min_rr_ratio', 2.0)))
        except Exception:
            rr_thr = 2.0
        try:
            if market_regime == 'RANGE':
                conf_thr = max(0.0, conf_thr - 0.5)
                rr_thr = max(1.5, rr_thr - 0.2)
            elif market_regime == 'VOLATILE':
                conf_thr = conf_thr + 0.3
                rr_thr = rr_thr + 0.1
        except Exception:
            pass
        valid_signals = [s for s in raw_signals
                         if float(s.get('confidence', 0)) >= conf_thr and
                         float(s.get('reward_risk_ratio', 0)) >= rr_thr]
        if not valid_signals and raw_signals:
            try:
                conf_fallback = max(7.5, conf_thr - 1.0)
                rr_fallback = max(1.8, rr_thr - 0.2)
                valid_signals = [s for s in raw_signals
                                 if float(s.get('confidence', 0)) >= conf_fallback and
                                 float(s.get('reward_risk_ratio', 0)) >= rr_fallback]
            except Exception:
                pass

        # Apply precision filters
        return self._apply_elite_filters(valid_signals, cfg)
    def _apply_elite_filters(self, candidates: List[Dict], cfg: Dict) -> List[Dict]:
        selected: List[Dict] = []
        max_positions = int(cfg.get('max_positions', 2))
        for s in candidates:
            if len(selected) >= max_positions:
                break
            symbol = str(s.get('symbol', ''))
            ok = True
            for prev in selected:
                psym = str(prev.get('symbol', ''))
                if symbol == psym:
                    ok = False
                    break
            if ok:
                selected.append(s)
        logger.info(f"✅ Elite candidates selected: {len(selected)}")
        return selected

    def _get_series(self, sym: str, window: int) -> List[float]:
        try:
            if hasattr(self.detector, 'data_provider') and hasattr(self.detector.data_provider, 'get_price_series'):
                series = list(self.detector.data_provider.get_price_series(sym, window))
                if series and len(series) >= 10:
                    return series[-window:]
        except Exception:
            pass
        ind = TechnicalIndicators(sym, data_provider=self.detector.data_provider)
        return [ind.get_current_price() for _ in range(window)]

    def _apply_precision_filters(self, candidates: List[Dict], cfg: Dict, max_positions: int) -> List[Dict]:
        try:
            hp = bool(cfg.get('high_precision_mode', False))
        except Exception:
            hp = False
        if not hp:
            return candidates[:max_positions]
        try:
            corr_thr = float(cfg.get('precision_max_correlation', 0.5))
        except Exception:
            corr_thr = 0.5
        window = 50
        selected: List[Dict] = []
        series_map: Dict[str, List[float]] = {}
        for s in candidates:
            sym = str(s.get('symbol', ''))
            if not sym:
                continue
            if len(selected) >= max_positions:
                break
            if sym not in series_map:
                series_map[sym] = self._get_series(sym, window)
            ok = True
            for prev in selected:
                psym = str(prev.get('symbol', ''))
                if psym == sym:
                    continue
                if psym not in series_map:
                    series_map[psym] = self._get_series(psym, window)
                a = np.array(series_map[psym])
                b = np.array(series_map[sym])
                try:
                    if len(a) == len(b) and len(a) >= 10:
                        ra = np.diff(a)
                        rb = np.diff(b)
                        c = float(np.corrcoef(ra, rb)[0, 1])
                        if abs(c) > corr_thr:
                            ok = False
                            break
                except Exception:
                    pass
            if ok:
                selected.append(s)
        return selected

    def assess_market_regime(self) -> str:
        sym = 'NIFTY'
        try:
            base = Settings.strategies.WATCHLIST
            if isinstance(base, list) and base:
                sym = base[0]
        except Exception:
            pass
        ind = TechnicalIndicators(sym, data_provider=getattr(self.detector, 'data_provider', None))
        price = ind.get_current_price()
        sma = ind.calculate_sma()
        rsi = ind.calculate_rsi()
        atr_pct = ind.calculate_atr_percent()
        sma50 = float(sma.get('sma50', price) or price)
        sma200 = float(sma.get('sma200', price) or price)
        r = float(rsi.get('value', 50))
        if (price > sma50 > sma200) and (40 <= r <= 65):
            return 'TRENDING'
        if atr_pct >= 3.5 or r <= 35 or r >= 70:
            return 'VOLATILE'
        return 'RANGE'

    def calculate_daily_metrics(self) -> Dict:
        return {
            'daily_pnl': 0.0,
            'daily_pnl_pct': 0.0,
            'trades_executed': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate': 0.0,
            'strategy_breakdown': {}
        }
