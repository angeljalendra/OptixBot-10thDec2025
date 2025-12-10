import os
import sys
import subprocess
import time
import json
from datetime import datetime, time as dtime

def main():
    repo_root = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(repo_root)

    env = dict(os.environ)
    env['MODE'] = 'PAPER'

    # Load capital and allocation config
    total_capital = 500_000
    strategy_allocations = None
    enabled_strategies = None
    try:
        cfg_path = os.path.join(repo_root, 'config', 'config.json')
        with open(cfg_path, 'r') as f:
            cfg = json.load(f)
        total_capital = int(cfg.get('capital', total_capital) or total_capital)
        strategy_allocations = cfg.get('strategy_allocations')
        enabled_strategies = cfg.get('enabled_strategies')
        try:
            single_strategy = cfg.get('single_strategy')
        except Exception:
            single_strategy = None
        try:
            nse_single_strategy = cfg.get('nse_single_strategy')
        except Exception:
            nse_single_strategy = None
        try:
            mcx_single_strategy = cfg.get('mcx_single_strategy')
        except Exception:
            mcx_single_strategy = None
    except Exception:
        cfg = {}
        single_strategy = os.getenv('SINGLE_STRATEGY')
        nse_single_strategy = os.getenv('SINGLE_NSE_STRATEGY')
        mcx_single_strategy = os.getenv('SINGLE_MCX_STRATEGY')

    # Define available strategies and their ports
    available_strategy_ports = [
        ('HIGH_CONVICTION', 5004),
        ('AGGRESSIVE_REVERSAL', 5005),
        ('MID_TREND_SWING', 5006),
        ('AGGRESSIVE_SCALP', 5007),
        ('ADAPTIVE_AUTO', 5008),
        ('COMMODITY_TREND', 5009),
        ('COMMODITY_REVERSAL', 5011),
        ('COMMODITY_SCALP', 5012)
    ]
    port_map = {s: p for s, p in available_strategy_ports}
    # Filter to enabled strategies if provided
    if enabled_strategies:
        strategy_ports = [(s, port_map[s]) for s in enabled_strategies if s in port_map]
        # Fallback to all available if none matched
        if not strategy_ports:
            strategy_ports = available_strategy_ports
    else:
        strategy_ports = available_strategy_ports
    agg_port = 5010

    def current_window() -> str:
        use_ist = os.getenv('USE_IST', '1') == '1'
        if use_ist:
            now_dt = datetime.utcnow() + timedelta(hours=5, minutes=30)
        else:
            now_dt = datetime.now()
        now = now_dt.time()
        nse_open = dtime(9, 15)
        nse_close = dtime(15, 30)
        mcx_open = dtime(15, 0)
        mcx_close = dtime(23, 55)
        if nse_open <= now <= nse_close:
            return 'NSE'
        if mcx_open <= now <= mcx_close:
            return 'MCX'
        return 'OFF'

    commodity_keys = {'COMMODITY_TREND','COMMODITY_REVERSAL','COMMODITY_SCALP'}
    adaptive_key = 'ADAPTIVE_AUTO'
    observe_nse = os.getenv('OBSERVE_NSE_STRATEGY')
    if not observe_nse:
        try:
            observe_nse = cfg.get('observer_nse_strategy')
        except Exception:
            observe_nse = None

    def select_group(keys_set) -> list:
        keys = [s for s in keys_set]
        if enabled_strategies:
            keys = [s for s in keys if s in enabled_strategies]
        return [(s, port_map[s]) for s in keys]

    def compute_allocations(selected: list, group_total: int) -> dict:
        out = {}
        if not selected:
            return out
        if strategy_allocations:
            pcts = [float(strategy_allocations.get(s, 0) or 0) for s, _ in selected]
            total_pct = sum(pcts) if sum(pcts) > 0 else 0
            if total_pct > 0:
                running = 0
                for i, (s, _) in enumerate(selected):
                    if i < len(selected) - 1:
                        amt = int(round(group_total * (float(strategy_allocations.get(s, 0) or 0) / total_pct)))
                        out[s] = amt
                        running += amt
                    else:
                        out[s] = max(0, group_total - running)
                return out
        base = group_total // len(selected)
        rem = group_total % len(selected)
        for i, (s, _) in enumerate(selected):
            out[s] = base + (1 if i < rem else 0)
        return out

    # Group capitals: allow override via env, else split equally
    nse_cap_str = os.getenv('NSE_CAPITAL')
    mcx_cap_str = os.getenv('MCX_CAPITAL')
    if not nse_cap_str:
        try:
            nse_cap_str = str(cfg.get('nse_capital')) if cfg.get('nse_capital') is not None else None
        except Exception:
            nse_cap_str = None
    if not mcx_cap_str:
        try:
            mcx_cap_str = str(cfg.get('mcx_capital')) if cfg.get('mcx_capital') is not None else None
        except Exception:
            mcx_cap_str = None
    if nse_cap_str and mcx_cap_str:
        nse_capital = int(nse_cap_str)
        mcx_capital = int(mcx_cap_str)
    else:
        nse_capital = total_capital // 2
        mcx_capital = total_capital - nse_capital

    # Build adaptive-only group
    nse_selected = []
    if adaptive_key in port_map:
        if (not enabled_strategies) or (adaptive_key in enabled_strategies):
            nse_selected = [(adaptive_key, port_map[adaptive_key])]
    if observe_nse and observe_nse in port_map:
        if (not enabled_strategies) or (observe_nse in enabled_strategies):
            nse_selected.append((observe_nse, port_map[observe_nse]))

    # Build commodity-only group
    mcx_selected_all = [(s, port_map[s]) for s in commodity_keys if s in port_map]
    if enabled_strategies:
        if ('HIGH_CONVICTION' in enabled_strategies) and not any(k in enabled_strategies for k in commodity_keys):
            enabled_strategies = list(enabled_strategies) + ['COMMODITY_TREND']
        mcx_selected = [(s, p) for (s, p) in mcx_selected_all if s in enabled_strategies]
    else:
        mcx_selected = mcx_selected_all

    agg_proc_nse = None
    agg_proc_mcx = None
    single_agg = os.getenv('SINGLE_AGGREGATOR') == '1'
    single_mode = (os.getenv('SINGLE_MODE') == '1')
    single_strategy = 'ADAPTIVE_AUTO'
    if not single_mode:
        nse_ports_csv = ",".join(str(p) for _, p in nse_selected)
        mcx_ports_csv = ",".join(str(p) for _, p in mcx_selected)
        if single_agg:
            ports_csv_both = ",".join([nse_ports_csv, mcx_ports_csv]).strip(',')
            agg_proc_nse = subprocess.Popen([sys.executable, os.path.join(repo_root, 'aggregator_dashboard.py'), '--ports', ports_csv_both, '--port', str(5010)], cwd=repo_root, env=env)
            print(f"Unified dashboard: http://0.0.0.0:5010/")
        else:
            agg_proc_nse = subprocess.Popen([sys.executable, os.path.join(repo_root, 'aggregator_dashboard.py'), '--ports', nse_ports_csv, '--port', str(5010)], cwd=repo_root, env=env)
            agg_proc_mcx = subprocess.Popen([sys.executable, os.path.join(repo_root, 'aggregator_dashboard.py'), '--ports', mcx_ports_csv, '--port', str(5013)], cwd=repo_root, env=env)
            print(f"NSE dashboard: http://0.0.0.0:5010/")
            print(f"MCX dashboard: http://0.0.0.0:5013/")
    else:
        if single_agg:
            try:
                ports_csv_both = str(port_map.get(adaptive_key, 5008))
                agg_proc_nse = subprocess.Popen([sys.executable, os.path.join(repo_root, 'aggregator_dashboard.py'), '--ports', ports_csv_both, '--port', str(5010)], cwd=repo_root, env=env)
                print(f"Unified dashboard: http://0.0.0.0:5010/")
            except Exception:
                pass
        else:
            print("Single-strategy mode: using only the strategy dashboard port; aggregator disabled.")

    procs = {}
    def start_selected(selected: list, group_total: int, observe_key: str = None):
        alloc = compute_allocations([(s,p) for (s,p) in selected if s != observe_key], group_total)
        for strat, port in selected:
            if strat in procs and procs[strat].poll() is None:
                continue
            local_env = dict(env)
            if observe_key and strat == observe_key:
                local_env['OBSERVE_ONLY'] = '1'
                local_env['CAPITAL'] = '0'
                local_env['INITIAL_CAPITAL'] = '0'
                local_env['CAPITAL_FORCE'] = '1'
                local_env['RESET_PORTFOLIO'] = '1'
            else:
                local_env['CAPITAL'] = str(alloc.get(strat, group_total))
                local_env['INITIAL_CAPITAL'] = str(alloc.get(strat, group_total))
            local_env['CAPITAL_FORCE'] = '1'
            local_env['RESET_PORTFOLIO'] = '1'
            local_env['API_QPS_LIMIT'] = '1'
            try:
                mr = cfg.get('max_risk_per_trade_pct')
                if mr is not None:
                    local_env['MAX_RISK_PCT'] = str(mr)
            except Exception:
                pass
            try:
                lmb = cfg.get('lot_multiplier_boost')
                if lmb is not None:
                    local_env['LOT_MULTIPLIER_BOOST'] = str(lmb)
            except Exception:
                pass
            cmd = [
                sys.executable,
                os.path.join(repo_root, 'trading_bot_live.py'),
                '--mode', 'PAPER',
                '--no-input',
                '--strategy', strat,
                '--port', str(port),
                '--scan-interval', '2',
                '--batch-size', '4',
                '--max-symbols', '10'
            ]
            p = subprocess.Popen(cmd, cwd=repo_root, env=local_env)
            procs[strat] = p
            alloc_print = 0 if (observe_key and strat == observe_key) else int(alloc.get(strat, 0) or 0)
            print(f"{strat} allocated capital: ₹{alloc_print:,}")
            print(f"{strat} dashboard: http://0.0.0.0:{port}/")
            time.sleep(4)

    def stop_unselected(selected: list):
        selected_keys = {s for s, _ in selected}
        for strat, p in list(procs.items()):
            if strat not in selected_keys:
                try:
                    p.terminate()
                except Exception:
                    pass
                del procs[strat]

    # Start selected strategies
    if (nse_single_strategy or os.getenv('SINGLE_NSE_STRATEGY') or mcx_single_strategy or os.getenv('SINGLE_MCX_STRATEGY')):
        ss_nse = (nse_single_strategy or os.getenv('SINGLE_NSE_STRATEGY'))
        ss_mcx = (mcx_single_strategy or os.getenv('SINGLE_MCX_STRATEGY'))
        if ss_nse and ss_nse in port_map:
            start_selected([(ss_nse, port_map[ss_nse])], nse_capital)
        if ss_mcx and ss_mcx in port_map:
            start_selected([(ss_mcx, port_map[ss_mcx])], mcx_capital)
        if (not (ss_nse and ss_nse in port_map)) and (not (ss_mcx and ss_mcx in port_map)):
            start_selected(nse_selected, nse_capital, observe_key=observe_nse)
            start_selected(mcx_selected, mcx_capital)
    elif (single_strategy or os.getenv('SINGLE_STRATEGY')):
        ss = 'ADAPTIVE_AUTO'
        if ss in port_map:
            if ss in commodity_keys:
                start_selected([(ss, port_map[ss])], mcx_capital)
            else:
                # In single-strategy mode, use total capital for ADAPTIVE_AUTO
                if ss == adaptive_key:
                    local = [(ss, port_map[ss])]
                    # temporarily wrap start_selected to use total capital for single ADAPTIVE_AUTO
                    start_selected(local, total_capital)
                else:
                    # disable observer
                    start_selected([(ss, port_map[ss])], nse_capital)
        else:
            # Fallback to normal when invalid single strategy provided - gate by window
            win = current_window()
            if win == 'NSE':
                start_selected(nse_selected, nse_capital, observe_key=observe_nse)
            elif win == 'MCX':
                start_selected(mcx_selected, mcx_capital)
    else:
        run_both = (os.getenv('RUN_BOTH') == '1')
        if run_both:
            if nse_selected:
                start_selected(nse_selected, nse_capital, observe_key=observe_nse)
            if mcx_selected:
                allow_anytime = (os.getenv('RUN_MCX_ANYTIME') == '1')
                win = current_window()
                if allow_anytime or win == 'MCX':
                    start_selected(mcx_selected, mcx_capital)
                else:
                    print('MCX gated: will start at 15:00 IST')
        else:
            win = current_window()
            if win == 'NSE':
                start_selected(nse_selected, nse_capital, observe_key=observe_nse)
            elif win == 'MCX':
                start_selected(mcx_selected, mcx_capital)
            else:
                print('Market off-hours: not starting NSE/MCX groups.')
    try:
        while True:
            time.sleep(120)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if agg_proc_nse:
                agg_proc_nse.terminate()
        except Exception:
            pass
        try:
            if agg_proc_mcx:
                agg_proc_mcx.terminate()
        except Exception:
            pass
        for p in list(procs.values()):
            try:
                p.terminate()
            except Exception:
                pass

if __name__ == '__main__':
    main()
