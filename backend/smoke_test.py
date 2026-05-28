"""
smoke_test.py — comprehensive API smoke test for TiTiBet.
Run from backend/ with:  python smoke_test.py
"""
import datetime
import json
import sys
import urllib.request
import urllib.error
import urllib.parse
from collections import Counter

BASE  = "http://localhost:8010"
today = datetime.date.today().isoformat()
results = []


def get(path, label, expect_status=200):
    url = f"{BASE}{path}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=12) as r:
            body = r.read()
            status = r.status
            try:
                data = json.loads(body)
            except Exception:
                data = body.decode()[:120]
            ok = (status == expect_status)
            results.append((label, status, None, data, ok))
            return data
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        ok = (e.code == expect_status)
        results.append((label, e.code, body[:80], None, ok))
        return None
    except Exception as e:
        results.append((label, "ERR", str(e)[:80], None, False))
        return None


def post(path, label, payload=None, expect_status=200):
    url = f"{BASE}{path}"
    body = json.dumps(payload or {}).encode()
    try:
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            resp_body = r.read()
            status = r.status
            try:
                data = json.loads(resp_body)
            except Exception:
                data = resp_body.decode()[:120]
            ok = (status == expect_status)
            results.append((label, status, None, data, ok))
            return data
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode()[:200]
        ok = (e.code == expect_status)
        results.append((label, e.code, body_txt[:80], None, ok))
        return None
    except Exception as e:
        results.append((label, "ERR", str(e)[:80], None, False))
        return None


# ── Infrastructure ─────────────────────────────────────────────────────────────
get("/health",                                            "GET /health")

# ── Signals ────────────────────────────────────────────────────────────────────
signals_data = get(f"/api/signals?date={today}",          "GET /api/signals (today, anon cap=5)")
get(f"/api/signals?date={today}&limit=3",                 "GET /api/signals (limit=3 -- silently ignored for anon)")

sig_id = None
market = None
if isinstance(signals_data, list) and signals_data:
    sig_id = signals_data[0].get("fixture_id")
    market = signals_data[0].get("market", "")
    get(f"/api/signals/{sig_id}",                             "GET /api/signals/{id}")
    mkt_enc = urllib.parse.quote(market, safe="")
    get(f"/api/signals/{sig_id}/explain?market={mkt_enc}",    "GET /api/signals/{id}/explain")
    get(f"/api/signals/{sig_id}/match-info",                  "GET /api/signals/{id}/match-info")

get("/api/signals/recommended-tickets",                       "GET /api/signals/recommended-tickets")

# Non-existent fixture returns 200+[] (correct REST for list endpoint)
get("/api/signals/999999999",                                 "GET /api/signals/nonexistent (200+[] is correct)")

# ── Analytics ──────────────────────────────────────────────────────────────────
# Unauthenticated: scoped to user_id=NULL rows -> returns 0/empty. Correct.
get("/api/analytics/summary",                                 "GET /api/analytics/summary (anon->0)")
analytics = get("/api/analytics/full",                        "GET /api/analytics/full (anon->0)")
get("/api/analytics/by-market",                               "GET /api/analytics/by-market (anon->[])")
get("/api/analytics/by-league",                               "GET /api/analytics/by-league (anon->[])")
get("/api/analytics/trend",                                   "GET /api/analytics/trend (anon->[])")
get("/api/analytics/intelligence",                            "GET /api/analytics/intelligence")
get("/api/analytics/parameter-status",                        "GET /api/analytics/parameter-status")
get("/api/analytics/probability-calibration",                 "GET /api/analytics/probability-calibration")
get("/api/analytics/staking-simulation",                      "GET /api/analytics/staking-simulation")
get("/api/analytics/model-intelligence",                      "GET /api/analytics/model-intelligence")

# ── Tracker ────────────────────────────────────────────────────────────────────
get("/api/tracker/bets",                                      "GET /api/tracker/bets (anon->[])")
# Auth-gated: expect 401
get("/api/tracker/accumulators",                              "GET /api/tracker/accumulators (expect 401)", expect_status=401)
get("/api/tracker/runs",                                      "GET /api/tracker/runs (expect 401)", expect_status=401)
# Optional auth: returns anon scope (empty) — 200 is correct
get("/api/tracker/analytics",                                 "GET /api/tracker/analytics (anon->0)")
get("/api/tracker/analytics/accumulators",                    "GET /api/tracker/analytics/accumulators (anon->0)")
get("/api/tracker/analytics/model-insights",                  "GET /api/tracker/analytics/model-insights (anon)")

# ── Advisor ────────────────────────────────────────────────────────────────────
# Requires Pro/Elite — 403 for anonymous is correct tier-gate behaviour
get(f"/api/advisor?date={today}",                             "GET /api/advisor (expect 403 — tier gate)", expect_status=403)

# ── Loss analysis ──────────────────────────────────────────────────────────────
get("/api/loss-analysis/summary",                             "GET /api/loss-analysis/summary")

# ── Backtest ───────────────────────────────────────────────────────────────────
get("/api/backtest/results",                                  "GET /api/backtest/results")

# ── Auth boundary ──────────────────────────────────────────────────────────────
get("/api/auth/me",                                           "GET /api/auth/me (expect 401)", expect_status=401)

# ── Input validation (bad payload -> 422) ──────────────────────────────────────
post("/api/auth/register",                                    "POST /api/auth/register bad payload (expect 422)",
     payload={"email": "notanemail"}, expect_status=422)


# ── Print results ──────────────────────────────────────────────────────────────
passes = sum(1 for *_, ok in results if ok)
fails  = len(results) - passes

print()
print(f"{'RESULT':<7} {'ENDPOINT':<58} {'STATUS':>6}  DETAIL")
print("-" * 105)

for (label, status, err, data, ok) in results:
    tag = "PASS" if ok else "FAIL"
    if err:
        detail = f"err={err[:55]}"
    elif isinstance(data, list):
        detail = f"list[{len(data)}]"
    elif isinstance(data, dict):
        keys = list(data.keys())[:5]
        detail = f"keys={keys}"
    else:
        detail = str(data)[:55]
    print(f"[{tag}]  {label:<57} {str(status):>4}  {detail}")

print()
print(f"Results: {passes} passed, {fails} failed out of {len(results)} endpoints")

# ── Data integrity spot-checks ─────────────────────────────────────────────────
print()
print("=" * 65)
print("DATA INTEGRITY SPOT-CHECKS")
print("=" * 65)

if isinstance(signals_data, list):
    n = len(signals_data)
    has_dual    = sum(1 for s in signals_data if s.get("dual_confidence"))
    has_bayes   = sum(1 for s in signals_data if s.get("bayesian"))
    has_poisson = sum(1 for s in signals_data if s.get("poisson"))
    null_market = sum(1 for s in signals_data if not s.get("market"))
    null_odds   = sum(1 for s in signals_data if not s.get("bayesian", {}).get("best_odd"))
    null_conf   = sum(1 for s in signals_data if not s.get("dual_confidence"))
    probs_bad   = sum(
        1 for s in signals_data
        if s.get("bayesian", {}).get("prob") is not None
        and not (0 < s["bayesian"]["prob"] < 1)
    )
    markets = Counter(s.get("market") for s in signals_data)
    confs   = Counter(s.get("dual_confidence") for s in signals_data)
    agrees  = Counter(s.get("dual_agreement") for s in signals_data)

    print(f"\nSignals (anon free-tier; 5-signal cap):")
    print(f"  Returned:              {n}")
    print(f"  dual_confidence:       {has_dual}/{n}  {'OK' if has_dual == n else 'WARN'}")
    print(f"  bayesian sub-doc:      {has_bayes}/{n}  {'OK' if has_bayes == n else 'WARN'}")
    print(f"  poisson sub-doc:       {has_poisson}/{n}  {'OK' if has_poisson == n else 'WARN'}")
    print(f"  Null market:           {null_market}   {'OK' if null_market == 0 else 'FAIL'}")
    print(f"  Null odds:             {null_odds}   {'OK' if null_odds == 0 else 'FAIL'}")
    print(f"  Null dual_confidence:  {null_conf}   {'OK' if null_conf == 0 else 'FAIL'}")
    print(f"  Prob out of range:     {probs_bad}   {'OK' if probs_bad == 0 else 'FAIL'}")
    print(f"  Markets: {dict(markets)}")
    print(f"  Confidence split: {dict(confs)}")
    print(f"  Agreement split:  {dict(agrees)}")

    if signals_data:
        s = signals_data[0]
        print(f"\nFirst signal required fields:")
        for field in ["fixture_id","home_team","away_team","league","country",
                      "kickoff_at","market","dual_confidence","dual_agreement",
                      "dual_quality_score","bayesian","poisson"]:
            val = s.get(field)
            status = "OK" if val is not None else "MISSING"
            snippet = str(val)[:40] if val is not None else ""
            print(f"  {field:<28} {status}  {snippet}")

# Analytics (anon scope)
print()
print("Analytics (unauthenticated — user_id=NULL scope, expect 0):")
if isinstance(analytics, dict):
    sb  = analytics.get("settled_bets", 0)
    tot = analytics.get("total_bets", 0)
    wr  = analytics.get("win_rate", 0)
    roi = analytics.get("roi", 0)
    print(f"  total={tot}  settled={sb}  win_rate={wr:.1f}%  roi={roi:+.1f}%")
    if tot == 0:
        print("  [INFO] Zero — auth-scoping working correctly for anonymous users")

# DB health
import sqlite3
conn = sqlite3.connect("titibet.db")

stale = conn.execute(
    "SELECT COUNT(*) FROM ingestion_runs WHERE (status='running' OR status IS NULL) AND ended_at IS NULL"
).fetchone()[0]
total_runs   = conn.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0]
success_runs = conn.execute("SELECT COUNT(*) FROM ingestion_runs WHERE status='success'").fetchone()[0]
error_runs   = conn.execute("SELECT COUNT(*) FROM ingestion_runs WHERE status='error'").fetchone()[0]
print()
print("Ingestion runs:")
print(f"  Total={total_runs}  Success={success_runs}  Error={error_runs}  Stale={stale}")
if stale > 0:
    print(f"  [WARN] {stale} runs stuck in 'running' — will auto-clean on next startup")
else:
    print("  [OK] No stale runs")

active_props = conn.execute(
    "SELECT change_type, target, proposed_value FROM learning_proposals WHERE is_active=1"
).fetchall()
total_props  = conn.execute("SELECT COUNT(*) FROM learning_proposals").fetchone()[0]
print()
print(f"Learning proposals (total={total_props}, active={len(active_props)}):")
for ct, tgt, val in active_props:
    print(f"  [{ct:<30}] target={str(tgt):<40} value={val}")

# Bet stats
bets = conn.execute("""
    SELECT result_status, COUNT(*) FROM tracked_bets GROUP BY result_status
""").fetchall()
print()
print("Tracked bets by status:")
total_settled = 0
for status, n in bets:
    print(f"  {str(status):<12} {n}")
    if status in ("Won", "Lost"):
        total_settled += n
if total_settled < 50:
    print(f"  [WARN] Only {total_settled} settled bets — self-learning reliability limited until 50+")
else:
    print(f"  [OK] {total_settled} settled bets — sufficient for reliable self-learning")

conn.close()

sys.exit(0 if fails == 0 else 1)
