"""Typed domain definitions: canonical metrics, periods, company identity.

Why a single source of truth for metric names:
M2's normalizer, M3's anomaly detector, M6's agent tools, and M8's evaluation
dataset must all agree on what "revenue" is called. A string typo like
"operating_income" vs "OperatingIncome" would silently produce empty queries.
Enum-like constants give one definition, imported everywhere.
"""

from enum import Enum


class PeriodType(str, Enum):
    """How a fact's time span should be interpreted.

    INSTANT: point-in-time only (balance-sheet items: Assets, Cash) -- no
             start date, just an 'end' date.
    ANNUAL: ~full year (~350-380 days).
    QUARTERLY: a standalone 3-month quarter (~80-100 days), either as filed
             in a 10-Q or derived by subtracting YTD figures.
    YTD: year-to-date cumulative (6M, 9M) -- an *input* to quarter-splitting,
             not a final output.
    """

    INSTANT = "instant"
    ANNUAL = "annual"
    QUARTERLY = "quarterly"
    YTD = "ytd"


class FiscalPeriod(str, Enum):
    """Which fiscal slot a fact belongs to within its fiscal year."""

    FY = "FY"
    Q1 = "Q1"
    Q2 = "Q2"
    Q3 = "Q3"
    Q4 = "Q4"


class Metric(str, Enum):
    """Canonical financial metrics tracked by FinSight.

    Each maps to one or more XBRL tags via CONCEPT_MAP in data_cleaner.py.
    Values are the snake_case names used in the database, API, and UI.
    """

    # --- Income statement ---
    REVENUE = "revenue"
    GROSS_PROFIT = "gross_profit"
    OPERATING_INCOME = "operating_income"
    NET_INCOME = "net_income"

    # --- Balance sheet ---
    TOTAL_ASSETS = "total_assets"
    TOTAL_LIABILITIES = "total_liabilities"
    CURRENT_ASSETS = "current_assets"
    CURRENT_LIABILITIES = "current_liabilities"
    CASH = "cash_and_equivalents"
    TOTAL_DEBT = "total_debt"
    ACCOUNTS_RECEIVABLE = "accounts_receivable"

    # --- Cash flow ---
    OPERATING_CASH_FLOW = "operating_cash_flow"
    CAPITAL_EXPENDITURE = "capital_expenditure"  # stored as positive outflow

    # --- Share data ---
    SHARES_OUTSTANDING = "shares_outstanding"


class FactUnit(str, Enum):
    """Units we accept; anything else is rejected loudly (never guessed)."""

    USD = "USD"
    SHARES = "shares"


# Metrics that represent a balance at a point in time (INSTANT facts only).
INSTANT_METRICS = frozenset(
    {
        Metric.TOTAL_ASSETS,
        Metric.TOTAL_LIABILITIES,
        Metric.CURRENT_ASSETS,
        Metric.CURRENT_LIABILITIES,
        Metric.CASH,
        Metric.TOTAL_DEBT,
        Metric.ACCOUNTS_RECEIVABLE,
        Metric.SHARES_OUTSTANDING,
    }
)

# Metrics measured over a duration (annual/quarterly/YTD facts).
DURATION_METRICS = frozenset(
    {
        Metric.REVENUE,
        Metric.GROSS_PROFIT,
        Metric.OPERATING_INCOME,
        Metric.NET_INCOME,
        Metric.OPERATING_CASH_FLOW,
        Metric.CAPITAL_EXPENDITURE,
    }
)
