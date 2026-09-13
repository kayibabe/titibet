# Stage 1 validation record — 2026-09-13

## Scope

This record covers the cold validation of the Stage 1 evidence persistence and
restore rehearsal on branch `feat/quant-validation-framework` at `bfef914`.
The rehearsal target was disposable and was removed after verification. The
source database and existing untracked analysis/database artifacts were not
modified.

## Restore and evidence results

The rehearsal copied `titibet_prod.db`, ran migrations twice, imported legacy
evidence, and reconciled the copied database:

| Check | Result |
| --- | --- |
| Fixtures | 45,492 source and target |
| Market snapshots | 4,970,351 source and target |
| Tracked bets | 64 source and target |
| Profit/loss | 1,516,500.0 source and target |
| Fixture revisions created | 45,492 |
| Odds quotes created | 4,970,351 |
| Legacy evidence class | `legacy_import` |
| Foreign keys | enabled (`PRAGMA foreign_keys = 1`) |
| Feature snapshots | 0, expected for this legacy database |

The first attempt used the system temporary directory and failed because the
`C:` volume had no free space. The identical rehearsal was rerun on `D:` and
completed successfully. Both disposable copies were removed.

The backend Stage 1 tests also verify migration idempotency, foreign-key
enforcement, legacy receipt classification, and the required odds-quote lookup
index.

## Checks

- Backend tests: `228 passed`
- Frontend ESLint: passed
- Frontend Vite production build: passed
- Focused Stage 1 Ruff check: passed
- Full Ruff: failed with 238 findings across existing application/tests/scripts
- Full mypy: failed with 205 findings across existing application code
- `git diff --check`: passed

The full static-check failures are baseline debt: `HEAD` and `origin/main`
resolve to the same tree (`bb8e1c92eda054b9d0…`). They are not attributable to
a branch-only diff. The focused Stage 1 slice is clean under Ruff.

## Merge and readiness decision

Do not merge `feat/quant-validation-framework`. `origin/main` already contains
the identical tree; the histories diverge, but merging would add no content.

This validation does not authorize market promotion or live staking. The next
evidence phase must collect genuine prospective, pre-kickoff records with
immutable quote/model/feature lineage, then pass calibration, chronological
walk-forward, drawdown, and minimum-sample gates before any promotion review.

## Prospective gate checklist

- [ ] Capture provider observation receipt time and raw-payload fingerprint.
- [ ] Persist executable quote, market identity, and selection lineage.
- [ ] Persist model/configuration identity and point-in-time feature snapshot.
- [ ] Reject missing, late, post-kickoff, or mutable evidence from promotion.
- [ ] Run chronological walk-forward evaluation on unseen observations.
- [ ] Report calibration, uncertainty, ROI/EV, drawdown, and sample size.
- [ ] Keep all outputs research-only until the evidence threshold is met.
