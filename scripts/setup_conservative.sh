#!/usr/bin/env bash

PORT="${1:-5011}"
BASE="http://localhost:${PORT}"

set -e

echo "Configuring conservative ADAPTIVE_AUTO on ${BASE}..."

curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"enable":true}' \
  "${BASE}/api/config/high_precision" | jq '.ok, .high_precision_mode'

curl -s -X POST -H 'Content-Type: application/json' \
  -d '{}' \
  "${BASE}/api/config/strategy/adaptive" | jq '{mode, active_strategy, regime}'

curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"exit_policy":"hybrid"}' \
  "${BASE}/api/strategies/exit-policy" | jq '.ok, .exit_policy'

curl -s -X POST -H 'Content-Type: application/json' \
  -d '{
    "min_win_rate_threshold": 70,
    "min_profit_factor_threshold": 2.5,
    "min_closed_trades_for_validation": 20,
    "precision_min_confidence": 9.8,
    "precision_min_rr_ratio": 2.6,
    "precision_max_vix": 18,
    "precision_max_signals_per_side": 1,
    "precision_cooldown_minutes": 30,
    "precision_max_correlation": 0.3,
    "max_consecutive_losses": 2,
    "max_risk_per_trade_pct": 0.5,
    "time_exit_minutes": 6,
    "partial_book_pct": 70
  }' \
  "${BASE}/api/config/validation" | jq '.ok, .updated'

echo "Audit snapshot:"
curl -s "${BASE}/api/audit" | jq '{strategy, exit_policy, market_regime, precision, validation, thresholds_in_effect, gates}'

echo "Done."
