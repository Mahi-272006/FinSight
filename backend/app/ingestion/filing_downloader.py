"""Filing discovery: parse SEC submissions JSON into typed filing references.

The submissions API (data.sec.gov/submissions/CIK##########.json) is the
official, structured entry point for downloading actual filing documents --
no HTML scraping of EDGAR browse pages needed. Its `filings.recent` section
holds PARALLEL ARRAYS (accessionNumber[i] pairs with filingDate[i], form[i],
primaryDocument[i] ...), which is compact but easy to zip incorrectly: our
parser keys off accessionNumber as the anchor and tolerates missing
secondary fields rather than trusting array lengths to match.

Design: pure functions over the parsed payload. Downloading (I/O) happens in
rag_pipeline via SECClient.get_bytes -- here we only decide WHAT to download
and WHERE it lives, which is exactly the logic that deserves unit tests.

Scope note: `filings.recent` covers roughly the most recent 1000 filings.
Older filings live in the extra files listed under `filings.files`; M4 does
not paginate into them (20 years of 10-K/10-Q is already ~100 filings per
company -- far more than the demo needs). Documented limitation.
"""

from dataclasses import dataclass
from typing import Any, Iterable

from app.ingestion.sec_client import SECClientError

# Document archive URL. The accession number appears in URLs WITHOUT dashes:
# 0000950170-25-100235 -> 000095017025100235. CIK is not zero-padded here
# (EDGAR archive paths use the bare integer).
ARCHIVES_URL = "{files_url}/Archives/edgar/data/{cik}/{accession_nodash}/{document}"

# Forms eligible for text extraction. Amendments ("10-K/A") are deliberately
# EXCLUDED in M4: they restate parts of the original document and would
# duplicate sections in retrieval. The original filing is the canonical text.
DEFAULT_FORMS: tuple[str, ...] = ("10-K", "10-Q")


@dataclass(frozen=True)
class FilingRef:
    """Identity of one filing document, enough to download and cite it."""

    cik: int
    accession: str          # e.g. "0000950170-25-100235" (dashed form, canonical)
    form: str               # "10-K" / "10-Q"
    filing_date: str        # ISO "YYYY-MM-DD" (when the filing was submitted)
    primary_document: str   # filename inside the accession folder, e.g. "msft-20250630.htm"
    report_date: str | None = None  # period end covered, when provided

    @property
    def accession_nodash(self) -> str:
        return self.accession.replace("-", "")

    def document_url(self, files_url: str) -> str:
        return ARCHIVES_URL.format(
            files_url=files_url,
            cik=self.cik,
            accession_nodash=self.accession_nodash,
            document=self.primary_document,
        )


def parse_recent_filings(submissions: dict[str, Any], cik: int) -> list[FilingRef]:
    """Convert `filings.recent` parallel arrays into FilingRefs.

    Keys off accessionNumber; skips entries whose primaryDocument is missing
    (an accession without a primary document -- e.g. paper filings in the
    distant past -- cannot be downloaded as HTML). Sorts newest-first by
    filing date, with accession as a deterministic tiebreaker so re-runs
    always process filings in the same order.
    """
    try:
        recent = submissions["filings"]["recent"]
        accessions = recent["accessionNumber"]
    except KeyError as exc:
        raise SECClientError(
            f"Submissions payload for CIK {cik} missing expected key: {exc}"
        ) from exc

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    docs = recent.get("primaryDocument", [])
    reports = recent.get("reportDate", [])

    refs: list[FilingRef] = []
    for i, accession in enumerate(accessions):
        document = docs[i] if i < len(docs) else ""
        if not document:
            continue
        refs.append(FilingRef(
            cik=cik,
            accession=accession,
            form=forms[i] if i < len(forms) else "",
            filing_date=dates[i] if i < len(dates) else "",
            primary_document=document,
            report_date=reports[i] if i < len(reports) and reports[i] else None,
        ))

    refs.sort(key=lambda r: (r.filing_date, r.accession), reverse=True)
    return refs


def select_filings(
    refs: Iterable[FilingRef],
    forms: tuple[str, ...] = DEFAULT_FORMS,
    limit: int | None = None,
) -> list[FilingRef]:
    """Filter to the requested forms, newest first, optionally capped.

    `limit=None` means "all". The limit applies AFTER form filtering so
    `--limit 3` means "3 10-K/10-Q filings", not "3 filings of any kind".
    """
    selected = [r for r in refs if r.form in forms]
    if limit is not None:
        if limit <= 0:
            raise SECClientError(f"Invalid filing limit: {limit}")
        selected = selected[:limit]
    return selected
