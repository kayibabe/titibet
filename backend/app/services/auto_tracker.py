"""
auto_tracker.py — Backend auto-tracking of system signals and ACCAs.

Creates TrackedBet rows (user_id=None) for every qualifying signal on a date.
Idempotent: existing rows are skipped.  Called from sync_and_compute() so
auto-tracking runs every sync cycle regardless of whether anyone visits the
Signals page.

Qualifying signals (everything served to subscribers):
  - Any signal with is_candidate=False and dual_agreement != "Contradiction"
  - Suppression guards applied (DISABLED_LEAGUES, OVER_GOALS_SUPPRESSED_LEAGUES,
    women's league filters, HO05_DATA_POOR_COUNTRIES, DUAL_HIGH_ODDS_CEILING)

ACCA auto-tracking (auto_track_acca_signals):
  - Builds a signal-model ACCA from all qualifying candidates each sync cycle.
  - On subsequent calls for the same day, only fixtures NOT already in an
    earlier system_acca ticket are eligible — guaranteeing zero leg overlap
    across multiple ACCA tickets for the same date.
  - Minimum 2 unused candidates required; target odds 4.0, fallback 3.5, 3.0.
  - Defers to advisor-path ACCA (acca_leg_system) when leg rows already exist.
"""
from __future__ import annotations

import json
import logging
from datetime import date

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Signal, Fixture, TrackedBet
from app.models.learning_proposal import LearningProposal
from app.models.user import User as _User  # noqa: F401 — registers users table in SA metadata
from app.core.config import (
    DUAL_HIGH_ODDS_CEILING, WOMEN_LEAGUE_KEYWORDS,
    WOMEN_OVER_SUPPRESSED_MARKETS, HO05_DATA_POOR_COUNTRIES,
    DISABLED_LEAGUES, OVER_GOALS_SUPPRESSED_LEAGUES,
    is_womens_fixture,
)
from app.services.acca_builder import build_acca_candidates, build_accumulator

logger = logging.getLogger("titibet.auto_tracker")

FLAT_STAKE = 50_000.0


async def _load_kelly_multipliers(db: AsyncSession) -> dict[str, float]:
    """
    Returns active kelly_fraction_adj multipliers keyed by dual_confidence target.
    E.g. {"High": 0.5} means Both+High stakes are halved before tracking.
    Falls back to {} (no adjustment) on any error.
    """
    try:
        result = await db.execute(
            select(LearningProposal).where(
                LearningProposal.change_type == "kelly_fraction_adj",
                LearningProposal.is_active == True,  # noqa: E712
            )
        )
        proposals = result.scalars().all()
        return {
            p.target: float(p.proposed_value)
            for p in proposals
            if p.proposed_value is not None and 0.1 <= float(p.proposed_value) <= 1.0
        }
    except Exception:
        logger.warning("_load_kelly_multipliers: could not load proposals — using 1.0×")
        return {}


def _grade(q: float | None) -> str | None:
    # Thresholds recalibrated 2026-07-02 for the probability-based quality scale
    # (quality ≈ prob × tier/bookmaker/confidence factors, typically 0.2–0.8;
    # the old 0.035–0.08 cutoffs matched the retired EV-based scale).
    if q is None:
        return None
    if q >= 0.60: return "A"
    if q >= 0.45: return "B"
    if q >= 0.30: return "C"
    return "D"


async def auto_track_date(db: AsyncSession, run_date: date) -> int:
    """
    Create system TrackedBet rows for all qualifying signals on run_date.
    Tracks every signal served to subscribers: is_candidate=False and not a
    Contradiction (engines actively disagree → no bet).
    Returns count of newly inserted bets.
    """
    # Load all non-candidate, non-contradiction signals for this date
    rows = list(
        (await db.execute(
            select(Signal, Fixture)
            .join(Fixture, Signal.fixture_id == Fixture.id)
            .where(Fixture.event_date == run_date)
            .where(Signal.is_candidate == False)  # noqa: E712
            .where(Signal.dual_agreement != "Contradiction")
        )).all()
    )

    if not rows:
        return 0

    # Load active kelly_fraction_adj multipliers from learning proposals.
    kelly_mults = await _load_kelly_multipliers(db)

    # Load existing bets for this date to avoid duplicates.
    # Check on (fixture_id, market_type) only — bookmaker varies between the
    # old per-user strategy-tracker rows and the new system_auto rows, so
    # using bookmaker in the key would miss those collisions.
    existing_rows = list(
        (await db.execute(
            select(TrackedBet.fixture_id, TrackedBet.market_type)
            .where(TrackedBet.event_date == run_date)
        )).all()
    )
    existing_keys: set[tuple] = {
        (r.fixture_id, r.market_type) for r in existing_rows
    }

    inserted = 0
    for signal, fixture in rows:
        bookmaker = signal.bayesian_bookmaker or "Best Available"
        key = (signal.fixture_id, signal.market)
        if key in existing_keys:
            continue

        # Defense-in-depth: skip disabled leagues and over-goals-suppressed leagues.
        # signal_engine already filters these at write time, but old signals in the
        # DB (generated before a league was suppressed) can still reach this loop.
        league_lower = (fixture.league or "").lower().strip()
        if league_lower in DISABLED_LEAGUES or "friendlies" in league_lower:
            continue
        if signal.market in {"Home Over 0.5", "Away Over 0.5", "Over 1.5", "Over 2.5"}:
            if any(k in league_lower for k in OVER_GOALS_SUPPRESSED_LEAGUES):
                continue

        odds = signal.bayesian_best_odd
        if not odds or odds <= 1.01:
            prob = signal.poisson_prob or signal.bayesian_prob
            if prob and 0.0 < prob < 1.0:
                odds = round(1.0 / prob, 3)
            else:
                continue

        # Skip Both+High picks whose odds exceed the serving-time ceiling —
        # consistent with what the router shows subscribers.
        ceiling = DUAL_HIGH_ODDS_CEILING.get(signal.market)
        if (
            ceiling is not None
            and signal.dual_confidence == "High"
            and signal.dual_agreement == "Both"
            and odds >= ceiling
        ):
            continue

        # Skip women's league over-goals picks — models calibrated on men's
        # football systematically overestimate scoring in women's fixtures.
        if (
            signal.market in WOMEN_OVER_SUPPRESSED_MARKETS
            and is_womens_fixture(fixture.league, fixture.home_team, fixture.away_team)
        ):
            continue

        # Skip Both+High Home Over 0.5 from data-poor countries at Tier 3.
        # Both engines can agree with high confidence on insufficient historical
        # data — the agreement reflects noise, not genuine edge.
        if (
            signal.market == "Home Over 0.5"
            and signal.dual_confidence == "High"
            and signal.dual_agreement == "Both"
            and (fixture.league_tier or 3) >= 3
            and (fixture.country or "").lower() in HO05_DATA_POOR_COUNTRIES
        ):
            continue

        agreement = signal.dual_agreement or ""
        confidence = signal.dual_confidence or ""
        match_name = f"{fixture.home_team} vs {fixture.away_team}"

        if agreement == "Both" and confidence == "High":
            source_rule_key   = "system_dual"
            source_rule_label = "Dual Signal (High+Both)"
        elif agreement == "Both":
            source_rule_key   = "system_dual"
            source_rule_label = f"Dual Signal ({confidence or 'Medium'}+Both)"
        elif agreement == "Poisson Only":
            source_rule_key   = "system_auto"
            source_rule_label = "System Poisson Pick"
        elif agreement == "Bayesian Only":
            source_rule_key   = "system_auto"
            source_rule_label = "System Bayesian Pick"
        else:
            source_rule_key   = "system_auto"
            source_rule_label = "System Auto-Pick"

        # Apply active kelly_fraction_adj multiplier (from learning proposals).
        kelly_mult = kelly_mults.get(confidence, 1.0)
        stake = round(FLAT_STAKE * kelly_mult)
        if stake != FLAT_STAKE:
            logger.debug(
                "auto_track: stake for %s %s confidence = %.0f (%.2f× of %.0f)",
                signal.market, confidence, stake, kelly_mult, FLAT_STAKE,
            )

        bet = TrackedBet(
            user_id=None,
            fixture_id=signal.fixture_id,
            bookmaker=bookmaker,
            event_date=fixture.event_date,
            match_name=match_name,
            league=fixture.league,
            market_type=signal.market,
            selection_name=signal.market,
            odds=odds,
            stake=stake,
            recommended_stake_pct=signal.dual_recommended_stake_pct,
            source_rule_key=source_rule_key,
            source_rule_label=source_rule_label,
            signal_grade=_grade(signal.dual_quality_score),
            dual_confidence=signal.dual_confidence,
            dual_agreement=signal.dual_agreement,
            result_status="Pending",
        )
        db.add(bet)
        existing_keys.add(key)
        inserted += 1

    if inserted:
        await db.commit()
        logger.info("Auto-tracker: inserted %d system bets for %s", inserted, run_date)

    return inserted


_ACCA_TARGET_TIERS = [4.0, 3.5, 3.0]
_ACCA_MIN_CANDIDATES = 2


async def auto_track_acca_signals(db: AsyncSession, run_date: date) -> int:
    """
    Build and record a new system ACCA ticket from qualifying signal candidates,
    ensuring no fixture leg appears in any previously auto-tracked system_acca
    ticket for the same date.

    Defers entirely to the advisor-path ACCA (acca_leg_system / acca_advisory_system)
    when those tickets already exist for run_date — the presync and advisory-cache
    jobs take priority and this function is a fallback for dates they haven't covered.

    Returns count of new TrackedBet rows inserted (0 or 1 combined row).
    """
    # Defer to the advisor path if it has already run OR is scheduled to run.
    # Check 1: acca_leg_system rows already exist (evening_extras job already ran).
    advisor_acca_count = await db.scalar(
        select(func.count()).select_from(TrackedBet).where(
            TrackedBet.event_date == run_date,
            TrackedBet.source_rule_key == "acca_leg_system",
            TrackedBet.user_id.is_(None),
        )
    )
    if advisor_acca_count:
        logger.info(
            "Auto-ACCA %s: advisor-path ACCA legs already exist (%d row(s)) — skipping signal-model ticket",
            run_date, advisor_acca_count,
        )
        return 0

    # Check 2: advisory cache in system_settings has an accumulator for this date.
    # The 08:30 cache job will create acca_advisory_system rows later — we should
    # not create a competing system_acca now when the advisory is already planned.
    from sqlalchemy import text as _text
    import json as _json
    cache_row = await db.scalar(
        _text("SELECT value FROM system_settings WHERE key = :k"),
        {"k": f"advisory_cache_{run_date}"},
    )
    if cache_row:
        try:
            cached = _json.loads(cache_row)
            has_acca = bool(
                cached.get("accumulator") or
                cached.get("acca_of_the_day") or
                cached.get("accumulators") or
                cached.get("acca")
            )
            if has_acca:
                logger.info(
                    "Auto-ACCA %s: advisory cache has an accumulator — deferring to advisor path",
                    run_date,
                )
                return 0
        except Exception:
            pass  # malformed cache — fall through to signal-model

    # Collect fixture_ids already used in system_acca tickets for this date.
    existing_accas = list(
        (await db.execute(
            select(TrackedBet.notes)
            .where(
                TrackedBet.event_date == run_date,
                TrackedBet.source_rule_key == "system_acca",
                TrackedBet.user_id.is_(None),
            )
        )).scalars().all()
    )

    used_fixture_ids: set[int] = set()
    for notes_json in existing_accas:
        try:
            data = json.loads(notes_json or "{}")
            for leg in data.get("legs", []):
                fid = leg.get("fixture_id")
                if fid is not None:
                    used_fixture_ids.add(int(fid))
        except Exception:
            pass

    # Build candidate pool excluding already-used fixtures.
    candidates = await build_acca_candidates(db, run_date, exclude_fixture_ids=used_fixture_ids)

    if len(candidates) < _ACCA_MIN_CANDIDATES:
        logger.info(
            "Auto-ACCA %s: only %d unused candidates (need %d) — skipping",
            run_date, len(candidates), _ACCA_MIN_CANDIDATES,
        )
        return 0

    # Try target tiers from highest to lowest; take the first that produces ≥2 legs.
    chosen: dict | None = None
    for tier in _ACCA_TARGET_TIERS:
        acca = build_accumulator(candidates, tier)
        if acca["leg_count"] >= _ACCA_MIN_CANDIDATES:
            chosen = acca
            break

    if not chosen:
        logger.info("Auto-ACCA %s: could not build ≥2-leg ticket from %d candidates", run_date, len(candidates))
        return 0

    legs      = chosen["legs"]
    combined  = chosen["combined_odds"]
    leg_count = chosen["leg_count"]

    leg_summary = "\n".join(
        f"{i+1}. {leg.get('home_team','')} vs {leg.get('away_team','')} · "
        f"{leg.get('market','')} @ {float(leg.get('odd') or leg.get('fair_odds') or 0):.2f}"
        for i, leg in enumerate(legs)
    )

    # Dedup: don't insert if an identical leg set already exists (same fixture_ids in same order).
    fingerprint = ",".join(str(leg["fixture_id"]) for leg in legs)
    fp_tag      = f"system_acca|{fingerprint}"
    already     = await db.scalar(
        select(TrackedBet.id).where(
            TrackedBet.source_rule_key == "system_acca",
            TrackedBet.event_date == run_date,
            TrackedBet.user_id.is_(None),
            TrackedBet.selection_name == fp_tag,
        )
    )
    if already:
        logger.debug("Auto-ACCA %s: identical ticket already tracked — skipping", run_date)
        return 0

    db.add(TrackedBet(
        user_id=None,
        fixture_id=None,
        bookmaker="System Acca",
        event_date=run_date,
        match_name=f"AI Acca · {leg_count} leg{'s' if leg_count != 1 else ''}",
        league=None,
        market_type="Accumulator",
        selection_name=fp_tag,
        odds=combined,
        stake=FLAT_STAKE,
        source_rule_key="system_acca",
        source_rule_label="System ACCA",
        result_status="Pending",
        notes=json.dumps({"legs": legs, "leg_summary": leg_summary}),
    ))

    await db.commit()
    logger.info(
        "Auto-ACCA %s: inserted %d-leg ticket @ %.2f combined odds",
        run_date, leg_count, combined,
    )
    return 1
