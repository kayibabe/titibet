"""
backfill_performance.py
-----------------------
Reports performance of all flip signals against actual match outcomes.
Run AFTER backfill_signals.py.

Flip rule keys:
  u35_flip   — Under 3.5 from weak Over 0.5 team lambda
  hu15_flip  — Home Under 1.5 from weak home lambda
  au15_flip  — Away Under 1.5 from weak away lambda
  hwtn_flip  — Home Win to Nil from weak away lambda
  awtn_flip  — Away Win to Nil from weak home lambda
"""
import sqlite3, sys, math

DB = r'D:\WebApps\titibet\backend\titibet.db'

OUTCOME_FN = {
    'Under 3.5':     lambda h, a: (h + a) <= 3,
    'Home Under 1.5': lambda h, a: h <= 1,
    'Away Under 1.5': lambda h, a: a <= 1,
    'Home Win to Nil': lambda h, a: h > a and a == 0,
    'Away Win to Nil': lambda h, a: a > h and h == 0,
}

FLIP_KEYS = {'u35_flip', 'hu15_flip', 'au15_flip', 'hwtn_flip', 'awtn_flip'}

def p(s): sys.stdout.buffer.write((str(s)+'\n').encode('utf-8')); sys.stdout.buffer.flush()

con = sqlite3.connect(DB)
cur = con.cursor()

p('=== FLIP SIGNAL BACKTEST PERFORMANCE ===\n')

# ── 1. Overall per flip market ─────────────────────────────────────────────────
p('--- 1. OVERALL PERFORMANCE BY FLIP MARKET ---')
cur.execute("""
    SELECT s.market, s.poisson_rule_key,
      COUNT(*) total,
      SUM(CASE WHEN s.bayesian_best_odd IS NOT NULL THEN 1 ELSE 0 END) has_odds,
      AVG(s.bayesian_best_odd) avg_odds,
      AVG(s.poisson_prob) avg_model_prob,
      AVG(s.poisson_lambda_h) avg_lh,
      AVG(s.poisson_lambda_a) avg_la
    FROM signals s
    WHERE s.poisson_rule_key IN ('u35_flip','hu15_flip','au15_flip','hwtn_flip','awtn_flip')
    GROUP BY s.market, s.poisson_rule_key
    ORDER BY s.market
""")
rows = cur.fetchall()
if not rows:
    p("  No flip signals found — run backfill_signals.py first.")
else:
    p(f"  {'Market':<20} {'Rule':<12} {'Count':<7} {'HasOdds':<9} {'AvgOdds':<9} {'AvgP%':<8} {'AvgLh':<7} AvgLa")
    p('  ' + '-'*78)
    for r in rows:
        mkt, rk, tot, ho, ao, ap, alh, ala = r
        p(f"  {mkt:<20} {rk:<12} {tot:<7} {ho:<9} {round(ao,2) if ao else '-':<9} {round((ap or 0)*100,1):<8} {round(alh or 0,2):<7} {round(ala or 0,2)}")

p('')

# ── 2. Win/Loss on settled fixtures ────────────────────────────────────────────
p('--- 2. WIN/LOSS AGAINST ACTUAL SCORES ---')
cur.execute("""
    SELECT s.market, s.poisson_rule_key,
      s.bayesian_best_odd,
      s.poisson_prob,
      s.dual_confidence,
      f.home_score, f.away_score,
      f.home_team, f.away_team,
      f.event_date
    FROM signals s
    JOIN fixtures f ON s.fixture_id = f.id
    WHERE s.poisson_rule_key IN ('u35_flip','hu15_flip','au15_flip','hwtn_flip','awtn_flip')
      AND f.home_score IS NOT NULL AND f.away_score IS NOT NULL
    ORDER BY s.market, f.event_date
""")
rows = cur.fetchall()

from collections import defaultdict
stats = defaultdict(lambda: {'won':0,'lost':0,'pl':0.0,'odds':[],'dates':set()})

for mkt, rk, odds, prob, conf, hs, aws, ht, at, dt in rows:
    if hs is None or aws is None:
        continue
    fn = OUTCOME_FN.get(mkt)
    if fn is None:
        continue
    won = fn(hs, aws)
    stake = 10.0
    pl = (odds - 1) * stake if (won and odds) else (-stake if odds else 0)
    stats[mkt]['won' if won else 'lost'] += 1
    stats[mkt]['pl'] += pl
    if odds:
        stats[mkt]['odds'].append(odds)
    stats[mkt]['dates'].add(dt)

p(f"  {'Market':<20} {'Won':<6} {'Lost':<6} {'Hit%':<7} {'AvgOdds':<9} {'P&L (10u)':<12} ROI%")
p('  ' + '-'*72)
for mkt, s in sorted(stats.items()):
    tot = s['won'] + s['lost']
    if not tot: continue
    hit = round(100 * s['won'] / tot, 1)
    avg_o = round(sum(s['odds'])/len(s['odds']), 2) if s['odds'] else '-'
    roi = round(100 * s['pl'] / (tot * 10), 1)
    p(f"  {mkt:<20} {s['won']:<6} {s['lost']:<6} {hit:<7} {avg_o:<9} {round(s['pl'],1):<12} {roi}%")

p('')

# ── 3. Performance by confidence level ─────────────────────────────────────────
p('--- 3. BY CONFIDENCE LEVEL ---')
cur.execute("""
    SELECT s.market, s.dual_confidence,
      COUNT(*) total,
      f.home_score, f.away_score
    FROM signals s
    JOIN fixtures f ON s.fixture_id = f.id
    WHERE s.poisson_rule_key IN ('u35_flip','hu15_flip','au15_flip','hwtn_flip','awtn_flip')
      AND f.home_score IS NOT NULL AND f.away_score IS NOT NULL
    GROUP BY s.market, s.dual_confidence, f.home_score, f.away_score
""")
# Re-query at row level for accuracy
cur.execute("""
    SELECT s.market, s.dual_confidence, s.bayesian_best_odd,
      f.home_score, f.away_score
    FROM signals s
    JOIN fixtures f ON s.fixture_id = f.id
    WHERE s.poisson_rule_key IN ('u35_flip','hu15_flip','au15_flip','hwtn_flip','awtn_flip')
      AND f.home_score IS NOT NULL AND f.away_score IS NOT NULL
""")
rows = cur.fetchall()
conf_stats = defaultdict(lambda: defaultdict(lambda: {'won':0,'lost':0}))
for mkt, conf, odds, hs, aws in rows:
    fn = OUTCOME_FN.get(mkt)
    if fn is None: continue
    won = fn(hs, aws)
    conf_stats[mkt][conf or 'None']['won' if won else 'lost'] += 1

p(f"  {'Market':<20} {'Conf':<10} {'Won':<6} {'Lost':<6} Hit%")
p('  '+'-'*50)
for mkt in sorted(conf_stats):
    for conf in ['High','Medium','Low','None']:
        s = conf_stats[mkt].get(conf)
        if not s: continue
        tot = s['won'] + s['lost']
        hit = round(100*s['won']/tot,1) if tot else 0
        p(f"  {mkt:<20} {conf:<10} {s['won']:<6} {s['lost']:<6} {hit}%")

p('')

# ── 4. Timeline — monthly performance ──────────────────────────────────────────
p('--- 4. TIMELINE (by week) ---')
cur.execute("""
    SELECT strftime('%Y-%W', f.event_date) week,
      s.market,
      COUNT(*) total,
      f.home_score, f.away_score,
      s.bayesian_best_odd
    FROM signals s
    JOIN fixtures f ON s.fixture_id = f.id
    WHERE s.poisson_rule_key IN ('u35_flip','hu15_flip','au15_flip','hwtn_flip','awtn_flip')
      AND f.home_score IS NOT NULL AND f.away_score IS NOT NULL
    ORDER BY week, s.market
""")
# Row-level for accuracy
cur.execute("""
    SELECT strftime('%Y-%W', f.event_date) week, s.market,
      s.bayesian_best_odd, f.home_score, f.away_score
    FROM signals s
    JOIN fixtures f ON s.fixture_id = f.id
    WHERE s.poisson_rule_key IN ('u35_flip','hu15_flip','au15_flip','hwtn_flip','awtn_flip')
      AND f.home_score IS NOT NULL AND f.away_score IS NOT NULL
    ORDER BY week
""")
rows = cur.fetchall()
week_stats = defaultdict(lambda: {'won':0,'lost':0,'pl':0.0})
for week, mkt, odds, hs, aws in rows:
    fn = OUTCOME_FN.get(mkt)
    if fn is None: continue
    won = fn(hs, aws)
    stake = 10.0
    pl = (odds - 1) * stake if (won and odds) else (-stake if odds else 0)
    week_stats[week]['won' if won else 'lost'] += 1
    week_stats[week]['pl'] += pl

p(f"  {'Week':<10} {'Won':<6} {'Lost':<6} {'Hit%':<8} P&L (10u)")
p('  '+'-'*42)
running_pl = 0
for week in sorted(week_stats):
    s = week_stats[week]
    tot = s['won'] + s['lost']
    hit = round(100*s['won']/tot,1) if tot else 0
    running_pl += s['pl']
    p(f"  {week:<10} {s['won']:<6} {s['lost']:<6} {hit:<8} {round(s['pl'],1)}  (running: {round(running_pl,1)})")

p('')

# ── 5. Sample wins and losses ───────────────────────────────────────────────────
p('--- 5. SAMPLE RESULTS ---')
cur.execute("""
    SELECT s.market, f.home_team, f.away_team, f.event_date,
      f.home_score, f.away_score,
      s.bayesian_best_odd, s.dual_confidence,
      s.poisson_lambda_h, s.poisson_lambda_a
    FROM signals s
    JOIN fixtures f ON s.fixture_id = f.id
    WHERE s.poisson_rule_key IN ('u35_flip','hu15_flip','au15_flip','hwtn_flip','awtn_flip')
      AND f.home_score IS NOT NULL AND f.away_score IS NOT NULL
    ORDER BY f.event_date DESC
    LIMIT 30
""")
rows = cur.fetchall()
p(f"  {'Market':<20} {'Match':<35} {'Date':<12} {'Score':<7} {'Odds':<6} {'Result'}")
p('  '+'-'*90)
for mkt, ht, at, dt, hs, aws, odds, conf, lh, la in rows:
    fn = OUTCOME_FN.get(mkt)
    if fn is None: continue
    won = fn(hs, aws)
    match = f"{ht} vs {at}"[:33]
    result = 'WIN' if won else 'LOSS'
    p(f"  {mkt:<20} {match:<35} {dt:<12} {hs}-{aws:<5} {round(odds,2) if odds else '-':<6} {result}")

p('\nDONE')
con.close()
