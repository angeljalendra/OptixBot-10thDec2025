import json
import os
import sys

# Ensure project root is on sys.path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def test_confidence_achievable():
    from signals.detector import SignalDetector

    detector = SignalDetector()

    macd = {'bullish_crossover': True, 'bearish_crossover': False}
    bb = {'price_below_lower': False, 'price_above_upper': False,
          'lower': 98.0, 'middle': 100.0, 'upper': 102.0}

    conf = detector._compute_confidence(
        rsi_val=55.0,
        macd=macd,
        bb=bb,
        atr_pct=1.5,
        volume_ratio=2.0,
        direction='BULLISH'
    )

    assert conf >= 9.5


def test_mcx_window_unified():
    import trading_bot_live as bot

    mcx = bot.MARKET_WINDOWS['MCX']
    assert mcx['OPEN'] == 9 * 60
    assert mcx['CLOSE'] == 23 * 60 + 55


def test_config_no_secrets():
    # Ensure secrets are not committed in config.json
    with open('config/config.json', 'r') as f:
        cfg = json.load(f)

    assert 'telegram_token' in cfg and (cfg['telegram_token'] is None or cfg['telegram_token'] == '')
    assert 'telegram_chat_id' in cfg and (cfg['telegram_chat_id'] is None or cfg['telegram_chat_id'] == '')


def test_get_config_defaults():
    from config.settings import get_config

    cfg = get_config()
    assert float(cfg.get('min_confidence', 0)) >= 9.5
    assert int(cfg.get('expanded_universe_size', 0)) >= 50
