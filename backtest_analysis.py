import sqlite3, math, sys

DB = r'D:\WebApps\titibet\backend\titibet.db'
con = sqlite3.connect(DB)
cur = con.cursor()

SEP = '-' * 72

def p(s): sys.stdout.write(str(s)+'\n'); sys.stdout.flush()

# ── 1. Market performance overview ──────────────────────────────────────
p('=== 1. MARKET PERFORMANCE OVERVIEW ===')
cur.execute("""
SELECT b.market_type,
  COUNT(*) total,
  SUM(CASE WHEN b.result_status='Won'  THEN 1 ELSE 0 END) won,
  SUM(CASE WHEN b.result_status='Lost' THEN 1 ELSE 0 END) lost,
  ROUND(100.0*SUM(CASE WHEN b.result_status='Won' THEN 1 ELSE 0 END)/COUNT(*),1) hit_pct,
  ROUND(SUM(b.profit_loss),2) pl
FROM tracked_bets b
WHERE b.result_status IN ('Won','Lost')
GROUP BY b.market_type ORDER BY total DESC
""")
p(f"  {'Market':<30} {'Total':<6} {'Won':<5} {'Lost':<5} {'Hit%':<7} P&L")
p('  '+SEP)
for r in cur.fetchall():
    p(f"  {r[0]:<30} {r[1]:<6} {r[2]:<5} {r[3]:<5} {r[4]:<7} {r[5]}")

# ── 2. Score profile of lost bets ────────────────────────────────────────
p('\n=== 2. SCORE PROFILE OF LOST BETS (top scores per market) ===')
markets = [
    'Home Over 0.5','Away Over 0.5','Home Over 1.5','Away Over 1.5',
    'Over 1.5','Over 2.5','Over 3.5','Over 0.5','BTTS Yes','Under 3.5','Under 2.5',
]
for mkt in markets:
    cur.execute("""
        SELECT f.home_score, f.away_score, COUNT(*) cnt
        FROM tracked_bets b JOIN fixtures f ON b.fixture_id=f.id
        WHERE b.market_type=? AND b.result_status='Lost'
          AND f.home_score IS NOT NULL AND f.away_score IS NOT NULL
        GROUP BY f.home_score, f.away_score ORDER BY cnt DESC LIMIT 8
    """, (mkt,))
    rows = cur.fetchall()
    if not rows: continue
    cur.execute("""SELECT COUNT(*) FROM tracked_bets b JOIN fixtures f ON b.fixture_id=f.id
        WHERE b.market_type=? AND b.result_status='Lost' AND f.home_score IS NOT NULL""", (mkt,))
    total = cur.fetchone()[0]
    p(f"\n  [{mkt}]  total losses: {total}")
    for hs, aw, cnt in rows:
        pct = round(100*cnt/total, 1)
        p(f"    {hs}-{aw}   {cnt:>4}x  {pct}%")

# ── 3. Flip signal opportunities ─────────────────────────────────────────
p('\n=== 3. FLIP SIGNAL OPPORTUNITIES ===')
flips = [
    ('Home Over 0.5', 'Under 3.5',          'f.home_score=0',                   'f.home_score+f.away_score<=3'),
    ('Away Over 0.5', 'Under 3.5',          'f.away_score=0',                   'f.home_score+f.away_score<=3'),
    ('Home Over 0.5', 'Away Win to Nil',    'f.home_score=0',                   'f.home_score=0 AND f.away_score>0'),
    ('Away Over 0.5', 'Home Win to Nil',    'f.away_score=0',                   'f.away_score=0 AND f.home_score>0'),
    ('Home Over 1.5', 'Home Under 1.5',     'f.home_score<=1',                  'f.home_score<=1'),
    ('Away Over 1.5', 'Away Under 1.5',     'f.away_score<=1',                  'f.away_score<=1'),
    ('Over 1.5',      'Under 1.5',          'f.home_score+f.away_score<=1',     'f.home_score+f.away_score<=1'),
    ('Over 2.5',      'Under 2.5',          'f.home_score+f.away_score<=2',     'f.home_score+f.away_score<=2'),
    ('Over 3.5',      'Under 3.5',          'f.home_score+f.away_score<=3',     'f.home_score+f.away_score<=3'),
    ('BTTS Yes',      'Under 2.5',          '(f.home_score=0 OR f.away_score=0)','f.home_score+f.away_score<=2'),
    ('BTTS Yes',      'Win to Nil',         '(f.home_score=0 OR f.away_score=0)','(f.home_score=0 OR f.away_score=0) AND (f.home_score+f.away_score>0)'),
    ('Over 0.5',      '0-0 scoreline',      'f.home_score+f.away_score=0',      'f.home_score+f.away_score=0'),
]
p(f"  {'Market Lost':<20} {'Flip':<22} {'Losses':<8} {'Flip Wins':<11} Flip%")
p('  '+SEP)
for mkt, flip, loss_f, flip_f in flips:
    cur.execute(f"""SELECT COUNT(*) FROM tracked_bets b JOIN fixtures f ON b.fixture_id=f.id
        WHERE b.market_type=? AND b.result_status='Lost'
        AND f.home_score IS NOT NULL AND ({loss_f})""", (mkt,))
    losses = cur.fetchone()[0]
    cur.execute(f"""SELECT COUNT(*) FROM tracked_bets b JOIN fixtures f ON b.fixture_id=f.id
        WHERE b.market_type=? AND b.result_status='Lost'
        AND f.home_score IS NOT NULL AND ({flip_f})""", (mkt,))
    wins = cur.fetchone()[0]
    if not losses: continue
    p(f"  {mkt:<20} -> {flip:<22} {losses:<8} {wins:<11} {round(100*wins/losses,1)}%")

# ── 4. BTTS Yes loss breakdown ────────────────────────────────────────────
p('\n=== 4. BTTS YES LOSSES — score breakdown ===')
cur.execute("""
    SELECT f.home_score, f.away_score, f.home_score+f.away_score total, COUNT(*) cnt
    FROM tracked_bets b JOIN fixtures f ON b.fixture_id=f.id
    WHERE b.market_type='BTTS Yes' AND b.result_status='Lost'
      AND f.home_score IS NOT NULL
    GROUP BY f.home_score, f.away_score ORDER BY cnt DESC LIMIT 12
""")
p(f"  {'Score':<8} {'Total G':<10} Count")
for r in cur.fetchall():
    p(f"  {r[0]}-{r[1]}    {r[2]}          {r[3]}")

# ── 5. Over 2.5 loss breakdown ────────────────────────────────────────────
p('\n=== 5. OVER 2.5 LOSSES — total goals breakdown ===')
cur.execute("""
    SELECT f.home_score+f.away_score total,
      COUNT(*) cnt,
      SUM(CASE WHEN f.home_score=0 OR f.away_score=0 THEN 1 ELSE 0 END) one_nil,
      SUM(CASE WHEN f.home_score>0 AND f.away_score>0 THEN 1 ELSE 0 END) both_scored
    FROM tracked_bets b JOIN fixtures f ON b.fixture_id=f.id
    WHERE b.market_type='Over 2.5' AND b.result_status='Lost' AND f.home_score IS NOT NULL
    GROUP BY total ORDER BY total
""")
p(f"  {'Total G':<10} {'Count':<8} {'One nil':<10} Both scored")
for r in cur.fetchall():
    p(f"  {r[0]:<10} {r[1]:<8} {r[2]:<10} {r[3]}")

# ── 6. Over 1.5 loss breakdown ────────────────────────────────────────────
p('\n=== 6. OVER 1.5 LOSSES — score breakdown ===')
cur.execute("""
    SELECT f.home_score, f.away_score, COUNT(*) cnt
    FROM tracked_bets b JOIN fixtures f ON b.fixture_id=f.id
    WHERE b.market_type='Over 1.5' AND b.result_status='Lost' AND f.home_score IS NOT NULL
    GROUP BY f.home_score, f.away_score ORDER BY cnt DESC LIMIT 10
""")
cur2 = con.cursor()
cur2.execute("SELECT COUNT(*) FROM tracked_bets b JOIN fixtures f ON b.fixture_id=f.id WHERE b.market_type='Over 1.5' AND b.result_status='Lost' AND f.home_score IS NOT NULL")
o15_tot = cur2.fetchone()[0]
p(f"  Total Over 1.5 losses: {o15_tot}")
for hs, aw, cnt in cur.fetchall():
    p(f"  {hs}-{aw}   {cnt}x  {round(100*cnt/o15_tot,1)}%")

# ── 7. Summary: high-value flip rates ────────────────────────────────────
p('\n=== 7. SUMMARY — HIGHEST VALUE FLIP RATES ===')
summary = [
    ('Home Over 0.5 lost', 'Under 3.5',       'Home Over 0.5', 'f.home_score=0 AND f.home_score+f.away_score<=3'),
    ('Away Over 0.5 lost', 'Under 3.5',       'Away Over 0.5', 'f.away_score=0 AND f.home_score+f.away_score<=3'),
    ('Over 3.5 lost',      'Under 3.5',       'Over 3.5',      'f.home_score+f.away_score<=3'),
    ('Over 2.5 lost',      'Under 2.5',       'Over 2.5',      'f.home_score+f.away_score<=2'),
    ('Over 1.5 lost',      'Under 1.5',       'Over 1.5',      'f.home_score+f.away_score<=1'),
    ('BTTS Yes lost',      'BTTS No/Win2Nil', 'BTTS Yes',      '(f.home_score=0 OR f.away_score=0)'),
    ('Home Over 0.5 lost', 'Away Win to Nil', 'Home Over 0.5', 'f.home_score=0 AND f.away_score>0'),
    ('Away Over 0.5 lost', 'Home Win to Nil', 'Away Over 0.5', 'f.away_score=0 AND f.home_score>0'),
]
for label, flip, mkt, filt in summary:
    cur.execute(f"""SELECT COUNT(*), SUM(CASE WHEN {filt} THEN 1 ELSE 0 END)
        FROM tracked_bets b JOIN fixtures f ON b.fixture_id=f.id
        WHERE b.market_type=? AND b.result_status='Lost' AND f.home_score IS NOT NULL""", (mkt,))
    tot, wins = cur.fetchone()
    wins = wins or 0
    if not tot: continue
    pct = round(100*wins/tot, 1)
    flag = ' <<<' if pct >= 70 else ''
    p(f"  {label:<22} -> {flip:<22} {wins}/{tot} = {pct}%{flag}")

con.close()
p('\nDONE')
