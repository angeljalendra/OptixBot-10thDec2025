import sys

import yfinance as yf
import pandas as pd
import numpy as np
import time
import json
import os
import socket
import requests
import schedule
import threading
import argparse
import random
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta, time as dtime
from typing import List, Dict, Tuple, Optional
import logging
import traceback
from colorama import Fore, Style
from sys import stdout
import math
import calendar
import psutil
from kiteconnect.exceptions import InputException, TokenException, PermissionException, OrderException

from scipy.stats import norm

# --- SECURITY FIX: Load Environment Variables ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("Warning: python-dotenv not installed. Using system environment variables.")

# CRITICAL SECURITY FIX: Load credentials from environment
API_KEY = os.getenv('KITE_API_KEY')
API_SECRET = os.getenv('KITE_API_SECRET')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# Check if essential keys are missing (Priority 1)
if not API_KEY or not API_SECRET:
    print(f"{Fore.YELLOW}WARNING: KITE API credentials not found in environment.{Style.RESET_ALL}")
# ------------------------------------------------

# Attempt to import Flask/SocketIO/KiteConnect
try:
    from flask import Flask, render_template, jsonify, Response
    from flask_socketio import SocketIO
    from kiteconnect import KiteConnect, KiteTicker
    KITE_AVAILABLE = True
except ImportError:
    Flask, SocketIO, KiteConnect, KiteTicker = None, None, None, None
    KITE_AVAILABLE = False
    print("WARNING: Flask/SocketIO/KiteConnect/KiteTicker not found. Running in headless mode.")

# Optional Zerodha wrapper (centralized broker operations)
try:
    from api.zerodha import KiteAPI
except Exception:
    KiteAPI = None

try:
    from core.win_rate_strategy import WinRateOptimizer
except Exception:
    WinRateOptimizer = None

# Optional alerts module
try:
    from monitoring.alerts import Alerts
except Exception:
    Alerts = None

DEFAULT_COMMODITY_UNIVERSE = ['CRUDEOIL', 'NATURALGAS', 'GOLD', 'SILVER', 'COPPER']
COMMODITY_YF_MAP = {
    'CRUDEOIL': 'CL=F',
    'NATURALGAS': 'NG=F',
    'GOLD': 'GC=F',
    'SILVER': 'SI=F',
    'COPPER': 'HG=F'
}
DEFAULT_NSE_UNIVERSE = [
    'RELIANCE','TCS','INFY','HDFCBANK','ICICIBANK','SBIN',
    'LT','ITC','HINDUNILVR','AXISBANK','KOTAKBANK','MARUTI',
    'SUNPHARMA','BHARTIARTL','TATASTEEL','ADANIENT'
]

# Centralized market windows
MARKET_WINDOWS = {
    'NSE': {'OPEN': 9 * 60 + 15, 'CLOSE': 15 * 60 + 30, 'CUTOFF': 15 * 60},
    'MCX': {'OPEN': 9 * 60 + 0, 'CLOSE': 23 * 60 + 55}
}

# --- CONFIG & PORTFOLIO FILES ---
os.makedirs('config', exist_ok=True)
os.makedirs('data', exist_ok=True)
os.makedirs('logs', exist_ok=True)

CONFIG_FILE = 'config/config.json'
PORTFOLIO_FILE = 'data/portfolio.json'
TRADE_JOURNAL_FILE = 'data/trade_journal.json'
TOKEN_FILE = "kite_access_token.json"
LTP_SNAPSHOT_FILE = 'data/ltp_snapshot.json'

# --- LOGGING SETUP ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

file_handler = RotatingFileHandler("logs/trading.log", maxBytes=10 * 1024 * 1024, backupCount=5)
file_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)

console_handler = logging.StreamHandler(stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

if not logger.handlers:
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

# ==================================================================================
# KITE INTEGRATION AND DYNAMIC DATA FETCHING
# ==================================================================================

def initialize_kite(api_key: str, api_secret: str, request_token: Optional[str] = None, allow_input: bool = True) -> Optional[KiteConnect]:
    """Initializes KiteConnect and forces token verification/refresh."""
    if not KITE_AVAILABLE or not KiteConnect:
        logger.critical("KiteConnect library not found. Initialization failed.")
        return None

    kite = KiteConnect(api_key=api_key)
    access_token = None

    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r") as f:
                data = json.load(f)
                access_token = data.get("access_token")
            kite.set_access_token(access_token)
            logger.info("Kite access token loaded from file.")

            # CRITICAL FIX: Verify token immediately with a simple call
            try:
                user_profile = kite.profile()
                logger.info(f"Kite session verified for user: {user_profile.get('user_name')}")
                return kite
            except Exception as profile_e:
                logger.warning(f"Existing Kite token is expired or invalid. Error: {profile_e}")
                access_token = None # Force generation of a new token

        except Exception as e:
            logger.error(f"Error loading token file: {e}")

    if not access_token:
        logger.warning("Kite token not found or expired. Authorization required.")
        print(f"Open this URL and login: {kite.login_url()}")
        try:
            if request_token is None:
                if not allow_input:
                    logger.critical("Request token not provided and interactive input disabled. Cannot initialize Kite.")
                    return None
                request_token = input("Enter request_token from URL: ").strip()
            session_data = kite.generate_session(request_token, api_secret=api_secret)
            # ---------------------------------------------

            # CRITICAL FIX: Convert datetime objects to string before saving
            if 'login_time' in session_data and isinstance(session_data['login_time'], datetime):
                session_data['login_time'] = session_data['login_time'].isoformat()

            with open(TOKEN_FILE, "w") as f:
                json.dump(session_data, f, indent=4)

            kite.set_access_token(session_data["access_token"])
            logger.info("Kite session generated successfully.")
            return kite
        except Exception as e:
            logger.critical(f"Kite session generation failed. Error: {e}")
            traceback.print_exc()
            return None

    return kite

def fetch_fo_instruments(kite: KiteConnect) -> Tuple[List[str], Dict[str, int], Dict[str, int]]:
    """Fetches F&O symbols, lot sizes, and instrument tokens (Symbol -> Token)."""
    if not kite:
        logger.critical("Kite object is None or KiteConnect not available.")
        return [], {}, {}

    try:
        instruments = kite.instruments("NFO")
        futures = [i for i in instruments if i["segment"] in ["NFO-FUT"]]
        options = [i for i in instruments if i["segment"] in ["NFO-OPT"]]

        fo_symbols = []
        lot_sizes = {}
        token_map_symbol_to_token = {}

        for instrument in futures:
            symbol = instrument["name"]

            if symbol not in fo_symbols:
                fo_symbols.append(symbol)

            ls = instrument.get("lot_size")
            if ls:
                lot_sizes[symbol] = int(ls)

            # Map symbol to its primary NSE Equity token for market data
            # NOTE: NSE Equity tokens are handled in _load_nse_instrument_map

        for instrument in options:
            symbol = instrument["name"]
            if symbol not in fo_symbols:
                fo_symbols.append(symbol)
            ls = instrument.get("lot_size")
            if ls and symbol not in lot_sizes:
                lot_sizes[symbol] = int(ls)

        fo_symbols.sort()
        logger.info(f"Dynamically fetched {len(fo_symbols)} F&O symbols.")
        return fo_symbols, lot_sizes, token_map_symbol_to_token

    except Exception as e:
        logger.error(f"Failed to fetch F&O instruments from Kite: {e}.")
        return [], {}, {}

def fetch_commodity_instruments(kite: KiteConnect) -> Tuple[List[str], Dict[str, int], Dict[str, int]]:
    """Fetches commodity symbols, lot sizes, and instrument tokens from MCX."""
    if not kite:
        logger.critical("Kite object is None or KiteConnect not available.")
        return [], {}, {}

    try:
        instruments = kite.instruments("MCX")
        commodity_futures = [i for i in instruments if i.get("segment") in ["MCX-FUT"]]
        commodity_options = [i for i in instruments if i.get("segment") in ["MCX-OPT"]]

        commodity_symbols = []
        commodity_lot_sizes = {}
        commodity_token_map = {}

        # Collect futures by symbol to choose nearest valid expiry
        futures_by_symbol: Dict[str, List[dict]] = {}
        for instrument in commodity_futures:
            symbol = instrument.get("name")
            if not symbol:
                continue

            futures_by_symbol.setdefault(symbol, []).append(instrument)

            if symbol not in commodity_symbols:
                commodity_symbols.append(symbol)

            ls = instrument.get("lot_size")
            if ls:
                commodity_lot_sizes[symbol] = int(ls)

        # Choose earliest upcoming expiry per symbol
        now_dt = datetime.now()
        for symbol, futs in futures_by_symbol.items():
            try:
                def expiry_key(inst):
                    exp = inst.get("expiry")
                    try:
                        if isinstance(exp, datetime):
                            return (exp.date() < now_dt.date(), exp)
                        # Fallback to string compare
                        return (str(exp) < str(now_dt.date()), str(exp))
                    except Exception:
                        return (True, str(exp))

                # Sort: prefer upcoming (not past) earliest expiry
                futs.sort(key=expiry_key)
                chosen = None
                # pick first with expiry >= today if available, else first overall
                for inst in futs:
                    exp = inst.get("expiry")
                    if isinstance(exp, datetime):
                        if exp.date() >= now_dt.date():
                            chosen = inst
                            break
                if not chosen:
                    chosen = futs[0]

                token = chosen.get("instrument_token")
                if token:
                    commodity_token_map[symbol] = int(token)
            except Exception:
                # If selection fails, try first available
                try:
                    token = futs[0].get("instrument_token")
                    if token:
                        commodity_token_map[symbol] = int(token)
                except Exception:
                    pass

        for instrument in commodity_options:
            symbol = instrument["name"]
            if symbol not in commodity_symbols:
                commodity_symbols.append(symbol)
            ls = instrument.get("lot_size")
            if ls and symbol not in commodity_lot_sizes:
                commodity_lot_sizes[symbol] = int(ls)

        commodity_symbols.sort()
        logger.info(f"Dynamically fetched {len(commodity_symbols)} commodity symbols from MCX.")
        return commodity_symbols, commodity_lot_sizes, commodity_token_map

    except Exception as e:
        logger.error(f"Failed to fetch commodity instruments from Kite: {e}.")
        return [], {}, {}

# ==================================================================================
# DASHBOARD IMPLEMENTATION
# ==================================================================================

class DashboardApp:
    def __init__(self, trader, port: int = 5024):
        if not KITE_AVAILABLE:
            logger.critical("Dashboard dependencies missing. Cannot initialize dashboard.")
            return

        template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
        self.app = Flask(__name__, template_folder=template_dir)
        import logging
        logging.getLogger('werkzeug').setLevel(logging.ERROR)
        logging.getLogger('engineio').setLevel(logging.ERROR)
        logging.getLogger('socketio').setLevel(logging.ERROR)
        logging.getLogger('websocket').setLevel(logging.ERROR)
        logging.getLogger('kiteconnect').setLevel(logging.ERROR)
        self.app.logger.propagate = False
        self.app.logger.handlers = []
        self.app.config['SECRET_KEY'] = 'secret!'
        self.socketio = SocketIO(self.app, async_mode='threading', ping_interval=15, ping_timeout=30)
        self.trader = trader
        self.port = port

        os.makedirs(template_dir, exist_ok=True)

        @self.app.route('/')
        def index():
            try:
                init = self.trader.package_dashboard_data() if self.trader else {}
            except Exception:
                init = {}
            return render_template('dashboard.html', initial_data=json.dumps(init))

        # Multi-page routes that render the same dashboard with tab selection handled client-side
        @self.app.route('/portfolio')
        def portfolio_page():
            try:
                init = self.trader.package_dashboard_data() if self.trader else {}
            except Exception:
                init = {}
            return render_template('dashboard.html', initial_data=json.dumps(init))

        @self.app.route('/positions')
        def positions_page():
            try:
                init = self.trader.package_dashboard_data() if self.trader else {}
            except Exception:
                init = {}
            return render_template('dashboard.html', initial_data=json.dumps(init))

        @self.app.route('/signals')
        def signals_page():
            try:
                init = self.trader.package_dashboard_data() if self.trader else {}
            except Exception:
                init = {}
            return render_template('dashboard.html', initial_data=json.dumps(init))

        @self.app.route('/trades')
        def trades_page():
            try:
                init = self.trader.package_dashboard_data() if self.trader else {}
            except Exception:
                init = {}
            return render_template('dashboard.html', initial_data=json.dumps(init))

        @self.app.route('/feed')
        def feed_page():
            try:
                init = self.trader.package_dashboard_data() if self.trader else {}
            except Exception:
                init = {}
            return render_template('dashboard.html', initial_data=json.dumps(init))

        @self.app.route('/health')
        def health_page():
            try:
                init = self.trader.package_dashboard_data() if self.trader else {}
            except Exception:
                init = {}
            return render_template('dashboard.html', initial_data=json.dumps(init))

        @self.app.route('/api/logs')
        def api_logs():
            try:
                from flask import request
                level = request.args.get('level', 'all')
            except Exception:
                level = 'all'
            return jsonify([])

        @self.app.route('/api/metrics', methods=['POST'])
        def api_metrics():
            from flask import request
            try:
                data = request.get_json(force=True)
                if self.socketio:
                    self.socketio.emit('metrics', data)
                return jsonify({'status': 'ok'})
            except Exception as e:
                logger.error(f"/api/metrics failed: {e}")
                return jsonify({'status': 'error'}), 500

        @self.app.route('/api/signal', methods=['POST'])
        def api_signal():
            from flask import request
            try:
                data = request.get_json(force=True)
                if self.socketio:
                    self.socketio.emit('signal', data)
                return jsonify({'status': 'ok'})
            except Exception as e:
                logger.error(f"/api/signal failed: {e}")
                return jsonify({'status': 'error'}), 500

        @self.app.route('/api/position', methods=['POST'])
        def api_position():
            from flask import request
            try:
                data = request.get_json(force=True)
                if self.socketio:
                    self.socketio.emit('position', data)
                return jsonify({'status': 'ok'})
            except Exception as e:
                logger.error(f"/api/position failed: {e}")
                return jsonify({'status': 'error'}), 500

        @self.app.route('/api/close_position', methods=['POST'])
        def api_close_position():
            try:
                from flask import request
                data = request.get_json(force=True) or {}
                symbol = (data.get('symbol') or '').strip()
                strike = data.get('strike') or data.get('strike_price')
                if not self.trader:
                    return jsonify({'status': 'no_trader'}), 400
                # Find matching position key
                pos_key = None
                for pk, pos in list(self.trader.paper_portfolio.get('positions', {}).items()):
                    if (str(pos.get('symbol','')) == str(symbol)) and (str(pos.get('strike_price','')) == str(strike)):
                        pos_key = pk
                        break
                if not pos_key:
                    return jsonify({'status': 'not_found'}), 404
                # Use real-time LTP when available for safer exit
                position = self.trader.paper_portfolio['positions'].get(pos_key)
                exit_prem = position.get('current_premium', position.get('premium_paid', 0)) or 0
                try:
                    ts = position.get('tradingsymbol')
                    if not ts:
                        ts = self.trader.find_nfo_tradingsymbol(position.get('symbol'), position.get('strike_price'), position.get('option_type'))
                        if ts:
                            position['tradingsymbol'] = ts
                    if ts and getattr(self.trader, 'kite', None):
                        q = self.trader.safe_quote(f"NFO:{ts}")
                        lp = q.get(f"NFO:{ts}", {}).get('last_price')
                        if lp and lp > 0.05:
                            exit_prem = float(lp)
                except Exception:
                    pass
                try:
                    logger.info(f"Manual exit requested for {position.get('symbol')} {position.get('option_type')} {position.get('strike_price')}: using LTP ₹{float(exit_prem):.2f} to derive SELL LIMIT")
                except Exception:
                    pass
                reason = data.get('reason', 'MANUAL EXIT')
                self.trader.close_option_position(pos_key, float(exit_prem), reason)
                packaged = self.trader.package_dashboard_data()
                # Push update to UI
                try:
                    self.socketio.emit('dashboard_update', packaged)
                except Exception:
                    pass
                return Response(json.dumps(packaged), mimetype='application/json')
            except Exception as e:
                logger.error(f"/api/close_position failed: {e}")
                return jsonify({'status': 'error'}), 500

        @self.app.route('/api/refresh_signals', methods=['POST'])
        def api_refresh_signals():
            try:
                if self.trader:
                    self.trader.reload_state_from_disk()
                    packaged = self.trader.package_dashboard_data()
                    return Response(json.dumps(packaged), mimetype='application/json')
                return jsonify({'status': 'no_trader'}), 400
            except Exception as e:
                logger.error(f"/api/refresh_signals failed: {e}")
                return jsonify({'status': 'error'}), 500

        @self.app.route('/api/admin/reset_daily', methods=['POST'])
        def api_reset_daily():
            try:
                if not self.trader:
                    return jsonify({'ok': False, 'error': 'no_trader'}), 400
                today = datetime.now().strftime('%Y-%m-%d')
                # Reset today's P&L and consecutive losses
                try:
                    if 'daily_pnl' not in self.trader.trade_journal:
                        self.trader.trade_journal['daily_pnl'] = {}
                    self.trader.trade_journal['daily_pnl'][today] = 0
                except Exception:
                    pass
                try:
                    self.trader.paper_portfolio['consecutive_losses'] = 0
                except Exception:
                    pass
                try:
                    self.trader.detected_signals_today.clear()
                except Exception:
                    pass
                try:
                    save_trade_journal(self.trader.trade_journal)
                except Exception:
                    pass
                packaged = self.trader.package_dashboard_data()
                return Response(json.dumps({'ok': True, 'reset': today, 'state': packaged}), mimetype='application/json')
            except Exception as e:
                logger.error(f"/api/admin/reset_daily failed: {e}")
                return jsonify({'ok': False, 'error': 'reset_failed'}), 500

        @self.app.route('/api/market-data')
        def api_market_data():
            try:
                trader = getattr(self, 'trader', None)
                kite = getattr(trader, 'kite', None) if trader else getattr(self, 'kite', None)
                out = {}
                mapping = {
                    'nifty': 'NSE:NIFTY 50',
                    'banknifty': 'NSE:NIFTY BANK',
                    'finnifty': 'NSE:NIFTY FIN SERVICE',
                    'sensex': 'BSE:SENSEX'
                }
                if kite and getattr(kite, 'access_token', None):
                    keys = list(mapping.values())
                    quotes = kite.quote(keys)
                    for k, key in mapping.items():
                        q = quotes.get(key, {})
                        price = float(q.get('last_price') or 0)
                        ohlc = q.get('ohlc') or {}
                        prev = float(ohlc.get('close') or price)
                        change = price - prev
                        pct = (change / prev * 100.0) if prev else 0.0
                        out[k] = {'price': price, 'change': change, 'change_percent': pct}
                    return jsonify(out)
                for k in ['nifty','banknifty','finnifty','sensex']:
                    out[k] = {'price': 0.0, 'change': 0.0, 'change_percent': 0.0}
                return jsonify(out)
            except Exception:
                return jsonify({
                    'nifty': {'price': 0.0, 'change': 0.0, 'change_percent': 0.0},
                    'banknifty': {'price': 0.0, 'change': 0.0, 'change_percent': 0.0},
                    'finnifty': {'price': 0.0, 'change': 0.0, 'change_percent': 0.0},
                    'sensex': {'price': 0.0, 'change': 0.0, 'change_percent': 0.0}
                })

        @self.app.route('/api/state')
        def api_state():
            try:
                if self.trader:
                    packaged = self.trader.package_dashboard_data()
                    try:
                        if (not packaged.get('positions')) and self.trader.paper_portfolio.get('positions'):
                            pos_list = []
                            for pk, pos in self.trader.paper_portfolio['positions'].items():
                                pnl_abs = ((pos.get('current_premium', pos.get('premium_paid', 0)) or 0) - (pos.get('premium_paid', 0) or 0)) * (pos.get('lot_size', 0) or 0) * (pos.get('quantity', 0) or 0)
                                current_value = (pos.get('current_premium', pos.get('premium_paid', 0)) or 0) * (pos.get('lot_size', 0) or 0) * (pos.get('quantity', 0) or 0)
                                pos_list.append({
                                    'symbol': pos.get('symbol'),
                                    'option_type': pos.get('option_type'),
                                    'strike_price': pos.get('strike_price'),
                                    'strike': pos.get('strike_price'),
                                    'entry_premium': pos.get('premium_paid'),
                                    'current_premium': pos.get('current_premium'),
                                    'lot_size': pos.get('lot_size'),
                                    'quantity': pos.get('quantity'),
                                    'avg_price': pos.get('premium_paid', 0),
                                    'ltp': pos.get('current_premium', 0),
                                    'value': current_value,
                                    'entry_time': pos.get('entry_time'),
                                    'order_id': pos.get('order_id'),
                                    'tradingsymbol': pos.get('tradingsymbol'),
                                    'stop_loss': pos.get('stop_loss'),
                                    'stop_loss_spot': pos.get('stop_loss'),
                                    'target': pos.get('target'),
                                    'target_spot': pos.get('target'),
                                    'pnl': pnl_abs,
                                    'pnl_pct': pos.get('pnl_pct'),
                                    'confidence': pos.get('confidence'),
                                    'risk_reward': pos.get('risk_reward'),
                                    'delta': pos.get('delta'),
                                    'theta': pos.get('theta'),
                                    'vega': pos.get('vega'),
                                    'iv': pos.get('iv')
                                })
                            packaged['positions'] = pos_list
                    except Exception:
                        pass
                    _rs = getattr(self.trader, 'recent_signals', [])
                    if not _rs:
                        try:
                            _rs = list((self.trader.trade_journal.get('recent_signals') or []))
                        except Exception:
                            _rs = []
                    base_recent = list((_rs or [])[-50:])
                    enriched = []
                    open_symbols = set([p.get('symbol') for p in packaged.get('positions', []) if p.get('symbol')])
                    const_min_conf = float(getattr(self.trader, 'min_confidence', 0) or 0)
                    const_min_rr = float(getattr(self.trader, 'min_rr_ratio', 0) or 0)
                    for s in base_recent:
                        s2 = dict(s)
                        if (s2.get('status') == 'PENDING') and (not s2.get('pending_reason') or str(s2.get('pending_reason')).lower() == 'unknown'):
                            try:
                                conf = float(s2.get('confidence') or 0)
                            except Exception:
                                conf = 0.0
                            try:
                                rr = float(s2.get('risk_reward') or 0)
                            except Exception:
                                rr = 0.0
                            if s2.get('symbol') in open_symbols:
                                s2['pending_reason'] = 'position_already_open'
                            elif conf < const_min_conf:
                                s2['pending_reason'] = 'confidence_below_min'
                            elif rr < const_min_rr:
                                s2['pending_reason'] = 'risk_reward_below_min'
                            else:
                                s2['pending_reason'] = 'awaiting_execution'
                                s2['pending_reason_detail'] = {
                                    'confidence': conf,
                                    'min_confidence': const_min_conf,
                                    'risk_reward': rr,
                                    'min_rr_ratio': const_min_rr,
                                    'position_open': bool(s2.get('symbol') in open_symbols)
                                }
                        enriched.append(s2)
                    packaged['signals'] = enriched
                    return Response(json.dumps(packaged), mimetype='application/json')
                cfg = load_config()
                cap = float(cfg.get('capital', 0) or 0)
                return Response(json.dumps({
                    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
                    'summary': {
                        'initial_capital': cap,
                        'portfolio_value': cap,
                        'cash': cap,
                        'margin_used': 0.0,
                        'exposure_value': 0.0,
                        'total_pnl': 0.0,
                        'total_pnl_pct': 0.0,
                        'active_trades': 0,
                        'exit_signals_count': 0,
                        'health_status': 'HEALTHY',
                        'strategy': 'UNKNOWN',
                        'market_regime': 'MIXED',
                        'scan_cycles': 0,
                        'stocks_scanned': 0,
                        'signals_found': 0,
                        'trades_executed': 0,
                        'api_calls': 0,
                        'consecutive_losses': 0,
                        'daily_pnl': 0.0,
                        'journal': {
                            'win_rate': 0.0,
                            'total_trades': 0,
                            'profitable_trades': 0,
                            'avg_win': 0.0,
                            'avg_loss': 0.0,
                            'largest_win': 0.0,
                            'largest_loss': 0.0,
                            'profit_factor': 0.0
                        }
                    },
                    'positions': [],
                    'signals': []
                }), mimetype='application/json')
            except Exception as e:
                logger.error(f"/api/state failed: {e}")
                return Response(json.dumps({'error': 'state_unavailable'}), mimetype='application/json', status=503)

        @self.app.route('/api/aggregate_state')
        def api_aggregate_state():
            return api_state()

        @self.app.route('/api/simple_state')
        def api_simple_state():
            try:
                if self.trader:
                    packaged = self.trader.package_dashboard_data()
                    try:
                        if (not packaged.get('positions')) and self.trader.paper_portfolio.get('positions'):
                            pos_list = []
                            for pk, pos in self.trader.paper_portfolio['positions'].items():
                                pnl_abs = ((pos.get('current_premium', pos.get('premium_paid', 0)) or 0) - (pos.get('premium_paid', 0) or 0)) * (pos.get('lot_size', 0) or 0) * (pos.get('quantity', 0) or 0)
                                current_value = (pos.get('current_premium', pos.get('premium_paid', 0)) or 0) * (pos.get('lot_size', 0) or 0) * (pos.get('quantity', 0) or 0)
                                pos_list.append({
                                    'symbol': pos.get('symbol'),
                                    'option_type': pos.get('option_type'),
                                    'strike_price': pos.get('strike_price'),
                                    'strike': pos.get('strike_price'),
                                    'entry_premium': pos.get('premium_paid'),
                                    'current_premium': pos.get('current_premium'),
                                    'lot_size': pos.get('lot_size'),
                                    'quantity': pos.get('quantity'),
                                    'avg_price': pos.get('premium_paid', 0),
                                    'ltp': pos.get('current_premium', 0),
                                    'value': current_value,
                                    'entry_time': pos.get('entry_time'),
                                    'order_id': pos.get('order_id'),
                                    'tradingsymbol': pos.get('tradingsymbol'),
                                    'stop_loss': pos.get('stop_loss'),
                                    'stop_loss_spot': pos.get('stop_loss'),
                                    'target': pos.get('target'),
                                    'target_spot': pos.get('target'),
                                    'pnl': pnl_abs,
                                    'pnl_pct': pos.get('pnl_pct'),
                                    'confidence': pos.get('confidence'),
                                    'risk_reward': pos.get('risk_reward'),
                                    'delta': pos.get('delta'),
                                    'theta': pos.get('theta'),
                                    'vega': pos.get('vega'),
                                    'iv': pos.get('iv')
                                })
                            packaged['positions'] = pos_list
                    except Exception:
                        pass
                    _rs = getattr(self.trader, 'recent_signals', [])
                    if not _rs:
                        try:
                            _rs = list((self.trader.trade_journal.get('recent_signals') or []))
                        except Exception:
                            _rs = []
                    base_recent = list((_rs or [])[-20:])
                    enriched = []
                    open_symbols = set([p.get('symbol') for p in packaged.get('positions', []) if p.get('symbol')])
                    const_min_conf = float(getattr(self.trader, 'min_confidence', 0) or 0)
                    const_min_rr = float(getattr(self.trader, 'min_rr_ratio', 0) or 0)
                    for s in base_recent:
                        s2 = dict(s)
                        if (s2.get('status') == 'PENDING') and (not s2.get('pending_reason') or str(s2.get('pending_reason')).lower() == 'unknown'):
                            try:
                                conf = float(s2.get('confidence') or 0)
                            except Exception:
                                conf = 0.0
                            try:
                                rr = float(s2.get('risk_reward') or 0)
                            except Exception:
                                rr = 0.0
                            if s2.get('symbol') in open_symbols:
                                s2['pending_reason'] = 'position_already_open'
                            elif conf < const_min_conf:
                                s2['pending_reason'] = 'confidence_below_min'
                            elif rr < const_min_rr:
                                s2['pending_reason'] = 'risk_reward_below_min'
                            else:
                                s2['pending_reason'] = 'awaiting_execution'
                                s2['pending_reason_detail'] = {
                                    'confidence': conf,
                                    'min_confidence': const_min_conf,
                                    'risk_reward': rr,
                                    'min_rr_ratio': const_min_rr,
                                    'position_open': bool(s2.get('symbol') in open_symbols)
                                }
                        enriched.append(s2)
                    packaged['signals'] = enriched
                    return Response(json.dumps(packaged), mimetype='application/json')
                cfg = load_config()
                cap = float(cfg.get('capital', 0) or 0)
                return Response(json.dumps({
                    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
                    'summary': {
                        'initial_capital': cap,
                        'portfolio_value': cap,
                        'cash': cap,
                        'margin_used': 0.0,
                        'exposure_value': 0.0,
                        'total_pnl': 0.0,
                        'total_pnl_pct': 0.0,
                        'active_trades': 0,
                        'exit_signals_count': 0,
                        'health_status': 'HEALTHY',
                        'strategy': 'UNKNOWN',
                        'market_regime': 'MIXED',
                        'scan_cycles': 0,
                        'stocks_scanned': 0,
                        'signals_found': 0,
                        'trades_executed': 0,
                        'api_calls': 0,
                        'consecutive_losses': 0,
                        'daily_pnl': 0.0,
                        'journal': {
                            'win_rate': 0.0,
                            'total_trades': 0,
                            'profitable_trades': 0,
                            'avg_win': 0.0,
                            'avg_loss': 0.0,
                            'largest_win': 0.0,
                            'largest_loss': 0.0,
                            'profit_factor': 0.0
                        }
                    },
                    'positions': [],
                    'signals': []
                }), mimetype='application/json')
            except Exception as e:
                logger.error(f"/api/simple_state failed: {e}")
                return Response(json.dumps({'error': 'state_unavailable'}), mimetype='application/json', status=503)

        @self.app.route('/api/audit')
        def api_audit():
            try:
                if not self.trader:
                    return jsonify({'ok': False, 'error': 'no_trader'}), 400
                t = self.trader
                cfg = t.config if hasattr(t, 'config') else {}
                perf = t.trade_journal.get('performance_metrics', {}) if isinstance(getattr(t, 'trade_journal', {}), dict) else {}
                vix_val = None
                try:
                    vix_limit = float(cfg.get('max_vix', 20.0))
                except Exception:
                    vix_limit = 20.0
                try:
                    if bool(cfg.get('high_precision_mode')):
                        vix_limit = float(cfg.get('precision_max_vix', vix_limit))
                except Exception:
                    pass
                try:
                    if t.kite:
                        q = t.safe_quote("NSE:INDIA VIX")
                        vix_val = q.get("NSE:INDIA VIX", {}).get('last_price')
                        if vix_val is not None:
                            vix_val = float(vix_val)
                except Exception:
                    vix_val = None
                try:
                    is_mcx_ctx = bool((t.strategy_params.get('market_type') == 'COMMODITY'))
                except Exception:
                    is_mcx_ctx = False
                late_block = (not is_mcx_ctx) and (datetime.now().hour >= 15)
                try:
                    thresh_conf = float(getattr(t, 'min_confidence', 0) or 0)
                except Exception:
                    thresh_conf = 0.0
                try:
                    thresh_rr = float(getattr(t, 'min_rr_ratio', 0) or 0)
                except Exception:
                    thresh_rr = 0.0
                out = {
                    'ok': True,
                    'strategy': getattr(t, 'display_strategy_key', getattr(t, 'active_strategy_key', 'UNKNOWN')),
                    'exit_policy': getattr(t, 'exit_policy', 'unknown'),
                    'market_regime': getattr(t, 'active_market_regime', 'UNKNOWN'),
                    'precision': {
                        'enabled': bool(cfg.get('high_precision_mode')),
                        'min_confidence': float(cfg.get('precision_min_confidence', thresh_conf)),
                        'min_rr_ratio': float(cfg.get('precision_min_rr_ratio', thresh_rr)),
                        'max_vix': float(cfg.get('precision_max_vix', cfg.get('max_vix', 20.0))),
                        'max_signals_per_side': int(cfg.get('precision_max_signals_per_side', cfg.get('max_signals_per_side', 0))),
                        'cooldown_minutes': int(cfg.get('precision_cooldown_minutes', 20)),
                        'max_correlation': float(cfg.get('precision_max_correlation', 0.7))
                    },
                    'validation': {
                        'min_win_rate_threshold': float(cfg.get('min_win_rate_threshold', 55.0)),
                        'min_profit_factor_threshold': float(cfg.get('min_profit_factor_threshold', 1.2)),
                        'min_closed_trades_for_validation': int(cfg.get('min_closed_trades_for_validation', 10))
                    },
                    'performance': {
                        'win_rate': float(perf.get('win_rate', 0) or 0),
                        'profit_factor': float(perf.get('profit_factor', 0) or 0),
                        'total_trades': int(perf.get('total_trades', 0) or 0),
                        'profitable_trades': int(perf.get('profitable_trades', 0) or 0)
                    },
                    'portfolio': {
                        'consecutive_losses': int(t.paper_portfolio.get('consecutive_losses', 0) or 0),
                        'initial_capital': float(t.paper_portfolio.get('initial_capital', 0) or 0),
                        'portfolio_value': float(t.paper_portfolio.get('total_value', 0) or 0)
                    },
                    'thresholds_in_effect': {
                        'min_confidence': thresh_conf,
                        'min_rr_ratio': thresh_rr
                    },
                    'gates': {
                        'late_entry_window': bool(late_block),
                        'vix_value': vix_val,
                        'vix_limit': vix_limit,
                        'vix_block': bool(vix_val is not None and vix_val > vix_limit),
                        'nifty_filter_bypass': bool(t.strategy_params.get('ignore_nifty_filter', False))
                    }
                }
                return Response(json.dumps(out), mimetype='application/json')
            except Exception as e:
                logger.error(f"/api/audit failed: {e}")
                return jsonify({'ok': False, 'error': 'audit_failed'}), 500

        @self.app.route('/api/index_quotes')
        def api_index_quotes():
            try:
                out = {}
                mapping = {
                    'nifty': 'NSE:NIFTY 50',
                    'banknifty': 'NSE:NIFTY BANK',
                    'finnifty': 'NSE:NIFTY FIN SERVICE',
                    'sensex': 'BSE:SENSEX'
                }
                trader = getattr(self, 'trader', None)
                kite = getattr(trader, 'kite', None) if trader else getattr(self, 'kite', None)
                if kite and getattr(kite, 'access_token', None):
                    try:
                        keys = list(mapping.values())
                        quotes = kite.quote(keys)
                        for k, key in mapping.items():
                            q = quotes.get(key, {})
                            price = float(q.get('last_price') or 0)
                            ohlc = q.get('ohlc') or {}
                            prev = float(ohlc.get('close') or price)
                            change = price - prev
                            pct = (change / prev * 100.0) if prev else 0.0
                            out[k] = {'price': price, 'change': change, 'change_percent': pct}
                        return Response(json.dumps(out), mimetype='application/json')
                    except Exception:
                        pass
                for k in ['nifty','banknifty','finnifty','sensex']:
                    out[k] = {'price': 0.0, 'change': 0.0, 'change_percent': 0.0}
                return Response(json.dumps(out), mimetype='application/json')
            except Exception as e:
                logger.error(f"/api/index_quotes failed: {e}")
                return Response(json.dumps({'error': 'index_unavailable'}), mimetype='application/json', status=503)

        @self.app.route('/api/kite_status')
        def api_kite_status():
            try:
                trader = getattr(self, 'trader', None)
                kite = getattr(trader, 'kite', None) if trader else None
                ws = getattr(trader, 'kws', None) if trader else None
                user = None
                connected = False
                if kite and getattr(kite, 'access_token', None):
                    try:
                        prof = kite.profile()
                        user = prof.get('user_name')
                        connected = True
                    except Exception:
                        connected = False
                status = {
                    'connected': connected,
                    'user': user,
                    'mode': getattr(trader, 'mode', 'UNKNOWN') if trader else 'UNKNOWN',
                    'ws_connected': bool(ws and ws.is_connected()) if ws else False,
                    'subscribed_tokens': {
                        'options': len(getattr(trader, 'open_option_tokens', set()) or set()),
                        'spots': len(getattr(trader, 'open_spot_tokens', set()) or set())
                    },
                    'last_tick_ts': getattr(trader, 'last_tick_time', None)
                }
                # Optional: include today's orders count
                try:
                    if kite and connected:
                        orders = kite.orders()
                        status['orders_today'] = len([o for o in orders if str(o.get('order_timestamp','')).split('T')[0] == datetime.now().strftime('%Y-%m-%d')])
                    else:
                        status['orders_today'] = 0
                except Exception:
                    status['orders_today'] = None
                return Response(json.dumps(status), mimetype='application/json')
            except Exception as e:
                logger.error(f"/api/kite_status failed: {e}")
                return Response(json.dumps({'connected': False, 'ws_connected': False}), mimetype='application/json', status=503)

        @self.app.route('/api/aggregate_kite_status')
        def api_aggregate_kite_status():
            return api_kite_status()

        @self.app.route('/api/start', methods=['POST'])
        def api_start():
            try:
                if self.trader:
                    self.trader.start_automatic_scheduler()
                    return jsonify({'ok': True, 'status': 'started'})
                return jsonify({'ok': False, 'error': 'no_trader'}), 400
            except Exception as e:
                logger.error(f"/api/start failed: {e}")
                return jsonify({'ok': False, 'error': 'start_failed'}), 500

        @self.app.route('/api/stop', methods=['POST'])
        def api_stop():
            try:
                if self.trader:
                    self.trader.stop_automatic_scheduler()
                    return jsonify({'ok': True, 'status': 'stopped'})
                return jsonify({'ok': False, 'error': 'no_trader'}), 400
            except Exception as e:
                logger.error(f"/api/stop failed: {e}")
                return jsonify({'ok': False, 'error': 'stop_failed'}), 500

        @self.app.route('/api/reset', methods=['POST'])
        def api_reset():
            try:
                if self.trader:
                    self.trader.reset_for_new_day()
                    self.socketio.emit('dashboard_update', self.trader.package_dashboard_data())
                    return jsonify({'ok': True, 'status': 'reset'})
                return jsonify({'ok': False, 'error': 'no_trader'}), 400
            except Exception as e:
                logger.error(f"/api/reset failed: {e}")
                return jsonify({'ok': False, 'error': 'reset_failed'}), 500

        @self.app.route('/api/reload', methods=['POST'])
        def api_reload():
            try:
                if self.trader:
                    self.trader.reload_state_from_disk()
                    return jsonify({'ok': True, 'status': 'reloaded'})
                return jsonify({'ok': False, 'error': 'no_trader'}), 400
            except Exception as e:
                logger.error(f"/api/reload failed: {e}")
                return jsonify({'ok': False, 'error': 'reload_failed'}), 500

        @self.app.route('/api/config/exit_policy', methods=['POST'])
        def api_set_exit_policy():
            from flask import request
            try:
                if not self.trader:
                    return jsonify({'ok': False, 'error': 'no_trader'}), 400
                payload = request.get_json(force=False, silent=True) or {}
                policy = (payload.get('exit_policy') or request.args.get('exit_policy') or '').strip().lower()
                allowed = ['hybrid', 'per_position_only', 'portfolio_only', 'fixed_target', 'time_only']
                if policy not in allowed:
                    return jsonify({'ok': False, 'error': 'invalid exit_policy', 'allowed': allowed}), 400
                self.trader.exit_policy = policy
                return jsonify({'ok': True, 'exit_policy': self.trader.exit_policy})
            except Exception as e:
                logger.error(f"/api/config/exit_policy failed: {e}")
                return jsonify({'ok': False, 'error': 'set_failed'}), 500

        @self.app.route('/api/config/strategy/adaptive', methods=['POST'])
        def api_set_adaptive_mode():
            try:
                if not self.trader:
                    return jsonify({'ok': False, 'error': 'no_trader'}), 400
                self.trader.display_strategy_key = 'ADAPTIVE_AUTO'
                initial_regime = self.trader.assess_market_regime()
                final_strategy_key = self.trader.REGIME_STRATEGY_MAP.get(initial_regime, 'HIGH_CONVICTION')
                self.trader.set_active_strategy(final_strategy_key)
                self.trader.active_market_regime = initial_regime
                self.trader.multi_strategy_mode = False
                self.trader.enabled_strategies = [final_strategy_key]
                logger.info(f"AUTOMODE: Switched to ADAPTIVE_AUTO (current: {final_strategy_key}, regime: {initial_regime})")
                try:
                    snapshot = self.trader.package_dashboard_data()
                    self.socketio.emit('dashboard_update', snapshot)
                except Exception:
                    pass
                return jsonify({'ok': True, 'mode': 'ADAPTIVE_AUTO', 'active_strategy': final_strategy_key, 'regime': initial_regime})
            except Exception as e:
                logger.error(f"/api/config/strategy/adaptive failed: {e}")
                return jsonify({'ok': False, 'error': 'adaptive_failed'}), 500

        @self.app.route('/api/scan/nse', methods=['POST'])
        def api_scan_nse():
            try:
                if not self.trader:
                    return jsonify({'ok': False, 'error': 'no_trader'}), 400
                regime = self.trader.assess_market_regime()
                try:
                    key = self.trader.select_best_strategy_nse(regime)
                except Exception:
                    key = self.trader.REGIME_STRATEGY_MAP.get(regime, 'HIGH_CONVICTION')
                self.trader.run_strategy_scan(key)
                try:
                    snapshot = self.trader.package_dashboard_data()
                    self.socketio.emit('dashboard_update', snapshot)
                except Exception:
                    pass
                return jsonify({'ok': True, 'strategy': key, 'regime': regime})
            except Exception as e:
                logger.error(f"/api/scan/nse failed: {e}")
                return jsonify({'ok': False, 'error': 'scan_failed'}), 500

        @self.app.route('/api/scan/mcx', methods=['POST'])
        def api_scan_mcx():
            try:
                if not self.trader:
                    return jsonify({'ok': False, 'error': 'no_trader'}), 400
                regime = self.trader.assess_commodity_regime()
                try:
                    key = self.trader.select_best_strategy_mcx(regime)
                except Exception:
                    key = 'COMMODITY_TREND' if regime == 'TRENDING' else 'COMMODITY_SCALP'
                self.trader.run_strategy_scan(key)
                try:
                    snapshot = self.trader.package_dashboard_data()
                    self.socketio.emit('dashboard_update', snapshot)
                except Exception:
                    pass
                return jsonify({'ok': True, 'strategy': key, 'regime': regime})
            except Exception as e:
                logger.error(f"/api/scan/mcx failed: {e}")
                return jsonify({'ok': False, 'error': 'scan_failed'}), 500

        @self.app.route('/api/config/update', methods=['POST'])
        def api_config_update():
            from flask import request
            try:
                payload = request.get_json(force=False, silent=True) or {}
                cap = payload.get('capital')
                enabled = payload.get('enabled_strategies') or []
                alloc = payload.get('strategy_allocations') or {}
                if self.trader:
                    cfg = self.trader.config if hasattr(self.trader, 'config') else {}
                    if cap is not None:
                        try:
                            cfg['capital'] = float(cap)
                        except Exception:
                            pass
                    if isinstance(enabled, list):
                        cfg['enabled_strategies'] = enabled
                    if isinstance(alloc, dict):
                        cfg['strategy_allocations'] = alloc
                    try:
                        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
                        with open(CONFIG_FILE, 'w') as f:
                            json.dump(cfg, f, indent=4)
                    except Exception:
                        pass
                    try:
                        self.trader.multi_strategy_mode = bool(enabled and len(enabled) > 1)
                        self.trader.enabled_strategies = [k for k in enabled if k in self.trader.STRATEGY_CONFIGS] if enabled else [getattr(self.trader, 'active_strategy_key', 'HIGH_CONVICTION')]
                        self.trader.strategy_allocations = alloc
                        self.trader._compute_strategy_weights()
                    except Exception:
                        pass
                    try:
                        snapshot = self.trader.package_dashboard_data()
                        self.socketio.emit('dashboard_update', snapshot)
                    except Exception:
                        pass
                return jsonify({'ok': True})
            except Exception as e:
                logger.error(f"/api/config/update failed: {e}")
                return jsonify({'ok': False, 'error': 'update_failed'}), 500

        @self.app.route('/api/config/high_precision', methods=['POST'])
        def api_config_high_precision():
            from flask import request
            try:
                payload = request.get_json(force=False, silent=True) or {}
                enable = bool(payload.get('enable', True))
                if self.trader:
                    cfg = self.trader.config if hasattr(self.trader, 'config') else {}
                    cfg['high_precision_mode'] = enable
                    try:
                        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
                        with open(CONFIG_FILE, 'w') as f:
                            json.dump(cfg, f, indent=4)
                    except Exception:
                        pass
                    self.trader.config = cfg
                    try:
                        snapshot = self.trader.package_dashboard_data()
                        self.socketio.emit('dashboard_update', snapshot)
                    except Exception:
                        pass
                return jsonify({'ok': True, 'high_precision_mode': enable})
            except Exception as e:
                logger.error(f"/api/config/high_precision failed: {e}")
                return jsonify({'ok': False, 'error': 'set_failed'}), 500

        @self.app.route('/api/config/validation', methods=['POST'])
        def api_config_validation():
            from flask import request
            try:
                payload = request.get_json(force=False, silent=True) or {}
                allowed_keys = [
                    'min_win_rate_threshold','min_profit_factor_threshold','min_closed_trades_for_validation',
                    'precision_min_confidence','precision_min_rr_ratio','precision_max_vix','precision_max_signals_per_side',
                    'precision_cooldown_minutes','precision_max_correlation','max_consecutive_losses','max_risk_per_trade_pct',
                    'time_exit_minutes','partial_book_pct','max_signals_per_side','exit_policy'
                ]
                if self.trader:
                    cfg = self.trader.config if hasattr(self.trader, 'config') else {}
                    for k, v in payload.items():
                        if k in allowed_keys:
                            cfg[k] = v
                    try:
                        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
                        with open(CONFIG_FILE, 'w') as f:
                            json.dump(cfg, f, indent=4)
                    except Exception:
                        pass
                    self.trader.config = cfg
                    try:
                        snapshot = self.trader.package_dashboard_data()
                        self.socketio.emit('dashboard_update', snapshot)
                    except Exception:
                        pass
                return jsonify({'ok': True, 'updated': {k: payload.get(k) for k in allowed_keys if k in payload}})
            except Exception as e:
                logger.error(f"/api/config/validation failed: {e}")
                return jsonify({'ok': False, 'error': 'update_failed'}), 500

        @self.app.route('/api/strategies/exit-policy', methods=['POST'])
        def api_strategies_exit_policy():
            from flask import request
            try:
                payload = request.get_json(force=False, silent=True) or {}
                val = (payload.get('exit_policy') or request.args.get('exit_policy') or 'hybrid').strip().lower()
                if self.trader:
                    self.trader.exit_policy = val
                return jsonify({'ok': True, 'exit_policy': val})
            except Exception as e:
                logger.error(f"/api/strategies/exit-policy failed: {e}")
                return jsonify({'ok': False, 'error': 'set_failed'}), 500

        @self.app.route('/api/strategies/start-all', methods=['POST'])
        def api_strategies_start_all():
            try:
                if not self.trader:
                    return jsonify({'ok': False, 'error': 'no_trader'}), 400
                self.trader.start_automatic_scheduler()
                try:
                    snapshot = self.trader.package_dashboard_data()
                    self.socketio.emit('dashboard_update', snapshot)
                except Exception:
                    pass
                return jsonify({'ok': True})
            except Exception as e:
                logger.error(f"/api/strategies/start-all failed: {e}")
                return jsonify({'ok': False, 'error': 'start_failed'}), 500

        @self.app.route('/api/strategies/stop-all', methods=['POST'])
        def api_strategies_stop_all():
            try:
                if not self.trader:
                    return jsonify({'ok': False, 'error': 'no_trader'}), 400
                self.trader.stop_automatic_scheduler()
                try:
                    snapshot = self.trader.package_dashboard_data()
                    self.socketio.emit('dashboard_update', snapshot)
                except Exception:
                    pass
                return jsonify({'ok': True})
            except Exception as e:
                logger.error(f"/api/strategies/stop-all failed: {e}")
                return jsonify({'ok': False, 'error': 'stop_failed'}), 500

        @self.app.route('/api/strategies/reset-all', methods=['POST'])
        def api_strategies_reset_all():
            try:
                if not self.trader:
                    return jsonify({'ok': False, 'error': 'no_trader'}), 400
                self.trader.reset_for_new_day()
                try:
                    snapshot = self.trader.package_dashboard_data()
                    self.socketio.emit('dashboard_update', snapshot)
                except Exception:
                    pass
                return jsonify({'ok': True})
            except Exception as e:
                logger.error(f"/api/strategies/reset-all failed: {e}")
                return jsonify({'ok': False, 'error': 'reset_failed'}), 500

        @self.app.route('/api/strategies/start/<strategy>', methods=['POST'])
        def api_strategy_start(strategy: str):
            try:
                if not self.trader:
                    return jsonify({'ok': False, 'error': 'no_trader'}), 400
                key = str(strategy).strip().upper()
                if key not in self.trader.STRATEGY_CONFIGS:
                    return jsonify({'ok': False, 'error': 'unknown_strategy'}), 400
                try:
                    if getattr(self.trader, 'multi_strategy_mode', False):
                        arr = set(self.trader.enabled_strategies or [])
                        arr.add(key)
                        self.trader.enabled_strategies = [k for k in arr if k in self.trader.STRATEGY_CONFIGS]
                        self.trader._compute_strategy_weights()
                    else:
                        self.trader.set_active_strategy(key)
                    self.trader.run_strategy_scan(key)
                except Exception:
                    pass
                try:
                    snapshot = self.trader.package_dashboard_data()
                    self.socketio.emit('dashboard_update', snapshot)
                except Exception:
                    pass
                return jsonify({'ok': True, 'strategy': key})
            except Exception as e:
                logger.error(f"/api/strategies/start/<key> failed: {e}")
                return jsonify({'ok': False, 'error': 'start_failed'}), 500

        @self.app.route('/api/strategies/stop/<strategy>', methods=['POST'])
        def api_strategy_stop(strategy: str):
            try:
                if not self.trader:
                    return jsonify({'ok': False, 'error': 'no_trader'}), 400
                key = str(strategy).strip().upper()
                try:
                    if getattr(self.trader, 'multi_strategy_mode', False):
                        self.trader.enabled_strategies = [k for k in (self.trader.enabled_strategies or []) if k != key]
                        self.trader._compute_strategy_weights()
                except Exception:
                    pass
                try:
                    snapshot = self.trader.package_dashboard_data()
                    self.socketio.emit('dashboard_update', snapshot)
                except Exception:
                    pass
                return jsonify({'ok': True, 'strategy': key})
            except Exception as e:
                logger.error(f"/api/strategies/stop/<key> failed: {e}")
                return jsonify({'ok': False, 'error': 'stop_failed'}), 500

        @self.app.route('/api/strategies/reset/<strategy>', methods=['POST'])
        def api_strategy_reset(strategy: str):
            try:
                if not self.trader:
                    return jsonify({'ok': False, 'error': 'no_trader'}), 400
                try:
                    self.trader.reload_state_from_disk()
                except Exception:
                    pass
                try:
                    snapshot = self.trader.package_dashboard_data()
                    self.socketio.emit('dashboard_update', snapshot)
                except Exception:
                    pass
                return jsonify({'ok': True, 'strategy': str(strategy).strip().upper()})
            except Exception as e:
                logger.error(f"/api/strategies/reset/<key> failed: {e}")
                return jsonify({'ok': False, 'error': 'reset_failed'}), 500

        @self.app.route('/api/kite/login_url')
        def api_kite_login_url():
            try:
                if not KITE_AVAILABLE or not KiteConnect:
                    return jsonify({'ok': False, 'error': 'kite_unavailable'}), 400
                kite = KiteConnect(api_key=API_KEY) if API_KEY else KiteConnect(api_key='')
                return jsonify({'ok': True, 'login_url': kite.login_url()})
            except Exception as e:
                logger.error(f"/api/kite/login_url failed: {e}")
                return jsonify({'ok': False, 'error': 'login_url_failed'}), 500

        @self.app.route('/api/kite/generate_session', methods=['POST'])
        def api_kite_generate_session():
            from flask import request
            try:
                if not KITE_AVAILABLE or not KiteConnect:
                    return jsonify({'ok': False, 'error': 'kite_unavailable'}), 400
                payload = request.get_json(force=False, silent=True) or {}
                rt = (payload.get('request_token') or '').strip()
                kite = initialize_kite(API_KEY, API_SECRET, request_token=rt, allow_input=False)
                if kite:
                    if self.trader:
                        self.trader.kite = kite
                    return jsonify({'ok': True})
                return jsonify({'ok': False, 'error': 'session_failed'}), 400
            except Exception as e:
                logger.error(f"/api/kite/generate_session failed: {e}")
                return jsonify({'ok': False, 'error': 'session_failed'}), 500

        @self.app.route('/api/kite/set_access_token', methods=['POST'])
        def api_kite_set_access_token():
            from flask import request
            try:
                if not KITE_AVAILABLE or not KiteConnect:
                    return jsonify({'ok': False, 'error': 'kite_unavailable'}), 400
                payload = request.get_json(force=False, silent=True) or {}
                val = (payload.get('access_token') or '').strip()
                if not val:
                    return jsonify({'ok': False, 'error': 'no_token'}), 400
                kite = KiteConnect(api_key=API_KEY)
                kite.set_access_token(val)
                try:
                    user_profile = kite.profile()
                except Exception:
                    pass
                try:
                    with open(TOKEN_FILE, 'w') as f:
                        json.dump({'access_token': val}, f, indent=4)
                except Exception:
                    pass
                if self.trader:
                    self.trader.kite = kite
                return jsonify({'ok': True})
            except Exception as e:
                logger.error(f"/api/kite/set_access_token failed: {e}")
                return jsonify({'ok': False, 'error': 'set_failed'}), 500

        @self.socketio.on('connect')
        def on_connect():
            try:
                if self.trader:
                    try:
                        self.trader.reload_state_from_disk()
                    except Exception:
                        pass
                    snapshot = self.trader.package_dashboard_data()
                    self.socketio.emit('dashboard_update', snapshot)
            except Exception:
                pass

        @self.socketio.on('ping_check')
        def on_ping_check(ts=None):
            try:
                self.socketio.emit('pong_check', {'ts': ts or time.time()})
            except Exception:
                pass

        @self.socketio.on('request_update')
        def on_request_update():
            try:
                if self.trader:
                    self.emit_portfolio_update(self.trader.package_dashboard_data())
            except Exception:
                pass

    def emit_portfolio_update(self, portfolio_data: Dict):
        """Emits the current structured portfolio data to all connected clients."""
        if self.socketio:
            self.socketio.emit('dashboard_update', portfolio_data)

    def start_dashboard(self):
        """Starts the Flask server in a non-blocking way using eventlet."""
        if not KITE_AVAILABLE: return
        try:
            logger.info(f"🌐 Starting Dashboard at http://0.0.0.0:{self.port}/")
            try:
                def _heartbeat():
                    while True:
                        try:
                            self.socketio.emit('server_heartbeat', {'ts': time.time()})
                        except Exception:
                            pass
                        time.sleep(10)
                threading.Thread(target=_heartbeat, daemon=True).start()
            except Exception:
                pass
            self.socketio.run(self.app, host='0.0.0.0', port=self.port, log_output=False, debug=False, allow_unsafe_werkzeug=True)
        except Exception as e:
            logger.error(f"Failed to start Dashboard server: {e}")

# ==================================================================================
# CORE HELPERS
# ==================================================================================

def load_config():
    try:
        from config.settings import get_config
        return get_config()
    except Exception as e:
        logger.error(f"Config load failed: {e}. Using empty config.")
        return {}

def load_portfolio() -> Dict:
    cfg = load_config()
    cap = float(cfg.get('capital', 500000))
    default_portfolio = {
        'cash': cap,
        'positions': {},
        'total_value': cap,
        'initial_capital': cap,
        'margin_used': 0,
        'realized_pnl_total': 0,
        'consecutive_losses': 0,
        'peak_portfolio_value': cap
    }

    loaded_portfolio = {}

    try:
        os.makedirs(os.path.dirname(PORTFOLIO_FILE), exist_ok=True)
        if os.path.exists(PORTFOLIO_FILE):
            with open(PORTFOLIO_FILE, 'r') as f:
                loaded_portfolio = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load portfolio file: {e}. Using default state.")
        pass

    force_reset = False
    try:
        v1 = os.getenv('RESET_PORTFOLIO')
        v2 = os.getenv('CAPITAL_FORCE')
        force_reset = (str(v1).strip() == '1') or (str(v2).strip() == '1')
    except Exception:
        force_reset = False
    if force_reset:
        return default_portfolio

    final_portfolio = default_portfolio.copy()
    final_portfolio.update(loaded_portfolio)

    if final_portfolio['positions']:
         estimated_position_value = sum(
            (pos.get('current_premium', pos.get('premium_paid', pos.get('entry_price', 0))) or 0) * (pos.get('lot_size', 0) or 0) * (pos.get('quantity', 0) or 0)
            for pos in final_portfolio['positions'].values()
         )
         final_portfolio['total_value'] = final_portfolio['cash'] + estimated_position_value
    else:
        if float(final_portfolio.get('initial_capital', 0) or 0) != cap:
            final_portfolio['initial_capital'] = cap
            final_portfolio['cash'] = cap
            final_portfolio['margin_used'] = 0
            final_portfolio['peak_portfolio_value'] = cap
        final_portfolio['total_value'] = final_portfolio['cash']

    if 'consecutive_losses' not in final_portfolio:
        final_portfolio['consecutive_losses'] = 0

    if 'peak_portfolio_value' not in final_portfolio:
        final_portfolio['peak_portfolio_value'] = final_portfolio['initial_capital']

    return final_portfolio

def save_portfolio(portfolio: Dict):
    """Saves the paper trading portfolio state."""
    try:
        os.makedirs(os.path.dirname(PORTFOLIO_FILE), exist_ok=True)
        def _json_sanitize(x):
            import datetime
            try:
                import numpy as np
            except Exception:
                np = None
            if isinstance(x, (str, int, float, bool)) or x is None:
                return x
            if np is not None and isinstance(x, (np.integer,)):
                return int(x)
            if np is not None and isinstance(x, (np.floating,)):
                return float(x)
            if np is not None and isinstance(x, (np.bool_)):
                return bool(x)
            if isinstance(x, (datetime.datetime, datetime.date)):
                return x.isoformat()
            if isinstance(x, dict):
                return {str(k): _json_sanitize(v) for k, v in x.items()}
            if isinstance(x, (list, tuple, set)):
                return [_json_sanitize(i) for i in list(x)]
            if hasattr(x, 'item'):
                try:
                    return _json_sanitize(x.item())
                except Exception:
                    pass
            return str(x)
        with open(PORTFOLIO_FILE, 'w') as f:
            json.dump(_json_sanitize(portfolio), f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save portfolio: {e}")

def load_trade_journal() -> Dict:
    """Loads the trade journal for performance tracking."""
    default_journal = {
        'closed_trades': [],
        'daily_pnl': {},
        'recent_signals': [],
        'performance_metrics': {
            'win_rate': 0,
            'avg_win': 0,
            'avg_loss': 0,
            'largest_win': 0,
            'largest_loss': 0,
            'total_trades': 0,
            'profitable_trades': 0,
            'profit_factor': 0,
            'daily_pnl': 0
        },
        'backtest_summary': {}
    }

    try:
        os.makedirs(os.path.dirname(TRADE_JOURNAL_FILE), exist_ok=True)
        if os.path.exists(TRADE_JOURNAL_FILE):
            with open(TRADE_JOURNAL_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load trade journal: {e}")

    return default_journal

def save_trade_journal(journal: Dict):
    """Saves the trade journal."""
    try:
        os.makedirs(os.path.dirname(TRADE_JOURNAL_FILE), exist_ok=True)
        def _json_sanitize(x):
            import datetime
            try:
                import numpy as np
            except Exception:
                np = None
            if isinstance(x, (str, int, float, bool)) or x is None:
                return x
            if np is not None and isinstance(x, (np.integer,)):
                return int(x)
            if np is not None and isinstance(x, (np.floating,)):
                return float(x)
            if np is not None and isinstance(x, (np.bool_)):
                return bool(x)
            if isinstance(x, (datetime.datetime, datetime.date)):
                return x.isoformat()
            if isinstance(x, dict):
                return {str(k): _json_sanitize(v) for k, v in x.items()}
            if isinstance(x, (list, tuple, set)):
                return [_json_sanitize(i) for i in list(x)]
            if hasattr(x, 'item'):
                try:
                    return _json_sanitize(x.item())
                except Exception:
                    pass
            return str(x)
        with open(TRADE_JOURNAL_FILE, 'w') as f:
            json.dump(_json_sanitize(journal), f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save trade journal: {e}")


def execute_trade(trade_plan: Dict, quantity: int = 1) -> bool:
    """Stubbed trade execution - returns True to allow paper accounting."""
    # NOTE: In production, this would be kite.place_order()
    logger.debug(f"Attempting to execute paper trade for {trade_plan['symbol']}...")
    return True

# ==================================================================================
# CORE TRADER CLASS
# ==================================================================================
class UltimateFNOTrader:

    # 1. STRATEGY CONFIGURATIONS (Now includes 'expiry_mode' and 'ignore_nifty_filter')
    STRATEGY_CONFIGS = {
        "HIGH_CONVICTION": {
            "min_conf": 9.0, "min_rr": 1.8, "lot_multiplier": 2, "tsl_activation_pct": 10.0,
            "tsl_pullback_pct": 4.0, "max_profit_pct": 20.0,
            "description": "MACD/Trend Breakout, Aggressive TSL (10/5).",
            "quick_cash_target": 25000,
            "time_exit_minutes": 25,
            "partial_book_pct": 50,
            "ignore_nifty_filter": False,
            "expiry_mode": "ROBUST_AUTO"
        },
        "AGGRESSIVE_REVERSAL": {
            "min_conf": 8.5, "min_rr": 2.0, "lot_multiplier": 2, "tsl_activation_pct": 5.0,
            "tsl_pullback_pct": 2.5, "max_profit_pct": 12.0,
            "description": "RSI Extreme/S&R Touch, Hyper-Aggressive TSL (5/2.5).",
            "quick_cash_target": 15000,
            "time_exit_minutes": 12,
            "partial_book_pct": 50,
            "ignore_nifty_filter": True, # BYPASS filter for aggressive counter-trend
            "expiry_mode": "ROBUST_AUTO"
        },
        "MID_TREND_SWING": {
            "min_conf": 8.5, "min_rr": 2.2, "lot_multiplier": 1, "tsl_activation_pct": 12.0,
            "tsl_pullback_pct": 5.0, "max_profit_pct": 25.0,
            "description": "Conservative 50/200 SMA Alignment, Wide TSL for max swing.",
            "quick_cash_target": 25000,
            "time_exit_minutes": 30,
            "partial_book_pct": 50,
            "ignore_nifty_filter": False,
            "expiry_mode": "ROBUST_AUTO"
        },
        "AGGRESSIVE_SCALP": {
            "min_conf": 8.2, "min_rr": 1.5, "lot_multiplier": 4, "tsl_activation_pct": 6.0,
            "tsl_pullback_pct": 3.0, "max_profit_pct": 12.0,
            "description": "High Volume Scalp, Maximize Lot Size, Fast 25k Exit.",
            "quick_cash_target": 10000,
            "time_exit_minutes": 10,
            "partial_book_pct": 50,
            "ignore_nifty_filter": False,
            "expiry_mode": "ROBUST_AUTO"
        },
        "ADAPTIVE_AUTO": { # Strategy key for full autonomous operation
            "min_conf": 9.8, "min_rr": 2.6, "lot_multiplier": 1, "tsl_activation_pct": 5.0,
            "tsl_pullback_pct": 2.5, "max_profit_pct": 12.0,
            "description": "Adaptive Baseline for Automatic Switching.",
            "quick_cash_target": 10000,
            "time_exit_minutes": 6,
            "partial_book_pct": 70,
            "ignore_nifty_filter": False,
            "expiry_mode": "ROBUST_AUTO"
        },
        # COMMODITY TRADING STRATEGIES
        "COMMODITY_TREND": {
            "min_conf": 8.0, "min_rr": 2.0, "lot_multiplier": 2, "tsl_activation_pct": 10.0,
            "tsl_pullback_pct": 5.0, "max_profit_pct": 20.0,
            "description": "Commodity trend following with moderate TSL.",
            "quick_cash_target": 20000,
            "time_exit_minutes": 45,
            "partial_book_pct": 40,
            "ignore_nifty_filter": True, # Commodities don't follow Nifty
            "expiry_mode": "COMMODITY_AUTO",
            "market_type": "COMMODITY"
        },
        "COMMODITY_REVERSAL": {
            "min_conf": 7.5, "min_rr": 1.8, "lot_multiplier": 3, "tsl_activation_pct": 6.0,
            "tsl_pullback_pct": 3.0, "max_profit_pct": 15.0,
            "description": "Commodity reversal trading with tight TSL.",
            "quick_cash_target": 15000,
            "time_exit_minutes": 25,
            "partial_book_pct": 50,
            "ignore_nifty_filter": True, # Commodities don't follow Nifty
            "expiry_mode": "COMMODITY_AUTO",
            "market_type": "COMMODITY"
        },
        "COMMODITY_SCALP": {
            "min_conf": 8.5, "min_rr": 1.3, "lot_multiplier": 4, "tsl_activation_pct": 4.0,
            "tsl_pullback_pct": 2.0, "max_profit_pct": 10.0,
            "description": "High-frequency commodity scalping.",
            "quick_cash_target": 8000,
            "time_exit_minutes": 8,
            "partial_book_pct": 60,
            "ignore_nifty_filter": True, # Commodities don't follow Nifty
            "expiry_mode": "COMMODITY_AUTO",
            "market_type": "COMMODITY"
        }
    }

    # Map market states to the strategy keys for AUTO-SWITCHING
    REGIME_STRATEGY_MAP = {
        "TRENDING": "HIGH_CONVICTION",
        "RANGING": "AGGRESSIVE_REVERSAL",
        "SIDEWAYS": "MID_TREND_SWING"
    }

    def __init__(self, dashboard_app, initial_strategy_key="HIGH_CONVICTION", request_token_override: Optional[str] = None, allow_input: bool = True):
        self.config = load_config()
        try:
            mr = os.getenv('MAX_RISK_PCT')
            if mr:
                self.config['max_risk_per_trade_pct'] = float(mr)
        except Exception:
            pass
        self.mode = self.config.get('mode', 'PAPER')
        self.exit_policy = self.config.get('exit_policy', 'hybrid')

        # --- DYNAMIC STRATEGY SELECTION ---
        self.active_strategy_key = initial_strategy_key
        self.display_strategy_key = initial_strategy_key

        # Load core config and portfolio
        self.load_trade_history()
        self.kite = initialize_kite(API_KEY, API_SECRET, request_token=request_token_override, allow_input=allow_input)
        self.kite_api = None
        try:
            if KiteAPI and self.kite and getattr(self.kite, 'access_token', None):
                self.kite_api = KiteAPI(API_KEY, API_SECRET)
                self.kite_api.set_access_token(self.kite.access_token)
        except Exception:
            self.kite_api = None
        self.nse_fo_stocks, self.lot_sizes, self.token_map = fetch_fo_instruments(self.kite)
        
        # COMMODITY TRADING: Load commodity instruments
        self.commodity_symbols, self.commodity_lot_sizes, self.commodity_token_map = fetch_commodity_instruments(self.kite)
        
        # Remove PAPER fallbacks: rely strictly on broker instruments
        
        self.health_status = "HEALTHY"
        self._verify_lot_sizes_ready()

        # CRITICAL FIX 1: Load NFO instruments for caching during initialization
        self.nfo_instruments = []
        self.last_cache_refresh = datetime.min
        self.refresh_nfo_instruments_cache()
        
        # COMMODITY TRADING: Load MCX instruments cache
        self.mcx_instruments = []
        self.last_mcx_cache_refresh = datetime.min
        self.refresh_mcx_instruments_cache()

        # Load NSE instrument map (used for spot token lookup)
        self.nse_instrument_map = self._load_nse_instrument_map()

        self.token_to_symbol = {}
        try:
            for s, t in (self.token_map or {}).items():
                self.token_to_symbol[int(t)] = s
        except Exception:
            pass
        try:
            for s, t in (self.commodity_token_map or {}).items():
                self.token_to_symbol[int(t)] = s
        except Exception:
            pass
        self.ohlc_5m = {}

        # Apply parameters based on the chosen key
        self.strategy_params = self.STRATEGY_CONFIGS.get(self.active_strategy_key, self.STRATEGY_CONFIGS["HIGH_CONVICTION"])
        self.min_confidence = self.strategy_params["min_conf"]
        self.min_rr_ratio = self.strategy_params["min_rr"]
        self.lot_multiplier = self.strategy_params["lot_multiplier"]
        self.tsl_activation_pct = self.strategy_params["tsl_activation_pct"]
        self.tsl_pullback_pct = self.strategy_params["tsl_pullback_pct"]
        self.max_profit_pct = self.strategy_params["max_profit_pct"]
        self.quick_cash_target = self.strategy_params.get("quick_cash_target", 25000)
        self.time_exit_minutes = self.strategy_params.get("time_exit_minutes", 15)
        self.partial_book_pct = self.strategy_params.get("partial_book_pct", 50)
        try:
            if self.mode == 'PAPER' and self.strategy_params.get('market_type') != 'COMMODITY':
                key = getattr(self, 'display_strategy_key', self.active_strategy_key)
                if key in ['AGGRESSIVE_SCALP','AGGRESSIVE_REVERSAL']:
                    self.min_confidence = max(5.0, float(self.min_confidence) - 1.5)
                    self.required_move = max(2.0, float(getattr(self, 'required_move', 4.0)) * 0.7)
                    self.scan_batch_size = max(24, int(getattr(self, 'scan_batch_size', 8) * 2))
                    logger.info(f"Boosted NSE signal surfacing for PAPER mode (aggressive only): min_conf={self.min_confidence}, required_move={self.required_move}, batch_size={self.scan_batch_size}")
                else:
                    self.required_move = max(3.0, float(getattr(self, 'required_move', 4.0)))
        except Exception:
            pass
        self.max_positions = self.config.get('max_positions', 3)
        self.max_daily_loss_pct = self.config.get('max_daily_loss_pct', 3.0)

        # --- WEB SOCKETS INTEGRATION (GOAL 2) ---
        self.kws = None
        self.live_ltp_cache = {}
        self.open_option_tokens = set()
        self.open_spot_tokens = set() # NEW: Track underlying spot tokens
        self.ws_reconnect_attempts = 0
        self.ws_reconnect_in_progress = False
        self.last_ws_error_log_time = datetime.min
        self.last_ws_close_log_time = datetime.min
        self._initialize_websockets()
        # ----------------------------------------

        self.telegram_token = TELEGRAM_TOKEN or self.config.get('telegram_token')
        self.telegram_chat_id = TELEGRAM_CHAT_ID or self.config.get('telegram_chat_id')
        self.telegram_enabled = self.telegram_token is not None and self.telegram_chat_id is not None

        self.option_strategy = 'ATM'

        self.scheduler_running = False
        self.scan_batch_size = 8  # REDUCED from 15 to avoid API limits
        self.dashboard_app = dashboard_app

        self.SCAN_INTERVAL_MINUTES = 10  # INCREASED from 5 to reduce API calls
        self.last_scan_time = datetime.min
        self.detected_signals_today = set()
        try:
            self.recent_signals = (self.trade_journal.get('recent_signals') or [])[-50:]
        except Exception:
            self.recent_signals = []
        self.today_date = datetime.now().date()
        self.active_market_regime = "INITIALIZING"
        self.backtest_run_today = False

        self.multi_strategy_mode = self.config.get('multi_strategy_mode', False)
        try:
            all_keys = list(self.STRATEGY_CONFIGS.keys())
        except Exception:
            all_keys = []
        self.enabled_strategies = [k for k in self.config.get('enabled_strategies', all_keys) if k in self.STRATEGY_CONFIGS]
        if getattr(self, 'active_strategy_key', None):
            if self.active_strategy_key != 'ADAPTIVE_AUTO':
                self.multi_strategy_mode = False
                self.enabled_strategies = [self.active_strategy_key]
            else:
                self.multi_strategy_mode = False
                self.enabled_strategies = ['ADAPTIVE_AUTO']
        self.strategy_allocations = self.config.get('strategy_allocations')
        self.strategy_weights = {}
        self._compute_strategy_weights()
        try:
            if str(self.config.get('allocation_mode', '')).strip().lower() == 'win_rate':
                self._update_weights_by_win_rate()
        except Exception:
            pass

        # --- CIRCUIT BREAKER ATTRIBUTES ---
        self.max_consecutive_losses = self.config.get('max_consecutive_losses', 3)
        self.consecutive_losses = self.paper_portfolio.get('consecutive_losses', 0)

        self.min_price = 50
        self.required_move = 4.0
        self.quick_cash_target = self.strategy_params.get("quick_cash_target", 25000)
        self.last_ltp_snapshot = {}
        self._load_ltp_snapshot()

        # --- API RATE LIMITING ---
        self.last_api_call = datetime.min
        self.api_failures = 0
        self.max_api_failures = 20
        try:
            self.api_qps_limit = float(os.getenv('API_QPS_LIMIT') or 1.0)
        except Exception:
            self.api_qps_limit = 1.0

        # --- HEALTH MONITORING ---
        self.health_status = "HEALTHY"
        self.start_health_monitor()

        self.last_failed_fetch_log_time = datetime.min
        self.last_failed_fetch_symbols = set()

        # --- TRADE JOURNAL ---
        self.trade_journal = load_trade_journal()
        self.update_performance_metrics()

        # --- LOGGING SUMMARY ATTRIBUTES ---
        self.scan_summary = {
            'cycles_run': 0,
            'stocks_scanned_count': 0,
            'signals_found_count': 0,
            'trades_executed_count': 0,
            'total_api_calls': 0,
            'start_time': datetime.now()
        }

        logger.info(f"📊 Trader Initialized. F&O Stocks: {len(self.nse_fo_stocks)}")
        logger.info(f"💰 Mode: {self.mode}. Capital: ₹{self.paper_portfolio['cash']:,.0f}")
        # Log strategy info after it's properly set
        if hasattr(self, 'min_confidence'):
            logger.info(f"🎯 Strategy: {self.active_strategy_key} (Conf={self.min_confidence}, R:R={self.min_rr_ratio}, Lots={self.lot_multiplier}x)")
        else:
            logger.info(f"🎯 Strategy: {self.active_strategy_key} (parameters not yet loaded)")

        self.network_unavailable = False
        try:
            socket.gethostbyname('api.kite.trade')
        except Exception:
            self.network_unavailable = True

        # Relax thresholds for commodity in PAPER/mock to surface signals
        try:
            if self.strategy_params.get('market_type') == 'COMMODITY' and (not self.kite or not getattr(self, 'commodity_token_map', None)):
                self.required_move = max(1.5, self.required_move * 0.5)
                self.min_confidence = max(6.5, float(self.strategy_params.get('min_conf', self.min_confidence)) - 1.0)
                logger.info(f"Adjusted thresholds for COMMODITY mock: req_move={self.required_move}, min_conf={self.min_confidence}")
        except Exception:
            pass
        self.init_telegram()
        try:
            self.alerts = Alerts() if Alerts else None
        except Exception:
            self.alerts = None

    def _available_cash(self) -> float:
        init_cap = float(self.paper_portfolio.get('initial_capital', 0) or 0)
        margin = float(self.paper_portfolio.get('margin_used', 0) or 0)
        realized = float(self.paper_portfolio.get('realized_pnl_total', 0) or 0)
        return max(0.0, init_cap + realized - margin)

    def _recalc_cash(self) -> None:
        self.paper_portfolio['cash'] = self._available_cash()

    def _available_cash_for_strategy(self) -> float:
        c = self._available_cash()
        try:
            k = getattr(self, 'active_strategy_key', None)
            if k and self.strategy_weights and k in self.strategy_weights:
                return c * float(self.strategy_weights[k])
        except Exception:
            pass
        return c

    def _compute_strategy_weights(self):
        w = {}
        try:
            keys = self.enabled_strategies if self.enabled_strategies else ([self.active_strategy_key] if hasattr(self, 'active_strategy_key') else [])
            if self.strategy_allocations and isinstance(self.strategy_allocations, dict):
                total = sum([float(self.strategy_allocations.get(k, 0)) for k in keys])
                if total > 0:
                    for k in keys:
                        w[k] = float(self.strategy_allocations.get(k, 0)) / total
                else:
                    eq = 1.0 / max(1, len(keys))
                    for k in keys:
                        w[k] = eq
            else:
                eq = 1.0 / max(1, len(keys))
                for k in keys:
                    w[k] = eq
        except Exception:
            pass
        self.strategy_weights = w

    def _update_weights_by_win_rate(self):
        keys = self.enabled_strategies if hasattr(self, 'enabled_strategies') else [getattr(self, 'active_strategy_key', 'HIGH_CONVICTION')]
        ct = self.trade_journal.get('closed_trades', []) if hasattr(self, 'trade_journal') else []
        min_samples = int(self.config.get('min_closed_trades_for_validation', 10))
        if not ct or len(ct) < 1:
            return
        wins_by_k = {k: 0 for k in keys}
        total_by_k = {k: 0 for k in keys}
        for t in ct:
            try:
                k = t.get('strategy')
                if k in total_by_k:
                    total_by_k[k] += 1
                    if float(t.get('pnl') or 0) > 0:
                        wins_by_k[k] += 1
            except Exception:
                pass
        scores = {}
        for k in keys:
            tot = total_by_k.get(k, 0)
            win = wins_by_k.get(k, 0)
            if tot >= max(3, min_samples // 2):
                scores[k] = (win / tot) if tot > 0 else 0.0
            else:
                scores[k] = None
        if all(v is None for v in scores.values()):
            return
        vals = [v for v in scores.values() if v is not None]
        base = sum(vals) if vals else 0.0
        if base <= 0:
            return
        w = {}
        for k in keys:
            v = scores.get(k)
            if v is None:
                continue
            w[k] = v / base
        if w:
            self.strategy_weights = w

    def calculate_black_scholes_delta(self, S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> float:
        """
        [PLACEHOLDER] Calculates the option Delta using the Black-Scholes model.

        S: Spot Price (Underlying)
        K: Strike Price
        T: Time to Expiration (in years)
        r: Risk-free rate (e.g., 0.05 for 5%)
        sigma: Volatility (e.g., 0.20 for 20%)
        option_type: 'CALL' or 'PUT'

        ***REPLACE THE CDF CALCULATION FOR LIVE ACCURACY***
        """

        # Avoid division by zero and log of zero
        if T <= 0 or sigma <= 0 or S <= 0:
            return 1.0 if S >= K and option_type == 'CALL' else 0.0

        try:
            d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))

            # Placeholder for norm.cdf(x).
            # You should replace this with:
            # from scipy.stats import norm; phi_d1 = norm.cdf(d1)
            phi_d1 = norm.cdf(d1)

            if option_type == 'CALL':
                return phi_d1
            else: # PUT
                # Put Delta = Call Delta - 1
                return phi_d1 - 1.0

        except Exception as e:
            logger.error(f"Black-Scholes Delta calculation failed: {e}. Defaulting to 0.5.")
            return 0.5

    def calculate_black_scholes_price(self, S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> float:
        if T <= 0 or sigma <= 0 or S <= 0:
            return max(0.0, S - K) if option_type == 'CALL' else max(0.0, K - S)
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        if option_type == 'CALL':
            return S * norm.cdf(d1) - K * math.exp(-r*T) * norm.cdf(d2)
        else:
            return K * math.exp(-r*T) * norm.cdf(-d2) - S * norm.cdf(-d1)

    def calculate_greeks(self, S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> Dict:
        if T <= 0 or sigma <= 0 or S <= 0:
            return {'delta': 0.0, 'theta': 0.0, 'vega': 0.0}
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        pdf_d1 = norm.pdf(d1)
        if option_type == 'CALL':
            delta = norm.cdf(d1)
            theta = (-S * pdf_d1 * sigma / (2 * math.sqrt(T)) - r * K * math.exp(-r*T) * norm.cdf(d2))
        else:
            delta = norm.cdf(d1) - 1.0
            theta = (-S * pdf_d1 * sigma / (2 * math.sqrt(T)) + r * K * math.exp(-r*T) * norm.cdf(-d2))
        vega = S * pdf_d1 * math.sqrt(T)
        return {'delta': float(delta), 'theta': float(theta), 'vega': float(vega)}

    def implied_volatility(self, price: float, S: float, K: float, T: float, r: float, option_type: str) -> float:
        low = 1e-6
        high = 3.0
        for _ in range(60):
            mid = (low + high) / 2
            p = self.calculate_black_scholes_price(S, K, T, r, mid, option_type)
            if p > price:
                high = mid
            else:
                low = mid
        return (low + high) / 2

    def _build_instance_breakdown(self, active_positions_list: List[Dict], portfolio_pnl_pct: float) -> List[Dict]:
        try:
            if getattr(self, 'display_strategy_key', self.active_strategy_key) == 'ADAPTIVE_AUTO':
                strategies = ['ADAPTIVE_AUTO']
            else:
                strategies = list(self.enabled_strategies) if hasattr(self, 'enabled_strategies') else [getattr(self, 'active_strategy_key', 'HIGH_CONVICTION')]
        except Exception:
            strategies = [getattr(self, 'active_strategy_key', 'HIGH_CONVICTION')]
        breakdown = []
        initial_capital_total = float((self.paper_portfolio or {}).get('initial_capital', 0) or 0)
        strategy_weights = getattr(self, 'strategy_weights', {}) or {}
        try:
            today_key = datetime.now().date().isoformat()
            daily_pnl_val = float((self.trade_journal.get('daily_pnl') or {}).get(today_key, 0) or 0)
        except Exception:
            daily_pnl_val = 0.0
        for strat in strategies:
            try:
                pos_for_strat = [p for p in active_positions_list if (p.get('source_strategy') or '') == strat]
                entry_sum = 0.0
                pnl_sum = 0.0
                for p in pos_for_strat:
                    entry_sum += float(p.get('entry_premium', 0.0)) * float(p.get('lot_size', 1)) * float(p.get('quantity', 1))
                    pnl_sum += float(p.get('pnl', 0.0))
                if not getattr(self, 'multi_strategy_mode', False):
                    alloc = initial_capital_total
                else:
                    alloc = float(strategy_weights.get(strat, 0.0)) * initial_capital_total if initial_capital_total > 0 else 0.0
                if pos_for_strat:
                    pnl_pct = (pnl_sum / alloc * 100.0) if alloc > 0 else ((pnl_sum / entry_sum * 100.0) if entry_sum > 0 else 0.0)
                else:
                    pnl_pct = (daily_pnl_val / alloc * 100.0) if alloc > 0 else 0.0
                breakdown.append({
                    'strategy': strat,
                    'health_status': self.health_status,
                    'exit_policy': self.exit_policy,
                    'journal': self.trade_journal.get('performance_metrics', {}),
                    'initial_capital': alloc,
                    'portfolio_value': (alloc + (pnl_sum if pos_for_strat else daily_pnl_val)) if alloc > 0 else alloc,
                    'total_pnl': (pnl_sum if pos_for_strat else daily_pnl_val),
                    'total_pnl_pct': pnl_pct if pos_for_strat else portfolio_pnl_pct,
                    'url': None
                })
            except Exception:
                breakdown.append({
                    'strategy': strat,
                    'health_status': self.health_status,
                    'exit_policy': self.exit_policy,
                    'journal': {},
                    'initial_capital': 0.0,
                    'portfolio_value': 0.0,
                    'total_pnl': 0.0,
                    'total_pnl_pct': portfolio_pnl_pct,
                    'url': None
                })
        return breakdown

    def _tick_round_up(self, price: float, tick: float = 0.05) -> float:
        return math.ceil(price / tick) * tick

    def _tick_round_down(self, price: float, tick: float = 0.05) -> float:
        return math.floor(price / tick) * tick

    def _market_protection_pct(self, instrument_type: str, price: float) -> float:
        try:
            cfg = getattr(self, 'config', {}) if hasattr(self, 'config') else {}
            if instrument_type == 'OPT':
                if price < 10: return float(cfg.get('opt_protect_pct_lt_10', 0.05))
                if price < 100: return float(cfg.get('opt_protect_pct_10_100', 0.03))
                if price < 500: return float(cfg.get('opt_protect_pct_100_500', 0.02))
                return float(cfg.get('opt_protect_pct_gt_500', 0.01))
            else:
                if price < 100: return float(cfg.get('eq_protect_pct_lt_100', 0.02))
                if price < 500: return float(cfg.get('eq_protect_pct_100_500', 0.01))
                return float(cfg.get('eq_protect_pct_gt_500', 0.005))
        except Exception:
            return 0.02

    def _market_protection_limit(self, ltp: float, side: str, instrument_type: str = 'OPT') -> float:
        pct = self._market_protection_pct(instrument_type, float(ltp or 0))
        base = float(ltp or 0)
        tick_val = float(self.config.get('tick_size_opt', 0.05)) if instrument_type == 'OPT' else float(self.config.get('tick_size_eq', 0.05))
        if side.upper() == 'BUY':
            target = base * (1.0 + pct)
            return self._tick_round_up(target, tick=tick_val)
        else:
            target = base * (1.0 - pct)
            return self._tick_round_down(target, tick=tick_val)

    def _widened_limit(self, ltp: float, side: str, instrument_type: str, step: int) -> float:
        mults = [1.0, 1.5, 2.0, 2.5]
        idx = max(0, min(step, len(mults) - 1))
        pct = self._market_protection_pct(instrument_type, float(ltp or 0)) * mults[idx]
        base = float(ltp or 0)
        tick_val = float(self.config.get('tick_size_opt', 0.05)) if instrument_type == 'OPT' else float(self.config.get('tick_size_eq', 0.05))
        if side.upper() == 'BUY':
            return self._tick_round_up(base * (1.0 + pct), tick=tick_val)
        else:
            return self._tick_round_down(base * (1.0 - pct), tick=tick_val)

    def self_test_market_protection(self):
        samples_opt = [8.5, 25.0, 150.0, 700.0]
        samples_eq = [80.0, 250.0, 620.0]
        for ltp in samples_opt:
            buy_lp = self._market_protection_limit(ltp, 'BUY', 'OPT')
            sell_lp = self._market_protection_limit(ltp, 'SELL', 'OPT')
            print(f"OPT LTP {ltp:.2f} -> BUY limit {buy_lp:.2f}, SELL limit {sell_lp:.2f}")
        for ltp in samples_eq:
            buy_lp = self._market_protection_limit(ltp, 'BUY', 'EQ')
            sell_lp = self._market_protection_limit(ltp, 'SELL', 'EQ')
            print(f"EQ  LTP {ltp:.2f} -> BUY limit {buy_lp:.2f}, SELL limit {sell_lp:.2f}")

    def self_test_widening(self):
        ltp = 25.0
        for step in range(0, 4):
            b = self._widened_limit(ltp, 'BUY', 'OPT', step)
            s = self._widened_limit(ltp, 'SELL', 'OPT', step)
            print(f"Step {step}: BUY {b:.2f}, SELL {s:.2f}")

    def refresh_nfo_instruments_cache(self):
        """Refresh NFO cache with error handling and fallback"""
        try:
            if self.kite:
                self.nfo_instruments = self.kite.instruments('NFO')
                self.last_cache_refresh = datetime.now()
                logger.info(f"NFO cache refreshed: {len(self.nfo_instruments)} instruments")
        except Exception as e:
            logger.error(f"Failed to refresh NFO cache: {e}")
            # Keep existing cache if refresh fails

    def refresh_mcx_instruments_cache(self):
        """Refresh MCX cache with error handling and fallback"""
        try:
            if self.kite:
                self.mcx_instruments = self.kite.instruments('MCX')
                self.last_mcx_cache_refresh = datetime.now()
                logger.info(f"MCX cache refreshed: {len(self.mcx_instruments)} instruments")
        except Exception as e:
            logger.error(f"Failed to refresh MCX cache: {e}")
            # Keep existing cache if refresh fails

    def get_nfo_instruments(self):
        """Get NFO instruments with auto-refresh every 30 minutes"""
        if datetime.now() - self.last_cache_refresh > timedelta(minutes=30):
            self.refresh_nfo_instruments_cache()
        return self.nfo_instruments

    def get_mcx_instruments(self):
        """Get MCX instruments with auto-refresh every 30 minutes"""
        if datetime.now() - self.last_mcx_cache_refresh > timedelta(minutes=30):
            self.refresh_mcx_instruments_cache()
        return self.mcx_instruments

    def get_option_chain(self, symbol: str, expiry_date_str: str) -> Dict:
        chain = {'CE': {}, 'PE': {}}
        for i in self.get_nfo_instruments():
            if i.get('name') == symbol and i.get('expiry').strftime("%Y-%m-%d") == expiry_date_str and i.get('instrument_type') in ['CE','PE']:
                chain[i['instrument_type']][int(i['strike'])] = {
                    'instrument_token': i['instrument_token'],
                    'tradingsymbol': i['tradingsymbol']
                }
        return chain

    def find_nfo_tradingsymbol(self, symbol: str, strike: float, option_type: str) -> Optional[str]:
        try:
            now = datetime.now()
            best = None
            best_expiry = None
            itype = 'CE' if str(option_type).upper() == 'CALL' else 'PE'
            s_int = int(round(float(strike))) if strike is not None else None
            for inst in self.get_nfo_instruments():
                if inst.get('name') != symbol:
                    continue
                if inst.get('instrument_type') != itype:
                    continue
                try:
                    if s_int is not None and int(inst.get('strike')) != s_int:
                        continue
                except Exception:
                    continue
                exp = inst.get('expiry')
                if not isinstance(exp, datetime):
                    continue
                # choose nearest upcoming expiry (>= today)
                if exp.date() >= now.date():
                    if best_expiry is None or exp < best_expiry:
                        best_expiry = exp
                        best = inst
            if best:
                return best.get('tradingsymbol')
            return None
        except Exception:
            return None

    def get_realtime_candles(self, symbol: str, minutes: int = 60) -> pd.DataFrame:
        token = self.token_map.get(symbol)
        if not self.kite or not token:
            return pd.DataFrame()
        to_dt = datetime.now()
        from_dt = to_dt - timedelta(minutes=minutes)
        data = self.kite.historical_data(token, from_dt.strftime("%Y-%m-%d %H:%M:%S"), to_dt.strftime("%Y-%m-%d %H:%M:%S"), 'minute', continuous=False)
        df = pd.DataFrame(data)
        if df.empty:
            return df
        df.set_index('date', inplace=True)
        df.index = pd.to_datetime(df.index)
        df.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close','volume':'Volume'}, inplace=True)
        return df

    def compute_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        if df is None or df.empty or len(df) < period + 1:
            return 0.0
        high = df['High']
        low = df['Low']
        close = df['Close']
        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        return float(atr) if not np.isnan(atr) else 0.0

    def _load_nse_instrument_map(self):
        """Fetches and maps all NSE instrument tokens with comprehensive mapping."""
        if not self.kite:
            return {}
        try:
            logger.info("Fetching all NSE equity instruments for token mapping...")
            instruments = self.kite.instruments('NSE')
            token_map = {}

            for inst in instruments:
                # Map multiple variations
                token_map[inst['tradingsymbol']] = inst['instrument_token']
                token_map[inst['name']] = inst['instrument_token']  # Add name mapping

                # Store token by trading symbol for easier lookup later
                if inst['segment'] == 'NSE':
                    self.token_map[inst['tradingsymbol']] = inst['instrument_token']

                # Special handling for indices
                if 'NIFTY' in inst['tradingsymbol']:
                    token_map['NIFTY'] = inst['instrument_token']
                    token_map['NIFTY 50'] = inst['instrument_token']
                    self.token_map['NIFTY'] = inst['instrument_token'] # Add NIFTY token to main map
                if 'BANKNIFTY' in inst['tradingsymbol']:
                    token_map['BANKNIFTY'] = inst['instrument_token']
                    self.token_map['BANKNIFTY'] = inst['instrument_token']

            logger.info(f"NSE instrument map loaded: {len(token_map)} mappings")
            return token_map

        except Exception as e:
            logger.error(f"Failed to load NSE instrument map from Kite: {e}")
            return {}

    def _initialize_websockets(self):
        """Initializes the Kite Ticker client with robust reconnection logic and initial token subscriptions."""
        if not KITE_AVAILABLE or not self.kite or not self.kite.access_token:
            logger.warning("Kite Ticker not initialized due to missing credentials.")
            return

        api_key = self.kite.api_key
        access_token = self.kite.access_token

        self.kws = KiteTicker(api_key, access_token)
        try:
            # Use built-in reconnect if available in the installed KiteTicker version
            self.kws.enable_reconnect(True, interval=5, retries=50)
        except Exception:
            pass

        def on_ticks(ws, ticks):
            """Callback to handle new market data ticks."""
            for tick in ticks:
                token = tick['instrument_token']
                if 'last_price' in tick:
                    self.live_ltp_cache[token] = tick['last_price']
                try:
                    symbol = self.token_to_symbol.get(int(token))
                    if symbol:
                        now_dt = datetime.now()
                        floored_minute = (now_dt.minute // 5) * 5
                        bucket = now_dt.replace(minute=floored_minute, second=0, microsecond=0)
                        bars = self.ohlc_5m.get(symbol)
                        lp = float(tick.get('last_price') or 0)
                        if lp > 0:
                            if not bars:
                                bars = []
                                self.ohlc_5m[symbol] = bars
                            if bars and bars[-1]['ts'] == bucket.isoformat():
                                b = bars[-1]
                                if lp > b['High']:
                                    b['High'] = lp
                                if lp < b['Low']:
                                    b['Low'] = lp
                                b['Close'] = lp
                            else:
                                bars.append({'Open': lp, 'High': lp, 'Low': lp, 'Close': lp, 'Volume': 0, 'ts': bucket.isoformat()})
                                if len(bars) > 240:
                                    self.ohlc_5m[symbol] = bars[-240:]
                except Exception:
                    pass

                # Check if this tick belongs to an underlying spot price
                # and update the position monitoring data
                for pos_key, pos in self.paper_portfolio['positions'].items():
                    spot_token = pos.get('spot_token')
                    if spot_token == token:
                        pos['current_spot'] = tick['last_price']
            try:
                self.last_tick_time = datetime.now().isoformat()
                da = getattr(self, 'dashboard_app', None)
                if da and getattr(da, 'socketio', None):
                    last_emit = getattr(da, 'last_emit_ts', 0)
                    now_ts = time.time()
                    if (now_ts - last_emit) >= 1.0:
                        da.socketio.emit('dashboard_update', self.package_dashboard_data())
                        da.last_emit_ts = now_ts
            except Exception:
                pass

        def on_connect(ws, response):
            """Callback on successful connection. Subscribes to all currently tracked tokens."""
            logger.info("WebSocket connected successfully.")
            self.ws_reconnect_attempts = 0
            self.ws_reconnect_in_progress = False

            # Combine all tokens needed: options + spot prices
            all_tokens = list(self.open_option_tokens.union(self.open_spot_tokens))

            if all_tokens:
                logger.info(f"WebSockets connected. Subscribing to {len(all_tokens)} contracts/spot tokens.")
                ws.subscribe(all_tokens)
                ws.set_mode(ws.MODE_LTP, all_tokens)


        def on_close(ws, code, reason):
            try:
                now = datetime.now()
                if (now - self.last_ws_close_log_time).total_seconds() > 3:
                    logger.warning(f"WebSocket connection closed. Code: {code}, Reason: {reason}")
                    self.last_ws_close_log_time = now
            except Exception:
                logger.warning(f"WebSocket connection closed. Code: {code}, Reason: {reason}")
            # Robust reconnection logic
            if self.scheduler_running and code != 1000:
                try:
                    if not self.ws_reconnect_in_progress:
                        self.ws_reconnect_in_progress = True
                        self.ws_reconnect_attempts += 1
                        delay = min(60, 2 ** min(self.ws_reconnect_attempts, 5))
                        logger.info(f"Attempting WebSocket reconnection in {delay}s...")
                        def _do_reconnect():
                            try:
                                api_key2 = self.kite.api_key
                                access_token2 = self.kite.access_token
                                self.kws = KiteTicker(api_key2, access_token2)
                                try:
                                    self.kws.enable_reconnect(True, interval=5, retries=50)
                                except Exception:
                                    pass
                                self.kws.on_ticks = on_ticks
                                self.kws.on_connect = on_connect
                                self.kws.on_close = on_close
                                self.kws.on_error = on_error
                                self.kws.connect()
                            except Exception as e:
                                logger.error(f"WebSocket reconnection failed: {e}")
                            finally:
                                self.ws_reconnect_in_progress = False
                        threading.Timer(delay, _do_reconnect).start()
                except Exception as e:
                    logger.error(f"WebSocket reconnect scheduling failed: {e}")

        def on_error(ws, code, reason):
            try:
                now = datetime.now()
                if (now - self.last_ws_error_log_time).total_seconds() > 3:
                    logger.error(f"WebSocket error. Code: {code}, Reason: {reason}")
                    self.last_ws_error_log_time = now
            except Exception:
                logger.error(f"WebSocket error. Code: {code}, Reason: {reason}")
            self.health_status = "WEBSOCKET_ERROR"
            # Avoid chaining on_error -> on_close to prevent duplicate reconnection scheduling

        self.kws.on_ticks = on_ticks
        self.kws.on_connect = on_connect
        self.kws.on_close = on_close
        self.kws.on_error = on_error

        # Start the connection
        try:
            try:
                # Pre-subscribe to core index spots to ensure baseline ticks
                for idx_sym in ['NIFTY', 'BANKNIFTY']:
                    tok = self.token_map.get(idx_sym)
                    if tok:
                        self.open_spot_tokens.add(tok)
            except Exception:
                pass
            self.kws.connect(threaded=True)
        except Exception:
            threading.Thread(target=self.kws.connect, daemon=True).start()

    def start_health_monitor(self):
        """Monitor system health metrics"""
        def health_check():
            while self.scheduler_running:
                try:
                    # Check API connectivity
                    try:
                        self.kite.profile()
                        api_status = 'HEALTHY'
                    except:
                        api_status = 'API_DISCONNECTED'

                    # Check WebSocket connection
                    ws_status = 'HEALTHY'
                    if self.kws and not self.kws.is_connected():
                        ws_status = 'WEBSOCKET_DISCONNECTED'

                    # Check memory usage
                    process = psutil.Process()
                    memory_mb = process.memory_info().rss / 1024 / 1024
                    memory_status = 'HEALTHY'
                    if memory_mb > 500:  # 500MB threshold
                        memory_status = 'HIGH_MEMORY'
                        logger.warning(f"High memory usage: {memory_mb:.1f}MB")

                    # Overall health status
                    if api_status == 'HEALTHY' and ws_status == 'HEALTHY' and memory_status == 'HEALTHY':
                        self.health_status = 'HEALTHY'
                    else:
                        self.health_status = f"ISSUES: API={api_status}, WS={ws_status}, MEM={memory_status}"

                except Exception as e:
                    logger.error(f"Health monitor error: {e}")

                time.sleep(60)  # Check every minute

        if self.scheduler_running:
            threading.Thread(target=health_check, daemon=True).start()

    def validate_strategy_parameters(self, params: Dict) -> bool:
        """Validate strategy parameters for consistency"""
        required_keys = ['min_conf', 'min_rr', 'lot_multiplier', 'tsl_activation_pct']

        for key in required_keys:
            if key not in params:
                logger.error(f"Missing required strategy parameter: {key}")
                return False

        return True

    def calculate_daily_pnl(self) -> float:
        """Calculate daily PNL percentage"""
        today = datetime.now().date()
        daily_pnl_val = self.trade_journal.get('daily_pnl', {}).get(today, 0)
        initial_capital = self.paper_portfolio.get('initial_capital', 500000)
        return (daily_pnl_val / initial_capital) * 100 if initial_capital > 0 else 0

    def set_active_strategy(self, strategy_key: str):
        """Applies the strategy parameters to the trader instance with validation."""
        # Prevent switching a commodity instance to non-commodity strategies
        current_is_commodity = bool((getattr(self, 'strategy_params', {}) or {}).get('market_type') == 'COMMODITY')
        if current_is_commodity and strategy_key not in {"COMMODITY_TREND","COMMODITY_REVERSAL","COMMODITY_SCALP"}:
            logger.warning(f"{Fore.YELLOW}❌ Ignoring switch to {strategy_key} for COMMODITY instance. Staying on {self.active_strategy_key}.{Style.RESET_ALL}")
            return

        if strategy_key in self.STRATEGY_CONFIGS:
            strategy_params = self.STRATEGY_CONFIGS[strategy_key]

            if self.validate_strategy_parameters(strategy_params):
                self.active_strategy_key = strategy_key
                self.strategy_params = strategy_params

                self.min_confidence = self.strategy_params["min_conf"]
                self.min_rr_ratio = self.strategy_params["min_rr"]
                self.lot_multiplier = self.strategy_params["lot_multiplier"]
                self.tsl_activation_pct = self.strategy_params["tsl_activation_pct"]
                self.tsl_pullback_pct = self.strategy_params["tsl_pullback_pct"]
                self.max_profit_pct = self.strategy_params["max_profit_pct"]
                self.quick_cash_target = self.strategy_params.get("quick_cash_target", 25000)
                self.time_exit_minutes = self.strategy_params.get("time_exit_minutes", 15)
                self.partial_book_pct = self.strategy_params.get("partial_book_pct", 50)

                # Use global config for max risk parameters
                self.max_positions = self.config.get('max_positions', 3)
                self.max_daily_loss_pct = self.config.get('max_daily_loss_pct', 3.0)

                logger.info(f"{Fore.GREEN}✅ Strategy switched to: {strategy_key}. Conf: {self.min_confidence:.1f}, R:R: {self.min_rr_ratio}, Lots: {self.lot_multiplier}x{Style.RESET_ALL}")
                try:
                    if self.strategy_params.get('market_type') == 'COMMODITY':
                        base_conf = float(self.min_confidence or 0)
                        base_rr = float(self.min_rr_ratio or 0)
                        self.min_confidence = max(7.5, base_conf - 0.5)
                        self.min_rr_ratio = max(1.3, base_rr - 0.2)
                        logger.info(f"Adjusted thresholds for COMMODITY live: min_conf={self.min_confidence:.2f}, min_rr={self.min_rr_ratio:.2f}")
                except Exception:
                    pass
            else:
                logger.error(f"{Fore.RED}Invalid parameters for strategy: {strategy_key}. Keeping current strategy.{Style.RESET_ALL}")
        else:
            logger.warning(f"{Fore.YELLOW}❌ Unknown strategy key: {strategy_key}. Keeping current strategy.{Style.RESET_ALL}")

    def get_option_instrument_token(self, symbol, strike, option_type, expiry_date_str):
        """
        Finds the specific NFO option contract token using the local cache.
        """
        nfo_instruments = self.get_nfo_instruments()
        if not nfo_instruments:
            return None, None

        try:
            instrument_code = 'CE' if str(option_type).upper() in {'CALL', 'CE'} else 'PE'
            desired_strike = int(strike)
            base_candidates = [
                i for i in nfo_instruments
                if i.get('instrument_type') == instrument_code
                and (i.get('name') == symbol or str(i.get('tradingsymbol','')).startswith(symbol))
                and i.get('expiry').strftime("%Y-%m-%d") == expiry_date_str
            ]

            if not base_candidates:
                # fall back to nearest future expiry if no contracts for target expiry
                alt_candidates = [
                    i for i in nfo_instruments
                    if i.get('instrument_type') == instrument_code
                    and (i.get('name') == symbol or str(i.get('tradingsymbol','')).startswith(symbol))
                ]
                if alt_candidates:
                    alt_candidates.sort(key=lambda x: x.get('expiry'))
                    # pick earliest future expiry set
                    earliest_expiry = alt_candidates[0].get('expiry')
                    base_candidates = [i for i in alt_candidates if i.get('expiry') == earliest_expiry]

            if base_candidates:
                # pick nearest strike within this expiry set
                target_instrument = min(base_candidates, key=lambda x: abs(int(x.get('strike', 0)) - desired_strike))
                return target_instrument['instrument_token'], target_instrument['tradingsymbol']

            logger.debug(f"Option token not found for {symbol} Strike:{strike} Type:{option_type} Expiry:{expiry_date_str}.")
            return None, None

        except Exception:
            return None, None

    def get_commodity_instrument_token(self, symbol, expiry_date_str, instrument_type='FUT'):
        """
        Finds the specific MCX commodity contract token using the local cache.
        """
        mcx_instruments = self.get_mcx_instruments()
        if not mcx_instruments:
            return None, None

        try:
            # For commodities, we primarily trade futures, but also support options
            instrument_code = 'FUT' if instrument_type.upper() == 'FUT' else ('CE' if 'CALL' in str(instrument_type).upper() else 'PE')
            
            base_candidates = [
                i for i in mcx_instruments
                if i.get('instrument_type') == instrument_code
                and (i.get('name') == symbol or str(i.get('tradingsymbol','')).startswith(symbol))
                and i.get('expiry').strftime("%Y-%m-%d") == expiry_date_str
            ]

            if not base_candidates:
                # fall back to nearest future expiry if no contracts for target expiry
                alt_candidates = [
                    i for i in mcx_instruments
                    if i.get('instrument_type') == instrument_code
                    and (i.get('name') == symbol or str(i.get('tradingsymbol','')).startswith(symbol))
                ]
                if alt_candidates:
                    alt_candidates.sort(key=lambda x: x.get('expiry'))
                    # pick earliest future expiry set
                    earliest_expiry = alt_candidates[0].get('expiry')
                    base_candidates = [i for i in alt_candidates if i.get('expiry') == earliest_expiry]

            if base_candidates:
                # Return the first available contract (commodities typically have fewer strikes than options)
                target_instrument = base_candidates[0]
                return target_instrument['instrument_token'], target_instrument['tradingsymbol']

            logger.debug(f"Commodity token not found for {symbol} Type:{instrument_type} Expiry:{expiry_date_str}.")
            return None, None

        except Exception:
            return None, None

    def parse_strike_from_tradingsymbol(self, ts: str) -> Optional[int]:
        try:
            s = str(ts)
            for suf in ['CE', 'PE']:
                if s.endswith(suf):
                    core = s[:-2]
                    digits = ''.join(ch for ch in core if ch.isdigit())
                    if digits:
                        return int(digits)
            digits = ''.join(ch for ch in s if ch.isdigit())
            if digits:
                return int(digits)
            return None
        except Exception:
            return None

    # ------------------ UTILITIES - PURELY DYNAMIC EXPIRY (The Fix) ------------------

    def get_next_tradable_expiry(self, symbol: str) -> datetime.date:
        """
        Dynamically finds the nearest future expiry date from the Kite instrument cache
        for the given symbol.
        """
        now = datetime.now()
        now_date = now.date()
        nfo_instruments = self.get_nfo_instruments()

        # Helper function to safely get the date part
        def get_date_safe(dt_obj):
            if isinstance(dt_obj, datetime):
                return dt_obj.date()
            return dt_obj # Assume it's already a date object

        # 1. Find all unique future expiry dates for the symbol
        future_expiries = sorted(list(set([
            get_date_safe(i['expiry']) for i in nfo_instruments
            if i['name'] == symbol and i['instrument_type'] in ['CE', 'PE'] and get_date_safe(i['expiry']) > now_date
        ])))

        if not future_expiries:
            logger.error(f"CRITICAL: No future expiry dates found in cache for {symbol}. Defaulting to +30 days.")
            return now_date + timedelta(days=30)

        # 2. Select the nearest future expiry date
        nearest_expiry = future_expiries[0]

        # 3. For ROBUST_AUTO, prioritize the standard monthly expiry (Last Thursday) if it's close.
        if self.strategy_params.get('expiry_mode') == 'ROBUST_AUTO':
            # Calculate the standard Last Thursday (3) of the current/next month (standard equity F&O)
            today_date = now_date
            year = today_date.year
            month = today_date.month

            # Find Last Thursday (3) of current month
            last_day = calendar.monthrange(year, month)[1]
            monthly_expiry = datetime(year, month, last_day).date()
            while monthly_expiry.weekday() != 3:  # Thursday = 3
                monthly_expiry -= timedelta(days=1)

            if monthly_expiry < today_date:
                next_month = today_date.replace(day=1) + timedelta(days=32)
                year = next_month.year
                month = next_month.month
                last_day = calendar.monthrange(year, month)[1]
                monthly_expiry = datetime(year, month, last_day).date()
                while monthly_expiry.weekday() != 3:  # Thursday = 3
                    monthly_expiry -= timedelta(days=1)

            # Accept the standard monthly expiry if it's within 7 days of the nearest expiry
            if abs((monthly_expiry - nearest_expiry).days) <= 7:
                return monthly_expiry

        return nearest_expiry

    def get_next_commodity_expiry(self, symbol: str) -> datetime.date:
        """
        Dynamically finds the nearest future expiry date from the MCX instrument cache
        for the given commodity symbol.
        """
        now = datetime.now()
        now_date = now.date()
        mcx_instruments = self.get_mcx_instruments()

        # Helper function to safely get the date part
        def get_date_safe(dt_obj):
            if isinstance(dt_obj, datetime):
                return dt_obj.date()
            return dt_obj # Assume it's already a date object

        # 1. Find all unique future expiry dates for the commodity symbol
        future_expiries = sorted(list(set([
            get_date_safe(i['expiry']) for i in mcx_instruments
            if i['name'] == symbol and i['instrument_type'] in ['FUT'] and get_date_safe(i['expiry']) > now_date
        ])))

        if not future_expiries:
            logger.error(f"CRITICAL: No future expiry dates found in MCX cache for {symbol}. Defaulting to +30 days.")
            return now_date + timedelta(days=30)

        # 2. Select the nearest future expiry date
        nearest_expiry = future_expiries[0]

        # 3. For COMMODITY_AUTO, prioritize the standard monthly expiry patterns
        if self.strategy_params.get('expiry_mode') == 'COMMODITY_AUTO':
            # Commodity futures typically expire on different schedules than equity options
            # Common patterns: 5th of month, 20th of month, or last business day
            today_date = now_date
            year = today_date.year
            month = today_date.month

            # Try to find the most liquid contract (usually nearest month contract)
            # For commodities, we'll use the nearest expiry that's at least 5 days away
            valid_expiries = [exp for exp in future_expiries if (exp - today_date).days >= 5]
            if valid_expiries:
                return valid_expiries[0]

        return nearest_expiry

    def get_strike_price(self, spot_price: float, direction: str) -> float:
        """
        Generates a desired strike price based on the spot price and common intervals.
        """
        if spot_price < 1000:
            interval = 10
        elif spot_price < 2000:
            interval = 20
        elif spot_price < 5000:
            interval = 50
        elif spot_price < 10000:
            interval = 100
        else:
            interval = 200

        base_strike = round(spot_price / interval) * interval
        return base_strike

    def find_nearest_available_strike(self, symbol: str, desired_strike: float, option_type: str, expiry_date_str: str) -> float:
        """
        Finds the nearest available strike price for a given symbol and expiry.
        """
        nfo_instruments = self.get_nfo_instruments()

        available_strikes = sorted(list(set([
            int(i['strike']) for i in nfo_instruments
            if (i.get('name') == symbol or str(i.get('tradingsymbol','')).startswith(symbol))
               and i.get('instrument_type') in ['CE', 'PE'] and i.get('expiry').strftime("%Y-%m-%d") == expiry_date_str
        ])))

        if not available_strikes:
            try:
                fallback_expiry = self.get_next_tradable_expiry(symbol)
                fallback_str = fallback_expiry.strftime("%Y-%m-%d")
                available_strikes = sorted(list(set([
                    int(i['strike']) for i in nfo_instruments
                    if (i.get('name') == symbol or str(i.get('tradingsymbol','')).startswith(symbol))
                       and i.get('instrument_type') in ['CE', 'PE'] and i.get('expiry').strftime("%Y-%m-%d") == fallback_str
                ])))
                if available_strikes:
                    logger.debug(
                        f"CRITICAL STRIKE WARNING: No tradeable strikes for {symbol} on {expiry_date_str}. Using nearest expiry {fallback_str}."
                    )
                else:
                    logger.debug(
                        f"CRITICAL STRIKE WARNING: No tradeable strikes for {symbol} on {expiry_date_str}. Falling back to ATM rounding."
                    )
            except Exception:
                logger.debug(
                    f"CRITICAL STRIKE WARNING: No tradeable strikes for {symbol} on {expiry_date_str}. Falling back to ATM rounding."
                )
            if not available_strikes:
                return round(desired_strike / 50) * 50

        # Find the strike closest to the desired strike (ATM)
        nearest_strike = min(available_strikes, key=lambda strike: abs(strike - desired_strike))
        return float(nearest_strike)

    def test_strike_selection(self, symbols_to_test: List[str], assumed_spot_prices: Dict):
        """
        Runs the strike selection logic offline for specific symbols and prints the result.
        """
        logger.info(f"{Fore.CYAN}--- OFFLINE STRIKE SELECTION TEST START ---{Style.RESET_ALL}")

        # Use dynamic expiry here
        results = {}
        for symbol, spot_price in assumed_spot_prices.items():
            if symbol not in self.nse_fo_stocks and symbol not in ['NIFTY', 'MANKIND', 'PATANJALI', 'RELIANCE', 'UPL']:
                logger.warning(f"Skipping {symbol}: Not found in fetched F&O list.")
                continue

            expiry_date = self.get_next_tradable_expiry(symbol)
            expiry_date_str = expiry_date.strftime("%Y-%m-%d")

            try:
                # 1. Get Initial Strike Guesses
                desired_call_strike = self.get_strike_price(spot_price, 'BULLISH')
                desired_put_strike = self.get_strike_price(spot_price, 'BEARISH')

                # 2. Get Final Available Strikes
                final_call_strike = self.find_nearest_available_strike(symbol, desired_call_strike, 'CE', expiry_date_str)
                final_put_strike = self.find_nearest_available_strike(symbol, desired_put_strike, 'PE', expiry_date_str)

                # 3. Check for Token Existence
                call_token, _ = self.get_option_instrument_token(symbol, final_call_strike, 'CE', expiry_date_str)
                put_token, _ = self.get_option_instrument_token(symbol, final_put_strike, 'PE', expiry_date_str)

                results[symbol] = {
                    'Spot': spot_price,
                    'Expiry': expiry_date_str,
                    'CALL Strike (Found)': final_call_strike,
                    'CALL Token Status': '✅ Found' if call_token else '❌ FAIL',
                    'PUT Strike (Found)': final_put_strike,
                    'PUT Token Status': '✅ Found' if put_token else '❌ FAIL'
                }

            except Exception as e:
                logger.error(f"Error testing strike for {symbol}: {e}")
                results[symbol] = {'Status': 'ERROR', 'Message': str(e)}

        print(f"{Fore.YELLOW}--- OFFLINE STRIKE TEST RESULTS (Strategy: {self.active_strategy_key}) ---{Style.RESET_ALL}")
        for symbol, res in results.items():
            call_status = Fore.GREEN if res.get('CALL Token Status') == '✅ Found' else Fore.RED
            put_status = Fore.GREEN if res.get('PUT Token Status') == '✅ Found' else Fore.RED

            print(f"| {symbol:<15} | Spot: {res['Spot']:.1f} | Expiry: {res['Expiry']} |")
            print(f"| {'':15} | CALL: {res['CALL Strike (Found)']:<6.0f} {call_status}{res['CALL Token Status']}{Style.RESET_ALL}")
            print(f"| {'':15} | PUT:  {res['PUT Strike (Found)']:<6.0f} {put_status}{res['PUT Token Status']}{Style.RESET_ALL}")
            print("-" * 60)

        logger.info(f"{Fore.CYAN}--- OFFLINE STRIKE SELECTION TEST END ---{Style.RESET_ALL}")

    def get_lot_size(self, symbol: str) -> int:
        ls = self.lot_sizes.get(symbol)
        if ls:
            return int(ls)
        try:
            if self.kite:
                instruments = self.kite.instruments("NFO")
                for i in instruments:
                    if i.get("name") == symbol and i.get("lot_size"):
                        self.lot_sizes[symbol] = int(i["lot_size"])
                        return self.lot_sizes[symbol]
        except Exception:
            pass
        logger.error(f"Lot size unavailable for {symbol}.")
        return 0

    def _verify_lot_sizes_ready(self):
        try:
            symbols = self.nse_fo_stocks or []
            missing = [s for s in symbols if not self.lot_sizes.get(s)]
            if not symbols:
                return
            if missing and self.kite:
                try:
                    instruments = self.kite.instruments("NFO")
                    missing_set = set(missing)
                    for i in instruments:
                        name = i.get("name")
                        ls = i.get("lot_size")
                        if name in missing_set and ls:
                            self.lot_sizes[name] = int(ls)
                except Exception:
                    pass
            missing_after = [s for s in symbols if not self.lot_sizes.get(s)]
            if missing_after:
                logger.warning(f"Lot sizes still missing for {len(missing_after)} symbols; will fetch at execution time.")
            else:
                logger.info("Lot sizes verified and cached from Kite.")
        except Exception:
            logger.warning("Lot size verification encountered an error; will fetch at execution time.")

    def get_days_to_expiry(self) -> int:
        today = datetime.now().date()
        days_ahead = 3 - today.weekday()
        if days_ahead <= 0: days_ahead += 7
        return max(1, days_ahead)

    def load_trade_history(self):
        self.paper_portfolio = load_portfolio()

    def save_trade_history(self):
        save_portfolio(self.paper_portfolio)
        logger.debug("💾 Trade history saved.")

        if self.dashboard_app:
            dashboard_data = self.package_dashboard_data()
            self.dashboard_app.emit_portfolio_update(dashboard_data)
            logger.debug(f"Dashboard Data Emitted. Total P&L: {dashboard_data['summary']['total_pnl']:.2f}")

    def init_telegram(self):
        self.telegram_token = TELEGRAM_TOKEN
        self.telegram_chat_id = TELEGRAM_CHAT_ID
        if self.telegram_token and self.telegram_chat_id:
            logger.info("🤖 Telegram alerts ENABLED.")
            self.telegram_enabled = True
        else:
            self.telegram_enabled = False
            logger.warning("❌ Telegram alerts DISABLED. Set token/chat ID in config.")

    def send_telegram_alert(self, message: str):
        try:
            if self.alerts:
                self.alerts.send_trade(message)
                return True
        except Exception:
            pass
        if not self.telegram_enabled or not self.telegram_chat_id:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {'chat_id': self.telegram_chat_id, 'text': message, 'parse_mode': 'HTML'}
            response = requests.post(url, json=payload, timeout=5)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    def get_signal_id(self, trade_plan: Dict, strike: float) -> str:
        """Utility method to generate a unique signal identifier."""
        return f"{trade_plan['symbol']}_{trade_plan['direction']}_{strike}_{round(trade_plan['target'], 1)}"

    def send_trade_alert(self, trade_plan: Dict, premium: float=0, strike: float=0, lot_size: int=0, quantity: int=0, trade_value: float=0, status: str="NEW SIGNAL"):
        """Sends a structured, trade-specific alert to Telegram."""
        if not self.telegram_enabled: return

        direction_icon = "🟢" if trade_plan['direction'] == 'BULLISH' else "🔴"
        option_type = "CALL" if trade_plan['direction'] == 'BULLISH' else "PUT"

        if status == 'TRADE EXECUTED':
            header = f"✅ TRADE EXECUTED ({self.mode}) - {trade_plan['symbol']} {option_type}"
            details = f"📦 Lots: {quantity} | Cost: ₹{trade_value:,.0f}"
        else:
            header = f"🔔 REAL-TIME HIGH-CONVICTION SIGNAL - {trade_plan['symbol']} {option_type}"
            details = f"💰 Live Premium: ₹{premium:.1f} (Lots: {quantity})"

        message = f"""
{direction_icon} <b>{header}</b>

📊 Conf: {trade_plan['confidence']:.1f}/10 | R:R: 1:{trade_plan['risk_reward']:.2f}

🎯 <b>Strike:</b> {strike:.0f} (ATM)
🎯 <b>Entry Spot:</b> ₹{trade_plan['entry']:.1f}
⬆️ <b>Target Spot:</b> ₹{trade_plan['target']:.1f}
⬇️ <b>Stop Spot:</b> ₹{trade_plan['stop_loss']:.1f}
{details}
"""
        self.send_telegram_alert(message)
        logger.info(f"ALERT SENT: {trade_plan['symbol']} {status}")

    # ------------------ DASHBOARD DATA PACKAGING (FIXED) ------------------

    def package_dashboard_data(self) -> Dict:
        """Calculates summary stats and packages data for SocketIO frontend."""
        portfolio = self.paper_portfolio
        total_pnl = 0.0
        active_positions_list = []
        exit_signals_count = 0

        estimated_position_value = 0.0
        quotes_map = {}
        try:
            if self.kite and getattr(self.kite, 'access_token', None):
                keys = []
                for pos_key, pos in portfolio['positions'].items():
                    ts = pos.get('tradingsymbol')
                    if ts:
                        if pos.get('market_type') == 'COMMODITY' or pos.get('instrument_type') == 'FUT':
                            keys.append(f"MCX:{ts}")
                        else:
                            keys.append(f"NFO:{ts}")
                if keys:
                    q = self.safe_quote(keys)
                    quotes_map = {k: q.get(k, {}) for k in keys}
                    self._save_ltp_snapshot(quotes_map)
        except Exception:
            quotes_map = {}

        for pos_key, pos in portfolio['positions'].items():
            entry_value = (pos.get('premium_paid', pos.get('entry_price', 0)) or 0) * (pos.get('lot_size', 0) or 0) * (pos.get('quantity', 0) or 0)
            ts = pos.get('tradingsymbol')
            if not ts:
                ts = self.find_nfo_tradingsymbol(pos.get('symbol'), pos.get('strike_price'), pos.get('option_type'))
                if ts:
                    pos['tradingsymbol'] = ts
            quote_key = None
            if ts:
                quote_key = f"MCX:{ts}" if (pos.get('market_type') == 'COMMODITY' or pos.get('instrument_type') == 'FUT') else f"NFO:{ts}"
            quote_ltp = None
            if quote_key and (quote_key in quotes_map):
                try:
                    src = quotes_map[quote_key]
                    lp = src.get('last_price')
                    if lp is not None and lp > 0:
                        quote_ltp = float(lp)
                    else:
                        close = (src.get('ohlc') or {}).get('close')
                        if close is not None and close > 0:
                            quote_ltp = float(close)
                except Exception:
                    quote_ltp = None
            snapshot_ltp = None
            if quote_key and (quote_key in self.last_ltp_snapshot):
                v = self.last_ltp_snapshot.get(quote_key, {})
                lp = v.get('last_price')
                if lp is not None and lp > 0:
                    snapshot_ltp = float(lp)
                else:
                    close = v.get('close')
                    if close is not None and close > 0:
                        snapshot_ltp = float(close)
            base_prem = pos.get('current_premium', pos.get('premium_paid', pos.get('entry_price', 0)))
            current_premium = quote_ltp if quote_ltp is not None else (snapshot_ltp if snapshot_ltp is not None else base_prem)
            current_value = (current_premium or 0) * (pos.get('lot_size', 0) or 0) * (pos.get('quantity', 0) or 0)

            estimated_position_value += current_value

            pnl = current_value - entry_value
            pnl_pct = (pnl / entry_value) * 100 if entry_value else 0

            total_pnl += pnl

            exit_signal = ''

            current_spot = pos.get('current_spot')
            if current_spot is not None:
                 ot = pos.get('option_type')
                 if ot == 'CALL' and current_spot <= pos.get('stop_loss', 0):
                     exit_signal = 'SL HIT (SPOT)'
                     exit_signals_count += 1
                 elif ot == 'PUT' and current_spot >= pos.get('stop_loss', 0):
                     exit_signal = 'SL HIT (SPOT)'
                     exit_signals_count += 1
                 elif ot == 'CALL' and current_spot >= pos.get('target', float('inf')):
                     exit_signal = 'TARGET HIT (SPOT)'
                     exit_signals_count += 1
                 elif ot == 'PUT' and current_spot <= pos.get('target', 0):
                     exit_signal = 'TARGET HIT (SPOT)'
                     exit_signals_count += 1

            if pos.get('exit_signal_reason'):
                 exit_signal = pos['exit_signal_reason']
                 exit_signals_count += 1

            active_positions_list.append({
                'symbol': pos.get('symbol'),
                'option_type': pos.get('option_type', 'FUT'),
                'strike_price': pos.get('strike_price', 0.0),
                'entry_premium': pos.get('premium_paid', pos.get('entry_price', 0.0)),
                'current_premium': current_premium,
                'lot_size': pos.get('lot_size', 1),
                'quantity': pos.get('quantity', 1),
                'entry_time': pos.get('entry_time'),
                'order_id': pos.get('order_id'),
                'tradingsymbol': pos.get('tradingsymbol'),
                'stop_loss_spot': pos.get('stop_loss', 0.0),
                'target_spot': pos.get('target', 0.0),
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'confidence': pos.get('confidence', 0.0),
                'risk_reward': pos.get('risk_reward', 0.0),
                'exit_signal': exit_signal,
                'delta': pos.get('delta', 0.0),
                'theta': pos.get('theta', 0.0),
                'vega': pos.get('vega', 0.0),
                'iv': pos.get('iv', 0.0),
                'market': (pos.get('market_type') or ('MCX' if (pos.get('instrument_type') == 'FUT') else 'NSE')),
                'source_strategy': getattr(self, 'display_strategy_key', self.active_strategy_key)
            })

        portfolio['total_value'] = portfolio['cash'] + estimated_position_value
        total_pnl_pct = (portfolio['total_value'] - portfolio['initial_capital']) / portfolio['initial_capital'] * 100 if portfolio['initial_capital'] else 0

        metrics = self.trade_journal.get('performance_metrics', {})
        win_rate = float(metrics.get('win_rate', 0) or 0)
        total_trades_m = int(metrics.get('total_trades', 0) or 0)
        profitable_trades = int(metrics.get('profitable_trades', 0) or 0)
        avg_win = float(metrics.get('avg_win', 0) or 0)
        avg_loss = float(metrics.get('avg_loss', 0) or 0)
        largest_win = float(metrics.get('largest_win', 0) or 0)
        largest_loss = float(metrics.get('largest_loss', 0) or 0)
        profit_factor = float(metrics.get('profit_factor', 0) or 0)
        daily_pnl_val = float(self.trade_journal.get('daily_pnl', {}).get(datetime.now().date().isoformat(), 0) if isinstance(self.trade_journal.get('daily_pnl', {}), dict) else metrics.get('daily_pnl', 0) or 0)
        
        # Get market data from current index quotes
        market_data = {}
        try:
            if self.kite and self.kite.access_token:
                mapping = {
                    'nifty': 'NSE:NIFTY 50',
                    'banknifty': 'NSE:NIFTY BANK',
                    'finnifty': 'NSE:NIFTY FIN SERVICE',
                    'sensex': 'BSE:SENSEX'
                }
                keys = list(mapping.values())
                quotes = self.safe_quote(keys)
                for k, key in mapping.items():
                    q = quotes.get(key, {})
                    price = float(q.get('last_price') or 0)
                    ohlc = q.get('ohlc') or {}
                    prev = float(ohlc.get('close') or price)
                    change = price - prev
                    pct = (change / prev * 100.0) if prev else 0.0
                    market_data[k] = {'price': price, 'change': change, 'change_percent': pct}
                # Commodity quotes via MCX instruments (nearest expiry)
                try:
                    sel = {'CRUDEOIL': None, 'GOLD': None, 'SILVER': None}
                    for inst in (self.mcx_instruments or []):
                        name = inst.get('name')
                        if name in sel:
                            exp = inst.get('expiry')
                            if sel[name] is None or (isinstance(exp, datetime) and exp < sel[name].get('expiry')):
                                sel[name] = inst
                    keys2 = []
                    mapping2 = {}
                    for k_sym, inst in sel.items():
                        if inst:
                            ts = inst.get('tradingsymbol')
                            keys2.append(f"MCX:{ts}")
                            mapping2[k_sym.lower()] = f"MCX:{ts}"
                    if keys2:
                        q2 = self.safe_quote(keys2)
                        for k_lower, key2 in mapping2.items():
                            qq = q2.get(key2, {})
                            price = float(qq.get('last_price') or 0)
                            ohlc = qq.get('ohlc') or {}
                            prev = float(ohlc.get('close') or price)
                            change = price - prev
                            pct = (change / prev * 100.0) if prev else 0.0
                            market_data[k_lower] = {'price': price, 'change': change, 'change_percent': pct}
                except Exception:
                    pass
                for ck in ['crudeoil','gold','silver']:
                    if ck not in market_data:
                        market_data[ck] = {'price': 0.0, 'change': 0.0, 'change_percent': 0.0}
        except Exception:
            # Fallback to zero values if market data unavailable
            if self.mode == 'PAPER':
                defaults = {
                    'nifty': 20000.0,
                    'banknifty': 45000.0,
                    'finnifty': 20000.0,
                    'sensex': 67000.0,
                    'crudeoil': 6800.0,
                    'gold': 62000.0,
                    'silver': 78000.0
                }
                for k, base in defaults.items():
                    change = (np.random.rand()-0.5) * (base * 0.002)
                    pct = (change / base) * 100.0
                    market_data[k] = {'price': base + change, 'change': change, 'change_percent': pct}
            else:
                for k in ['nifty','banknifty','finnifty','sensex','crudeoil','gold','silver']:
                    market_data[k] = {'price': 0.0, 'change': 0.0, 'change_percent': 0.0}
        
        # Enrich signals with pending_reason if missing
        try:
            base_recent = list((getattr(self, 'recent_signals', []) or (self.trade_journal.get('recent_signals') or []))[-50:])
        except Exception:
            base_recent = []
        enriched_signals = []
        open_symbols = set([p.get('symbol') for p in active_positions_list if p.get('symbol')])
        for s in base_recent:
            s2 = dict(s)
            if (s2.get('status') == 'PENDING') and (not s2.get('pending_reason')):
                sym = s2.get('symbol')
                try:
                    conf = float(s2.get('confidence') or 0)
                except Exception:
                    conf = 0.0
                try:
                    rr = float(s2.get('risk_reward') or 0)
                except Exception:
                    rr = 0.0
                min_conf = float(getattr(self, 'min_confidence', 0) or 0)
                min_rr = float(getattr(self, 'min_rr_ratio', 0) or 0)
                if sym in open_symbols:
                    s2['pending_reason'] = 'position_already_open'
                elif conf < min_conf:
                    s2['pending_reason'] = 'confidence_below_min'
                elif rr < min_rr:
                    s2['pending_reason'] = 'risk_reward_below_min'
                else:
                    try:
                        ds = str(getattr(self, 'display_strategy_key', '') or '')
                    except Exception:
                        ds = ''
                    is_mcx_ctx = bool((self.strategy_params.get('market_type') == 'COMMODITY') or ds.endswith('_MCX'))
                    if (not is_mcx_ctx) and (datetime.now().hour >= 15):
                        s2['pending_reason'] = 'late_entry_window'
                        s2['pending_reason_detail'] = {
                            'hour': datetime.now().hour,
                            'window': 'NSE intraday cutoff >= 15:00'
                        }
                    else:
                        s2['pending_reason'] = 'awaiting_execution'
                        s2['pending_reason_detail'] = {
                            'confidence': conf,
                            'min_confidence': min_conf,
                            'risk_reward': rr,
                            'min_rr_ratio': min_rr,
                            'position_open': bool(sym in open_symbols)
                        }
            enriched_signals.append(s2)

        return {
            'timestamp': datetime.now().isoformat(),
            'summary': {
                'initial_capital': portfolio['initial_capital'],
                'portfolio_value': portfolio['total_value'],
                'cash': portfolio.get('cash', 0.0),
                'margin_used': portfolio.get('margin_used', 0.0),
                'exposure_value': estimated_position_value,
                'total_pnl': total_pnl,
                'total_pnl_pct': total_pnl_pct,
                'active_trades': len(portfolio['positions']),
                'exit_signals_count': exit_signals_count,
                'health_status': self.health_status,
                'strategy': getattr(self, 'display_strategy_key', self.active_strategy_key),
                'exit_policy': self.exit_policy,
                'market_regime': self.active_market_regime,
                'min_confidence_threshold': float(getattr(self, 'min_confidence', 0) or 0),
                'min_rr_ratio_threshold': float(getattr(self, 'min_rr_ratio', 0) or 0),
                'scan_cycles': self.scan_summary.get('cycles_run', 0),
                'stocks_scanned': self.scan_summary.get('stocks_scanned_count', 0),
                'signals_found': self.scan_summary.get('signals_found_count', 0),
                'trades_executed': self.scan_summary.get('trades_executed_count', 0),
                'api_calls': self.scan_summary.get('total_api_calls', 0),
                'consecutive_losses': portfolio.get('consecutive_losses', 0),
                'daily_pnl': daily_pnl_val,
                'journal': {
                    'win_rate': win_rate,
                    'total_trades': total_trades_m,
                    'profitable_trades': profitable_trades,
                    'avg_win': avg_win,
                    'avg_loss': avg_loss,
                    'largest_win': largest_win,
                    'largest_loss': largest_loss,
                    'profit_factor': profit_factor,
                    'recent_signals': list((getattr(self, 'recent_signals', []) or (self.trade_journal.get('recent_signals') or []))[-50:])
                }
            },
            'positions': active_positions_list,
            'closed_trades': self.trade_journal.get('closed_trades', [])[-100:],
            'market_data': market_data,
            'signals': enriched_signals,
            'sources': (list(getattr(self, 'unified_active_keys', []) or []) if (getattr(self, 'display_strategy_key', self.active_strategy_key) == 'ADAPTIVE_AUTO') else (list(self.enabled_strategies) if hasattr(self, 'enabled_strategies') else [getattr(self, 'active_strategy_key', 'HIGH_CONVICTION')])),
            'instance_breakdown': self._build_instance_breakdown(active_positions_list, total_pnl_pct),
            'strategy_breakdown': self._build_instance_breakdown(active_positions_list, total_pnl_pct)
        }

    # ------------------ DATA FETCHING (KITE CONNECT LIVE) - FIXED VERSION ------------------

    def get_stock_data_batch(self, symbols: List[str]) -> Dict:
        # Only fetch data for the purpose of regime check / initial calc
        # For open positions, we rely on WebSockets (live_ltp_cache) for spot price.
        return self.get_stock_data_batch_with_retry(symbols, max_retries=2)

    def get_stock_data_batch_with_retry(self, symbols: List[str], max_retries: int = 2) -> Dict:
        """
        Retrieves 5-minute historical data for trend/indicator calculation.
        """
        is_commodity_mode = bool(self.strategy_params.get('market_type') == 'COMMODITY')
        needs_mock_env = str(os.getenv('FORCE_MOCK_DATA', '')).strip() == '1'
        needs_mock = needs_mock_env or getattr(self, 'network_unavailable', False) or (not self.kite)
        if needs_mock and is_commodity_mode:
            try:
                syms = [s for s in symbols if s in DEFAULT_COMMODITY_UNIVERSE]
                data = self._fetch_commodity_yf_batch(syms)
                self.scan_summary['total_api_calls'] += len(syms)
                return data
            except Exception:
                return {}
        if needs_mock:
            try:
                syms = symbols if symbols else DEFAULT_NSE_UNIVERSE
                if not syms or len(syms) == 0:
                    syms = DEFAULT_NSE_UNIVERSE
                data = self._fetch_nse_yf_batch(syms)
                self.scan_summary['total_api_calls'] += len(syms)
                return data
            except Exception:
                logger.error("Broker data unavailable; NSE mock fetch failed.")
                return {}

        data = {}
        failed_symbols = []
        now = datetime.now()
        from_date = now - timedelta(hours=5)
        to_date = now

        self.scan_summary['total_api_calls'] += len(symbols)

        for symbol in symbols:
            is_comm_symbol = bool((self.commodity_token_map or {}).get(symbol))
            token = (self.commodity_token_map.get(symbol) if is_commodity_mode else self.token_map.get(symbol))
            symbol_commodity_mode = is_commodity_mode
            if not token and is_comm_symbol:
                token = self.commodity_token_map.get(symbol)
                symbol_commodity_mode = False
            if not token:
                logger.error(f"Token not found for {symbol} (commodity_mode={is_commodity_mode}). Instrument mapping may be missing or expired.")
                failed_symbols.append(symbol)
                continue

            # Session-aware window for MCX
            to_d = to_date
            from_d = from_date
            if is_comm_symbol:
                su = symbol.upper()
                is_agri = su in {"KAPAS", "COTTON", "COTTONOIL", "CARDAMOM"}
                if is_agri:
                    session_open = datetime.combine(now.date(), dtime(hour=9, minute=0))
                    session_close = datetime.combine(now.date(), dtime(hour=17, minute=0))
                else:
                    session_open = datetime.combine(now.date(), dtime(hour=9, minute=0))
                    session_close = datetime.combine(now.date(), dtime(hour=23, minute=55))
                try:
                    logger.info(f"MCX session for {symbol}: {session_open.strftime('%Y-%m-%d %H:%M')} → {session_close.strftime('%Y-%m-%d %H:%M')}")
                except Exception:
                    pass
                if now > session_close:
                    to_d = session_close
                    if from_d > to_d:
                        from_d = to_d - timedelta(hours=5)
                    if from_d < session_open:
                        from_d = session_open
                    try:
                        logger.info(f"Clamped history window for {symbol}: {from_d.strftime('%Y-%m-%d %H:%M:%S')} → {to_d.strftime('%Y-%m-%d %H:%M:%S')}")
                    except Exception:
                        pass

            for attempt in range(max_retries):
                try:
                    # Rate limiting
                    time_since_last = (datetime.now() - self.last_api_call).total_seconds()
                    min_interval = max(1.0 / max(0.1, getattr(self, 'api_qps_limit', 1.0)), 0.25)
                    if time_since_last < min_interval:
                        time.sleep(min_interval - time_since_last)

                    cont = False
                    logger.info(f"Fetching history for {symbol} token={token} interval=5minute from={from_d.strftime('%Y-%m-%d %H:%M:%S')} to={to_d.strftime('%Y-%m-%d %H:%M:%S')} (continuous={cont})")
                    history = self.kite.historical_data(
                        token,
                        from_d.strftime("%Y-%m-%d %H:%M:%S"),
                        to_d.strftime("%Y-%m-%d %H:%M:%S"),
                        '5minute',
                        continuous=cont
                    )

                    self.last_api_call = datetime.now()

                    df = pd.DataFrame(history)

                    if not df.empty and len(df) >= 10:
                        df.set_index('date', inplace=True)
                        df.index = pd.to_datetime(df.index)
                        df.rename(columns={
                            'open': 'Open', 'high': 'High', 'low': 'Low',
                            'close': 'Close', 'volume': 'Volume'
                        }, inplace=True)
                        data[symbol] = df
                        logger.debug(f"✅ Data fetched for {symbol}: {len(df)} bars")
                        break
                    else:
                        logger.warning(f"Data for {symbol} insufficient: {len(df) if not df.empty else 0} bars")
                        if attempt == max_retries - 1:
                            try:
                                ext_from = (session_open if is_comm_symbol else (now - timedelta(hours=12)))
                                ext_history = self.kite.historical_data(
                                    token,
                                    ext_from.strftime("%Y-%m-%d %H:%M:%S"),
                                    to_d.strftime("%Y-%m-%d %H:%M:%S"),
                                    '5minute',
                                    continuous=cont
                                )
                                df2 = pd.DataFrame(ext_history)
                                if not df2.empty and len(df2) >= 10:
                                    df2.set_index('date', inplace=True)
                                    df2.index = pd.to_datetime(df2.index)
                                    df2.rename(columns={
                                        'open': 'Open', 'high': 'High', 'low': 'Low',
                                        'close': 'Close', 'volume': 'Volume'
                                    }, inplace=True)
                                    data[symbol] = df2
                                    logger.info(f"Expanded window data for {symbol}: {len(df2)} bars")
                                else:
                                    failed_symbols.append(symbol)
                            except Exception:
                                failed_symbols.append(symbol)
                        time.sleep(0.1)

                except Exception as e:
                    logger.error(f"API error for {symbol} (Attempt {attempt+1}): {e}")
                    msg = str(e).lower()
                    if ("failed to resolve" in msg) or ("nameresolutionerror" in msg) or ("nodename nor servname" in msg):
                        self.network_unavailable = True
                        break
                    if ("rate limit" in msg) or ("too many requests" in msg) or ("429" in msg):
                        time.sleep(15 * (attempt + 1))
                    else:
                        time.sleep(1)

                    if attempt == max_retries - 1:
                        failed_symbols.append(symbol)
                        self.api_failures += 1

        if failed_symbols:
            sset = set(failed_symbols)
            now2 = datetime.now()
            if sset != self.last_failed_fetch_symbols or (now2 - self.last_failed_fetch_log_time).total_seconds() > 120:
                logger.warning(f"Failed to fetch data for {len(failed_symbols)} symbols: {failed_symbols}")
                self.last_failed_fetch_symbols = sset
                self.last_failed_fetch_log_time = now2

        logger.info(f"Data fetch complete: {len(data)}/{len(symbols)} symbols successful")
        return data

    def _fetch_commodity_yf_batch(self, symbols: List[str]) -> Dict:
        out = {}
        for s in symbols:
            ticker = COMMODITY_YF_MAP.get(s)
            if not ticker:
                continue
            try:
                df = yf.download(tickers=ticker, period='2d', interval='15m', progress=False)
                if df is None or df.empty or len(df) < 10:
                    continue
                df = df.tail(100)
                df = df[['Open','High','Low','Close','Volume']]
                df = df.rename_axis('date')
                out[s] = df
            except Exception:
                continue
        return out

    def _fetch_nse_yf_batch(self, symbols: List[str]) -> Dict:
        out = {}
        for s in symbols:
            try:
                ticker = f"{s}.NS"
                df = yf.download(tickers=ticker, period='2d', interval='15m', progress=False)
                if df is None or df.empty or len(df) < 10:
                    continue
                df = df.tail(100)
                df = df[['Open','High','Low','Close','Volume']]
                df = df.rename_axis('date')
                out[s] = df
            except Exception:
                continue
        return out

    def compute_expanded_universe(self) -> List[str]:
        candidates = [s for s in self.token_map.keys() if s.isalpha() and s.upper() not in ['NIFTY', 'BANKNIFTY']]
        extra = []
        sample_size = 50 if getattr(self, 'mode', 'PAPER') == 'PAPER' else 150
        sample = candidates[:sample_size]
        batch = self.get_stock_data_batch_with_retry(sample)
        vols = []
        for sym, df in batch.items():
            try:
                if df is not None and not df.empty:
                    vols.append((sym, float(df['Volume'].tail(10).mean())))
            except Exception:
                pass
        vols.sort(key=lambda x: x[1], reverse=True)
        top = [s for s, _ in vols][:self.config.get('expanded_universe_size', 50)]
        for s in top:
            if s not in self.nse_fo_stocks:
                extra.append(s)
        return extra

    def _load_ltp_snapshot(self):
        try:
            if os.path.exists(LTP_SNAPSHOT_FILE):
                with open(LTP_SNAPSHOT_FILE, 'r') as f:
                    self.last_ltp_snapshot = json.load(f) or {}
        except Exception:
            self.last_ltp_snapshot = {}

    def _save_ltp_snapshot(self, quotes_map: Dict):
        try:
            if not quotes_map:
                return
            ts = datetime.now().isoformat()
            for k, v in quotes_map.items():
                lp = v.get('last_price')
                close = (v.get('ohlc') or {}).get('close')
                payload = {}
                if lp is not None and lp > 0:
                    payload['last_price'] = float(lp)
                if close is not None and close > 0:
                    payload['close'] = float(close)
                if payload:
                    payload['ts'] = ts
                    self.last_ltp_snapshot[k] = payload
            with open(LTP_SNAPSHOT_FILE, 'w') as f:
                json.dump(self.last_ltp_snapshot, f)
        except Exception:
            pass

    def safe_quote(self, keys):
        if isinstance(keys, str):
            keys_list = [keys]
        else:
            keys_list = list(keys)
        for attempt in range(3):
            try:
                tdiff = (datetime.now() - self.last_api_call).total_seconds()
                min_interval = max(1.0 / max(0.1, getattr(self, 'api_qps_limit', 1.0)), 0.25)
                if tdiff < min_interval:
                    time.sleep(min_interval - tdiff)
                if self.kite_api and getattr(self.kite_api, 'access_token', None):
                    q = self.kite_api.quote(keys_list)
                elif self.kite:
                    q = self.kite.quote(keys_list)
                else:
                    q = {}
                self.last_api_call = datetime.now()
                quotes_map = {k: (q.get(k, {}) if isinstance(q, dict) else {}) for k in keys_list}
                self._save_ltp_snapshot(quotes_map)
                return q if isinstance(q, dict) else {}
            except Exception:
                if attempt < 2:
                    time.sleep(1.0 + random.random())
                    continue
        res = {}
        for k in ([keys] if isinstance(keys, str) else keys):
            snap = (self.last_ltp_snapshot or {}).get(k) or {}
            lp = snap.get('last_price')
            close = snap.get('close')
            payload = {}
            if lp is not None:
                payload['last_price'] = lp
            if close is not None:
                payload['ohlc'] = {'close': close}
            res[k] = payload
        return res

    def _place_order(self, tradingsymbol: str, exchange: str, transaction_type: str,
                     quantity: int, product: str, order_type: str, price: Optional[float], validity: str, variety: str) -> Optional[str]:
        try:
            if self.kite_api and getattr(self.kite_api, 'access_token', None):
                return self.kite_api.place_order(
                    tradingsymbol=tradingsymbol,
                    exchange=exchange,
                    transaction_type=transaction_type,
                    order_type=order_type,
                    quantity=quantity,
                    price=price,
                    product=product,
                    validity=validity,
                    variety=variety
                )
            elif self.kite:
                resp = self.kite.place_order(
                    tradingsymbol=tradingsymbol,
                    exchange=exchange,
                    transaction_type=transaction_type,
                    order_type=order_type,
                    quantity=quantity,
                    price=price,
                    product=product,
                    validity=validity,
                    variety=variety
                )
                return resp.get('order_id') if isinstance(resp, dict) else None
        except Exception:
            return None
        return None

    def _order_history(self, order_id: str) -> List[Dict]:
        try:
            if self.kite_api and getattr(self.kite_api, 'access_token', None):
                return self.kite_api.order_history(order_id) or []
            elif self.kite:
                return self.kite.order_history(order_id) or []
        except Exception:
            return []
        return []

    # ------------------ TRADE JOURNAL AND PERFORMANCE TRACKING ------------------

    def update_performance_metrics(self):
        """Update performance metrics based on trade journal."""
        closed_trades = self.trade_journal.get('closed_trades', [])
        if not closed_trades:
            return

        profitable_trades = [t for t in closed_trades if t['pnl'] > 0]
        losing_trades = [t for t in closed_trades if t['pnl'] <= 0]

        total_trades = len(closed_trades)
        profitable_count = len(profitable_trades)

        wins = [t for t in closed_trades if t['pnl'] > 0]
        losses = [t for t in closed_trades if t['pnl'] <= 0]
        total_profit = sum([t['pnl'] for t in wins]) if wins else 0
        total_loss = -sum([t['pnl'] for t in losses]) if losses else 0
        profit_factor = (total_profit / total_loss) if total_loss > 0 else 0
        today = datetime.now().strftime('%Y-%m-%d')
        daily_pnl_val = self.trade_journal.get('daily_pnl', {}).get(today, 0)

        metrics = {
            'win_rate': (profitable_count / total_trades * 100) if total_trades > 0 else 0,
            'avg_win': np.mean([t['pnl'] for t in profitable_trades]) if profitable_trades else 0,
            'avg_loss': np.mean([t['pnl'] for t in losing_trades]) if losing_trades else 0,
            'largest_win': max([t['pnl'] for t in profitable_trades]) if profitable_trades else 0,
            'largest_loss': min([t['pnl'] for t in losing_trades]) if losing_trades else 0,
            'total_trades': total_trades,
            'profitable_trades': profitable_count,
            'profit_factor': profit_factor,
            'daily_pnl': daily_pnl_val
        }

        self.trade_journal['performance_metrics'] = metrics
        save_trade_journal(self.trade_journal)
        try:
            self.auto_optimize_thresholds()
        except Exception:
            pass
        try:
            if str(self.config.get('allocation_mode', '')).strip().lower() == 'win_rate':
                self._update_weights_by_win_rate()
        except Exception:
            pass

    def auto_optimize_thresholds(self):
        metrics = self.trade_journal.get('performance_metrics', {})
        win = float(metrics.get('win_rate', 0))
        pf = float(metrics.get('profit_factor', 0))
        base_conf = float(self.strategy_params.get('min_conf', self.min_confidence))
        base_rr = float(self.strategy_params.get('min_rr', self.min_rr_ratio))

        target_win = float(self.config.get('min_win_rate_threshold', 55.0))
        target_pf = float(self.config.get('min_profit_factor_threshold', 1.2))

        if win < target_win or pf < target_pf:
            self.min_confidence = min(9.5, base_conf + 0.3)
            self.min_rr_ratio = min(2.5, base_rr + 0.2)
        else:
            self.min_confidence = max(7.0, base_conf - 0.2)
            self.min_rr_ratio = max(1.4, base_rr - 0.1)

    def validate_strategy_performance(self, min_win_rate: float = 55.0, min_profit_factor: float = 1.2) -> bool:
        closed_trades = self.trade_journal.get('closed_trades', [])
        if not closed_trades:
            return True
        min_samples = int(self.config.get('min_closed_trades_for_validation', 10))
        if len(closed_trades) < min_samples:
            return True
        wins = [t for t in closed_trades if t['pnl'] > 0]
        losses = [t for t in closed_trades if t['pnl'] <= 0]
        win_rate = (len(wins) / len(closed_trades)) * 100 if closed_trades else 0
        total_profit = sum(t['pnl'] for t in wins) if wins else 0
        total_loss = -sum(t['pnl'] for t in losses) if losses else 0
        profit_factor = (total_profit / total_loss) if total_loss > 0 else (float('inf') if total_profit > 0 else 0)
        logger.info(f"Strategy Validation - Win Rate: {win_rate:.1f}%, Profit Factor: {profit_factor:.2f}")
        return win_rate >= min_win_rate and profit_factor >= min_profit_factor

    def record_closed_trade(self, position, exit_price: float, reason: str, pnl: float):
        """Record complete trade details for analysis."""
        trade_record = {
            'symbol': position.get('symbol'),
            'option_type': position.get('option_type', 'FUT'),
            'strike_price': position.get('strike_price', 0),
            'entry_time': position['entry_time'],
            'exit_time': datetime.now().isoformat(),
            'entry_price': position.get('premium_paid', position.get('entry_price', 0)),
            'exit_price': exit_price,
            'quantity': position['quantity'],
            'lot_size': position['lot_size'],
            'pnl': pnl,
            'reason': reason,
            'strategy': self.active_strategy_key,
            'confidence': position.get('confidence', 0)
        }

        self.trade_journal['closed_trades'].append(trade_record)

        # Update daily P&L
        today = datetime.now().strftime('%Y-%m-%d')
        if today not in self.trade_journal['daily_pnl']:
            self.trade_journal['daily_pnl'][today] = 0
        self.trade_journal['daily_pnl'][today] += pnl

        self.update_performance_metrics()
        save_trade_journal(self.trade_journal)

    # ------------------ CIRCUIT BREAKER CHECKS ------------------

    def check_circuit_breakers(self):
        """Comprehensive circuit breaker checks."""
        # 1. Consecutive losses
        if self.paper_portfolio['consecutive_losses'] >= self.max_consecutive_losses:
            return "CONSECUTIVE_LOSSES"

        # 2. Daily loss limit
        if self.check_max_daily_loss():
            return "DAILY_LOSS_LIMIT"

        # 3. Maximum drawdown
        peak_value = self.paper_portfolio.get('peak_portfolio_value',
                                             self.paper_portfolio['initial_capital'])
        current_value = self.paper_portfolio['total_value']
        drawdown_pct = (peak_value - current_value) / peak_value * 100

        if drawdown_pct >= 10:  # 10% max drawdown
            return "MAX_DRAWDOWN"

        # 4. API failure rate
        if self.api_failures > self.max_api_failures:
            return "API_FAILURE_RATE"

        # 5. Health status
        if self.health_status != "HEALTHY":
            return f"HEALTH_ISSUE: {self.health_status}"

        return None  # No circuit breaker triggered

    def check_max_daily_loss(self):
        """Checks if the maximum daily portfolio loss has been reached."""
        initial_cap = self.paper_portfolio.get('initial_capital', 500000)
        current_value = self.paper_portfolio.get('total_value', initial_cap)
        max_loss_amount = initial_cap * (self.max_daily_loss_pct / 100)

        current_loss = initial_cap - current_value

        if current_loss > max_loss_amount:
            self.stop_automatic_scheduler()
            self.send_telegram_alert(
                f"🛑 **FATAL STOP: MAX DAILY LOSS HIT** 🛑\n"
                f"Loss Limit: {self.max_daily_loss_pct:.1f}% (₹{max_loss_amount:,.0f})\n"
                f"Current Loss: ₹{current_loss:,.0f}\n"
                f"All trading has been halted."
            )
            return True
        return False

    # ------------------ POSITION SIZING (FIXING DELTA GAP) ------------------
    def calculate_position_size(self, trade_plan: Dict, premium: float, lot_size: int) -> int:
        """
        Calculates the optimal number of lots based on fixed risk percentage of capital.
        Uses a dynamic Black-Scholes Delta.
        """
        total_capital = self.paper_portfolio.get('initial_capital', 500000)
        S = trade_plan['entry']
        K = trade_plan.get('strike', S)
        T = max(trade_plan.get('expiry_days', 1) / 365, 0.001) # Time to expiry in years (min 1 day)
        sigma = trade_plan['volatility'] / 100 # Volatility as a decimal (e.g., 0.20)
        option_type = 'CALL' if trade_plan['direction'] == 'BULLISH' else 'PUT'

        # Risk-free rate (R) set conservatively
        RISK_FREE_RATE = 0.05

        # 1. Calculate Dynamic Delta
        option_delta = abs(self.calculate_black_scholes_delta(
            S, K, T, RISK_FREE_RATE, sigma, option_type
        ))

        # Ensure delta is within bounds
        option_delta = max(0.01, min(1.0, option_delta))

        # 2. Get Max Risk Amount (e.g., 0.5% of total capital)
        max_risk_pct = self.config.get('max_risk_per_trade_pct', 0.5) / 100
        max_risk_amount = total_capital * max_risk_pct

        # 3. Calculate Risk Per Share (Spot SL)
        spot_risk_per_share = abs(trade_plan['entry'] - trade_plan['stop_loss'])

        # Risk per lot in monetary terms: (Spot Risk * Lot Size * Actual Delta)
        risk_per_lot_monetary = spot_risk_per_share * lot_size * option_delta

        if risk_per_lot_monetary <= 0.05:
            calculated_lots = 1
        else:
            calculated_lots = math.floor(max_risk_amount / risk_per_lot_monetary)

        # Confidence-based scaling
        confidence = float(trade_plan.get('confidence', 8.0) or 8.0)
        if confidence >= 9.2:
            confidence_scale = 3.0
        elif confidence >= 8.8:
            confidence_scale = 2.0
        elif confidence >= 8.2:
            confidence_scale = 1.5
        else:
            confidence_scale = 1.0

        # Apply strategy multiplier, confidence scaling, and cash check
        boost = 1.0
        try:
            boost = float(self.config.get('lot_multiplier_boost', 1.0) or 1.0)
        except Exception:
            pass
        try:
            env_boost = os.getenv('LOT_MULTIPLIER_BOOST')
            if env_boost:
                boost = float(env_boost)
        except Exception:
            pass
        dyn = float(trade_plan.get('regime_confidence', 1.0) or 1.0)
        corr_penalty = float(trade_plan.get('correlation_penalty', 1.0) or 1.0)
        target_lots = max(1, int(calculated_lots * self.lot_multiplier * confidence_scale * boost * dyn * corr_penalty))
        max_lots_by_cash = math.floor(self._available_cash_for_strategy() / (premium * lot_size * 1.05))
        final_lots = max(1, min(target_lots, max_lots_by_cash))

        logger.debug(f"Sizing: Delta={option_delta:.3f}, Risk/Lot={risk_per_lot_monetary:.2f}, Final Lots={final_lots}")

        return final_lots

    # ------------------ INDICATORS AND SIGNAL LOGIC - FIXED VERSION ------------------

    def calculate_indicators(self, df: pd.DataFrame) -> Dict:
        if df is None or len(df) < 20:
            return None

        try:
            df = df.tail(100)
            close, high, low = df['Close'], df['High'], df['Low']
            current_price = float(close.iloc[-1])

            available_periods = min(14, len(df))

            delta = close.diff()
            gain = delta.clip(lower=0)
            loss = (-delta).clip(lower=0)
            rsi = 50.0
            if available_periods >= 14:
                # Use Wilder's smoothing for RSI
                avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
                avg_loss = loss.ewm(alpha=1/14, adjust=False).mean().replace(0, np.nan)
                rs = avg_gain / avg_loss
                rsi_series = 100 - (100 / (1 + rs))
                rsi_val = float(rsi_series.iloc[-1])
                if not np.isnan(rsi_val) and not np.isinf(rsi_val):
                    rsi = rsi_val

            tr = np.maximum(high - low,
                            (high - close.shift()).abs(),
                            (low - close.shift()).abs())
            atr = float(tr.rolling(available_periods).mean().iloc[-1])

            lookback = min(20, len(df))
            recent_high = float(high.tail(lookback).max())
            recent_low = float(low.tail(lookback).min())

            current_volume = float(df['Volume'].iloc[-1]) if 'Volume' in df.columns else 1.0
            avg_volume = float(df['Volume'].tail(lookback).mean()) if 'Volume' in df.columns else 1.0
            volume_ratio = (current_volume / avg_volume) if avg_volume > 0 else 1.0

            sma_period = min(20, len(df))
            sma_20_series = close.rolling(sma_period).mean()
            sma_20 = float(sma_20_series.iloc[-1])
            sma_50_series = close.rolling(min(50, len(df))).mean()
            sma_50 = float(sma_50_series.iloc[-1]) if len(df) >= 50 else sma_20

            # Bollinger Bands (20, 2)
            bb_std = float(close.rolling(sma_period).std().iloc[-1]) if len(df) >= sma_period else 0.0
            bb_upper = sma_20 + 2 * bb_std
            bb_lower = sma_20 - 2 * bb_std

            # MACD (12,26,9)
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd_series = ema12 - ema26
            macd_signal_series = macd_series.ewm(span=9, adjust=False).mean()
            macd = float(macd_series.iloc[-1])
            macd_signal = float(macd_signal_series.iloc[-1])
            macd_hist = macd - macd_signal

            prev_close = float(close.iloc[-2]) if len(close) > 1 else current_price
            price_change = ((current_price - prev_close) / prev_close) * 100 if prev_close else 0.0

            return {
                'current_price': current_price,
                'price_change': float(price_change),
                'volume_ratio': float(volume_ratio),
                'rsi': float(rsi),
                'support': recent_low,
                'resistance': recent_high,
                'volatility': max(5.0, (atr / current_price) * 100 * 4),
                'true_range': float(atr),
                'atr_percent': (atr / current_price) * 100 if current_price else 0.0,
                'sma_20': float(sma_20),
                'sma_50': float(sma_50),
                'macd': float(macd),
                'macd_signal': float(macd_signal),
                'macd_hist': float(macd_hist),
                'bb_upper': float(bb_upper),
                'bb_lower': float(bb_lower),
                'bars': int(len(df)),
                'has_volume': bool('Volume' in df.columns),
            }
        except Exception as e:
            logger.debug(f"Error calculating indicators: {e}")
            return None

    def calculate_bullish_score(self, ind: Dict) -> float:
        """Dynamically selects scoring logic based on active strategy."""
        if self.active_strategy_key == "AGGRESSIVE_REVERSAL":
            score = 0.0
            if ind['rsi'] <= 35: score += 4.0
            if ind['current_price'] <= ind['support']: score += 3.0
            if ind['price_change'] > 0.0: score += 1.0
            if ind['volume_ratio'] < 0.8: score += 2.0
            if ind.get('macd_hist', 0) > 0: score += 0.5
            regime = getattr(self, 'active_market_regime', 'TRENDING')
            vol = float(ind.get('volatility', 20))
            mult = 1.0
            if regime == 'RANGING':
                mult *= 1.10
            else:
                mult *= 0.95
            if vol > 30:
                mult *= 0.95
            elif vol < 12:
                mult *= 1.05
            return min(score * mult, 10.0)

        elif self.active_strategy_key == "MID_TREND_SWING":
            score = 0.0
            if ind['current_price'] > ind['sma_50']: score += 3.5
            if ind['sma_20'] > ind['sma_50']: score += 2.5
            if ind['macd'] > ind['macd_signal']: score += 2.0
            if ind.get('macd_hist', 0) > 0: score += 0.5
            if 50 <= ind['rsi'] <= 65: score += 2.0
            regime = getattr(self, 'active_market_regime', 'TRENDING')
            vol = float(ind.get('volatility', 20))
            mult = 1.0
            if regime == 'TRENDING':
                mult *= 1.10
            else:
                mult *= 0.95
            if vol > 30:
                mult *= 1.05
            elif vol < 12:
                mult *= 0.95
            return min(score * mult, 10.0)

        elif self.active_strategy_key == "AGGRESSIVE_SCALP":
            score = 0.0
            if ind['price_change'] > 0.7: score += 3.0
            if ind['volume_ratio'] >= 2.0: score += 3.0
            if ind['macd'] > ind['macd_signal'] and ind['macd'] > 0: score += 2.5
            if ind.get('macd_hist', 0) > 0: score += 0.5
            if ind['current_price'] > ind['sma_20']: score += 1.5
            regime = getattr(self, 'active_market_regime', 'TRENDING')
            vol = float(ind.get('volatility', 20))
            mult = 1.0
            if regime == 'TRENDING':
                mult *= 1.05
            else:
                mult *= 1.00
            if vol > 30:
                mult *= 1.05
            elif vol < 12:
                mult *= 0.95
            return min(score * mult, 10.0)

        score = 0.0
        if ind['current_price'] < ind['sma_20']: score += 1.5
        if ind['current_price'] < ind['sma_50']: score += 1.5
        if ind['volume_ratio'] >= 1.5: score += 1.5
        if ind['price_change'] > 0.5: score += 1.0
        if ind['macd'] > ind['macd_signal']: score += 1.5
        if ind['macd'] > 0: score += 1.0
        if ind.get('macd_hist', 0) > 0: score += 0.5
        # Bollinger squeeze breakout
        if ind.get('bb_upper') and ind.get('bb_lower'):
            band_width_pct = ((ind['bb_upper'] - ind['bb_lower']) / ind['current_price']) * 100
            if band_width_pct < 2.0 and ind['price_change'] > 0.5:
                score += 0.5
        resistance_distance_pct = ((ind['resistance'] - ind['current_price']) / ind['current_price']) * 100
        if 40 <= ind['rsi'] <= 70: score += 1.0
        if resistance_distance_pct >= 2.0: score += 1.0
        regime = getattr(self, 'active_market_regime', 'TRENDING')
        vol = float(ind.get('volatility', 20))
        mult = 1.0
        if regime == 'TRENDING':
            mult *= 1.05
        else:
            mult *= 0.95
        if vol > 30:
            mult *= 1.05
        elif vol < 12:
            mult *= 0.95
        return min(score * mult, 10.0)

    def calculate_bearish_score(self, ind: Dict) -> float:
        """Dynamically selects scoring logic based on active strategy."""
        if self.active_strategy_key == "AGGRESSIVE_REVERSAL":
            score = 0.0
            if ind['rsi'] >= 75: score += 4.0
            if ind['current_price'] >= ind['resistance']: score += 3.0
            if ind['price_change'] < 0.0: score += 1.0
            if ind['volume_ratio'] < 0.8: score += 2.0
            if ind.get('macd_hist', 0) < 0: score += 0.5
            regime = getattr(self, 'active_market_regime', 'TRENDING')
            vol = float(ind.get('volatility', 20))
            mult = 1.0
            if regime == 'RANGING':
                mult *= 1.10
            else:
                mult *= 0.95
            if vol > 30:
                mult *= 0.95
            elif vol < 12:
                mult *= 1.05
            return min(score * mult, 10.0)

        elif self.active_strategy_key == "MID_TREND_SWING":
            score = 0.0
            if ind['current_price'] < ind['sma_50']: score += 3.5
            if ind['sma_20'] < ind['sma_50']: score += 2.5
            if ind['macd'] < ind['macd_signal']: score += 2.0
            if ind.get('macd_hist', 0) < 0: score += 0.5
            if 35 <= ind['rsi'] <= 50: score += 2.0
            regime = getattr(self, 'active_market_regime', 'TRENDING')
            vol = float(ind.get('volatility', 20))
            mult = 1.0
            if regime == 'TRENDING':
                mult *= 1.10
            else:
                mult *= 0.95
            if vol > 30:
                mult *= 1.05
            elif vol < 12:
                mult *= 0.95
            return min(score * mult, 10.0)

        elif self.active_strategy_key == "AGGRESSIVE_SCALP":
            score = 0.0
            if ind['price_change'] < -0.7: score += 3.0
            if ind['volume_ratio'] >= 2.0: score += 3.0
            if ind['macd'] < ind['macd_signal'] and ind['macd'] < 0: score += 2.5
            if ind.get('macd_hist', 0) < 0: score += 0.5
            if ind['current_price'] < ind['sma_20']: score += 1.5
            regime = getattr(self, 'active_market_regime', 'TRENDING')
            vol = float(ind.get('volatility', 20))
            mult = 1.0
            if regime == 'TRENDING':
                mult *= 1.05
            else:
                mult *= 1.00
            if vol > 30:
                mult *= 1.05
            elif vol < 12:
                mult *= 0.95
            return min(score * mult, 10.0)

        score = 0.0
        if ind['current_price'] < ind['sma_20']: score += 1.5
        if ind['current_price'] < ind['sma_50']: score += 1.5
        if ind['volume_ratio'] >= 1.5: score += 1.5
        if ind['price_change'] < -0.5: score += 1.0
        if ind['macd'] < ind['macd_signal']: score += 1.5
        if ind['macd'] < 0: score += 1.0
        if ind.get('macd_hist', 0) < 0: score += 0.5
        if ind.get('bb_upper') and ind.get('bb_lower'):
            band_width_pct = ((ind['bb_upper'] - ind['bb_lower']) / ind['current_price']) * 100
            if band_width_pct < 2.0 and ind['price_change'] < -0.5:
                score += 0.5
        support_distance_pct = ((ind['current_price'] - ind['support']) / ind['current_price']) * 100
        if 30 <= ind['rsi'] <= 60: score += 1.0
        if support_distance_pct >= 2.0: score += 1.0
        regime = getattr(self, 'active_market_regime', 'TRENDING')
        vol = float(ind.get('volatility', 20))
        mult = 1.0
        if regime == 'TRENDING':
            mult *= 1.05
        else:
            mult *= 0.95
        if vol > 30:
            mult *= 1.05
        elif vol < 12:
            mult *= 0.95
        return min(score * mult, 10.0)

    def calculate_potential(self, ind: Dict, direction: str, power: float) -> float:
        """Calculate potential move % based on score and ATR"""
        if self.active_strategy_key == "MID_TREND_SWING":
             base_potential = power * 0.8
        else:
             base_potential = power / 2.0

        atr_target = ind['atr_percent'] * 1.5
        return min(max(base_potential, atr_target, 2.5), 6.0)

    def compute_regime_confidence(self, ind: Dict) -> float:
        score = 0.0
        adr_align = min(abs(ind.get('price_change', 0.0)) / max(1e-6, ind.get('atr_percent', 1.0)), 2.0)
        vol_conf = 1.0 if (ind.get('volatility', 0.0) >= 15.0) else 0.5
        time_minutes = datetime.now().hour * 60 + datetime.now().minute
        tod = 0.8 if (time_minutes % (24*60) in range(9*60, 12*60)) else 0.6
        recent_stability = 0.7 if abs(ind.get('macd_hist', 0.0)) > 0.05 else 0.5
        score = 0.3 * adr_align + 0.2 * vol_conf + 0.15 * tod + 0.15 * recent_stability + 0.2 * (ind.get('bars', 0) >= 50)
        return max(0.0, min(1.0, score))

    def compute_symbol_correlation(self, a: str, b: str) -> Optional[float]:
        try:
            data = self.get_stock_data_batch_with_retry([a, b])
            da = data.get(a)
            db = data.get(b)
            if da is None or db is None or da.empty or db.empty:
                return None
            ca = da['Close'].tail(60).pct_change().dropna()
            cb = db['Close'].tail(60).pct_change().dropna()
            if len(ca) < 20 or len(cb) < 20:
                return None
            return float(np.corrcoef(ca[-min(len(ca), len(cb)):], cb[-min(len(ca), len(cb)):])[0,1])
        except Exception:
            return None

    def detect_signals(self, ind: Dict) -> List[Tuple]:
        bullish = self.calculate_bullish_score(ind)
        bearish = self.calculate_bearish_score(ind)
        signals = []
        regime = getattr(self, 'active_market_regime', 'TRENDING')
        volatility = ind.get('volatility', 20)
        dyn_conf_adj = 0.0
        if regime == 'RANGING':
            dyn_conf_adj += 0.4
        else:
            dyn_conf_adj += 0.2
        if volatility > 30:
            dyn_conf_adj += 0.4
        elif volatility < 12:
            dyn_conf_adj -= 0.2
        min_conf = float(self.min_confidence) + dyn_conf_adj
        min_conf = max(min_conf, float(self.min_confidence))
        
        # Directional strategies
        diff_req = 2.0 if regime == 'RANGING' else 1.5
        if bullish >= min_conf and bullish - bearish >= diff_req:
            bull_pot = self.calculate_potential(ind, 'BULLISH', bullish)
            if bull_pot >= self.required_move:
                signals.append(('BULLISH', bull_pot, bullish))

        if bearish >= min_conf and bearish - bullish >= diff_req:
            bear_pot = self.calculate_potential(ind, 'BEARISH', bearish)
            if bear_pot >= self.required_move:
                signals.append(('BEARISH', bear_pot, bearish))
        
        # Volatility strategies for high volatility environments
        is_commodity_mode = bool(self.strategy_params.get('market_type') == 'COMMODITY')
        if (not is_commodity_mode) and volatility > 25 and abs(bullish - bearish) < 2.0:
            signals.append(('VOLATILITY_STRADDLE', self.required_move * 0.8, min_conf))
            signals.append(('VOLATILITY_STRANGLE', self.required_move * 0.7, min_conf - 0.5))
        
        # Range-bound strategies for low volatility
        if (not is_commodity_mode) and volatility < 15 and abs(bullish - bearish) < 1.0:
            signals.append(('IRON_CONDOR', self.required_move * 0.5, min_conf - 1.0))

        return signals

    def trade_plan(self, symbol: str, direction: str, potential: float, confidence: float,
                   price: float, indicators: Dict):
        atr_val = float(indicators.get('true_range', 0.0) or 0.0)
        if atr_val > 0 and price > 0:
            stop_abs = atr_val * 2.0
            trail_abs = atr_val * 1.5
            stop_pct = (stop_abs / price) * 100
        else:
            stop_pct = indicators['atr_percent'] * 1.5
        stop_pct = max(stop_pct, 0.5)

        if direction == 'BULLISH':
            target = round(price * (1 + potential/100), 1)
            stop = round(price * (1 - stop_pct/100), 1)
            rr = round((target - price) / (price - stop), 2) if price > stop else 1.0
        else:
            target = round(price * (1 - potential/100), 1)
            stop = round(price * (1 + stop_pct/100), 1)
            rr = round((price - target) / (stop - price), 2) if stop > price else 1.0

        if rr < self.min_rr_ratio:
            logger.debug(f"Plan for {symbol} failed R:R check: {rr:.2f} < {self.min_rr_ratio}")
            return None

        return {
            'symbol': symbol, 'direction': direction, 'entry': price,
            'target': target, 'stop_loss': stop, 'risk_reward': rr,
            'confidence': confidence, 'potential': potential,
            'trade_mode': 'SWING',
            'volatility': indicators['volatility']
        }

    def get_option_metrics_paper(self, symbol: str, direction: str, spot_price: float, volatility: float) -> Tuple[float, float, int]:

        # --- DYNAMIC EXPIRY (The Fix is here) ---
        expiry_date = self.get_next_tradable_expiry(symbol)
        expiry_date_str = expiry_date.strftime("%Y-%m-%d")
        days_to_expiry_int = (expiry_date - datetime.now().date()).days

        # --- DYNAMIC STRIKE ---
        initial_strike = self.get_strike_price(spot_price, direction)
        try:
            if self.option_strategy and isinstance(self.option_strategy, str):
                if self.option_strategy.upper() == 'ATM':
                    pass
                elif self.option_strategy.upper() in ['+100','-100','+200','-200']:
                    offset = int(self.option_strategy)
                    initial_strike = initial_strike + offset
        except Exception:
            pass
        strike = self.find_nearest_available_strike(symbol, initial_strike, 'CALL' if direction == 'BULLISH' else 'PUT', expiry_date_str)

        premium = 0.0
        option_type = 'CALL' if direction == 'BULLISH' else 'PUT'
        option_token, tradingsymbol = self.get_option_instrument_token(symbol, strike, option_type, expiry_date_str)
        if tradingsymbol:
            parsed = self.parse_strike_from_tradingsymbol(tradingsymbol)
            if parsed:
                strike = parsed
        if option_token and tradingsymbol and self.kite:
            try:
                quote_key = f"NFO:{tradingsymbol}"
                live_quote = self.safe_quote(quote_key)
                ltp = live_quote.get(quote_key, {}).get('last_price')
                if ltp and ltp > 0.05:
                    premium = float(ltp)
            except Exception:
                pass
        lot_size = self.get_lot_size(symbol)

        logger.debug(f"Metrics: {symbol} Spot={spot_price:.1f}, Strike={strike:.0f}, Prem={premium:.1f}, Lot={lot_size}")
        return strike, premium, lot_size

    def get_commodity_metrics_paper(self, symbol: str, direction: str, current_price: float, volatility: float) -> Tuple[float, float, int]:
        """Get commodity futures metrics for paper trading"""
        
        # --- DYNAMIC EXPIRY FOR COMMODITIES ---
        expiry_date = self.get_next_commodity_expiry(symbol)
        expiry_date_str = expiry_date.strftime("%Y-%m-%d")
        days_to_expiry_int = (expiry_date - datetime.now().date()).days

        # For commodities, we trade futures directly, so no strike selection needed
        # The "strike" is effectively the current futures price
        futures_price = current_price
        
        # Get live commodity futures price
        commodity_token, tradingsymbol = self.get_commodity_instrument_token(symbol, expiry_date_str, 'FUT')
        
        if commodity_token and tradingsymbol and self.kite:
            try:
                quote_key = f"MCX:{tradingsymbol}"
                live_quote = self.safe_quote(quote_key)
                ltp = live_quote.get(quote_key, {}).get('last_price')
                if ltp and ltp > 0:
                    futures_price = float(ltp)
            except Exception:
                pass
        
        lot_size = self.commodity_lot_sizes.get(symbol, 1)
        
        logger.debug(f"Commodity Metrics: {symbol} Price={futures_price:.1f}, Lot={lot_size}, Expiry={expiry_date_str}")
        return futures_price, futures_price, lot_size  # Return price as both strike and premium for commodities

    def execute_option_trade(self, trade_plan: Dict) -> bool:
        if getattr(self, 'observe_only', False):
            logger.info("OBSERVE_ONLY: skipping option trade execution")
            return False
        symbol = trade_plan['symbol']
        direction = trade_plan['direction']
        spot_price = trade_plan['entry']
        volatility = trade_plan.get('volatility', 20)

        # Comprehensive circuit breaker check
        circuit_breaker = self.check_circuit_breakers()
        if circuit_breaker:
            logger.critical(f"{Fore.RED}CIRCUIT BREAKER TRIGGERED: {circuit_breaker}. STOPPING ALL TRADES.{Style.RESET_ALL}")
            self.stop_automatic_scheduler()
            return False

        logger.info(f"Attempting execution for {symbol} ({direction}). Conf: {trade_plan['confidence']:.1f}")

        try:
            # 1. Determine Option Metrics (Strike & Expiry)
            expiry_date = self.get_next_tradable_expiry(symbol)
            expiry_date_str = expiry_date.strftime("%Y-%m-%d")
            days_to_expiry_int = (expiry_date - datetime.now().date()).days

            initial_strike = self.get_strike_price(spot_price, direction)
            final_strike = self.find_nearest_available_strike(symbol, initial_strike, 'CALL' if direction == 'BULLISH' else 'PUT', expiry_date_str)

            if final_strike is None:
                today_date = datetime.now().date()
                year = today_date.year
                month = today_date.month
                last_day = calendar.monthrange(year, month)[1]
                monthly_expiry = datetime(year, month, last_day).date()
                while monthly_expiry.weekday() != 3:
                    monthly_expiry -= timedelta(days=1)
                if monthly_expiry <= today_date:
                    next_month = today_date.replace(day=1) + timedelta(days=32)
                    year = next_month.year
                    month = next_month.month
                    last_day = calendar.monthrange(year, month)[1]
                    monthly_expiry = datetime(year, month, last_day).date()
                    while monthly_expiry.weekday() != 3:
                        monthly_expiry -= timedelta(days=1)
                expiry_date = monthly_expiry
                expiry_date_str = expiry_date.strftime("%Y-%m-%d")
                days_to_expiry_int = (expiry_date - datetime.now().date()).days
                final_strike = self.find_nearest_available_strike(symbol, initial_strike, 'CALL' if direction == 'BULLISH' else 'PUT', expiry_date_str)
                if final_strike is None:
                    logger.error(f"Cannot execute {symbol}: Strike calculation failed unexpectedly.")
                    return False

            strike_rounded = round(final_strike)
            option_type = 'CALL' if direction=='BULLISH' else 'PUT'

            # 2. Get Live Option Premium and Instrument Details
            option_token, tradingsymbol = self.get_option_instrument_token(
                symbol, strike_rounded, option_type, expiry_date_str
            )
            if tradingsymbol:
                parsed = self.parse_strike_from_tradingsymbol(tradingsymbol)
                if parsed and parsed != strike_rounded:
                    strike_rounded = parsed
            if not option_token or not tradingsymbol:
                today_date = datetime.now().date()
                year = today_date.year
                month = today_date.month
                last_day = calendar.monthrange(year, month)[1]
                monthly_expiry = datetime(year, month, last_day).date()
                while monthly_expiry.weekday() != 3:
                    monthly_expiry -= timedelta(days=1)
                if monthly_expiry <= today_date:
                    next_month = today_date.replace(day=1) + timedelta(days=32)
                    year = next_month.year
                    month = next_month.month
                    last_day = calendar.monthrange(year, month)[1]
                    monthly_expiry = datetime(year, month, last_day).date()
                    while monthly_expiry.weekday() != 3:
                        monthly_expiry -= timedelta(days=1)
                expiry_date = monthly_expiry
                expiry_date_str = expiry_date.strftime("%Y-%m-%d")
                days_to_expiry_int = (expiry_date - datetime.now().date()).days
                final_strike = self.find_nearest_available_strike(symbol, initial_strike, 'CALL' if direction == 'BULLISH' else 'PUT', expiry_date_str)
                strike_rounded = round(final_strike) if final_strike is not None else strike_rounded
                option_token, tradingsymbol = self.get_option_instrument_token(
                    symbol, strike_rounded, option_type, expiry_date_str
                )
            spot_token = self.token_map.get(symbol) # Get the underlying spot token

            premium = None
            if option_token and tradingsymbol:
                quote_key_full = f"NFO:{tradingsymbol}"
                try:
                    live_quote = self.safe_quote(quote_key_full)
                    live_ltp = live_quote[quote_key_full]['last_price']
                    bid = live_quote[quote_key_full].get('buy_price')
                    ask = live_quote[quote_key_full].get('sell_price')
                    if bid and ask and bid > 0 and ask > 0:
                        mid = (bid + ask) / 2.0
                        spread_pct = (ask - bid) / mid if mid > 0 else 0.0
                        if spread_pct > 0.02:
                            logger.warning(f"Skipping {tradingsymbol}: Spread {spread_pct:.2%} > 2%")
                            return False

                    if live_ltp is not None and live_ltp > 0.05:
                        premium = live_ltp
                    else:
                        logger.warning(f"Live LTP for {tradingsymbol} invalid (₹{live_ltp}). Falling back to simulation.")
                except Exception as e:
                    logger.error(f"Kite quote failed during execution for {tradingsymbol}: {e}. Falling back to simulation.")

            if premium is None or premium <= 0.05:
                logger.error("Live premium unavailable; skipping trade.")
                return False

            lot_size = self.get_lot_size(symbol)
            if not lot_size or lot_size <= 0:
                logger.error(f"Cannot execute {symbol}: lot size unavailable.")
                return False

            # --- Position Sizing using Dynamic Delta ---
            trade_plan['strike'] = strike_rounded
            trade_plan['expiry_days'] = days_to_expiry_int
            quantity = self.calculate_position_size(trade_plan, premium, lot_size)
            # -----------------------------------------

            cost_per_lot = premium * lot_size
            available_cash = self._available_cash()
            trade_value = cost_per_lot * quantity # Initial value estimate

            if quantity == 0:
                 if self.mode == 'PAPER':
                     quantity = 1
                     logger.warning(f"Position size was zero; defaulting to 1 lot in PAPER for {symbol}.")
                 else:
                     logger.warning(f"Skipping {symbol}: Calculated position size is zero.")
                     return False

            if available_cash < trade_value:
                logger.warning(f"Skipping {symbol}: Insufficient cash (₹{available_cash:,.0f}) for {quantity} lots (Cost: ₹{trade_value:,.0f}).")
                return False

            if len(self.paper_portfolio['positions']) >= self.max_positions:
                logger.warning(f"Max open positions ({self.max_positions}) reached. Skipping {symbol}.")
                return False

            self.scan_summary['trades_executed_count'] += 1
            logger.info(f"{Fore.YELLOW}PRE-EXECUTION CHECK PASSED for {symbol}. Lots: {quantity}, Estimated Cost: ₹{trade_value:,.0f}{Style.RESET_ALL}")

            # --- PHASE 3: EXECUTION LOGIC (LIVE MODE CHANGES) ---
            trade_successful = False
            final_entry_premium = premium
            order_id = None # Initialize order_id

            if self.mode == 'LIVE':
                if not self.kite or not option_token or not tradingsymbol:
                    logger.critical("Kite not initialized or Option Token/Tradingsymbol missing. Cannot place LIVE order.")
                    return False

                try:
                    limit_price = self._market_protection_limit(premium, 'BUY', 'OPT')
                    est_cost_per_lot = limit_price * lot_size
                    est_trade_value = est_cost_per_lot * quantity
                    if available_cash < est_trade_value:
                        logger.warning(f"Skipping {symbol}: Insufficient cash (₹{available_cash:,.0f}) for protected-limit cost (₹{est_trade_value:,.0f}).")
                        return False
                    order_id = self._place_order(
                        tradingsymbol=tradingsymbol,
                        exchange=self.kite.EXCHANGE_NFO,
                        transaction_type=self.kite.TRANSACTION_TYPE_BUY,
                        quantity=quantity * lot_size,
                        product=self.kite.PRODUCT_MIS,
                        order_type=self.kite.ORDER_TYPE_LIMIT,
                        price=limit_price,
                        validity=self.kite.VALIDITY_DAY,
                        variety=self.kite.VARIETY_REGULAR
                    )
                    logger.info(f"{Fore.GREEN}✅ LIVE Order placed: {symbol}, ID: {order_id}{Style.RESET_ALL}")

                    # --- ENHANCED: Polling loop for execution confirmation ---
                    max_retries = 10
                    fill_confirmed = False
                    widen_steps = [3, 6, 9]
                    widen_idx = 0
                    for attempt in range(max_retries):
                        time.sleep(0.5) # Poll every 0.5s
                        # Need to retry fetching order history as well, as that API can fail temporarily
                        try:
                            order_info = self._order_history(order_id)
                        except Exception as e:
                            logger.warning(f"Failed to fetch order history for {order_id} (Attempt {attempt+1}): {e}")
                            if attempt < max_retries - 1: continue
                            break

                        filled_order = next((o for o in order_info if o.get('status') == 'COMPLETE'), None)

                        if filled_order:
                            final_entry_premium = filled_order['average_price']
                            logger.info(f"✅ Order filled successfully (Attempt {attempt+1}). Price: ₹{final_entry_premium:.2f}")
                            fill_confirmed = True
                            break

                        last_status = order_info[-1].get('status') if order_info else 'UNKNOWN'
                        if last_status in ['REJECTED', 'CANCELLED']:
                             logger.error(f"{Fore.RED}🔴 LIVE ORDER REJECTED/CANCELLED. Status: {last_status}{Style.RESET_ALL}")
                             return False # Fail trade immediately

                        if widen_idx < len(widen_steps) and attempt >= widen_steps[widen_idx]:
                            try:
                                widened_price = self._widened_limit(premium, 'BUY', 'OPT', widen_idx + 1)
                                est_cost_per_lot2 = widened_price * lot_size
                                est_trade_value2 = est_cost_per_lot2 * quantity
                                if available_cash < est_trade_value2:
                                    try:
                                        self.kite.cancel_order(self.kite.VARIETY_REGULAR, order_id)
                                    except Exception:
                                        pass
                                    logger.warning(f"Skipping {symbol}: Insufficient cash for widened price (₹{widened_price:.2f}).")
                                    return False
                                try:
                                    self.kite.modify_order(variety=self.kite.VARIETY_REGULAR, order_id=order_id, price=widened_price, order_type=self.kite.ORDER_TYPE_LIMIT)
                                    logger.info(f"Widened BUY limit to ₹{widened_price:.2f} (step {widen_idx+1})")
                                except Exception:
                                    try:
                                        self.kite.cancel_order(self.kite.VARIETY_REGULAR, order_id)
                                    except Exception:
                                        pass
                                    order_id = self._place_order(
                                        tradingsymbol=tradingsymbol,
                                        exchange=self.kite.EXCHANGE_NFO,
                                        transaction_type=self.kite.TRANSACTION_TYPE_BUY,
                                        quantity=quantity * lot_size,
                                        product=self.kite.PRODUCT_MIS,
                                        order_type=self.kite.ORDER_TYPE_LIMIT,
                                        price=widened_price,
                                        validity=self.kite.VALIDITY_DAY,
                                        variety=self.kite.VARIETY_REGULAR
                                    )
                                    logger.info(f"Replaced BUY order with widened limit ₹{widened_price:.2f}")
                                    widen_idx += 1
                            except Exception:
                                pass

                    if not fill_confirmed:
                        logger.error(f"{Fore.RED}🔴 LIVE ORDER FAILED TO CONFIRM FILL after {max_retries} attempts.{Style.RESET_ALL}")
                        return False

                    trade_successful = True
                    # ----------------------------------------------------

                except (InputException, TokenException, PermissionException, OrderException, Exception) as e:
                    logger.error(f"{Fore.RED}🔴 LIVE ORDER FAILED for {symbol}: {e}{Style.RESET_ALL}")
                    return False

            else: # PAPER mode
                logger.debug(f"Executing paper trade for {symbol}...")
                trade_successful = True
                final_entry_premium = premium # Use simulated premium for paper mode

            # --- END EXECUTION LOGIC ---

            if trade_successful:
                final_trade_value = final_entry_premium * lot_size * quantity

                if option_token and option_token != 0:
                    self.subscribe_to_option(option_token)

                # NEW: Subscribe to underlying spot for real-time SL monitoring
                if spot_token and spot_token != 0:
                    self.subscribe_to_spot(spot_token)

                # Use the actual entry premium/value for accounting with guard against negative cash
                if self._available_cash() < final_trade_value:
                    logger.error(f"Cash shortfall detected at accounting for {symbol}. Required: ₹{final_trade_value:,.0f}, Available: ₹{self.paper_portfolio['cash']:,.0f}. Skipping position creation.")
                    return False
                self.paper_portfolio['margin_used'] += final_trade_value
                self._recalc_cash()

                pos_key = f"{symbol}_{direction}_{strike_rounded}_{datetime.now().strftime('%H%M%S')}"

                self.paper_portfolio['positions'][pos_key] = {
                    'symbol': symbol,
                    'option_type': option_type,
                    'strike_price': strike_rounded,
                    'spot_price': spot_price,
                    'premium_paid': final_entry_premium, # Use actual/final premium
                    'lot_size': lot_size,
                    'quantity': quantity,
                    'entry_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    'stop_loss': trade_plan['stop_loss'],
                    'target': trade_plan['target'],
                    'trade_mode': trade_plan.get('trade_mode','SWING'),
                    'expiry_days': days_to_expiry_int,
                    'moneyness': 'ATM',
                    'current_premium': final_entry_premium,
                    'current_spot': spot_price,
                    'confidence': trade_plan['confidence'],
                    'risk_reward': trade_plan['risk_reward'],
                    'volatility': trade_plan.get('volatility', 20),
                    'peak_pnl_pct': 0.0,
                    'token': option_token,
                    'tradingsymbol': tradingsymbol, # CRITICAL: Store tradingsymbol for live exit
                    'order_id': order_id, # Store the initial order ID for reference
                    'spot_token': spot_token # NEW: Store spot token for WebSocket monitoring
                }
                self.save_trade_history()

                try:
                    if 'slippage' not in self.trade_journal:
                        self.trade_journal['slippage'] = []
                    planned = float(premium)
                    actual = float(final_entry_premium)
                    slip = abs(actual - planned) / planned if planned > 0 else 0.0
                    self.trade_journal['slippage'].append(slip)
                    if len(self.trade_journal['slippage']) > 10:
                        s = float(np.mean(self.trade_journal['slippage'][-10:]))
                        if s > 0.02:
                            self.strategy_paused_until = datetime.now() + timedelta(minutes=10)
                except Exception:
                    pass

                try:
                    if 'slippage' not in self.trade_journal:
                        self.trade_journal['slippage'] = []
                    self.trade_journal['slippage'].append(0.0)
                    if len(self.trade_journal['slippage']) > 10:
                        s = float(np.mean(self.trade_journal['slippage'][-10:]))
                        if s > 0.02:
                            self.strategy_paused_until = datetime.now() + timedelta(minutes=10)
                except Exception:
                    pass

                self.send_trade_alert(trade_plan, premium=final_entry_premium, strike=strike_rounded, lot_size=lot_size, quantity=quantity, trade_value=final_trade_value, status="TRADE EXECUTED")
                if self.dashboard_app:
                    self.dashboard_app.socketio.emit('trade_executed', {
                        'symbol': symbol, 'option_type': option_type,
                        'strike': strike_rounded, 'premium': final_entry_premium, 'cost': final_trade_value
                    })

                logger.info(f"{Fore.GREEN}✅ Trade executed: {symbol} @ Lots: {quantity}, Cost: ₹{final_trade_value:,.0f}{Style.RESET_ALL}")
                return True

            logger.error(f"Trade execution stub returned False for {symbol}.{Style.RESET_ALL}")
            return False
        except Exception as e:
            logger.error(f"{Fore.RED}Error executing option trade for {symbol}: {e}{Style.RESET_ALL}")
            traceback.print_exc()
            return False

    def execute_commodity_trade(self, trade_plan: Dict) -> bool:
        if getattr(self, 'observe_only', False):
            logger.info("OBSERVE_ONLY: skipping commodity trade execution")
            return False
        """Execute commodity futures trade using MCX exchange"""
        symbol = trade_plan['symbol']
        direction = trade_plan['direction']
        entry_price = trade_plan['entry']
        volatility = trade_plan.get('volatility', 20)

        # Comprehensive circuit breaker check
        circuit_breaker = self.check_circuit_breakers()
        if circuit_breaker:
            logger.critical(f"{Fore.RED}CIRCUIT BREAKER TRIGGERED: {circuit_breaker}. STOPPING ALL TRADES.{Style.RESET_ALL}")
            self.stop_automatic_scheduler()
            return False

        logger.info(f"Attempting commodity execution for {symbol} ({direction}). Conf: {trade_plan['confidence']:.1f}")

        try:
            # 1. Determine Commodity Contract Metrics (Expiry)
            expiry_date = self.get_next_commodity_expiry(symbol)
            expiry_date_str = expiry_date.strftime("%Y-%m-%d")
            days_to_expiry_int = (expiry_date - datetime.now().date()).days

            # 2. Get Live Commodity Contract Details
            commodity_token, tradingsymbol = self.get_commodity_instrument_token(
                symbol, expiry_date_str, 'FUT'
            )
            try:
                logger.info(f"MCX contract resolved: {symbol} → {tradingsymbol} ({expiry_date_str}) token={commodity_token}")
            except Exception:
                pass
            
            if not commodity_token or not tradingsymbol:
                logger.error(f"Cannot execute {symbol}: Commodity contract not found.")
                return False

            # 3. Get Live Commodity Price
            current_price = None
            if commodity_token and tradingsymbol:
                quote_key_full = f"MCX:{tradingsymbol}"
                try:
                    live_quote = self.safe_quote(quote_key_full)
                    live_ltp = live_quote[quote_key_full]['last_price']

                    if live_ltp is not None and live_ltp > 0:
                        current_price = live_ltp
                    else:
                        logger.warning(f"Live LTP for {tradingsymbol} invalid (₹{live_ltp}). Falling back to simulation.")
                except Exception as e:
                    logger.error(f"Kite quote failed during commodity execution for {tradingsymbol}: {e}. Falling back to simulation.")
            # No fallback to entry price; require a valid current price

            if current_price is None or current_price <= 0:
                logger.error("Live commodity price unavailable; skipping trade.")
                return False

            lot_size = self.commodity_lot_sizes.get(symbol)
            if not lot_size or lot_size <= 0:
                logger.error(f"Cannot execute {symbol}: Commodity lot size unavailable.")
                return False

            # 4. Position Sizing for Commodities
            trade_plan['expiry_days'] = days_to_expiry_int
            quantity = self.calculate_position_size(trade_plan, current_price, lot_size)

            cost_per_lot = current_price * lot_size
            available_cash = self._available_cash()
            trade_value = cost_per_lot * quantity

            if quantity == 0:
                 if self.mode == 'PAPER':
                     quantity = 1
                     logger.warning(f"Commodity position size was zero; defaulting to 1 lot in PAPER for {symbol}.")
                 else:
                     logger.warning(f"Skipping {symbol}: Calculated commodity position size is zero.")
                     return False

            if available_cash < trade_value:
                logger.warning(f"Skipping {symbol}: Insufficient cash (₹{available_cash:,.0f}) for {quantity} lots (Cost: ₹{trade_value:,.0f}).")
                return False

            if len(self.paper_portfolio['positions']) >= self.max_positions:
                logger.warning(f"Max open positions ({self.max_positions}) reached. Skipping {symbol}.")
                return False

            self.scan_summary['trades_executed_count'] += 1
            logger.info(f"{Fore.YELLOW}PRE-EXECUTION CHECK PASSED for commodity {symbol}. Lots: {quantity}, Estimated Cost: ₹{trade_value:,.0f}{Style.RESET_ALL}")

            # 5. EXECUTION LOGIC FOR COMMODITIES
            trade_successful = False
            final_entry_price = current_price
            order_id = None

            if self.mode == 'LIVE':
                if not self.kite or not commodity_token or not tradingsymbol:
                    logger.critical("Kite not initialized or Commodity Token/Tradingsymbol missing. Cannot place LIVE order.")
                    return False

                try:
                    limit_price = self._market_protection_limit(current_price, 'BUY', 'FUT')
                    est_cost_per_lot2 = limit_price * lot_size
                    est_trade_value2 = est_cost_per_lot2 * quantity
                    if available_cash < est_trade_value2:
                        logger.warning(f"Skipping {symbol}: Insufficient cash (₹{available_cash:,.0f}) for protected-limit cost (₹{est_trade_value2:,.0f}).")
                        return False
                    
                    order_id = self._place_order(
                        tradingsymbol=tradingsymbol,
                        exchange=self.kite.EXCHANGE_MCX,
                        transaction_type=self.kite.TRANSACTION_TYPE_BUY,
                        quantity=quantity * lot_size,
                        product=self.kite.PRODUCT_MIS,
                        order_type=self.kite.ORDER_TYPE_LIMIT,
                        price=limit_price,
                        validity=self.kite.VALIDITY_DAY,
                        variety=self.kite.VARIETY_REGULAR
                    )
                    logger.info(f"{Fore.GREEN}✅ LIVE Commodity Order placed: {symbol}, ID: {order_id}{Style.RESET_ALL}")

                    # Polling loop for execution confirmation
                    max_retries = 10
                    fill_confirmed = False
                    widen_steps = [3, 6, 9]
                    widen_idx = 0
                    for attempt in range(max_retries):
                        time.sleep(0.5)
                        try:
                            order_info = self._order_history(order_id)
                        except Exception as e:
                            logger.warning(f"Failed to fetch commodity order history for {order_id} (Attempt {attempt+1}): {e}")
                            if attempt < max_retries - 1: continue
                            break

                        filled_order = next((o for o in order_info if o.get('status') == 'COMPLETE'), None)

                        if filled_order:
                            final_entry_price = filled_order['average_price']
                            logger.info(f"✅ Commodity order filled successfully (Attempt {attempt+1}). Price: ₹{final_entry_price:.2f}")
                            fill_confirmed = True
                            break

                        last_status = order_info[-1].get('status') if order_info else 'UNKNOWN'
                        if last_status in ['REJECTED', 'CANCELLED']:
                             logger.error(f"{Fore.RED}🔴 LIVE COMMODITY ORDER REJECTED/CANCELLED. Status: {last_status}{Style.RESET_ALL}")
                             return False

                        if widen_idx < len(widen_steps) and attempt >= widen_steps[widen_idx]:
                            try:
                                widened_price = self._widened_limit(current_price, 'BUY', 'FUT', widen_idx + 1)
                                est_cost_per_lot3 = widened_price * lot_size
                                est_trade_value3 = est_cost_per_lot3 * quantity
                                if available_cash < est_trade_value3:
                                    try:
                                        self.kite.cancel_order(self.kite.VARIETY_REGULAR, order_id)
                                    except Exception:
                                        pass
                                    logger.warning(f"Skipping {symbol}: Insufficient cash for widened price (₹{widened_price:.2f}).")
                                    return False
                                try:
                                    self.kite.modify_order(variety=self.kite.VARIETY_REGULAR, order_id=order_id, price=widened_price, order_type=self.kite.ORDER_TYPE_LIMIT)
                                    logger.info(f"Widened commodity BUY limit to ₹{widened_price:.2f} (step {widen_idx+1})")
                                except Exception:
                                    try:
                                        self.kite.cancel_order(self.kite.VARIETY_REGULAR, order_id)
                                    except Exception:
                                        pass
                                    order_id = self._place_order(
                                        tradingsymbol=tradingsymbol,
                                        exchange=self.kite.EXCHANGE_MCX,
                                        transaction_type=self.kite.TRANSACTION_TYPE_BUY,
                                        quantity=quantity * lot_size,
                                        product=self.kite.PRODUCT_MIS,
                                        order_type=self.kite.ORDER_TYPE_LIMIT,
                                        price=widened_price,
                                        validity=self.kite.VALIDITY_DAY,
                                        variety=self.kite.VARIETY_REGULAR
                                    )
                                    logger.info(f"Replaced commodity BUY order with widened limit ₹{widened_price:.2f}")
                                    widen_idx += 1
                            except Exception:
                                pass

                    if not fill_confirmed:
                        logger.error(f"{Fore.RED}🔴 LIVE COMMODITY ORDER FAILED TO CONFIRM FILL after {max_retries} attempts.{Style.RESET_ALL}")
                        return False

                    trade_successful = True

                except (InputException, TokenException, PermissionException, OrderException, Exception) as e:
                    logger.error(f"{Fore.RED}🔴 LIVE COMMODITY ORDER FAILED for {symbol}: {e}{Style.RESET_ALL}")
                    return False

            else: # PAPER mode
                logger.debug(f"Executing paper commodity trade for {symbol}...")
                trade_successful = True
                final_entry_price = current_price

            # 6. POSITION TRACKING FOR COMMODITIES
            if trade_successful:
                final_trade_value = final_entry_price * lot_size * quantity

                if commodity_token and commodity_token != 0:
                    self.subscribe_to_commodity(commodity_token)

                if self._available_cash() < final_trade_value:
                    logger.error(f"Cash shortfall detected at accounting for commodity {symbol}. Required: ₹{final_trade_value:,.0f}, Available: ₹{self.paper_portfolio['cash']:,.0f}. Skipping position creation.")
                    return False
                self.paper_portfolio['margin_used'] += final_trade_value
                self._recalc_cash()

                pos_key = f"{symbol}_{direction}_COMMODITY_{datetime.now().strftime('%H%M%S')}"

                self.paper_portfolio['positions'][pos_key] = {
                    'symbol': symbol,
                    'instrument_type': 'FUT',  # Commodity futures
                    'entry_price': final_entry_price,
                    'lot_size': lot_size,
                    'quantity': quantity,
                    'entry_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    'stop_loss': trade_plan['stop_loss'],
                    'target': trade_plan['target'],
                    'trade_mode': trade_plan.get('trade_mode','COMMODITY_TREND'),
                    'expiry_days': days_to_expiry_int,
                    'current_price': final_entry_price,
                    'confidence': trade_plan['confidence'],
                    'risk_reward': trade_plan['risk_reward'],
                    'volatility': trade_plan.get('volatility', 20),
                    'direction': direction,
                    'peak_pnl_pct': 0.0,
                    'token': commodity_token,
                    'tradingsymbol': tradingsymbol,
                    'order_id': order_id,
                    'market_type': 'COMMODITY'
                }
                self.save_trade_history()

                self.send_trade_alert(trade_plan, premium=final_entry_price, lot_size=lot_size, quantity=quantity, trade_value=final_trade_value, status="COMMODITY TRADE EXECUTED")
                if self.dashboard_app:
                    self.dashboard_app.socketio.emit('commodity_trade_executed', {
                        'symbol': symbol, 'instrument_type': 'FUT',
                        'entry_price': final_entry_price, 'cost': final_trade_value
                    })

                logger.info(f"{Fore.GREEN}✅ Commodity trade executed: {symbol} @ Lots: {quantity}, Cost: ₹{final_trade_value:,.0f}{Style.RESET_ALL}")
                return True

            logger.error(f"Commodity trade execution stub returned False for {symbol}.{Style.RESET_ALL}")
            return False
        except Exception as e:
            logger.error(f"{Fore.RED}Error executing commodity trade for {symbol}: {e}{Style.RESET_ALL}")
            traceback.print_exc()
            return False

    def execute_long_straddle(self, symbol: str, entry_price: float, volatility: float) -> bool:
        try:
            bullish_plan = self.trade_plan(symbol, 'BULLISH', potential=2.0, confidence=self.min_confidence, price=entry_price, indicators={'atr_percent': 1.0, 'volatility': volatility})
            bearish_plan = self.trade_plan(symbol, 'BEARISH', potential=2.0, confidence=self.min_confidence, price=entry_price, indicators={'atr_percent': 1.0, 'volatility': volatility})
            if not bullish_plan or not bearish_plan:
                return False
            ok1 = self.execute_option_trade(bullish_plan)
            ok2 = self.execute_option_trade(bearish_plan)
            return ok1 and ok2
        except Exception:
            return False

    def execute_long_strangle(self, symbol: str, entry_price: float, volatility: float, otm_distance: float = 0.02) -> bool:
        """Execute long strangle strategy with OTM strikes"""
        try:
            # Calculate OTM strikes
            otm_call_strike = entry_price * (1 + otm_distance)
            otm_put_strike = entry_price * (1 - otm_distance)
            
            bullish_plan = self.trade_plan(symbol, 'BULLISH', potential=2.5, confidence=self.min_confidence, price=entry_price, indicators={'atr_percent': 1.0, 'volatility': volatility})
            bearish_plan = self.trade_plan(symbol, 'BEARISH', potential=2.5, confidence=self.min_confidence, price=entry_price, indicators={'atr_percent': 1.0, 'volatility': volatility})
            
            if not bullish_plan or not bearish_plan:
                return False
                
            # Modify strikes for OTM
            bullish_plan['strike_price'] = otm_call_strike
            bearish_plan['strike_price'] = otm_put_strike
            
            ok1 = self.execute_option_trade(bullish_plan)
            ok2 = self.execute_option_trade(bearish_plan)
            return ok1 and ok2
        except Exception:
            return False

    def execute_bull_call_spread(self, symbol: str, entry_price: float, volatility: float, spread_width: float = 0.02) -> bool:
        """Execute bull call spread strategy"""
        try:
            # Long call at current level, short call at higher strike
            long_call_strike = entry_price
            short_call_strike = entry_price * (1 + spread_width)
            
            bullish_plan = self.trade_plan(symbol, 'BULLISH', potential=1.5, confidence=self.min_confidence, price=entry_price, indicators={'atr_percent': 1.0, 'volatility': volatility})
            if not bullish_plan:
                return False
                
            # Execute long call
            bullish_plan['strike_price'] = long_call_strike
            ok1 = self.execute_option_trade(bullish_plan)
            
            # Execute short call (would need additional implementation for shorting)
            # For now, just execute the long side
            return ok1
        except Exception:
            return False

    def execute_iron_condor(self, symbol: str, entry_price: float, volatility: float, width: float = 0.03) -> bool:
        """Execute iron condor strategy for range-bound markets"""
        try:
            # This would require both call and put spreads
            # For now, execute a simplified version with straddle
            if volatility < 20:  # Low volatility environment
                return self.execute_long_straddle(symbol, entry_price, volatility)
            return False
        except Exception:
            return False

    def subscribe_to_option(self, option_token: int):
        """Subscribes to a single option contract."""
        if not self.kws: return

        self.open_option_tokens.add(option_token)
        if self.kws.is_connected():
            self.kws.subscribe([option_token])
            self.kws.set_mode(self.kws.MODE_LTP, [option_token])

    def subscribe_to_commodity(self, commodity_token: int):
        """Subscribes to a commodity futures contract."""
        if not self.kws: return

        self.open_option_tokens.add(commodity_token)  # Reuse the same set for commodity tokens
        if self.kws.is_connected():
            self.kws.subscribe([commodity_token])
            self.kws.set_mode(self.kws.MODE_LTP, [commodity_token])

    def subscribe_to_spot(self, spot_token: int):
        """Subscribes to the underlying spot instrument."""
        if not self.kws: return

        self.open_spot_tokens.add(spot_token)
        if self.kws.is_connected():
            self.kws.subscribe([spot_token])
            self.kws.set_mode(self.kws.MODE_LTP, [spot_token])

    def unsubscribe_from_option(self, option_token: int):
        """Removes subscription and clears cache entry."""
        if not self.kws: return

        self.open_option_tokens.discard(option_token)
        self.live_ltp_cache.pop(option_token, None)
        if self.kws.is_connected():
            self.kws.unsubscribe([option_token])

    def unsubscribe_from_spot(self, spot_token: int):
        """Removes subscription from the underlying spot instrument."""
        if not self.kws: return

        self.open_spot_tokens.discard(spot_token)
        self.live_ltp_cache.pop(spot_token, None)
        if self.kws.is_connected():
            self.kws.unsubscribe([spot_token])


    # ------------------ SCHEDULER & SCANNING - FIXED VERSION ------------------

    def assess_market_regime(self):
        """Analyzes Nifty 50 to determine if market is TRENDING or RANGING."""
        nifty_data = self.get_stock_data_batch_with_retry(['NIFTY'])

        if 'NIFTY' not in nifty_data:
            logger.warning("Nifty data missing for regime assessment. Defaulting to HIGH_CONVICTION.")
            return "TRENDING" # Defaulting to TRENDING/HIGH_CONVICTION

        df = nifty_data['NIFTY']
        if len(df) < 10:
            logger.warning("Nifty data insufficient. Defaulting to HIGH_CONVICTION.")
            return "TRENDING"

        close = df['Close']
        available_periods = min(20, len(close))
        sma_20 = close.rolling(available_periods).mean().iloc[-1]
        current_nifty_price = close.iloc[-1]

        if current_nifty_price > sma_20:
             return "TRENDING"
        else:
             return "RANGING"

    def assess_commodity_regime(self):
        symbols = self.commodity_symbols[:min(5, len(self.commodity_symbols))] if isinstance(self.commodity_symbols, list) else []
        if not symbols:
            return "TRENDING"
        data = self.get_stock_data_batch_with_retry(symbols)
        trending = 0
        total = 0
        for s in symbols:
            df = data.get(s)
            if df is None or df.empty:
                continue
            total += 1
            close = df['Close']
            n = min(20, len(close))
            sma = close.rolling(n).mean().iloc[-1]
            cur = close.iloc[-1]
            if cur > sma:
                trending += 1
        if total == 0:
            return "TRENDING"
        return "TRENDING" if trending >= max(1, total // 2) else "RANGING"

    def run_continuous_scan_job(self):
        if self.multi_strategy_mode:
            return self.run_multi_strategy_cycle()
        if self.check_max_daily_loss():
            return
        if (getattr(self, 'active_strategy_key', None) == 'ADAPTIVE_AUTO' or getattr(self, 'display_strategy_key', None) == 'ADAPTIVE_AUTO') and bool(self.config.get('commodity_trading_enabled')):
            return self.run_unified_auto_cycle()

        is_commodity_mode = bool(self.strategy_params.get('market_type') == 'COMMODITY')
        if not is_commodity_mode:
            current_regime = self.assess_market_regime()
            try:
                new_strategy_key = self.select_best_strategy_nse(current_regime)
            except Exception:
                new_strategy_key = self.REGIME_STRATEGY_MAP.get(current_regime, "HIGH_CONVICTION")
            if new_strategy_key != self.active_strategy_key:
                self.set_active_strategy(new_strategy_key)
                self.active_market_regime = current_regime
                logger.info(f"{Fore.CYAN}--- MARKET SHIFT: Switching to {new_strategy_key} (Regime: {current_regime}) ---{Style.RESET_ALL}")
        else:
            self.active_market_regime = self.assess_commodity_regime()

        scan_list = self.nse_fo_stocks or DEFAULT_NSE_UNIVERSE
        
        # COMMODITY TRADING: Add commodity symbols when using commodity strategies
        if self.strategy_params.get('market_type') == 'COMMODITY':
            scan_list = self.commodity_symbols or DEFAULT_COMMODITY_UNIVERSE
            logger.info(f"{Fore.CYAN}⚡ RUNNING COMMODITY SCAN ({len(scan_list)} symbols) - {datetime.now().strftime('%H:%M')}{Style.RESET_ALL}")
        else:
            if self.config.get('universe_mode') == 'EXPANDED_LIQUID':
                try:
                    extra = self.compute_expanded_universe()
                    scan_list = list(dict.fromkeys(self.nse_fo_stocks + extra))
                except Exception:
                    pass
            logger.info(f"{Fore.CYAN}⚡ RUNNING FULL NFO SCAN ({len(scan_list)} symbols) - {datetime.now().strftime('%H:%M')}{Style.RESET_ALL}")

        self.scan_summary['cycles_run'] += 1

        batch_size = self.scan_batch_size
        all_signals = []
        aggregate_data = {}

        market_regime = self.assess_market_regime()
        market_trend_bullish = (market_regime == "TRENDING")
        nifty_filter_bypass = self.strategy_params.get('ignore_nifty_filter', False)

        for i in range(0, len(scan_list), batch_size):
            batch_symbols = scan_list[i:i + batch_size]
            logger.info(f"Scanning batch {i//batch_size + 1}/{(len(scan_list)-1)//batch_size + 1}: {len(batch_symbols)} symbols")

            batch_data = self.get_stock_data_batch_with_retry(batch_symbols)
            aggregate_data.update(batch_data)

            for symbol, data in batch_data.items():
                if not data.empty:
                    self.scan_summary['stocks_scanned_count'] += 1

                try:
                    indicators = self.calculate_indicators(data)
                    if not indicators or indicators['current_price'] < self.min_price:
                        logger.debug(f"Skipping {symbol}: No indicators or price too low.")
                        continue

                    signals = self.detect_signals(indicators)
                    for direction, potential, confidence in signals:
                        self.scan_summary['signals_found_count'] += 1

                        if direction == 'BULLISH' and not market_trend_bullish and not nifty_filter_bypass:
                             logger.debug(f"Skipping BULLISH signal for {symbol}: Nifty trend is not bullish (Filter ON).")
                             continue

                        if direction == 'BULLISH' and not market_trend_bullish and nifty_filter_bypass:
                             logger.debug(f"Allowing BULLISH signal for {symbol}: Nifty trend is not bullish (Filter BYPASSED by strategy).")

                        trade_plan = self.trade_plan(symbol, direction, potential, confidence,
                                                     indicators['current_price'], indicators)
                        if trade_plan:
                            all_signals.append(trade_plan)
                        else:
                            try:
                                signal_id = self.get_signal_id({'symbol': symbol, 'direction': direction}, None)
                                if signal_id not in self.detected_signals_today:
                                    self.detected_signals_today.add(signal_id)
                                    pending_sig = {
                                        'symbol': symbol,
                                        'direction': direction,
                                        'entry': float(indicators.get('current_price') or 0),
                                        'target': None,
                                        'stop_loss': None,
                                        'confidence': float(confidence or 0),
                                        'risk_reward': float(self.min_rr_ratio or 0),
                                        'timestamp': datetime.now().isoformat(),
                                        'status': 'PENDING',
                                        'pending_reason': 'no_trade_plan',
                                        'source_strategy': getattr(self, 'display_strategy_key', self.active_strategy_key)
                                    }
                                    self.recent_signals.append(pending_sig)
                                    if len(self.recent_signals) > 50:
                                        self.recent_signals = self.recent_signals[-50:]
                                    try:
                                        self.trade_journal['recent_signals'] = list(self.recent_signals[-50:])
                                        save_trade_journal(self.trade_journal)
                                    except Exception:
                                        pass
                                    if self.dashboard_app:
                                        try:
                                            self.dashboard_app.socketio.emit('signal', pending_sig)
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                except Exception as e:
                    logger.debug(f"Error processing {symbol}: {e}")

            if i + batch_size < len(scan_list):
                time.sleep(1)

        self.process_scan_results(all_signals)
        self.update_option_positions(aggregate_data)
        logger.info(f"{Fore.CYAN}Scan Cycle Complete. Found {len(all_signals)} total potential signals.{Style.RESET_ALL}")

    def run_unified_auto_cycle(self):
        now = datetime.now()
        current_minutes = now.hour * 60 + now.minute
        MARKET_OPEN = MARKET_WINDOWS['NSE']['OPEN']
        MARKET_CLOSE = MARKET_WINDOWS['NSE']['CLOSE']
        CUTOFF = MARKET_WINDOWS['NSE']['CUTOFF']
        MCX_OPEN = MARKET_WINDOWS['MCX']['OPEN']
        MCX_CLOSE = MARKET_WINDOWS['MCX']['CLOSE']

        reg_nse = self.assess_market_regime()
        try:
            key_nse = self.select_best_strategy_nse(reg_nse)
        except Exception:
            key_nse = self.REGIME_STRATEGY_MAP.get(reg_nse, 'HIGH_CONVICTION')

        try:
            if MARKET_OPEN <= current_minutes <= MARKET_CLOSE and current_minutes <= CUTOFF:
                self.enabled_strategies = [key_nse]
                self.strategy_allocations = self.config.get('strategy_allocations')
                self._compute_strategy_weights()
                self.unified_active_keys = [key_nse]
                self.run_strategy_scan(key_nse)
            elif MCX_OPEN <= current_minutes <= MCX_CLOSE and current_minutes > CUTOFF:
                reg_mcx = self.assess_commodity_regime()
                try:
                    key_mcx = self.select_best_strategy_mcx(reg_mcx)
                except Exception:
                    key_mcx = 'COMMODITY_TREND' if reg_mcx == 'TRENDING' else 'COMMODITY_SCALP'
                self.enabled_strategies = [key_mcx]
                self.strategy_allocations = self.config.get('strategy_allocations')
                self._compute_strategy_weights()
                self.unified_active_keys = [key_mcx]
                self.run_strategy_scan(key_mcx)
        except Exception:
            pass

        self.display_strategy_key = 'ADAPTIVE_AUTO'

    def run_strategy_scan(self, strategy_key: str):
        if strategy_key not in self.STRATEGY_CONFIGS:
            return
        self.set_active_strategy(strategy_key)
        scan_list = self.nse_fo_stocks or DEFAULT_NSE_UNIVERSE
        if self.strategy_params.get('market_type') == 'COMMODITY':
            # Filter to symbols with known tokens and prioritize liquid contracts
            liquid_priority = ['CRUDEOIL','NATURALGAS','GOLD','SILVER','COPPER','ZINC','ALUMINIUM','LEAD','NICKEL']
            base_list = [s for s in self.commodity_symbols if self.commodity_token_map.get(s)]
            pri = [s for s in liquid_priority if s in base_list]
            rest = [s for s in base_list if s not in pri]
            scan_list = pri + rest
            logger.info(f"⚙️ Commodity scan list prepared: {len(scan_list)} symbols (tokens available)")
        else:
            if self.config.get('universe_mode') == 'EXPANDED_LIQUID':
                try:
                    extra = self.compute_expanded_universe()
                    base = self.nse_fo_stocks or DEFAULT_NSE_UNIVERSE
                    scan_list = list(dict.fromkeys(base + extra))
                except Exception:
                    pass
        self.scan_summary['cycles_run'] += 1
        batch_size = self.scan_batch_size
        all_signals = []
        aggregate_data = {}
        market_regime = self.assess_market_regime()
        market_trend_bullish = (market_regime == "TRENDING")
        nifty_filter_bypass = (self.strategy_params.get('ignore_nifty_filter', False) or (self.mode == 'PAPER' and self.strategy_params.get('market_type') != 'COMMODITY'))
        for i in range(0, len(scan_list), batch_size):
            batch_symbols = scan_list[i:i + batch_size]
            batch_data = self.get_stock_data_batch_with_retry(batch_symbols)
            aggregate_data.update(batch_data)
            for symbol, data in batch_data.items():
                if not data.empty:
                    self.scan_summary['stocks_scanned_count'] += 1
                try:
                    if data is None or len(data) < 50:
                        continue
                    indicators = self.calculate_indicators(data)
                    if not indicators or indicators['current_price'] < self.min_price:
                        logger.info(f"Skipping {symbol}: Indicators missing or price {indicators.get('current_price',0):.2f} < min {self.min_price:.2f}")
                        continue
                    rc = self.compute_regime_confidence(indicators)
                    if self.strategy_params.get('market_type') == 'COMMODITY':
                        min_rc = 0.45
                    else:
                        min_rc = 0.5 if self.mode == 'PAPER' else 0.75
                    if rc < min_rc:
                        logger.info(f"Skipping {symbol}: Regime confidence {rc:.2f} < {min_rc:.2f}")
                        continue
                    try:
                        logger.info(f"Indicators {symbol}: price {indicators.get('current_price'):.2f}, vol {indicators.get('volatility',0):.2f}, atr% {indicators.get('atr_percent',0):.2f}, rc {rc:.2f}")
                    except Exception:
                        pass
                    signals = self.detect_signals(indicators)
                    for direction, potential, confidence in signals:
                        self.scan_summary['signals_found_count'] += 1
                        if direction == 'BULLISH' and not market_trend_bullish and not nifty_filter_bypass:
                            continue
                        trade_plan = self.trade_plan(symbol, direction, potential, confidence,
                                                     indicators['current_price'], indicators)
                        if trade_plan:
                            try:
                                for pk, pos in self.paper_portfolio.get('positions', {}).items():
                                    other = pos.get('symbol')
                                    if other and other != symbol:
                                        corr = self.compute_symbol_correlation(symbol, other)
                                        if corr is not None and corr > 0.6:
                                            trade_plan['correlation_penalty'] = 0.5
                                            break
                            except Exception:
                                pass
                            trade_plan['regime_confidence'] = rc
                            all_signals.append(trade_plan)
                except Exception:
                    pass
            if i + batch_size < len(scan_list):
                time.sleep(1)
        self.process_scan_results(all_signals)
        self.update_option_positions(aggregate_data)

    def _evaluate_strategy_signals(self, strategy_key: str, symbols: List[str], max_symbols: int = 20) -> Dict:
        try:
            original_key = self.active_strategy_key
            self.set_active_strategy(strategy_key)
            arr = symbols[:max_symbols]
            batch = self.get_stock_data_batch_with_retry(arr)
            high_conf = 0
            conf_sum = 0.0
            total = 0
            for symbol, data in batch.items():
                if data is None or len(data) < 30:
                    continue
                inds = self.calculate_indicators(data)
                if not inds or float(inds.get('current_price') or 0) < self.min_price:
                    continue
                sigs = self.detect_signals(inds)
                for _, pot, conf in sigs:
                    total += 1
                    conf_sum += float(conf or 0)
                    if float(conf or 0) >= float(self.min_confidence or 0):
                        high_conf += 1
            self.set_active_strategy(original_key)
            avg_conf = (conf_sum / total) if total else 0.0
            return {'high_conf': high_conf, 'avg_conf': avg_conf, 'total': total}
        except Exception:
            return {'high_conf': 0, 'avg_conf': 0.0, 'total': 0}

    def select_best_strategy_nse(self, regime: str) -> str:
        try:
            candidates = ['HIGH_CONVICTION','MID_TREND_SWING']
            bias = []
            if regime == 'TRENDING':
                bias = ['HIGH_CONVICTION','MID_TREND_SWING']
            else:
                bias = ['MID_TREND_SWING','HIGH_CONVICTION']
            try:
                perf_ok = self.validate_strategy_performance(self.config.get('min_win_rate_threshold', 55.0), self.config.get('min_profit_factor_threshold', 1.2))
                if not perf_ok or self.paper_portfolio.get('consecutive_losses', 0) >= self.max_consecutive_losses:
                    bias = ['MID_TREND_SWING','HIGH_CONVICTION']
            except Exception:
                pass
            base_list = self.nse_fo_stocks[:max(1, min(40, len(self.nse_fo_stocks)))]
            scores = []
            for k in candidates:
                m = self._evaluate_strategy_signals(k, base_list, 20)
                w = 1.2 if k in bias else 1.0
                score = (m['high_conf'] * 2.0 + m['avg_conf']) * w
                scores.append((score, k))
            scores.sort(reverse=True)
            return scores[0][1] if scores else self.REGIME_STRATEGY_MAP.get(regime, 'HIGH_CONVICTION')
        except Exception:
            return self.REGIME_STRATEGY_MAP.get(regime, 'HIGH_CONVICTION')

    def select_best_strategy_mcx(self, regime: str) -> str:
        try:
            candidates = ['COMMODITY_TREND','COMMODITY_REVERSAL','COMMODITY_SCALP']
            bias = ['COMMODITY_TREND'] if regime == 'TRENDING' else ['COMMODITY_SCALP','COMMODITY_REVERSAL']
            liquid_priority = ['CRUDEOIL','NATURALGAS','GOLD','SILVER','COPPER','ZINC','ALUMINIUM','LEAD','NICKEL']
            base_list = [s for s in self.commodity_symbols if self.commodity_token_map.get(s)]
            pri = [s for s in liquid_priority if s in base_list]
            rest = [s for s in base_list if s not in pri]
            sample = (pri + rest)[:max(1, min(30, len(base_list)))]
            scores = []
            for k in candidates:
                m = self._evaluate_strategy_signals(k, sample, 20)
                w = 1.2 if k in bias else 1.0
                score = (m['high_conf'] * 2.0 + m['avg_conf']) * w
                scores.append((score, k))
            scores.sort(reverse=True)
            return scores[0][1] if scores else ('COMMODITY_TREND' if regime == 'TRENDING' else 'COMMODITY_SCALP')
        except Exception:
            return 'COMMODITY_TREND' if regime == 'TRENDING' else 'COMMODITY_SCALP'

    def run_multi_strategy_cycle(self):
        original_key = self.active_strategy_key
        keys = [k for k in self.enabled_strategies if k in self.STRATEGY_CONFIGS]
        for key in keys:
            self.run_strategy_scan(key)
            time.sleep(1)
        self.set_active_strategy(original_key)

    def log_final_summary(self):
        """Displays a comprehensive summary of the bot's activity since startup."""
        end_time = datetime.now()
        duration = end_time - self.scan_summary['start_time']

        current_portfolio_value = self.paper_portfolio['total_value']
        initial_capital = self.paper_portfolio['initial_capital']
        total_pnl = current_portfolio_value - initial_capital

        total_cycles = self.scan_summary['cycles_run']
        total_unique_stocks = len(self.nse_fo_stocks)

        if total_cycles > 0:
            scanned_per_cycle = self.scan_summary['stocks_scanned_count'] / total_cycles
        else:
            scanned_per_cycle = 0

        # Performance metrics from trade journal
        metrics = self.trade_journal['performance_metrics']
        win_rate = metrics.get('win_rate', 0)
        total_trades = metrics.get('total_trades', 0)

        summary_message = f"""
{Fore.CYAN}╔══════════════════════════════════════════════════════════╗{Style.RESET_ALL}
{Fore.CYAN}║             TRADING SESSION SUMMARY (NFO SCAN)           ║{Style.RESET_ALL}
{Fore.CYAN}╚══════════════════════════════════════════════════════════╝{Style.RESET_ALL}
{Fore.YELLOW}SESSION DURATION:{Style.RESET_ALL} {str(duration).split('.')[0]}

{Fore.GREEN}SCANNING ACTIVITY:{Style.RESET_ALL}
▶ Total Scan Cycles Run: {total_cycles}
▶ NFO Symbols in Universe: {total_unique_stocks}
▶ Stocks Scanned (Success Rate): {self.scan_summary['stocks_scanned_count']} (Avg {scanned_per_cycle:.0f} per cycle)
▶ Total API Historical Calls: {self.scan_summary['total_api_calls']}

{Fore.MAGENTA}SIGNAL & TRADE ACTIVITY:{Style.RESET_ALL}
▶ Total Signals Generated: {self.scan_summary['signals_found_count']}
▶ Total Trades Executed: {self.scan_summary['trades_executed_count']}
▶ Total Closed Trades: {total_trades}
▶ Win Rate: {win_rate:.1f}%

{Fore.YELLOW}PORTFOLIO PERFORMANCE ({self.mode}):{Style.RESET_ALL}
💰 Initial Capital: ₹{initial_capital:,.2f}
💰 Final Portfolio Value: ₹{current_portfolio_value:,.2f}
📈 Net P&L: ₹{total_pnl:,.2f} ({total_pnl/initial_capital*100:.2f}%)

{Fore.CYAN}SYSTEM HEALTH:{Style.RESET_ALL}
▶ Health Status: {self.health_status}
▶ API Failures: {self.api_failures}
▶ Consecutive Losses: {self.paper_portfolio['consecutive_losses']}
"""
        print(summary_message)

    def process_scan_results(self, all_signals: List[Dict]):
        if datetime.now().date() != self.today_date:
            self.detected_signals_today.clear()
            self.today_date = datetime.now().date()
            logger.info("Daily signal memory reset.")

        # Track signals per side for throttling
        signals_per_side = {'BULLISH': 0, 'BEARISH': 0, 'VOLATILITY': 0}
        max_signals = self.config.get('max_signals_per_side', 8)
        
        # Check daily profit target
        daily_pnl = self.calculate_daily_pnl()
        target_profit = self.config.get('target_profit_pct', 15.0)
        if daily_pnl >= target_profit:
            logger.info(f"Daily profit target reached: {daily_pnl:.2f}% >= {target_profit:.2f}%. Stopping new entries.")
            return

        for trade_plan in all_signals:
            try:
                is_commodity_mode = bool(self.strategy_params.get('market_type') == 'COMMODITY')
                if not is_commodity_mode:
                    vix_quote = self.safe_quote("NSE:INDIA VIX") if self.kite else None
                    vix_val = vix_quote.get("NSE:INDIA VIX", {}).get('last_price') if vix_quote else None
                    vix_limit = float(self.config.get('max_vix', 20.0))
                    if bool(self.config.get('high_precision_mode')):
                        vix_limit = float(self.config.get('precision_max_vix', vix_limit))
                    if vix_val and vix_val > vix_limit:
                        logger.info(f"Skipping {trade_plan['symbol']}: VIX {vix_val:.2f} > {vix_limit:.2f}")
                        continue
            except Exception:
                pass
            if bool(self.strategy_params.get('market_type') == 'COMMODITY'):
                strike, premium, lot_size = self.get_commodity_metrics_paper(
                    trade_plan['symbol'], trade_plan['direction'], trade_plan['entry'], trade_plan['volatility']
                )
            else:
                strike, premium, lot_size = self.get_option_metrics_paper(
                    trade_plan['symbol'], trade_plan['direction'], trade_plan['entry'], trade_plan['volatility']
                )

            signal_id = self.get_signal_id(trade_plan, strike)

            is_open = any(pos['symbol'] == trade_plan['symbol'] for pos in self.paper_portfolio['positions'].values())

            logger.debug(f"Processing signal: {signal_id}. Is Open: {is_open}. Confidence: {trade_plan['confidence']:.1f}")

            if signal_id in self.detected_signals_today:
                if is_open:
                    logger.debug(f"Skipping {signal_id}: Already processed signal and position is open.")
                    continue

            self.detected_signals_today.add(signal_id)

            # Defer alert emission until after validation and execution gating
            if self.dashboard_app:
                self.dashboard_app.socketio.emit('signal', {
                    'symbol': trade_plan['symbol'],
                    'direction': trade_plan['direction'],
                    'strike': strike,
                    'premium': premium,
                    'entry': trade_plan.get('entry'),
                    'target': trade_plan.get('target'),
                    'stop_loss': trade_plan.get('stop_loss'),
                    'confidence': trade_plan['confidence'],
                    'risk_reward': trade_plan['risk_reward'],
                    'timestamp': datetime.now().isoformat(),
                    'status': 'PENDING'
                })
            try:
                sig_entry = {
                    'symbol': trade_plan['symbol'],
                    'direction': trade_plan['direction'],
                    'strike': strike,
                    'premium': premium,
                    'entry': trade_plan.get('entry'),
                    'target': trade_plan.get('target'),
                    'stop_loss': trade_plan.get('stop_loss'),
                    'confidence': trade_plan['confidence'],
                    'risk_reward': trade_plan['risk_reward'],
                    'timestamp': datetime.now().isoformat(),
                    'status': 'PENDING',
                    'pending_reason': None,
                    'source_strategy': getattr(self, 'display_strategy_key', self.active_strategy_key)
                }
                self.recent_signals.append(sig_entry)
                if len(self.recent_signals) > 50:
                    self.recent_signals = self.recent_signals[-50:]
                try:
                    self.trade_journal['recent_signals'] = list(self.recent_signals[-50:])
                    save_trade_journal(self.trade_journal)
                except Exception:
                    pass
            except Exception:
                pass

            # Adaptive dynamic gating for win-rate improvement
            try:
                is_adaptive = (getattr(self, 'display_strategy_key', self.active_strategy_key) == 'ADAPTIVE_AUTO')
            except Exception:
                is_adaptive = False
            if is_adaptive:
                try:
                    min_wr = float(self.config.get('min_win_rate_threshold', 55.0))
                    min_pf = float(self.config.get('min_profit_factor_threshold', 1.2))
                    if bool(self.config.get('high_precision_mode')):
                        min_wr = float(self.config.get('precision_min_win_rate', min_wr))
                        min_pf = float(self.config.get('precision_min_profit_factor', min_pf))
                    perf_ok = self.validate_strategy_performance(min_win_rate=min_wr, min_profit_factor=min_pf)
                except Exception:
                    perf_ok = True
                # Cooldown after recent loss
                recent_loss_cooldown = False
                try:
                    ct = self.trade_journal.get('closed_trades', [])[-1:] or []
                    if ct:
                        last = ct[0]
                        if float(last.get('pnl', 0) or 0) < 0:
                            et = last.get('exit_time')
                            if et:
                                dt = datetime.fromisoformat(et)
                                cd_mins = int(self.config.get('precision_cooldown_minutes', 20)) if bool(self.config.get('high_precision_mode')) else 20
                                recent_loss_cooldown = (datetime.now() - dt).total_seconds() < cd_mins * 60
                            else:
                                recent_loss_cooldown = True
                except Exception:
                    recent_loss_cooldown = False
                # Enforce stricter entry thresholds when performance is poor or in cooldown
                base_conf = float(self.min_confidence or 0)
                base_rr = float(self.min_rr_ratio or 0)
                strict_conf = base_conf + 0.5
                strict_rr = base_rr + 0.3
                if bool(self.config.get('high_precision_mode')):
                    strict_conf = max(strict_conf, float(self.config.get('precision_min_confidence', strict_conf)))
                    strict_rr = max(strict_rr, float(self.config.get('precision_min_rr_ratio', strict_rr)))
                if (not perf_ok) or recent_loss_cooldown or (self.paper_portfolio.get('consecutive_losses', 0) >= self.max_consecutive_losses):
                    if (trade_plan.get('confidence', 0) < strict_conf) or (trade_plan.get('risk_reward', 0) < strict_rr):
                        try:
                            sig_entry['pending_reason'] = 'adaptive_strict_gating'
                            sig_entry['pending_reason_detail'] = {
                                'confidence': float(trade_plan.get('confidence', 0)),
                                'required_confidence': strict_conf,
                                'risk_reward': float(trade_plan.get('risk_reward', 0)),
                                'required_rr': strict_rr,
                                'cooldown': recent_loss_cooldown
                            }
                        except Exception:
                            pass
                        continue

            # Performance gating
            min_wr = float(self.config.get('min_win_rate_threshold', 55.0))
            min_pf = float(self.config.get('min_profit_factor_threshold', 1.2))
            if bool(self.config.get('high_precision_mode')):
                min_wr = float(self.config.get('precision_min_win_rate', min_wr))
                min_pf = float(self.config.get('precision_min_profit_factor', min_pf))
            valid_perf = self.validate_strategy_performance(min_win_rate=min_wr, min_profit_factor=min_pf)
            if not valid_perf:
                logger.warning("Validation thresholds not met. Skipping new entries this cycle.")
                try:
                    sig_entry['pending_reason'] = 'performance_thresholds'
                    sig_entry['pending_reason_detail'] = {
                        'win_rate_min': self.config.get('min_win_rate_threshold', 55.0),
                        'profit_factor_min': self.config.get('min_profit_factor_threshold', 1.2)
                    }
                except Exception:
                    pass
                continue
            # Threshold derivation
            eff_conf = float(self.min_confidence or 0)
            if bool(self.config.get('high_precision_mode')):
                eff_conf = max(eff_conf, float(self.config.get('precision_min_confidence', eff_conf)))
            eff_rr = float(self.min_rr_ratio or 0)
            if bool(self.config.get('high_precision_mode')):
                eff_rr = max(eff_rr, float(self.config.get('precision_min_rr_ratio', eff_rr)))
                logger.debug(f"Precision mode ON: effective thresholds conf={eff_conf}, rr={eff_rr}")
            # Risk-reward gating
            if trade_plan.get('risk_reward', 0) < eff_rr:
                logger.info(f"Skipping {trade_plan['symbol']}: R:R {trade_plan.get('risk_reward',0):.2f} < {eff_rr:.2f}")
                try:
                    sig_entry['pending_reason'] = 'risk_reward_below_min'
                    sig_entry['pending_reason_detail'] = {
                        'risk_reward': float(trade_plan.get('risk_reward', 0)),
                        'min_rr_ratio': float(eff_rr or 0),
                        'precision_mode': bool(self.config.get('high_precision_mode'))
                    }
                except Exception:
                    pass
                continue
            # Correlation gating to avoid stacking correlated exposure
            try:
                correlated_block = False
                open_positions = list(self.paper_portfolio.get('positions', {}).values())
                for pos in open_positions:
                    other = pos.get('symbol')
                    if other and other != trade_plan['symbol']:
                        corr = self.compute_symbol_correlation(trade_plan['symbol'], other)
                        corr_threshold = float(self.config.get('precision_max_correlation', 0.7)) if bool(self.config.get('high_precision_mode')) else 0.7
                        if corr is not None and corr >= corr_threshold:
                            correlated_block = True
                            break
                if correlated_block:
                    logger.info(f"Skipping {trade_plan['symbol']}: Correlated exposure detected (>= {corr_threshold:.2f})")
                    try:
                        sig_entry['pending_reason'] = 'correlated_exposure'
                        sig_entry['pending_reason_detail'] = {
                            'threshold': corr_threshold
                        }
                    except Exception:
                        pass
                    continue
            except Exception:
                pass
            if WinRateOptimizer and (not is_commodity_mode):
                try:
                    ok, checks = WinRateOptimizer.validate_signal({
                        'confidence': float(trade_plan.get('confidence', 0)),
                        'reward_risk_ratio': float(trade_plan.get('risk_reward', 0)),
                        'bid_ask_spread': float(trade_plan.get('bid_ask_spread', 5))
                    })
                except Exception:
                    ok, checks = True, {}
                if not ok:
                    try:
                        sig_entry['pending_reason'] = 'quality_gates'
                        sig_entry['pending_reason_detail'] = checks
                    except Exception:
                        pass
                    continue
            # Liquidity gating for option premium
            if trade_plan.get('entry', 0) < 10:
                logger.info(f"Skipping {trade_plan['symbol']}: Underlying entry {trade_plan.get('entry',0):.2f} too low for reliable premium.")
                try:
                    sig_entry['pending_reason'] = 'underlying_price_low'
                    sig_entry['pending_reason_detail'] = {
                        'entry': float(trade_plan.get('entry', 0))
                    }
                except Exception:
                    pass
                continue
            if not is_commodity_mode and datetime.now().hour >= 15:
                logger.info(f"Skipping {trade_plan['symbol']}: Late entry window (>= 15:00)")
                try:
                    sig_entry['pending_reason'] = 'late_entry_window'
                    sig_entry['pending_reason_detail'] = {
                        'hour': datetime.now().hour,
                        'window': 'NSE intraday cutoff >= 15:00'
                    }
                except Exception:
                    pass
                continue
            
            # Signal throttling per side
            direction = trade_plan['direction']
            if 'VOLATILITY' in direction:
                signals_per_side['VOLATILITY'] += 1
                max_side = int(self.config.get('precision_max_signals_per_side', max_signals)) if bool(self.config.get('high_precision_mode')) else max_signals
                if signals_per_side['VOLATILITY'] > max_side:
                    logger.info(f"Skipping {trade_plan['symbol']}: Max volatility signals ({max_signals}) reached.")
                    try:
                        sig_entry['pending_reason'] = 'max_signals_reached_volatility'
                        sig_entry['pending_reason_detail'] = {
                            'side_count': signals_per_side['VOLATILITY'],
                            'max_signals': max_side
                        }
                    except Exception:
                        pass
                    continue
            elif direction in ['BULLISH', 'BEARISH']:
                signals_per_side[direction] += 1
                max_side = int(self.config.get('precision_max_signals_per_side', max_signals)) if bool(self.config.get('high_precision_mode')) else max_signals
                if signals_per_side[direction] > max_side:
                    logger.info(f"Skipping {trade_plan['symbol']}: Max {direction} signals ({max_signals}) reached.")
                    try:
                        sig_entry['pending_reason'] = 'max_signals_reached_' + direction.lower()
                        sig_entry['pending_reason_detail'] = {
                            'side': direction.lower(),
                            'side_count': signals_per_side[direction],
                            'max_signals': max_side
                        }
                    except Exception:
                        pass
                    continue
            
            # Enhanced execution with strategy selection
            if trade_plan['confidence'] >= eff_conf and not is_open:
                self.send_trade_alert(trade_plan, premium=premium, strike=strike, lot_size=lot_size, status="NEW SIGNAL")
                logger.info(f"{Fore.MAGENTA}Execution Triggered for {trade_plan['symbol']}. Conf >= {eff_conf:.1f} and Position is Closed.{Style.RESET_ALL}")
                
                # Strategy-specific execution
                direction = trade_plan['direction']
                entry_price = trade_plan['entry']
                volatility = trade_plan.get('volatility', 20)
                
                is_commodity_mode = bool(self.strategy_params.get('market_type') == 'COMMODITY')
                if is_commodity_mode:
                    if direction in ('IRON_CONDOR','VOLATILITY_STRADDLE','VOLATILITY_STRANGLE'):
                        try:
                            sig_entry['pending_reason'] = 'strategy_not_applicable'
                        except Exception:
                            pass
                        continue
                    success = self.execute_commodity_trade(trade_plan)
                    if success:
                        logger.info(f"{Fore.GREEN}Successfully executed commodity {direction} strategy for {trade_plan['symbol']}{Style.RESET_ALL}")
                    else:
                        logger.error(f"{Fore.RED}Failed to execute commodity {direction} strategy for {trade_plan['symbol']}{Style.RESET_ALL}")
                        try:
                            sig_entry['pending_reason'] = 'execution_failed'
                        except Exception:
                            pass
                elif direction == 'VOLATILITY_STRADDLE':
                    success = self.execute_long_straddle(trade_plan['symbol'], entry_price, volatility)
                    if not success:
                        try:
                            sig_entry['pending_reason'] = 'execution_failed'
                        except Exception:
                            pass
                elif direction == 'VOLATILITY_STRANGLE':
                    success = self.execute_long_strangle(trade_plan['symbol'], entry_price, volatility)
                    if not success:
                        try:
                            sig_entry['pending_reason'] = 'execution_failed'
                        except Exception:
                            pass
                elif direction == 'IRON_CONDOR':
                    success = self.execute_iron_condor(trade_plan['symbol'], entry_price, volatility)
                    if not success:
                        try:
                            sig_entry['pending_reason'] = 'execution_failed'
                        except Exception:
                            pass
                else:
                    # Standard directional trade
                    success = self.execute_option_trade(trade_plan)
                    if not success:
                        try:
                            sig_entry['pending_reason'] = 'execution_failed'
                        except Exception:
                            pass
                
                if success and self.strategy_params.get('market_type') != 'COMMODITY':
                    logger.info(f"{Fore.GREEN}Successfully executed {direction} strategy for {trade_plan['symbol']}{Style.RESET_ALL}")
                elif not success and self.strategy_params.get('market_type') != 'COMMODITY':
                    logger.error(f"{Fore.RED}Failed to execute {direction} strategy for {trade_plan['symbol']}{Style.RESET_ALL}")
                    try:
                        sig_entry['pending_reason'] = 'execution_failed'
                    except Exception:
                        pass
                    
            elif is_open:
                logger.debug(f"Not executing {trade_plan['symbol']}: Position already open.")
                try:
                    sig_entry['pending_reason'] = 'position_already_open'
                    sig_entry['pending_reason_detail'] = {
                        'symbol': trade_plan['symbol']
                    }
                except Exception:
                    pass
            else:
                logger.info(f"Not executing {trade_plan['symbol']}: Confidence {trade_plan['confidence']:.1f} < {eff_conf:.1f}")
                try:
                    sig_entry['pending_reason'] = 'confidence_below_min'
                    sig_entry['pending_reason_detail'] = {
                        'confidence': float(trade_plan['confidence']),
                        'min_confidence': float(eff_conf or 0),
                        'precision_mode': bool(self.config.get('high_precision_mode'))
                    }
                except Exception:
                    pass

        try:
            if self.dashboard_app:
                snapshot = self.package_dashboard_data()
                self.dashboard_app.socketio.emit('dashboard_update', snapshot)
        except Exception:
            pass

    def continuous_scan_loop(self):
        MARKET_OPEN = MARKET_WINDOWS['NSE']['OPEN']
        MARKET_CLOSE = MARKET_WINDOWS['NSE']['CLOSE']
        MCX_OPEN = MARKET_WINDOWS['MCX']['OPEN']
        MCX_CLOSE = MARKET_WINDOWS['MCX']['CLOSE']

        while self.scheduler_running:
            use_ist = (os.getenv('USE_IST', '1') == '1')
            now = (datetime.utcnow() + timedelta(hours=5, minutes=30)) if use_ist else datetime.now()
            current_minutes = now.hour * 60 + now.minute

            is_commodity = bool(self.strategy_params.get('market_type') == 'COMMODITY')
            is_auto_unified = ((getattr(self, 'active_strategy_key', None) == 'ADAPTIVE_AUTO') or (getattr(self, 'display_strategy_key', None) == 'ADAPTIVE_AUTO'))
            allow_mcx_anytime = (os.getenv('RUN_MCX_ANYTIME', '0') == '1')
            mcx_open_now = (MCX_OPEN <= current_minutes <= MCX_CLOSE) or allow_mcx_anytime
            try:
                if not is_auto_unified:
                    if mcx_open_now:
                        base_key = getattr(self, 'base_strategy_key', None)
                        if base_key is None and (self.active_strategy_key not in {"COMMODITY_TREND","COMMODITY_REVERSAL","COMMODITY_SCALP"}):
                            self.base_strategy_key = self.active_strategy_key
                        if self.active_strategy_key in {"COMMODITY_TREND","COMMODITY_REVERSAL","COMMODITY_SCALP"}:
                            pass
                        else:
                            key_map = {
                                "HIGH_CONVICTION": "COMMODITY_TREND",
                                "AGGRESSIVE_REVERSAL": "COMMODITY_REVERSAL",
                                "MID_TREND_SWING": "COMMODITY_TREND",
                                "AGGRESSIVE_SCALP": "COMMODITY_SCALP"
                            }
                            target = key_map.get(self.active_strategy_key, "COMMODITY_TREND")
                            self.set_active_strategy(target)
                            try:
                                base = getattr(self, 'base_strategy_key', self.active_strategy_key)
                                self.display_strategy_key = f"{base}_MCX"
                            except Exception:
                                self.display_strategy_key = target
                        try:
                            if bool(self.strategy_params.get('market_type') == 'COMMODITY'):
                                self.SCAN_INTERVAL_MINUTES = max(3, min(self.SCAN_INTERVAL_MINUTES, 5))
                        except Exception:
                            pass
                    else:
                        if self.active_strategy_key in {"COMMODITY_TREND","COMMODITY_REVERSAL","COMMODITY_SCALP"}:
                            rev_map = {
                                "COMMODITY_TREND": "HIGH_CONVICTION",
                                "COMMODITY_REVERSAL": "AGGRESSIVE_REVERSAL",
                                "COMMODITY_SCALP": "AGGRESSIVE_SCALP"
                            }
                            base = getattr(self, 'base_strategy_key', None)
                            target = base or rev_map.get(self.active_strategy_key, "HIGH_CONVICTION")
                            self.set_active_strategy(target)
                            try:
                                self.display_strategy_key = f"{target}_NSE"
                            except Exception:
                                self.display_strategy_key = target
                        try:
                            self.SCAN_INTERVAL_MINUTES = max(7, self.SCAN_INTERVAL_MINUTES)
                        except Exception:
                            pass
            except Exception:
                pass
            is_commodity = bool(self.strategy_params.get('market_type') == 'COMMODITY')
            in_window = ((MARKET_OPEN <= current_minutes <= MARKET_CLOSE) or mcx_open_now) if is_auto_unified else (mcx_open_now if is_commodity else (MARKET_OPEN <= current_minutes <= MARKET_CLOSE))
            if in_window:
                time_since_last_scan = (now - self.last_scan_time).total_seconds()

                if time_since_last_scan >= self.SCAN_INTERVAL_MINUTES * 60:
                    self.last_scan_time = now
                    self.run_continuous_scan_job()

                sleep_time = min(30, self.SCAN_INTERVAL_MINUTES * 60 - time_since_last_scan + 1)
                time.sleep(max(1, sleep_time))
            else:
                if (current_minutes > MARKET_CLOSE) and (self.today_date == now.date()):
                    self.update_option_positions({})
                    if not getattr(self, 'backtest_run_today', False):
                        try:
                            self.run_offhours_backtest(days=3, max_symbols=20)
                            self.backtest_run_today = True
                        except Exception as e:
                            logger.error(f"Backtest failed: {e}")

                self.detected_signals_today.clear()
                if self.today_date != now.date():
                    self.backtest_run_today = False
                self.today_date = now.date()
                logger.info("Market closed. Sleeping...")
                time.sleep(300)

    def setup_automatic_schedule(self):
        schedule.clear()
        schedule.every().day.at("15:15").do(self.auto_close_intraday_positions).tag('trading', 'close')
        logger.info("✅ Automatic utility schedule setup complete.")
        try:
            schedule.every().day.at("23:50").do(self.auto_close_commodity_positions).tag('trading', 'close_commodity')
        except Exception:
            pass

    def start_automatic_scheduler(self):
        if self.scheduler_running:
            logger.warning("Scheduler already running.")
            return
        self.setup_automatic_schedule()
        self.scheduler_running = True
        logger.info(f"{Fore.GREEN}🚀 Starting continuous scanner and utility threads.{Style.RESET_ALL}")

        threading.Thread(target=self.continuous_scan_loop, daemon=True).start()

        def run_scheduler():
            while self.scheduler_running:
                schedule.run_pending()
                time.sleep(1)
        threading.Thread(target=run_scheduler, daemon=True).start()

        self.start_health_monitor()
        self.send_telegram_alert(f"🤖 <b>CONTINUOUS {self.mode} TRADER STARTED</b>")

    def reload_state_from_disk(self):
        try:
            self.paper_portfolio = load_portfolio()
        except Exception:
            pass
        try:
            self.trade_journal = load_trade_journal()
            try:
                self.recent_signals = (self.trade_journal.get('recent_signals') or [])[-50:]
                try:
                    # Sanitize pending reasons for recent signals loaded from disk
                    open_symbols = set([p.get('symbol') for p in (self.paper_portfolio.get('positions', {}) or {}).values() if p.get('symbol')])
                    const_min_conf = float(getattr(self, 'min_confidence', 0) or 0)
                    const_min_rr = float(getattr(self, 'min_rr_ratio', 0) or 0)
                    updated = []
                    for s in self.recent_signals:
                        s2 = dict(s)
                        if (s2.get('status') == 'PENDING') and (not s2.get('pending_reason') or str(s2.get('pending_reason')).lower() == 'unknown'):
                            try:
                                conf = float(s2.get('confidence') or 0)
                            except Exception:
                                conf = 0.0
                            try:
                                rr = float(s2.get('risk_reward') or 0)
                            except Exception:
                                rr = 0.0
                            if s2.get('symbol') in open_symbols:
                                s2['pending_reason'] = 'position_already_open'
                            elif conf < const_min_conf:
                                s2['pending_reason'] = 'confidence_below_min'
                            elif rr < const_min_rr:
                                s2['pending_reason'] = 'risk_reward_below_min'
                            else:
                                s2['pending_reason'] = 'awaiting_execution'
                                s2['pending_reason_detail'] = {
                                    'confidence': conf,
                                    'min_confidence': const_min_conf,
                                    'risk_reward': rr,
                                    'min_rr_ratio': const_min_rr,
                                    'position_open': bool(s2.get('symbol') in open_symbols)
                                }
                        updated.append(s2)
                    self.recent_signals = updated[-50:]
                    try:
                        self.trade_journal['recent_signals'] = list(self.recent_signals)
                        save_trade_journal(self.trade_journal)
                    except Exception:
                        pass
                except Exception:
                    pass
            except Exception:
                self.recent_signals = []
        except Exception:
            pass
        try:
            if self.dashboard_app:
                self.dashboard_app.socketio.emit('dashboard_update', self.package_dashboard_data())
        except Exception:
            pass
        return True

    def reset_for_new_day(self):
        try:
            initial = float(self.paper_portfolio.get('initial_capital', 0) or 0)
            self.paper_portfolio['positions'] = {}
            self.paper_portfolio['cash'] = initial
            self.paper_portfolio['total_value'] = initial
            self.paper_portfolio['margin_used'] = 0.0
            self.paper_portfolio['consecutive_losses'] = 0
            self.paper_portfolio['peak_portfolio_value'] = initial
            save_portfolio(self.paper_portfolio)
        except Exception:
            pass
        try:
            self.trade_journal = load_trade_journal()
            self.trade_journal['closed_trades'] = []
            self.trade_journal['daily_pnl'] = {}
            self.trade_journal['performance_metrics'] = {
                'win_rate': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'largest_win': 0,
                'largest_loss': 0,
                'total_trades': 0,
                'profitable_trades': 0,
                'profit_factor': 0,
                'daily_pnl': 0
            }
            save_trade_journal(self.trade_journal)
        except Exception:
            pass
        try:
            self.detected_signals_today.clear()
        except Exception:
            pass
        try:
            self.recent_signals = self.recent_signals[-50:]
        except Exception:
            pass
        return True

    def run_offhours_backtest(self, days: int = 3, max_symbols: int = 20):
        symbols = self.nse_fo_stocks[:max_symbols]
        now = datetime.now()
        from_date = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        to_date = now.strftime("%Y-%m-%d %H:%M:%S")
        results = []
        for symbol in symbols:
            token = self.token_map.get(symbol)
            if not token:
                continue
            try:
                history = self.kite.historical_data(token, from_date, to_date, '5minute', continuous=False)
                df = pd.DataFrame(history)
                if df.empty:
                    continue
                df.set_index('date', inplace=True)
                df.index = pd.to_datetime(df.index)
                df.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close','volume':'Volume'}, inplace=True)
                wins = 0
                losses = 0
                signals_count = 0
                for i in range(20, len(df)):
                    window = df.iloc[i-20:i]
                    inds = self.calculate_indicators(window)
                    if not inds:
                        continue
                    sigs = self.detect_signals(inds)
                    for direction, potential, confidence in sigs:
                        signals_count += 1
                        entry = window['Close'].iloc[-1]
                        stop_pct = max(inds['atr_percent'] * 1.5, 0.5)
                        future = df.iloc[i:min(i+12, len(df))]['Close']
                        if direction == 'BULLISH':
                            target = entry * (1 + potential/100)
                            stop = entry * (1 - stop_pct/100)
                            hit_target = any(f >= target for f in future)
                            hit_stop = any(f <= stop for f in future)
                        else:
                            target = entry * (1 - potential/100)
                            stop = entry * (1 + stop_pct/100)
                            hit_target = any(f <= target for f in future)
                            hit_stop = any(f >= stop for f in future)
                        if hit_target and not hit_stop:
                            wins += 1
                        elif hit_stop and not hit_target:
                            losses += 1
                if signals_count > 0:
                    results.append({
                        'symbol': symbol,
                        'signals': signals_count,
                        'wins': wins,
                        'losses': losses,
                        'win_rate': (wins/(wins+losses))*100 if (wins+losses)>0 else 0
                    })
            except Exception as e:
                logger.error(f"Backtest error for {symbol}: {e}")
                continue
        summary = {
            'timestamp': datetime.now().isoformat(),
            'symbols_tested': len(results),
            'avg_win_rate': np.mean([r['win_rate'] for r in results]) if results else 0,
            'total_signals': sum([r['signals'] for r in results]) if results else 0
        }
        self.trade_journal['backtest_summary'] = {
            'summary': summary,
            'details': results
        }
        save_trade_journal(self.trade_journal)
        logger.info(f"Backtest complete: {summary}")

    def stop_automatic_scheduler(self):
        self.scheduler_running = False
        schedule.clear()
        if self.kws and self.kws.is_connected():
            self.kws.stop()
        logger.info("🛑 Automatic scheduler stopped.")
        if self.telegram_enabled:
            self.send_telegram_alert("🛑 <b>AUTOMATIC SCHEDULER STOPPED</b>")

    # ------------------ POSITION MONITORING & CLOSURE (TSL) ------------------

    def update_option_positions(self, spot_data_batch: Dict):
        positions_to_close = []
        est_val_sum = 0.0

        for position_key, position in list(self.paper_portfolio['positions'].items()):
            try:
                symbol = position['symbol']
                current_spot = position.get('current_spot') # Get spot from WebSocket cache/position data
                stock_data = None
                volatility_pct_val = 0.0

                # If spot is still None (e.g., first run, or token not connected), try historical data for baseline indicator calc
                if current_spot is None:
                    stock_data = spot_data_batch.get(symbol)
                    if stock_data is not None and not stock_data.empty:
                        current_spot = stock_data['Close'].iloc[-1]
                        position['current_spot'] = current_spot # Update position cache

                    if stock_data is not None and not stock_data.empty and len(stock_data) >= 10:
                        indicators = self.calculate_indicators(stock_data)
                        volatility_pct_val = indicators.get('volatility', 20.0) / 100
                else:
                    # If we have live spot data, check if we have volatility data in cache/indicators
                    # This section relies on historical fetch only for initial indicator baseline
                    pass


                if current_spot is None:
                    logger.warning(f"Skipping position update for {symbol}: Could not fetch spot data/WebSocket LTP missing.")
                    continue

                option_token = position.get('token')
                if option_token and option_token in self.live_ltp_cache:
                    current_premium = self.live_ltp_cache[option_token]
                else:
                    tradingsymbol = position.get('tradingsymbol')
                    current_premium = None
                    exch = 'MCX' if position.get('market_type') == 'COMMODITY' or position.get('instrument_type') == 'FUT' else 'NFO'
                    quote_key_full = f"{exch}:{tradingsymbol}" if tradingsymbol else None
                    if self.kite and tradingsymbol:
                        try:
                            live_quote = self.safe_quote(quote_key_full)
                            current_premium = (live_quote.get(quote_key_full, {}) or {}).get('last_price')
                        except Exception as e:
                            logger.error(f"Quote fetch failed for {tradingsymbol}: {e}")
                    if not current_premium and quote_key_full:
                        try:
                            snap = self.last_ltp_snapshot.get(quote_key_full, {})
                            lp = snap.get('last_price') or snap.get('close')
                            if lp is not None and lp > 0:
                                current_premium = float(lp)
                        except Exception:
                            pass
                    if not current_premium:
                        current_premium = float(position.get('current_premium', position.get('premium_paid', position.get('entry_price', 0)) ) or 0)

                TSL_ACTIVATION_PCT = float(self.tsl_activation_pct)
                TSL_PULLBACK_PCT = float(self.tsl_pullback_pct)
                QUICK_MAX_PROFIT_PCT = float(self.max_profit_pct)

                entry_value = (position.get('premium_paid', position.get('entry_price', 0)) or 0) * (position.get('lot_size', 0) or 0) * (position.get('quantity', 0) or 0)
                current_value = current_premium * position['lot_size'] * position['quantity']
                pnl_abs = current_value - entry_value
                pnl_pct = (pnl_abs / entry_value) * 100 if entry_value else 0
                est_val_sum += current_value

                position['exit_signal_reason'] = None

                is_call = position.get('option_type') == 'CALL'
                stop_loss = position.get('stop_loss', 0)
                target = position.get('target', float('inf'))

                exit_reason = None

                is_commodity_pos = bool(position.get('market_type') == 'COMMODITY' or position.get('instrument_type') == 'FUT')
                direction_pos = str(position.get('direction', 'BULLISH')).upper()

                if is_commodity_pos and current_spot is None:
                    current_spot = current_premium
                # Spot SL/Target check
                if is_commodity_pos:
                    if direction_pos == 'BULLISH':
                        if current_spot >= target:
                            exit_reason = "TARGET HIT (SPOT)"
                        elif current_spot <= stop_loss:
                            exit_reason = "STOP LOSS"
                    else:
                        if current_spot <= target:
                            exit_reason = "TARGET HIT (SPOT)"
                        elif current_spot >= stop_loss:
                            exit_reason = "STOP LOSS"
                else:
                    if (is_call and current_spot >= target) or (not is_call and current_spot <= target):
                        exit_reason = "TARGET HIT (SPOT)"
                    elif (is_call and current_spot <= stop_loss) or (not is_call and current_spot >= stop_loss):
                        exit_reason = "STOP LOSS"

                # Hard P&L stop based on premium P&L
                if (self.exit_policy in ('hybrid', 'per_position_only')) and (not exit_reason):
                    try:
                        sl_pct = float(self.config.get('stop_loss_pct', 10.0))
                        if pnl_pct <= -sl_pct:
                            exit_reason = f"PNL STOP LOSS ({abs(pnl_pct):.1f}%)"
                    except Exception:
                        pass

                if pnl_abs < 0:
                    self.paper_portfolio['consecutive_losses'] += 1
                else:
                    self.paper_portfolio['consecutive_losses'] = 0

                # 1b. ATR-based adverse move exit
                atr_val = self.compute_atr(stock_data) if stock_data is not None and not stock_data.empty and len(stock_data) >= 14 else 0
                if atr_val > 0 and not exit_reason:
                    atr_mult = self.config.get('atr_sl_multiplier', 1.5)
                    if is_commodity_pos:
                        adverse = (stop_loss - current_spot) if direction_pos == 'BULLISH' else (current_spot - stop_loss)
                    else:
                        adverse = (stop_loss - current_spot) if is_call else (current_spot - stop_loss)
                    if adverse > atr_mult * atr_val:
                        exit_reason = f"ATR STOP ({atr_mult}x)"

                # 2. P&L TSL/Max Profit Check (Secondary, uses premium P&L)
                elif (self.exit_policy in ('hybrid', 'per_position_only')) and (pnl_abs >= self.quick_cash_target):
                    if not position.get('partial_booked'):
                        market_type = position.get('market_type', 'OPTIONS')
                        if market_type == 'COMMODITY':
                            # For commodities, use a simple partial close by reducing quantity
                            partial_ratio = float(self.partial_book_pct)/100.0
                            close_qty = max(1, math.floor(position['quantity'] * partial_ratio))
                            if close_qty < position['quantity']:
                                # Create a new position with reduced quantity for accounting
                                position['quantity'] -= close_qty
                                position['partial_booked'] = True
                                logger.info(f"🟡 PARTIAL COMMODITY CLOSE: {position['symbol']} | Qty -{close_qty}")
                        else:
                            self.partial_close_option_position(position_key, current_premium, float(self.partial_book_pct)/100.0, reason="QUICK BOOK")
                        position['partial_booked'] = True
                    else:
                        exit_reason = f"QUICK CASH TARGET HIT (₹{self.quick_cash_target:,.0f})"
                        self.paper_portfolio['consecutive_losses'] = 0

                elif (self.exit_policy in ('hybrid', 'per_position_only')) and (pnl_pct >= TSL_ACTIVATION_PCT):
                    position['peak_pnl_pct'] = max(position.get('peak_pnl_pct', pnl_pct), pnl_pct)
                    peak_pnl = position['peak_pnl_pct']

                    if pnl_pct >= QUICK_MAX_PROFIT_PCT:
                        exit_reason = f"QUICK MAX PROFIT ({QUICK_MAX_PROFIT_PCT:.1f}%)"
                        self.paper_portfolio['consecutive_losses'] = 0
                    elif pnl_pct < (peak_pnl - TSL_PULLBACK_PCT):
                         exit_reason = f"TSL EXIT (Peak {peak_pnl:.1f}%)"
                         self.paper_portfolio['consecutive_losses'] = 0

                # 3. Time-based exit: if not profitable after X minutes
                if (self.exit_policy in ('hybrid', 'per_position_only')) and (not exit_reason):
                    try:
                        entry_ts = datetime.strptime(position['entry_time'], "%Y-%m-%d %H:%M:%S")
                        elapsed = (datetime.now() - entry_ts).total_seconds() / 60.0
                        if elapsed >= float(self.time_exit_minutes) and pnl_abs <= 0:
                            exit_reason = "TIME EXIT"
                        # Age-based exit in days if still not profitable
                        try:
                            days_held = (datetime.now().date() - entry_ts.date()).days
                            max_days = int(self.config.get('max_days_in_trade', 2))
                            if days_held >= max_days and pnl_abs <= 0:
                                exit_reason = "AGE EXIT"
                        except Exception:
                            pass
                    except Exception:
                        pass

                if exit_reason:
                    position['exit_signal_reason'] = exit_reason
                    positions_to_close.append((position_key, current_premium, exit_reason))

                position['current_premium'] = current_premium
                position['pnl_pct'] = pnl_pct
                try:
                    S = float(current_spot)
                    K = float(position.get('strike_price', S))
                    T_days = position.get('expiry_days', 1)
                    T = max(float(T_days) / 365.0, 0.001)
                    r = 0.05
                    opt_type = 'CALL' if position.get('option_type') == 'CALL' else 'PUT'
                    sigma_guess = float(position.get('volatility', 20)) / 100.0
                    iv = self.implied_volatility(float(current_premium), S, K, T, r, opt_type)
                    greeks = self.calculate_greeks(S, K, T, r, iv if iv > 0 else sigma_guess, opt_type)
                    position['delta'] = greeks.get('delta', 0.0)
                    position['theta'] = greeks.get('theta', 0.0)
                    position['vega'] = greeks.get('vega', 0.0)
                    position['iv'] = iv
                except Exception as e:
                    pass

            except Exception as e:
                logger.error(f"Error updating position {position.get('symbol', 'Unknown')}: {e}")
                continue

        pv = max(0.0, float(self.paper_portfolio.get('cash', 0) or 0) + est_val_sum)
        self.paper_portfolio['total_value'] = pv
        if pv > float(self.paper_portfolio.get('peak_portfolio_value', 0) or 0):
            self.paper_portfolio['peak_portfolio_value'] = pv

        init_cap = float(self.paper_portfolio.get('initial_capital', 0) or 0)
        peak_val = float(self.paper_portfolio.get('peak_portfolio_value', init_cap) or init_cap)
        curr_pnl_pct = ((pv - init_cap) / init_cap * 100.0) if init_cap > 0 else 0.0
        peak_pnl_pct = ((peak_val - init_cap) / init_cap * 100.0) if init_cap > 0 else 0.0

        if self.exit_policy in ('hybrid', 'portfolio_only'):
            if curr_pnl_pct >= float(self.config.get('portfolio_tsl_activation_pct', 8.0)):
                if curr_pnl_pct < (peak_pnl_pct - float(self.config.get('portfolio_tsl_pullback_pct', 3.0))):
                    for pk, pos in list(self.paper_portfolio['positions'].items()):
                        try:
                            prem = float(pos.get('current_premium', pos.get('premium_paid', 0)) or 0)
                            if prem <= 0: continue
                            ev = prem * pos.get('lot_size', 0) * pos.get('quantity', 0)
                            en = pos.get('premium_paid', 0) * pos.get('lot_size', 0) * pos.get('quantity', 0)
                            if ev > en:
                                self.partial_close_option_position(pk, prem, float(self.partial_book_pct)/100.0, reason="PORTFOLIO TSL")
                        except Exception:
                            pass
                hard_pull = float(self.config.get('portfolio_hard_lock_pullback_pct', 6.0))
                if curr_pnl_pct < (peak_pnl_pct - hard_pull):
                    for pk, pos in list(self.paper_portfolio['positions'].items()):
                        try:
                            prem = float(pos.get('current_premium', pos.get('premium_paid', 0)) or 0)
                            if prem <= 0: continue
                            positions_to_close.append((pk, prem, "PORTFOLIO HARD LOCK"))
                        except Exception:
                            pass

        for position_key, exit_premium, reason in positions_to_close:
            if position_key in self.paper_portfolio['positions']:
                position = self.paper_portfolio['positions'][position_key]
                market_type = position.get('market_type', 'OPTIONS')
                
                if market_type == 'COMMODITY':
                    self.close_commodity_position(position_key, exit_premium, reason)
                else:
                    self.close_option_position(position_key, exit_premium, reason)

        self.save_trade_history()

    def close_option_position(self, position_key: str, exit_premium: float, reason: str):
        try:
            position = self.paper_portfolio['positions'].get(position_key)
            if not position:
                logger.warning(f"Attempted to close non-existent position: {position_key}")
                return

            if position.get('token'):
                self.unsubscribe_from_option(position['token'])

            if position.get('spot_token'):
                self.unsubscribe_from_spot(position['spot_token']) # NEW: Unsubscribe from spot token

            effective_exit_prem = float(exit_premium)
            entry_value = (position.get('premium_paid', position.get('entry_price', 0)) or 0) * position['lot_size'] * position['quantity']

            # --- LIVE TRADE EXIT (CRITICAL CHANGE) ---
            if self.mode == 'LIVE':
                tradingsymbol = position.get('tradingsymbol')
                if not tradingsymbol:
                    try:
                        tradingsymbol = self.find_nfo_tradingsymbol(position.get('symbol'), position.get('strike_price'), position.get('option_type'))
                        if tradingsymbol:
                            position['tradingsymbol'] = tradingsymbol
                    except Exception:
                        tradingsymbol = None
                if not tradingsymbol:
                    logger.critical(f"Cannot close LIVE position {position_key}: Missing tradingsymbol. Manual intervention required.")
                    return

                # --- GAP B: Retry loop for resilient exit ---
                max_exit_retries = 3
                exit_successful = False

                for attempt in range(max_exit_retries):
                    try:
                        quote_key_full = f"NFO:{tradingsymbol}"
                        current_ltp = None
                        try:
                            q = self.safe_quote(quote_key_full)
                            current_ltp = q.get(quote_key_full, {}).get('last_price')
                        except Exception:
                            current_ltp = None
                        base_prem = current_ltp if (current_ltp and current_ltp > 0.05) else exit_premium
                        limit_price = self._market_protection_limit(base_prem, 'SELL', 'OPT')
                        order_id = self._place_order(
                            tradingsymbol=tradingsymbol,
                            exchange=self.kite.EXCHANGE_NFO,
                            transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                            quantity=position['quantity'] * position['lot_size'],
                            product=self.kite.PRODUCT_MIS,
                            order_type=self.kite.ORDER_TYPE_LIMIT,
                            price=limit_price,
                            validity=self.kite.VALIDITY_DAY,
                            variety=self.kite.VARIETY_REGULAR
                        )
                        try:
                            logger.info(f"{Fore.GREEN}✅ LIVE Exit order placed (Attempt {attempt+1}): {position['symbol']} | SELL LIMIT ₹{limit_price:.2f} | qty {position['quantity'] * position['lot_size']} | variety REGULAR product MIS | ID: {order_id}{Style.RESET_ALL}")
                        except Exception:
                            logger.info(f"{Fore.GREEN}✅ LIVE Exit order placed (Attempt {attempt+1}): {position['symbol']}, ID: {order_id}{Style.RESET_ALL}")

                        max_exit_checks = 8
                        fill_confirmed = False
                        widen_steps = [2, 5, 7]
                        widen_idx = 0
                        for chk in range(max_exit_checks):
                            time.sleep(0.5)
                            try:
                                order_info = self._order_history(order_id)
                            except Exception:
                                order_info = []
                            filled_order = next((o for o in order_info if o.get('status') == 'COMPLETE'), None)
                            if filled_order:
                                fill_confirmed = True
                                try:
                                    ap = filled_order.get('average_price')
                                    if ap and ap > 0:
                                        effective_exit_prem = float(ap)
                                except Exception:
                                    pass
                                break
                            last_status = order_info[-1].get('status') if order_info else 'UNKNOWN'
                            if last_status in ['REJECTED', 'CANCELLED']:
                                break
                            if widen_idx < len(widen_steps) and chk >= widen_steps[widen_idx]:
                                try:
                                    widened_price = self._widened_limit(base_prem, 'SELL', 'OPT', widen_idx + 1)
                                    try:
                                        try:
                                            self.kite.modify_order(variety=self.kite.VARIETY_REGULAR, order_id=order_id, price=widened_price, order_type=self.kite.ORDER_TYPE_LIMIT)
                                        except Exception:
                                            pass
                                        logger.info(f"Widened SELL limit to ₹{widened_price:.2f} (step {widen_idx+1})")
                                    except Exception:
                                        try:
                                            self.kite.cancel_order(self.kite.VARIETY_REGULAR, order_id)
                                        except Exception:
                                            pass
                                        order_id = self._place_order(
                                            tradingsymbol=tradingsymbol,
                                            exchange=self.kite.EXCHANGE_NFO,
                                            transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                                            quantity=position['quantity'] * position['lot_size'],
                                            product=self.kite.PRODUCT_MIS,
                                            order_type=self.kite.ORDER_TYPE_LIMIT,
                                            price=widened_price,
                                            validity=self.kite.VALIDITY_DAY,
                                            variety=self.kite.VARIETY_REGULAR
                                        )
                                        logger.info(f"Replaced SELL order with widened limit ₹{widened_price:.2f}")
                                    widen_idx += 1
                                except Exception:
                                    pass

                        if fill_confirmed:
                            exit_successful = True
                            break

                    except Exception as e:
                        logger.error(f"{Fore.RED}🔴 LIVE EXIT FAILED (Attempt {attempt+1}) for {position['symbol']}: {e}.{Style.RESET_ALL}")
                        if attempt < max_exit_retries - 1:
                            time.sleep(2) # Wait 2 seconds before retrying

                if not exit_successful:
                    logger.critical(f"{Fore.RED}🔴 CRITICAL: ALL LIVE EXIT RETRIES FAILED for {position['symbol']}. Requires MANUAL CLOSE!{Style.RESET_ALL}")
                    self.send_telegram_alert(f"🚨 <b>CRITICAL EXIT FAILURE</b>: {position['symbol']} - All retries failed. **MANUAL CLOSE REQUIRED.**")
                    return # Stop processing this position

            # --- LOCAL ACCOUNTING ---
            exit_value = effective_exit_prem * position['lot_size'] * position['quantity']
            pnl = exit_value - entry_value
            pnl_pct = (pnl/entry_value)*100 if entry_value else 0
            self.paper_portfolio['margin_used'] = max(0, self.paper_portfolio['margin_used'] - entry_value)
            try:
                self.paper_portfolio['realized_pnl_total'] = float(self.paper_portfolio.get('realized_pnl_total', 0) or 0) + float(pnl)
            except Exception:
                pass
            self._recalc_cash()

            pnl_icon = "🟢" if pnl > 0 else "🔴"
            logger.info(f"{pnl_icon} CLOSED: {position.get('symbol')} {position.get('option_type','FUT')} | P&L: ₹{pnl:,.0f} ({reason})")

            # Record in trade journal
            self.record_closed_trade(position, effective_exit_prem, reason, pnl)

            if self.dashboard_app:
                self.dashboard_app.socketio.emit('trade_closed', {
                    'symbol': position['symbol'], 'reason': reason, 'pnl': pnl
                })
                try:
                    self.dashboard_app.socketio.emit('position', {
                        'symbol': position['symbol'], 'pnl': pnl, 'exit_reason': reason
                    })
                except Exception:
                    pass

            del self.paper_portfolio['positions'][position_key]

            if self.telegram_enabled:
                message = f"""
{pnl_icon} <b>OPTION POSITION CLOSED</b>

<b>{position.get('symbol')} {position.get('option_type','FUT')} {position.get('strike_price',0):.0f}</b>

💰 Entry: ₹{(position.get('premium_paid', position.get('entry_price', 0)) or 0):.1f}
💰 Exit: ₹{effective_exit_prem:.1f}
📊 P&L: ₹{pnl:,.0f} ({pnl_pct:+.1f}%)

🎯 Reason: {reason}
"""
                self.send_telegram_alert(message)

            self.save_trade_history()

        except Exception as e:
            logger.error(f"Error closing option position: {e}")
            traceback.print_exc()

    def partial_close_option_position(self, position_key: str, exit_premium: float, ratio: float, reason: str = "PARTIAL BOOK"):
        try:
            position = self.paper_portfolio['positions'].get(position_key)
            if not position:
                return
            close_qty = max(1, math.floor(position['quantity'] * ratio))
            if close_qty >= position['quantity']:
                return self.close_option_position(position_key, exit_premium, reason)
            exit_value = exit_premium * position['lot_size'] * close_qty
            entry_value = (position.get('premium_paid', position.get('entry_price', 0)) or 0) * position['lot_size'] * close_qty
            pnl = exit_value - entry_value
            self.paper_portfolio['margin_used'] = max(0, self.paper_portfolio['margin_used'] - entry_value)
            try:
                self.paper_portfolio['realized_pnl_total'] = float(self.paper_portfolio.get('realized_pnl_total', 0) or 0) + float(pnl)
            except Exception:
                pass
            self._recalc_cash()
            position['quantity'] -= close_qty
            position['partial_booked'] = True
            logger.info(f"🟡 PARTIAL CLOSE: {position.get('symbol')} {position.get('option_type','FUT')} | Qty -{close_qty}, P&L: ₹{pnl:,.0f}")
            self.record_closed_trade(position, exit_premium, reason, pnl)
            self.save_trade_history()
        except Exception as e:
            logger.error(f"Partial close error: {e}")

    def close_commodity_position(self, position_key: str, exit_price: float, reason: str):
        """Close commodity futures position with proper MCX exchange handling"""
        try:
            position = self.paper_portfolio['positions'].get(position_key)
            if not position:
                logger.warning(f"Attempted to close non-existent commodity position: {position_key}")
                return

            if position.get('token'):
                self.unsubscribe_from_option(position['token'])  # Reuse existing unsubscribe method

            exit_value = exit_price * position['lot_size'] * position['quantity']
            entry_value = position['entry_price'] * position['lot_size'] * position['quantity']
            pnl = exit_value - entry_value
            pnl_pct = (pnl/entry_value)*100 if entry_value else 0

            # --- LIVE COMMODITY TRADE EXIT ---
            if self.mode == 'LIVE':
                tradingsymbol = position.get('tradingsymbol')
                if not tradingsymbol:
                    logger.critical(f"Cannot close LIVE commodity position {position_key}: Missing tradingsymbol. Manual intervention required.")
                    return

                max_exit_retries = 3
                exit_successful = False

                for attempt in range(max_exit_retries):
                    try:
                        quote_key_full = f"MCX:{tradingsymbol}"
                        current_ltp = None
                        try:
                            q = self.safe_quote(quote_key_full)
                            current_ltp = q.get(quote_key_full, {}).get('last_price')
                        except Exception:
                            current_ltp = None
                        
                        base_price = current_ltp if (current_ltp and current_ltp > 0.05) else exit_price
                        limit_price = self._market_protection_limit(base_price, 'SELL', 'FUT')
                        
                        order_id = self._place_order(
                            tradingsymbol=tradingsymbol,
                            exchange=self.kite.EXCHANGE_MCX,
                            transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                            quantity=position['quantity'] * position['lot_size'],
                            product=self.kite.PRODUCT_MIS,
                            order_type=self.kite.ORDER_TYPE_LIMIT,
                            price=limit_price,
                            validity=self.kite.VALIDITY_DAY,
                            variety=self.kite.VARIETY_REGULAR
                        )
                        try:
                            logger.info(f"✅ LIVE Commodity Exit order: {position['symbol']} | SELL LIMIT ₹{limit_price:.2f} | qty {position['quantity'] * position['lot_size']} | variety REGULAR product MIS | ID: {order_id}")
                        except Exception:
                            logger.info(f"✅ LIVE Commodity Exit Order placed: {position['symbol']}, ID: {order_id}")

                        # Polling loop for execution confirmation
                        max_retries = 10
                        fill_confirmed = False
                        widen_steps = [3, 6, 9]
                        widen_idx = 0
                        
                        for attempt in range(max_retries):
                            time.sleep(0.5)
                            try:
                                order_info = self._order_history(order_id)
                            except Exception as e:
                                logger.warning(f"Failed to fetch commodity exit order history for {order_id} (Attempt {attempt+1}): {e}")
                                if attempt < max_retries - 1: continue
                                break

                            filled_order = next((o for o in order_info if o.get('status') == 'COMPLETE'), None)
                            
                            if filled_order:
                                final_exit_price = filled_order['average_price']
                                logger.info(f"✅ Commodity exit order filled successfully. Price: ₹{final_exit_price:.2f}")
                                fill_confirmed = True
                                break

                            last_status = order_info[-1].get('status') if order_info else 'UNKNOWN'
                            if last_status in ['REJECTED', 'CANCELLED']:
                                logger.error(f"🔴 LIVE COMMODITY EXIT ORDER REJECTED/CANCELLED. Status: {last_status}")
                                return

                            # Price widening logic for commodity exits
                            if widen_idx < len(widen_steps) and attempt >= widen_steps[widen_idx]:
                                try:
                                    widened_price = self._widened_limit(base_price, 'SELL', 'FUT', widen_idx + 1)
                                    try:
                                        self.kite.modify_order(variety=self.kite.VARIETY_REGULAR, order_id=order_id, price=widened_price, order_type=self.kite.ORDER_TYPE_LIMIT)
                                        logger.info(f"Widened commodity SELL limit to ₹{widened_price:.2f} (step {widen_idx+1})")
                                    except Exception:
                                        try:
                                            self.kite.cancel_order(self.kite.VARIETY_REGULAR, order_id)
                                        except Exception:
                                            pass
                                        # Place new order with widened price
                                        order_id = self._place_order(
                                            tradingsymbol=tradingsymbol,
                                            exchange=self.kite.EXCHANGE_MCX,
                                            transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                                            quantity=position['quantity'] * position['lot_size'],
                                            product=self.kite.PRODUCT_MIS,
                                            order_type=self.kite.ORDER_TYPE_LIMIT,
                                            price=widened_price,
                                            validity=self.kite.VALIDITY_DAY,
                                            variety=self.kite.VARIETY_REGULAR
                                        )
                                        logger.info(f"Replaced commodity SELL order with widened limit ₹{widened_price:.2f}")
                                    widen_idx += 1
                                except Exception:
                                    pass

                        if fill_confirmed:
                            exit_successful = True
                            break

                    except Exception as e:
                        logger.error(f"🔴 LIVE COMMODITY EXIT FAILED (Attempt {attempt+1}) for {position['symbol']}: {e}")
                        if attempt < max_exit_retries - 1:
                            time.sleep(2)

                if not exit_successful:
                    logger.critical(f"🔴 CRITICAL: ALL LIVE COMMODITY EXIT RETRIES FAILED for {position['symbol']}. Requires MANUAL CLOSE!")
                    self.send_telegram_alert(f"🚨 <b>CRITICAL COMMODITY EXIT FAILURE</b>: {position['symbol']} - All retries failed. **MANUAL CLOSE REQUIRED.**")
                    return

            # --- LOCAL ACCOUNTING FOR COMMODITY EXIT ---
            self.paper_portfolio['margin_used'] = max(0, self.paper_portfolio['margin_used'] - entry_value)
            self._recalc_cash()

            pnl_icon = "🟢" if pnl > 0 else "🔴"
            logger.info(f"{pnl_icon} COMMODITY CLOSED: {position['symbol']} FUT | P&L: ₹{pnl:,.0f} ({pnl_pct:+.1f}%) ({reason})")

            # Record in trade journal
            self.record_closed_trade(position, exit_price, reason, pnl)

            if self.dashboard_app:
                self.dashboard_app.socketio.emit('commodity_trade_closed', {
                    'symbol': position['symbol'], 'reason': reason, 'pnl': pnl
                })
                try:
                    self.dashboard_app.socketio.emit('position', {
                        'symbol': position['symbol'], 'pnl': pnl, 'exit_reason': reason
                    })
                except Exception:
                    pass

            del self.paper_portfolio['positions'][position_key]

            if self.telegram_enabled:
                message = f"""
{pnl_icon} <b>COMMODITY POSITION CLOSED</b>

<b>{position['symbol']} FUTURES</b>

💰 Entry: ₹{position['entry_price']:.1f}
💰 Exit: ₹{exit_price:.1f}
📊 P&L: ₹{pnl:,.0f} ({pnl_pct:+.1f}%)

🎯 Reason: {reason}
"""
                self.send_telegram_alert(message)

            self.save_trade_history()

        except Exception as e:
            logger.error(f"Error closing commodity position: {e}")
            traceback.print_exc()
    def auto_close_intraday_positions(self):
        logger.info("EOD close utility running...")
        positions_for_final_close = list(self.paper_portfolio['positions'].keys())

        if not positions_for_final_close:
            logger.info("No positions to close at EOD.")
            return

        scan_list = [self.paper_portfolio['positions'][k]['symbol'] for k in positions_for_final_close]
        # Fetch updated batch data for closing metrics (although live monitoring is preferred)
        batch_data = self.get_stock_data_batch(scan_list)
        self.update_option_positions(batch_data)

        for key in positions_for_final_close:
            position = self.paper_portfolio['positions'].get(key)
            if position:
                # Use current monitored premium for closing calculation
                self.close_option_position(key, position.get('current_premium', 0), "EOD AUTO CLOSE")

    def auto_close_commodity_positions(self):
        logger.info("EOD commodity close utility running...")
        positions_for_close = [k for k, v in self.paper_portfolio['positions'].items() if (v.get('market_type') == 'COMMODITY' or v.get('instrument_type') == 'FUT')]
        if not positions_for_close:
            logger.info("No commodity positions to close at EOD.")
            return
        for key in positions_for_close:
            position = self.paper_portfolio['positions'].get(key)
            if position:
                exit_price = position.get('current_premium', position.get('entry_price', 0))
                self.close_commodity_position(key, exit_price, "EOD AUTO CLOSE")

# ==================================================================================
# MAIN EXECUTION
# ==================================================================================
def main():
    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║   ULTIMATE F&O OPTIONS TRADER - LIVE READY VERSION       ║")
    logger.info("╚══════════════════════════════════════════════════════════╝")

    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest", action="store_true")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--max-symbols", type=int, default=20)
    parser.add_argument("--mode", type=str, choices=["LIVE","PAPER"], default=None)
    parser.add_argument("--strategy", type=str, choices=list(UltimateFNOTrader.STRATEGY_CONFIGS.keys()) + ["ADAPTIVE_AUTO"], default=None)
    parser.add_argument("--no-dashboard", action="store_true")
    parser.add_argument("--scan-interval", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--request-token", type=str, default=None)
    parser.add_argument("--no-input", action="store_true")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--observe-only", action="store_true")
    parser.add_argument("--self-test-protection", action="store_true")
    parser.add_argument("--self-test-widening", action="store_true")
    args, unknown = parser.parse_known_args()

    cfg = load_config()

    strategy_options = list(UltimateFNOTrader.STRATEGY_CONFIGS.keys())
    specific_strategies = [key for key in strategy_options if key != "ADAPTIVE_AUTO"]
    strategy_options_display = specific_strategies + ["ADAPTIVE_AUTO"]

    print("\nAvailable Strategies for Testing:")
    for i, key in enumerate(specific_strategies, 1):
        desc = UltimateFNOTrader.STRATEGY_CONFIGS[key]['description']
        nifty_status = "Bypass Filter" if UltimateFNOTrader.STRATEGY_CONFIGS[key]['ignore_nifty_filter'] else "Filter ON"
        expiry_mode = UltimateFNOTrader.STRATEGY_CONFIGS[key]['expiry_mode']
        print(f"{i}. {key} (Expiry: {expiry_mode}) [Nifty Filter: {nifty_status}]")

    print(f"{len(specific_strategies) + 1}. ADAPTIVE_AUTO (System assesses market and switches automatically.) [Nifty Filter: Strategy-Dependent]")

    selected_key = args.strategy or "ADAPTIVE_AUTO"
    if not args.backtest and args.strategy is None and not args.no_input and not cfg.get('multi_strategy_mode', False):
        while True:
            try:
                choice = input(f"Select strategy (1-{len(strategy_options_display)}, default={len(strategy_options_display)}): ").strip()

                if not choice:
                    selected_key = "ADAPTIVE_AUTO"
                    break

                idx = int(choice) - 1
                if 0 <= idx < len(specific_strategies):
                    selected_key = specific_strategies[idx]
                    break
                elif int(choice) == len(strategy_options_display):
                     selected_key = "ADAPTIVE_AUTO"
                     break
                else:
                    print("Invalid choice. Please enter a number from the list.")
            except ValueError:
                print("Invalid input. Please enter a number.")

    dashboard_instance = None
    trader = None

    try:
        
        if not API_KEY or not API_SECRET:
            try:
                mode_arg = (args.mode or '').upper() if args.mode else ''
            except Exception:
                mode_arg = ''
            if mode_arg == 'PAPER':
                logger.warning("KITE API credentials missing. Continuing in PAPER mode without live Kite.")
            else:
                logger.critical("KITE API credentials missing. Set environment or .env before running.")
                return
        # Configure per-strategy storage paths BEFORE trader init
        try:
            strategy_storage_dir = os.path.join('data', selected_key)
            os.makedirs(strategy_storage_dir, exist_ok=True)
            global PORTFOLIO_FILE, TRADE_JOURNAL_FILE
            PORTFOLIO_FILE = os.path.join(strategy_storage_dir, 'portfolio.json')
            TRADE_JOURNAL_FILE = os.path.join(strategy_storage_dir, 'trade_journal.json')
        except Exception:
            pass

        if args.backtest or args.no_dashboard:
            dashboard_instance = None
        else:
            if not KITE_AVAILABLE:
                logger.critical("Aborting execution: Dashboard dependencies (Flask/SocketIO) missing.")
                return
            cfg = load_config()
            port = args.port if args.port is not None else cfg.get('dashboard_port', 5024)
            dashboard_instance = DashboardApp(None, port=port)

        trader = UltimateFNOTrader(dashboard_instance, initial_strategy_key=selected_key, request_token_override=args.request_token, allow_input=(not args.no_input))
        try:
            trader.observe_only = bool(args.observe_only) or (str(os.getenv('OBSERVE_ONLY','')).strip() == '1')
        except Exception:
            pass
        if dashboard_instance:
            dashboard_instance.trader = trader
        try:
            trader.reload_state_from_disk()
        except Exception:
            pass

        now = datetime.now()
        market_open_time = dtime(9, 15)
        market_close_time = dtime(15, 30)
        current_time = now.time()

        # NOTE: Market is closed check is run *after* trader init to allow API loading.
        # This block is now fully disabled to ensure continuous run.
        # if market_open_time > current_time or current_time > market_close_time:
        #     # OFFLINE TEST BLOCK REMOVED/DISABLED

        if args.self_test_protection:
             trader.self_test_market_protection()
             return
        if args.self_test_widening:
             trader.self_test_widening()
             return
        if args.backtest:
             trader.set_active_strategy("HIGH_CONVICTION")
             trader.active_market_regime = "BACKTEST"
             trader.run_offhours_backtest(days=args.days, max_symbols=args.max_symbols)
             trader.log_final_summary()
             return
        elif (args.strategy or selected_key) == "ADAPTIVE_AUTO":
             trader.display_strategy_key = "ADAPTIVE_AUTO"
             initial_regime = trader.assess_market_regime()
             final_strategy_key = trader.REGIME_STRATEGY_MAP.get(initial_regime, "HIGH_CONVICTION")
             trader.set_active_strategy(final_strategy_key)
             trader.active_market_regime = initial_regime
             logger.info(f"{Fore.CYAN}--- AUTONOMOUS START: System chose {final_strategy_key} (Regime: {initial_regime}) ---{Style.RESET_ALL}")
             trader.update_performance_metrics()
             trader.validate_strategy_performance()
        else:
             trader.set_active_strategy(args.strategy or selected_key)
             trader.active_market_regime = "MANUAL_OVERRIDE"
             logger.info(f"{Fore.CYAN}--- MANUAL START: Strategy forced to {selected_key} ---{Style.RESET_ALL}")

        if not trader.nse_fo_stocks or len(trader.nse_fo_stocks) < 1:
            if trader.mode == 'PAPER':
                logger.warning(f"Proceeding in PAPER mode with mock universe (F&O list size: {len(trader.nse_fo_stocks)}).")
            else:
                logger.critical(f"Aborting execution: F&O stock list is too small ({len(trader.nse_fo_stocks)}). Check Kite API keys/token.")
                return

        if trader and dashboard_instance:
            logger.info(f"{Fore.GREEN}Emitting initial dashboard data on startup...{Style.RESET_ALL}")
            dashboard_instance.emit_portfolio_update(trader.package_dashboard_data())

        if args.scan_interval:
            trader.SCAN_INTERVAL_MINUTES = max(1, args.scan_interval)
        if args.batch_size:
            trader.scan_batch_size = max(1, args.batch_size)
        if args.mode:
            trader.mode = args.mode
        trader.start_automatic_scheduler()

        if dashboard_instance:
            dashboard_thread = threading.Thread(target=dashboard_instance.start_dashboard, daemon=True)
            dashboard_thread.start()

        cfg = load_config()
        port = args.port if args.port is not None else cfg.get('dashboard_port', 5024)
        logger.info(f"{Fore.GREEN}System running continuously. Access dashboard at http://0.0.0.0:{port}/. Press Ctrl+C to stop.{Style.RESET_ALL}")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("\nCtrl+C detected. Shutting down system.")
        if trader:
            trader.log_final_summary()

    except Exception as e:
        logger.critical(f"Critical error in main loop: {e}")
        traceback.print_exc()

    finally:
        if 'trader' in locals() and trader is not None:
            try:
                trader.stop_automatic_scheduler()
                trader.save_trade_history()
                logger.info("👋 System Shutdown Complete. Portfolio saved.")
            except Exception as e:
                 logger.critical(f"Error during final cleanup: {e}. Force quit.")

if __name__ == "__main__":
    main()
