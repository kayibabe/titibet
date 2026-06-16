import sqlite3, sys

DB = r'D:\WebApps\titibet\backend\titibet.db'
con = sqlite3.connect(DB)
cur = con.cursor()

def p(s): sys.stdout.buffer.write((str(s)+'\n').encode('utf-8')); sys.stdout.buffer.flush()

cur.execute("SELECT MIN(event_date), MAX(event_date), COUNT(DISTINCT event_date) FROM fixtures WHERE home_score IS NOT NULL")
r = cur.fetchone()
p(f"Completed fixtures — from {r[0]} to {r[1]}, across {r[2]} dates")

cur.execute("SELECT COUNT(*) FROM fixtures WHERE home_score IS NOT NULL")
p(f"Total completed fixtures: {cur.fetchone()[0]}")

cur.execute("""
    SELECT COUNT(DISTINCT f.event_date)
    FROM fixtures f
    JOIN market_snapshots ms ON ms.fixture_id = f.id
    WHERE f.home_score IS NOT NULL
""")
p(f"Dates with BOTH scores AND market_snapshots: {cur.fetchone()[0]}")

cur.execute("""
    SELECT f.event_date, COUNT(DISTINCT f.id) fixtures, COUNT(ms.id) snapshots
    FROM fixtures f
    JOIN market_snapshots ms ON ms.fixture_id = f.id
    WHERE f.home_score IS NOT NULL
    GROUP BY f.event_date
    ORDER BY f.event_date DESC
    LIMIT 20
""")
p("\nMost recent dates with data:")
p(f"  {'Date':<14} {'Fixtures':<10} {'Snapshots'}")
for r in cur.fetchall():
    p(f"  {r[0]:<14} {r[1]:<10} {r[2]}")

con.close()
