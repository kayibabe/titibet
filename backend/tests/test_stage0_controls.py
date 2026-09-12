from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.auth import get_current_user, require_admin
from app.core.config import resolve_database_url
from app.core.database import get_db
from app.models import Fixture, MarketSnapshot, Signal, TrackedBet
from app.models.learning_proposal import LearningProposal
from app.services.tracking_evidence import classify_entry, tracking_rejection
from app.services.learning_suggestions import save_suggestion


@pytest.mark.asyncio
async def test_stage1_evidence_tables_are_created_and_idempotent():
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.core.migrations import run_migrations
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(__import__('app.core.database', fromlist=['Base']).Base.metadata.create_all)
    await run_migrations(engine)
    await run_migrations(engine)
    async with engine.connect() as conn:
        names = {row[0] for row in (await conn.exec_driver_sql("select name from sqlite_master where type='table'")).all()}
    assert {"provider_observations", "market_definitions", "odds_quotes"} <= names
    await engine.dispose()


def test_database_path_independent_of_launch_directory(tmp_path, monkeypatch):
    expected = Path(__file__).resolve().parents[1] / 'titibet.db'
    for cwd in (tmp_path, Path(__file__).resolve().parents[2]):
        monkeypatch.chdir(cwd)
        url = make_url(resolve_database_url('sqlite+aiosqlite:///./titibet.db?timeout=30'))
        assert Path(url.database) == expected
        assert url.query['timeout'] == '30'


@pytest.mark.parametrize('value', [
    'sqlite+aiosqlite:///:memory:', 'sqlite+aiosqlite://',
    'sqlite+aiosqlite:///file:memdb?mode=memory&cache=shared&uri=true',
    'postgresql+asyncpg://user:password@localhost/db',
])
def test_special_database_urls_preserved(value):
    assert resolve_database_url(value) == value


def test_explicit_database_path_preserved(tmp_path):
    path = tmp_path / 'separate database.db'
    url = resolve_database_url('sqlite+aiosqlite:///' + path.as_posix())
    assert Path(make_url(url).database) == path


def test_legacy_evidence_is_never_promoted_by_timing_alone():
    kickoff = datetime(2026, 9, 8, 12)
    assert classify_entry(kickoff, kickoff) == 'late_entry_unverified'
    assert classify_entry(kickoff + timedelta(days=2), kickoff) == 'late_entry_unverified'
    assert classify_entry(kickoff - timedelta(seconds=1), kickoff) == 'pre_kickoff_incomplete_evidence'
    assert classify_entry(None, kickoff) == 'timing_unknown'
    assert classify_entry(kickoff, None) == 'timing_unknown'
    cat = timezone(timedelta(hours=2))
    assert classify_entry(kickoff.replace(hour=13, tzinfo=cat), kickoff) == 'pre_kickoff_incomplete_evidence'


async def seed_signal(db):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    fx = Fixture(id=99001, external_fixture_id=99001, home_team='Home', away_team='Away', league='Premier League',
                 country='England', league_tier=1, event_date=now.date(),
                 kickoff_at=now + timedelta(hours=1), status='NS')
    sig = Signal(fixture_id=fx.id, market='Under 3.5', bayesian_best_odd=1.33,
                 bayesian_bookmaker='Test Book', bayesian_prob=0.8, poisson_prob=0.8,
                 poisson_lambda_total=2.3, dual_agreement='Both', dual_confidence='Medium',
                 dual_quality_score=0.6, is_candidate=False,
                 computed_at=now - timedelta(minutes=2))
    quote = MarketSnapshot(fixture_id=fx.id, bookmaker='Test Book',
                           market_type='Goals Over/Under', selection_name='Under 3.5',
                           odds=1.33, pulled_at=now - timedelta(minutes=3))
    db.add_all([fx, sig, quote])
    await db.commit()
    return now, fx, sig, quote


async def test_auto_tracker_inserts_real_pre_kickoff_quote_once(db):
    from app.services.auto_tracker import auto_track_date
    now, fx, sig, quote = await seed_signal(db)
    assert await tracking_rejection(db, sig, fx, now=now) is None
    assert await auto_track_date(db, fx.event_date) == 1
    assert await auto_track_date(db, fx.event_date) == 0
    bet = await db.scalar(select(TrackedBet))
    assert bet.odds == quote.odds
    assert bet.bookmaker == quote.bookmaker


@pytest.mark.parametrize('change,reason', [
    ('missing_odds', 'MISSING_QUOTE'), ('wrong_book', 'MISSING_MATCHING_QUOTE'),
    ('wrong_period', 'MISSING_MATCHING_QUOTE'), ('price_changed', 'MISSING_MATCHING_QUOTE'),
    ('late_receipt', 'QUOTE_AFTER_PREDICTION'), ('stale', 'STALE_QUOTE'),
    ('started', 'POST_KICKOFF'), ('no_kickoff', 'MISSING_KICKOFF'),
    ('live', 'FIXTURE_NOT_SCHEDULED'), ('unknown_market', 'UNSUPPORTED_MARKET_SCOPE'),
])
async def test_auto_tracker_rejects_unexecutable_evidence(db, change, reason):
    from app.services.auto_tracker import auto_track_date
    now, fx, sig, quote = await seed_signal(db)
    if change == 'missing_odds': sig.bayesian_best_odd = None
    if change == 'wrong_book': quote.bookmaker = 'Another Book'
    if change == 'wrong_period': quote.market_type = 'Goals Over/Under First Half'
    if change == 'price_changed': quote.odds = 1.31
    if change == 'late_receipt': quote.pulled_at = now - timedelta(seconds=10)
    if change == 'stale': quote.pulled_at = now - timedelta(days=1)
    if change == 'started': fx.kickoff_at = now
    if change == 'no_kickoff': fx.kickoff_at = None
    if change == 'live': fx.status = '1H'
    if change == 'unknown_market': sig.market = 'Unmapped goals'
    await db.commit()
    assert await tracking_rejection(db, sig, fx, now=now) == reason
    assert await auto_track_date(db, fx.event_date) == 0
    assert await db.scalar(select(TrackedBet.id)) is None


async def test_catchup_and_shadow_cannot_create_post_kickoff_entries(db):
    from app.services.auto_tracker import auto_track_date
    from app.services.shadow_tracker import shadow_track_date
    from app.models.signal_observation import SignalObservation
    now, fx, sig, quote = await seed_signal(db)
    fx.kickoff_at = now - timedelta(days=3)
    fx.event_date = fx.kickoff_at.date()
    fx.status = 'FT'
    sig.market = 'Home Over 0.5'
    sig.dual_confidence = 'High'
    sig.bayesian_best_odd = 2.2
    await db.commit()
    assert await auto_track_date(db, fx.event_date) == 0
    assert await shadow_track_date(db, fx.event_date) == 0
    assert await db.scalar(select(SignalObservation.id)) is None


async def test_advisor_does_not_borrow_price_from_different_market(db):
    from app.services.advisor_service import auto_track_advisor_picks
    now, fx, sig, quote = await seed_signal(db)
    pick = {'home_team': 'Home', 'away_team': 'Away', 'market': 'Home Over 0.5'}
    assert await auto_track_advisor_picks(db, [('test', {'top_picks': [pick]})],
                                        [{'id': 'scout'}], [(sig, fx)], now.date()) == 0
    assert await db.scalar(select(TrackedBet.id)) is None


async def test_advisor_records_exact_bookmaker_and_rejects_late_pick(db):
    from app.services.advisor_service import auto_track_advisor_picks
    now, fx, sig, quote = await seed_signal(db)
    pick = {'home_team': 'Home', 'away_team': 'Away', 'market': sig.market}
    args = ([('test', {'top_picks': [pick]})], [{'id': 'scout'}], [(sig, fx)], now.date())
    assert await auto_track_advisor_picks(db, *args) == 1
    bet = await db.scalar(select(TrackedBet))
    assert bet.bookmaker == quote.bookmaker
    assert bet.odds == quote.odds
    fx.kickoff_at = now - timedelta(seconds=1)
    await db.commit()
    args = ([('test', {'top_picks': [pick]})], [{'id': 'skeptic'}], [(sig, fx)], now.date())
    assert await auto_track_advisor_picks(db, *args) == 0


def test_telegram_value_band_never_invents_a_price():
    from app.services.telegram import build_value_band_message

    fixture = SimpleNamespace(
        country='England', league='Premier League', home_team='Home',
        away_team='Away', kickoff_at=datetime(2026, 9, 12, 12, tzinfo=timezone.utc),
    )
    signal = SimpleNamespace(
        market='Home Over 0.5', bayesian_prob=0.8, poisson_prob=0.8,
        bayesian_best_odd=None,
    )
    message = build_value_band_message([(signal, fixture)], datetime(2026, 9, 12).date())
    assert '@1.25' not in message
    assert '@' not in message


async def test_legacy_api_classifies_without_changing_records(db):
    from app.routers.tracker import router
    now, fx, sig, quote = await seed_signal(db)
    bet = TrackedBet(fixture_id=fx.id, bookmaker='Test Book', match_name='Home vs Away',
                     market_type=sig.market, selection_name=sig.market, odds=1.33,
                     stake=10, profit_loss=-10, result_status='Lost', source_rule_key='system_dual',
                     created_at=fx.kickoff_at + timedelta(days=2))
    db.add(bet)
    await db.commit()
    app = FastAPI()
    app.include_router(router)
    async def test_db():
        yield db
    app.dependency_overrides[get_db] = test_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/tracker/bets')
    assert response.status_code == 200
    item = next(row for row in response.json() if row['id'] == bet.id)
    assert item['evidence_status'] == 'late_entry_unverified'
    await db.refresh(bet)
    assert (bet.stake, bet.profit_loss, bet.result_status) == (10, -10, 'Lost')


def test_probability_proposal_fails_closed_without_snapshot_probabilities():
    from app.services.loss_analysis_agent import _backtest_proposal
    result = _backtest_proposal({'change_type': 'min_probability', 'target': 'Under 3.5',
                                'proposed_value': 0.7}, [])
    assert result.accepted is False
    assert 'Probability-at-selection' in result.reason


async def test_loss_pipeline_saves_inactive_and_preserves_active_rule(db, monkeypatch):
    from app.services import loss_analysis_agent as service
    from app.models.loss_analysis import LossAnalysis
    active = LearningProposal(change_type='market_odds_ceiling', target='Under 3.5',
                              proposed_value=1.7, is_active=True)
    db.add(active)
    await db.commit()
    analysis = LossAnalysis(match_name='Home vs Away', market_type='Under 3.5')
    proposal = {'proposal_id': 'test', 'change_type': 'market_odds_ceiling',
                'target': 'Under 3.5', 'proposed_value': 1.6}
    monkeypatch.setattr(service, 'get_settings', lambda: SimpleNamespace(groq_api_key='mocked'))
    monkeypatch.setattr(service, '_load_unanalysed_losses', AsyncMock(return_value=[]))
    monkeypatch.setattr(service, '_load_recent_analyses', AsyncMock(return_value=[analysis, analysis]))
    monkeypatch.setattr(service, '_detect_patterns', AsyncMock(return_value={'top_patterns': ['test']}))
    monkeypatch.setattr(service, '_tune_thresholds', AsyncMock(return_value={'proposals': [proposal]}))
    monkeypatch.setattr(service, '_backtest_proposal', lambda *args: service.BacktestResult('test', accepted=True, reason='diagnostic'))
    await service.run_loss_analysis_pipeline(db)
    rows = (await db.scalars(select(LearningProposal).order_by(LearningProposal.id))).all()
    assert len(rows) == 2
    assert rows[0].is_active is True
    assert rows[1].is_active is False


async def test_watch_guard_requires_review_for_suppression_and_recovery(db, monkeypatch):
    from app.services import league_watch_guard as service
    monkeypatch.setattr(service, 'LEAGUE_WATCHLIST', {'test': {'min_bets_act': 10}})
    monkeypatch.setattr(service, '_graduated_leagues', AsyncMock(return_value=[]))
    monkeypatch.setattr(service, '_query_league_stats', AsyncMock(return_value=(20, 5, -30)))
    for _ in range(2):
        states = await service.run_league_watch_guard(db)
        assert states[0].action_taken == 'proposed'
    rows = (await db.scalars(select(LearningProposal))).all()
    assert len(rows) == 1 and rows[0].is_active is False
    rows[0].is_active = True  # Existing administrator-controlled state.
    await db.commit()
    monkeypatch.setattr(service, '_query_league_stats', AsyncMock(return_value=(20, 18, 30)))
    states = await service.run_league_watch_guard(db)
    assert states[0].action_taken == 'review_recovery'
    assert rows[0].is_active is True


async def test_suggestions_preserve_active_rules_and_retry_without_duplicates(db):
    active = LearningProposal(change_type='market_odds_ceiling', target='Under 3.5',
                              proposed_value=1.7, is_active=True)
    db.add(active)
    await db.commit()
    for _ in range(2):
        suggestion = LearningProposal(change_type=active.change_type, target=active.target,
                                      proposed_value=1.6, is_active=True)
        saved = await save_suggestion(db, suggestion)
        await db.commit()
        assert saved.is_active is False
    assert active.is_active is True
    assert len((await db.scalars(select(LearningProposal))).all()) == 2


async def test_strategy_pipeline_cannot_supersede_active_rule(db, monkeypatch):
    from app.services import strategy_pipeline as service
    active = LearningProposal(change_type='market_suppression', target='Under 3.5',
                              proposed_value=1, is_active=True)
    db.add(active)
    await db.commit()
    proposal = {'change_type': 'market_suppression', 'target': 'Under 3.5', 'proposed_value': 0.9}
    report = SimpleNamespace(n_bets_total=100, overall_win_rate=0.7)
    monkeypatch.setattr(service, 'run_signal_analyst', AsyncMock(return_value=report))
    monkeypatch.setattr(service, 'run_strategy_agent', AsyncMock(return_value=[proposal]))
    monkeypatch.setattr(service, 'run_risk_agent', AsyncMock(return_value=([proposal], [])))
    await service.run_strategy_pipeline(db)
    rows = (await db.scalars(select(LearningProposal).order_by(LearningProposal.id))).all()
    assert len(rows) == 2
    assert rows[0].is_active is True
    assert rows[1].is_active is False
    assert await service.check_suppression_reactivations(db) == 0
    assert rows[0].is_active is True


@pytest.mark.parametrize('module,path', [
    ('backtest', '/api/backtest/run'), ('backtest', '/api/backtest/cancel'),
    ('signals', '/api/signals/compute'), ('tracker', '/api/tracker/sync'),
    ('loss_analysis', '/api/loss-analysis/run'),
])
async def test_shared_mutations_require_admin_before_side_effects(module, path):
    from importlib import import_module
    router = import_module(f'app.routers.{module}').router
    app = FastAPI()
    app.include_router(router)
    async def dummy_db():
        yield None
    app.dependency_overrides[get_db] = dummy_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post(path, json={})).status_code == 401
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(is_active=True, is_admin=False)
        assert (await client.post(path, json={})).status_code == 403


async def test_admin_can_reach_cancel_without_running_a_job():
    from app.routers import backtest
    app = FastAPI()
    app.include_router(backtest.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(is_active=True, is_admin=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/backtest/cancel')
        assert response.status_code == 200
        assert response.json()['status'] == 'not_running'
