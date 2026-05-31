from datetime import datetime
from typing import Optional
from sqlalchemy import Index, Integer, String, Float, Boolean, DateTime, ForeignKey, JSON, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint("fixture_id", "market", name="uq_signal_fixture_market"),
        Index("ix_signal_fixture_market", "fixture_id", "market"),
        Index("ix_signal_fixture_computed", "fixture_id", "computed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), index=True)
    market: Mapped[str] = mapped_column(String(80), index=True)

    # ── Bayesian engine outputs (from FootBet odds_engine.py) ──
    bayesian_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bayesian_edge: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bayesian_best_odd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bayesian_bookmaker: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    bayesian_overround: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bayesian_coverage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bayesian_bookmaker_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bayesian_is_value: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    bayesian_confidence: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    bayesian_quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bayesian_kelly_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Outlier odds detection: True when the best bookmaker price is >35% above
    # all other bookmakers. EV/edge/Kelly are computed from consensus_odd instead.
    # best_odd is still stored so the user can act on it if the price is genuine.
    bayesian_odds_outlier: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    bayesian_consensus_odd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ── Poisson engine outputs (ported from TiTiBet rules.js) ──
    poisson_lambda_h: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    poisson_lambda_a: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    poisson_lambda_total: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    poisson_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    poisson_rule_key: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    poisson_rule_pass: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    poisson_rule_strong: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    poisson_edge_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    poisson_grade: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    # Fixture-level list of contradiction descriptions emitted by the Poisson
    # engine (e.g. "Bayesian likes Over 2.5 but λ_total=1.9 favours Under").
    # Same list is denormalised onto every row for the fixture; the frontend
    # surfaces it via ContradictionAlert when signal.contradiction is True.
    poisson_mixed_signals: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)

    # ── Dual-engine fusion ──
    dual_confidence: Mapped[str] = mapped_column(String(10), index=True, default="None")
    dual_agreement: Mapped[str] = mapped_column(String(20), default="None")
    dual_quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    dual_recommended_stake_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    contradiction: Mapped[bool] = mapped_column(Boolean, default=False)

    # Line movement: (current_best_odd - opening_best_odd) / opening_best_odd × 100.
    # Negative = odds shortened (sharp money confirmed our selection).
    # Positive = odds drifted out (market moved against us).
    odds_drift_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ── BOS 2.0 — Match Stability Index ────────────────────────────────────
    # SI range: 0–400; threshold 75. Populated per fixture (same value on every
    # signal row for the fixture). bos_passed=True boosts dual_quality_score.
    bos_si: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bos_passed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # ── ZINB — Zero-Inflated Negative Binomial expected goals ──────────────
    # Model-fitted (mu_home, mu_away) from ZINB when sufficient history exists.
    # Complement to the CS-ratio Poisson lambdas.
    zinb_lambda_h: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    zinb_lambda_a: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ── Explicit Expected Value ──────────────────────────────────────────────
    # ev_score = p_model × best_odd − 1
    # Positive → expected profit per unit staked (before variance).
    ev_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ── Glicko-2 rating differential ────────────────────────────────────────
    # home_rating − away_rating on the 1500-point Glicko-2 scale.
    # Positive = home team is rated higher. Magnitude reflects strength gap.
    glicko_r_diff: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ── BREA — BTTS Risk-Elimination Analysis ───────────────────────────────
    # Only populated for BTTS Yes signals.
    # brea_ri1: P(1:1 scoreline) — only losing case for BTTS+U2.5 NO.
    #           Lower is safer. Threshold < 10%.
    # brea_fss: BREA Final Selection Score [0-1]. Higher = cleaner BTTS signal.
    brea_ri1: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    brea_fss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ── FHGI — First-Half Goal Intensity (enhanced Over 0.5 1H) ────────────
    # Only populated for "Over 0.5 1H" signals where all 4 HT CS odds exist.
    # fhgi_gpi:   Goal Probability Index = devigged P(HT 1:1)
    # fhgi_fhgmi: FHGMI ratio = (P_10+P_01+2P_11)/P_00
    # fhgi_p_model: Logistic model probability for FH Over 0.5
    fhgi_gpi: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fhgi_fhgmi: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fhgi_p_model: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    computed_at: Mapped[datetime] = mapped_column(DateTime, index=True, server_default=func.now())

    fixture: Mapped["Fixture"] = relationship("Fixture", back_populates="signals")  # noqa: F821
