"""Walk-forward evaluation helpers.

The evaluator is intentionally generic: the caller supplies a chronological
sequence and a fit/evaluate function. No future rows are ever presented to the
fit step for a test fold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, Sequence, TypeVar


T = TypeVar("T")
M = TypeVar("M")
R = TypeVar("R")


@dataclass(frozen=True)
class WalkForwardFold:
    train_start: int
    train_end: int
    test_start: int
    test_end: int

    @property
    def train_slice(self) -> slice:
        return slice(self.train_start, self.train_end)

    @property
    def test_slice(self) -> slice:
        return slice(self.test_start, self.test_end)


def make_expanding_folds(
    n_observations: int,
    *,
    minimum_train: int,
    test_size: int,
    step: int | None = None,
) -> tuple[WalkForwardFold, ...]:
    """Create chronological expanding-window folds using half-open indexes."""
    if n_observations <= 0:
        raise ValueError("n_observations must be positive")
    if minimum_train <= 0 or test_size <= 0:
        raise ValueError("minimum_train and test_size must be positive")
    step = test_size if step is None else step
    if step <= 0:
        raise ValueError("step must be positive")

    folds: list[WalkForwardFold] = []
    test_start = minimum_train
    while test_start < n_observations:
        test_end = min(test_start + test_size, n_observations)
        folds.append(WalkForwardFold(0, test_start, test_start, test_end))
        test_start += step
    return tuple(folds)


def evaluate_walk_forward(
    observations: Sequence[T],
    fit: Callable[[Sequence[T]], M],
    evaluate: Callable[[M, Sequence[T]], R],
    *,
    minimum_train: int,
    test_size: int,
    step: int | None = None,
) -> list[R]:
    """Fit only on past observations and evaluate on the immediately following fold."""
    results: list[R] = []
    for fold in make_expanding_folds(
        len(observations), minimum_train=minimum_train, test_size=test_size, step=step
    ):
        model = fit(observations[fold.train_slice])
        results.append(evaluate(model, observations[fold.test_slice]))
    return results
