"""
cleanup_phantom_proposals.py — one-shot cleanup of phantom and spurious proposals.

Phantoms (change_types that no downstream code consumes):
  quality_threshold, min_confidence, rule_disable, tier_suppression

Spurious:
  id=23  market_suppression: Away Over 0.5
    Away Over 0.5 has 30W / 16L = 65% WR and positive ROI — suppression is wrong.
    Root cause was the old win-rate-gap criterion; fixed in strategy_pipeline.py.

Keeping:
  id=17  market_odds_ceiling: Home/Away Over 0.5, Home/Away Over 1.5
  id=20  market_odds_ceiling: Home/Away Over 0.5
    Both are CONSUMED by accumulator_generator. Slash notation now expanded correctly.
"""
import sqlite3
from datetime import datetime

conn = sqlite3.connect('titibet.db')
conn.row_factory = sqlite3.Row

PHANTOM_TYPES = ('quality_threshold', 'min_confidence', 'rule_disable', 'tier_suppression')
SPURIOUS_IDS  = (23,)  # Away Over 0.5 market_suppression with profitable ROI

before = conn.execute('SELECT COUNT(*) FROM learning_proposals WHERE is_active=1').fetchone()[0]

# Deactivate phantom change_types
phantom_result = conn.execute(
    f"UPDATE learning_proposals SET is_active=0 WHERE change_type IN ({','.join('?' for _ in PHANTOM_TYPES)}) AND is_active=1",
    PHANTOM_TYPES
)
print(f"Deactivated {phantom_result.rowcount} phantom proposals (unknown change_types)")

# Deactivate spurious specific IDs
for sid in SPURIOUS_IDS:
    row = conn.execute('SELECT change_type, target FROM learning_proposals WHERE id=?', (sid,)).fetchone()
    if row:
        conn.execute('UPDATE learning_proposals SET is_active=0 WHERE id=?', (sid,))
        print(f"Deactivated id={sid} {row['change_type']}: {row['target']} (Away Over 0.5 has positive ROI)")
    else:
        print(f"id={sid} not found (already cleaned or never existed)")

conn.commit()

after = conn.execute('SELECT COUNT(*) FROM learning_proposals WHERE is_active=1').fetchone()[0]
print(f"\nActive proposals: {before} -> {after}")

remaining = conn.execute(
    "SELECT id, change_type, target, proposed_value FROM learning_proposals WHERE is_active=1"
).fetchall()
print("Remaining active proposals:")
for r in remaining:
    print(f"  id={r['id']} [{r['change_type']}] target={r['target']} val={r['proposed_value']}")

conn.close()
print("\nDone.")
