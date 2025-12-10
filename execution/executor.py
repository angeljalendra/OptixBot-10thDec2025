import uuid
from typing import Dict, List
from datetime import datetime
from core.logger import Logger
from database.models import Trade
from execution.exit_manager import ExitManager
from monitoring.alerts import Alerts
from config.settings import get_config


logger = Logger.get_logger('executor')


class ExecutionEngine:
    def __init__(self, kite, session, mode: str = 'paper'):
        self.kite = kite
        self.session = session
        self.mode = mode
        self._open_positions: List[Trade] = []
        self.exit_mgr = ExitManager(kite)
        self.alerts = Alerts()

    def execute_trade(self, signal: Dict):
        try:
            if self.mode.lower() == 'live' and hasattr(self.kite, 'place_order'):
                cfg = get_config()
                cap = float(cfg.get('capital', 500000))
                risk_pct = float(cfg.get('max_risk_per_trade_pct', 0.5))
                stop_pct = float(cfg.get('stop_loss_pct', 10.0))
                tgt_pct = float(cfg.get('trade_target_pct', 15.0))
                lot_size = int(signal.get('lot_size', 1))
                premium = float(signal.get('premium', signal.get('entry_price', 0.0)))
                risk_budget = cap * (risk_pct / 100.0)
                risk_per_lot = max(premium * (stop_pct / 100.0) * lot_size, 1.0)
                lots = max(1, int(risk_budget / risk_per_lot))
                lots = min(lots, 2)
                qty = lots * lot_size
                # Resolve tradingsymbol via instrument lookup if missing
                tsym = signal.get('tradingsymbol')
                expiry_str = str(signal.get('days_to_expiry', ''))
                if not tsym and hasattr(self.kite, 'find_option_instrument') and signal.get('strike') and signal.get('option_type'):
                    inst = self.kite.find_option_instrument(signal['symbol'], float(signal['strike']), signal['option_type'])
                    if inst:
                        tsym = inst.get('tradingsymbol') or signal.get('symbol')
                        exp_val = inst.get('expiry')
                        try:
                            expiry_str = exp_val.strftime('%Y-%m-%d') if hasattr(exp_val, 'strftime') else str(exp_val)
                        except Exception:
                            expiry_str = str(exp_val)
                order_id = self.kite.place_order(
                    tradingsymbol=tsym,
                    exchange='NFO',
                    transaction_type='BUY',
                    order_type='MARKET',
                    quantity=qty,
                    price=None,
                    product='MIS',
                    validity='DAY',
                    variety='regular'
                )
                if not order_id:
                    raise RuntimeError('Order placement failed')
                entry_price = premium
                trade = Trade(
                    id=str(uuid.uuid4()),
                    order_id=order_id,
                    symbol=signal['symbol'],
                    strike=signal.get('strike', 0.0),
                    option_type=signal.get('option_type', 'CALL'),
                    expiry=expiry_str,
                    entry_time=datetime.utcnow(),
                    entry_price=entry_price,
                    quantity=lots,
                    lot_size=lot_size,
                    premium_paid=signal.get('premium', 0.0),
                    strategy=signal.get('strategy', 'UNKNOWN'),
                    strategy_confidence=signal.get('confidence', 0.0),
                    status='OPEN'
                )
                try:
                    setattr(trade, 'direction', signal.get('direction', 'BULLISH'))
                    setattr(trade, 'target_price_hint', signal.get('target_price'))
                    setattr(trade, 'stop_loss_hint', signal.get('stop_loss'))
                    token = self.kite.subscribe_option(signal['symbol'], float(signal.get('strike', 0.0)), signal.get('option_type', 'CALL'))
                    if token:
                        setattr(trade, 'option_token', token)
                    setattr(trade, 'option_target_premium', round(premium * (1 + tgt_pct / 100.0), 2))
                    setattr(trade, 'option_stop_premium', round(premium * (1 - stop_pct / 100.0), 2))
                except Exception:
                    pass
                self._open_positions.append(trade)
                logger.info(f"Trade executed (LIVE): {trade.symbol} {trade.option_type} id={order_id}")
                self.alerts.send_trade(f"✅ LIVE TRADE EXECUTED: <b>{trade.symbol}</b> {trade.option_type}")
                return trade
            # PAPER fallback
            expiry_str = str(signal.get('days_to_expiry', ''))
            if hasattr(self.kite, 'find_option_instrument') and signal.get('strike') and signal.get('option_type'):
                inst = self.kite.find_option_instrument(signal['symbol'], float(signal['strike']), signal['option_type'])
                if inst:
                    exp_val = inst.get('expiry')
                    try:
                        expiry_str = exp_val.strftime('%Y-%m-%d') if hasattr(exp_val, 'strftime') else str(exp_val)
                    except Exception:
                        expiry_str = str(exp_val)
            cfg = get_config()
            cap = float(cfg.get('capital', 500000))
            risk_pct = float(cfg.get('max_risk_per_trade_pct', 0.5))
            stop_pct = float(cfg.get('stop_loss_pct', 10.0))
            tgt_pct = float(cfg.get('trade_target_pct', 15.0))
            lot_size = int(signal.get('lot_size', 1))
            premium = float(signal.get('premium', signal.get('entry_price', 0.0)))
            risk_budget = cap * (risk_pct / 100.0)
            risk_per_lot = max(premium * (stop_pct / 100.0) * lot_size, 1.0)
            lots = max(1, int(risk_budget / risk_per_lot))
            lots = min(lots, 2)
            trade = Trade(
                id=str(uuid.uuid4()),
                order_id=str(uuid.uuid4()),
                symbol=signal['symbol'],
                strike=signal.get('strike', 0.0),
                option_type=signal.get('option_type', 'CALL'),
                expiry=expiry_str,
                entry_time=datetime.utcnow(),
                entry_price=premium,
                quantity=lots,
                lot_size=lot_size,
                premium_paid=signal.get('premium', 0.0),
                strategy=signal.get('strategy', 'UNKNOWN'),
                strategy_confidence=signal.get('confidence', 0.0),
                status='OPEN'
            )
            try:
                setattr(trade, 'direction', signal.get('direction', 'BULLISH'))
                setattr(trade, 'target_price_hint', signal.get('target_price'))
                setattr(trade, 'stop_loss_hint', signal.get('stop_loss'))
                token = self.kite.subscribe_option(signal['symbol'], float(signal.get('strike', 0.0)), signal.get('option_type', 'CALL'))
                if token:
                    setattr(trade, 'option_token', token)
                setattr(trade, 'option_target_premium', round(premium * (1 + tgt_pct / 100.0), 2))
                setattr(trade, 'option_stop_premium', round(premium * (1 - stop_pct / 100.0), 2))
            except Exception:
                pass
            self._open_positions.append(trade)
            logger.info(f"Trade executed (PAPER): {trade.symbol} {trade.option_type}")
            self.alerts.send_trade(f"✅ PAPER TRADE EXECUTED: <b>{trade.symbol}</b> {trade.option_type}")
            return trade
        except Exception as e:
            logger.error(f"Trade execution failed: {e}")
            return None

    def get_open_positions(self) -> List[Trade]:
        return list(self._open_positions)

    def check_position_exits(self, position: Trade):
        before_reason = getattr(position, 'exit_reason', None)
        position = self.exit_mgr.check(position)
        try:
            if getattr(position, 'partial_exit', False) and getattr(position, 'partial_exit_price', None) is not None:
                cfg = get_config()
                pct = int(cfg.get('partial_book_pct', 0))
                if pct and pct > 0 and position.quantity > 1:
                    qty_partial = max(1, int(position.quantity * pct / 100.0))
                    qty_partial = min(qty_partial, position.quantity - 1)
                else:
                    qty_partial = 1 if position.quantity > 1 else 0
                if qty_partial > 0:
                    if self.mode.lower() == 'live' and hasattr(self.kite, 'place_order'):
                        try:
                            self.kite.place_order(
                                tradingsymbol=position.symbol,
                                exchange='NFO',
                                transaction_type='SELL',
                                order_type='MARKET',
                                quantity=qty_partial * position.lot_size,
                                price=None,
                                product='MIS',
                                validity='DAY',
                                variety='regular'
                            )
                        except Exception:
                            pass
                    qty = qty_partial * position.lot_size
                    exit_p = float(getattr(position, 'partial_exit_price', position.entry_price))
                    try:
                        entry_prem = float(getattr(position, 'premium_paid', 0.0))
                        pnl_part = (exit_p - entry_prem) * qty
                    except Exception:
                        dirn = getattr(position, 'direction', 'BULLISH')
                        delta = (exit_p - float(position.entry_price))
                        if dirn == 'BEARISH':
                            delta = -delta
                        pnl_part = delta * qty
                    position.quantity = max(0, position.quantity - qty_partial)
                    try:
                        position.pnl = float(getattr(position, 'pnl', 0.0)) + float(pnl_part)
                    except Exception:
                        position.pnl = float(pnl_part)
                    self.alerts.send_trade(f"🟨 PARTIAL BOOKED: <b>{position.symbol}</b> qty {qty_partial} pnl ₹{pnl_part:.2f}")
                    setattr(position, 'partial_booked', True)
                    setattr(position, 'partial_exit', False)
                    setattr(position, 'partial_exit_price', None)
        except Exception:
            pass
        if getattr(position, 'exit_reason', None) and getattr(position, 'exit_price', None) is not None:
            qty = position.quantity * position.lot_size
            try:
                entry_prem = float(getattr(position, 'premium_paid', 0.0))
                if entry_prem and position.exit_price:
                    position.pnl = (float(position.exit_price) - entry_prem) * qty
                else:
                    dirn = getattr(position, 'direction', 'BULLISH')
                    delta = (float(position.exit_price) - float(position.entry_price))
                    if dirn == 'BEARISH':
                        delta = -delta
                    position.pnl = delta * qty
            except Exception:
                dirn = getattr(position, 'direction', 'BULLISH')
                delta = (float(position.exit_price) - float(position.entry_price))
                if dirn == 'BEARISH':
                    delta = -delta
                position.pnl = delta * qty
        else:
            return
        if self.mode.lower() == 'live' and hasattr(self.kite, 'place_order'):
            try:
                qty = position.quantity * position.lot_size
                self.kite.place_order(
                    tradingsymbol=position.symbol,
                    exchange='NFO',
                    transaction_type='SELL',
                    order_type='MARKET',
                    quantity=qty,
                    price=None,
                    product='MIS',
                    validity='DAY',
                    variety='regular'
                )
            except Exception:
                pass
        position.status = 'CLOSED'
        logger.info(f"Position closed: {position.symbol} PnL={position.pnl:.2f}")
        self.alerts.send_trade(f"📊 POSITION CLOSED: <b>{position.symbol}</b> P&L ₹{position.pnl:.2f} ({position.exit_reason})")

    def force_close_all(self, reason: str = 'FORCE_EXIT'):
        try:
            remaining = []
            for pos in list(self._open_positions):
                try:
                    before_reason = getattr(pos, 'exit_reason', None)
                    token = getattr(pos, 'option_token', None)
                    ltp = 0.0
                    try:
                        if hasattr(self.exit_mgr, '_current_option_price'):
                            ltp = float(self.exit_mgr._current_option_price(token, pos.entry_price))
                    except Exception:
                        ltp = float(pos.entry_price)
                    pos.exit_time = datetime.utcnow()
                    pos.exit_price = float(ltp)
                    pos.exit_reason = reason
                    qty = pos.quantity * pos.lot_size
                    try:
                        entry_prem = float(getattr(pos, 'premium_paid', 0.0))
                        if entry_prem and pos.exit_price:
                            pos.pnl = (float(pos.exit_price) - entry_prem) * qty
                        else:
                            dirn = getattr(pos, 'direction', 'BULLISH')
                            delta = (float(pos.exit_price) - float(pos.entry_price))
                            if dirn == 'BEARISH':
                                delta = -delta
                            pos.pnl = delta * qty
                    except Exception:
                        dirn = getattr(pos, 'direction', 'BULLISH')
                        delta = (float(pos.exit_price) - float(pos.entry_price))
                        if dirn == 'BEARISH':
                            delta = -delta
                        pos.pnl = delta * qty
                    if self.mode.lower() == 'live' and hasattr(self.kite, 'place_order'):
                        try:
                            qty_live = pos.quantity * pos.lot_size
                            self.kite.place_order(
                                tradingsymbol=pos.symbol,
                                exchange='NFO',
                                transaction_type='SELL',
                                order_type='MARKET',
                                quantity=qty_live,
                                price=None,
                                product='MIS',
                                validity='DAY',
                                variety='regular'
                            )
                        except Exception:
                            pass
                    pos.status = 'CLOSED'
                    logger.info(f"Position closed: {pos.symbol} PnL={pos.pnl:.2f}")
                    self.alerts.send_trade(f"📊 POSITION CLOSED: <b>{pos.symbol}</b> P&L ₹{pos.pnl:.2f} ({pos.exit_reason})")
                except Exception:
                    remaining.append(pos)
            self._open_positions = remaining
        except Exception:
            return
