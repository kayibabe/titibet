"""
Kalman filter for live/form team strength estimation.
Ported from qsbip/models/ratings/kalman.py.

Tracks a team's attacking strength as a hidden state updated by observed
goal counts in time windows (half-time, periods, or rolling form windows).

In titibet this is used as a form-strength tracker:
  - initialise with Glicko-2 or Poisson-lambda prior
  - update with the team's recent goal counts
  - output: adjusted strength estimate and uncertainty

Usage:
    kf = TeamKalmanFilter(initial_strength=1.4, initial_uncertainty=0.09)
    kf.update(goals_observed=2, time_fraction=1.0)   # full 90-min window
    strength = kf.state     # updated λ estimate
    uncertainty = kf.uncertainty
"""
from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)


class TeamKalmanFilter:
    """
    1-D Kalman filter for a single team's attacking strength.

    Parameters
    ----------
    initial_strength    : Prior estimate of xG/90 (from Glicko-2 or Poisson λ)
    initial_uncertainty : Prior variance σ² in the strength estimate
    process_noise       : Q — how much strength can drift per update step
    """

    def __init__(
        self,
        initial_strength: float = 1.4,
        initial_uncertainty: float = 0.09,
        process_noise: float = 0.01,
    ):
        self.x = initial_strength
        self.P = initial_uncertainty
        self.Q = process_noise
        self._updates: int = 0

    @property
    def state(self) -> float:
        return self.x

    @property
    def uncertainty(self) -> float:
        return math.sqrt(max(self.P, 0.0))

    def predict(self) -> None:
        """Time-update: uncertainty grows; state unchanged."""
        self.P = self.P + self.Q

    def update(self, goals_observed: float, time_fraction: float) -> None:
        """
        Measurement update.

        goals_observed : goals scored in this window
        time_fraction  : fraction of 90 min elapsed in this window (0–1)
        """
        if time_fraction <= 0:
            return

        expected = self.x * time_fraction
        R = max(expected, 0.1)  # Poisson variance ≈ expected

        self.predict()

        K = self.P / (self.P + R)
        innovation = goals_observed - expected
        self.x = max(self.x + K * innovation, 0.05)
        self.P = (1.0 - K) * self.P
        self._updates += 1

        logger.debug(
            "KF update #%d: obs=%.1f exp=%.2f K=%.4f → strength=%.4f±%.4f",
            self._updates, goals_observed, expected, K, self.x, self.uncertainty,
        )

    def __repr__(self) -> str:
        return (
            f"TeamKalmanFilter(strength={self.x:.3f}±{self.uncertainty:.3f}, "
            f"updates={self._updates})"
        )


class MatchStrengthTracker:
    """
    Tracks live strength estimates for both teams in one match.
    Wraps two TeamKalmanFilter instances.
    """

    def __init__(
        self,
        home_strength: float,
        away_strength: float,
        home_uncertainty: float = 0.09,
        away_uncertainty: float = 0.09,
    ):
        self.home = TeamKalmanFilter(home_strength, home_uncertainty)
        self.away = TeamKalmanFilter(away_strength, away_uncertainty)
        self.minute: int = 0

    def update_interval(
        self,
        minute: int,
        home_goals_in_window: int,
        away_goals_in_window: int,
    ) -> None:
        prev = self.minute
        elapsed = minute - prev
        if elapsed <= 0:
            return
        time_frac = elapsed / 90.0
        self.home.update(home_goals_in_window, time_frac)
        self.away.update(away_goals_in_window, time_frac)
        self.minute = minute

    def remaining_expected_goals(self) -> tuple[float, float]:
        remaining = max((90 - self.minute) / 90.0, 0.0)
        return self.home.state * remaining, self.away.state * remaining

    def live_result_probs(self, home_score: int, away_score: int) -> dict[str, float]:
        """P(home_win|score), P(draw|score), P(away_win|score) via Poisson on remaining xG."""
        home_xg, away_xg = self.remaining_expected_goals()
        return _poisson_result_probs(home_score, away_score, home_xg, away_xg)

    def __repr__(self) -> str:
        return (
            f"MatchStrengthTracker(min={self.minute}, home={self.home}, away={self.away})"
        )


def _poisson_result_probs(
    home_score: int,
    away_score: int,
    home_xg: float,
    away_xg: float,
    max_add: int = 6,
) -> dict[str, float]:
    """Poisson convolution on remaining expected goals conditioned on current score."""
    try:
        from scipy.stats import poisson as _sp
        _pmf = lambda k, l: float(_sp.pmf(k, max(l, 1e-6)))  # noqa: E731
    except ImportError:
        def _pmf(k: int, l: float) -> float:  # type: ignore[misc]
            import math
            l = max(l, 1e-6)
            return math.exp(-l) * (l ** k) / math.factorial(min(k, 20))

    home_win = draw = away_win = 0.0
    for ah in range(max_add + 1):
        ph = _pmf(ah, home_xg)
        for aa in range(max_add + 1):
            pa = _pmf(aa, away_xg)
            prob = ph * pa
            final_h, final_a = home_score + ah, away_score + aa
            if final_h > final_a:
                home_win += prob
            elif final_h == final_a:
                draw += prob
            else:
                away_win += prob

    total = home_win + draw + away_win
    if total == 0:
        return {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}
    return {"home": home_win / total, "draw": draw / total, "away": away_win / total}
