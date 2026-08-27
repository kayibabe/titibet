"""Calibration utilities for binary prediction probabilities.

The module deliberately contains no production betting gates. It provides
small calibrators and walk-forward selection helpers for quantitative research
so calibration is fitted only on observations that occurred before evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import log, sqrt
from typing import Iterable, Sequence

import numpy as np
from scipy.optimize import minimize


@dataclass(frozen=True)
class CalibrationBin:
    lower: float
    upper: float
    n: int
    mean_predicted: float
    observed_rate: float
    absolute_error: float


@dataclass(frozen=True)
class CalibrationReport:
    n: int
    brier: float
    log_loss: float
    mean_absolute_calibration_error: float
    bins: tuple[CalibrationBin, ...]


@dataclass(frozen=True)
class CalibrationFit:
    method: str
    n_train: int
    brier: float
    log_loss: float
    calibrator: "ProbabilityCalibrator"


class ProbabilityCalibrator:
    method = "identity"

    def fit(self, probabilities: Sequence[float], outcomes: Sequence[int]) -> "ProbabilityCalibrator":
        raise NotImplementedError

    def predict(self, probabilities: Sequence[float]) -> np.ndarray:
        raise NotImplementedError


class IdentityCalibrator(ProbabilityCalibrator):
    method = "identity"

    def fit(self, probabilities: Sequence[float], outcomes: Sequence[int]) -> "IdentityCalibrator":
        _validate(probabilities, outcomes)
        return self

    def predict(self, probabilities: Sequence[float]) -> np.ndarray:
        return np.asarray(_clip_probabilities(probabilities), dtype=float)


class PlattCalibrator(ProbabilityCalibrator):
    """Logistic calibration on the logit of the base probability."""

    method = "platt"

    def __init__(self) -> None:
        self.intercept = 0.0
        self.slope = 1.0

    def fit(self, probabilities: Sequence[float], outcomes: Sequence[int]) -> "PlattCalibrator":
        pairs = _validate(probabilities, outcomes)
        x = np.asarray([_logit(p) for p, _ in pairs], dtype=float)
        y = np.asarray([v for _, v in pairs], dtype=float)

        def objective(theta: np.ndarray) -> float:
            z = theta[0] + theta[1] * x
            return float(np.mean(np.maximum(z, 0.0) - z * y + np.log1p(np.exp(-np.abs(z)))))

        result = minimize(objective, np.array([0.0, 1.0]), method="L-BFGS-B")
        if not result.success or not np.all(np.isfinite(result.x)):
            raise ValueError("Platt calibration optimization failed")
        self.intercept = float(result.x[0])
        self.slope = float(result.x[1])
        return self

    def predict(self, probabilities: Sequence[float]) -> np.ndarray:
        x = np.asarray([_logit(p) for p in probabilities], dtype=float)
        z = self.intercept + self.slope * x
        return _sigmoid_array(z)


class BetaCalibrator(ProbabilityCalibrator):
    """Beta calibration using log(p) and log(1-p) features."""

    method = "beta"

    def __init__(self) -> None:
        self.intercept = 0.0
        self.a = 1.0
        self.b = -1.0

    def fit(self, probabilities: Sequence[float], outcomes: Sequence[int]) -> "BetaCalibrator":
        pairs = _validate(probabilities, outcomes)
        p = np.asarray([_clip_probability(v) for v, _ in pairs], dtype=float)
        y = np.asarray([v for _, v in pairs], dtype=float)
        x1, x2 = np.log(p), np.log1p(-p)

        def objective(theta: np.ndarray) -> float:
            z = theta[0] + theta[1] * x1 + theta[2] * x2
            return float(np.mean(np.maximum(z, 0.0) - z * y + np.log1p(np.exp(-np.abs(z)))))

        result = minimize(objective, np.array([0.0, 1.0, -1.0]), method="L-BFGS-B")
        if not result.success or not np.all(np.isfinite(result.x)):
            raise ValueError("Beta calibration optimization failed")
        self.intercept, self.a, self.b = map(float, result.x)
        return self

    def predict(self, probabilities: Sequence[float]) -> np.ndarray:
        p = np.asarray([_clip_probability(v) for v in probabilities], dtype=float)
        z = self.intercept + self.a * np.log(p) + self.b * np.log1p(-p)
        return _sigmoid_array(z)


class IsotonicCalibrator(ProbabilityCalibrator):
    """Monotone calibration via pool-adjacent-violators."""

    method = "isotonic"

    def __init__(self) -> None:
        self.x_: np.ndarray | None = None
        self.y_: np.ndarray | None = None

    def fit(self, probabilities: Sequence[float], outcomes: Sequence[int]) -> "IsotonicCalibrator":
        pairs = sorted(_validate(probabilities, outcomes), key=lambda item: item[0])
        xs = np.asarray([p for p, _ in pairs], dtype=float)
        ys = np.asarray([y for _, y in pairs], dtype=float)
        block_start: list[int] = []
        block_end: list[int] = []
        block_mean: list[float] = []
        block_weight: list[float] = []

        for i, value in enumerate(ys):
            block_start.append(i)
            block_end.append(i)
            block_mean.append(float(value))
            block_weight.append(1.0)
            while len(block_mean) >= 2 and block_mean[-2] > block_mean[-1]:
                w1, w2 = block_weight[-2], block_weight[-1]
                merged_mean = (block_mean[-2] * w1 + block_mean[-1] * w2) / (w1 + w2)
                block_end[-2] = block_end[-1]
                block_mean[-2] = merged_mean
                block_weight[-2] = w1 + w2
                block_start.pop()
                block_end.pop()
                block_mean.pop()
                block_weight.pop()

        self.x_ = np.asarray([xs[end] for end in block_end], dtype=float)
        self.y_ = np.asarray(block_mean, dtype=float)
        return self

    def predict(self, probabilities: Sequence[float]) -> np.ndarray:
        if self.x_ is None or self.y_ is None:
            raise ValueError("isotonic calibrator is not fitted")
        p = np.asarray(probabilities, dtype=float)
        if len(self.x_) == 1:
            return np.full_like(p, self.y_[0], dtype=float)
        return np.interp(p, self.x_, self.y_, left=self.y_[0], right=self.y_[-1])


def _clip_probability(value: float) -> float:
    return min(1.0 - 1e-12, max(1e-12, float(value)))


def _clip_probabilities(values: Iterable[float]) -> list[float]:
    return [_clip_probability(v) for v in values]


def _logit(p: float) -> float:
    p = _clip_probability(p)
    return log(p / (1.0 - p))


def _sigmoid_array(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    out = np.empty_like(values)
    positive = values >= 0
    out[positive] = 1.0 / (1.0 + np.exp(-values[positive]))
    ev = np.exp(values[~positive])
    out[~positive] = ev / (1.0 + ev)
    return np.clip(out, 1e-12, 1.0 - 1e-12)


def _validate(probabilities: Iterable[float], outcomes: Iterable[int]) -> list[tuple[float, int]]:
    pairs = [(float(p), int(y)) for p, y in zip(probabilities, outcomes)]
    if not pairs:
        raise ValueError("at least one observation is required")
    for p, y in pairs:
        if not 0.0 <= p <= 1.0:
            raise ValueError("probabilities must be between 0 and 1")
        if y not in (0, 1):
            raise ValueError("outcomes must be binary")
    return pairs


def calibration_bins(
    probabilities: Iterable[float], outcomes: Iterable[int], *, n_bins: int = 10
) -> tuple[CalibrationBin, ...]:
    if n_bins < 2:
        raise ValueError("n_bins must be at least 2")
    pairs = _validate(probabilities, outcomes)
    bins: list[CalibrationBin] = []
    width = 1.0 / n_bins
    for i in range(n_bins):
        lower = i * width
        upper = 1.0 if i == n_bins - 1 else (i + 1) * width
        members = [
            (p, y)
            for p, y in pairs
            if (lower <= p < upper) or (i == n_bins - 1 and p == 1.0)
        ]
        if not members:
            continue
        mean_p = sum(p for p, _ in members) / len(members)
        observed = sum(y for _, y in members) / len(members)
        bins.append(CalibrationBin(lower, upper, len(members), mean_p, observed, abs(mean_p - observed)))
    return tuple(bins)


def calibration_report(
    probabilities: Iterable[float], outcomes: Iterable[int], *, n_bins: int = 10
) -> CalibrationReport:
    pairs = _validate(probabilities, outcomes)
    ps = [p for p, _ in pairs]
    ys = [y for _, y in pairs]
    epsilon = 1e-15
    brier = sum((p - y) ** 2 for p, y in pairs) / len(pairs)
    ll = sum(
        -(y * log(max(epsilon, min(1.0 - epsilon, p)))
        + (1 - y) * log(max(epsilon, min(1.0 - epsilon, 1.0 - p))))
        for p, y in pairs
    ) / len(pairs)
    bins = calibration_bins(ps, ys, n_bins=n_bins)
    total = sum(b.n for b in bins)
    mace = sum(b.absolute_error * b.n for b in bins) / total
    return CalibrationReport(len(pairs), brier, ll, mace, bins)


def fit_calibrator(method: str, probabilities: Sequence[float], outcomes: Sequence[int]) -> ProbabilityCalibrator:
    methods = {"identity": IdentityCalibrator, "platt": PlattCalibrator, "beta": BetaCalibrator, "isotonic": IsotonicCalibrator}
    try:
        calibrator = methods[method.lower()]()
    except KeyError as exc:
        raise ValueError(f"unknown calibration method: {method}") from exc
    return calibrator.fit(probabilities, outcomes)


def evaluate_calibrator(
    calibrator: ProbabilityCalibrator, probabilities: Sequence[float], outcomes: Sequence[int]
) -> CalibrationReport:
    calibrated = calibrator.predict(probabilities)
    return calibration_report(calibrated.tolist(), outcomes)


def select_calibrator(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    *,
    methods: Sequence[str] = ("identity", "platt", "beta", "isotonic"),
    min_train: int = 50,
    validation_fraction: float = 0.25,
) -> CalibrationFit:
    """Select a calibrator using a chronological holdout inside the training window.

    The final selected calibrator is refit on the complete training window.
    This prevents selecting a flexible calibrator from the same observations on
    which it is scored. The outer walk-forward evaluation remains untouched.
    """
    if len(probabilities) < min_train:
        raise ValueError(f"at least {min_train} observations are required")
    if not 0.1 <= validation_fraction <= 0.5:
        raise ValueError("validation_fraction must be between 0.1 and 0.5")
    split = int(len(probabilities) * (1.0 - validation_fraction))
    if split < min_train // 2 or len(probabilities) - split < 5:
        raise ValueError("training window is too small for calibration holdout")

    train_p, val_p = probabilities[:split], probabilities[split:]
    train_y, val_y = outcomes[:split], outcomes[split:]
    candidates: list[CalibrationFit] = []
    for method in methods:
        try:
            cal = fit_calibrator(method, train_p, train_y)
            report = evaluate_calibrator(cal, val_p, val_y)
            final_cal = fit_calibrator(method, probabilities, outcomes)
        except (ValueError, FloatingPointError):
            continue
        candidates.append(CalibrationFit(method, len(probabilities), report.brier, report.log_loss, final_cal))
    if not candidates:
        raise ValueError("no calibration method could be fitted")
    return min(candidates, key=lambda c: (c.brier, c.log_loss, c.method))


def walk_forward_calibration(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    *,
    train_size: int = 100,
    test_size: int = 25,
    min_train: int = 50,
    methods: Sequence[str] = ("identity", "platt", "beta", "isotonic"),
) -> dict:
    """Chronological calibration evaluation with no future leakage."""
    _validate(probabilities, outcomes)
    if train_size < min_train or test_size < 1:
        raise ValueError("train_size/test_size are invalid")
    n = len(probabilities)
    if n <= train_size:
        return {"n": 0, "folds": [], "summary": None}

    rows: list[dict] = []
    start = train_size
    while start < n:
        train_start = max(0, start - train_size)
        train_p = probabilities[train_start:start]
        train_y = outcomes[train_start:start]
        test_end = min(n, start + test_size)
        test_p = probabilities[start:test_end]
        test_y = outcomes[start:test_end]
        if len(train_p) < min_train or not test_p:
            break
        try:
            chosen = select_calibrator(train_p, train_y, methods=methods, min_train=min_train)
        except ValueError:
            chosen = CalibrationFit("identity", len(train_p), 0.0, 0.0, IdentityCalibrator().fit(train_p, train_y))
        report = evaluate_calibrator(chosen.calibrator, test_p, test_y)
        rows.append({
            "start": start, "end": test_end, "train_n": len(train_p),
            "selected_method": chosen.method, "test_n": len(test_p),
            "brier": report.brier, "log_loss": report.log_loss,
            "calibration_error": report.mean_absolute_calibration_error,
        })
        start = test_end

    if not rows:
        return {"n": 0, "folds": [], "summary": None}
    total = sum(r["test_n"] for r in rows)
    return {
        "n": total,
        "folds": rows,
        "summary": {
            "brier": sum(r["brier"] * r["test_n"] for r in rows) / total,
            "log_loss": sum(r["log_loss"] * r["test_n"] for r in rows) / total,
            "calibration_error": sum(r["calibration_error"] * r["test_n"] for r in rows) / total,
            "method_counts": {
                method: sum(1 for r in rows if r["selected_method"] == method)
                for method in sorted({r["selected_method"] for r in rows})
            },
        },
    }


def wilson_interval(wins: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n <= 0 or not 0 <= wins <= n:
        raise ValueError("wins must be between 0 and n, with n positive")
    p = wins / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * sqrt((p * (1 - p) / n) + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)
