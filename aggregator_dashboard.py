import os
import json
import threading
import time
import argparse
from typing import List, Dict

from flask import Flask, jsonify, render_template, render_template_string
import yfinance as yf
from flask_socketio import SocketIO
from urllib.request import urlopen
from urllib.error import URLError, HTTPError
import requests
try:
    from core.bot import TradingBot
except Exception:
    TradingBot = None

EXPECTED_STRATEGIES = []

PORT_STRATEGY_MAP = {
    5004: 'HIGH_CONVICTION',
    5005: 'AGGRESSIVE_REVERSAL',
    5006: 'MID_TREND_SWING',
    5007: 'AGGRESSIVE_SCALP',
    5008: 'ADAPTIVE_AUTO',
    5009: 'COMMODITY_TREND',
    5011: 'COMMODITY_REVERSAL',
    5012: 'COMMODITY_SCALP'
}


def fetch_state(port: int, timeout: float = 3.0, strategy_key: str = None) -> Dict:
    endpoints = [
        f"http://127.0.0.1:{port}/api/state",
        f"http://127.0.0.1:{port}/api/simple_state"
    ]
    for url in endpoints:
        try:
            with urlopen(url, timeout=timeout) as resp:
                data = resp.read().decode('utf-8')
                j = json.loads(data)
                return j if isinstance(j, dict) else {}
        except (URLError, HTTPError, TimeoutError):
            continue
        except Exception:
            continue
    return {}


def merge_states(states: List[Dict]) -> Dict:
    summary_agg = {
        'initial_capital': 0.0,
        'portfolio_value': 0.0,
        'cash': 0.0,
        'margin_used': 0.0,
        'exposure_value': 0.0,
        'open_pnl': 0.0,
        'net_pnl': 0.0,
        'total_pnl': 0.0,
        'total_pnl_pct': 0.0,
        'active_trades': 0,
        'exit_signals_count': 0,
        'health_status': 'HEALTHY',
        'strategy': 'AGGREGATED',
        'market_regime': 'MIXED',
        'exit_policy': 'MIXED',
        'scan_cycles': 0,
        'stocks_scanned': 0,
        'signals_found': 0,
        'trades_executed': 0,
        'api_calls': 0,
        'consecutive_losses': 0,
        'daily_pnl': 0.0,
        'daily_pnl_pct': 0.0,
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
    }

    positions_agg: List[Dict] = []
    signals_agg: List[Dict] = []
    closed_agg: List[Dict] = []
    by_strategy: Dict[str, Dict] = {}
    daily_pnl_union: Dict[str, float] = {}
    intraday_pnl_union: Dict[str, float] = {}
    exit_policies: List[str] = []

    total_initial = 0.0
    total_value = 0.0
    journal_pf_values = []
    win_rate_weighted_sum = 0.0

    for st in states:
        s = st.get('summary', {})
        pos = st.get('positions', [])

        summary_agg['initial_capital'] += float(s.get('initial_capital', 0) or 0)
        summary_agg['portfolio_value'] += float(s.get('portfolio_value', 0) or 0)
        summary_agg['cash'] += float(s.get('cash', 0) or 0)
        summary_agg['margin_used'] += float(s.get('margin_used', 0) or 0)
        summary_agg['exposure_value'] += float(s.get('exposure_value', 0) or 0)
        summary_agg['total_pnl'] += float(s.get('total_pnl', 0) or 0)
        summary_agg['active_trades'] += int(s.get('active_trades', 0) or 0)
        summary_agg['exit_signals_count'] += int(s.get('exit_signals_count', 0) or 0)
        summary_agg['scan_cycles'] += int(s.get('scan_cycles', 0) or 0)
        summary_agg['stocks_scanned'] += int(s.get('stocks_scanned', 0) or 0)
        summary_agg['signals_found'] += int(s.get('signals_found', 0) or 0)
        summary_agg['trades_executed'] += int(s.get('trades_executed', 0) or 0)
        summary_agg['api_calls'] += int(s.get('api_calls', 0) or 0)
        summary_agg['consecutive_losses'] += int(s.get('consecutive_losses', 0) or 0)
        summary_agg['daily_pnl'] += float(s.get('daily_pnl', 0) or 0)
        try:
            ep = s.get('exit_policy')
            if isinstance(ep, str) and ep:
                exit_policies.append(ep.strip().lower())
        except Exception:
            pass

        j = s.get('journal', {})
        total_trades = int(j.get('total_trades', 0) or 0)
        profitable_trades = int(j.get('profitable_trades', 0) or 0)
        summary_agg['journal']['total_trades'] += total_trades
        summary_agg['journal']['profitable_trades'] += profitable_trades
        summary_agg['journal']['avg_win'] += float(j.get('avg_win', 0) or 0)
        summary_agg['journal']['avg_loss'] += float(j.get('avg_loss', 0) or 0)
        summary_agg['journal']['largest_win'] = max(summary_agg['journal']['largest_win'], float(j.get('largest_win', 0) or 0))
        summary_agg['journal']['largest_loss'] = min(summary_agg['journal']['largest_loss'] or 0.0, float(j.get('largest_loss', 0) or 0)) if summary_agg['journal']['largest_loss'] != 0.0 else float(j.get('largest_loss', 0) or 0)
        pf = float(j.get('profit_factor', 0) or 0)
        if pf:
            journal_pf_values.append(pf)

        total_initial += float(s.get('initial_capital', 0) or 0)
        total_value += float(s.get('portfolio_value', 0) or 0)
        if total_trades:
            win_rate_weighted_sum += (float(j.get('win_rate', 0) or 0) * total_trades)

        # Merge daily pnl series for portfolio history
        series = j.get('daily_pnl_series', {}) if isinstance(j, dict) else {}
        for d, val in (series.items() if isinstance(series, dict) else []):
            try:
                daily_pnl_union[d] = daily_pnl_union.get(d, 0.0) + float(val or 0)
            except Exception:
                pass
        intra = j.get('intraday_pnl_series', {}) if isinstance(j, dict) else {}
        for t, val in (intra.items() if isinstance(intra, dict) else []):
            try:
                intraday_pnl_union[t] = intraday_pnl_union.get(t, 0.0) + float(val or 0)
            except Exception:
                pass

        for p in pos:
            p2 = dict(p)
            p2['source_strategy'] = s.get('strategy', 'UNKNOWN')
            positions_agg.append(p2)
            if os.getenv('AGG_DEBUG') == '1':
                print(f"DEBUG: Added position from {s.get('strategy', 'UNKNOWN')}: {p2.get('symbol', 'Unknown')}")
        sigs = st.get('signals', [])
        for sg in sigs:
            sg2 = dict(sg)
            sg2['source_strategy'] = s.get('strategy', 'UNKNOWN')
            signals_agg.append(sg2)
        # Merge closed trades
        cts = st.get('closed_trades', []) or []
        for ct in cts:
            ct2 = dict(ct)
            ct2['source_strategy'] = s.get('strategy', 'UNKNOWN')
            closed_agg.append(ct2)

        strat = s.get('strategy', 'UNKNOWN')
        if strat not in by_strategy:
            by_strategy[strat] = {
                'initial_capital': 0.0,
                'portfolio_value': 0.0,
                'cash': 0.0,
                'margin_used': 0.0,
                'exposure_value': 0.0,
                'active_trades': 0,
                'signals_found': 0,
                'trades_executed': 0,
                'api_calls': 0,
                'journal': {
                    'win_rate_weighted_sum': 0.0,
                    'total_trades': 0,
                    'profitable_trades': 0,
                    'avg_win_sum': 0.0,
                    'avg_loss_sum': 0.0,
                    'largest_win': 0.0,
                    'largest_loss': 0.0,
                    'profit_factor_values': []
                },
                'health_statuses': [],
                'sources': []
            }
        bs = by_strategy[strat]
        bs['initial_capital'] += float(s.get('initial_capital', 0) or 0)
        bs['portfolio_value'] += float(s.get('portfolio_value', 0) or 0)
        bs['cash'] += float(s.get('cash', 0) or 0)
        bs['margin_used'] += float(s.get('margin_used', 0) or 0)
        bs['exposure_value'] += float(s.get('exposure_value', 0) or 0)
        bs['active_trades'] += int(s.get('active_trades', 0) or 0)
        bs['signals_found'] += int(s.get('signals_found', 0) or 0)
        bs['trades_executed'] += int(s.get('trades_executed', 0) or 0)
        bs['api_calls'] += int(s.get('api_calls', 0) or 0)
        j = s.get('journal', {})
        total_trades = int(j.get('total_trades', 0) or 0)
        bs['journal']['total_trades'] += total_trades
        bs['journal']['profitable_trades'] += int(j.get('profitable_trades', 0) or 0)
        bs['journal']['avg_win_sum'] += float(j.get('avg_win', 0) or 0)
        bs['journal']['avg_loss_sum'] += float(j.get('avg_loss', 0) or 0)
        bs['journal']['largest_win'] = max(bs['journal']['largest_win'], float(j.get('largest_win', 0) or 0))
        # For largest_loss we want most negative value
        bs['journal']['largest_loss'] = min(bs['journal']['largest_loss'] or 0.0, float(j.get('largest_loss', 0) or 0)) if bs['journal']['largest_loss'] != 0.0 else float(j.get('largest_loss', 0) or 0)
        pf = float(j.get('profit_factor', 0) or 0)
        if pf:
            bs['journal']['profit_factor_values'].append(pf)
        win_rate = float(j.get('win_rate', 0) or 0)
        if total_trades:
            bs['journal']['win_rate_weighted_sum'] += (win_rate * total_trades)
        bs['health_statuses'].append(s.get('health_status', 'UNKNOWN'))

    if total_initial:
        summary_agg['total_pnl_pct'] = ((summary_agg['portfolio_value'] - total_initial) / total_initial) * 100.0
    try:
        summary_agg['exit_policy'] = exit_policies[0] if exit_policies and all(ep == exit_policies[0] for ep in exit_policies) else 'MIXED'
    except Exception:
        pass

    if summary_agg['journal']['total_trades']:
        summary_agg['journal']['win_rate'] = (win_rate_weighted_sum / summary_agg['journal']['total_trades'])
    if journal_pf_values:
        summary_agg['journal']['profit_factor'] = sum(journal_pf_values) / len(journal_pf_values)

    # Build strategy breakdown list
    strategy_breakdown = []
    for strat, bs in by_strategy.items():
        init_cap = bs['initial_capital']
        port_val = bs['portfolio_value']
        total_pnl = port_val - init_cap
        pnl_pct = ((port_val - init_cap) / init_cap * 100.0) if init_cap else 0.0
        jt = bs['journal']['total_trades']
        win_rate = (bs['journal']['win_rate_weighted_sum'] / jt) if jt else 0.0
        pf_vals = bs['journal']['profit_factor_values']
        pf = (sum(pf_vals) / len(pf_vals)) if pf_vals else 0.0
        avg_win = bs['journal']['avg_win_sum']
        avg_loss = bs['journal']['avg_loss_sum']
        health = 'HEALTHY'
        if any(h for h in by_strategy[strat]['health_statuses'] if h != 'HEALTHY'):
            health = 'MIXED'
        strategy_breakdown.append({
            'strategy': strat,
            'initial_capital': init_cap,
            'portfolio_value': port_val,
            'total_pnl': total_pnl,
            'total_pnl_pct': pnl_pct,
            'active_trades': bs['active_trades'],
            'signals_found': bs['signals_found'],
            'trades_executed': bs['trades_executed'],
            'api_calls': bs['api_calls'],
            'journal': {
                'win_rate': win_rate,
                'total_trades': jt,
                'profitable_trades': bs['journal']['profitable_trades'],
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'largest_win': bs['journal']['largest_win'],
                'largest_loss': bs['journal']['largest_loss'],
                'profit_factor': pf
            },
            'health_status': health,
            'sources': []
        })

    # Do not override strategy capital; use actual values reported by instances

    # Strict live mode: do not inject missing strategies or simulate target allocations

    # Adaptive filter: if any source reports ADAPTIVE_AUTO with an explicit sources list,
    # then show only those active keys to avoid listing all strategies.
    try:
        adaptive_keys = set()
        for st in states:
            summ = (st or {}).get('summary', {})
            if (summ.get('strategy') == 'ADAPTIVE_AUTO'):
                srcs = summ.get('sources') or []
                if not srcs:
                    srcs = (st or {}).get('sources') or []
                for k in (srcs if isinstance(srcs, list) else []):
                    adaptive_keys.add(k)
        if adaptive_keys:
            strategy_breakdown = [e for e in strategy_breakdown if e.get('strategy') in adaptive_keys]
    except Exception:
        pass

    # Get market data from first available state
    market_data = {}
    for st in states:
        if st.get('market_data'):
            market_data = st['market_data']
            break
    
    # Calculate daily_pnl_pct based on daily_pnl and initial_capital
    if summary_agg['initial_capital'] > 0:
        summary_agg['daily_pnl_pct'] = (summary_agg['daily_pnl'] / summary_agg['initial_capital']) * 100.0
    
    # Build portfolio history from merged daily pnl series
    portfolio_history = {'labels': [], 'values': []}
    if daily_pnl_union:
        try:
            dates_sorted = sorted(daily_pnl_union.keys())
            cumulative = 0.0
            base = total_initial
            for d in dates_sorted:
                cumulative += float(daily_pnl_union.get(d, 0.0) or 0.0)
                portfolio_history['labels'].append(d)
                portfolio_history['values'].append(base + cumulative)
        except Exception:
            portfolio_history = {'labels': [], 'values': []}
    if not portfolio_history['labels']:
        portfolio_history['labels'] = ['Now']
        portfolio_history['values'] = [summary_agg.get('portfolio_value', total_initial)]

    intraday_history = {'labels': [], 'values': []}
    if intraday_pnl_union:
        try:
            times_sorted = sorted(intraday_pnl_union.keys())
            cumulative = 0.0
            base = total_initial
            for t in times_sorted:
                cumulative += float(intraday_pnl_union.get(t, 0.0) or 0.0)
                intraday_history['labels'].append(t)
                intraday_history['values'].append(base + cumulative)
        except Exception:
            intraday_history = {'labels': [], 'values': []}
    if not intraday_history['labels']:
        now_label = time.strftime('%H:%M')
        intraday_history['labels'] = [now_label]
        intraday_history['values'] = [summary_agg.get('portfolio_value', total_initial)]

    
    result = {
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'summary': summary_agg,
        'positions': positions_agg,
        'signals': signals_agg[-50:],
        'closed_trades': closed_agg[-200:],
        'strategy_breakdown': strategy_breakdown,
        'market_data': market_data,
        'portfolio_history': portfolio_history,
        'intraday_history': intraday_history
    }
    try:
        exposure_sum = 0.0
        open_pnl_sum = 0.0
        for p in positions_agg:
            entry = p.get('entry_premium') or p.get('entry_price') or p.get('avg_price') or 0
            current = p.get('current_premium') or p.get('current_price') or p.get('ltp') or entry or 0
            qty = float(p.get('quantity') or 0)
            lot = float(p.get('lot_size') or 1)
            exposure_sum += abs(current) * lot * abs(qty)
            open_pnl_sum += ((current - entry) * lot * qty)
        result['summary']['exposure_value'] = exposure_sum
        result['summary']['open_pnl'] = open_pnl_sum
        cash_val = float(result['summary'].get('cash', 0) or 0)
        result['summary']['portfolio_value'] = cash_val + exposure_sum
        # Compute pure available cash excluding realized P&L (initial capital minus margin used)
        init_cap_for_avail = float(result['summary'].get('initial_capital', 0) or 0)
        margin_used_val = float(result['summary'].get('margin_used', 0) or 0)
        result['summary']['available_cash'] = max(0.0, init_cap_for_avail - margin_used_val)
        init_cap = float(result['summary'].get('initial_capital', 0) or 0)
        if init_cap:
            total_pnl = (cash_val + exposure_sum) - init_cap
            result['summary']['total_pnl'] = total_pnl
            result['summary']['total_pnl_pct'] = (total_pnl / init_cap) * 100.0
        result['summary']['total_closed_pnl'] = sum(float(ct.get('pnl') or 0) for ct in closed_agg)
        result['summary']['net_pnl'] = result['summary']['open_pnl'] + result['summary']['total_closed_pnl']
    except Exception:
        pass
    if os.getenv('AGG_DEBUG') == '1':
        print(f"DEBUG: Returning {len(positions_agg)} positions in total")
    return result


class AggregatorApp:
    def __init__(self, source_ports: List[int], port: int = 5010):
        template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
        self.app = Flask(__name__, template_folder=template_dir)
        import logging
        logging.getLogger('werkzeug').setLevel(logging.ERROR)
        logging.getLogger('engineio').setLevel(logging.ERROR)
        logging.getLogger('socketio').setLevel(logging.ERROR)
        self.app.logger.propagate = False
        self.app.logger.handlers = []
        
        # Add CORS headers to all responses
        @self.app.after_request
        def after_request(response):
            response.headers.add('Access-Control-Allow-Origin', '*')
            response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
            response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
            return response

        @self.app.errorhandler(Exception)
        def handle_error(e):
            try:
                return jsonify({'ok': False, 'error': str(e)}), 500
            except Exception:
                return ('', 500)
            
        self.socketio = SocketIO(self.app, async_mode='threading', ping_interval=15, ping_timeout=30, cors_allowed_origins="*")
        self.source_ports = source_ports
        self.port = port
        self.last_nonzero = None
        self.last_states_by_port: Dict[int, Dict] = {}
        self.last_seen_at_port: Dict[int, float] = {}
        self.state_ttl_seconds: float = 15.0
        self.prev_positions_by_port = {}
        self.prev_closed_count_by_port = {}
        self.main_bot = None
        self.bot_thread = None
        self.signal_buffer: List[Dict] = []
        self.signal_buffer_max: int = 1000
        # Default telemetry target so TradingBot posts to this aggregator
        try:
            os.environ.setdefault('DASHBOARD_URL', f'http://127.0.0.1:{self.port}')
        except Exception:
            pass
        # Auto-start main bot if requested
        try:
            auto = (os.getenv('AUTO_START_MAIN_BOT') or '').strip().lower()
            if auto in ('1', 'true', 'yes') and TradingBot is not None:
                mode = os.getenv('TRADING_MODE', 'paper')
                self.main_bot = TradingBot(mode=mode)
                def run_bot():
                    try:
                        self.main_bot.start()
                    except Exception:
                        pass
                self.bot_thread = threading.Thread(target=run_bot, daemon=True)
                self.bot_thread.start()
        except Exception:
            pass
        try:
            from api.zerodha import KiteAPI
            self.kite_api = KiteAPI(api_key=os.getenv('KITE_API_KEY'), api_secret=os.getenv('KITE_API_SECRET'))
        except Exception:
            self.kite_api = None

        @self.app.route('/')
        def index():
            try:
                states = [fetch_state(p, strategy_key=PORT_STRATEGY_MAP.get(p)) for p in self.source_ports]
                agg = merge_states([s for s in states if s])
            except Exception:
                agg = {}
            try:
                sigs = list(agg.get('signals', []) or [])
                if self.signal_buffer:
                    sigs = (sigs + self.signal_buffer[-50:])[-50:]
                agg['signals'] = sigs
            except Exception:
                pass
            return render_template('dashboard.html', initial_data=json.dumps(agg))

        @self.app.route('/favicon.ico')
        def favicon():
            return ('', 204)

        @self.app.route('/api/market-data')
        def api_market_data():
            # Try proxying to a live strategy server first
            for port in self.source_ports:
                try:
                    with urlopen(f"http://127.0.0.1:{port}/api/index_quotes", timeout=2.0) as resp:
                        data = resp.read().decode('utf-8')
                        j = json.loads(data)
                        if isinstance(j, dict) and j:
                            keys = ['nifty','banknifty','finnifty','sensex']
                            out = {k: j.get(k) for k in keys if j.get(k) is not None}
                            if out:
                                return jsonify(out)
                except Exception:
                    continue
            # Strict live-only: return zeroed values if live proxy unavailable
            return jsonify({
                'nifty': {'price': 0.0, 'change': 0.0, 'change_percent': 0.0},
                'banknifty': {'price': 0.0, 'change': 0.0, 'change_percent': 0.0},
                'finnifty': {'price': 0.0, 'change': 0.0, 'change_percent': 0.0},
                'sensex': {'price': 0.0, 'change': 0.0, 'change_percent': 0.0}
            })

        

        @self.app.route('/api/kite_status')
        def api_kite_status():
            for port in self.source_ports:
                try:
                    with urlopen(f"http://127.0.0.1:{port}/api/kite_status", timeout=2.0) as resp:
                        data = resp.read().decode('utf-8')
                        j = json.loads(data)
                        if isinstance(j, dict) and j:
                            return jsonify(j)
                except Exception:
                    continue
            return jsonify({'connected': False, 'ws_connected': False, 'mode': 'UNKNOWN', 'subscribed_tokens': {'options': 0, 'spots': 0}})

        @self.app.route('/api/kite/login_url')
        def api_kite_login_url():
            try:
                if self.kite_api:
                    url = self.kite_api.login_url()
                    return jsonify({'login_url': url})
            except Exception:
                pass
            return jsonify({'login_url': None})

        @self.app.route('/api/kite/generate_session', methods=['POST'])
        def api_kite_generate_session():
            from flask import request
            try:
                payload = request.get_json(force=False, silent=True) or {}
                token = (payload.get('request_token') or request.args.get('request_token') or '').strip()
                if not token:
                    return jsonify({'ok': False, 'error': 'missing_request_token'}), 400
                if not self.kite_api:
                    return jsonify({'ok': False, 'error': 'kite_unavailable'}), 500
                sess = self.kite_api.generate_session(token)
                if isinstance(sess, dict) and sess.get('access_token'):
                    try:
                        fp = os.path.join(os.getcwd(), 'kite_access_token.json')
                        with open(fp, 'w') as f:
                            json.dump(sess, f, indent=4)
                    except Exception:
                        pass
                    return jsonify({'ok': True, 'access_token': sess.get('access_token')})
                return jsonify({'ok': False, 'error': 'session_failed'}), 500
            except Exception:
                return jsonify({'ok': False, 'error': 'session_error'}), 500

        @self.app.route('/api/kite/set_access_token', methods=['POST'])
        def api_kite_set_access_token():
            from flask import request
            try:
                payload = request.get_json(force=True) or {}
                access_token = (payload.get('access_token') or '').strip()
                if not access_token:
                    return jsonify({'ok': False, 'error': 'missing_access_token'}), 400
                if not self.kite_api:
                    return jsonify({'ok': False, 'error': 'kite_unavailable'}), 500
                try:
                    self.kite_api.set_access_token(access_token)
                    fp = os.path.join(os.getcwd(), 'kite_access_token.json')
                    with open(fp, 'w') as f:
                        json.dump({'access_token': access_token}, f, indent=4)
                except Exception:
                    return jsonify({'ok': False, 'error': 'save_failed'}), 500
                return jsonify({'ok': True})
            except Exception:
                return jsonify({'ok': False, 'error': 'token_set_error'}), 500

        @self.app.route('/api/aggregate_kite_status')
        def api_aggregate_kite_status():
            statuses = []
            for port in self.source_ports:
                try:
                    with urlopen(f"http://127.0.0.1:{port}/api/kite_status", timeout=2.0) as resp:
                        data = resp.read().decode('utf-8')
                        j = json.loads(data)
                        j['port'] = port
                        statuses.append(j)
                except Exception:
                    statuses.append({'port': port, 'connected': False, 'ws_connected': False})
            total = len(self.source_ports) if self.source_ports else 0
            connected = sum(1 for s in statuses if s.get('connected'))
            ws_connected = sum(1 for s in statuses if s.get('ws_connected'))
            last_ticks = [s.get('last_tick_ts') for s in statuses if s.get('last_tick_ts')]
            agg = {
                'total_ports': total,
                'connected_ports': connected,
                'ws_connected_ports': ws_connected,
                'all_connected': (connected == total and total > 0),
                'all_ws_connected': (ws_connected == total and total > 0),
                'latest_tick_ts': max(last_ticks) if last_ticks else None,
                'statuses': statuses
            }
            return jsonify(agg)

        @self.app.route('/api/logs')
        def api_logs():
            from flask import request
            level = (request.args.get('level') or 'all').lower()
            try:
                import glob
                root = os.path.join(os.getcwd(), 'logs')
                files = sorted(glob.glob(os.path.join(root, '*.log')))
                entries = []
                for fp in files:
                    try:
                        with open(fp, 'r', encoding='utf-8', errors='ignore') as f:
                            lines = f.readlines()[-200:]
                        for ln in lines:
                            txt = ln.strip()
                            if not txt:
                                continue
                            lvl = 'info'
                            if '[ERROR]' in txt or 'ERROR' in txt:
                                lvl = 'error'
                            elif '[WARN' in txt or 'WARN' in txt:
                                lvl = 'warning'
                            elif '[INFO]' in txt or 'INFO' in txt:
                                lvl = 'info'
                            if level != 'all' and lvl != level:
                                continue
                            entries.append({'file': os.path.basename(fp), 'level': lvl, 'text': txt})
                    except Exception:
                        continue
                entries = entries[-200:]
                return jsonify({'ok': True, 'entries': entries})
            except Exception as e:
                return jsonify({'ok': False, 'error': str(e), 'entries': []}), 500

        @self.app.route('/api/start', methods=['POST'])
        def api_start_scheduler():
            try:
                if self.main_bot is None:
                    if TradingBot is None:
                        return jsonify({'ok': False, 'error': 'trading_bot_unavailable'}), 500
                    mode = os.getenv('TRADING_MODE', 'paper')
                    self.main_bot = TradingBot(mode=mode)
                if not getattr(self.main_bot, 'is_running', False):
                    def run_bot():
                        try:
                            self.main_bot.start()
                        except Exception:
                            pass
                    self.bot_thread = threading.Thread(target=run_bot, daemon=True)
                    self.bot_thread.start()
                return jsonify({'ok': True})
            except Exception as e:
                return jsonify({'ok': False, 'error': str(e)}), 500

        @self.app.route('/api/stop', methods=['POST'])
        def api_stop_scheduler():
            try:
                if self.main_bot is None:
                    return jsonify({'ok': True})
                try:
                    self.main_bot.stop()
                except Exception:
                    pass
                try:
                    if self.bot_thread:
                        self.bot_thread.join(timeout=2.0)
                except Exception:
                    pass
                return jsonify({'ok': True})
            except Exception as e:
                return jsonify({'ok': False, 'error': str(e)}), 500

        @self.app.route('/api/admin/reset_daily', methods=['POST'])
        def api_admin_reset_daily():
            try:
                if self.main_bot is None:
                    if TradingBot is None:
                        return jsonify({'ok': False, 'error': 'trading_bot_unavailable'}), 500
                    mode = os.getenv('TRADING_MODE', 'paper')
                    self.main_bot = TradingBot(mode=mode)
                try:
                    self.main_bot.reset_system(hard=False)
                except Exception:
                    pass
                return jsonify({'ok': True})
            except Exception as e:
                return jsonify({'ok': False, 'error': str(e)}), 500

        @self.app.route('/api/admin/system_reset', methods=['POST'])
        def api_admin_system_reset():
            try:
                if self.main_bot is None:
                    if TradingBot is None:
                        return jsonify({'ok': False, 'error': 'trading_bot_unavailable'}), 500
                    mode = os.getenv('TRADING_MODE', 'paper')
                    self.main_bot = TradingBot(mode=mode)
                try:
                    self.main_bot.reset_system(hard=True)
                except Exception:
                    pass
                return jsonify({'ok': True})
            except Exception as e:
                return jsonify({'ok': False, 'error': str(e)}), 500

        @self.app.route('/api/metrics', methods=['POST'])
        def api_receive_metrics():
            from flask import request
            try:
                payload = request.get_json(force=False, silent=True) or {}
                try:
                    self.socketio.emit('metrics', payload)
                except Exception:
                    pass
                return jsonify({'ok': True})
            except Exception as e:
                return jsonify({'ok': False, 'error': str(e)}), 500

        @self.app.route('/api/signal', methods=['POST'])
        def api_receive_signal():
            from flask import request
            try:
                payload = request.get_json(force=False, silent=True) or {}
                try:
                    self.socketio.emit('signal', payload)
                except Exception:
                    pass
                try:
                    rec = dict(payload)
                    if 'timestamp' not in rec:
                        rec['timestamp'] = time.strftime('%Y-%m-%dT%H:%M:%S')
                    self.signal_buffer.append(rec)
                    if len(self.signal_buffer) > self.signal_buffer_max:
                        self.signal_buffer = self.signal_buffer[-self.signal_buffer_max:]
                except Exception:
                    pass
                return jsonify({'ok': True})
            except Exception as e:
                return jsonify({'ok': False, 'error': str(e)}), 500

        @self.app.route('/api/strategies/start-all', methods=['POST'])
        def api_start_all():
            outcomes = {}
            for port in self.source_ports:
                name = PORT_STRATEGY_MAP.get(port, str(port))
                try:
                    r = requests.post(f"http://127.0.0.1:{port}/api/start", timeout=5)
                    outcomes[name] = {'ok': r.status_code == 200}
                except Exception as e:
                    outcomes[name] = {'ok': False, 'error': str(e)}
            return jsonify({'results': outcomes})

        @self.app.route('/api/strategies/stop-all', methods=['POST'])
        def api_stop_all():
            outcomes = {}
            for port in self.source_ports:
                name = PORT_STRATEGY_MAP.get(port, str(port))
                try:
                    r = requests.post(f"http://127.0.0.1:{port}/api/stop", timeout=5)
                    outcomes[name] = {'ok': r.status_code == 200}
                except Exception as e:
                    outcomes[name] = {'ok': False, 'error': str(e)}
            return jsonify({'results': outcomes})

        @self.app.route('/api/strategies/reset-all', methods=['POST'])
        def api_reset_all():
            outcomes = {}
            for port in self.source_ports:
                name = PORT_STRATEGY_MAP.get(port, str(port))
                try:
                    r = requests.post(f"http://127.0.0.1:{port}/api/reset", timeout=5)
                    outcomes[name] = {'ok': r.status_code == 200}
                except Exception as e:
                    outcomes[name] = {'ok': False, 'error': str(e)}
            return jsonify({'results': outcomes})

        @self.app.route('/api/strategies/start/<strategy>', methods=['POST'])
        def api_start_strategy(strategy):
            outcomes = {}
            matched = [p for p, name in PORT_STRATEGY_MAP.items() if name == strategy]
            for port in matched:
                name = PORT_STRATEGY_MAP.get(port, str(port))
                try:
                    r = requests.post(f"http://127.0.0.1:{port}/api/start", timeout=5)
                    outcomes[name] = {'ok': r.status_code == 200}
                except Exception as e:
                    outcomes[name] = {'ok': False, 'error': str(e)}
            return jsonify({'results': outcomes})

        @self.app.route('/api/strategies/stop/<strategy>', methods=['POST'])
        def api_stop_strategy(strategy):
            outcomes = {}
            matched = [p for p, name in PORT_STRATEGY_MAP.items() if name == strategy]
            for port in matched:
                name = PORT_STRATEGY_MAP.get(port, str(port))
                try:
                    r = requests.post(f"http://127.0.0.1:{port}/api/stop", timeout=5)
                    outcomes[name] = {'ok': r.status_code == 200}
                except Exception as e:
                    outcomes[name] = {'ok': False, 'error': str(e)}
            return jsonify({'results': outcomes})

        @self.app.route('/api/strategies/reset/<strategy>', methods=['POST'])
        def api_reset_strategy(strategy):
            outcomes = {}
            matched = [p for p, name in PORT_STRATEGY_MAP.items() if name == strategy]
            for port in matched:
                name = PORT_STRATEGY_MAP.get(port, str(port))
                try:
                    r = requests.post(f"http://127.0.0.1:{port}/api/reset", timeout=5)
                    outcomes[name] = {'ok': r.status_code == 200}
                except Exception as e:
                    outcomes[name] = {'ok': False, 'error': str(e)}
            return jsonify({'results': outcomes})

        @self.app.route('/api/strategies/exit-policy', methods=['POST'])
        def api_set_exit_policy_all():
            from flask import request
            payload = {}
            try:
                payload = request.get_json(force=False, silent=True) or {}
            except Exception:
                payload = {}
            policy = (payload.get('exit_policy') or request.args.get('exit_policy') or '').strip().lower()
            allowed = ['hybrid', 'per_position_only', 'portfolio_only', 'fixed_target', 'time_only']
            outcomes = {}
            for port in self.source_ports:
                name = PORT_STRATEGY_MAP.get(port, str(port))
                try:
                    r = requests.post(f"http://127.0.0.1:{port}/api/config/exit_policy", json={'exit_policy': policy}, timeout=5)
                    ok = (r.status_code == 200)
                    ep = None
                    try:
                        ep = (r.json() or {}).get('exit_policy')
                    except Exception:
                        ep = None
                    outcomes[name] = {'ok': ok, 'exit_policy': ep}
                except Exception as e:
                    outcomes[name] = {'ok': False, 'error': str(e)}
            return jsonify({'results': outcomes, 'requested': policy, 'allowed': allowed})

        @self.app.route('/api/config/update', methods=['POST'])
        def api_update_config():
            from flask import request
            try:
                payload = request.get_json(force=True) or {}
            except Exception:
                payload = {}
            cfg_path = os.path.join(os.getcwd(), 'config', 'config.json')
            cur = {}
            try:
                with open(cfg_path, 'r') as f:
                    cur = json.load(f)
            except Exception:
                cur = {}
            changed = {}
            for k in ['capital', 'enabled_strategies', 'strategy_allocations', 'nse_capital', 'mcx_capital']:
                if k in payload and payload[k] is not None:
                    cur[k] = payload[k]
                    changed[k] = payload[k]
            try:
                os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
                with open(cfg_path, 'w') as f:
                    json.dump(cur, f, indent=4)
            except Exception:
                return jsonify({'ok': False, 'error': 'write_failed'}), 500
            return jsonify({'ok': True, 'changed': changed, 'config': cur})

        @self.app.route('/api/aggregate_state')
        def api_aggregate_state():
            now_ts = time.time()
            states = []
            for p in self.source_ports:
                st = fetch_state(p, timeout=5.0, strategy_key=PORT_STRATEGY_MAP.get(p))
                if not st:
                    last = self.last_states_by_port.get(p)
                    seen = self.last_seen_at_port.get(p, 0)
                    if last and (now_ts - seen) <= self.state_ttl_seconds:
                        st = last
                else:
                    self.last_states_by_port[p] = st
                    self.last_seen_at_port[p] = now_ts
                states.append(st)
            agg = merge_states([s for s in states if s])
            sources = []
            instances = []
            for port, st in zip(self.source_ports, states):
                s = (st or {}).get('summary', {})
                sources.append({
                    'port': port,
                    'url': f"http://0.0.0.0:{port}/",
                    'strategy': s.get('strategy', 'UNKNOWN'),
                    'health_status': s.get('health_status', 'UNKNOWN')
                })
                init_cap = float(s.get('initial_capital', 0) or 0)
                port_val = float(s.get('portfolio_value', 0) or 0)
                total_pnl = port_val - init_cap
                pnl_pct = ((port_val - init_cap) / init_cap * 100.0) if init_cap else 0.0
                instances.append({
                    'port': port,
                    'url': f"http://0.0.0.0:{port}/",
                    'strategy': s.get('strategy', 'UNKNOWN'),
                    'exit_policy': s.get('exit_policy', 'UNKNOWN'),
                    'health_status': s.get('health_status', 'UNKNOWN'),
                    'initial_capital': init_cap,
                    'portfolio_value': port_val,
                    'total_pnl': total_pnl,
                    'total_pnl_pct': pnl_pct,
                    'active_trades': int(s.get('active_trades', 0) or 0),
                    'signals_found': int(s.get('signals_found', 0) or 0),
                    'trades_executed': int(s.get('trades_executed', 0) or 0),
                    'api_calls': int(s.get('api_calls', 0) or 0),
                    'journal': s.get('journal', {})
                })
            agg['sources'] = sources
            agg['instance_breakdown'] = instances
            try:
                # Enrich strategy breakdown with source mapping and consolidated health
                sb = agg.get('strategy_breakdown', [])
                for entry in sb:
                    strat = entry.get('strategy')
                    strat_sources = [src for src in sources if src.get('strategy') == strat]
                    entry['sources'] = strat_sources
                    entry['health_status'] = 'HEALTHY'
                    if any(src.get('health_status') != 'HEALTHY' for src in strat_sources):
                        entry['health_status'] = 'MIXED'
                agg['strategy_breakdown'] = sb
            except Exception:
                pass
            try:
                sigs = list(agg.get('signals', []) or [])
                if self.signal_buffer:
                    sigs = (sigs + self.signal_buffer[-50:])[-50:]
                agg['signals'] = sigs
            except Exception:
                pass
            return jsonify(agg)

        @self.app.route('/api/close_position', methods=['POST'])
        def api_close_position():
            try:
                from flask import request
                payload = request.get_json(force=True) or {}
                symbol = (payload.get('symbol') or '').strip()
                strike = str(payload.get('strike') or payload.get('strike_price') or '').strip()
                strategy = (payload.get('strategy') or '').strip()
                # Resolve port from strategy or search states
                target_ports = []
                if strategy:
                    target_ports = [p for p, name in PORT_STRATEGY_MAP.items() if name == strategy]
                if not target_ports:
                    target_ports = list(self.source_ports)
                # Try each port for a matching position
                for port in target_ports:
                    try:
                        st = fetch_state(port, timeout=3.0, strategy_key=PORT_STRATEGY_MAP.get(port))
                        positions = (st or {}).get('positions', [])
                        matched = None
                        for pos in positions:
                            if str(pos.get('symbol','')).strip() == symbol and str(pos.get('strike_price','')).strip() == strike:
                                matched = pos
                                break
                        if matched:
                            try:
                                r = requests.post(f"http://127.0.0.1:{port}/api/close_position", json={'symbol': symbol, 'strike': matched.get('strike_price')}, timeout=5)
                                ok = (r.status_code == 200)
                            except Exception as e:
                                ok = False
                            break
                    except Exception:
                        continue
                # Return updated aggregate state
                states = [fetch_state(p, strategy_key=PORT_STRATEGY_MAP.get(p)) for p in self.source_ports]
                agg = merge_states([s for s in states if s])
                try:
                    self.socketio.emit('dashboard_update', agg)
                except Exception:
                    pass
                return jsonify(agg)
            except Exception:
                return jsonify({'error': 'close_failed'}), 500

        @self.socketio.on('connect')
        def on_connect():
            try:
                now_ts = time.time()
                states = []
                for p in self.source_ports:
                    st = fetch_state(p, timeout=5.0, strategy_key=PORT_STRATEGY_MAP.get(p))
                    if not st:
                        last = self.last_states_by_port.get(p)
                        seen = self.last_seen_at_port.get(p, 0)
                        if last and (now_ts - seen) <= self.state_ttl_seconds:
                            st = last
                    else:
                        self.last_states_by_port[p] = st
                        self.last_seen_at_port[p] = now_ts
                    states.append(st)
                agg = merge_states([s for s in states if s])
                sources = []
                instances = []
                for port, st in zip(self.source_ports, states):
                    s = (st or {}).get('summary', {})
                    sources.append({
                        'port': port,
                        'url': f"http://0.0.0.0:{port}/",
                        'strategy': s.get('strategy', 'UNKNOWN'),
                        'health_status': s.get('health_status', 'UNKNOWN')
                    })
                    init_cap = float(s.get('initial_capital', 0) or 0)
                    port_val = float(s.get('portfolio_value', 0) or 0)
                    total_pnl = port_val - init_cap
                    pnl_pct = ((port_val - init_cap) / init_cap * 100.0) if init_cap else 0.0
                    instances.append({
                        'port': port,
                        'url': f"http://0.0.0.0:{port}/",
                        'strategy': s.get('strategy', 'UNKNOWN'),
                        'health_status': s.get('health_status', 'UNKNOWN'),
                        'initial_capital': init_cap,
                        'portfolio_value': port_val,
                        'total_pnl': total_pnl,
                        'total_pnl_pct': pnl_pct,
                        'active_trades': int(s.get('active_trades', 0) or 0),
                        'signals_found': int(s.get('signals_found', 0) or 0),
                        'trades_executed': int(s.get('trades_executed', 0) or 0),
                        'api_calls': int(s.get('api_calls', 0) or 0),
                        'journal': s.get('journal', {})
                    })
                agg['sources'] = sources
                agg['instance_breakdown'] = instances
                try:
                    sb = agg.get('strategy_breakdown', [])
                    for entry in sb:
                        strat = entry.get('strategy')
                        strat_sources = [src for src in sources if src.get('strategy') == strat]
                        entry['sources'] = strat_sources
                        entry['health_status'] = 'HEALTHY'
                        if any(src.get('health_status') != 'HEALTHY' for src in strat_sources):
                            entry['health_status'] = 'MIXED'
                    agg['strategy_breakdown'] = sb
                except Exception:
                    pass
                self.socketio.emit('dashboard_update', agg)
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
                now_ts = time.time()
                states = []
                for p in self.source_ports:
                    st = fetch_state(p, timeout=5.0, strategy_key=PORT_STRATEGY_MAP.get(p))
                    if not st:
                        last = self.last_states_by_port.get(p)
                        seen = self.last_seen_at_port.get(p, 0)
                        if last and (now_ts - seen) <= self.state_ttl_seconds:
                            st = last
                    else:
                        self.last_states_by_port[p] = st
                        self.last_seen_at_port[p] = time.time()
                    states.append(st)
                agg = merge_states([s for s in states if s])
                sources = []
                instances = []
                for port, st in zip(self.source_ports, states):
                    s = (st or {}).get('summary', {})
                    sources.append({
                        'port': port,
                        'url': f"http://0.0.0.0:{port}/",
                        'strategy': s.get('strategy', 'UNKNOWN'),
                        'health_status': s.get('health_status', 'UNKNOWN')
                    })
                    init_cap = float(s.get('initial_capital', 0) or 0)
                    port_val = float(s.get('portfolio_value', 0) or 0)
                    total_pnl = port_val - init_cap
                    pnl_pct = ((port_val - init_cap) / init_cap * 100.0) if init_cap else 0.0
                    instances.append({
                        'port': port,
                        'url': f"http://0.0.0.0:{port}/",
                        'strategy': s.get('strategy', 'UNKNOWN'),
                        'health_status': s.get('health_status', 'UNKNOWN'),
                        'initial_capital': init_cap,
                        'portfolio_value': port_val,
                        'total_pnl': total_pnl,
                        'total_pnl_pct': pnl_pct,
                        'active_trades': int(s.get('active_trades', 0) or 0),
                        'signals_found': int(s.get('signals_found', 0) or 0),
                        'trades_executed': int(s.get('trades_executed', 0) or 0),
                        'api_calls': int(s.get('api_calls', 0) or 0),
                        'journal': s.get('journal', {})
                    })
                agg['sources'] = sources
                agg['instance_breakdown'] = instances
                try:
                    sb = agg.get('strategy_breakdown', [])
                    for entry in sb:
                        strat = entry.get('strategy')
                        strat_sources = [src for src in sources if src.get('strategy') == strat]
                        entry['sources'] = strat_sources
                        entry['health_status'] = 'HEALTHY'
                        if any(src.get('health_status') != 'HEALTHY' for src in strat_sources):
                            entry['health_status'] = 'MIXED'
                    agg['strategy_breakdown'] = sb
                except Exception:
                    pass
                self.socketio.emit('dashboard_update', agg)
            except Exception:
                pass
            
        def poller():
            while True:
                try:
                    now_ts = time.time()
                    states = []
                    for p in self.source_ports:
                        st = fetch_state(p, timeout=5.0, strategy_key=PORT_STRATEGY_MAP.get(p))
                        if not st:
                            last = self.last_states_by_port.get(p)
                            seen = self.last_seen_at_port.get(p, 0)
                            if last and (now_ts - seen) <= self.state_ttl_seconds:
                                st = last
                        else:
                            self.last_states_by_port[p] = st
                            self.last_seen_at_port[p] = now_ts
                        states.append(st)
                    agg = merge_states([s for s in states if s])
                    sources = []
                    for port, st in zip(self.source_ports, states):
                        s = (st or {}).get('summary', {})
                        sources.append({
                            'port': port,
                            'url': f"http://0.0.0.0:{port}/",
                            'strategy': s.get('strategy', 'UNKNOWN'),
                            'health_status': s.get('health_status', 'UNKNOWN')
                        })
                    agg['sources'] = sources
                    try:
                        sb = agg.get('strategy_breakdown', [])
                        for entry in sb:
                            strat = entry.get('strategy')
                            strat_sources = [src for src in sources if src.get('strategy') == strat]
                            entry['sources'] = strat_sources
                            entry['health_status'] = 'HEALTHY'
                            if any(src.get('health_status') != 'HEALTHY' for src in strat_sources):
                                entry['health_status'] = 'MIXED'
                        agg['strategy_breakdown'] = sb
                    except Exception:
                        pass

                    # Emit trade events by diffing positions and closed trades per port
                    try:
                        for port, st in zip(self.source_ports, states):
                            if not st: 
                                continue
                            positions = st.get('positions', []) or []
                            closed = st.get('closed_trades', []) or []
                            # Build signature set for positions
                            sigs = set()
                            for p in positions:
                                key = f"{p.get('symbol','')}-{p.get('option_type','')}-{p.get('strike_price')}-{p.get('entry_time','')}-{p.get('order_id','')}"
                                sigs.add(key)
                            prev_sigs = self.prev_positions_by_port.get(port, set())
                            # New executions = sigs - prev_sigs
                            new_exec_sigs = sigs - prev_sigs
                            if new_exec_sigs:
                                for p in positions:
                                    key = f"{p.get('symbol','')}-{p.get('option_type','')}-{p.get('strike_price')}-{p.get('entry_time','')}-{p.get('order_id','')}"
                                    if key in new_exec_sigs:
                                        payload = {
                                            'symbol': p.get('symbol'),
                                            'option_type': p.get('option_type'),
                                            'strike': p.get('strike_price'),
                                            'premium': p.get('entry_premium'),
                                            'quantity': p.get('quantity'),
                                            'lot_size': p.get('lot_size'),
                                            'entry_time': p.get('entry_time'),
                                            'order_id': p.get('order_id'),
                                            'source_port': port,
                                            'strategy': (st.get('summary') or {}).get('strategy')
                                        }
                                        self.socketio.emit('trade_executed', payload)
                            self.prev_positions_by_port[port] = sigs

                            # Closed trades diff by length
                            prev_cnt = self.prev_closed_count_by_port.get(port, 0)
                            cur_cnt = len(closed)
                            if cur_cnt > prev_cnt:
                                delta = cur_cnt - prev_cnt
                                new_closed = closed[-delta:]
                                for ct in new_closed:
                                    payload = {
                                        'symbol': ct.get('symbol'),
                                        'option_type': ct.get('option_type'),
                                        'strike': ct.get('strike_price'),
                                        'entry_price': ct.get('entry_price'),
                                        'exit_price': ct.get('exit_price'),
                                        'quantity': ct.get('quantity'),
                                        'lot_size': ct.get('lot_size'),
                                        'pnl': ct.get('pnl'),
                                        'reason': ct.get('reason'),
                                        'exit_time': ct.get('exit_time'),
                                        'source_port': port,
                                        'strategy': ct.get('strategy')
                                    }
                                    self.socketio.emit('trade_closed', payload)
                            self.prev_closed_count_by_port[port] = cur_cnt
                    except Exception:
                        pass
                    self.socketio.emit('dashboard_update', agg)
                except Exception:
                    pass
                try:
                    self.socketio.emit('server_heartbeat', {'ts': time.time()})
                except Exception:
                    pass
                time.sleep(3.0)

        self.thread = threading.Thread(target=poller, daemon=True)
        self.thread.start()

    def start(self):
        self.socketio.run(self.app, host='0.0.0.0', port=self.port, log_output=False, debug=False, allow_unsafe_werkzeug=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ports', type=str, default='5004')
    parser.add_argument('--port', type=int, default=5010)
    args = parser.parse_args()

    source_ports = [int(p.strip()) for p in args.ports.split(',') if p.strip()]
    app = AggregatorApp(source_ports, port=args.port)
    app.start()


if __name__ == '__main__':
    main()
