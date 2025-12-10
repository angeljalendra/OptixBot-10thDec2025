import os
import json
from dataclasses import dataclass


@dataclass
class APIConfig:
    API_KEY = os.getenv('KITE_API_KEY')
    API_SECRET = os.getenv('KITE_API_SECRET')
    REDIRECT_URL = os.getenv('REDIRECT_URL', 'http://localhost:5000/callback')
    REQUEST_TOKEN = os.getenv('REQUEST_TOKEN', '')
    ACCESS_TOKEN = os.getenv('ACCESS_TOKEN', '')
    BROKER_ID = os.getenv('BROKER_ID', 'zerodha')


@dataclass
class DatabaseConfig:
    DRIVER = os.getenv('DB_DRIVER', 'sqlite')
    HOST = os.getenv('DB_HOST', 'localhost')
    PORT = int(os.getenv('DB_PORT', 5432))
    USER = os.getenv('DB_USER', 'trader')
    PASSWORD = os.getenv('DB_PASSWORD', 'secure_password')
    DATABASE = os.getenv('DB_NAME', 'trading_bot')

    @property
    def CONNECTION_STRING(self):
        if self.DRIVER == 'postgresql':
            return f"postgresql://{self.USER}:{self.PASSWORD}@{self.HOST}:{self.PORT}/{self.DATABASE}"
        return f"sqlite:///trading_bot.db"


@dataclass
class RedisConfig:
    HOST = os.getenv('REDIS_HOST', 'localhost')
    PORT = int(os.getenv('REDIS_PORT', 6379))
    DB = int(os.getenv('REDIS_DB', 0))
    PASSWORD = os.getenv('REDIS_PASSWORD', None)


@dataclass
class TelegramConfig:
    TOKEN = os.getenv('TELEGRAM_TOKEN')
    CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
    ENABLED = TOKEN is not None and CHAT_ID is not None


@dataclass
class TradingConfig:
    MARKET_OPEN = 9 * 60 + 15
    MARKET_CLOSE = 15 * 60 + 30
    INITIAL_CAPITAL = float(os.getenv('INITIAL_CAPITAL', 500000))
    MAX_LOSS_PER_TRADE = 0.5
    MAX_DAILY_LOSS = 2.0
    MAX_DRAWDOWN = 10.0
    MAX_POSITIONS = 10
    MAX_EXPOSURE = 5.0
    MIN_CONFIDENCE = 8.8
    MIN_RR_RATIO = 2.0
    TRAILING_STOP_ACTIVATION = 12
    TRAILING_STOP_TRAIL = 5
    QUICK_PROFIT_TARGET = 15000
    QUICK_PROFIT_FULL = 25000
    MAX_HOLD_TIME = 15
    TRADE_TARGET_PCT = float(os.getenv('TRADE_TARGET_PCT', 15.0))


@dataclass
class StrategyConfig:
    ACTIVE_STRATEGIES = [
        'ADAPTIVE_AUTO',
        'HIGH_CONVICTION',
        'AGGRESSIVE_SCALP',
        'MID_TREND_SWING',
        'AGGRESSIVE_REVERSAL'
    ]
    CAPITAL_ALLOCATION = {
        'ADAPTIVE_AUTO': 40,
        'HIGH_CONVICTION': 25,
        'AGGRESSIVE_SCALP': 10,
        'MID_TREND_SWING': 15,
        'AGGRESSIVE_REVERSAL': 10
    }
    WATCHLIST = ['NIFTY', 'BANKNIFTY', 'RELIANCE', 'TCS']
    WATCHLIST = [
        'RELIANCE','TCS','INFY','HDFCBANK','ICICIBANK','SBIN','LT','ITC','HINDUNILVR','AXISBANK',
        'KOTAKBANK','MARUTI','SUNPHARMA','BHARTIARTL','TATASTEEL','ADANIENT','HCLTECH','NTPC',
        'POWERGRID','TATAMOTORS','ONGC','JSWSTEEL','BAJFINANCE','HDFCLIFE','HEROMOTOCO','ASIANPAINT',
        'ULTRACEMCO','TECHM','WIPRO','M&M','DIVISLAB','BRITANNIA'
    ]


class Settings:
    api = APIConfig()
    db = DatabaseConfig()
    redis = RedisConfig()
    telegram = TelegramConfig()
    trading = TradingConfig()
    strategies = StrategyConfig()

    PAPER_TRADING = os.getenv('PAPER_TRADING', 'True').lower() == 'true'
    DEBUG_MODE = os.getenv('DEBUG_MODE', 'False').lower() == 'true'
    BACKTESTING = os.getenv('BACKTESTING', 'False').lower() == 'true'

    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_DIR = os.getenv('LOG_DIR', './logs')

def get_config():
    default_config = {
        'mode': 'PAPER',
        'capital': 500000.0,
        'max_positions': 3,
        'min_confidence': 9.8,
        'min_rr_ratio': 2.6,
        'max_daily_loss_pct': 1.5,
        'target_profit_pct': 10.0,
        'stop_loss_pct': 10.0,
        'trade_target_pct': 15.0,
        'dashboard_port': 5024,
        'telegram_chat_id': None,
        'telegram_token': None,
        'max_signals_per_side': 1,
        'max_consecutive_losses': 2,
        'max_risk_per_trade_pct': 0.5,
        'min_win_rate_threshold': 70.0,
        'min_profit_factor_threshold': 2.0,
        'min_closed_trades_for_validation': 15,
        'max_vix': 18.0,
        'atr_sl_multiplier': 1.5,
        'exit_policy': 'hybrid',
        'time_exit_minutes': 6,
        'portfolio_tsl_activation_pct': 8.0,
        'portfolio_tsl_pullback_pct': 3.0,
        'portfolio_hard_lock_pullback_pct': 6.0,
        'partial_book_pct': 70,
        'universe_mode': 'NFO_ONLY',
        'expanded_universe_size': 50,
        'tick_size_opt': 0.05,
        'tick_size_eq': 0.05,
        'opt_protect_pct_lt_10': 0.05,
        'opt_protect_pct_10_100': 0.03,
        'opt_protect_pct_100_500': 0.02,
        'opt_protect_pct_gt_500': 0.01,
        'eq_protect_pct_lt_100': 0.02,
        'eq_protect_pct_100_500': 0.01,
        'eq_protect_pct_gt_500': 0.005,
        'multi_strategy_mode': False,
        'enabled_strategies': ['ADAPTIVE_AUTO'],
        'strategy_allocations': {'ADAPTIVE_AUTO': 100},
        'commodity_trading_enabled': True,
        'commodity_strategies': ['COMMODITY_TREND','COMMODITY_REVERSAL','COMMODITY_SCALP'],
        'commodity_max_positions': 20,
        'commodity_min_confidence': 7.5,
        'high_precision_mode': True,
        'precision_min_confidence': 9.8,
        'precision_min_rr_ratio': 2.6,
        'precision_min_win_rate': 75.0,
        'precision_min_profit_factor': 1.8,
        'precision_max_vix': 16.0,
        'precision_max_signals_per_side': 1,
        'precision_cooldown_minutes': 30,
        'precision_max_correlation': 0.5
    }

    try:
        base_path = os.path.dirname(__file__)
        cfg_path = os.path.join(base_path, 'config.json')
        if os.path.exists(cfg_path):
            with open(cfg_path, 'r') as f:
                user_cfg = json.load(f)
                default_config.update(user_cfg)
    except Exception:
        pass

    env_map = {
        'capital': 'CAPITAL',
        'mode': 'TRADING_MODE',
        'max_risk_per_trade_pct': 'MAX_RISK_PCT',
        'telegram_token': 'TELEGRAM_TOKEN',
        'telegram_chat_id': 'TELEGRAM_CHAT_ID',
        'target_profit_pct': 'TARGET_PROFIT_PCT',
        'stop_loss_pct': 'STOP_LOSS_PCT',
        'max_daily_loss_pct': 'MAX_DAILY_LOSS'
    }
    for k, env_k in env_map.items():
        v = os.getenv(env_k)
        if v is not None and v != '':
            try:
                default_config[k] = float(v) if k in ['capital','max_risk_per_trade_pct','target_profit_pct','stop_loss_pct','max_daily_loss_pct'] else v
            except Exception:
                default_config[k] = v

    return default_config
