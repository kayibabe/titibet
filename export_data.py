"""
Exports users and tracked_bets from the local DB as SQL INSERT statements,
then prints a base64-encoded string you can paste into the VM via SSH.

Run: python export_data.py
"""
import base64
import sqlite3
from pathlib import Path

DB = Path("backend/titibet.db")
OUT = Path("seed_data.sql")

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

lines = ["BEGIN TRANSACTION;"]

# Date filter for large tables — last 90 days only
DATE_FILTERED = {
    "fixtures": "WHERE kickoff_at >= date('now', '-30 days')",
    "signals":  "WHERE fixture_id IN (SELECT id FROM fixtures WHERE kickoff_at >= date('now', '-30 days'))",
}

for table in ["users", "tracked_bets", "fixtures", "signals"]:
    try:
        where = DATE_FILTERED.get(table, "")
        rows = conn.execute(f"SELECT * FROM {table} {where}").fetchall()
        if not rows:
            print(f"  {table}: empty, skipping")
            continue
        cols = rows[0].keys()
        col_list = ", ".join(f'"{c}"' for c in cols)
        for row in rows:
            vals = []
            for v in row:
                if v is None:
                    vals.append("NULL")
                elif isinstance(v, (int, float)):
                    vals.append(str(v))
                else:
                    vals.append("'" + str(v).replace("'", "''") + "'")
            lines.append(f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({', '.join(vals)});")
        print(f"  {table}: {len(rows)} rows exported")
    except Exception as e:
        print(f"  {table}: ERROR — {e}")

lines.append("COMMIT;")
sql = "\n".join(lines)
OUT.write_text(sql, encoding="utf-8")
print(f"\nWritten to {OUT} ({len(sql):,} bytes)")

# Also print base64 so you can paste it directly
b64 = base64.b64encode(sql.encode()).decode()
print(f"Base64 length: {len(b64)} chars\n")
print("=== RUN THIS ON THE VM ===")
print(f'python3 -c "import base64,sqlite3; sql=base64.b64decode(\'{b64}\').decode(); conn=sqlite3.connect(\'/data/titibet.db\'); conn.executescript(sql); conn.close(); print(\'done\')"')
