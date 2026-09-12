"""Simple calibrated probability ensemble.

The ensemble combines probabilities, not confidence labels. Weights are supplied
by the caller and should be estimated only from historical training data, never
from the held-out evaluation period.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class EnsemblePrediction:
    probability: float
    component_probabilities: Mapping[str, float]
    weights: Mapping[str, float]


def weighted_probability(
    probabilities: Mapping[str, float],
    weights: Mapping[str, float],
) -> EnsemblePrediction:
    if not probabilities:
        raise ValueError("probabilities must not be empty")
    if set(probabilities) != set(weights):
        raise ValueError("probabilities and weights must have identical keys")
    total_weight = sum(float(w) for w in weights.values())
    if total_weight <= 0:
        raise ValueError("total weight must be positive")

    normalized = {k: float(w) / total_weight for k, w in weights.items()}
    for name, p in probabilities.items():
        if not 0.0 <= float(p) <= 1.0:
            raise ValueError(f"probability for {name!r} must be between 0 and 1")
    probability = sum(float(probabilities[k]) * normalized[k] for k in probabilities)
    return EnsemblePrediction(
        probability=max(0.0, min(1.0, probability)),
        component_probabilities=dict(probabilities),
        weights=normalized,
    )
