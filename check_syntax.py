import ast, sys

files = [
    r'D:\WebApps\titibet\backend\app\services\signal_engine.py',
    r'D:\WebApps\titibet\backend\app\core\config.py',
    r'D:\WebApps\titibet\backend\app\services\strategy_tracker.py',
    r'D:\WebApps\titibet\backend\app\routers\signals.py',
    r'D:\WebApps\titibet\backend\app\scheduler.py',
]
ok = True
for f in files:
    try:
        ast.parse(open(f, encoding='utf-8-sig').read())
        print(f'OK  {f.split(chr(92))[-1]}')
    except SyntaxError as e:
        print(f'ERR {f.split(chr(92))[-1]}  line {e.lineno}: {e.msg}')
        ok = False

sys.exit(0 if ok else 1)
