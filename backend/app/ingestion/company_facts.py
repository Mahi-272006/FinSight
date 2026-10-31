"""Company Facts retrieval + inspection.

Why a separate module from sec_client.py:
- sec_client.py = HTTP mechanics (rate limits, retries, headers). It knows
  nothing about what a "company fact" is.
- company_facts.py = domain logic: what the payload contains, how to inspect
  it, where raw copies live.

Keeping these apart means the HTTP layer can be reused unchanged for filing
downloads (M4), and the inspection logic can be tested without any network.

The `summarize()` function exists because SEC's Company Facts payload is HUGE
(Apple's is ~15 MB, thousands of XBRL facts). Before writing M2's normalizer
we must understand the actual shape: which concepts exist, which forms and
periods are covered, and where the gaps are. Inspection first, normalization
second -- building a normalizer against imagined data is how silent bugs are
born.
"""

from collections import Counter
from typing import Any

from app.ingestion.sec_client import SECClient
from app.utils.logging import get_logger

logger = get_logger(__name__)


def fetch_and_save(client: SECClient, query: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve a company and fetch + persist its full Company Facts payload.

    Returns (identity, facts_payload).
    """
    identity = client.resolve_company(query)
    logger.info(
        "Resolved %r -> %s (CIK %010d)", query, identity["ticker"], identity["cik"]
    )
    payload = client.get_company_facts(identity["cik"])
    client.save_raw(payload, identity["cik"])
    return identity, payload


def summarize(payload: dict[str, Any]) -> dict[str, Any]:
    """Build an inspection summary of a Company Facts payload.

    Returns a plain dict (easy to print or assert on in tests):
        {
          "entity_name": str,
          "cik": int,
          "taxonomies": ["us-gaap", ...],
          "concept_count": int,
          "concepts_by_taxonomy": {"us-gaap": 1234, "dei": 12},
          "forms": {"10-K": 12, "10-Q": 48},
          "fiscal_years": [2016, 2017, ...],      # years with annual (10-K) facts
          "sample_concepts": [...],               # first few concept names
          "key_concepts_present": {               # does it have what M2 needs?
              "Revenues": False,
              "RevenueFromContractWithCustomerExcludingAssessedTax": True,
              "NetIncomeLoss": True,
              ...
          },
        }
    """
    entity = payload.get("entityName", "UNKNOWN")
    cik = payload.get("cik", "UNKNOWN")
    facts = payload.get("facts", {})

    concept_count = 0
    concepts_by_taxonomy: Counter[str] = Counter()
    forms: Counter[str] = Counter()
    all_concepts: list[str] = []
    annual_years: set[int] = set()

    for taxonomy, concepts in facts.items():
        concepts_by_taxonomy[taxonomy] = len(concepts)
        for concept_name, concept_data in concepts.items():
            concept_count += 1
            all_concepts.append(concept_name)
            for unit_facts in concept_data.get("units", {}).values():
                for fact in unit_facts:
                    form = fact.get("form")
                    if form:
                        forms[form] += 1
                    # Annual facts come from 10-K filings covering ~1 year.
                    # We track the END year of such periods: that is the
                    # fiscal year the fact belongs to.
                    if form == "10-K" and _is_annual_period(fact):
                        end = fact.get("end", "")
                        if len(end) >= 4 and end[:4].isdigit():
                            annual_years.add(int(end[:4]))

    # Concepts M2's normalizer will look for. Note: different companies use
    # different tags for the same idea -- that is exactly why the normalizer
    # needs a per-concept fallback chain (see key_concepts_present).
    key_concepts = [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "OperatingIncomeLoss",
        "NetIncomeLoss",
        "Assets",
        "Liabilities",
        "NetCashProvidedByUsedInOperatingActivities",
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "CashAndCashEquivalentsAtCarryingValue",
        "GrossProfit",
    ]

    return {
        "entity_name": entity,
        "cik": cik,
        "taxonomies": sorted(facts.keys()),
        "concept_count": concept_count,
        "concepts_by_taxonomy": dict(concepts_by_taxonomy),
        "forms": dict(forms.most_common()),
        "fiscal_years": sorted(annual_years),
        "sample_concepts": sorted(all_concepts)[:10],
        "key_concepts_present": {c: c in all_concepts for c in key_concepts},
    }


def _is_annual_period(fact: dict[str, Any]) -> bool:
    """Heuristic: does this fact cover roughly a full year?

    XBRL facts carry 'start' and 'end' ISO dates. Annual facts (from 10-K)
    span ~365 days. Quarterly YTD facts from 10-Qs span months and would be
    wrongly counted as annual if we only looked at the form field, so we
    check the actual duration. Using date arithmetic instead of trusting
    labels is the same principle M2 applies when splitting YTD into quarters.
    """
    from datetime import date

    start, end = fact.get("start"), fact.get("end")
    if not start or not end:
        return False
    try:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
    except ValueError:
        return False
    return 300 <= (e - s).days <= 400
