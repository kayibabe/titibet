import sqlite3
conn = sqlite3.connect('titibet.db')
rows = conn.execute(
    'SELECT id, change_type, target, is_active FROM learning_proposals WHERE is_active=1 ORDER BY id'
).fetchall()
print('Active proposals:')
for r in rows:
    print(f'  id={r[0]} type={r[1]} target={r[2]} active={r[3]}')
conn.close()
