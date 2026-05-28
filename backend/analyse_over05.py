import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
c = sqlite3.connect('titibet.db')
FINAL = ('FT', 'AET', 'PEN')

# 1. Signal-level performance: Home/Away Over 0.5 by confidence and league tier
print('=== HOME/AWAY OVER 0.5 — SIGNAL OUTCOMES BY CONFIDENCE ===')
rows = c.execute(
    '''SELECT s.market, s.dual_confidence, s.dual_agreement,
       COUNT(*) as sigs,
       SUM(CASE WHEN
           (s.market='Home Over 0.5' AND f.home_score >= 1) OR
           (s.market='Away Over 0.5' AND f.away_score >= 1)
           THEN 1 ELSE 0 END) as wins,
       ROUND(AVG(s.bayesian_best_odd),3) as avg_odds,
       ROUND(AVG(s.bayesian_prob),3) as avg_prob,
       ROUND(AVG(s.dual_quality_score),4) as avg_q
       FROM signals s JOIN fixtures f ON s.fixture_id=f.id
       WHERE f.status IN (?,?,?) AND f.home_score IS NOT NULL
       AND s.market IN ('Home Over 0.5','Away Over 0.5')
       GROUP BY s.market, s.dual_confidence, s.dual_agreement
       ORDER BY s.market, s.dual_confidence''', FINAL
).fetchall()
print(f'  {"Market":15s} {"Conf":6s} {"Agreement":15s}  Sigs  Wins  Hit%   AvgOdds  AvgProb  ROI%')
for r in rows:
    hit = round(100*r[4]/r[3],1) if r[3] else 0
    roi = round((r[4]*(r[5]-1) - (r[3]-r[4]))/r[3]*100,1) if r[3] else 0
    print(f'  {r[0]:15s} {r[1]:6s} {r[2]:15s}  {r[3]:4d}  {r[4]:4d}  {hit:5.1f}%  {r[5]:.3f}   {r[6]:.3f}  {roi:+.1f}%')

# 2. By league tier
print()
print('=== HOME/AWAY OVER 0.5 — BY LEAGUE TIER ===')
rows2 = c.execute(
    '''SELECT s.market, f.league_tier,
       COUNT(*) as sigs,
       SUM(CASE WHEN
           (s.market='Home Over 0.5' AND f.home_score >= 1) OR
           (s.market='Away Over 0.5' AND f.away_score >= 1)
           THEN 1 ELSE 0 END) as wins,
       ROUND(AVG(s.bayesian_best_odd),3) as avg_odds
       FROM signals s JOIN fixtures f ON s.fixture_id=f.id
       WHERE f.status IN (?,?,?) AND f.home_score IS NOT NULL
       AND s.market IN ('Home Over 0.5','Away Over 0.5')
       GROUP BY s.market, f.league_tier
       ORDER BY s.market, f.league_tier''', FINAL
).fetchall()
print(f'  {"Market":15s}  Tier  Sigs  Wins  Hit%   ROI%')
for r in rows2:
    hit = round(100*r[3]/r[2],1) if r[2] else 0
    roi = round((r[3]*(r[4]-1) - (r[2]-r[3]))/r[2]*100,1) if r[2] else 0
    print(f'  {r[0]:15s}  T{r[1] or "?"}    {r[2]:4d}  {r[3]:4d}  {hit:5.1f}%  {roi:+.1f}%')

# 3. Tracked bets performance
print()
print('=== HOME/AWAY OVER 0.5 — TRACKED BETS ===')
rows3 = c.execute(
    '''SELECT market_type, dual_confidence,
       COUNT(*) as bets,
       SUM(CASE WHEN result_status='Won' THEN 1 ELSE 0 END) as wins,
       ROUND(AVG(odds),3) as avg_odds,
       ROUND(SUM(profit_loss),2) as pl,
       ROUND(100.0*SUM(profit_loss)/SUM(stake),1) as roi
       FROM tracked_bets
       WHERE result_status IN ('Won','Lost')
       AND market_type IN ('Home Over 0.5','Away Over 0.5')
       GROUP BY market_type, dual_confidence
       ORDER BY market_type, dual_confidence'''
).fetchall()
if rows3:
    for r in rows3:
        hit = round(100*r[3]/r[2],1)
        print(f'  {r[0]:15s} [{r[1]:6s}]  {r[2]:3d}b  {r[3]}W  {hit}% hit  avg {r[4]}  ROI {r[6]}%  P&L {r[5]}')
else:
    print('  No settled tracked bets')

# 4. Top leagues for these markets
print()
print('=== TOP LEAGUES FOR AWAY OVER 0.5 SIGNALS ===')
rows4 = c.execute(
    '''SELECT f.league, f.country, COUNT(*) as sigs,
       SUM(CASE WHEN f.away_score >= 1 THEN 1 ELSE 0 END) as wins,
       ROUND(AVG(s.bayesian_best_odd),3) as avg_odds
       FROM signals s JOIN fixtures f ON s.fixture_id=f.id
       WHERE f.status IN (?,?,?) AND f.home_score IS NOT NULL
       AND s.market='Away Over 0.5'
       GROUP BY f.league ORDER BY wins*1.0/sigs DESC, sigs DESC
       LIMIT 15''', FINAL
).fetchall()
for r in rows4:
    hit = round(100*r[3]/r[2],1)
    roi = round((r[3]*(r[4]-1)-(r[2]-r[3]))/r[2]*100,1)
    print(f'  {round(hit,0):5.0f}%  {r[0]:35s} {r[1]:15s}  {r[2]}sigs  ROI {roi:+.1f}%')

print()
print('=== TOP LEAGUES FOR HOME OVER 0.5 SIGNALS ===')
rows5 = c.execute(
    '''SELECT f.league, f.country, COUNT(*) as sigs,
       SUM(CASE WHEN f.home_score >= 1 THEN 1 ELSE 0 END) as wins,
       ROUND(AVG(s.bayesian_best_odd),3) as avg_odds
       FROM signals s JOIN fixtures f ON s.fixture_id=f.id
       WHERE f.status IN (?,?,?) AND f.home_score IS NOT NULL
       AND s.market='Home Over 0.5'
       GROUP BY f.league ORDER BY wins*1.0/sigs DESC, sigs DESC
       LIMIT 15''', FINAL
).fetchall()
for r in rows5:
    hit = round(100*r[3]/r[2],1)
    roi = round((r[3]*(r[4]-1)-(r[2]-r[3]))/r[2]*100,1)
    print(f'  {round(hit,0):5.0f}%  {r[0]:35s} {r[1]:15s}  {r[2]}sigs  ROI {roi:+.1f}%')

# 5. Current learning proposals affecting these markets
print()
print('=== LEARNING PROPOSALS AFFECTING OVER 0.5 MARKETS ===')
lp = c.execute(
    '''SELECT change_type, target, proposed_value, is_active, rationale, backtest_note, created_at
       FROM learning_proposals
       WHERE target LIKE '%Over 0.5%' OR target LIKE '%over 0.5%'
       ORDER BY created_at DESC'''
).fetchall()
for r in lp:
    active = 'ACTIVE' if r[3] else 'inactive'
    print(f'  [{active}] {r[0]:25s} | {r[1]} = {r[2]}')
    print(f'    Rationale: {r[4][:100]}')
    print(f'    Backtest:  {r[5][:120] if r[5] else "N/A"}')
    print()

# 6. Under 3.5 signal performance check
print('=== UNDER 3.5 SIGNAL PERFORMANCE (from signal records) ===')
u35 = c.execute(
    '''SELECT s.dual_confidence, COUNT(*) as sigs,
       SUM(CASE WHEN (f.home_score+f.away_score) < 3.5 THEN 1 ELSE 0 END) as wins,
       ROUND(AVG(s.bayesian_best_odd),3) as avg_odds,
       ROUND(AVG(s.bayesian_prob),3) as avg_prob
       FROM signals s JOIN fixtures f ON s.fixture_id=f.id
       WHERE f.status IN (?,?,?) AND f.home_score IS NOT NULL
       AND s.market='Under 3.5'
       GROUP BY s.dual_confidence''', FINAL
).fetchall()
if u35:
    for r in u35:
        hit = round(100*r[2]/r[1],1)
        roi = round((r[2]*(r[3]-1)-(r[1]-r[2]))/r[1]*100,1)
        print(f'  [{r[0]:6s}] {r[1]:4d}sigs  {r[2]}W  {hit}% hit  avg odds {r[3]}  avg prob {r[4]}  ROI {roi:+.1f}%')
else:
    print('  No Under 3.5 signals in DB (market currently DISABLED)')

# 7. Simulated Under 3.5 performance: what WOULD have happened if enabled?
print()
print('=== SIMULATED UNDER 3.5 PERFORMANCE (all fixtures where U3.5 would have been value) ===')
# Check fixtures where CS model would have backed Under 3.5 based on total goals
sim = c.execute(
    '''SELECT
       COUNT(*) as total_matches,
       SUM(CASE WHEN home_score+away_score < 3.5 THEN 1 ELSE 0 END) as would_win,
       ROUND(AVG(home_score+away_score),2) as avg_goals
       FROM fixtures
       WHERE home_score IS NOT NULL AND status IN (?,?,?)''', FINAL
).fetchone()
print(f'  All FT fixtures: {sim[0]} total, {sim[1]} U3.5 wins = {round(100*sim[1]/sim[0],1)}% hit rate, avg {sim[2]} goals')

# Simulated by league tier
sim2 = c.execute(
    '''SELECT league_tier,
       COUNT(*) as total_matches,
       SUM(CASE WHEN home_score+away_score < 3.5 THEN 1 ELSE 0 END) as would_win,
       ROUND(AVG(home_score+away_score),2) as avg_goals
       FROM fixtures
       WHERE home_score IS NOT NULL AND status IN (?,?,?)
       GROUP BY league_tier ORDER BY league_tier''', FINAL
).fetchall()
for r in sim2:
    hit = round(100*r[2]/r[1],1)
    print(f'  Tier {r[0]}: {hit}% U3.5 hit rate  (avg {r[3]} goals, n={r[1]})')

c.close()
print('\nDone.')
