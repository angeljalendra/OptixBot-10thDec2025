import os
import json
import argparse

def load_journal(path):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def compute_kpis(journal):
    closed = journal.get('closed_trades', []) or []
    wins = [t for t in closed if float(t.get('pnl', 0)) > 0]
    losses = [t for t in closed if float(t.get('pnl', 0)) <= 0]
    total_trades = len(closed)
    win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0
    total_profit = sum(float(t.get('pnl', 0)) for t in wins) if wins else 0
    total_loss = -sum(float(t.get('pnl', 0)) for t in losses) if losses else 0
    if total_loss > 0:
        profit_factor = total_profit / total_loss
    else:
        profit_factor = float('inf') if total_profit > 0 else 0
    avg_win = (total_profit / len(wins)) if wins else 0
    avg_loss = (sum(float(t.get('pnl', 0)) for t in losses) / len(losses)) if losses else 0
    largest_win = max((float(t.get('pnl', 0)) for t in wins), default=0)
    largest_loss = min((float(t.get('pnl', 0)) for t in losses), default=0)
    return {
        'total_trades': total_trades,
        'profitable_trades': len(wins),
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'largest_win': largest_win,
        'largest_loss': largest_loss
    }

def list_strategy_journals(data_dir):
    result = []
    default_path = os.path.join(data_dir, 'trade_journal.json')
    if os.path.exists(default_path):
        result.append(('DEFAULT', default_path))
    try:
        for name in os.listdir(data_dir):
            d = os.path.join(data_dir, name)
            if os.path.isdir(d):
                p = os.path.join(d, 'trade_journal.json')
                if os.path.exists(p):
                    result.append((name, p))
    except Exception:
        pass
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--strategy', type=str, default=None)
    parser.add_argument('--path', type=str, default=None)
    parser.add_argument('--all', action='store_true')
    args = parser.parse_args()

    data_dir = 'data'
    targets = []

    if args.path:
        targets = [(os.path.basename(os.path.dirname(args.path)) or 'DEFAULT', args.path)]
    elif args.strategy:
        p = os.path.join(data_dir, args.strategy, 'trade_journal.json')
        targets = [(args.strategy, p)]
    elif args.all:
        targets = list_strategy_journals(data_dir)
    else:
        targets = list_strategy_journals(data_dir)

    printed = False
    for name, path in targets:
        journal = load_journal(path)
        k = compute_kpis(journal)
        print(f"Strategy: {name}")
        print(f"File: {path}")
        print(f"Trades: {k['total_trades']}")
        print(f"Profitable: {k['profitable_trades']}")
        print(f"Win Rate: {k['win_rate']:.2f}%")
        pf = k['profit_factor']
        if pf == float('inf'):
            print("Profit Factor: inf")
        else:
            print(f"Profit Factor: {pf:.2f}")
        print(f"Avg Win: {k['avg_win']:.2f}")
        print(f"Avg Loss: {k['avg_loss']:.2f}")
        print(f"Largest Win: {k['largest_win']:.2f}")
        print(f"Largest Loss: {k['largest_loss']:.2f}")
        print("")
        printed = True

    if not printed:
        print("No trade journals found.")

if __name__ == '__main__':
    main()

