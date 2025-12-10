from typing import List, Dict
import numpy as np
from signals.indicators import TechnicalIndicators
from core.logger import Logger
from config.settings import Settings
from config.settings import get_config
from signals.detector import SignalDetector
from signals.validator import SignalValidator


logger = Logger.get_logger('strategies')


class StrategyManager:
    def __init__(self, session, data_provider=None):
        self.session = session
        self.active_strategies = Settings.strategies.ACTIVE_STRATEGIES
        self.detector = SignalDetector(session, data_provider=data_provider)
        self.validator = SignalValidator()

    def scan_all_strategies(self) -> List[Dict]:
        try:
            cfg = get_config()
            if str(cfg.get('universe_mode', '')).upper() == 'NFO_ONLY' and hasattr(self.detector, 'data_provider') and hasattr(self.detector.data_provider, 'get_nfo_underlyings'):
                sz = int(cfg.get('expanded_universe_size', Settings.trading.MAX_POSITIONS))
                symbols = self.detector.data_provider.get_nfo_underlyings(limit=sz)
            else:
                symbols = Settings.strategies.WATCHLIST
        except Exception:
            symbols = Settings.strategies.WATCHLIST
        raw = self.detector.scan_symbols(symbols)
        valid = [s for s in raw if self.validator.validate(s)]
        try:
            max_positions = int(cfg.get('max_positions', Settings.trading.MAX_POSITIONS))
        except Exception:
            max_positions = Settings.trading.MAX_POSITIONS
        scored = []
        for s in valid:
            q = float(s.get('confidence', 0)) * float(s.get('reward_risk_ratio', 0))
            scored.append((q, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        try:
            per_side = int(cfg.get('max_signals_per_side', 0))
        except Exception:
            per_side = 0
        if per_side and per_side > 0:
            bulls = 0
            bears = 0
            selected = []
            for _, s in scored:
                d = str(s.get('direction', '')).upper()
                if d == 'BULLISH' and bulls < per_side:
                    selected.append(s)
                    bulls += 1
                elif d == 'BEARISH' and bears < per_side:
                    selected.append(s)
                    bears += 1
                if len(selected) >= max_positions:
                    break
            return self._apply_precision_filters(selected, cfg, max_positions)
        return self._apply_precision_filters([s for _, s in scored], cfg, max_positions)

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
