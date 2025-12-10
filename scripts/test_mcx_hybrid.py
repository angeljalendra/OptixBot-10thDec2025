import os
import time
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import importlib.util
import sys
ROOT = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ROOT)
tb_path = os.path.join(ROOT, 'trading_bot_live.py')
spec = importlib.util.spec_from_file_location('trading_bot_live', tb_path)
tb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tb)
UltimateFNOTrader = tb.UltimateFNOTrader
load_config = tb.load_config


def main():
    cfg = load_config()
    dashboard = None
    trader = UltimateFNOTrader(dashboard_app=dashboard, initial_strategy_key="COMMODITY_TREND", request_token_override=None, allow_input=False)
    trader.mode = 'PAPER'
    trader.display_strategy_key = 'COMMODITY_TREND'
    trader.set_active_strategy('COMMODITY_TREND')

    start = time.time()
    trader.run_strategy_scan('COMMODITY_TREND')
    elapsed = time.time() - start

    ss = trader.scan_summary
    print("\n=== MCX Hybrid Scan Performance ===")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Elapsed: {elapsed:.2f}s")
    print(f"API calls: {ss.get('total_api_calls', 0)}")
    print(f"Symbols scanned: {ss.get('stocks_scanned_count', 0)}")
    print(f"Signals found: {ss.get('signals_found_count', 0)}")
    trader.log_final_summary()


if __name__ == '__main__':
    main()
