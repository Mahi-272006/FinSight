"""Ingest a company's SEC financial facts (raw layer).

Usage:
    python scripts/ingest_company.py MSFT
    python scripts/ingest_company.py "Microsoft"
    python scripts/ingest_company.py MSFT AAPL NVDA     # several at once

What it does:
1. Loads settings from backend/.env (fails fast if SEC_USER_AGENT is bad).
2. Resolves the ticker/name to a SEC CIK via the official ticker file.
3. Downloads the full Company Facts payload (throttled, with retries).
4. Saves it byte-faithfully to data/raw/companyfacts/CIK##########.json.
5. Prints an inspection summary: concept count, forms, fiscal years,
   and which key XBRL concepts are available (this guides M2's normalizer).

This is the raw layer only: no cleaning, no normalization, no database.
Raw first, clean later -- the raw file is the audit trail everything
downstream can be verified against.
"""

import sys
from pathlib import Path

# Make the backend package importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.ingestion.company_facts import fetch_and_save, summarize
from app.ingestion.sec_client import SECClient, SECClientError
from app.utils.logging import configure_logging, get_logger


def main(queries: list[str]) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, str(settings.logs_dir))
    settings.ensure_directories()
    logger = get_logger("ingest")

    if not queries:
        print(__doc__)
        return 2

    failures = 0
    with SECClient(settings) as client:
        for query in queries:
            try:
                identity, payload = fetch_and_save(client, query)
            except SECClientError as exc:
                # Clean, actionable error -- no stack trace dump for the user.
                logger.error("Failed to ingest %r: %s", query, exc)
                failures += 1
                continue

            report = summarize(payload)
            _print_summary(identity, report)
    return 1 if failures else 0


def _print_summary(identity: dict, report: dict) -> None:
    print()
    print("=" * 64)
    print(f"Company : {report['entity_name']}")
    print(f"CIK     : {report['cik']:010d}   Ticker: {identity['ticker']}")
    print(f"Concepts: {report['concept_count']} across taxonomies {report['taxonomies']}")
    print(f"Forms   : {report['forms']}")
    print(f"Fiscal years with annual facts: {report['fiscal_years'][0] if report['fiscal_years'] else '?'}"
          f" - {report['fiscal_years'][-1] if report['fiscal_years'] else '?'}"
          f"  ({len(report['fiscal_years'])} years)")
    print("Key concepts available for M2 normalization:")
    for concept, present in report["key_concepts_present"].items():
        mark = "yes" if present else "-- "
        print(f"  [{mark}] {concept}")
    print("=" * 64)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
