from datetime import date

from app.scripts_test_helpers import quality_lab_outcome_for, quality_lab_earliest_snapshots


def test_quality_lab_outcome_for_over_2_5():
    class FixtureLike:
        home_score = 2
        away_score = 1

    assert quality_lab_outcome_for("Over 2.5", FixtureLike()) == 1
    assert quality_lab_outcome_for("Under 2.5", FixtureLike()) == 0


def test_quality_lab_earliest_snapshot_selection():
    class Snapshot:
        def __init__(self, ts, ident):
            self.bookmaker = "Book"
            self.market_type = "Goals Over/Under"
            self.selection_name = "Over 2.5"
            self.pulled_at = ts
            self.id = ident

    first = Snapshot(date(2026, 1, 1), 1)
    later = Snapshot(date(2026, 1, 2), 2)
    assert quality_lab_earliest_snapshots([later, first])[0] is first
