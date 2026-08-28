# TiTiBet Quantitative Remediation Status

## Verified
- 174 backend tests pass locally on the quantitative branch.
- Frontend Vite production build passes locally.
- Strict historical benchmark runs against the backend SQLite database.
- Current adaptive weights and current league suppression are disabled in strict research mode.
- Quantitative metrics include Brier score, log-loss, calibration error, EV and ROI diagnostics.

## Research rules
- Model quality must be evaluated separately from live signal gates.
- Historical evaluation must use point-in-time evidence only.
- EV is an economic criterion, not a proxy for probability quality.
- LLM recommendations are hypotheses and require deterministic out-of-sample validation.
- No model or threshold is promoted to production from a small retrospective sample.

## Next decision gate
Run the ungated model-quality laboratory over a sufficiently large historical scope. Promote only market/engine combinations that show adequate sample size, stable calibration and positive out-of-sample economic value.
