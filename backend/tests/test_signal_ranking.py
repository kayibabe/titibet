"""
Tests for the signal ranking logic in app/routers/signals.py.

Covers:
  * _system_rank tuple ordering — the documented 14-field priority order
  * _market_slot / _best_per_fixture deduplication behaviour

These are the invariants documented in CLAUDE.md / AGENTS.md ("keep this in
sync" contract); if a refactor changes ranking behaviour, these tests break.
"""
from app.models import Fixture, Signal
from app.routers.signals import (
    _best_per_fixture,
    _market_slot,
    _system_rank,
)


def _signal(**kw) -> Signal:
    """Signal with neutral defaults — override only what a test cares about."""
    defaults = dict(
        fixture_id=1,
        market="Over 2.5",
        bayesian_prob=0.60,
        poisson_prob=0.60,
        dual_confidence="Low",
        dual_agreement="Both",
        dual_quality_score=0.5,
        poisson_lambda_total=2.5,
        bayesian_bookmaker_count=1,
        odds_drift_pct=None,
        glicko_r_diff=None,
    )
    defaults.update(kw)
    return Signal(**defaults)


def _fx(fid: int = 1) -> Fixture:
    return Fixture(id=fid, external_fixture_id=1000 + fid, home_team="H", away_team="A")


# ---------------------------------------------------------------------------
# _system_rank — tuple shape and priority ordering
# ---------------------------------------------------------------------------

def test_rank_tuple_has_14_fields():
    assert len(_system_rank(_signal())) == 14


def test_poisson_medium_outranks_everything():
    """Position 0: Poisson Only + Medium beats even Both+High signals."""
    poisson_medium = _signal(dual_agreement="Poisson Only", dual_confidence="Medium",
                             bayesian_prob=0.55, poisson_prob=0.55)
    both_high = _signal(dual_agreement="Both", dual_confidence="High",
                        bayesian_prob=0.90, poisson_prob=0.90)
    assert _system_rank(poisson_medium) > _system_rank(both_high)


def test_confidence_outranks_agreement():
    """Position 1 (confidence) dominates position 2 (agreement)."""
    high_conf_weak_agreement = _signal(dual_confidence="High", dual_agreement="Poisson Only")
    med_conf_strong_agreement = _signal(dual_confidence="Medium", dual_agreement="Both")
    assert _system_rank(high_conf_weak_agreement) > _system_rank(med_conf_strong_agreement)


def test_high_probability_flag_at_070():
    at_threshold = _signal(bayesian_prob=0.70, poisson_prob=0.60)
    below = _signal(bayesian_prob=0.699, poisson_prob=0.60)
    assert _system_rank(at_threshold)[3] == 1
    assert _system_rank(below)[3] == 0
    assert _system_rank(at_threshold) > _system_rank(below)


def test_primary_prob_is_max_of_engines():
    sig = _signal(bayesian_prob=0.55, poisson_prob=0.65)
    assert _system_rank(sig)[4] == 0.65


def test_bookmaker_support_rank_buckets():
    assert _system_rank(_signal(bayesian_bookmaker_count=5))[5] == 2
    assert _system_rank(_signal(bayesian_bookmaker_count=3))[5] == 2
    assert _system_rank(_signal(bayesian_bookmaker_count=2))[5] == 1
    assert _system_rank(_signal(bayesian_bookmaker_count=1))[5] == 0
    assert _system_rank(_signal(bayesian_bookmaker_count=None))[5] == 0


def test_clv_market_rank_applied_from_lookup():
    sig = _signal(market="Over 2.5")
    assert _system_rank(sig, clv_ranks={"Over 2.5": 1})[6] == 1
    assert _system_rank(sig, clv_ranks={"Under 2.5": 1})[6] == 0
    assert _system_rank(sig, clv_ranks=None)[6] == 0


def test_drift_rank_requires_more_than_3pct_shortening():
    assert _system_rank(_signal(odds_drift_pct=-5.0))[7] == 1
    assert _system_rank(_signal(odds_drift_pct=-3.0))[7] == 0   # threshold is strict <
    assert _system_rank(_signal(odds_drift_pct=2.0))[7] == 0
    assert _system_rank(_signal(odds_drift_pct=None))[7] == 0


def test_dual_model_probability_flag_requires_both_engines():
    assert _system_rank(_signal(bayesian_prob=0.65, poisson_prob=0.65))[8] == 1
    assert _system_rank(_signal(bayesian_prob=0.80, poisson_prob=0.60))[8] == 0


def test_glicko_certainty_clamped_and_age_gated():
    # |diff|/400 clamped to 1.0
    assert _system_rank(_signal(glicko_r_diff=200.0))[9] == 0.5
    assert _system_rank(_signal(glicko_r_diff=-200.0))[9] == 0.5
    assert _system_rank(_signal(glicko_r_diff=800.0))[9] == 1.0
    # Stale ratings (>14 days) contribute nothing
    assert _system_rank(_signal(glicko_r_diff=200.0, glicko_rating_age_days=15))[9] == 0.0
    assert _system_rank(_signal(glicko_r_diff=200.0, glicko_rating_age_days=14))[9] == 0.5


def test_tier_rank_suppressed_to_zero():
    """Tier 3 boost was removed (negative ROI post-Jul-2) — must always be 0."""
    assert _system_rank(_signal())[10] == 0


def test_avg_prob_falls_back_to_primary_when_one_engine_missing():
    sig = _signal(bayesian_prob=0.70, poisson_prob=None)
    rank = _system_rank(sig)
    assert rank[4] == 0.70   # primary
    assert rank[11] == 0.70  # avg falls back to primary


# ---------------------------------------------------------------------------
# _market_slot — deduplication slot mapping
# ---------------------------------------------------------------------------

def test_market_slots():
    assert _market_slot("Under 2.5") == "under"
    assert _market_slot("Under 3.5") == "under"
    assert _market_slot("Home Under 1.5") == "under"
    assert _market_slot("Over 1.5") == "over"
    assert _market_slot("Over 2.5") == "over"
    assert _market_slot("Home Over 0.5") == "over_home"
    assert _market_slot("Away Over 1.5") == "over_away"
    assert _market_slot("BTTS Yes") == "other"
    assert _market_slot("Home Win") == "other"


# ---------------------------------------------------------------------------
# _best_per_fixture — one best signal per (fixture, slot)
# ---------------------------------------------------------------------------

def test_best_per_fixture_keeps_strongest_in_contested_slot():
    fx = _fx(1)
    weak = _signal(fixture_id=1, market="Over 1.5", dual_confidence="Low")
    strong = _signal(fixture_id=1, market="Over 2.5", dual_confidence="High")
    result = _best_per_fixture([(weak, fx), (strong, fx)], sort_by="system")
    assert len(result) == 1
    assert result[0][0] is strong


def test_best_per_fixture_keeps_over_and_under_separately():
    """Over and Under occupy different slots — both survive for one fixture."""
    fx = _fx(1)
    over = _signal(fixture_id=1, market="Over 2.5")
    under = _signal(fixture_id=1, market="Under 3.5")
    result = _best_per_fixture([(over, fx), (under, fx)], sort_by="system")
    assert len(result) == 2


def test_best_per_fixture_home_and_away_scoring_are_orthogonal():
    fx = _fx(1)
    home = _signal(fixture_id=1, market="Home Over 0.5")
    away = _signal(fixture_id=1, market="Away Over 0.5")
    result = _best_per_fixture([(home, fx), (away, fx)], sort_by="system")
    assert len(result) == 2


def test_best_per_fixture_separate_fixtures_never_collide():
    fx1, fx2 = _fx(1), _fx(2)
    s1 = _signal(fixture_id=1, market="Over 2.5")
    s2 = _signal(fixture_id=2, market="Over 2.5")
    result = _best_per_fixture([(s1, fx1), (s2, fx2)], sort_by="system")
    assert len(result) == 2


def test_best_per_fixture_tie_broken_by_quality_score():
    fx = _fx(1)
    low_q = _signal(fixture_id=1, market="Over 1.5", dual_quality_score=0.4)
    high_q = _signal(fixture_id=1, market="Over 1.5", dual_quality_score=0.9)
    # Identical rank tuples except quality — but quality is INSIDE the rank
    # tuple (position 12), so make everything else identical and verify the
    # higher-quality signal wins the slot.
    result = _best_per_fixture([(low_q, fx), (high_q, fx)], sort_by="system")
    assert len(result) == 1
    assert result[0][0] is high_q
