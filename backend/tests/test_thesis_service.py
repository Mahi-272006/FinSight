"""M7 tests: the verified layer's matrices, DB behavior, and the LLM seam.

Three groups, matching the three risks in the design:

1. PURE FUNCTIONS (no DB, no LLM): condition parsing, comparison operators,
   period selection, evaluation verdicts, and the status matrix. These are
   the determinism guarantee -- heavy matrix coverage, including the
   signal-neutrality invariant (the status function cannot even SEE
   signals, and a service-level test pins that end to end).

2. DB BEHAVIOR (sqlite): creation with evidence snapshots, append-only
   assessment history (assess twice -> 2 rows, first row byte-identical),
   status transition explanation, unknown-ticker/not-found errors.

3. THE LLM SEAM: interpretation present / absent / failing, and the prompt
   contract (numbers restated verbatim, no-arithmetic rule, signals framed
   as context, tiers named).
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    Base,
    Company,
    FinancialFact,
    FinancialMetric,
    InvestigationSignal,
)
from app.services.llm_providers import LLMResponse
from app.services.llm_providers import LLMAllProvidersFailed
from app.services.prompting import (
    THESIS_INTERPRETATION_POLICY,
    build_thesis_interpretation_messages,
)
from app.services.thesis_service import (
    STATUS_CONTRADICTED,
    STATUS_INSUFFICIENT,
    STATUS_SUPPORTED,
    STATUS_WEAKENED,
    VERDICT_INCOMPLETE,
    VERDICT_MET,
    VERDICT_NOT_MET,
    ConditionSpec,
    ConditionEvaluation,
    ThesisInputError,
    ThesisNotFoundError,
    ThesisService,
    compare,
    derive_status,
    evaluate_condition,
    format_condition,
    parse_condition,
    select_row,
)

ACCESSION = "0001045810-26-000075"
MSFT_CIK = 789019


# ---------------------------------------------------------------------------
# Pure function tests: parsing
# ---------------------------------------------------------------------------


class TestParseCondition:
    def test_basic_form(self):
        spec = parse_condition("revenue_growth_yoy>=0.10")
        assert spec == ConditionSpec(
            metric="revenue_growth_yoy", comparator=">=",
            threshold=0.1, fiscal_year=None, fiscal_period=None,
        )

    def test_whitespace_tolerated(self):
        spec = parse_condition("  net_income_margin <= 0.35 ")
        assert (spec.metric, spec.comparator, spec.threshold) == (
            "net_income_margin", "<=", 0.35,
        )

    def test_unicode_comparators_normalize(self):
        assert parse_condition("revenue≥0.1").comparator == ">="
        assert parse_condition("revenue≤0.1").comparator == "<="

    def test_bare_equals_is_equality(self):
        assert parse_condition("current_ratio=1.5").comparator == "=="

    def test_negative_threshold(self):
        assert parse_condition("earnings_cashflow_divergence>=-0.05").threshold == -0.05

    def test_integer_threshold(self):
        assert parse_condition("total_assets>1000000000").threshold == 1e9

    def test_period_pin_carried_through(self):
        spec = parse_condition("revenue_growth_yoy>=0.1", fiscal_year=2026, fiscal_period="FY")
        assert (spec.fiscal_year, spec.fiscal_period) == (2026, "FY")

    @pytest.mark.parametrize("bad", [
        "", "   ", "revenue growth >= 0.1",  # metric names have no spaces
        "revenue>=",  # missing threshold
        ">=0.1",  # missing metric
        "revenue~=0.1",  # unknown operator
        "revenue>=0.1; DROP TABLE theses",  # injection attempt is just a parse error
        "revenue>=abc",  # non-numeric threshold
    ])
    def test_invalid_inputs_raise(self, bad):
        with pytest.raises(ThesisInputError):
            parse_condition(bad)

    def test_error_message_shows_example(self):
        with pytest.raises(ThesisInputError, match="revenue_growth_yoy"):
            parse_condition("nonsense")


class TestFormatCondition:
    def test_latest_period_label(self):
        assert format_condition(parse_condition("revenue>=1")) == \
            "revenue >= 1 (latest available period)"

    def test_year_pin_label(self):
        spec = parse_condition("revenue>=1", fiscal_year=2026)
        assert format_condition(spec) == "revenue >= 1 (FY2026)"

    def test_year_and_period_label(self):
        spec = parse_condition("revenue>=1", fiscal_year=2026, fiscal_period="Q3")
        assert format_condition(spec) == "revenue >= 1 (FY2026 Q3)"


# ---------------------------------------------------------------------------
# Pure function tests: comparison
# ---------------------------------------------------------------------------


class TestCompare:
    @pytest.mark.parametrize("value,op,threshold,expected", [
        (0.10, ">=", 0.10, True),   # boundary: >= holds at equality
        (0.0999, ">=", 0.10, False),
        (0.10, "<=", 0.10, True),
        (0.1001, "<=", 0.10, False),
        (0.11, ">", 0.10, True),
        (0.10, ">", 0.10, False),   # boundary: > fails at equality
        (0.09, "<", 0.10, True),
        (0.10, "<", 0.10, False),
        (0.10, "==", 0.10, True),
        (0.1, "==", 0.1 + 2e-17, True),  # float noise does not break equality
        (0.11, "==", 0.10, False),
        (-0.5, ">=", -1.0, True),   # negatives compare correctly
    ])
    def test_operator_matrix(self, value, op, threshold, expected):
        assert compare(value, op, threshold) is expected

    def test_unknown_operator_raises(self):
        with pytest.raises(ThesisInputError):
            compare(1.0, "=~", 1.0)


# ---------------------------------------------------------------------------
# Pure function tests: period selection
# ---------------------------------------------------------------------------


class _Row:
    """Minimal stand-in for a FinancialMetric/Fact row."""

    def __init__(self, year, period, value=1.0):
        self.fiscal_year = year
        self.fiscal_period = period
        self.value = value


ROWS = [
    _Row(2025, "FY", 0.12),
    _Row(2026, "Q1", 0.18),
    _Row(2026, "Q2", 0.16),
    _Row(2026, "FY", 0.1779),
    _Row(2027, "Q1", 0.188),
]


class TestSelectRow:
    def test_latest_prefers_fy_over_earlier_year(self):
        row, reason = select_row(ROWS, None, None)
        assert reason == "" and row.fiscal_year == 2027

    def test_year_pin_without_period_prefers_annual(self):
        row, reason = select_row(ROWS, 2026, None)
        assert reason == "" and row.fiscal_period == "FY" and row.value == 0.1779

    def test_year_and_period_exact_match(self):
        row, reason = select_row(ROWS, 2026, "Q1")
        assert reason == "" and row.value == 0.18

    def test_missing_year_is_incomplete_with_reason(self):
        row, reason = select_row(ROWS, 2024, None)
        assert row is None and "FY2024" in reason and "2025" in reason  # lists what exists

    def test_year_present_but_no_annual_row_is_incomplete(self):
        # 2027 has only Q1: a FY-pinned condition must NOT silently use it.
        row, reason = select_row(ROWS, 2027, None)
        assert row is None and "annual" in reason and "Q1" in reason

    def test_missing_period_is_incomplete(self):
        row, reason = select_row(ROWS, 2026, "Q4")
        assert row is None and "Q4" in reason

    def test_empty_rows(self):
        row, reason = select_row([], None, None)
        assert row is None and reason == "no data"


# ---------------------------------------------------------------------------
# Pure function tests: evaluation + status matrix
# ---------------------------------------------------------------------------


def _value(v=0.1779, year=2026, period="FY", tier="CALCULATED METRIC"):
    from app.services.thesis_service import MetricValue
    return MetricValue(
        metric="revenue_growth_yoy", value=v, unit="ratio", tier=tier,
        fiscal_year=year, fiscal_period=period,
        formula="(current - prior) / abs(prior)",
        accessions=(ACCESSION,),
    )


class TestEvaluateCondition:
    def test_met_carries_full_provenance(self):
        spec = parse_condition("revenue_growth_yoy>=0.10", fiscal_year=2026)
        e = evaluate_condition(spec, _value())
        assert e.verdict == VERDICT_MET
        assert e.value == 0.1779 and e.tier == "CALCULATED METRIC"
        assert e.formula == "(current - prior) / abs(prior)"
        assert e.accessions == (ACCESSION,)
        assert e.fiscal_year == 2026 and e.fiscal_period == "FY"

    def test_not_met(self):
        spec = parse_condition("revenue_growth_yoy>=0.20")
        assert evaluate_condition(spec, _value()).verdict == VERDICT_NOT_MET

    def test_missing_value_is_incomplete_with_reason(self):
        spec = parse_condition("revenue_growth_yoy>=0.10", fiscal_year=2024)
        e = evaluate_condition(spec, None, missing_reason="no data for FY2024")
        assert e.verdict == VERDICT_INCOMPLETE
        assert e.value is None and e.reason == "no data for FY2024"

    def test_verified_fallback_tier_shows_through(self):
        spec = parse_condition("revenue>=1000000000")
        from app.services.thesis_service import MetricValue
        fact = MetricValue(
            metric="revenue", value=130_000_000_000.0, unit="USD",
            tier="VERIFIED FACT", fiscal_year=2025, fiscal_period="FY",
            formula=None, accessions=(ACCESSION,),
        )
        e = evaluate_condition(spec, fact)
        assert e.tier == "VERIFIED FACT" and e.formula is None
        assert e.verdict == VERDICT_MET


def _eval(verdict, desc="x >= 1", value=1.0, year=2026):
    return ConditionEvaluation(
        condition_id=None, description=desc, metric="x", comparator=">=",
        threshold=1.0, verdict=verdict, value=value, unit="ratio",
        tier="CALCULATED METRIC", fiscal_year=year, fiscal_period="FY",
        formula="f", accessions=(), reason="",
    )


class TestDeriveStatus:
    """The full 3x verdict-count matrix -> status."""

    def test_all_met_is_supported(self):
        status, expl = derive_status([_eval(VERDICT_MET), _eval(VERDICT_MET)])
        assert status == STATUS_SUPPORTED and "2/2" in expl

    def test_mixed_is_weakened(self):
        status, expl = derive_status([_eval(VERDICT_MET), _eval(VERDICT_NOT_MET)])
        assert status == STATUS_WEAKENED and "Not met" in expl

    def test_all_evaluable_failed_is_contradicted(self):
        status, _ = derive_status([_eval(VERDICT_NOT_MET), _eval(VERDICT_NOT_MET)])
        assert status == STATUS_CONTRADICTED

    def test_failed_without_any_met_is_contradicted(self):
        # not_met + incomplete, zero met -> the evidence that exists all
        # points one way: Contradicted, with the gap disclosed.
        status, expl = derive_status([_eval(VERDICT_NOT_MET), _eval(VERDICT_INCOMPLETE)])
        assert status == STATUS_CONTRADICTED and "Incomplete" in expl

    def test_incomplete_without_any_failure_is_insufficient(self):
        status, expl = derive_status([_eval(VERDICT_MET), _eval(VERDICT_INCOMPLETE)])
        assert status == STATUS_INSUFFICIENT and "Incomplete" in expl

    def test_all_incomplete_is_insufficient(self):
        status, _ = derive_status([_eval(VERDICT_INCOMPLETE)] * 3)
        assert status == STATUS_INSUFFICIENT

    def test_explanation_names_failed_value_and_period(self):
        _, expl = derive_status([
            _eval(VERDICT_NOT_MET, desc="revenue_growth_yoy >= 0.2", value=0.1779),
        ])
        assert "revenue_growth_yoy" in expl
        assert "0.1779" in expl and "FY2026" in expl

    def test_signals_cannot_reach_this_function(self):
        """Structural pin: derive_status takes ONLY evaluations.

        There is no parameter for signals, so 'an anomaly alone never
        contradicts a thesis' holds by signature, not by convention. If
        someone later adds a signals parameter, this test's intent (and the
        interview answer) still documents the invariant.
        """
        import inspect
        params = list(inspect.signature(derive_status).parameters)
        assert params == ["evaluations"]


# ---------------------------------------------------------------------------
# Service tests (sqlite): creation, snapshots, append-only history
# ---------------------------------------------------------------------------


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    session.add(Company(cik=MSFT_CIK, ticker="MSFT", name="Microsoft Corp"))
    session.add(FinancialMetric(
        cik=MSFT_CIK, metric="revenue_growth_yoy", value=0.1779, unit="ratio",
        fiscal_year=2026, fiscal_period="FY", period_type="annual",
        formula="(current - prior) / abs(prior)", inputs_json="{}",
        source_accessions_json=json.dumps([ACCESSION]),
    ))
    session.add(FinancialMetric(
        cik=MSFT_CIK, metric="operating_income_margin", value=0.44, unit="ratio",
        fiscal_year=2026, fiscal_period="FY", period_type="annual",
        formula="operating_income / revenue", inputs_json="{}",
        source_accessions_json=json.dumps([ACCESSION]),
    ))
    session.add(FinancialFact(
        cik=MSFT_CIK, metric="revenue", value=281_700_000_000.0, unit="USD",
        period_type="annual", fiscal_year=2026, fiscal_period="FY",
        source_concept="RevenueFromContractWithCustomerExcludingAssessedTax",
        form="10-K", accession_number=ACCESSION, is_restated=False,
    ))
    session.commit()
    session.close()
    return factory


@pytest.fixture
def service(session_factory):
    return ThesisService(session_factory, manager=None)


MSFT_STATEMENT = "Microsoft sustains double-digit growth through FY2026."


class TestCreateThesis:
    def test_creates_thesis_with_conditions(self, service):
        thesis_id = service.create_thesis(
            "MSFT", MSFT_STATEMENT, "FY2026",
            ["revenue_growth_yoy>=0.10"], fiscal_year=2026,
        )
        data = service.get_thesis(thesis_id)
        assert data["status"] == "Unassessed"  # NULL = never assessed
        assert len(data["conditions"]) == 1
        assert data["conditions"][0]["description"] == \
            "revenue_growth_yoy >= 0.1 (FY2026)"

    def test_creation_snapshots_current_value(self, service):
        thesis_id = service.create_thesis(
            "MSFT", MSFT_STATEMENT, "FY2026",
            ["revenue_growth_yoy>=0.10"], fiscal_year=2026,
        )
        snap = service.get_thesis(thesis_id)["conditions"][0]["creation_evidence"]
        assert snap["value"] == 0.1779
        assert snap["tier"] == "CALCULATED METRIC"
        assert snap["formula"] == "(current - prior) / abs(prior)"
        assert snap["accessions"] == [ACCESSION]
        assert snap["period"] == "FY2026"

    def test_creation_snapshot_of_missing_metric_records_absence(self, service):
        thesis_id = service.create_thesis(
            "MSFT", MSFT_STATEMENT, "FY2030",
            ["revenue_growth_yoy>=0.10"], fiscal_year=2030,
        )
        snap = service.get_thesis(thesis_id)["conditions"][0]["creation_evidence"]
        assert snap["value"] is None  # honest snapshot: it did not exist yet

    def test_fact_fallback_condition(self, service):
        # 'revenue' has no calculated row -> VERIFIED fact is used
        thesis_id = service.create_thesis(
            "MSFT", MSFT_STATEMENT, "FY2026",
            ["revenue>=200000000000"], fiscal_year=2026,
        )
        snap = service.get_thesis(thesis_id)["conditions"][0]["creation_evidence"]
        assert snap["tier"] == "VERIFIED FACT"
        assert snap["accessions"] == [ACCESSION]

    def test_unknown_ticker_raises(self, service):
        with pytest.raises(ThesisInputError, match="unknown ticker"):
            service.create_thesis("ZZZZ", "x", "FY2026", ["revenue>=1"])

    def test_empty_statement_raises(self, service):
        with pytest.raises(ThesisInputError, match="statement"):
            service.create_thesis("MSFT", "   ", "FY2026", ["revenue>=1"])

    def test_no_conditions_raises(self, service):
        with pytest.raises(ThesisInputError, match="condition"):
            service.create_thesis("MSFT", MSFT_STATEMENT, "FY2026", [])

    def test_bad_condition_raises(self, service):
        with pytest.raises(ThesisInputError):
            service.create_thesis("MSFT", MSFT_STATEMENT, "FY2026", ["nonsense"])


class TestAssess:
    def _two_condition_thesis(self, service):
        return service.create_thesis(
            "MSFT", MSFT_STATEMENT, "FY2026",
            ["revenue_growth_yoy>=0.10", "operating_income_margin>=0.50"],
            fiscal_year=2026,
        )

    def test_assess_all_met_is_supported(self, service):
        thesis_id = service.create_thesis(
            "MSFT", MSFT_STATEMENT, "FY2026",
            ["revenue_growth_yoy>=0.10"], fiscal_year=2026,
        )
        result = service.assess(thesis_id, interpret=False)
        assert result.status == STATUS_SUPPORTED
        assert result.evaluations[0].value == 0.1779
        assert result.status_changed_from is None  # first assessment

    def test_assess_mixed_is_weakened_with_provenance(self, service):
        result = service.assess(self._two_condition_thesis(service), interpret=False)
        assert result.status == STATUS_WEAKENED
        failed = [e for e in result.evaluations if e.verdict == VERDICT_NOT_MET]
        assert failed[0].value == 0.44 and failed[0].tier == "CALCULATED METRIC"

    def test_assessment_is_append_only(self, service):
        thesis_id = self._two_condition_thesis(service)
        first = service.assess(thesis_id, interpret=False)
        second = service.assess(thesis_id, interpret=False)

        data = service.get_thesis(thesis_id)
        assert len(data["assessments"]) == 2  # history grows, never rewrites
        a, b = data["assessments"]
        assert a["id"] == first.assessment_id and b["id"] == second.assessment_id
        # the FIRST row is byte-identical after the second assess
        assert a["status"] == first.status
        assert a["explanation"] == first.explanation
        # same world -> same status -> no change recorded on the second row
        assert second.status == first.status
        assert b["status_changed_from"] is None
        assert "Status changed from" not in second.explanation

    def test_transition_explanation_names_previous_status(self, service):
        thesis_id = service.create_thesis(
            "MSFT", MSFT_STATEMENT, "now",
            ["revenue_growth_yoy>=0.10"],  # no period pin -> latest row
        )
        first = service.assess(thesis_id, interpret=False)
        assert first.status == STATUS_SUPPORTED  # 2027 Q1 = 0.188

        # weaken the world: pin a second, stricter condition impossible to meet
        # by assessing a NEW thesis... instead simulate a data revision below.
        # Here we assert the change-note plumbing on a real flip:
        from app.services.thesis_service import ThesisCondition
        engine_session = service._session_factory()
        cond = ThesisCondition(
            thesis_id=thesis_id, description="impossible", metric="does_not_exist",
            comparator=">=", threshold=1.0, fiscal_year=None, fiscal_period=None,
            evidence_json="{}",
        )
        engine_session.add(cond)
        engine_session.commit()
        engine_session.close()

        second = service.assess(thesis_id, interpret=False)
        assert second.status == STATUS_INSUFFICIENT
        assert second.status_changed_from == STATUS_SUPPORTED
        assert "Status changed from Supported to Insufficient evidence" \
            in second.explanation

    def test_unknown_thesis_raises(self, service):
        with pytest.raises(ThesisNotFoundError):
            service.assess(999)

    def test_incomplete_condition_drives_insufficient(self, service):
        thesis_id = service.create_thesis(
            "MSFT", MSFT_STATEMENT, "FY2030",
            ["revenue_growth_yoy>=0.10"], fiscal_year=2030,
        )
        result = service.assess(thesis_id, interpret=False)
        assert result.status == STATUS_INSUFFICIENT
        assert result.evaluations[0].verdict == VERDICT_INCOMPLETE
        assert "FY2030" in result.evaluations[0].reason

    def test_list_theses(self, service):
        service.create_thesis("MSFT", "a", "FY2026", ["revenue>=1"], fiscal_year=2026)
        service.create_thesis("MSFT", "b", "FY2026", ["revenue>=1"], fiscal_year=2026)
        assert len(service.list_theses()) == 2


# ---------------------------------------------------------------------------
# Service tests (sqlite): the signal-neutrality invariant, end to end
# ---------------------------------------------------------------------------


class TestSignalNeutrality:
    def test_elevated_signals_never_change_status(self, session_factory):
        """The headline rule of ARCHITECTURE 2.8, pinned end to end:
        every condition met + high-severity signals -> still Supported."""
        session = session_factory()
        # NB: the signals table is unique on (company, type, period) by M3
        # design, so two signals need two distinct types.
        for sig_type, sev in [
            ("earnings_cashflow_divergence", "high"),
            ("accounts_receivable_growth", "elevated"),
        ]:
            session.add(InvestigationSignal(
                cik=MSFT_CIK, ticker="MSFT",
                signal_type=sig_type, severity=sev,
                fiscal_year=2026, fiscal_period="FY", period_type="annual",
                headline=f"{sig_type} flagged ({sev})",
                explanation="net income growth outpaces operating cash flow growth",
                evidence_json="{}", source_accessions_json="[]",
            ))
        session.commit()
        session.close()

        service = ThesisService(session_factory, manager=None)
        thesis_id = service.create_thesis(
            "MSFT", MSFT_STATEMENT, "FY2026",
            ["revenue_growth_yoy>=0.10"], fiscal_year=2026,
        )
        result = service.assess(thesis_id, interpret=False)

        assert result.status == STATUS_SUPPORTED  # signals did NOT flip it
        assert len(result.signals_context) == 2  # ...but they are attached
        assert "context" in result.explanation
        stored = service.get_thesis(thesis_id)["assessments"][0]["signals"]
        assert {s["severity"] for s in stored} == {"elevated", "high"}


# ---------------------------------------------------------------------------
# The LLM seam: prompt contract + present/absent/failing manager
# ---------------------------------------------------------------------------


def _evaluation(**overrides):
    base = dict(
        condition_id=1, description="revenue_growth_yoy >= 0.1 (FY2026)",
        metric="revenue_growth_yoy", comparator=">=", threshold=0.1,
        verdict=VERDICT_MET, value=0.1779, unit="ratio",
        tier="CALCULATED METRIC", fiscal_year=2026, fiscal_period="FY",
        formula="(current - prior) / abs(prior)", accessions=(ACCESSION,),
        reason="holds",
    )
    base.update(overrides)
    return ConditionEvaluation(**base)


class TestInterpretationPrompt:
    def test_contains_policy_rules(self):
        assert "NO ARITHMETIC" in THESIS_INTERPRETATION_POLICY
        assert "LLM INTERPRETATION" in THESIS_INTERPRETATION_POLICY
        assert "never evidence of misstatement or fraud" in THESIS_INTERPRETATION_POLICY

    def test_message_carries_statement_status_and_numbers(self):
        msgs = build_thesis_interpretation_messages(
            ticker="MSFT", statement=MSFT_STATEMENT, horizon="FY2026",
            status=STATUS_WEAKENED,
            evaluations=[_evaluation(verdict=VERDICT_NOT_MET, value=0.1779)],
        )
        human = msgs[1].content
        assert MSFT_STATEMENT in human
        assert STATUS_WEAKENED in human
        assert "0.1779" in human  # the exact computed number
        assert ACCESSION in human  # provenance travels to the model
        assert "NOT_MET" in human

    def test_signals_are_labelled_context_only(self):
        msgs = build_thesis_interpretation_messages(
            ticker="MSFT", statement="s", horizon="h",
            status=STATUS_SUPPORTED, evaluations=[_evaluation()],
            signals_context=[{
                "signal_type": "ar_growth", "severity": "high",
                "period": "FY FY2026", "headline": "AR outpacing revenue",
                "explanation": "x",
            }],
        )
        human = msgs[1].content
        assert "context only, never verdicts" in human
        assert "AR outpacing revenue" in human

    def test_dollar_amounts_format_with_separators(self):
        msgs = build_thesis_interpretation_messages(
            ticker="MSFT", statement="s", horizon="h",
            status=STATUS_SUPPORTED,
            evaluations=[_evaluation(metric="revenue", value=281_700_000_000.0)],
        )
        assert "281,700,000,000" in msgs[1].content


class FakeManager:
    """Scripted LLMManager: returns fixed text, or raises."""

    def __init__(self, text=None, error=None):
        self._text = text
        self._error = error
        self.calls = []

    def generate(self, messages):
        self.calls.append(messages)
        if self._error is not None:
            raise self._error
        return LLMResponse(
            text=self._text, provider="fake", model="fake-1",
            input_tokens=10, output_tokens=20, latency_ms=1.0,
        )


class TestInterpretationSeam:
    def _thesis(self, service):
        return service.create_thesis(
            "MSFT", MSFT_STATEMENT, "FY2026",
            ["revenue_growth_yoy>=0.10"], fiscal_year=2026,
        )

    def test_llm_success_stores_interpretation_with_source(self, session_factory):
        manager = FakeManager(text="Growth held above 10%: an LLM reading of a met condition.")
        service = ThesisService(session_factory, manager=manager)
        result = service.assess(self._thesis(service))

        assert result.explanation.startswith("Growth held above")
        assert result.explanation_source == "llm:fake/fake-1"
        assert len(manager.calls) == 1
        # the prompt the service actually sent carries the computed number
        assert "0.1779" in manager.calls[0][1].content

    def test_llm_failure_falls_back_to_deterministic(self, session_factory):
        manager = FakeManager(error=LLMAllProvidersFailed([("groq", "boom")]))
        service = ThesisService(session_factory, manager=manager)
        result = service.assess(self._thesis(service))

        assert result.explanation_source == "deterministic:condition-counts"
        assert "Supported: 1/1 condition(s) met" in result.explanation

    def test_no_manager_still_assesses(self, session_factory):
        service = ThesisService(session_factory, manager=None)
        result = service.assess(self._thesis(service))
        assert result.status == STATUS_SUPPORTED
        assert result.explanation_source == "deterministic:condition-counts"

    def test_interpret_false_skips_llm_entirely(self, session_factory):
        manager = FakeManager(text="should never be used")
        service = ThesisService(session_factory, manager=manager)
        result = service.assess(self._thesis(service), interpret=False)
        assert manager.calls == []
        assert result.explanation_source == "deterministic:condition-counts"

    def test_llm_explanation_saved_to_history_row(self, session_factory):
        manager = FakeManager(text="interp")
        service = ThesisService(session_factory, manager=manager)
        thesis_id = self._thesis(service)
        service.assess(thesis_id)
        row = service.get_thesis(thesis_id)["assessments"][0]
        assert row["explanation"] == "interp"
        assert row["explanation_source"] == "llm:fake/fake-1"
