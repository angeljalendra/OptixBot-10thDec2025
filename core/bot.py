import time
import os
import sys
from typing import Optional, List, Dict
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from config.settings import Settings, get_config
from core.logger import Logger
from api.zerodha import KiteAPI
from database.models import get_db_engine, get_session, DailyMetric, Trade
from core.strategy_manager import StrategyManager
from core.risk_manager import RiskManager
from execution.executor import ExecutionEngine
from monitoring.alerts import Alerts
import requests


logger = Logger.get_logger('bot')


class TradingBot:
    def __init__(self, mode: str = 'paper'):
        self.mode = mode
        self.is_running = False
        self.market_open = True
        try:
            self.cfg = get_config()
        except Exception:
            self.cfg = {}
        self._init_api()
        self._init_database()
        self._init_managers()
        self._init_metrics()

    def _init_api(self):
        self.kite = KiteAPI(Settings.api.API_KEY, Settings.api.API_SECRET)
        if Settings.api.ACCESS_TOKEN:
            self.kite.set_access_token(Settings.api.ACCESS_TOKEN)
        self.kite.fetch_instruments()
        self.kite.start_websocket()
        # Subscribe tokens for dynamic or static universe
        try:
            symbols = Settings.strategies.WATCHLIST
            try:
                if str(self.cfg.get('universe_mode', '')).upper() == 'NFO_ONLY' and hasattr(self.kite, 'get_nfo_underlyings'):
                    symbols = self.kite.get_nfo_underlyings(limit=int(self.cfg.get('expanded_universe_size', 50))) or symbols
            except Exception:
                symbols = Settings.strategies.WATCHLIST
            for sym in symbols:
                self.kite.subscribe_spot(sym)
        except Exception:
            pass

    def _init_database(self):
        self.engine = get_db_engine(Settings.db.CONNECTION_STRING)
        self.session = get_session(self.engine)

    def _init_managers(self):
        self.strategy_manager = StrategyManager(self.session, data_provider=self.kite)
        self.risk_manager = RiskManager(self.session)
        self.executor = ExecutionEngine(self.kite, self.session, mode=self.mode)
        self.dashboard_url = os.getenv('DASHBOARD_URL', 'http://127.0.0.1:5000')
        self.alerts = Alerts()

    def _init_metrics(self):
        self.metrics = {
            'initial_capital': Settings.trading.INITIAL_CAPITAL,
            'portfolio_value': Settings.trading.INITIAL_CAPITAL,
            'total_pnl': 0.0,
            'daily_pnl': 0.0,
            'daily_pnl_pct': 0.0,
            'signals_count': 0,
            'trades_executed': 0,
            'closed_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate': 0.0
        }
        try:
            tgt_pct = float(self.cfg.get('target_profit_pct', 10.0))
            loss_pct = float(self.cfg.get('max_daily_loss_pct', 2.0))
        except Exception:
            tgt_pct = 10.0
            loss_pct = 2.0
        cap0 = float(self.metrics['initial_capital'])
        self.daily_profit_target_abs = cap0 * tgt_pct / 100.0
        self.daily_loss_limit_abs = cap0 * loss_pct / 100.0
        self.consecutive_losses = 0
        self.portfolio_tsl_active = False
        self.portfolio_peak_daily_pnl = 0.0
        self.loss_cooldown_until = 0.0

    def start(self):
        self.is_running = True
        logger.info(f"Bot started in {self.mode.upper()} mode")
        while self.is_running:
            self.scan_and_execute_signals()
            self.monitor_positions()
            time.sleep(5)

    def stop(self):
        self.is_running = False

    def scan_and_execute_signals(self):
        if self.metrics.get('daily_pnl', 0.0) >= self.daily_profit_target_abs:
            self._publish('rejection', {'reason': 'DAILY_TARGET_REACHED'})
            return
        if abs(self.metrics.get('daily_pnl', 0.0)) >= self.daily_loss_limit_abs and self.metrics.get('daily_pnl', 0.0) < 0:
            self._publish('rejection', {'reason': 'DAILY_LOSS_LIMIT'})
            return
        try:
            if int(self.cfg.get('max_consecutive_losses', 0)) and self.consecutive_losses >= int(self.cfg.get('max_consecutive_losses', 0)):
                self._publish('rejection', {'reason': 'MAX_CONSECUTIVE_LOSSES'})
                return
        except Exception:
            pass
        try:
            if self.loss_cooldown_until and time.time() < float(self.loss_cooldown_until or 0):
                self._publish('rejection', {'reason': 'LOSS_COOLDOWN_ACTIVE'})
                return
        except Exception:
            pass
        if self.portfolio_tsl_active:
            self._publish('rejection', {'reason': 'PORTFOLIO_TSL_ACTIVE'})
            return
        try:
            if not self._vix_allowed():
                self._publish('rejection', {'reason': 'VIX_TOO_HIGH'})
                return
        except Exception:
            pass
        signals = self.strategy_manager.scan_all_strategies()
        try:
            if bool(self.cfg.get('elite_mode', False)):
                self.process_scan_results_elite(signals)
                return
        except Exception:
            pass
        # Default loop executes signals; elite users may call process_scan_results_elite explicitly
        for signal in signals:
            ok, checks = self.risk_manager.validate_signal_info(signal)
            if not ok:
                payload = {'symbol': signal.get('symbol', ''), 'reason': 'RISK_CHECK_FAILED', 'checks': checks}
                self._publish('rejection', payload)
                continue
            self._publish('signal', signal)
            try:
                self.kite.subscribe_spot(signal.get('symbol', ''))
                if signal.get('option_type') and signal.get('strike'):
                    self.kite.subscribe_option(signal['symbol'], float(signal['strike']), signal['option_type'])
            except Exception:
                pass
            self.executor.execute_trade(signal)
            try:
                hc = float(self.cfg.get('precision_min_confidence', self.cfg.get('min_confidence', 0)))
            except Exception:
                hc = 0.0
            try:
                conf = float(signal.get('confidence', 0) or 0)
                rr = float(signal.get('reward_risk_ratio', 0) or 0)
                direction = str(signal.get('direction', '')).upper()
                icon = '🟢' if direction == 'BULLISH' else '🔴'
                txt = f"{icon} SIGNAL {signal.get('symbol','')}: {direction}\nEntry: ₹{signal.get('entry_price',0)} Target: ₹{signal.get('target_price',0)} Stop: ₹{signal.get('stop_loss',0)}\nR:R 1:{rr:.2f} Conf {conf:.1f}/10"
                if conf >= hc:
                    self.alerts.send_trade(txt)
            except Exception:
                pass
            self.metrics['signals_count'] += 1
            self.metrics['trades_executed'] += 1
            self._recompute_metrics()
            self._publish('metrics', self.metrics)

    def monitor_positions(self):
        for pos in self.executor.get_open_positions():
            self.executor.check_position_exits(pos)
            payload = {
                'symbol': pos.symbol,
                'pnl': pos.pnl,
                'exit_reason': pos.exit_reason
            }
            self._publish('position', payload)
            try:
                if getattr(pos, 'exit_reason', None):
                    icon = '🟢' if (pos.pnl or 0) >= 0 else '🔴'
                    txt = f"{icon} CLOSED {pos.symbol} P&L ₹{float(pos.pnl or 0):.2f} ({pos.exit_reason})"
                    self.alerts.send_trade(txt)
            except Exception:
                pass
            # Metrics update
            self.metrics['closed_trades'] += 1
            self.metrics['total_pnl'] += float(pos.pnl or 0)
            if (pos.pnl or 0) >= 0:
                self.metrics['winning_trades'] += 1
                self.consecutive_losses = 0
            else:
                self.metrics['losing_trades'] += 1
                self.consecutive_losses += 1
                try:
                    mcl = int(self.cfg.get('max_consecutive_losses', 0) or 0)
                    cd_min = float(self.cfg.get('precision_cooldown_minutes', 0) or 0)
                    if mcl and cd_min and self.consecutive_losses >= mcl:
                        if not self.loss_cooldown_until or time.time() >= float(self.loss_cooldown_until or 0):
                            self.loss_cooldown_until = time.time() + (cd_min * 60.0)
                            try:
                                self.alerts.send_trade(f"⏳ COOL-DOWN {cd_min:.0f}m after {self.consecutive_losses} consecutive losses")
                            except Exception:
                                pass
                except Exception:
                    pass
            self._recompute_metrics()
            self._publish('metrics', self.metrics)
        self._portfolio_tsl_check()

    def process_scan_results_elite(self, all_signals: List[Dict]):
        """
        ELITE trading process - only best trades
        Target: 2-4 trades per day with 90%+ win rate
        """

        from core.risk_manager import EliteRiskManager
        rm = EliteRiskManager(self.session, self.cfg)

        existing_positions = [{'symbol': getattr(p, 'symbol', None)} for p in self.executor.get_open_positions()]
        portfolio_value = float(self.metrics.get('portfolio_value', self.metrics.get('initial_capital', 0)))
        initial_capital = float(self.metrics.get('initial_capital', 0))
        portfolio_pnl_pct = ((portfolio_value - initial_capital) / initial_capital * 100.0) if initial_capital > 0 else 0.0

        logger.info(f"🔍 ELITE SIGNAL PROCESSING: {len(all_signals)} candidates | "
                    f"Portfolio P&L: {portfolio_pnl_pct:.1f}% | Open: {len(existing_positions)}")

        valid_signals = []
        for signal in all_signals:
            is_valid, checks = rm.validate_signal_elite(signal)
            if not is_valid:
                failed_checks = [k for k, v in checks.items() if not v]
                logger.debug(f"❌ Signal rejected: {signal.get('symbol')} - Failed: {', '.join(failed_checks)}")
                continue
            can_execute, reason = rm.validate_position_execution(signal, existing_positions, portfolio_pnl_pct)
            if not can_execute:
                logger.info(f"⏸️ Signal blocked: {signal.get('symbol')} - {reason}")
                continue
            valid_signals.append(signal)

        logger.info(f"✅ ELITE SIGNALS VALIDATED: {len(valid_signals)} passed all filters")

        scored_signals = []
        for signal in valid_signals:
            score = (float(signal.get('confidence', 8)) + (float(signal.get('reward_risk_ratio', 2)) / 3.0)) / 2.0
            scored_signals.append({'score': score, 'signal': signal})

        scored_signals.sort(key=lambda x: x['score'], reverse=True)

        bullish_count = 0
        bearish_count = 0
        final_trades = []

        for item in scored_signals:
            signal = item['signal']
            direction = str(signal.get('direction', '')).upper()
            if direction == 'BULLISH' and bullish_count >= 1:
                logger.info(f"⏸️ Signal skipped (max bullish reached): {signal.get('symbol')}")
                continue
            elif direction == 'BEARISH' and bearish_count >= 1:
                logger.info(f"⏸️ Signal skipped (max bearish reached): {signal.get('symbol')}")
                continue
            if len(existing_positions) + len(final_trades) >= int(self.cfg.get('max_positions', 2)):
                logger.info(f"⏸️ Signal skipped (position limit): {signal.get('symbol')}")
                continue
            final_trades.append(signal)
            if direction == 'BULLISH':
                bullish_count += 1
            else:
                bearish_count += 1

        logger.info(f"🎯 FINAL ELITE TRADES: {len(final_trades)} ready for execution "
                    f"({bullish_count} bullish, {bearish_count} bearish)")

        for trade_plan in final_trades:
            try:
                trade = self.executor.execute_trade(trade_plan)
                if trade:
                    logger.info(f"✅ TRADE EXECUTED: {trade_plan.get('symbol')}")
                    self.metrics['signals_count'] += 1
                    self.metrics['trades_executed'] += 1
                    self._recompute_metrics()
                    self._publish('metrics', self.metrics)
            except Exception as e:
                logger.error(f"❌ Execution failed for {trade_plan.get('symbol')}: {e}")
                continue

    def _save_daily_metrics(self):
        metrics = self.strategy_manager.calculate_daily_metrics()
        daily = DailyMetric(
            date=time.strftime('%Y-%m-%d'),
            daily_pnl=metrics['daily_pnl'],
            daily_pnl_pct=metrics['daily_pnl_pct'],
            trades_executed=metrics['trades_executed'],
            winning_trades=metrics['winning_trades'],
            losing_trades=metrics['losing_trades'],
            win_rate=metrics['win_rate'],
            strategy_breakdown=metrics['strategy_breakdown']
        )
        self.session.add(daily)
        self.session.commit()

    def _publish(self, kind: str, payload: dict):
        try:
            requests.post(f"{self.dashboard_url}/api/{kind}", json=payload, timeout=0.5)
        except Exception:
            pass

    def _recompute_metrics(self):
        cap0 = float(self.metrics['initial_capital'])
        total_pnl = float(self.metrics['total_pnl'])
        self.metrics['portfolio_value'] = cap0 + total_pnl
        self.metrics['daily_pnl'] = total_pnl  # simple for demo; wire actual daily calc later
        self.metrics['daily_pnl_pct'] = (self.metrics['daily_pnl'] / cap0 * 100.0) if cap0 else 0.0
        ct = self.metrics['closed_trades']
        wins = self.metrics['winning_trades']
        self.metrics['win_rate'] = (wins / ct * 100.0) if ct else 0.0
        if self.metrics['daily_pnl'] > self.portfolio_peak_daily_pnl:
            self.portfolio_peak_daily_pnl = self.metrics['daily_pnl']

    def _portfolio_tsl_check(self):
        try:
            act_pct = float(self.cfg.get('portfolio_tsl_activation_pct', 0.0))
            pull_pct = float(self.cfg.get('portfolio_tsl_pullback_pct', 0.0))
            hard_pull = float(self.cfg.get('portfolio_hard_lock_pullback_pct', 0.0))
        except Exception:
            act_pct = 0.0
            pull_pct = 0.0
            hard_pull = 0.0
        cap0 = float(self.metrics['initial_capital'])
        gain_pct = (self.metrics['daily_pnl'] / cap0 * 100.0) if cap0 else 0.0
        if not self.portfolio_tsl_active and gain_pct >= act_pct and act_pct > 0:
            self.portfolio_tsl_active = True
            self.portfolio_peak_daily_pnl = self.metrics['daily_pnl']
        if self.portfolio_tsl_active:
            drop_amt = self.portfolio_peak_daily_pnl - self.metrics['daily_pnl']
            drop_pct = (drop_amt / cap0 * 100.0) if cap0 else 0.0
            if hard_pull and drop_pct >= hard_pull:
                self.executor.force_close_all('PORTFOLIO_HARD_LOCK')
                self.portfolio_tsl_active = False
            elif pull_pct and drop_pct >= pull_pct:
                self.executor.force_close_all('PORTFOLIO_TSL')
                self.portfolio_tsl_active = False

    def _vix_allowed(self) -> bool:
        try:
            mv = float(self.cfg.get('max_vix', 0) or 0)
        except Exception:
            mv = 0.0
        if not mv:
            return True
        try:
            sym = '^INDIAVIX'
            df = yf.Ticker(sym).history(period='5d', interval='1d')
            if df is None or df.empty:
                sym = '^VIX'
                df = yf.Ticker(sym).history(period='5d', interval='1d')
            if df is None or df.empty:
                return True
            val = float(df['Close'].iloc[-1])
            return val <= mv
        except Exception:
            return True

    def reset_system(self, hard: bool = False):
        try:
            self.executor.force_close_all('SYSTEM_RESET')
        except Exception:
            pass
        try:
            self.kite.reset_state()
        except Exception:
            pass
        try:
            # Resubscribe universe
            symbols = Settings.strategies.WATCHLIST
            try:
                if str(self.cfg.get('universe_mode', '')).upper() == 'NFO_ONLY' and hasattr(self.kite, 'get_nfo_underlyings'):
                    symbols = self.kite.get_nfo_underlyings(limit=int(self.cfg.get('expanded_universe_size', 50))) or symbols
            except Exception:
                symbols = Settings.strategies.WATCHLIST
            for sym in symbols:
                self.kite.subscribe_spot(sym)
        except Exception:
            pass
        try:
            self._init_metrics()
            self._publish('metrics', self.metrics)
        except Exception:
            pass
        if hard:
            try:
                # Close lingering OPEN trades in DB and clear today's metrics
                open_trades = self.session.query(Trade).filter(Trade.status == 'OPEN').all()
                for t in open_trades:
                    t.status = 'CLOSED'
                    t.exit_reason = 'SYSTEM_RESET'
                    t.exit_time = time.strftime('%Y-%m-%d %H:%M:%S')
                today = time.strftime('%Y-%m-%d')
                self.session.query(DailyMetric).filter(DailyMetric.date == today).delete()
                self.session.commit()
            except Exception:
                try:
                    self.session.rollback()
                except Exception:
                    pass


def main():
    bot = TradingBot(mode='paper')
    bot.start()


if __name__ == '__main__':
    main()
