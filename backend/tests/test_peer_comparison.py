"""Tests for peer comparison (M3).

Golden values hand-computed; the leave-one-out math is checked against
manually calculated z-scores so any drift in the statistics breaks tests
before it breaks reports.
"""

from datetime import date

from app.services.peer_comparison import (
    CompanyMetricPoint,
    compare_all_periods,
    compare_metric,
)


def point(cik: int, ticker: str, value: float, fy: int = 2025, metric: str = "operating_income_margin") -> CompanyMetricPoint:
    return CompanyMetricPoint(
        cik=cik, ticker=ticker, metric=metric, fiscal_year=fy,
        fiscal_period="FY", period_type="annual", value=value,
        period_end=date(fy, 6, 30),
    )


class TestCompareMetric:
    def test_requires_at_least_two_companies(self):
        assert compare_metric([point(1, "A", 0.3)]) is None

    def test_ranking_highest_first(self):
        ranking = compare_metric([
            point(1, "A", 0.20), point(2, "B", 0.30), point(3, "C", 0.10),
        ])
        assert [p.ticker for p in ranking.points] == ["B", "A", "C"]
        assert ranking.rank_of(2) == 1
        assert ranking.rank_of(3) == 3
        assert ranking.rank_of(999) is None

    def test_group_statistics_hand_verified(self):
        # Values 0.10, 0.20, 0.30: mean 0.20, median 0.20,
        # sample std = sqrt(((0.01)+(0)+(0.01))/2) = sqrt(0.01) = 0.10.
        ranking = compare_metric([
            point(1, "A", 0.10), point(2, "B", 0.20), point(3, "C", 0.30),
        ])
        assert abs(ranking.peer_mean - 0.20) < 1e-12
        assert abs(ranking.peer_median - 0.20) < 1e-12
        assert abs(ranking.peer_std - 0.10) < 1e-12

    def test_leave_one_out_z_scores_hand_verified(self):
        # Values: A=0.10, B=0.20, C=0.30.
        # For A: reference {0.20, 0.30} -> mean 0.25, std = 0.070710678...
        #   z = (0.10 - 0.25) / 0.070710678 = -2.121320344
        # For C: reference {0.10, 0.20} -> mean 0.15, std = 0.070710678...
        #   z = (0.30 - 0.15) / 0.070710678 = +2.121320344
        # For B: reference {0.10, 0.30} -> mean 0.20, std = 0.141421356...
        #   z = (0.20 - 0.20) / 0.141421356 = 0.0
        ranking = compare_metric([
            point(1, "A", 0.10), point(2, "B", 0.20), point(3, "C", 0.30),
        ])
        assert abs(ranking.z_scores[1] - (-2.1213203435596424)) < 1e-9
        assert abs(ranking.z_scores[3] - 2.1213203435596424) < 1e-9
        assert abs(ranking.z_scores[2] - 0.0) < 1e-12
        # Reference counts: each subject uses the OTHER two.
        assert ranking.z_reference_counts == {1: 2, 2: 2, 3: 2}

    def test_small_peer_set_flagged_not_meaningful(self):
        # 3 companies -> 2 reference points each -> NOT statistically
        # meaningful; the report must label z-scores 'indicative only'.
        ranking = compare_metric([
            point(1, "A", 0.10), point(2, "B", 0.20), point(3, "C", 0.30),
        ])
        assert ranking.is_z_statistically_meaningful() is False

    def test_large_peer_set_flagged_meaningful(self):
        # 9 companies -> 8 reference points each -> meaningful by our rule.
        points = [point(i, f"C{i}", float(i)) for i in range(1, 10)]
        ranking = compare_metric(points)
        assert ranking.is_z_statistically_meaningful() is True

    def test_tied_values_share_ranking_without_crash(self):
        # A=0.20, B=0.20, C=0.10. For C the reference is {0.20, 0.20} ->
        # reference std exactly 0 -> z is None BY DESIGN (never inf). For A
        # and B the reference includes the differing 0.10, so std > 0 and
        # z-scores exist. This pins the divide-by-zero guard.
        ranking = compare_metric([
            point(1, "A", 0.20), point(2, "B", 0.20), point(3, "C", 0.10),
        ])
        assert set(ranking.z_scores) == {1, 2}
        assert 3 not in ranking.z_scores          # degenerate reference (std 0)
        assert ranking.z_reference_counts == {1: 2, 2: 2, 3: 2}

    def test_identical_reference_values_yield_no_z(self):
        # A vs two identical peers: reference std = 0 -> z undefined (None),
        # never inf. The UI must show 'no dispersion' not a division error.
        ranking = compare_metric([
            point(1, "A", 0.40), point(2, "B", 0.20), point(3, "C", 0.20),
        ])
        assert 1 not in ranking.z_scores  # subject A: reference {0.2, 0.2}, std 0
        assert ranking.z_reference_counts[1] == 2

    def test_two_company_pair_has_no_z(self):
        # n=2 -> 1 reference point -> std undefined -> no z-score for either.
        ranking = compare_metric([point(1, "A", 0.1), point(2, "B", 0.2)])
        assert ranking.z_scores == {}
        assert ranking.n_companies == 2
        assert ranking.rank_of(2) == 1


class TestCompareAllPeriods:
    def test_groups_by_period_and_sorts_newest_first(self):
        points = [
            point(1, "A", 0.10, fy=2024), point(2, "B", 0.20, fy=2024),
            point(1, "A", 0.30, fy=2025), point(2, "B", 0.25, fy=2025),
        ]
        rankings = compare_all_periods(points)
        assert len(rankings) == 2
        assert rankings[0].fiscal_year == 2025  # newest first
        assert rankings[1].fiscal_year == 2024

    def test_singleton_periods_skipped(self):
        points = [
            point(1, "A", 0.10, fy=2023),  # only company in 2023
            point(1, "A", 0.30, fy=2025), point(2, "B", 0.25, fy=2025),
        ]
        rankings = compare_all_periods(points)
        assert len(rankings) == 1
        assert rankings[0].fiscal_year == 2025
