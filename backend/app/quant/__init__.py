"""Quantitative validation and model-governance utilities for TiTiBet."""

from .backtest_report import ValidationSummary, summarize_backtest
from .ensemble import EnsemblePrediction, weighted_probability
from .expected_value import ValueAssessment, assess_value
from .probability import edge, expected_value, fair_odds, implied_probability, overround
from .calibration import CalibrationBin, CalibrationReport, calibration_bins, calibration_report, wilson_interval
from .validation import ValidationWindow, assert_point_in_time, require_sample_size, validate_binary_labels
from .leakage_guard import EvidenceTimestamp, assert_no_future_evidence, assert_training_before_test
from .walk_forward import WalkForwardFold, evaluate_walk_forward, make_expanding_folds
from .model_registry import ModelVersion

__all__ = [
    "CalibrationBin", "CalibrationReport", "calibration_bins", "calibration_report", "wilson_interval",
    "EnsemblePrediction", "weighted_probability",
    "EvidenceTimestamp", "assert_no_future_evidence", "assert_training_before_test",
    "ValueAssessment", "assess_value", "edge", "expected_value", "fair_odds", "implied_probability", "overround",
    "ModelVersion",
    "ValidationSummary", "summarize_backtest",
    "ValidationWindow", "assert_point_in_time", "require_sample_size", "validate_binary_labels",
    "WalkForwardFold", "evaluate_walk_forward", "make_expanding_folds",
]
