"""Tests for SM-2 spaced repetition algorithm."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from notedrill.srs import sm2_quality, sm2_update, get_due_status, mastery_level


class TestSM2Quality:
    def test_correct_high_confidence(self):
        assert sm2_quality(True, "high") == 5

    def test_correct_medium_confidence(self):
        assert sm2_quality(True, "medium") == 4

    def test_correct_low_confidence(self):
        assert sm2_quality(True, "low") == 3

    def test_incorrect_high_confidence(self):
        assert sm2_quality(False, "high") == 2

    def test_incorrect_medium_confidence(self):
        assert sm2_quality(False, "medium") == 1

    def test_incorrect_low_confidence(self):
        assert sm2_quality(False, "low") == 0

    def test_default_confidence_correct(self):
        assert sm2_quality(True) == 4

    def test_default_confidence_incorrect(self):
        assert sm2_quality(False) == 1


class TestSM2Update:
    def test_first_success(self):
        """First successful review: quality 4, 0 reps → interval 1 day, reps 1."""
        new_reps, new_ef, new_interval, next_date = sm2_update(4, 0, 2.5, 0.0)
        assert new_reps == 1
        assert new_ef == 2.5  # quality 4: no change
        assert 0.9 <= new_interval <= 1.1  # with fuzz

    def test_second_success(self):
        """Second successful review: quality 4, 1 rep → interval 6 days."""
        new_reps, new_ef, new_interval, next_date = sm2_update(4, 1, 2.5, 1.0)
        assert new_reps == 2
        assert 5.7 <= new_interval <= 6.3  # 6 * fuzz

    def test_third_success_expands(self):
        """Third successful review: interval * ease_factor."""
        new_reps, new_ef, new_interval, next_date = sm2_update(4, 2, 2.5, 6.0)
        assert new_reps == 3
        assert 14.2 <= new_interval <= 15.8  # 6 * 2.5 = 15 * fuzz

    def test_failure_resets(self):
        """Quality < 3 resets repetitions and sets interval to 1."""
        new_reps, new_ef, new_interval, next_date = sm2_update(1, 5, 2.5, 30.0)
        assert new_reps == 0
        assert new_interval == 1.0  # always 1.0 for failures

    def test_perfect_recall_increases_ef(self):
        """Quality 5 increases ease factor."""
        _, new_ef, _, _ = sm2_update(5, 3, 2.5, 15.0)
        assert new_ef >= 2.6  # EF increases with quality 5

    def test_poor_recall_decreases_ef(self):
        """Quality 2 decreases ease factor but floors at 1.3."""
        _, new_ef, _, _ = sm2_update(2, 0, 2.5, 1.0)
        assert new_ef < 2.5
        assert new_ef >= 1.3

    def test_ease_factor_never_below_1_3(self):
        """EF should never go below 1.3."""
        _, new_ef, _, _ = sm2_update(0, 0, 1.3, 1.0)
        assert new_ef == 1.3

    def test_next_date_is_in_future(self):
        """Next review date should be in the future."""
        _, _, _, next_date = sm2_update(4, 0, 2.5, 0.0)
        next_dt = datetime.fromisoformat(next_date)
        assert next_dt > datetime.now(timezone.utc)


class TestDueStatus:
    def test_new_card(self):
        assert get_due_status(0.0, "") == "new"

    def test_due_card(self):
        past = datetime.now(timezone.utc) - timedelta(days=1)
        assert get_due_status(1.0, past.isoformat()) == "due"

    def test_learning_card(self):
        future = datetime.now(timezone.utc) + timedelta(hours=12)
        assert get_due_status(0.5, future.isoformat()) == "learning"

    def test_young_card(self):
        future = datetime.now(timezone.utc) + timedelta(days=7)
        assert get_due_status(7.0, future.isoformat()) == "young"

    def test_mature_card(self):
        future = datetime.now(timezone.utc) + timedelta(days=30)
        assert get_due_status(30.0, future.isoformat()) == "mature"


class TestMasteryLevel:
    def test_beginner(self):
        assert "初学" in mastery_level(0.0, 0)

    def test_novice(self):
        assert "入门" in mastery_level(3.0, 1)

    def test_consolidating(self):
        assert "巩固" in mastery_level(10.0, 2)

    def test_proficient(self):
        assert "熟练" in mastery_level(30.0, 5)

    def test_master(self):
        assert "精通" in mastery_level(100.0, 10)
