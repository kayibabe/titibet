"""Durable Stage 1 evidence records used for point-in-time replay."""
from datetime import datetime
from typing import Optional
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, Float, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class ProviderObservation(Base):
    __tablename__ = "provider_observations"
    __table_args__ = (Index("ix_provider_obs_received", "received_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(160), nullable=False)
    request_scope: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    provider_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MarketDefinition(Base):
    __tablename__ = "market_definitions"
    __table_args__ = (UniqueConstraint("canonical_key", "version", name="uq_market_definition_version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_key: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    settlement_rules: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class OddsQuote(Base):
    __tablename__ = "odds_quotes"
    __table_args__ = (
        Index("ix_odds_quotes_fixture_market_time", "fixture_id", "market_key", "received_at"),
        Index("ix_odds_quotes_received", "received_at"),
        UniqueConstraint(
            "provider_observation_id", "market_key", "market_version",
            "bookmaker", "selection_name", name="uq_odds_quote_observation_key",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False, index=True)
    market_key: Mapped[str] = mapped_column(String(120), nullable=False)
    market_version: Mapped[str] = mapped_column(String(40), nullable=False, default="v1")
    bookmaker: Mapped[str] = mapped_column(String(80), nullable=False)
    selection_name: Mapped[str] = mapped_column(String(120), nullable=False)
    odds: Mapped[float] = mapped_column(Float, nullable=False)
    pulled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    provider_observation_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("provider_observations.id"), nullable=True, index=True)
    availability: Mapped[str] = mapped_column(String(20), nullable=False, server_default="prematch")
    evidence_class: Mapped[str] = mapped_column(String(40), nullable=False, server_default="provider")
    legacy_snapshot_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FixtureRevision(Base):
    """Append-only provider view of a fixture at one receipt time."""
    __tablename__ = "fixture_revisions"
    __table_args__ = (
        Index("ix_fixture_revisions_fixture_received", "fixture_id", "received_at"),
        UniqueConstraint("legacy_fixture_id", name="uq_fixture_revision_legacy_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False, index=True)
    external_fixture_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    event_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    kickoff_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_score_ht: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_score_ht: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    provider_observation_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("provider_observations.id"), nullable=True, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    supersedes_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("fixture_revisions.id"), nullable=True)
    evidence_class: Mapped[str] = mapped_column(String(40), nullable=False, server_default="provider")
    legacy_fixture_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ModelVersion(Base):
    """Immutable model/configuration identity used by a snapshot."""
    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("name", "version", "config_sha256", name="uq_model_version_identity"),
        Index("ix_model_versions_created", "created_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    source_revision: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    config_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    training_manifest_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    max_input_available_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    runtime_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    seed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    parameters_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'{}'"))
    evidence_class: Mapped[str] = mapped_column(String(40), nullable=False, server_default="prospective")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FeatureSnapshot(Base):
    """Exact feature values for one fixture revision and point-in-time cutoff."""
    __tablename__ = "feature_snapshots"
    __table_args__ = (
        UniqueConstraint("fixture_revision_id", "as_of", "transform_version", "content_sha256", name="uq_feature_snapshot_identity"),
        Index("ix_feature_snapshots_fixture_asof", "fixture_id", "as_of"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False, index=True)
    fixture_revision_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixture_revisions.id"), nullable=False, index=True)
    model_version_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("model_versions.id"), nullable=True, index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    features_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_refs_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'{}'"))
    transform_version: Mapped[str] = mapped_column(String(80), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    evidence_class: Mapped[str] = mapped_column(String(40), nullable=False, server_default="prospective")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
