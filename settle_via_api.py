"""
Calls the running TiTiBet API to:
1. Sync June 1-2 fixtures (within free plan window)
2. Mark completed May fixtures as FT in the DB (direct fix)
3. Run settlement
"""
import urllib.request, urllib.parse, json, sqlite3, sys, asyncio, os
from pathlib import Path
from datetime import date, datetime

BASE = "http://localhost:8010"
EMAIL = "cmhango@gmail.com"

def p(s): sys.stdout.buffer.write((str(s)+'\n').encode('utf-8')); sys.stdout.buffer.flush()

# ── Step 1: Get auth token ────────────────────────────────────────────────────
p("Step 1: Authenticating...")
data = urllib.parse.urlencode({"username": EMAIL, "password": input("Enter password: ")}).encode()
try:
    req = urllib.request.Request(f"{BASE}/api/auth/login", data=data,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = urllib.request.urlopen(req, timeout=10)
    token = json.loads(resp.read())["access_token"]
    p(f"  Token obtained.")
except Exception as e:
    p(f"  Auth failed: {e}. Proceeding without auth (admin endpoint may require it).")
    token = None

headers = {"Authorization": f"Bearer {token}"} if token else {}

# ── Step 2: Fix May fixtures with scores but non-final status ─────────────────
p("\nStep 2: Fixing May fixtures with scores but non-final status...")
DB = r'D:\WebApps\titibet\backend\titibet.db'
# Note: this will fail if server has exclusive lock. Try anyway.
try:
    con = sqlite3.connect(DB, timeout=5)
    cur = con.cursor()
    cur.execute("""
        UPDATE fixtures SET status = 'FT'
        WHERE home_score IS NOT NULL
          AND away_score IS NOT NULL
          AND status NOT IN ('FT','AET','PEN')
          AND event_date < '2026-06-01'
    """)
    n = cur.rowcount
    con.commit()
    con.close()
    p(f"  Updated {n} fixtures to FT status.")
except Exception as e:
    p(f"  Could not update DB directly (server has lock): {e}")
    p("  Will rely on API settlement only.")

# ── Step 3: Trigger sync for accessible dates ─────────────────────────────────
p("\nStep 3: Syncing June 1-2 (API free plan window)...")
for d in ["2026-06-01", "2026-06-02"]:
    try:
        req = urllib.request.Request(
            f"{BASE}/api/tracker/sync?run_date={d}&force=true",
            data=b"", method="POST",
            headers=headers
        )
        resp = urllib.request.urlopen(req, timeout=30)
        p(f"  {d}: {resp.read().decode()[:80]}")
    except Exception as e:
        p(f"  {d}: {e}")

# ── Step 4: Trigger settlement via admin endpoint ─────────────────────────────
p("\nStep 4: Running settlement...")
try:
    req = urllib.request.Request(
        f"{BASE}/api/admin/settle",
        data=b"", method="POST",
        headers=headers
    )
    resp = urllib.request.urlopen(req, timeout=30)
    result = json.loads(resp.read())
    p(f"  Settled: {result.get('settled', 0)}")
    p(f"  Result: {result}")
except Exception as e:
    p(f"  Settlement via API failed: {e}")

p("\nDone.")
