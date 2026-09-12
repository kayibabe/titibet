"""Compatibility import of legacy mutable rows into Stage 1 evidence tables.

The importer is deliberately additive.  It retains the legacy pulled time,
records the time this process received the row, and marks the result as legacy.
It never claims that import time was the original availability time and never
updates or deletes a legacy row.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Fixture, FixtureRevision, MarketSnapshot, OddsQuote


@dataclass(frozen=True)
class LegacyImportReport:
    fixtures_seen: int = 0
    fixture_revisions_created: int = 0
    quotes_seen: int = 0
    quotes_created: int = 0


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def import_legacy_evidence(
    db: AsyncSession,
    *,
    imported_at: datetime | None = None,
    fixture_ids: set[int] | None = None,
) -> LegacyImportReport:
    """Import legacy fixtures and quotes, idempotently, in one transaction.

    ``fixture_ids`` supports a bounded rehearsal or resumable production batch.
    No provider observation is fabricated: legacy rows have no independently
    verifiable receipt and therefore keep ``provider_observation_id`` null.
    """
    receipt = _utc(imported_at or datetime.now(timezone.utc))
    if fixture_ids is None:
        fixture_count = int((await db.execute(text("SELECT COUNT(*) FROM fixtures"))).scalar_one())
        quote_count = int((await db.execute(text(
            "SELECT COUNT(*) FROM market_snapshots WHERE odds IS NOT NULL AND pulled_at IS NOT NULL"
        ))).scalar_one())
        revision_result = await db.execute(text("""
            INSERT INTO fixture_revisions
                (fixture_id, external_fixture_id, event_date, kickoff_at, status,
                 home_score, away_score, home_score_ht, away_score_ht,
                 received_at, evidence_class, legacy_fixture_id)
            SELECT f.id, f.external_fixture_id, f.event_date, f.kickoff_at, f.status,
                   f.home_score, f.away_score, f.home_score_ht, f.away_score_ht,
                   :received_at, 'legacy_import', f.id
            FROM fixtures f
            WHERE NOT EXISTS (
                SELECT 1 FROM fixture_revisions r WHERE r.legacy_fixture_id = f.id
            )
        """), {"received_at": receipt.replace(tzinfo=None)})
        quote_result = await db.execute(text("""
            INSERT INTO odds_quotes
                (fixture_id, market_key, market_version, bookmaker, selection_name,
                 odds, pulled_at, received_at, availability, evidence_class,
                 legacy_snapshot_id)
            SELECT ms.fixture_id, ms.market_type, 'legacy-v1', ms.bookmaker,
                   ms.selection_name, ms.odds, ms.pulled_at, :received_at,
                   'unknown', 'legacy_import', ms.id
            FROM market_snapshots ms
            WHERE ms.odds IS NOT NULL AND ms.pulled_at IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM odds_quotes q WHERE q.legacy_snapshot_id = ms.id
              )
        """), {"received_at": receipt.replace(tzinfo=None)})
        await db.commit()
        return LegacyImportReport(
            fixtures_seen=fixture_count,
            fixture_revisions_created=max(revision_result.rowcount or 0, 0),
            quotes_seen=quote_count,
            quotes_created=max(quote_result.rowcount or 0, 0),
        )

    fixture_query = select(Fixture).order_by(Fixture.id)
    if fixture_ids is not None:
        fixture_query = fixture_query.where(Fixture.id.in_(fixture_ids))
    fixtures = list((await db.scalars(fixture_query)).all())
    revisions_created = 0
    quotes_seen = 0
    quotes_created = 0

    for fixture in fixtures:
        revision = await db.scalar(
            select(FixtureRevision).where(FixtureRevision.legacy_fixture_id == fixture.id)
        )
        if revision is None:
            db.add(FixtureRevision(
                fixture_id=fixture.id,
                external_fixture_id=fixture.external_fixture_id,
                event_date=fixture.event_date.isoformat() if fixture.event_date else None,
                kickoff_at=fixture.kickoff_at,
                status=fixture.status,
                home_score=fixture.home_score,
                away_score=fixture.away_score,
                home_score_ht=fixture.home_score_ht,
                away_score_ht=fixture.away_score_ht,
                received_at=receipt,
                evidence_class="legacy_import",
                legacy_fixture_id=fixture.id,
            ))
            revisions_created += 1

        snapshots = list((await db.scalars(
            select(MarketSnapshot).where(MarketSnapshot.fixture_id == fixture.id).order_by(MarketSnapshot.id)
        )).all())
        quotes_seen += len(snapshots)
        for snapshot in snapshots:
            if snapshot.odds is None or snapshot.pulled_at is None:
                # A legacy row without a price is not a quote.  Do not invent
                # one or an availability time to make the count look complete.
                continue
            already = await db.scalar(
                select(OddsQuote.id).where(OddsQuote.legacy_snapshot_id == snapshot.id)
            )
            if already is not None:
                continue
            db.add(OddsQuote(
                fixture_id=fixture.id,
                market_key=snapshot.market_type,
                market_version="legacy-v1",
                bookmaker=snapshot.bookmaker,
                selection_name=snapshot.selection_name,
                odds=snapshot.odds,
                pulled_at=_utc(snapshot.pulled_at),
                received_at=receipt,
                provider_observation_id=None,
                availability="unknown",
                evidence_class="legacy_import",
                legacy_snapshot_id=snapshot.id,
            ))
            quotes_created += 1

    await db.commit()
    return LegacyImportReport(
        fixtures_seen=len(fixtures),
        fixture_revisions_created=revisions_created,
        quotes_seen=quotes_seen,
        quotes_created=quotes_created,
    )


def content_sha256(value: object) -> str:
    """Canonical hash helper for feature/model snapshot writers."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
