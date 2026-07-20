import sqlite3, math, sys

DB = r'D:\WebApps\titibet\backend\titibet.db'
con = sqlite3.connect(DB)
cur = con.cursor()

def poisson_cdf(lam, k):
    if not lam or lam <= 0: return None
    total, term = 0.0, math.exp(-lam)
    for i in range(k+1):
        total += term
        if i < k: term = term * lam / (i+1)
    return min(1.0, total)

def pr(s): sys.stdout.buffer.write((str(s)+'\n').encode('utf-8')); sys.stdout.buffer.flush()

HU15_LAMBDA = 1.35
AU15_LAMBDA = 1.35
HWTN_AWAY   = 0.90
HWTN_HOME   = 1.00
AWTN_HOME   = 0.70
AWTN_AWAY   = 1.00

pr('=== FLIP SIGNAL VALIDATION ON HISTORICAL LOSSES ===\n')

# --- Home Under 1.5 flip ---
cur.execute("""
    SELECT COUNT(*),
      SUM(CASE WHEN f.home_score <= 1 THEN 1 ELSE 0 END),
      AVG(s.poisson_lambda_h)
    FROM tracked_bets b
    JOIN fixtures f ON b.fixture_id = f.id
    JOIN signals s ON s.fixture_id = f.id AND s.market = 'Home Over 1.5'
    WHERE b.market_type = 'Home Over 1.5' AND b.result_status = 'Lost'
      AND s.poisson_lambda_h IS NOT NULL AND s.poisson_lambda_h < ?
      AND f.home_score IS NOT NULL
""", (HU15_LAMBDA,))
tot, correct, avg_lh = cur.fetchone()
pr(f"Home Under 1.5 flip (lam_home < {HU15_LAMBDA}):")
pr(f"  Losses where flip fires: {tot}")
pr(f"  Home scored <=1 (wins):  {correct} ({round(100*correct/tot,1) if tot else 0}%)")
pr(f"  Avg lam_home: {round(avg_lh,3) if avg_lh else 'N/A'}")
p = poisson_cdf(avg_lh, 1)
pr(f"  Model P(home<=1): {round(p*100,1) if p else 'N/A'}%\n")

# --- Away Under 1.5 flip ---
cur.execute("""
    SELECT COUNT(*),
      SUM(CASE WHEN f.away_score <= 1 THEN 1 ELSE 0 END),
      AVG(s.poisson_lambda_a)
    FROM tracked_bets b
    JOIN fixtures f ON b.fixture_id = f.id
    JOIN signals s ON s.fixture_id = f.id AND s.market = 'Away Over 1.5'
    WHERE b.market_type = 'Away Over 1.5' AND b.result_status = 'Lost'
      AND s.poisson_lambda_a IS NOT NULL AND s.poisson_lambda_a < ?
      AND f.away_score IS NOT NULL
""", (AU15_LAMBDA,))
tot, correct, avg_la = cur.fetchone()
pr(f"Away Under 1.5 flip (lam_away < {AU15_LAMBDA}):")
pr(f"  Losses where flip fires: {tot}")
pr(f"  Away scored <=1 (wins):  {correct} ({round(100*correct/tot,1) if tot else 0}%)")
pr(f"  Avg lam_away: {round(avg_la,3) if avg_la else 'N/A'}")
p = poisson_cdf(avg_la, 1)
pr(f"  Model P(away<=1): {round(p*100,1) if p else 'N/A'}%\n")

# --- Home Win to Nil flip ---
cur.execute("""
    SELECT COUNT(*),
      SUM(CASE WHEN f.home_score > 0 AND f.away_score = 0 THEN 1 ELSE 0 END),
      AVG(s.poisson_lambda_h), AVG(s.poisson_lambda_a)
    FROM tracked_bets b
    JOIN fixtures f ON b.fixture_id = f.id
    JOIN signals s ON s.fixture_id = f.id AND s.market = 'Away Over 0.5'
    WHERE b.market_type = 'Away Over 0.5' AND b.result_status = 'Lost'
      AND s.poisson_lambda_a IS NOT NULL AND s.poisson_lambda_a < ?
      AND s.poisson_lambda_h IS NOT NULL AND s.poisson_lambda_h > ?
      AND f.home_score IS NOT NULL
""", (HWTN_AWAY, HWTN_HOME))
tot, correct, avg_lh, avg_la = cur.fetchone()
pr(f"Home Win to Nil flip (lam_away < {HWTN_AWAY}, lam_home > {HWTN_HOME}):")
pr(f"  Losses where flip fires: {tot}")
pr(f"  Home WtN wins:           {correct} ({round(100*correct/tot,1) if tot else 0}%)")
if avg_lh and avg_la:
    mp = math.exp(-avg_la) * (1.0 - math.exp(-avg_lh))
    pr(f"  Avg lam_home={round(avg_lh,3)} lam_away={round(avg_la,3)}")
    pr(f"  Model P(Home WtN): {round(mp*100,1)}%\n")
else:
    pr('')

# --- Away Win to Nil flip ---
cur.execute("""
    SELECT COUNT(*),
      SUM(CASE WHEN f.away_score > 0 AND f.home_score = 0 THEN 1 ELSE 0 END),
      AVG(s.poisson_lambda_h), AVG(s.poisson_lambda_a)
    FROM tracked_bets b
    JOIN fixtures f ON b.fixture_id = f.id
    JOIN signals s ON s.fixture_id = f.id AND s.market = 'Home Over 0.5'
    WHERE b.market_type = 'Home Over 0.5' AND b.result_status = 'Lost'
      AND s.poisson_lambda_h IS NOT NULL AND s.poisson_lambda_h < ?
      AND s.poisson_lambda_a IS NOT NULL AND s.poisson_lambda_a > ?
      AND f.home_score IS NOT NULL
""", (AWTN_HOME, AWTN_AWAY))
tot, correct, avg_lh, avg_la = cur.fetchone()
pr(f"Away Win to Nil flip (lam_home < {AWTN_HOME}, lam_away > {AWTN_AWAY}):")
pr(f"  Losses where flip fires: {tot}")
pr(f"  Away WtN wins:           {correct} ({round(100*correct/tot,1) if tot else 0}%)")
if avg_lh and avg_la:
    mp = math.exp(-avg_lh) * (1.0 - math.exp(-avg_la))
    pr(f"  Avg lam_home={round(avg_lh,3)} lam_away={round(avg_la,3)}")
    pr(f"  Model P(Away WtN): {round(mp*100,1)}%\n")
else:
    pr('')

pr('DONE')
con.close()
