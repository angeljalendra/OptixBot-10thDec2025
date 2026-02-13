# System In/Out and Performance Diagnostics

## Overview
- Purpose: Provide a clear map of inputs, processing stages, and outputs to diagnose why hit rate is below 90%.
- Scope: Scanning, regime detection, signal scoring, risk validation, execution, and metrics.

## Inputs
- Market data provider: `api/zerodha.py` (spot/NFO/MCX) — quote mapping `api/zerodha.py:172`, NFO universe `api/zerodha.py:72`, commodities `api/zerodha.py:218`.
- Indicators: `signals/indicators.py` — RSI `signals/indicators.py:30`, MACD `signals/indicators.py:43`, Bollinger `signals/indicators.py:57`, SMA `signals/indicators.py:83`, ATR% `signals/indicators.py:93`, ADX `signals/indicators.py:122`, SMA slope `signals/indicators.py:138`, trend persistence `signals/indicators.py:149`.
- Configuration: `config/settings.py:get_config` `config/settings.py:107` — elite/precision thresholds `config/settings.py:153–161`, VIX/limits.

## Processing Pipeline
- Strategy scanning entry: `core/strategy_manager.py:21` → `scan_all_strategies` delegates to `scan_all_strategies_elite` (`core/strategy_manager.py:27`).
- Universe build: NFO and optional MCX symbols (`core/strategy_manager.py:34–46`).
- Regime detection: adaptive TRENDING/RANGE/VOLATILE (`core/strategy_manager.py:138–149`).
- Detection/scoring: `signals/detector.py:135` → builds indicators and computes bullish/bearish scores (`signals/detector.py:23`, `signals/detector.py:79`).
- Momentum boosts and gating: ADX/slope/persistence added to confidence and gate entries (`signals/detector.py:159–181`, `signals/detector.py:188–207`, `signals/detector.py:221–250`).
- Precision filtering: config-driven confidence/R:R with regime-aware tuning (`core/strategy_manager.py:58–69`).
- Risk validation: fast gates + elite fallback (`core/risk_manager.py:81–127`), plus strict elite `core/risk_manager.py:17–79`.
- Execution: bot loop prefilters batch then runs per-signal checks and executes (`core/bot.py:139–146`, `core/bot.py:145–153`).

## Outputs
- Signals: enriched dicts with confidence, R:R, direction, prices, volatility (`signals/detector.py:200–219`, `signals/detector.py:233–250`).
- Trades: executor places orders based on validated signals (`execution` module, referenced in `core/bot.py:152`).
- Metrics: bot increments counters and recomputes (`core/bot.py:167–171`). Daily metrics stub in manager (`core/strategy_manager.py:141–150`).

## Performance Checklist (Hit Rate Drivers)
- Universe quality: ensure liquid NFO/MCX symbols from provider (`api/zerodha.py:72`, `api/zerodha.py:218`).
- Indicator integrity: RSI/MACD/ATR%/Bollinger computed from sufficient series; fallback paths used only when necessary.
- Thresholds alignment: `precision_min_confidence`, `precision_min_rr_ratio`, `min_confidence`, `min_rr_ratio` tuned per regime (`core/strategy_manager.py:58–69`).
- Momentum context: ADX ≥ 18, SMA slope sign, trend persistence strengthen continuation setups.
- Diversification: correlation control in precision filter (`core/strategy_manager.py:93–136`).
- Windows: NSE vs MCX scanning windows configured in `trading_bot_live.py:607–616` (MARKET_WINDOWS) and loop gating (`trading_bot_live.py:6018`).

## Tuning Levers
- Confidence threshold: lower by 0.5 in RANGE, raise by 0.3 in VOLATILE (`core/strategy_manager.py:62–69`).
- R:R threshold: lower by 0.2 in RANGE, raise by 0.1 in VOLATILE (`core/strategy_manager.py:62–69`).
- Momentum gating: require ADX ≥ 15 or slope sign match before signal add (`signals/detector.py`).
- Price floor: detector minimum entry price (currently 50) — adjust for specific markets (`signals/detector.py:147–149`).
- Correlation ceiling: `precision_max_correlation` (`config/settings.py:161`).

## Debug Workflow
- Compile-time check: `python -m compileall .`.
- Logging sources:
  - Signals: `logs/signals.log` via `Logger('signals')` (`signals/detector.py:6`).
  - Strategies: `logs/strategies.log` (`core/strategy_manager.py:11`).
  - Risk: `logs/risk.log` (`core/risk_manager.py:5`).
  - Bot: `logs/bot.log` (`core/bot.py:17`).
- Enable debug mode: `Settings.DEBUG_MODE` and `LOG_LEVEL` in env/`config/settings.py:104`.
- Repro recipe:
  - Run scan in unified auto cycle (`trading_bot_live.py:5207–5241`) or commodity test (`scripts/test_mcx_hybrid.py:23–46`).
  - Inspect counts: signals found, executed, win/loss updates (`core/bot.py:167–171`).

## KPIs
- Hit rate: winning_trades / (winning_trades + losing_trades) (`core/bot.py:190–196`).
- Profit factor: sum winners / |sum losers| (compute from positions; add if missing).
- Average R:R: mean of `reward_risk_ratio` for executed signals.
- Throughput: signals per hour/day, trades per day.

## Known Constraints
- MACD uses close-only series; crossover logic approximates momentum (`signals/indicators.py:43–55`).
- ADX is a close-only approximation; accurate DI+/DI- requires high/low series.
- Fallback indicator paths can inflate/deflate quality; prioritize provider-backed series.

## Next Steps
- Regime-aware R:R tuning per strategy key.
- Rising ADX requirement for continuation entries.
- Cache shared attributes across validation/execution to reduce loop latency.
- Add profit factor computation to metrics export.

## Strategy Profiles
- ELITE_HIGH_CONVICTION (NSE): Trend continuation with strict confluence and momentum gating. Best in `TRENDING` regime.
- COMMODITY_TREND (MCX): Persistent commodity trends with same elite gates; runs during MCX window.

## Recommended Config (90% Target)
- `elite_mode: true`, `high_precision_mode: true`
- `max_positions: 2`, `max_signals_per_side: 1`
- `precision_min_confidence: 8.2`, `min_confidence: 8.2`
- `precision_min_rr_ratio: 2.0`, `min_rr_ratio: 2.0`
- `precision_max_correlation: 0.4`, `precision_cooldown_minutes: 45`
- `min_entry_price: 30`, `expanded_universe_size: 75`, `universe_mode: "NFO_ONLY"`
- `precision_max_vix: 16.0`, `max_vix: 18.0`, `time_exit_minutes: 6`

## Run Guide
- Prepare config: edit `config/config.json` with the recommended values.
- NSE session (elite continuation):
  - `python -m compileall .`
  - Start unified auto or your bot entry. If using `trading_bot_live.py`, set env `USE_IST=1` and run in NSE hours; the loop scans and executes elite signals.
- MCX session (commodity trend):
  - Set env `RUN_MCX_ANYTIME=1` if outside MCX hours for testing.
  - Use `scripts/test_mcx_hybrid.py` to run a commodity trend scan.
- Monitoring:
  - Check logs in `logs/strategies.log`, `logs/signals.log`, `logs/risk.log`, `logs/bot.log`.
  - Review KPIs and trade outcomes; iterate thresholds based on daily hit rate and profit factor.
