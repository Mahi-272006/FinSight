"""Tests for deterministic evidence validation (M6's differentiator).

Pure-function matrix: every rule in evidence.py's module docstring has a
test, including the documented looseness (years, small integers) and the
masking rules (accessions/dates are not numbers).
"""

import json

import pytest

from app.graph import evidence


class TestInternalEvidenceStructures:
    def test_evidence_id_is_stable_for_identical_source_identity(self):
        identity = {
            "ticker": "NVDA",
            "accession": "0001045810-26-000075",
            "metric": "revenue",
            "fiscal_year": 2026,
            "fiscal_period": "Q2",
        }

        first = evidence.build_evidence_id("financial_fact", identity)
        second = evidence.build_evidence_id("financial_fact", dict(reversed(list(identity.items()))))

        assert first == second

    @pytest.mark.parametrize(
        "changed, value",
        [
            ("accession", "0001045810-26-000076"),
            ("fiscal_period", "Q3"),
        ],
    )
    def test_evidence_id_changes_for_different_accession_or_period(self, changed, value):
        identity = {
            "ticker": "NVDA",
            "accession": "0001045810-26-000075",
            "metric": "revenue",
            "fiscal_year": 2026,
            "fiscal_period": "Q2",
        }

        other_identity = {**identity, changed: value}

        assert evidence.build_evidence_id("financial_fact", identity) != (
            evidence.build_evidence_id("financial_fact", other_identity)
        )

    def test_financial_identity_uses_cik_not_ticker_and_includes_calculation_version(self):
        base = {
            "cik": 1045810, "ticker": "NVDA",
            "accession": "0001045810-26-000075", "metric": "revenue",
            "source_concept": "RevenueConcept", "fiscal_year": 2026,
            "fiscal_period": "Q2", "period_type": "quarterly", "unit": "USD",
        }
        assert evidence.build_financial_evidence_id("financial_fact", **base) == (
            evidence.build_financial_evidence_id(
                "financial_fact", **{**base, "ticker": "NEW"}
            )
        )
        assert evidence.build_financial_evidence_id(
            "calculated_metric", **{**base, "calculation_version": "v1"}
        ) != evidence.build_financial_evidence_id(
            "calculated_metric", **{**base, "calculation_version": "v2"}
        )
        with pytest.raises(ValueError, match="raw financial facts"):
            evidence.build_financial_evidence_id(
                "financial_fact", **{**base, "calculation_version": "v1"}
            )
        original = evidence.build_financial_evidence_id("financial_fact", **base)
        assert original != evidence.build_financial_evidence_id(
            "financial_fact", **{**base, "accession": "0001045810-26-000076"}
        )
        assert original != evidence.build_financial_evidence_id(
            "financial_fact", **{**base, "fiscal_period": "Q3"}
        )

    def test_filing_identity_includes_cik(self):
        assert evidence.build_filing_chunk_evidence_id(
            1045810, "0001045810-26-000075", "0001045810-26-000075:3"
        ) != evidence.build_filing_chunk_evidence_id(
            320193, "0001045810-26-000075", "0001045810-26-000075:3"
        )

    def test_decimal_value_is_preserved_and_structures_are_json_serializable(self):
        record: evidence.EvidenceRecord = {
            "evidence_id": evidence.build_financial_evidence_id(
                "calculated_metric",
                ticker="NVDA",
                cik=1045810,
                accession=None,
                source_accessions=["0001045810-26-000075"],
                metric="operating_margin",
                source_concept=None,
                fiscal_year=2026,
                fiscal_period="Q2",
                period_type="quarterly",
                unit="ratio",
            ),
            "source_kind": "calculated_metric",
            "ticker": "NVDA",
            "cik": 1045810,
            "accession": None,
            "form": None,
            "filed_at": None,
            "section": None,
            "chunk_id": None,
            "metric": "operating_margin",
            "source_concept": None,
            "fiscal_year": 2026,
            "fiscal_period": "Q2",
            "period_type": "quarterly",
            "unit": "ratio",
            "numeric_value": "0.56",
            "value_original": None,
            "is_restated": False,
            "source_accessions": ["0001045810-26-000075"],
            "formula": "operating_margin",
            "calculation_version": "operating_margin",
            "signal_type": None,
            "severity": None,
            "text": "Operating margin: 0.56 ratio",
        }
        claim: evidence.AnswerClaim = {
            "text": "Operating margin was 0.56 ratio.",
            "claim_type": "calculated_metric",
            "metric": "operating_margin",
            "source_concept": None,
            "fiscal_year": 2026,
            "fiscal_period": "Q2",
            "period_type": "quarterly",
            "unit": "ratio",
            "numeric_value": "0.56",
            "accession": None,
            "value_scope": "as_filed",
            "quote": None,
            "supporting_evidence_ids": [record["evidence_id"]],
        }
        observation = evidence.make_tool_observation(
            "get_financial_facts",
            {"ticker": "NVDA", "fiscal_year": 2026},
            "Operating margin: 0.56 ratio",
            evidence_ids=[record["evidence_id"]],
        )

        serialized = json.dumps({
            "record": record,
            "claim": claim,
            "observation": observation,
        })
        restored = json.loads(serialized)

        assert restored["record"]["numeric_value"] == "0.56"
        assert not isinstance(restored["record"]["numeric_value"], float)
        assert restored["claim"]["supporting_evidence_ids"] == [record["evidence_id"]]
        assert restored["observation"]["evidence_ids"] == [record["evidence_id"]]

    def test_operational_observation_has_no_evidence_ids_by_default(self):
        observation = evidence.make_tool_observation(
            "get_financial_facts",
            {"ticker": "NVDA"},
            "Tool failed while querying stored facts.",
            status="error",
        )

        assert observation["status"] == "error"
        assert observation["evidence_ids"] == []

        with pytest.raises(ValueError, match="cannot produce evidence IDs"):
            evidence.make_tool_observation(
                "get_financial_facts",
                {"ticker": "NVDA"},
                "Tool failed.",
                status="error",
                evidence_ids=["ev:financial_fact:invalid"],
            )
        with pytest.raises(ValueError, match="unsupported tool observation status"):
            evidence.make_tool_observation(
                "get_financial_facts", {}, "pending", status="pending"
            )


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


def _record(
    *, evidence_id="ev:financial_fact:test", source_kind="financial_fact",
    cik=1045810, accession="0001045810-26-000075", metric="revenue",
    concept="RevenueFromContractWithCustomerExcludingAssessedTax",
    fiscal_year=2026, fiscal_period="Q2", period_type="quarterly", unit="USD",
    value="1000", ticker="NVDA", text="Revenue was $1,000 USD.",
    filed_at="2026-08-26", is_restated=False, original=None,
    source_accessions=None, formula=None, signal_type=None, severity=None,
):
    return {
        "evidence_id": evidence_id, "source_kind": source_kind,
        "ticker": ticker, "cik": cik, "accession": accession,
        "form": "10-Q" if accession else None, "filed_at": filed_at,
        "section": "financial_statements" if source_kind == "filing_chunk" else None,
        "chunk_id": f"{accession}:1" if source_kind == "filing_chunk" else None,
        "metric": metric, "source_concept": concept,
        "fiscal_year": fiscal_year, "fiscal_period": fiscal_period,
        "period_type": period_type, "unit": unit,
        "numeric_value": value, "value_original": original,
        "is_restated": is_restated,
        "source_accessions": source_accessions or ([accession] if accession else []),
        "formula": formula, "calculation_version": formula,
        "signal_type": signal_type, "severity": severity, "text": text,
    }


def _claim(
    record, *, text="Revenue was $1,000 USD.", claim_type="verified_fact",
    metric="revenue", concept="RevenueFromContractWithCustomerExcludingAssessedTax",
    fiscal_year=2026, fiscal_period="Q2", period_type="quarterly", unit="USD",
    numeric_value="1000", accession="0001045810-26-000075",
    value_scope="as_filed", quote=None, evidence_ids=None,
):
    return {
        "text": text, "claim_type": claim_type, "metric": metric,
        "source_concept": concept, "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period, "period_type": period_type,
        "unit": unit, "numeric_value": numeric_value,
        "accession": accession, "value_scope": value_scope, "quote": quote,
        "supporting_evidence_ids": (
            evidence_ids if evidence_ids is not None else [record["evidence_id"]]
        ),
    }


class TestStructuredClaimValidation:
    def test_clean_numeric_claim_passes_and_citation_is_derived_from_record(self):
        record = _record()
        result = evidence.validate_answer([_claim(record)], [record], expected_cik=1045810)
        assert result.report.ok
        assert result.report.citations == ["0001045810-26-000075"]
        assert result.report.verified_numbers == ["1,000"]
        assert evidence.render_claims(result.accepted_claims, [record]) == (
            "revenue for FY2026 Q2 (quarterly) was 1,000 USD (as filed). "
            "[0001045810-26-000075]"
        )

    def test_missing_or_unknown_evidence_id_fails_closed(self):
        record = _record()
        claim = _claim(record, evidence_ids=["ev:missing"])
        result = evidence.validate_answer([claim], [record], expected_cik=1045810)
        assert not result.report.ok
        assert not result.accepted_claims

    def test_uncited_qualitative_fact_is_removed_even_alongside_supported_claim(self):
        record = _record()
        supported = _claim(record)
        unsupported = _claim(
            record,
            text="Management concealed material risks.",
            claim_type="filing_quote",
            numeric_value=None,
            accession=None,
            quote="Management concealed material risks.",
            evidence_ids=[],
        )
        result = evidence.validate_answer(
            [supported, unsupported], [record], expected_cik=1045810
        )
        assert result.accepted_claims == [supported]
        assert not result.report.ok
        assert "no supporting evidence IDs" in result.findings[0]["reason"]

    def test_claim_cannot_borrow_an_unrelated_accession(self):
        record = _record()
        claim = _claim(record, text="Revenue was $1,000 USD [0000000000-00-000001].")
        result = evidence.validate_answer([claim], [record], expected_cik=1045810)
        assert not result.report.ok
        assert result.report.unsupported_citations == ["0000000000-00-000001"]

    def test_wrong_company_fails_even_when_evidence_id_exists(self):
        record = _record(cik=320193, ticker="AAPL")
        result = evidence.validate_answer([_claim(record)], [record], expected_cik=1045810)
        assert not result.report.ok
        assert "wrong company" in result.findings[0]["reason"]

    def test_wrong_metric_concept_period_and_unit_fail(self):
        record = _record()
        cases = [
            _claim(record, metric="net_income"),
            _claim(record, concept="NetIncomeLoss"),
            _claim(record, fiscal_year=2025),
            _claim(record, fiscal_period="FY"),
            _claim(record, period_type="annual"),
            _claim(record, unit="shares"),
        ]
        for claim in cases:
            assert not evidence.validate_answer([claim], [record], expected_cik=1045810).report.ok

    def test_value_from_another_metric_is_rejected(self):
        revenue = _record(value="1000")
        net_income = _record(
            evidence_id="ev:financial_fact:net-income", metric="net_income",
            concept="NetIncomeLoss", value="250", text="Net income was $250 USD.",
        )
        claim = _claim(revenue, text="Revenue was $250 USD.", numeric_value="250")
        result = evidence.validate_answer([claim], [revenue, net_income], expected_cik=1045810)
        assert not result.report.ok

    def test_small_decimal_is_checked_with_decimal_arithmetic(self):
        record = _record(
            evidence_id="ev:calculated_metric:margin", source_kind="calculated_metric",
            metric="operating_margin", concept=None, unit="ratio", value="0.56",
            formula="operating_margin", text="Operating margin was 0.56 ratio.",
        )
        claim = _claim(
            record, text="Operating margin was 0.56 ratio.", claim_type="calculated_metric",
            metric="operating_margin", concept=None, unit="ratio", numeric_value="0.56",
        )
        result = evidence.validate_answer([claim], [record], expected_cik=1045810)
        assert result.report.ok
        assert result.report.verified_numbers == ["0.56"]
        wrong = {**claim, "text": "Operating margin was 0.57 ratio.", "numeric_value": "0.57"}
        assert not evidence.validate_answer([wrong], [record], expected_cik=1045810).report.ok

    def test_rounded_calculated_metric_report_matches_rendered_source_value(self):
        record = _record(
            evidence_id="ev:calculated_metric:rounded-margin",
            source_kind="calculated_metric", metric="operating_margin",
            concept=None, unit="ratio", value="0.563",
            formula="operating_margin", text="Operating margin was 0.56 ratio.",
        )
        claim = _claim(
            record, text="Operating margin was 0.56 ratio.",
            claim_type="calculated_metric", metric="operating_margin",
            concept=None, unit="ratio", numeric_value="0.56",
        )

        result = evidence.validate_answer([claim], [record], expected_cik=1045810)
        final_answer = evidence.render_claims(result.accepted_claims, [record])

        assert result.report.ok
        assert "was 0.563 ratio" in final_answer
        assert result.report.verified_numbers == ["0.563"]

    def test_rounded_financial_fact_report_matches_rendered_source_value(self):
        record = _record(value="1000.4", text="Revenue was $1,000 USD.")
        claim = _claim(record, numeric_value="1000", text="Revenue was $1,000 USD.")

        result = evidence.validate_answer([claim], [record], expected_cik=1045810)
        final_answer = evidence.render_claims(result.accepted_claims, [record])

        assert result.report.ok
        assert "was 1,000.4 USD" in final_answer
        assert result.report.verified_numbers == ["1,000.4"]

    def test_mixed_supported_and_unsupported_claims_keep_only_supported(self):
        record = _record()
        good = _claim(record)
        bad = _claim(record, text="Management concealed material risks.",
                     claim_type="interpretation", numeric_value=None)
        result = evidence.validate_answer([good, bad], [record], expected_cik=1045810)
        assert result.accepted_claims == [good]
        assert not result.report.ok
        assert "qualitative interpretation" in result.findings[0]["reason"]
        assert evidence.render_claims(result.accepted_claims, [record])

    def test_retrieved_accession_does_not_support_unrelated_qualitative_claim(self):
        record = _record(
            evidence_id="ev:filing_chunk:1", source_kind="filing_chunk",
            metric=None, concept=None, value=None,
            text="Revenue increased during the period.",
        )
        claim = _claim(
            record, text="NVDA faces major litigation.", claim_type="filing_quote",
            metric=None, concept=None, numeric_value=None,
            quote="NVDA faces major litigation.",
        )
        result = evidence.validate_answer([claim], [record], expected_cik=1045810)
        assert not result.report.ok
        assert "exact excerpt" in result.findings[0]["reason"]

    def test_exact_qualitative_quote_is_supported_by_linked_filing_record(self):
        record = _record(
            evidence_id="ev:filing_chunk:1", source_kind="filing_chunk",
            metric=None, concept=None, value=None,
            text="Export controls constrain sales in certain markets.",
        )
        claim = _claim(
            record, text="Export controls constrain sales in certain markets.",
            claim_type="filing_quote", metric=None, concept=None,
            numeric_value=None, quote="Export controls constrain sales in certain markets.",
        )
        result = evidence.validate_answer([claim], [record], expected_cik=1045810)
        assert result.report.ok
        assert evidence.render_claims(result.accepted_claims, [record]) == (
            "“Export controls constrain sales in certain markets.” "
            "[0001045810-26-000075]"
        )

    def test_numeric_claim_cannot_use_filing_quote_to_bypass_fact_validation(self):
        record = _record(
            evidence_id="ev:filing_chunk:numeric", source_kind="filing_chunk",
            metric=None, concept=None, value=None,
            text="Revenue was $1,000 USD.",
        )
        claim = _claim(
            record, text="Revenue was $1,000 USD.", claim_type="filing_quote",
            metric=None, concept=None, numeric_value=None,
            quote="Revenue was $1,000 USD.",
        )
        result = evidence.validate_answer([claim], [record], expected_cik=1045810)
        assert not result.report.ok
        assert "numeric excerpts" in result.findings[0]["reason"]

    def test_latest_claim_and_conflicting_unattributed_value_fail_but_as_filed_passes(self):
        original = _record(
            evidence_id="ev:fact:original", accession="0001045810-25-000010",
            value="10000", fiscal_year=2025, fiscal_period="FY",
            filed_at="2025-02-01", text="Revenue was $10,000 USD.",
        )
        restated = _record(
            evidence_id="ev:fact:restated", accession="0001045810-26-000020",
            value="12000", fiscal_year=2025, fiscal_period="FY",
            filed_at="2026-02-01", is_restated=True, original="10000",
            text="Revenue was restated to $12,000 USD.",
        )
        latest = _claim(
            original, text="Latest revenue was $10,000 USD.", numeric_value="10000",
            fiscal_year=2025, fiscal_period="FY", accession=original["accession"],
            value_scope="latest",
        )
        rejected = evidence.validate_answer([latest], [original, restated], expected_cik=1045810)
        assert not rejected.report.ok
        assert rejected.conflicts
        historical = {**latest, "text": "Revenue as filed was $10,000 USD.", "value_scope": "as_filed"}
        accepted = evidence.validate_answer([historical], [original, restated], expected_cik=1045810)
        assert accepted.accepted_claims == [historical]
        assert accepted.conflicts

    def test_inconsistent_duplicate_evidence_id_is_rejected(self):
        first = _record()
        second = {**first, "numeric_value": "2000"}
        result = evidence.validate_answer([_claim(first)], [first, second], expected_cik=1045810)
        assert not result.report.ok
        assert "inconsistent records" in result.findings[0]["reason"]

    def test_parser_rejects_malformed_json_and_requires_explicit_ids(self):
        claims, findings = evidence.parse_answer_claims("not json")
        assert claims == []
        assert findings and "valid JSON" in findings[0]["reason"]
        claims, findings = evidence.parse_answer_claims('{"answer":"free prose"}')
        assert claims == []
        assert findings and "claims list" in findings[0]["reason"]

    def test_parser_preserves_valid_claim_and_reports_each_discarded_entry(self):
        record = _record()
        valid = _claim(record)
        malformed_text = "Unsupported claim with malformed evidence IDs."
        unknown_type_text = "Unsupported claim with unknown type."
        bad_scope_text = "Unsupported claim with invalid scope."
        invalid_type_text = "Unsupported claim with non-string type."
        invalid_scope_text = "Unsupported claim with non-string scope."
        payload = {
            "claims": [
                valid,
                {"text": malformed_text, "claim_type": "verified_fact",
                 "supporting_evidence_ids": "not-a-list"},
                {**_claim(record), "text": unknown_type_text, "claim_type": "forecast"},
                {**_claim(record), "text": bad_scope_text, "value_scope": "latest-ish"},
                {**_claim(record), "text": invalid_type_text, "claim_type": []},
                {**_claim(record), "text": invalid_scope_text, "value_scope": {}},
            ]
        }

        claims, findings = evidence.parse_answer_claims(json.dumps(payload))

        assert claims == [valid]
        assert len(findings) == 5
        assert [finding["claim"] for finding in findings] == [
            malformed_text, unknown_type_text, bad_scope_text,
            invalid_type_text, invalid_scope_text,
        ]
        assert "malformed" in findings[0]["reason"]
        assert "unknown" in findings[1]["reason"]
        assert "value_scope" in findings[2]["reason"]

    def test_report_matches_retained_claims_and_excludes_removed_claim_citations(self):
        record = _record()
        good = _claim(record)
        bad = _claim(record, text="Revenue was $9,999 USD [0000000000-00-000001].",
                     numeric_value="9999")
        result = evidence.validate_answer([good, bad], [record], expected_cik=1045810)
        final_claims = result.accepted_claims
        final = evidence.validate_answer(final_claims, [record], expected_cik=1045810)
        assert final.report.to_dict() == evidence.validate_answer(
            [good], [record], expected_cik=1045810
        ).report.to_dict()
        assert "0000000000-00-000001" not in final.report.citations


class TestDecimalValidation:
    @pytest.mark.parametrize("value", ["0.56", "0", "-0.56", "123.4500"])
    def test_valid_decimal_strings_are_preserved(self, value):
        assert evidence.validate_decimal_string(value) == value

    @pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999999", "1.2.3", "", " 1"])
    def test_invalid_decimal_strings_are_rejected(self, value):
        with pytest.raises(ValueError):
            evidence.validate_decimal_string(value)

    def test_overflowing_or_excess_precision_decimal_strings_are_rejected(self):
        with pytest.raises(ValueError):
            evidence.validate_decimal_string("9" * 300)
        with pytest.raises(ValueError):
            evidence.validate_decimal_string("1e999999")


class TestStructuredFinalization:
    def test_all_unsupported_claims_leave_no_rendered_answer(self):
        record = _record()
        claim = _claim(record, claim_type="interpretation", text="Outlook is strong.")
        result = evidence.validate_answer([claim], [record], expected_cik=1045810)
        assert evidence.render_claims(result.accepted_claims, [record]) == ""
        assert result.findings
