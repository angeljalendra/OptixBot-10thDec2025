## What’s Likely Blocking Signals
- Confidence/R:R gates prevent surfacing and execution: see pending_reason enrichment and thresholds [trading_bot_live.py:L6739-L6796](file:///Users/anju.kumari/Documents/OptixBot%2010thDec2025/trading_bot_live.py#L6739-L6796), [trading_bot_live.py:L7158-L7184](file:///Users/anju.kumari/Documents/OptixBot%2010thDec2025/trading_bot_live.py#L7158-L7184)
- Performance gates block execution in can_execute_live (min win-rate/profit-factor): [can_execute_live](file:///Users/anju.kumari/Documents/OptixBot%2010thDec2025/trading_bot_live.py#L4666-L4695)
- Market/time gates (late_entry_window, VIX) affect signals: audit building logic [trading_bot_live.py:L915-L996](file:///Users/anju.kumari/Documents/OptixBot%2010thDec2025/trading_bot_live.py#L915-L996)
- Quote validity/spread checks skip trades if LTP invalid or spread > 2%: [trading_bot_live.py:L5456-L5478](file:///Users/anju.kumari/Documents/OptixBot%2010thDec2025/trading_bot_live.py#L5456-L5478)
- Cash/position limits stop openings in PAPER: [trading_bot_live.py:L5496-L5510](file:///Users/anju.kumari/Documents/OptixBot%2010thDec2025/trading_bot_live.py#L5496-L5510)

## Diagnostics To Confirm
- Check current state and pending reasons: GET /api/state (recent_signals → pending_reason/pending_reason_detail)
- Check thresholds and market gates: GET /api/audit (min_confidence/min_rr, vix gates)
- Verify broker connectivity and subscriptions: GET /api/kite_status, GET /api/index_quotes

## Adjustments To Generate Signals (PAPER)
1. Lower confidence/R:R temporarily via POST /api/config/validation:
   - precision_min_confidence: 6.5
   - precision_min_rr_ratio: 1.3
   - max_signals_per_side: 10
   - precision_cooldown_minutes: 5
2. Bypass performance gates for testing via POST /api/config/validation:
   - min_win_rate_threshold: 0
   - min_profit_factor_threshold: 0
3. Ensure observe_only is disabled (we’ll set it false if present in runtime params)
4. Trigger Stock-of-the-Day scan: POST /api/scan/sotd
5. Start auto scheduler (optional, to keep scanning): POST /api/start or POST /api/strategies/start-all

## Expected Result
- Signals appear with status PENDING or EXECUTED; pending reasons should disappear for items meeting thresholds
- In PAPER, at least one option position opens when premium/cash checks pass; verify in UI /groww/positions and /api/state

## Validation & Rollback
- Verify a position in /api/state and UI; confirm trade_executed event
- Gradually restore gates to intended levels (min_confidence/min_rr and performance thresholds)

## Notes
- If spread > 2% or LTP invalid, execution will skip by design [trading_bot_live.py:L5461-L5478]
- If cash < estimated trade cost or max_positions is reached, openings will skip [trading_bot_live.py:L5496-L5510]
- Late-entry and VIX gates can block signals near market close or high volatility [trading_bot_live.py:L945-L996]

If you approve, I’ll apply the config changes, trigger the SOTD scan, and verify a paper trade opens, then revert gates back to your preferred levels.