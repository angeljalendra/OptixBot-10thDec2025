#!/usr/bin/env zsh
export TRADING_MODE=${TRADING_MODE:-PAPER}
export CAPITAL=${CAPITAL:-100000}
python trading_bot_live.py --mode $TRADING_MODE --strategy AGGRESSIVE_SCALP --no-input --port ${PORT:-5051}
