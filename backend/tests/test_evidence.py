"""Tests for deterministic evidence validation (M6's differentiator).

Pure-function matrix: every rule in evidence.py's module docstring has a
test, including the documented looseness (years, small integers) and the
masking rules (accessions/dates are not numbers).
"""

import pytest

from app.graph import evidence


class TestExtractors:
    def test_accessions_and_chunk_ids(self):
        text = "see 0001045810-26-000075 and chunk 0001045810-26-000075:12 in the 10-Q"
        assert evidence.extract_accessions(text) == {"0001045810-26-000075"}
        assert evidence.extract_chunk_ids(text) == {"0001045810-26-000075:12"}

    def test_financial_number_forms(self):
        assert evidence.extract_financial_numbers("$96.2B") == [96_200_000_000.0]
        assert evidence.extract_financial_numbers("15%") == [15.0]
        assert evidence.extract_financial_numbers("96,221") == [96_221.0]
        assert evidence.extract_financial_numbers("$1,500M") == [1_500_000_000.0]

    def test_years_and_small_integers_exempt(self):
        # Not financial claims: years, counts, item numbers.
        assert evidence.extract_financial_numbers("fiscal 2026 Item 1A covers three risks") == []

    def test_accession_and_date_masked(self):
        # 0001045810-26-000075 and 2026-08-26 must contribute NO numbers.
        assert evidence.extract_financial_numbers(
            "filed 2026-08-26 as 0001045810-26-000075"
        ) == []


class TestValidateAnswer:
    OBS = [
        "chunk_id: 0001045810-26-000075:3 | NVDA 10-Q 0001045810-26-000075 "
        "section:financial_statements\nRevenue was $46,743,000,000 USD.",
    ]

    def test_clean_answer_passes(self):
        report = evidence.validate_answer(
            "NVDA reported revenue of $46,743,000,000 USD [0001045810-26-000075].",
            self.OBS,
        )
        assert report.ok
        assert report.citations == ["0001045810-26-000075"]
        assert report.unsupported_citations == []
        assert report.unverified_numbers == []

    def test_unsupported_citation_fails(self):
        report = evidence.validate_answer(
            "Risks are severe [0000000000-00-000001].", self.OBS
        )
        assert not report.ok
        assert report.unsupported_citations == ["0000000000-00-000001"]

    def test_unverified_number_fails(self):
        report = evidence.validate_answer(
            "Revenue of $999,999,999,999 USD [0001045810-26-000075].", self.OBS
        )
        assert not report.ok
        assert report.unverified_numbers == ["999,999,999,999"]

    def test_rounding_tolerance_verifies_same_figure(self):
        # "$46.7B" is the same figure as 46,743,000,000 to within 1%.
        report = evidence.validate_answer(
            "Revenue was about $46.7B [0001045810-26-000075].", self.OBS
        )
        assert report.ok, report.summary()

    def test_no_citations_fails_with_note(self):
        report = evidence.validate_answer("Revenue was large.", self.OBS)
        assert not report.ok
        assert any("no citations" in n for n in report.notes)

    def test_empty_answer_fails(self):
        report = evidence.validate_answer("", self.OBS)
        assert not report.ok
        assert any("empty draft" in n for n in report.notes)

    def test_no_observations_nothing_passes(self):
        # Fabrication guard: citations and numbers with zero retrieved
        # evidence are all unsupported.
        report = evidence.validate_answer(
            "Revenue $500,000,000 [0001045810-26-000075].", []
        )
        assert not report.ok
        assert report.unsupported_citations == ["0001045810-26-000075"]
        assert report.unverified_numbers


class TestStripUnsupported:
    OBS = [
        "chunk_id: 0001045810-26-000075:3\nRevenue was $46,743,000,000 USD."
    ]

    def test_bad_sentence_removed_good_kept(self):
        answer = (
            "NVDA reported revenue of $46,743,000,000 USD [0001045810-26-000075]. "
            "Its competitor ACME earned $7B [0000000000-00-000001]. "
            "The outlook is strong."
        )
        kept, limitations = evidence.strip_unsupported(answer, self.OBS)
        assert "$46,743,000,000" in kept
        assert "The outlook is strong." in kept
        assert "ACME" not in kept
        assert len(limitations) == 1
        assert "0000000000-00-000001" in limitations[0]

    def test_bad_number_sentence_removed(self):
        answer = "Margins hit 99% [0001045810-26-000075]."
        kept, limitations = evidence.strip_unsupported(answer, self.OBS)
        assert kept == ""
        assert "99" in limitations[0]

    def test_all_clean_keeps_everything(self):
        answer = "Revenue was $46,743,000,000 USD [0001045810-26-000075]."
        kept, limitations = evidence.strip_unsupported(answer, self.OBS)
        assert kept == answer
        assert limitations == []
