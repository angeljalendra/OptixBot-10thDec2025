#!/usr/bin/env zsh
export TRADING_MODE=${TRADING_MODE:-PAPER}
PORT=${PORT:-5056}
export BROKER=${BROKER:-KITE}

ARGS=(--mode "$TRADING_MODE" --strategy SOTD_INTRADAY --port "$PORT" --scan_interval "${SCAN_INTERVAL:-3}" --batch_size "${BATCH_SIZE:-24}")
if [[ -n "${REQUEST_TOKEN}" ]]; then
  ARGS+=(--request-token "${REQUEST_TOKEN}")
fi
python trading_bot_live.py "${ARGS[@]}"
