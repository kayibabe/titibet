import sqlite3

conn = sqlite3.connect('titibet.db')
conn.row_factory = sqlite3.Row

CONSUMED = {'market_odds_ceiling', 'market_suppression', 'league_suppression', 'kelly_fraction_adj'}

rows = conn.execute(
    'SELECT id, change_type, target, proposed_value, created_at FROM learning_proposals WHERE is_active=1 ORDER BY change_type'
).fetchall()

print('Active proposals (CONSUMED = read by accumulator_generator | PHANTOM = stored but never read)')
print()
for r in rows:
    ct  = r['change_type']
    tgt = r['target'] or ''
    val = r['proposed_value']
    status = 'CONSUMED' if ct in CONSUMED else 'PHANTOM'
    print(f'  [{status}] {ct:<30} target={tgt:<40} val={val}')
    print(f'            created={r["created_at"]}  id={r["id"]}')
print()

# Check market_odds_ceiling targets vs actual market names (exact match needed)
print('market_odds_ceiling targets vs real market names:')
ceiling_targets = [r['target'] for r in rows if r['change_type'] == 'market_odds_ceiling']
real_markets = [m[0] for m in conn.execute('SELECT DISTINCT market FROM signals LIMIT 30').fetchall()]
for ct in ceiling_targets:
    match = ct in real_markets
    print(f'  target="{ct}"  -> exact match in signals? {"YES" if match else "NO -- ceiling never applied"}')

print()
# Away Over 0.5 tracked bets
print('Away Over 0.5 tracked bets:')
rows2 = conn.execute(
    'SELECT result_status, COUNT(*) n FROM tracked_bets WHERE market_type=? GROUP BY result_status',
    ('Away Over 0.5',)
).fetchall()
total_n = sum(r['n'] for r in rows2)
for r in rows2:
    print(f'  {r["result_status"]}: {r["n"]}')
print(f'  total: {total_n}')

# New market_suppression that appeared since cleanup
print()
print('All market_suppression proposals (active and inactive):')
rows3 = conn.execute(
    "SELECT id, target, is_active, created_at FROM learning_proposals WHERE change_type='market_suppression' ORDER BY id"
).fetchall()
for r in rows3:
    print(f'  id={r["id"]} target={r["target"]} active={r["is_active"]} created={r["created_at"]}')

conn.close()
