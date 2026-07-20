"""
ZINB — Zero-Inflated Negative Binomial Goal Model.
Ported from qsbip/models/goal_model/zinb.py.

Handles overdispersion and excess zeros (common in defensive leagues).
Fitted per-team, per-league from historical match data. Exponential
forgetting down-weights older matches (decay_rate per week).

Falls back gracefully if scipy/numpy are unavailable or data is insufficient.

Usage in titibet:
    model = ZINBGoalModel()
    model.fit(matches)                         # list of dicts from DB
    mu_h, mu_a = model.predict_goals(home_id, away_id)
    matrix = model.score_matrix(home_id, away_id)

The predicted (mu_h, mu_a) augment the CS-ratio λ values in signal_engine.py
for fixtures where enough historical data exists.
"""
from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Optional scientific imports ────────────────────────────────────────────────
try:
    import numpy as np
    from scipy import optimize, stats as _scipy_stats
    _SCIPY_OK = True
except ImportError:
    _SCIPY_OK = False
    logger.warning("scipy/numpy not installed — ZINB model disabled; using Poisson fallback")


@dataclass
class TeamParams:
    """ZINB parameters for a single team's goal output."""
    mu: float = 1.2       # mean goals
    theta: float = 10.0   # NB dispersion (variance = mu + mu²/theta)
    pi: float = 0.05      # zero-inflation probability


@dataclass
class MatchParams:
    home_attack: TeamParams = field(default_factory=lambda: TeamParams(mu=1.4))
    home_defense: TeamParams = field(default_factory=TeamParams)
    away_attack: TeamParams = field(default_factory=lambda: TeamParams(mu=1.1))
    away_defense: TeamParams = field(default_factory=TeamParams)


def zinb_pmf(k, mu: float, theta: float, pi: float):
    """P(Y=k) for ZINB(mu, theta, pi). k can be int or ndarray."""
    if not _SCIPY_OK:
        return None
    k = np.asarray(k, dtype=int)
    p_nb = theta / (theta + mu)
    nb_0 = _scipy_stats.nbinom.pmf(0, n=theta, p=p_nb)
    nb_k = _scipy_stats.nbinom.pmf(k, n=theta, p=p_nb)
    return np.where(k == 0, pi + (1.0 - pi) * nb_0, (1.0 - pi) * nb_k)


def _neg_loglik(params: list, goals, weights) -> float:
    """Weighted ZINB negative log-likelihood."""
    mu, log_theta, logit_pi = params
    if mu <= 0:
        return 1e10
    theta = np.exp(log_theta)
    pi = 1.0 / (1.0 + np.exp(-logit_pi))
    pmf = zinb_pmf(goals, mu, theta, pi)
    pmf = np.clip(pmf, 1e-12, 1.0)
    return -float(np.dot(weights, np.log(pmf)))


def fit_zinb(goals: list[int], weights=None, min_samples: int = 5) -> TeamParams:
    """
    Fit ZINB parameters to an observed goal sequence.
    Returns default TeamParams if scipy unavailable or data insufficient.
    """
    if not _SCIPY_OK:
        avg = sum(goals) / len(goals) if goals else 1.2
        return TeamParams(mu=max(avg, 0.1))

    goals_arr = np.asarray(goals, dtype=int)
    if weights is None:
        weights = np.ones(len(goals_arr))
    weights = np.asarray(weights, dtype=float)

    if len(goals_arr) < min_samples:
        return TeamParams(mu=max(float(np.mean(goals_arr)) if len(goals_arr) else 1.2, 0.1))

    mu0 = max(float(np.mean(goals_arr)), 0.5)
    x0 = [mu0, np.log(5.0), -3.0]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = optimize.minimize(
            _neg_loglik, x0, args=(goals_arr, weights),
            method="Nelder-Mead",
            # 500 iterations + 1e-4 tolerance give betting-grade precision in
            # ~1/6 the time; tighter convergence doesn't improve pick quality.
            options={"xatol": 1e-4, "fatol": 1e-4, "maxiter": 500},
        )

    mu = max(result.x[0], 0.01)
    theta = max(float(np.exp(result.x[1])), 0.5)
    pi = float(np.clip(1.0 / (1.0 + np.exp(-result.x[2])), 0.0, 0.5))
    return TeamParams(mu=mu, theta=theta, pi=pi)


def _tau_dc(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    """Dixon-Coles low-score correction factor."""
    if x == 0 and y == 0:
        return 1.0 - lam * mu * rho
    if x == 0 and y == 1:
        return 1.0 + lam * rho
    if x == 1 and y == 0:
        return 1.0 + mu * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def _dixon_coles(matrix, lam: float, mu: float, rho: float):
    matrix = matrix.copy()
    for i in range(min(2, matrix.shape[0])):
        for j in range(min(2, matrix.shape[1])):
            matrix[i, j] = max(matrix[i, j] * _tau_dc(i, j, lam, mu, rho), 1e-12)
    return matrix


class ZINBGoalModel:
    """
    League-level ZINB goal model.
    Thread-safe for read operations after fit().
    """

    def __init__(
        self,
        decay_rate: float = 0.05,
        min_matches: int = 5,
        max_goals: int = 8,
        rho: float = -0.13,
    ):
        self.decay_rate = decay_rate
        self.min_matches = min_matches
        self.max_goals = max_goals
        self.rho = rho

        self._attack: dict[int, TeamParams] = {}
        self._defense: dict[int, TeamParams] = {}
        self.home_advantage: float = 0.15
        self.fitted: bool = False

    # ------------------------------------------------------------------
    # Fitting
    # ------------------------------------------------------------------

    def fit(self, matches: list[dict], reference_date: str | None = None) -> "ZINBGoalModel":
        """
        Fit from match records.

        Each record must contain:
            home_team_id, away_team_id, home_goals, away_goals, match_date (ISO str)
        """
        if not _SCIPY_OK:
            logger.debug("ZINB: scipy unavailable, model not fitted.")
            return self
        if not matches:
            logger.debug("ZINB: no matches provided.")
            return self

        try:
            import pandas as pd
        except ImportError:
            logger.warning("ZINB: pandas not installed — fitting disabled.")
            return self

        df = pd.DataFrame(matches)
        df["match_date"] = pd.to_datetime(df["match_date"])
        ref = pd.Timestamp(reference_date) if reference_date else df["match_date"].max()

        weeks_ago = (ref - df["match_date"]).dt.days / 7.0
        df["w"] = np.exp(-self.decay_rate * weeks_ago.clip(lower=0))

        h_att: dict[int, tuple[list, list]] = {}
        a_att: dict[int, tuple[list, list]] = {}
        h_def: dict[int, tuple[list, list]] = {}
        a_def: dict[int, tuple[list, list]] = {}

        for _, row in df.iterrows():
            w = float(row["w"])
            h, a = int(row["home_team_id"]), int(row["away_team_id"])
            hg, ag = int(row["home_goals"]), int(row["away_goals"])
            h_att.setdefault(h, ([], []))[0].append(hg)
            h_att[h][1].append(w)
            a_att.setdefault(a, ([], []))[0].append(ag)
            a_att[a][1].append(w)
            h_def.setdefault(h, ([], []))[0].append(ag)
            h_def[h][1].append(w)
            a_def.setdefault(a, ([], []))[0].append(hg)
            a_def[a][1].append(w)

        all_teams = set(h_att) | set(a_att)
        for team_id in all_teams:
            att_g = h_att.get(team_id, ([], []))[0] + a_att.get(team_id, ([], []))[0]
            att_w = h_att.get(team_id, ([], []))[1] + a_att.get(team_id, ([], []))[1]
            def_g = h_def.get(team_id, ([], []))[0] + a_def.get(team_id, ([], []))[0]
            def_w = h_def.get(team_id, ([], []))[1] + a_def.get(team_id, ([], []))[1]
            self._attack[team_id] = fit_zinb(att_g, np.array(att_w) if att_w else None, self.min_matches)
            self._defense[team_id] = fit_zinb(def_g, np.array(def_w) if def_w else None, self.min_matches)

        home_avg = df["home_goals"].mean()
        away_avg = df["away_goals"].mean()
        self.home_advantage = max(0.0, float(home_avg - away_avg))
        self.fitted = True
        logger.info("ZINBGoalModel fitted: %d matches, %d teams, home_adv=%.3f",
                    len(df), len(all_teams), self.home_advantage)
        return self

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def _default_attack(self, home: bool = True) -> TeamParams:
        base = 1.35 if home else 1.10
        return TeamParams(mu=base + self.home_advantage * (0.5 if home else 0))

    def predict_goals(self, home_id: int, away_id: int) -> tuple[float, float]:
        """
        Return (mu_home, mu_away) expected goals, adjusted for home advantage
        and opponent defensive strength.
        """
        if not self.fitted or not _SCIPY_OK:
            return (1.35 + self.home_advantage * 0.5, 1.10)

        h_att = self._attack.get(home_id, self._default_attack(True))
        a_att = self._attack.get(away_id, self._default_attack(False))
        h_def = self._defense.get(home_id, TeamParams())
        a_def = self._defense.get(away_id, TeamParams())

        lg_atk = float(np.mean([p.mu for p in self._attack.values()])) if self._attack else 1.2
        lg_def = float(np.mean([p.mu for p in self._defense.values()])) if self._defense else 1.2

        home_factor = 1.0 + self.home_advantage / max(lg_atk, 0.5)
        home_mu = h_att.mu * (a_def.mu / max(lg_def, 0.5)) * home_factor
        away_mu = a_att.mu * (h_def.mu / max(lg_def, 0.5))
        return max(home_mu, 0.05), max(away_mu, 0.05)

    def score_matrix(self, home_id: int, away_id: int):
        """
        Joint score probability matrix M[i,j] = P(home_goals=i, away_goals=j).
        Returns None if scipy unavailable.
        """
        if not _SCIPY_OK or not self.fitted:
            return None

        home_mu, away_mu = self.predict_goals(home_id, away_id)
        h_att = self._attack.get(home_id, TeamParams(mu=home_mu))
        a_att = self._attack.get(away_id, TeamParams(mu=away_mu))

        k = np.arange(self.max_goals + 1)
        hp = zinb_pmf(k, home_mu, h_att.theta, h_att.pi)
        ap = zinb_pmf(k, away_mu, a_att.theta, a_att.pi)

        matrix = np.outer(hp, ap)
        matrix = _dixon_coles(matrix, home_mu, away_mu, self.rho)
        total = matrix.sum()
        if total > 0:
            matrix /= total
        return matrix
