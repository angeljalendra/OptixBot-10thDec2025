import os
import shutil
import sys

def rm(path):
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.remove(path)
    except Exception:
        pass

def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(root, 'data')
    logs_dir = os.path.join(root, 'logs')

    if os.path.isdir(data_dir):
        try:
            for name in os.listdir(data_dir):
                p = os.path.join(data_dir, name)
                if name.lower().endswith('.json'):
                    rm(p)
                elif os.path.isdir(p):
                    for fn in ['portfolio.json', 'trade_journal.json']:
                        rm(os.path.join(p, fn))
        except Exception:
            pass

    if os.path.isdir(logs_dir):
        try:
            for name in os.listdir(logs_dir):
                if name.endswith('.log') or name.startswith('trading.log'):
                    rm(os.path.join(logs_dir, name))
        except Exception:
            pass

    print('Reset complete')

if __name__ == '__main__':
    main()
