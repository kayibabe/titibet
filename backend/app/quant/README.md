# TiTiBet Quantitative Validation Layer

The `app.quant` package is the governance boundary between prediction engines and
historical evaluation. It must remain side-effect free wherever possible.

## Rules

1. **Point-in-time evidence only.** A feature, parameter, suppression rule, or
   performance weight must be derived only from information available at the
   prediction timestamp.
2. **Probability and value are separate.** A high model probability does not by
   itself qualify a bet. Production value qualification should apply explicit
   probability, edge, EV, price, and market-quality gates.
3. **Walk forward in time.** Fit/tune on past observations and evaluate on a later,
   unseen period. Never tune on the final evaluation fold.
4. **LLMs generate hypotheses, not statistical conclusions.** Any AI-generated
   threshold or strategy proposal must pass deterministic validation before it can
   affect production configuration.
5. **Version everything.** Store the model version, configuration identity, feature
   snapshot timestamp, and evaluation window alongside validation results.

## Current primitives

- `probability.py`: implied probability, fair odds, edge, EV, overround.
- `expected_value.py`: explicit multi-gate value assessment.
- `calibration.py`: Brier score, log-loss, calibration bins, Wilson intervals.
- `walk_forward.py`: expanding-window chronological evaluation.
- `leakage_guard.py`: future-evidence and train/test overlap checks.
- `feature_snapshot.py`: immutable point-in-time feature container.
- `ensemble.py`: weighted probability combination.
- `statistical_tests.py`: conservative hit-rate significance comparison.
- `model_registry.py`: immutable model/configuration identity.

## Planned integration

The existing Bayesian, Poisson, ZINB, and Dual Engine outputs should first be
captured as comparable probabilities, then calibrated and evaluated out-of-sample.
Only after that comparison should live ranking/qualification rules be changed.
