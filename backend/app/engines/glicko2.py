"""
Glicko-2 team rating system.
Ported from qsbip/models/ratings/glicko2.py.

Tracks three quantities per team:
  r  — rating         (default 1500)
  RD — rating deviation   (uncertainty; shrinks as more games played)
  σ  — volatility     (consistency; adapts to inconsistent performers)

Used in titibet to compute a rating-difference feature (glicko_r_diff)
for each fixture, which augments signal quality scoring.
RD naturally models injury absences and new-promoted teams.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Sequence

logger = logging.getLogger(__name__)

_SCALE = 173.7178
_DEFAULT_R = 1500.0
_DEFAULT_RD = 350.0
_DEFAULT_SIGMA = 0.06
_TAU = 0.5
_EPSILON = 1e-6


@dataclass
class TeamRating:
    name: str
    r: float = _DEFAULT_R
    rd: float = _DEFAULT_RD
    sigma: float = _DEFAULT_SIGMA

    @property
    def mu(self) -> float:
        return (self.r - 1500.0) / _SCALE

    @property
    def phi(self) -> float:
        return self.rd / _SCALE

    def __str__(self) -> str:
        return f"{self.name}: r={self.r:.1f} RD={self.rd:.1f} σ={self.sigma:.4f}"


@dataclass
class MatchResult:
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    home_advantage: float = 0.0

    @property
    def home_score(self) -> float:
        if self.home_goals > self.away_goals:
            return 1.0
        if self.home_goals == self.away_goals:
            return 0.5
        return 0.0

    @property
    def away_score(self) -> float:
        return 1.0 - self.home_score


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi ** 2 / math.pi ** 2)


def _E(mu: float, mu_j: float, phi_j: float) -> float:
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


def _new_sigma(mu, phi, sigma, delta, v, tau=_TAU) -> float:
    a = math.log(sigma ** 2)
    delta_sq, phi_sq = delta ** 2, phi ** 2

    def f(x: float) -> float:
        ex = math.exp(x)
        num = ex * (delta_sq - phi_sq - v - ex)
        denom = 2.0 * (phi_sq + v + ex) ** 2
        return num / denom - (x - a) / (tau ** 2)

    A = a
    if delta_sq > phi_sq + v:
        B = math.log(delta_sq - phi_sq - v)
    else:
        k = 1
        while f(a - k * tau) < 0:
            k += 1
        B = a - k * tau

    fA, fB = f(A), f(B)
    iters = 0
    while abs(B - A) > _EPSILON and iters < 100:
        C = A + (A - B) * fA / (fB - fA)
        fC = f(C)
        if fC * fB < 0:
            A, fA = B, fB
        else:
            fA /= 2.0
        B, fB = C, fC
        iters += 1
    return math.exp(A / 2.0)


def _update_rating(team: TeamRating, opponents: list, tau=_TAU) -> TeamRating:
    if not opponents:
        phi_star = math.sqrt(team.phi ** 2 + team.sigma ** 2)
        return TeamRating(name=team.name, r=team.r,
                          rd=min(phi_star * _SCALE, _DEFAULT_RD), sigma=team.sigma)

    mu, phi, sigma = team.mu, team.phi, team.sigma

    v_sum = 0.0
    for opp, _, ha_adj in opponents:
        mu_adj = mu + ha_adj
        g_j = _g(opp.phi)
        e_j = _E(mu_adj, opp.mu, opp.phi)
        v_sum += g_j ** 2 * e_j * (1.0 - e_j)

    if v_sum == 0:
        return team

    v = 1.0 / v_sum
    delta_sum = 0.0
    for opp, score, ha_adj in opponents:
        mu_adj = mu + ha_adj
        g_j = _g(opp.phi)
        e_j = _E(mu_adj, opp.mu, opp.phi)
        delta_sum += g_j * (score - e_j)

    delta = v * delta_sum
    new_sig = _new_sigma(mu, phi, sigma, delta, v, tau)
    phi_star = math.sqrt(phi ** 2 + new_sig ** 2)
    phi_new = 1.0 / math.sqrt(1.0 / phi_star ** 2 + 1.0 / v)
    mu_new = mu + phi_new ** 2 * delta_sum

    return TeamRating(
        name=team.name,
        r=round(_SCALE * mu_new + 1500.0, 4),
        rd=round(_SCALE * phi_new, 4),
        sigma=round(new_sig, 6),
    )


class Glicko2System:
    """
    Manages Glicko-2 ratings for a set of teams.
    Call update_period() after each block of matches.
    """

    def __init__(self, tau: float = _TAU, home_advantage_pts: float = 50.0):
        self.tau = tau
        self.home_advantage_mu = home_advantage_pts / _SCALE
        self._ratings: dict[str, TeamRating] = {}

    def _get_or_create(self, name: str) -> TeamRating:
        if name not in self._ratings:
            self._ratings[name] = TeamRating(name=name)
        return self._ratings[name]

    def get_rating(self, name: str) -> TeamRating:
        return self._get_or_create(name)

    def expected_score(self, home: str, away: str) -> tuple[float, float]:
        h = self._get_or_create(home)
        a = self._get_or_create(away)
        p_home = _E(h.mu + self.home_advantage_mu, a.mu, a.phi)
        return p_home, 1.0 - p_home

    def rating_diff(self, home: str, away: str) -> float:
        """Return home_rating - away_rating (positive → home favoured)."""
        h = self._get_or_create(home)
        a = self._get_or_create(away)
        return round(h.r - a.r, 2)

    def update_period(self, results: Sequence[MatchResult], grow_inactive: bool = True) -> None:
        opponents: dict[str, list] = {}
        teams_played: set[str] = set()

        for r in results:
            h_rating = self._get_or_create(r.home_team)
            a_rating = self._get_or_create(r.away_team)
            teams_played.update([r.home_team, r.away_team])
            opponents.setdefault(r.home_team, []).append(
                (a_rating, r.home_score, self.home_advantage_mu))
            opponents.setdefault(r.away_team, []).append(
                (h_rating, r.away_score, -self.home_advantage_mu))

        new_ratings: dict[str, TeamRating] = {}
        for name, opps in opponents.items():
            new_ratings[name] = _update_rating(self._get_or_create(name), opps, self.tau)

        if grow_inactive:
            for name, rating in self._ratings.items():
                if name not in teams_played:
                    new_ratings[name] = _update_rating(rating, [], self.tau)

        self._ratings.update(new_ratings)

    def rating_features(self, home: str, away: str) -> dict[str, float]:
        """Flat dict of Glicko-2 features for ML / ranking use."""
        h = self._get_or_create(home)
        a = self._get_or_create(away)
        exp_home, _ = self.expected_score(home, away)
        return {
            "glicko_home_r":   h.r,
            "glicko_home_rd":  h.rd,
            "glicko_home_sig": h.sigma,
            "glicko_away_r":   a.r,
            "glicko_away_rd":  a.rd,
            "glicko_away_sig": a.sigma,
            "glicko_r_diff":   h.r - a.r,
            "glicko_rd_sum":   h.rd + a.rd,
            "glicko_exp_home": exp_home,
        }

    def top_ratings(self, n: int = 10) -> list[TeamRating]:
        return sorted(self._ratings.values(), key=lambda t: t.r, reverse=True)[:n]
