import os
import json
import importlib.util
import sys
base = os.path.dirname(__file__)
path = os.path.join(base, 'stock-of-the-day-fno-chatgpt.py')
spec = importlib.util.spec_from_file_location('sotd', path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
trader = mod.UltimateFNOTrader(live_mode=False)
days = int(os.getenv('BACKTEST_DAYS', '3'))
max_symbols = int(os.getenv('BACKTEST_MAX_SYMBOLS', '40'))
res = trader.run_intraday_backtest(days=days, max_symbols=max_symbols)
res_sorted = sorted(res, key=lambda r: r['win_rate'], reverse=True)
top = res_sorted[:10]
avg_wr = (sum([r['win_rate'] for r in res]) / len(res)) if res else 0.0
out = {'top': top, 'summary': {'symbols_tested': len(res), 'avg_win_rate': avg_wr}}
print(json.dumps(out, indent=2))
