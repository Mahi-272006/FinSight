"""Tests for trend math (M3): slope and trailing streaks. Golden values."""

from app.services.trend import linear_slope, trailing_streak


class TestLinearSlope:
    def test_strictly_increasing_line(self):
        # y = 2x + 1 at x=0,1,2,3 -> values [1,3,5,7]; slope exactly 2.
        assert abs(linear_slope([1.0, 3.0, 5.0, 7.0]) - 2.0) < 1e-12

    def test_strictly_decreasing_line(self):
        assert abs(linear_slope([7.0, 5.0, 3.0, 1.0]) - (-2.0)) < 1e-12

    def test_flat_series_zero_slope(self):
        assert linear_slope([5.0, 5.0, 5.0]) == 0.0

    def test_single_point_none(self):
        assert linear_slope([1.0]) is None

    def test_empty_none(self):
        assert linear_slope([]) is None

    def test_noisy_series_sits_between(self):
        # [10, 12, 11, 13]: overall upward but not a clean line; slope ~1.0
        # (hand: cov/var over x=0..3). Must be positive, between 0 and 2.
        s = linear_slope([10.0, 12.0, 11.0, 13.0])
        assert 0.0 < s < 2.0


class TestTrailingStreak:
    def test_consecutive_declines_full_streak(self):
        # 30->28->26->24: three trailing down-changes.
        assert trailing_streak([30.0, 28.0, 26.0, 24.0], "down") == 3

    def test_broken_streak_counts_only_trailing_run(self):
        # 30,28,29,27: 29->27 is down (+1), then 28->29 is up -> stop. Streak=1.
        assert trailing_streak([30.0, 28.0, 29.0, 27.0], "down") == 1

    def test_rising_series_zero_down_streak(self):
        assert trailing_streak([10.0, 20.0, 30.0], "down") == 0

    def test_up_streak(self):
        assert trailing_streak([10.0, 20.0, 30.0, 40.0], "up") == 3

    def test_flat_tail_breaks_streak(self):
        # 30,28,26,26: last change is flat -> streak 0.
        assert trailing_streak([30.0, 28.0, 26.0, 26.0], "down") == 0

    def test_two_points_single_change(self):
        assert trailing_streak([30.0, 28.0], "down") == 1

    def test_too_few_points(self):
        assert trailing_streak([30.0], "down") == 0
        assert trailing_streak([], "up") == 0
