import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
SCRIPTS_DIR = BACKEND_DIR / "scripts"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from model_quality_lab import earliest_snapshots, outcome_for


class FixtureLike:
    def __init__(self, home_score, away_score):
        self.home_score = home_score
        self.away_score = away_score


def test_quality_lab_outcome_for_over_2_5():
    assert outcome_for("Over 2.5", FixtureLike(2, 1)) == 1
    assert outcome_for("Under 2.5", FixtureLike(2, 1)) == 0


def test_quality_lab_earliest_snapshot_selection():
    class Snapshot:
        def __init__(self, ts, ident):
            self.bookmaker = "Book"
            self.market_type = "Goals Over/Under"
            self.selection_name = "Over 2.5"
            self.pulled_at = ts
            self.id = ident

    first = Snapshot("2026-01-01T00:00:00", 1)
    later = Snapshot("2026-01-02T00:00:00", 2)
    selected = earliest_snapshots([later, first])
    assert len(selected) == 1
    assert selected[0] is first
