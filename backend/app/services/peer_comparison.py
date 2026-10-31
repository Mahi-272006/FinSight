"""Peer comparison: cross-company metric tables, ranks, and z-scores.

WHY THIS EXISTS:
A metric like "operating margin = 46.8%" is meaningless alone. Analysts judge
numbers RELATIVE to peers ("highest of the three") and RELATIVE to history
("widening lead"). This module provides the peer-relative layer. The
historical/trend layer lives in trend.py.

Design decisions:
- PURE FUNCTIONS over plain rows (no DB, no ORM). The API (M9) and evaluation
  (M8) reuse these unchanged; unit tests run in milliseconds.
- LEAVE-ONE-OUT z-scores: each company is scored against the OTHER peers only.
  Including the company in its own reference mean would pull the mean toward
  it and shrink every z-score toward zero (a subtle bias that flatters the
  largest company). We compute the reference mean/std from peers EXCLUDING
  the subject company.
- HONEST SMALL-SAMPLE LABELS: with n companies, leave-one-out uses n-1
  reference points. With n=3 that is 2 points -- far too few for a
  statistically meaningful z-score. The module records `n_reference` so the
  report can say "indicative only" instead of printing false precision.
  (Interview point: knowing when a statistic is NOT trustworthy is as
  important as computing it.)
- MISSING PERIODS: a company absent from a period is simply not ranked in it
  (never imputed, never zero-filled -- that would corrupt both mean and rank).
- PERIOD ALIGNMENT by fiscal-year LABEL. Caveat documented in the module
  docstring: for non-calendar fiscal years (MSFT June, AAPL Sept, NVDA Jan)
  "FY2025" covers different calendar windows. We align on the company's own
  fiscal-year label (standard analyst practice) but every comparison row
  carries each company's period_end so the UI can disclose the windows.
  Perfect calendar alignment would require restating to calendar quarters --
  out of M3 scope, documented as a limitation.
"""

from dataclasses import dataclass, field
from datetime import date
from statistics import mean, median, stdev
from typing import Iterable, Sequence


@dataclass(frozen=True)
class CompanyMetricPoint:
    """One company's value for one metric in one fiscal period."""

    cik: int
    ticker: str
    metric: str
    fiscal_year: int
    fiscal_period: str  # FY / Q1..Q4
    period_type: str    # annual / quarterly
    value: float
    period_end: date | None = None  # disclosed so UIs can show the window


@dataclass
class PeerRanking:
    """All companies' values for one metric-period, ranked, with statistics."""

    metric: str
    fiscal_year: int
    fiscal_period: str
    period_type: str
    points: list[CompanyMetricPoint] = field(default_factory=list)  # ranked by value desc
    peer_mean: float | None = None
    peer_median: float | None = None
    peer_std: float | None = None      # sample std (stdev); None if n<2
    z_scores: dict[int, float] = field(default_factory=dict)  # cik -> leave-one-out z
    z_reference_counts: dict[int, int] = field(default_factory=dict)  # cik -> n used

    @property
    def n_companies(self) -> int:
        return len(self.points)

    def rank_of(self, cik: int) -> int | None:
        """1-based rank (highest value = 1). None if company absent."""
        for i, p in enumerate(self.points, start=1):
            if p.cik == cik:
                return i
        return None

    def is_z_statistically_meaningful(self) -> bool:
        """False when reference points are too few for a real z-score.

        Rule of thumb: a z-score is treated as indicative (not significant)
        below 8 reference points. With 3 companies we have 2 -- the report
        labels them 'indicative only'. This guard prevents the UI from ever
        presenting a 2-point z-score as if it were a calibrated statistic.
        """
        return all(n >= 8 for n in self.z_reference_counts.values())


def _leave_one_out_z(
    values: dict[int, float], subject_cik: int
) -> tuple[float | None, int]:
    """z-score for subject vs all OTHER companies (leave-one-out).

    Returns (z, n_reference). z is None when:
    - fewer than 2 reference companies (std undefined), or
    - reference std is 0 (all peers identical -> any deviation is undefined
      relative to a degenerate distribution; we refuse to emit inf).

    Using abs(std) guard: stdev is always >= 0, but the explicit check keeps
    the divide-by-zero impossible by construction.
    """
    reference = [v for cik, v in values.items() if cik != subject_cik]
    n = len(reference)
    if n < 2:
        return None, n
    ref_std = stdev(reference)
    if ref_std == 0:
        return None, n
    return (values[subject_cik] - mean(reference)) / ref_std, n


def compare_metric(
    points: Iterable[CompanyMetricPoint],
) -> PeerRanking | None:
    """Build a PeerRanking from all companies' points for ONE metric-period.

    Returns None if fewer than 2 companies have data for this period (a
    'comparison' of one company is not a comparison).

    Ranking is by value DESCENDING (highest margin/growth = rank 1). For
    metrics where lower is better (e.g. debt_to_assets), the caller can
    invert -- ranking itself is a display concept, not a verdict of quality.
    """
    points = list(points)
    if len(points) < 2:
        return None

    # Group values by company (last wins if a company has duplicates).
    values: dict[int, float] = {p.cik: p.value for p in points}
    first = points[0]

    ranked = sorted(points, key=lambda p: p.value, reverse=True)

    ranking = PeerRanking(
        metric=first.metric,
        fiscal_year=first.fiscal_year,
        fiscal_period=first.fiscal_period,
        period_type=first.period_type,
        points=ranked,
        peer_mean=mean(values.values()),
        peer_median=median(values.values()),
        peer_std=stdev(values.values()) if len(values) >= 2 else None,
    )

    for cik in values:
        z, n_ref = _leave_one_out_z(values, cik)
        if z is not None:
            ranking.z_scores[cik] = z
        ranking.z_reference_counts[cik] = n_ref

    return ranking


def compare_all_periods(
    points: Iterable[CompanyMetricPoint],
) -> list[PeerRanking]:
    """Group points by (metric, fiscal_year, fiscal_period, period_type) and
    build one PeerRanking per group. Groups with <2 companies are skipped."""
    groups: dict[tuple[str, int, str, str], list[CompanyMetricPoint]] = {}
    for p in points:
        key = (p.metric, p.fiscal_year, p.fiscal_period, p.period_type)
        groups.setdefault(key, []).append(p)

    rankings = [compare_metric(group) for group in groups.values()]
    # Sort: newest fiscal year first, then metric name (stable report order).
    return sorted(
        (r for r in rankings if r is not None),
        key=lambda r: (-r.fiscal_year, r.fiscal_period, r.metric),
    )


# Metrics shown in the M3 comparison report, in display order. Values are
# metric names as stored in FinancialMetric (single source of truth with
# the engine). Direction noted for honest display ("highest" vs "lowest").
REPORT_METRICS: tuple[str, ...] = (
    "revenue_growth_yoy",
    "operating_income_margin",
    "net_income_margin",
    "return_on_assets",
    "debt_to_assets",
    "free_cash_flow",
)
