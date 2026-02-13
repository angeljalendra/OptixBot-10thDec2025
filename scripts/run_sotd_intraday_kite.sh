#!/usr/bin/env zsh
export TRADING_MODE=${TRADING_MODE:-PAPER}
python scripts/stock-of-the-day-fno-chatgpt.py --mode intraday --data_source kite --paper true --max_symbols ${MAX_SYMBOLS:-12}
