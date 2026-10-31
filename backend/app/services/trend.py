"""Trend analysis: slope and streak detection over metric time series.

Pure, dependency-free math (hand-rolled least squares -- a 30-year-old
formula that needs no library and is trivially golden-testable). Used by
anomaly rules (e.g. "operating margin down 3 years in a row") and by the
report (e.g. "+1.2pp/yr margin trend").

Design: sequences are plain lists of floats aligned to consecutive fiscal
years (caller's responsibility). None values (missing periods) break the
series -- the caller passes only contiguous runs. That keeps THIS module
free of period bookkeeping and keeps the period logic in one place.
"""

from typing import Literal, Sequence


def linear_slope(values: Sequence[float]) -> float | None:
    """Least-squares slope over equally spaced points (x = 0..n-1).

    slope = cov(x, y) / var(x). Returns None when n < 2 (a trend needs at
    least two points) or when all y are identical (var(x) != 0 always here,
    but a flat series has slope exactly 0 -- returned as 0.0, not None).

    Interpretation: units of `values` per step. For margins given as
    decimals, slope is decimal-change per year (0.012 = +1.2pp/yr).
    """
    n = len(values)
    if n < 2:
        return None
    x_mean = (n - 1) / 2
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den


def trailing_streak(values: Sequence[float], direction: Literal["up", "down"]) -> int:
    """Length of the trailing consecutive increase/decrease streak.

    Counts how many consecutive *changes* at the END of the series all move
    in `direction`. E.g. [30, 28, 26, 24] -> down-streak 3 (three drops:
    30->28, 28->26, 26->24). [30, 28, 29, 27] -> down-streak 1 (29->27 is the
    only trailing drop; 28->29 was a rise, breaking the run).

    Returns 0 when the series has <2 points or the last change moves the
    other way. A streak of k means k+1 consecutive periods moved together --
    the margin rule uses this to demand >= 3 DECLINES (4 declining years).
    """
    if len(values) < 2:
        return 0
    streak = 0
    for prev, curr in zip(reversed(values[:-1]), reversed(values[1:])):
        # Walk backwards from the end; each pair (prev, curr) is one change
        # with prev the EARLIER element. (Bug fixed here: the arguments were
        # originally swapped, pairing (later, earlier) so every direction
        # inverted -- a rising series read as a 'down' streak. Golden tests
        # caught it; note how [10,20,30] returned down=2.)
        if direction == "up" and curr > prev:
            streak += 1
        elif direction == "down" and curr < prev:
            streak += 1
        else:
            break
    return streak
